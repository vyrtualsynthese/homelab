# SnapRAID parity stale alert — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Grafana rule warns when `nas` has not finished a SnapRAID sync for more than 7 days, and reports Alerting when no sync has ever been recorded.

**Architecture:** The runner installed by `ansible/roles/snapraid` writes a one-line marker with the Unix time of the last sync that exited cleanly, atomically, into `/var/lib/node-exporter/textfile/snapraid.prom` on `nas`. A small Deployment in `monitoring`, pinned to `nas`, runs `node-exporter` with every default collector off and only the textfile collector, reading that directory read-only, and is discovered by the existing pod-annotation scrape job. The Grafana rule subtracts the timestamp from `time()` and compares the age to 7 days in its **threshold**, so a recent sync is Normal and a missing metric is NoData, which the rule reports as Alerting.

**Tech Stack:** Helm 3, Prometheus node-exporter 1.13.1 textfile collector, Ansible (host layer), Grafana unified alerting (provisioning API v1).

**Spec:** `docs/superpowers/specs/2026-11-25-snapraid-parity-alert-design.md`

## Global Constraints

- Metric name exactly `homelab_snapraid_last_sync_timestamp_seconds`, no labels, value is Unix seconds. It is a **timestamp**, never an age.
- Marker path on the host: `/var/lib/node-exporter/textfile/snapraid.prom`, directory `0755` root:root, file `0644`.
- Threshold: `604800` seconds (7 days). Severity `warning`.
- Rule group `homelab-core` (300 s interval), `for: 10m`, `noDataState: Alerting`, `execErrState: OK`.
- The restore-drill ConfigMap, its `/restore-drills` mount and the `prometheus-node-exporter` DaemonSet are **not modified**.
- The exporter must never publish `node_*` families from `nas`: `--collector.disable-defaults` stays.
- The exporter must not use `hostNetwork` (the DaemonSet already holds port 9100 on the host).
- Rules are never committed: created through `POST /api/v1/provisioning/alert-rules` after the merge, and recorded in `docs/reference/alerts.md`.
- `cluster/argocd/**` files use **CRLF**; everything else LF. 2-space YAML, no tabs.
- Gitmoji commit prefixes (`🚀 feat:`, `🐛 fix:`, `📝 docs:`, `⚙️ chore:`).

## Review Focus

- A healthy night must be **Normal**, not Alerting: the 7-day comparison lives in the Grafana threshold, never in PromQL, because a false comparison returns an empty vector and this rule's no-data state is Alerting.
- A missing marker must be **Alerting**: no marker means no sync has ever finished, which is the fault the card exists for — and the message must let a reader tell it from a stale one.
- A half-written marker must never make node-exporter drop the file silently: the write is a temp file plus `rename(2)`, and a malformed body would show up as `node_scrape_collector_success{collector="textfile"} == 0`, not as a value.
- The second exporter must not become a second, wrong copy of `nas`'s node metrics: defaults off, one target, `/var/lib/node-exporter/textfile` only.
- A sync that fails must leave the timestamp behind, not update it: the write sits after `snapraid sync` under `set -e`, so a non-zero exit never reaches it.

---

### Task 1: The marker on the host

**Files:**
- Modify: `ansible/roles/snapraid/tasks/main.yml`
- Modify: `ansible/roles/snapraid/files/snapraid-sync.sh`
- Test: `hack/tests/test_ansible_layer.py`

**Interfaces:**
- Produces: `/var/lib/node-exporter/textfile/snapraid.prom` on `nas`, a world-readable textfile containing
  `# HELP homelab_snapraid_last_sync_timestamp_seconds …`, `# TYPE … gauge` and
  `homelab_snapraid_last_sync_timestamp_seconds <epoch>`, rewritten after every successful sync.

- [ ] **Step 1: Commit the design and this plan first**

```bash
git add docs/superpowers/specs/2026-11-25-snapraid-parity-alert-design.md \
        docs/superpowers/plans/2026-11-25-snapraid-parity-alert.md
git commit -m "📝 docs(monitoring): design and plan the parity alert"
```

- [ ] **Step 2: Write the two failing tests**

Append to `hack/tests/test_ansible_layer.py`, after `test_snapraid_runs_outside_the_backup_window`:

