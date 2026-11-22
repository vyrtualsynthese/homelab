# Argo CD manifest generation failing: implementation plan

> **For the implementer.** Read the design first:
> [`../specs/2026-11-22-argocd-generation-alert-design.md`](../specs/2026-11-22-argocd-generation-alert-design.md).
> Card 644 (#451). Branch `feature/argocd-alert`, base `develop`.

**Goal.** Put Argo CD's three components into Prometheus through the existing annotation discovery,
and add one Grafana rule that fires when an Application carries a `ComparisonError` — the condition
Argo CD sets when it cannot render the Application at all.

**Architecture.** Argo CD's own chart stamps `prometheus.io/scrape` on its metrics Services when
`global.addPrometheusAnnotations` is on; the `kubernetes-service-endpoints` job already honours that
annotation. A second, numeric-port NetworkPolicy is needed because k3s here does not enforce the
chart's `namespaceSelector: {}` allow. The rule lives in Grafana's database, like every other rule;
git records it in `docs/reference/alerts.md`.

**Tech stack.** Helm wrapper chart (`cluster/helm/argocd`, dependency `argo-cd` 10.18.2), Prometheus
values, Grafana provisioning API, Vikunja API.

**Conventions that gate this work.**

- `cluster/argocd/**` is CRLF; this plan touches `cluster/helm/**` and `docs/**`, which are LF.
- No rule, dashboard or `kubectl apply` reaches the cluster as a *fix* for drift. The one hand-made
  object is the throwaway proof Application in Task 7, which is deleted in the same task.
- Every commit ends with the repo's emoji prefix.

---

### Task 1: Prove the chart's scrape shape with a test (red)

**Files:**
- Create: `hack/tests/test_argocd_chart.py`

The test renders the wrapper chart into a temporary copy, the way `hack/check-charts.sh` does,
because `charts/` is gitignored (pattern D).

- [ ] Write the file:

```python
"""Tests for the Argo CD chart's scrape shapes.

`charts/` is gitignored (pattern D), so the chart is copied to a temporary directory and
its dependency built there before rendering — the same steps ArgoCD runs at sync time.
"""

import pathlib
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "cluster/helm/argocd"

METRICS_SERVICES = {
    "argocd-application-controller-metrics": 8082,
    "argocd-repo-server-metrics": 8084,
    "argocd-server-metrics": 8083,
}


def render():
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "argocd"
        shutil.copytree(CHART, work)
        subprocess.run(
            ["helm", "dependency", "build", str(work)],
            check=True,
            capture_output=True,
            text=True,
        )
        out = subprocess.run(
            [
                "helm", "template", "argocd", str(work),
                "-n", "argocd", "-f", str(work / "values.yaml"),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def by_kind(kind):
    return [doc for doc in render() if doc["kind"] == kind]


class ScrapeShapeTests(unittest.TestCase):
    def test_every_metrics_service_carries_the_scrape_annotation(self):
        services = {d["metadata"]["name"]: d for d in by_kind("Service")}
        for name, port in METRICS_SERVICES.items():
            with self.subTest(service=name):
                annotations = services[name]["metadata"].get("annotations", {})
                self.assertEqual(annotations.get("prometheus.io/scrape"), "true")
                self.assertEqual(annotations.get("prometheus.io/port"), str(port))

    def test_the_scrape_policy_allows_the_two_metrics_ports_by_number(self):
        policy = next(
            d for d in by_kind("NetworkPolicy")
            if d["metadata"]["name"] == "argocd-metrics-scrape"
        )
        selector = policy["spec"]["podSelector"]["matchExpressions"][0]
        self.assertEqual(selector["key"], "app.kubernetes.io/name")
        self.assertEqual(
            sorted(selector["values"]),
            ["argocd-application-controller", "argocd-repo-server"],
        )
        rule = policy["spec"]["ingress"][0]
        self.assertEqual(rule["from"], [{"ipBlock": {"cidr": "10.42.0.0/16"}}])
        self.assertEqual(sorted(p["port"] for p in rule["ports"]), [8082, 8084])

    def test_the_controller_is_told_which_condition_to_publish(self):
        cm = next(
            d for d in by_kind("ConfigMap")
            if d["metadata"]["name"] == "argocd-cmd-params-cm"
        )
        self.assertEqual(
            cm["data"]["controller.metrics.application.conditions"],
            "ComparisonError",
        )
```

- [ ] Run it and confirm it fails on the missing pieces (not on the build):

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q \
  hack/tests/test_argocd_chart.py
```

Expected: three failures — `KeyError: 'argocd-application-controller-metrics'` (the Service is not
rendered), `StopIteration` (no policy), and a `KeyError` on the params key. If instead
`helm dependency build` fails, the network is absent; stop and report, do not fake the test.

### Task 2: Turn the scrape on (green)

**Files:**
- Modify: `cluster/helm/argocd/values.yaml`

- [ ] Under `global:`, after `domain:`, add:

```yaml
    # Every metrics Service the chart renders carries prometheus.io/scrape and
    # prometheus.io/port, which is how this cluster finds an exporter: there is
    # no Prometheus Operator and no ServiceMonitor. The three Services are the
    # scrape targets (the controller, the repo-server and the server); the pods
    # themselves are not scraped.
    addPrometheusAnnotations: true
```

- [ ] Under `controller:`, after `resources:`, add:

```yaml
    metrics:
      # -- Deploy the controller's metrics Service on 8082
      enabled: true
```

- [ ] Under `repoServer:`, after `resources:`, add:

```yaml
    metrics:
      # -- Deploy the repo-server's metrics Service on 8084
      enabled: true
```

- [ ] In `configs.params`, after `server.insecure: true`, add:

```yaml
      # With no list here the controller publishes no condition metric at all.
      # ComparisonError is the condition Argo CD sets when it cannot render an
      # Application, so it is the one the alert reads.
      controller.metrics.application.conditions: "ComparisonError"
```

- [ ] In `extraObjects`, after the `argocd-server-redirect` document, append:

```yaml
    # The chart ships an ingress policy per component that allows the named
    # `metrics` port "from any namespace" (`namespaceSelector: {}`). This k3s
    # does not enforce that form: measured 2026-11-22, a probe pod in
    # `monitoring` was refused on 8082 while the same pod was allowed by a rule
    # naming a pod label or an ipBlock. The metrics Services are headless, so a
    # scrape is a pod-to-pod connection to the pod's own address and falls under
    # that policy. This rule is additive to the chart's, and numeric on purpose:
    # the named port is what the namespace form failed to match.
    - apiVersion: networking.k8s.io/v1
      kind: NetworkPolicy
      metadata:
        name: argocd-metrics-scrape
      spec:
        podSelector:
          matchExpressions:
            - key: app.kubernetes.io/name
              operator: In
              values:
                - argocd-application-controller
                - argocd-repo-server
        policyTypes:
          - Ingress
        ingress:
          - from:
              - ipBlock:
                  cidr: 10.42.0.0/16
            ports:
              - port: 8082
                protocol: TCP
              - port: 8084
                protocol: TCP
```

- [ ] Run the test again; it must pass:

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q \
  hack/tests/test_argocd_chart.py
```

- [ ] Lint and render the chart:

```bash
hack/check-charts.sh --only argocd
```

Expected: `checked: 1` (or the script's clean line) and no `FAIL`.

> **Why the server needs no extra policy:** its own chart policy allows all ingress. Do not add
> 8083 here; the test asserts exactly two ports.

### Task 3: Bound the series the scrape adds

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml`

Measured 2026-11-22 on the three live endpoints: 3,384 series exposed, 350 of them the Application
and repository families the alert and the repo-server health read. The largest block is the server's
pre-initialised gRPC matrix, 1,938 series (one per method/service × 17 codes, most permanently
zero).

- [ ] In the `kubernetes-service-endpoints` job, after the existing KSM drop entry, add:

```yaml
        # Argo CD's three components publish 3,384 series and the alert reads
        # 350 of them (argocd_app_*, argocd_repo_*, argocd_cluster_*,
        # argocd_git_*, argocd_redis_*). The rest is the server's gRPC matrix —
        # 1,938 series, one per method × 17 status codes, most permanently zero
        # — plus client-go's work-queue and kubectl instrumentation, the
        # controller's webhook counters, and each process's Go runtime.
        # Measured 2026-11-22; no panel and no rule reads any of it.
        - action: drop
          source_labels:
            - namespace
            - __name__
          separator: ";"
          regex: "argocd;(go_.*|process_.*|grpc_server_.*|argocd_kubectl_.*|workqueue_.*|controller_runtime_.*|certwatcher_.*)"
```

- [ ] Add the identical entry to `kubernetes-service-endpoints-slow`, below its KSM drop entry, so
  a Service that ever carries `prometheus.io/scrape_slow` cannot bring the families back through
  the side door (same reasoning as the existing comment).

- [ ] Render and confirm both jobs carry the rule:

```bash
helm template prometheus cluster/helm/monitoring/prometheus \
  -f cluster/helm/monitoring/prometheus/values.yaml | grep -c 'argocd;(go_'
```

Expected: `2`.

### Task 4: Commit, hand over, merge

- [ ] Full local gate:

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
hack/check-charts.sh
hack/check-exposure.sh
python3 hack/check_docs_links.py
```

All four must be clean. A failure in an unrelated chart is a real failure of this branch's gate —
report it rather than working around it.

- [ ] Commit:

```bash
git add cluster/helm/argocd/values.yaml \
        cluster/helm/monitoring/prometheus/values.yaml \
        hack/tests/test_argocd_chart.py
git commit -m "🚀 feat: scrape Argo CD's three components and bound their series"
```

- [ ] Rebase, push, open the PR against `develop`, and wait for the merge:

```bash
git fetch origin
git rebase origin/develop
git push origin feature/argocd-alert
```

PR title: `🚀 Scrape Argo CD and alert when manifests cannot be generated`. Body: what changed, the
network-policy measurement, the series before/after, and that the rule is Task 6.

### Task 5: Verify the scrape on the cluster (after merge)

Nothing here writes to the cluster.

- [ ] Wait for the argocd Application to sync (the root app polls; a manual sync needs the Argo CD
  API, which has no token — say so if the poll is slow).

- [ ] Three targets up:

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=count(up{namespace="argocd"} == 1)' | jq '.data.result'
```

Expected: `3`.

- [ ] The data is there:

```bash
for q in 'count(argocd_app_info)' 'count(argocd_app_condition)' \
         'count({__name__=~"argocd_.*"})'; do
  curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode "query=$q" \
    | jq -r --arg q "$q" '"\($q) => \(.data.result[0].value[1] // "none")"'
done
```

Expected: `60`, `0` (no Application has a condition), and about `350` stored `argocd_*` series.
If the last number is near 3,384 the drop did not apply — the regex does not match; fix it.

- [ ] The dropped families are gone:

```bash
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=count({__name__=~"grpc_server_.*|workqueue_.*", namespace="argocd"})'
```

Expected: no result. A number means the relabel rule is not live.

- [ ] Record the measured per-job series for the docs task:

```bash
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=count by (job) ({__name__!=""})' | jq -r '.data.result[] | "\(.metric.job) \(.value[1])"'
```

### Task 6: Create the rule

Rules are created through Grafana's provisioning API; the database stays the authority.
`GRAFANA_TOKEN` and the internal CA are in `~/.config/secrets/homelab.env` and
`~/.config/secrets/kub-local/local-ca.pem`.

- [ ] Validate the expression against Prometheus first — it must return an empty vector:

```bash
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=argocd_app_condition{condition="ComparisonError"} > 0' | jq '.data.result'
```

Expected: `[]`.

- [ ] Create it:

```bash
cat > /tmp/opencode/argocd-rule.json <<'JSON'
{
  "folderUID": "homelab",
  "ruleGroup": "homelab-core",
  "title": "ArgoAppRenderFailing",
  "condition": "C",
  "data": [
    {
      "refId": "A",
      "queryType": "",
      "relativeTimeRange": {"from": 600, "to": 0},
      "datasourceUid": "PBFA97CFB590B2093",
      "model": {
        "editorMode": "code",
        "expr": "argocd_app_condition{condition=\"ComparisonError\"} > 0",
        "instant": true,
        "intervalMs": 1000,
        "maxDataPoints": 43200,
        "range": false,
        "refId": "A"
      }
    },
    {
      "refId": "C",
      "queryType": "",
      "relativeTimeRange": {"from": 0, "to": 0},
      "datasourceUid": "__expr__",
      "model": {
        "conditions": [
          {
            "evaluator": {"params": [0], "type": "gt"},
            "operator": {"type": "and"},
            "query": {"params": ["A"]},
            "reducer": {"type": "last"},
            "type": "query"
          }
        ],
        "datasource": {"type": "__expr__", "uid": "__expr__"},
        "expression": "A",
        "intervalMs": 1000,
        "maxDataPoints": 43200,
        "refId": "C",
        "type": "threshold"
      }
    }
  ],
  "noDataState": "OK",
  "execErrState": "OK",
  "for": "10m",
  "annotations": {
    "summary": "Argo CD cannot generate manifests for {{ $labels.name }}",
    "description": "Argo CD could not render this Application, so it has stopped reporting whether it is in sync. Project: {{ $labels.project }}. The repo-server has the reason: kubectl -n argocd logs deploy/argocd-repo-server --since=30m | grep -i '<name>'. The routine `missing in charts/` line is not this — it clears itself; a ComparisonError does not. Runbook: docs/runbooks/argocd.md."
  },
  "labels": {"severity": "warning"},
  "isPaused": false
}
JSON

set -a; . ~/.config/secrets/homelab.env; set +a
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  -X POST https://grafana.kub.local/api/v1/provisioning/alert-rules \
  -d @/tmp/opencode/argocd-rule.json | jq '{uid, title, ruleGroup, for, noDataState}'
```

Expected: `uid` set, `ruleGroup` `homelab-core`, `for` `10m`, `noDataState` `OK`.

- [ ] Read it back and confirm the group now holds 27 rules and still runs every 300 s:

```bash
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core \
  | jq '{title, interval, rules: (.rules | length), has: ([.rules[].title] | index("ArgoAppRenderFailing"))}'
```

Expected: `interval 300`, `rules 27`, `has 26` (or any non-null index).

- [ ] Confirm it reads `inactive` / `Normal` after two evaluations (up to 10 minutes):

```bash
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  'https://grafana.kub.local/api/prometheus/grafana/api/v1/rules?type=alert' \
  | jq -r '.data.groups[].rules[] | select(.name=="ArgoAppRenderFailing") | "\(.state)"'
```

### Task 7: Prove it fires, then put the timing back

The proof object is a throwaway Application, not a manifest in git. It deletes cleanly.

- [ ] Write the object:

```bash
cat > /tmp/opencode/alert-proof-render.yaml <<'YAML'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: alert-proof-render
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/vyrtualsynthese/homelab.git
    targetRevision: develop
    path: cluster/helm/does-not-exist
  destination:
    server: https://kubernetes.default.svc
    namespace: default
YAML
kubectl apply -f /tmp/opencode/alert-proof-render.yaml
```

No `syncPolicy`: nothing is applied, and the render failure is the whole point.

- [ ] Wait for the condition (the controller reconciles within three minutes):

```bash
for i in $(seq 1 20); do
  c=$(kubectl -n argocd get application alert-proof-render \
        -o jsonpath='{.status.conditions[?(@.type=="ComparisonError")].message}' 2>/dev/null)
  [ -n "$c" ] && { echo "$c"; break; }
  sleep 15
done
```

- [ ] Confirm the rule's own data fires, without waiting for its group:

```bash
RULE=$(curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/alert-rules \
  | jq '[.[] | select(.title=="ArgoAppRenderFailing")][0]')
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  -X POST https://grafana.kub.local/api/v1/eval \
  -d "$(echo "$RULE" | jq '{condition, data}')" | jq '.data[].frames[].data.values'
```

Expected: the value `1` for `alert-proof-render`.

- [ ] Shorten the timing for the end-to-end check — rule `for: 1m`, group interval 60 s:

```bash
UID=$(echo "$RULE" | jq -r .uid)
echo "$RULE" | jq '.for="1m"' > /tmp/opencode/rule-short.json
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  -X PUT "https://grafana.kub.local/api/v1/provisioning/alert-rules/$UID" \
  -d @/tmp/opencode/rule-short.json >/dev/null

curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core \
  | jq '.interval=60' > /tmp/opencode/group-short.json
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  -X PUT https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core \
  -d @/tmp/opencode/group-short.json >/dev/null
```

- [ ] Watch it go `pending` → `firing`, and confirm the notification reached the contact point:

```bash
watch -n 15 'curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/prometheus/grafana/api/v1/rules?type=alert" \
  | jq -r ".data.groups[].rules[] | select(.name==\"ArgoAppRenderFailing\") | \"\(.state) \(.alerts[0].labels.name // \"\") \(.alerts[0].annotations.summary // \"\")"'

curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  'https://grafana.kub.local/api/alertmanager/grafana/api/v2/alerts' \
  | jq '[.[] | select(.labels.alertname=="ArgoAppRenderFailing") | {state: .status.state, receiver: .receivers[0].name, summary: .annotations.summary}]'
```

Expected: `firing`, the summary names `alert-proof-render`, and the Alertmanager view lists it as
`active` with `discord-homelab`. Ask the operator to confirm the Discord message; that is the one
piece no API here reports.

- [ ] Delete the proof Application and restore the timing:

```bash
kubectl -n argocd delete application alert-proof-render
```

Restore the rule to `for: 10m` with the same `PUT` pattern, and the group with:

```bash
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core \
  | jq '.interval=300' > /tmp/opencode/group-restore.json
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  -X PUT https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core \
  -d @/tmp/opencode/group-restore.json >/dev/null
```

- [ ] Confirm `Normal`, `for 10m`, interval 300, 27 rules, and that no other rule changed state in
  the round trip (compare the 26 pre-existing uids you can list before and after).

### Task 8: Write it down

**Files:**
- Modify: `docs/reference/alerts.md`
- Modify: `docs/reference/metrics.md`
- Modify: `docs/observability-conventions.md`
- Modify: `docs/runbooks/argocd.md`

- [ ] `docs/reference/alerts.md`
  - Intro: `46 rules` → `47`, `26 rules` in `homelab-core` → `27`.
  - Table, after `JobFailed`:

```markdown
| `ArgoAppRenderFailing` | an Application carrying a `ComparisonError` — Argo CD could not render it, so it no longer reports sync state | 10m | warning | inactive |
```

  - After the `JobFailed` paragraph, add a paragraph: the `missing in charts/` line is routine (789
    in 24 h, card 643); the dependency build and the retry happen inside one request, so it never
    produces a failed request or a condition; the rule therefore reads the condition rather than a
    failed-request share, which cannot name the Application. Name the label set
    (`namespace`, `name`, `project`, `condition`) and the one requirement — the controller publishes
    a condition only when `controller.metrics.application.conditions` lists it.
  - Known gaps: a **failed sync** is still unalerted. `ComparisonError` is comparison, not the
    operation; a sync that runs and fails shows `operationState.phase: Failed` on the dashboard.
    `SyncError` is not in the published list, so the rule does not see it.

- [ ] `docs/reference/metrics.md`
  - In the drop table's `kubernetes-service-endpoints` row, add the Argo CD clause:

```markdown
| `kubernetes-service-endpoints` | `kube_secret_*`, `kube_pod_tolerations`, `kube_replicaset_*`, `kube_pod_status_qos_class`, and Argo CD's `go_*`, `process_*`, `grpc_server_*`, `argocd_kubectl_*`, `workqueue_*`, `controller_runtime_*`, `certwatcher_*` | 6,156 + 3,034 | one series per object: 421 Secrets, and the replica panels read Deployments and StatefulSets; of Argo CD's 3,384 series only the 350 Application, repository and cluster families have a reader |
```

  - Add a short "**Measured on 2026-11-22**" line under the 2026-11-18 measurement with the number
    Task 5 printed for `kubernetes-service-endpoints`, and the 350 that survive.

- [ ] `docs/observability-conventions.md`, in `## How metrics get in`, after the mktxp bullet:

```markdown
- **Argo CD's three components are annotation discovery since 2026-11-22**, on their **metrics
  Services** — controller 8082, repo-server 8084, server 8083 — not on the pods.
  `global.addPrometheusAnnotations` stamps the annotation; `controller.metrics` and
  `repoServer.metrics` are what make the first two Services exist. The Services are headless, so an
  endpoint is the pod's own address and the scrape is a pod-to-pod connection: **the chart's own
  policy, the named `metrics` port allowed from `namespaceSelector: {}`, is not enforced on this
  k3s** (measured 2026-11-22 — a probe in `monitoring` was refused until a rule named numeric ports
  from an `ipBlock`). `cluster/helm/argocd/values.yaml` carries that numeric rule for 8082 and 8084
  beside the chart's own.
```

- [ ] `docs/runbooks/argocd.md`, new section before `## Break-glass`:

```markdown
## When the alert says the manifests cannot be generated

`ArgoAppRenderFailing` fires on an Application carrying a `ComparisonError`: Argo CD could not
render it, so its sync status reads `Unknown` (see the field check above). It is not the routine
`missing in charts/` line — that one is built and retried inside a single request and leaves no
condition behind.

```bash
kubectl -n argocd get application <name> -o jsonpath='{.status.conditions}' | jq
kubectl -n argocd logs deploy/argocd-repo-server --since=30m | grep -i '<name>'
```

The causes seen here, in order of frequency: a `path` that does not exist at that
`targetRevision`; a chart that fails `helm template` (the message quotes the template error); a
repository the repo-server cannot read (credentials, or a `targetRevision` branch that was never
pushed); and a dependency build that keeps failing (see the section above). The rule's `for: 10m`
means a retry that succeeds clears it without a message.
```

- [ ] Run the docs gate:

```bash
python3 hack/check_docs_links.py
```

- [ ] Commit:

```bash
git add docs/reference/alerts.md docs/reference/metrics.md \
        docs/observability-conventions.md docs/runbooks/argocd.md
git commit -m "📝 docs: record the Argo CD generation alert and its scrape"
```

### Task 9: Card 644

Use `VIKUNJA_MCP_TOKEN` (the bot), never `VIKUNJA_API_TOKEN`. Description by `PUT`, not `PATCH`.
The card is already in `Doing`; finishing moves it to `Validation` (never `Done`).

- [ ] Rewrite the description (≤15 lines, ≤1,200 characters, the five-line block first, every URL
  an `<a href>`):

```markdown
repo: https://github.com/vyrtualsynthese/homelab
docs: https://docs.kub.local/homelab/develop/reference/alerts/
site: none
branch: feature/argocd-alert
pr: <the merged PR's URL>

**(1)** Scraped Argo CD's three components through the metrics Services
(`global.addPrometheusAnnotations`, `controller.metrics`, `repoServer.metrics`) and added a numeric
NetworkPolicy for 8082/8084, because this k3s ignores the chart's `namespaceSelector` allow.
**(2)** Dropped 3,034 of the 3,384 series at the scrape; 350 stored. **(3)** Created
`ArgoAppRenderFailing`, warning, `homelab-core`, `for: 10m`, on
`argocd_app_condition{condition="ComparisonError"} > 0`.

**Verified.** 3 of 3 targets up; `argocd_app_info` = 60; condition count 0 on a healthy cluster; the
rule inactive; a throwaway Application with a missing path went pending → firing, the summary named
it, and deleting it cleared the rule. Timings restored to 10m/300 s afterwards.

**Known gap.** A failing *sync* (`SyncError`) is still unalerted; the rule watches comparison.
```

- [ ] Move it to `Validation`, and read it back: creator bot, one `priority/*` label
  (`priority/low`), every non-`none` block line an `<a href>`, position inside the low band.

- [ ] Comment the card with the API calls and their codes: `POST /api/v1/provisioning/alert-rules`
  (200, uid …), `PUT` of the group for the proof (200), and the Prometheus counts from Task 5.

---

## Completion

- [ ] PR merged to `develop`; branch kept until the operator says otherwise.
- [ ] `ArgoAppRenderFailing` in `homelab-core`, `for: 10m`, inactive on a healthy cluster.
- [ ] No `argocd` target down, no dropped family stored, series count as recorded.
- [ ] Card 644 in `Validation` with the evidence, not `Done`.