```python
def test_snapraid_writes_the_parity_marker_after_a_successful_sync():
    """The alert reads a timestamp the runner writes. An age written once a night
    would be frozen by the dead runner it exists to expose."""
    script = (ANSIBLE / "roles/snapraid/files/snapraid-sync.sh").read_text()
    assert "write_marker ||" in script, "the call must tolerate a failed write and log it"
    assert script.index("write_marker ||") > script.index("/usr/bin/snapraid sync"), (
        "the marker must be written after the sync it reports"
    )
    assert "homelab_snapraid_last_sync_timestamp_seconds" in script
    tasks = (ANSIBLE / "roles/snapraid/tasks/main.yml").read_text()
    assert "/var/lib/node-exporter/textfile" in tasks, "the role owns the directory"


def test_snapraid_role_does_not_claim_the_journal_reaches_loki():
    """Fluent Bit tails /var/log/containers/*.log only: no host line reaches Loki.
    A comment that says otherwise sends the next reader to a store that never held it."""
    for path in (
        ANSIBLE / "roles/snapraid/tasks/main.yml",
        ANSIBLE / "roles/snapraid/files/snapraid-sync.sh",
    ):
        assert "reaches Loki" not in path.read_text(), path
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_layer.py -k snapraid`
Expected: the two new tests FAIL (`write_marker ||` missing, `reaches Loki` present); the older snapraid tests still pass.

- [ ] **Step 4: Correct the false claim and add the directory task**

In `ansible/roles/snapraid/tasks/main.yml`, replace the header paragraph's last sentence — `the journal already reaches Loki` — with:

```
#   The runner has no mail path and the journal is not shipped: Fluent Bit tails
#   container logs only, so nothing from this host reaches Loki. What the parity
#   alert reads instead is the marker this role's runner writes (see the
#   `snapraid-exporter` Application).
```

Then, immediately before the `Install the runner` task:

```yaml
# The parity alert reads the marker the runner writes here. A node-exporter pod
# pinned to this node serves this directory through its textfile collector, and
# the rule compares the timestamp with now. The directory is created here rather
# than by the pod so a missing one is a failed role, not a silently empty metric.
- name: Create the directory the parity marker is written to
  ansible.builtin.file:
    path: /var/lib/node-exporter/textfile
    state: directory
    owner: root
    group: root
    mode: "0755"
```

- [ ] **Step 5: Write the marker in the runner**

In `ansible/roles/snapraid/files/snapraid-sync.sh`:

Replace the header's `# journal: it already reaches Loki, and this homelab has no mail path of its own.` with:

```bash
# journal, and into the marker below, which is what the parity alert reads: this
# homelab has no mail path of its own and the journal is not shipped to Loki.
```

After `rc=0`, define the writer — the body is fixed, so write it exactly:

```bash
marker=/var/lib/node-exporter/textfile/snapraid.prom

# A timestamp, not an age: the rule subtracts it from now, so a run that stops
# makes the number grow instead of freezing it. `rename(2)` inside one directory
# is atomic, so a scrape sees the old body or the new one, never half of either.
write_marker() {
  local tmp="$marker.$$"
  {
    printf '# HELP homelab_snapraid_last_sync_timestamp_seconds Unix time of the last snapraid sync that finished without an error.\n'
    printf '# TYPE homelab_snapraid_last_sync_timestamp_seconds gauge\n'
    printf 'homelab_snapraid_last_sync_timestamp_seconds %s\n' "$(date +%s)"
  } >"$tmp" && mv "$tmp" "$marker" && return 0
  rm -f "$tmp"
  return 1
}
```

Then, directly after the existing `logger -t snapraid "sync finished"` line:

```bash
# Not fatal: the monthly scrub must not be lost to it, and a marker that stops
# being written is already what the alert reports.
write_marker || logger -t snapraid "ERROR could not write $marker"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_layer.py -k snapraid
bash -n ansible/roles/snapraid/files/snapraid-sync.sh && echo syntax-ok
```

Expected: all snapraid tests PASS and `syntax-ok`. The `bash -n` is the only check on the writer's body before it runs nightly as root; a body node-exporter rejects would show as `node_scrape_collector_success{collector="textfile"} == 0` in Task 5, not as an error here.

- [ ] **Step 7: Prove the play is idempotent before touching the host**

Run, from `ansible/`:

```bash
ansible-playbook playbooks/site.yml --limit storage --check --diff
```

Expected: the only task reporting a change is `Create the directory the parity marker is written to`; everything else `ok`.

- [ ] **Step 8: Apply the role to `nas` and prove the directory**

Run, from `ansible/`:

```bash
ansible-playbook playbooks/site.yml --limit storage
ansible nas -m shell -a 'stat -c "%a %U:%G %n" /var/lib/node-exporter/textfile'
```

Expected: `changed=1` on the directory task, then `755 root:root /var/lib/node-exporter/textfile`. The marker file itself appears at the next sync that finishes (`17 3 * * *`, or the run in flight now); Task 5 writes one by hand to prove the chain.

- [ ] **Step 9: Commit**

```bash
git add ansible/roles/snapraid/tasks/main.yml ansible/roles/snapraid/files/snapraid-sync.sh hack/tests/test_ansible_layer.py
git commit -m "🚀 feat(snapraid): write the last successful sync as a textfile metric"
```

---

### Task 2: The exporter chart and its Application

**Files:**
- Create: `cluster/helm/monitoring/snapraid-exporter/Chart.yaml`
- Create: `cluster/helm/monitoring/snapraid-exporter/values.yaml`
- Create: `cluster/helm/monitoring/snapraid-exporter/templates/_helpers.tpl`
- Create: `cluster/helm/monitoring/snapraid-exporter/templates/deployment.yaml`
- Create: `cluster/argocd/projects/monitoring/snapraid-exporter.yaml`

**Interfaces:**
- Consumes: the host directory from Task 1.
- Produces: pod `monitoring/snapraid-exporter-*` on `nas`, serving `homelab_snapraid_last_sync_timestamp_seconds` on `:9100/metrics`, discovered by the `kubernetes-pods` job through its pod annotations; series carry `node="nas"` and `pod`.

- [ ] **Step 1: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: snapraid-exporter
description: The SnapRAID runner's last successful sync, served as a node-exporter textfile metric
type: application
version: 0.1.0
appVersion: "1.13.1"
```

- [ ] **Step 2: Write `values.yaml`**

```yaml
# One metric, written on the host by ansible/roles/snapraid. Every default
# collector is off: a second node-exporter with its defaults on would publish a
# second, wrong copy of nas's node metrics under its own instance label, read
# from the container's /proc instead of the host's.
replicaCount: 1

image:
  repository: quay.io/prometheus/node-exporter
  pullPolicy: IfNotPresent
  tag: v1.13.1

# What the existing pod-annotation job looks for. No Service and no scrape
# config: mktxp and blackbox-exporter are found the same way.
podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "9100"
  prometheus.io/path: /metrics

# Where the runner writes the marker. The role creates it, and the pod mounts it
# with `type: Directory` rather than creating it: a missing directory is a pod
# that will not start, which is louder than a metric that is silently absent.
textfileDirectory: /var/lib/node-exporter/textfile

nodeSelector:
  kubernetes.io/hostname: nas

resources:
  requests:
    cpu: 5m
    memory: 16Mi
  limits:
    memory: 32Mi
```

- [ ] **Step 3: Write `templates/_helpers.tpl`**

Copy the five helpers `app.name`, `app.fullname`, `app.chart`, `app.labels` and
`app.selectorLabels` verbatim from `cluster/helm/media-stack/autobrr/templates/_helpers.tpl`
(lines 11–50). Do not copy `app.serviceAccountName`, `app.configClaimName` or
`app.containerPort`: this chart has no service account, no claim and one port.

- [ ] **Step 4: Write `templates/deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "app.fullname" . }}
  labels:
    {{- include "app.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      {{- include "app.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      annotations:
        {{- toYaml .Values.podAnnotations | nindent 8 }}
      labels:
        {{- include "app.labels" . | nindent 8 }}
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 65534
        runAsGroup: 65534
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args:
            - --collector.disable-defaults
            - --collector.textfile
            - --collector.textfile.directory=/textfile
            - --web.listen-address=:9100
          ports:
            - name: metrics
              containerPort: 9100
              protocol: TCP
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL
          readinessProbe:
            httpGet:
              path: /
              port: metrics
          livenessProbe:
            httpGet:
              path: /
              port: metrics
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          volumeMounts:
            - name: textfile
              mountPath: /textfile
              readOnly: true
      volumes:
        - name: textfile
          hostPath:
            path: {{ .Values.textfileDirectory }}
            type: Directory
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
```

- [ ] **Step 5: Write the Application, in CRLF**

Create `cluster/argocd/projects/monitoring/snapraid-exporter.yaml` with the same shape as its
neighbour `prometheus-smartctl.yaml`: `metadata.name: snapraid-exporter`, `namespace: argocd`,
`spec.project: default`, `destination.namespace: monitoring`, `syncPolicy.syncOptions:
[CreateNamespace=true]`, `source.repoURL: https://github.com/vyrtualsynthese/homelab.git`,
`targetRevision: develop`, `path: cluster/helm/monitoring/snapraid-exporter`.

Then convert the file to CRLF and prove it:

```bash
python3 - <<'PY'
p = 'cluster/argocd/projects/monitoring/snapraid-exporter.yaml'
d = open(p, 'rb').read().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
open(p, 'wb').write(d)
PY
file cluster/argocd/projects/monitoring/snapraid-exporter.yaml | grep -q CRLF && echo CRLF-ok
```

- [ ] **Step 6: Render the chart**

```bash
helm lint cluster/helm/monitoring/snapraid-exporter
helm template snapraid-exporter cluster/helm/monitoring/snapraid-exporter --namespace monitoring \
  | grep -E 'disable-defaults|textfile|hostPath|prometheus.io|port:|hostname'
```

Expected: `helm lint` reports 0 failures; the render shows the three `--collector` args, the
`/textfile` mount, `path: /var/lib/node-exporter/textfile` with `type: Directory`, the three
annotations, and `kubernetes.io/hostname: nas`.

- [ ] **Step 7: Run the chart gates**

```bash
hack/check-charts.sh --only monitoring/snapraid-exporter
hack/check-app-version.sh --only snapraid
```

Expected: both report the chart clean (exit 0); `appVersion 1.13.1` matches `image.tag v1.13.1`.

- [ ] **Step 8: Commit**

```bash
git add cluster/helm/monitoring/snapraid-exporter cluster/argocd/projects/monitoring/snapraid-exporter.yaml
git commit -m "🚀 feat(monitoring): serve the snapraid marker from a textfile-only exporter"
```

---

### Task 3: The written record

**Files:**
- Modify: `docs/reference/alerts.md`
- Modify: `docs/observability-conventions.md`
- Modify: `docs/reference/metrics.md`
- Modify: `docs/how-it-works/applications.md`

**Interfaces:**
- Consumes: the metric name from Task 1, the exporter from Task 2, and the rule body created in Task 5.

- [ ] **Step 1: Record the rule in `docs/reference/alerts.md`**

- Header counts: `homelab-core` **30** rules, `homelab-core-fast` 21, **51** rules total (develop's split of `DriveNeedsReplacement` into `DriveWorn`/`DriveFailing` had already taken the group to 29 / 50, so this rule is the thirtieth and fifty-first).
- Table row, after the `RestoreDrillOverdue` row:
  `| `SnapraidParityStale` | no completed snapraid sync on `nas` for 7 days, or no marker at all | 10m | warning | inactive |`
- A prose paragraph after the `RestoreDrillOverdue` one, covering: the runner (`ansible/roles/snapraid`) writes `homelab_snapraid_last_sync_timestamp_seconds` into `/var/lib/node-exporter/textfile/snapraid.prom` only after `snapraid sync` exits cleanly; a second textfile-only node-exporter on `nas` serves it; the age is computed in PromQL and the 7-day comparison sits in the threshold for the same reason as the drill rule; seven days is a week of missed nights and longer than the multi-night first sync; `snapraid status` is not called because it refuses while a sync holds the content lock; and that a `[no value]` summary means the marker or the exporter is missing, not that the runner is slow.
- The no-data sentence: `noDataState` is `OK` everywhere except `LokiSilent`, `RestoreDrillOverdue` **and `SnapraidParityStale`** — and note that this rule keeps `execErrState: OK`, unlike the drill rule, because a failed query is the monitoring stack's fault (`MonitoringDown`, `GrafanaAlertEvaluationFailing`) and not a missing marker.
- A known-gap bullet: **no host logs reach Loki** — Fluent Bit tails `/var/log/containers/*.log` only, so systemd, kernel and cron lines (including this runner's own) are in the host journal and nowhere else; the parity alert reads a marker for that reason, and shipping the journal is its own decision.

- [ ] **Step 2: Add the exporter to `docs/observability-conventions.md`**

- The "Three older exporters live beside them" sentence becomes four, naming `snapraid-exporter` on `nas` and what it is: a textfile-only node-exporter serving the parity marker.
- One bullet under "How metrics get in": annotation discovery on the pod, every default collector off so it publishes no `node_*`, and one target on `nas` only.

- [ ] **Step 3: Add the quirk to `docs/reference/metrics.md`**

Under "Exporter quirks", after the SMART entry, a **SnapRAID** entry: the value is written by the host runner, not measured; the exporter is a second node-exporter on `nas` with `--collector.disable-defaults`, so `node_*` families do not come from it — it carries `homelab_snapraid_last_sync_timestamp_seconds`, `node_textfile_mtime_seconds` and `node_scrape_collector_success{collector="textfile"}`; a `0` on that last one means the file was rejected (a corrupt body), not that the sync is fine.

- [ ] **Step 4: Add the catalogue row**

In `docs/how-it-works/applications.md`, alphabetically after the `prometheus` row:

`| `snapraid-exporter` | `monitoring` | nas | A | the SnapRAID runner's last-success timestamp, served as a metric |`

- [ ] **Step 5: Check the docs**

```bash
hack/check-docs.sh
grep -rn "SnapraidParityStale" docs/ | wc -l
grep -n "snapraid-exporter" docs/how-it-works/applications.md
```

Expected: `check-docs.sh` passes, including the bidirectional catalogue check; the rule is named in `alerts.md`; the catalogue row exists.

- [ ] **Step 6: Commit**

```bash
git add docs/reference/alerts.md docs/observability-conventions.md docs/reference/metrics.md docs/how-it-works/applications.md
git commit -m "📝 docs(monitoring): record the parity alert and the host-log gap it works around"
```

---

### Task 4: The gate and the pull request

**Files:** none.

- [ ] **Step 1: Run every check on this tree**

```bash
hack/check-charts.sh
hack/check-app-version.sh
hack/check-docs.sh
hack/check-exposure.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```

Expected: every chart clean, exposure unchanged (the chart adds no route), zero test failures.

- [ ] **Step 2: Check the ArgoCD paths resolve**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")
```

Expected: no `BROKEN PATH` line.

- [ ] **Step 3: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push origin feature/snapraid-parity-alert
```

- [ ] **Step 4: Open the pull request, base `develop`**

Body: what the marker is and why it is a timestamp; why a second exporter instead of the restore-drill directory (one textfile directory per node-exporter); why the comparison is in the Grafana threshold; that the rule is created after the merge because it reads a metric that does not exist yet, and that it therefore reports Alerting until the first sync finishes — which is the intended answer, not a false page.

---

### Task 5: The rule, its three states, and the card (after the merge to `develop`)

**Files:** none (Grafana's database; evidence goes into card 713 as a comment).

**Interfaces:**
- Consumes: the metric from Task 1 and the exporter from Task 2, once ArgoCD has synced `develop`.

- [ ] **Step 1: Confirm the exporter and the metric**

```bash
kubectl get pods -n monitoring -l app.kubernetes.io/name=snapraid-exporter
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=count({__name__=~"homelab_snapraid.*|node_textfile.*|node_scrape_collector_success",pod=~"snapraid-exporter-.*"})'
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=node_scrape_collector_success{collector="textfile",pod=~"snapraid-exporter-.*"}'
```

Expected: one running pod on `nas`; `node_scrape_collector_success` = 1; the count is small (a handful of series) and no `node_cpu_*`/`node_memory_*` is among them. If `homelab_snapraid_last_sync_timestamp_seconds` is still absent, the first sync has not finished yet — record that, continue, and expect Alerting in Step 4.

- [ ] **Step 2: Create the rule**

`POST https://grafana.kub.local/api/v1/provisioning/alert-rules` with `Authorization: Bearer $GRAFANA_TOKEN`, the body below. `PBFA97CFB590B2093` is the Prometheus datasource uid; the shape is `RestoreDrillOverdue`'s, with its own expression and threshold.

```json
{
  "folderUID": "homelab",
  "ruleGroup": "homelab-core",
  "title": "SnapraidParityStale",
  "condition": "C",
  "for": "10m",
  "noDataState": "Alerting",
  "execErrState": "OK",
  "isPaused": false,
  "labels": {"severity": "warning"},
  "annotations": {
    "summary": "snapraid parity is behind on {{ $labels.node }}",
    "description": "The last snapraid sync on nas finished more than 7 days ago, so writes since then are not covered by the parity and a disk lost now costs them. Check /etc/cron.d/snapraid on nas and the runner's journal (journalctl -t snapraid), then run /usr/local/sbin/snapraid-sync. If the summary reads [no value], the marker /var/lib/node-exporter/textfile/snapraid.prom or the snapraid-exporter pod is missing, not the runner: the rule reports no data as Alerting by design.",
    "runbook_url": "https://github.com/vyrtualsynthese/homelab/blob/develop/docs/reference/alerts.md"
  },
  "data": [
    {
      "refId": "A",
      "queryType": "",
      "relativeTimeRange": {"from": 600, "to": 0},
      "datasourceUid": "PBFA97CFB590B2093",
      "model": {
        "editorMode": "code",
        "expr": "time() - homelab_snapraid_last_sync_timestamp_seconds",
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
            "evaluator": {"params": [604800], "type": "gt"},
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
  ]
}
```

Expected: `201` with a `uid`. Record it.

- [ ] **Step 3: Write a current timestamp and prove Normal**

The marker is only ever written by the runner, so writing it by hand is the same write the runner makes:

```bash
ansible nas -b -m shell -a 'printf "# HELP homelab_snapraid_last_sync_timestamp_seconds Unix time of the last snapraid sync that finished without an error.\n# TYPE homelab_snapraid_last_sync_timestamp_seconds gauge\nhomelab_snapraid_last_sync_timestamp_seconds %s\n" "$(date +%s)" > /var/lib/node-exporter/textfile/snapraid.prom.tmp && chmod 0644 /var/lib/node-exporter/textfile/snapraid.prom.tmp && mv /var/lib/node-exporter/textfile/snapraid.prom.tmp /var/lib/node-exporter/textfile/snapraid.prom'
```

Wait one scrape (≤ 60 s) and one rule evaluation (≤ 300 s), then read
`GET /api/prometheus/grafana/api/v1/rules`, filtered to the rule.
Expected: `state: inactive`, `health: ok`, and `time() - homelab_snapraid_last_sync_timestamp_seconds` under 60.

- [ ] **Step 4: Silence the notification, then prove an old timestamp fires**

Create a one-hour silence through `POST /api/v1/silences` matching `alertname="SnapraidParityStale"` and record its id — the real rule must be seen firing, not heard. Then write a timestamp ten days back:

```bash
ansible nas -b -m shell -a 'printf "# HELP homelab_snapraid_last_sync_timestamp_seconds Unix time of the last snapraid sync that finished without an error.\n# TYPE homelab_snapraid_last_sync_timestamp_seconds gauge\nhomelab_snapraid_last_sync_timestamp_seconds %s\n" "$(date -d "10 days ago" +%s)" > /var/lib/node-exporter/textfile/snapraid.prom.tmp && chmod 0644 /var/lib/node-exporter/textfile/snapraid.prom.tmp && mv /var/lib/node-exporter/textfile/snapraid.prom.tmp /var/lib/node-exporter/textfile/snapraid.prom'
```

Wait for the next evaluation and the `for: 10m` window. Expected: `pending` then `firing`, the summary naming `nas`, the value about `864000`, and no Discord message because of the silence.

- [ ] **Step 5: Prove no data reports Alerting, then put the timestamp back**

Create a muted temporary rule `SnapraidParityStale-TEST` in the same group with `for: 0s`,
`noDataState: Alerting`, `notification_settings.mute_all: true` (if the API rejects that field,
record it and rely on the silence from Step 4) and expression
`time() - homelab_snapraid_last_sync_timestamp_seconds_missing`. Wait one evaluation.
Expected: the no-data state, `Alerting` — this is the shape a deleted marker or a dead runner produces.

Then delete the temporary rule, rewrite the true current timestamp with Step 3's command, delete the
silence, and confirm the rule returns to `inactive`.

- [ ] **Step 6: Read the rule back and make the page agree**

```bash
curl -sk -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["interval"], len(d["rules"])); print([ (r["title"], r["for"], r["noDataState"], r["execErrState"]) for r in d["rules"] if r["title"].startswith("Snapraid")])'
```

Expected: `300 30`, and `('SnapraidParityStale', '10m', 'Alerting', 'OK')`. If the page disagrees with this read-back, correct `docs/reference/alerts.md` in a one-file commit.

- [ ] **Step 7: Close the card**

Comment on card 713 with the API calls, their response codes, the three states and the queries that proved them; then move it to `Validation` with the branch, the PR link and that evidence in its description (`PUT /tasks/713?format=markdown`, marker file left at its real value).
