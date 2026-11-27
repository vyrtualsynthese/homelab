# CrowdSec CAPI and the community blocklist — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the LAPI online against CrowdSec's central API so a known-bad address is refused before it sends a request, without letting a blocklist decision reach a LAN or WireGuard address.

**Architecture:** One values change in the CrowdSec wrapper chart turns CAPI on and adds two config files the chart already knows how to mount: a CAPI whitelist (the LAN boundary, applied at blocklist insertion) and a console file (no custom or tainted signals leave). One Grafana alert rule is rewritten in the live database, because the blocklist's first pull raises the kernel set by thousands and would fire the burst rule.

**Tech Stack:** Helm (chart `crowdsec` 0.24.3, CrowdSec `v1.8.2`), ArgoCD, Grafana provisioning API, Python `unittest` in `hack/tests`.

**Spec:** `docs/superpowers/specs/2026-11-27-crowdsec-coverage-design.md` — workstream A (§4) and its alert fix (§4.4). The spec also designs workstreams B (authentik brute force) and C (AppSec); they get their own plans, after the gate this one ends on.

## Global Constraints

- The LAN and the tunnel are never banned: `192.168.1.0/24`, `10.8.0.0/24`, `10.42.0.0/16`, `10.43.0.0/16`, and the four node addresses `192.168.1.41`, `192.168.1.27`, `192.168.1.46`, `192.168.1.48`.
- No secret in git. The CAPI identity is a file the LAPI writes to `crowdsec-config-pvc`; nothing in this plan creates a Secret.
- `cluster/argocd/**` is CRLF; every file this plan touches is LF. 2-space YAML, no tabs.
- Commit prefixes: `🚀 feat:`, `🐛 fix:`, `📝 docs:`, `🔧 chore:`.
- Tests: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`.
- Charts: `hack/check-charts.sh --only security` (it builds the dependency in a temporary copy).
- Exposure gate: `hack/check-exposure.sh` must exit 0 before the branch is pushed.
- Docs gate: `hack/check-docs.sh --base origin/develop`.
- The Grafana rule edit in Task 2 lands **before** the branch merges: the merge is what triggers the first pull.

## Review Focus

- **A blocklist decision lands on a LAN address.** Expected: it never enters the table, because `capi_whitelists_path` filters at insertion — not excused afterwards. Task 1's test pins the exact ranges and asserts the two whitelist files agree.
- **The first blocklist pull fires `SecurityBanBurst`.** Expected: the rule counts decisions this installation made (`origin="crowdsec"`), so an arriving list is not an incident. Task 2 pins the expression in the export, and the merge waits for the live edit.
- **CAPI on, but the LAPI cannot register.** Expected: `cs_features{capi}=1` and `cs_capi_last_pull_timestamp > 0`; a silent failure leaves the stack exactly as it is today, which looks healthy. Task 4 reads both metrics, not the pod's Ready state.
- **The identity does not survive the pod.** Expected: `online_api_credentials.yaml` is on the config claim, so a rollout re-uses it instead of registering again. Task 1 asserts the claim is persistent, which is also what makes the chart render no registration Job.
- **A wrong key name in `console.yaml`.** Expected: CrowdSec reads `share_custom` and `share_tainted` and shares nothing custom or tainted. The chart renders the file with `%+v`, which renders a YAML map as Go map syntax, so the file must stay a string; Task 1 asserts both the type and the parsed keys.

---

### Task 1: CAPI on, with the blocklist filtered at insertion

**Files:**
- Modify: `cluster/helm/security/crowdsec/values.yaml`
- Test: `hack/tests/test_crowdsec_chart.py`

**Interfaces:**
- Consumes: the value shape the test already uses — `self.crowdsec` is the `crowdsec:` block of the wrapper chart's `values.yaml`, so `lapi.env` is a list of `{name, value|valueFrom}` and `config` is a dict of file contents.
- Produces: `config.capi_whitelists.yaml` and `config.console.yaml` as **strings**, and a `lapi.env` with no `DISABLE_ONLINE_API` entry. Later tasks and the runbook rely on the ConfigMaps the chart derives from them (`crowdsec-capi-whitelists`, `crowdsec-console`) and on the env var `CAPI_WHITELISTS_PATH`, which the chart sets by itself.

- [ ] **Step 1: Replace the CAPI test and add three**

In `hack/tests/test_crowdsec_chart.py`, delete `test_the_central_api_is_off` and write these four in its place:

```python
    def test_the_central_api_is_on_and_the_identity_lives_on_the_config_claim(self):
        # CAPI is the point of the workstream: the blocklist is what refuses an
        # address before it sends a request. No registration Job is rendered
        # because the chart only renders one when the config claim is *not*
        # persistent; with it, the LAPI's start script registers and writes
        # `online_api_credentials.yaml` to the claim, which the backup matrix
        # already covers, so a rebuild keeps the identity.
        env = {e["name"]: e.get("value") for e in self.crowdsec["lapi"]["env"]}
        self.assertNotIn("DISABLE_ONLINE_API", env)
        self.assertTrue(self.crowdsec["lapi"]["persistentVolume"]["config"]["enabled"])

    def test_the_blocklist_cannot_insert_a_lan_address(self):
        # A blocklist decision never passes the postoverflow whitelist: that runs
        # before a decision exists from a local alert, and CAPI inserts into the
        # database directly. This file is read at pull time instead, so the
        # address never enters the table — the only control that keeps the
        # operator's rule true for a decision nobody here made.
        raw = self.crowdsec["config"]["capi_whitelists.yaml"]
        # `%+v` on a string renders the string; on a map it renders Go syntax.
        self.assertIsInstance(raw, str)
        capi = yaml.safe_load(raw)
        self.assertEqual(set(capi["cidrs"]), set(WHITELIST_RANGES))
        self.assertEqual(
            set(capi["ips"]),
            {"192.168.1.41", "192.168.1.27", "192.168.1.46", "192.168.1.48"},
        )

    def test_the_two_whitelists_name_the_same_addresses(self):
        post = yaml.safe_load(
            self.crowdsec["config"]["postoverflows"]["s01-whitelist"]["whitelist-lan.yaml"]
        )["whitelist"]
        capi = yaml.safe_load(self.crowdsec["config"]["capi_whitelists.yaml"])
        self.assertEqual(set(capi["cidrs"]), set(post["cidr"]))
        self.assertEqual(set(capi["ips"]), set(post["ip"]))

    def test_the_engine_does_not_share_custom_or_tainted_alerts(self):
        # The official hub scenarios still leave — that is how the full list is
        # earned, and the design states it (§4.3). A local parser or a modified
        # scenario counts for nothing upstream and there is no reason to send it.
        raw = self.crowdsec["config"]["console.yaml"]
        self.assertIsInstance(raw, str)
        console = yaml.safe_load(raw)
        self.assertIs(console["share_custom"], False)
        self.assertIs(console["share_tainted"], False)
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_crowdsec_chart.py`

Expected: 4 failures — `assertNotIn` finds `DISABLE_ONLINE_API`, and the three reads raise `KeyError: 'capi_whitelists.yaml'` / `'console.yaml'`.

- [ ] **Step 3: Take the LAPI online**

In `cluster/helm/security/crowdsec/values.yaml`, replace the whole block that begins with the comment
line `# CAPI off. Without this the LAPI registers` and ends with the entry `value: "true"` (the last
entry of `lapi.env`) with:

```yaml
      # CAPI is on, and this comment is the record of what that means. The LAPI
      # registers itself on its next start and writes
      # `online_api_credentials.yaml` to the config claim below, which is a backup
      # row, so a rebuild keeps the identity instead of registering again. The
      # chart renders no registration Job when that claim is persistent; the
      # start script does it.
      #
      # What leaves, per alert: the scenario's name and hash, the timestamp, the
      # machine id, the offending address and its geolocation; at sign-in, the
      # list of scenarios this engine runs. Nothing else — no URL, no header, no
      # user name. The design states the trade in §4.3.
```

- [ ] **Step 4: Add the two config files**

In the same file, inside `config:`, insert these two keys just before the comment block that begins
`# The chart's default value here holds the auto-registration block` (that is, after the
`postoverflows:` block and before `config.yaml.local:`):

```yaml
    # The blocklist's own LAN boundary, and it is a second file for a reason: a
    # decision that arrives from CAPI never passes through the postoverflow
    # above. That one runs before a decision is created from a local alert, and
    # CAPI inserts into the database directly, so a whitelist written as a
    # postoverflow would excuse nothing here. This file is read at pull time by
    # `api.server.capi_whitelists_path`, which the chart points at it, and an
    # address in it never enters the table at all.
    #
    # The ranges repeat `homelab/whitelist-lan` on purpose; a test fails when the
    # two disagree. Deprecated upstream in favour of the centralized allowlists,
    # which live in the LAPI database and are therefore state outside git — this
    # stays until that migration is a decision of its own.
    capi_whitelists.yaml: |
      ips:
        - "192.168.1.41"
        - "192.168.1.27"
        - "192.168.1.46"
        - "192.168.1.48"
      cidrs:
        - "192.168.1.0/24"
        - "10.8.0.0/24"
        - "10.42.0.0/16"
        - "10.43.0.0/16"

    # Signals from scenarios that are not the official, unmodified hub set count
    # for nothing upstream, so they are not sent. The official set still leaves;
    # that is the price of the list, and §4.3 of the design says so.
    console.yaml: |
      share_custom: false
      share_tainted: false
```

- [ ] **Step 5: Run the tests to watch them pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_crowdsec_chart.py`

Expected: all pass, including the four new ones.

- [ ] **Step 6: Prove the chart still renders, and that no registration Job appears**

Run: `hack/check-charts.sh --only security`

Expected: no `FAIL` line for `cluster/helm/security/crowdsec`. Then check the derived objects by hand, because a missing mount here is silent. The wrapper chart's `charts/` and `Chart.lock` are gitignored, so build once in place:

```bash
helm dependency build cluster/helm/security/crowdsec
helm template t cluster/helm/security/crowdsec -f cluster/helm/security/crowdsec/values.yaml \
  | grep -E "capi-whitelists|CAPI_WHITELISTS_PATH|kind: Job"
```

Expected: a `crowdsec-capi-whitelists` ConfigMap, a `CAPI_WHITELISTS_PATH` env entry, and **no** `kind: Job`. A Job means the chart took the other registration path, which writes the credentials to a Secret this repository does not make. `git status` stays clean afterwards.

- [ ] **Step 7: Run the whole suite and commit**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
git add cluster/helm/security/crowdsec/values.yaml hack/tests/test_crowdsec_chart.py
git commit -m "🚀 feat(crowdsec): enable CAPI, with the blocklist filtered before insertion"
```

---

### Task 2: The burst rule stops counting the blocklist

**Files:**
- Create: `hack/tests/test_security_alert_export.py`
- Modify: `cluster/helm/monitoring/grafana/export/security/rules.json`

**Interfaces:**
- Consumes: the export file is a JSON **list** of rule objects, each with `title`, `condition`, and `data` (a list of query steps; `data[0].model.expr` is the PromQL, `data[1].model.conditions[0].evaluator.params` is the threshold).
- Produces: `SecurityBanBurst` reads `delta(sum(cs_active_decisions{origin="crowdsec"})[10m:1m])`, both in the export and live. `cs_active_decisions` labels are `action`, `origin`, `reason`, `type` — there is **no address label**, so this rule can never be written per-address.

- [ ] **Step 1: Write the failing test**

Create `hack/tests/test_security_alert_export.py`:

```python
"""The exported security rules, checked where a rule changed meaning.

`SecurityBanBurst` used to read the size of the kernel's ban set. That set is a
gauge, so the first CAPI blocklist pull raises it by thousands in one step and
the rule fires — on the one alert meant to say "something is happening here".
It now counts the decisions this installation created, which is what a burst is.
"""

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPORT = ROOT / "cluster/helm/monitoring/grafana/export/security/rules.json"

LOCAL_BURST = 'delta(sum(cs_active_decisions{origin="crowdsec"})[10m:1m])'


class SecurityAlertExportTests(unittest.TestCase):
    def setUp(self):
        self.rules = {rule["title"]: rule for rule in json.loads(EXPORT.read_text())}

    def test_the_burst_rule_counts_this_installation_and_not_the_blocklist(self):
        rule = self.rules["SecurityBanBurst"]
        self.assertEqual(rule["data"][0]["model"]["expr"], LOCAL_BURST)
        # The kernel set is what a blocklist pull moves. Reading it here is the
        # defect the expression change removes, so the whole rule is checked.
        self.assertNotIn("fw_bouncer_banned_ips", json.dumps(rule))

    def test_the_burst_threshold_is_still_twenty_in_ten_minutes(self):
        condition = self.rules["SecurityBanBurst"]["data"][1]["model"]["conditions"][0]
        self.assertEqual(condition["evaluator"]["params"], [20])
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_security_alert_export.py`

Expected: `test_the_burst_rule_counts_this_installation_and_not_the_blocklist` FAILS with the old `delta(sum(fw_bouncer_banned_ips)[10m:1m])`. The threshold test passes — it is there to catch the edit widening the rule by accident.

- [ ] **Step 3: Change the export**

```bash
jq 'map(if .title == "SecurityBanBurst"
        then .data[0].model.expr = "delta(sum(cs_active_decisions{origin=\"crowdsec\"})[10m:1m])"
        else . end)' \
  cluster/helm/monitoring/grafana/export/security/rules.json > /tmp/opencode/rules.json \
  && mv /tmp/opencode/rules.json cluster/helm/monitoring/grafana/export/security/rules.json
```

- [ ] **Step 4: Run the test to watch it pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_security_alert_export.py`

Expected: both pass.

- [ ] **Step 5: Apply the same change to the live rule, before the merge**

The export is a copy nothing applies. The live rule is in Grafana's database:

```bash
set -a; . "$HOME/.config/secrets/homelab.env"; set +a
CA="$HOME/.config/secrets/kub-local/local-ca.pem"
API="https://grafana.kub.local/api/v1/provisioning/alert-rules"

curl -sG --cacert "$CA" -H "Authorization: Bearer $GRAFANA_TOKEN" "$API" \
  | jq '.[] | select(.title=="SecurityBanBurst")' > /tmp/opencode/rule.json

jq '.data[0].model.expr = "delta(sum(cs_active_decisions{origin=\"crowdsec\"})[10m:1m])"
    | del(.id, .updated, .provenance)' /tmp/opencode/rule.json > /tmp/opencode/rule.put.json

UID_=$(jq -r .uid /tmp/opencode/rule.json)
curl -s -X PUT --cacert "$CA" -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -H 'Content-Type: application/json' --data @/tmp/opencode/rule.put.json "$API/$UID_" \
  | jq -r '.title + " " + .data[0].model.expr'
```

Expected: `SecurityBanBurst delta(sum(cs_active_decisions{origin="crowdsec"})[10m:1m])`. `id`, `updated` and `provenance` are read-only on the way in; leaving them in the body is a `400`. If the token has no write scope, the operator applies the same body by hand — record which.

- [ ] **Step 6: Commit**

```bash
git add hack/tests/test_security_alert_export.py cluster/helm/monitoring/grafana/export/security/rules.json
git commit -m "🐛 fix(grafana): count local decisions in the ban burst, not the blocklist"
```

---

### Task 3: The two pages that would otherwise lie

**Files:**
- Modify: `docs/reference/alerts.md` (the `## Known gaps` bullet, and the two tables that name `SecurityBanBurst`)
- Modify: `docs/runbooks/crowdsec.md` (`## What is where`, and a fifth proof)

**Interfaces:**
- Consumes: the metric names the design and this plan read — `cs_features{feature="capi"}`, `cs_capi_last_pull_timestamp`, `cs_active_decisions{origin="CAPI"}`.
- Produces: the runbook's proof 5, which Task 4 runs.

- [ ] **Step 1: Replace the stale gap bullet**

In `docs/reference/alerts.md`, the bullet that begins `- **CrowdSec is watched for silence, not for a dead poll.**` is wrong today: `SecurityBouncerStalled` exists, is in `rules.json`, and reads `rate(cs_lapi_bouncer_requests_total[10m])`. Replace the whole bullet with:

```markdown
- **The bouncer rule proves a poll, not an accepted one.** `SecurityBouncerStalled` fires when
  `rate(cs_lapi_bouncer_requests_total[10m])` reaches zero, which is the bouncer process gone or
  hung. The metric carries `bouncer`, `route` and `method` and no status, so a bouncer holding a
  revoked or wrong key still polls every ten seconds, is refused every time, and bans nothing while
  the rule stays green. The LAPI's log (`kubectl -n security logs deploy/crowdsec-lapi | grep 401`)
  is what names that case; no rule reads it.
```

- [ ] **Step 2: Correct the two table rows that name the old trigger**

The same page carries `SecurityBanBurst` twice more, in the summary table and in the
"what reads what" table. Both still describe the kernel set:

```markdown
| `SecurityBanBurst` | more than 20 new decisions created here in 10 minutes | 5m | warning | inactive |
```

for the summary row (it currently reads `more than 20 new bans on the \`streaming\` bouncer in 10
minutes`), and

```markdown
| `SecurityBanBurst` | `delta(sum(cs_active_decisions{origin="crowdsec"})[10m:1m])`, Prometheus | one or two bans a day is the normal rate; twenty decisions in ten minutes is a scenario matching a stream. The kernel set is not read: the first blocklist pull raises it by thousands in one step |
```

for the detail row (it currently reads `delta(sum(fw_bouncer_banned_ips)[10m:1m])` and `twenty in ten
minutes is a scenario matching a stream`).

- [ ] **Step 3: Add the CAPI rows to the runbook's table**

In `docs/runbooks/crowdsec.md`, in `## What is where`, after the `Decisions` row:

```markdown
| Blocklist decisions, and whether CAPI is registered | the LAPI | `kubectl -n security exec deploy/crowdsec-lapi -- cscli capi status` |
| What the blocklist may never insert | `capi_whitelists.yaml` on the config claim | `kubectl -n security exec deploy/crowdsec-lapi -- cat /etc/crowdsec_data/capi_whitelists.yaml` |
```

- [ ] **Step 4: Add proof 5**

After proof 4 in `## The four proofs, run once when this was built`, add — and change that heading to
`## The five proofs, run once when each piece was built`, and in the banner at the top of the page
change `the four proofs below have not been run` to `the five proofs below have not been run`:

```markdown
5. **The blocklist cannot reach the LAN.** After the first CAPI pull,
   `cscli decisions list --origin CAPI -o json` names addresses, and none of them falls in
   `192.168.1.0/24` or `10.8.0.0/24`; `nft list set ip crowdsec crowdsec-blacklists` holds the same
   addresses and no LAN one. The file that makes this true is
   `/etc/crowdsec_data/capi_whitelists.yaml`, read at pull time — a decision already in the table is
   *not* retroactively removed, which is why the check reads the list and not a count.
```

- [ ] **Step 5: Run the two branch gates**

```bash
hack/check-docs.sh --base origin/develop
hack/check-exposure.sh
```

Expected: both exit 0. The runbook is in the nav already; a page touched on this branch must carry
its banner, which it does. No route changes on this branch, so the exposure gate is a regression
check rather than a proof of this work — it is here because the repository requires it before a PR.

- [ ] **Step 6: Commit**

```bash
git add docs/reference/alerts.md docs/runbooks/crowdsec.md
git commit -m "📝 docs(security): record CAPI, and the gap the bouncer rule leaves"
```

---

### Task 4: After the merge — prove it, and record it

**Files:**
- Modify: `docs/runbooks/crowdsec.md` (`## When these were last run`, and the banner)
- Modify: `docs/runbooks/README.md` (the `crowdsec.md` row's status cell)

**Interfaces:**
- Consumes: what Tasks 1–3 shipped. A pod restart of the LAPI is ArgoCD's, not this task's.
- Produces: one dated line per proof, as that section demands, and the `proved <date>` state.

- [ ] **Step 1: Confirm the stack converged, and that CAPI is actually registered**

```bash
set -a; . "$HOME/.config/secrets/homelab.env"; set +a
export KUBECONFIG="$HOME/.kube/mcp-readonly.yaml"
kubectl -n security get pods -l k8s-app=crowdsec

for q in 'cs_features{feature="capi",type="lapi"}' 'cs_capi_last_pull_timestamp{type="lapi"}'; do
  printf '%s -> ' "$q"
  curl -sG -u "ops:$PROMETHEUS_AUTH_PASSWORD" http://prometheus.kub.local/api/v1/query \
    --data-urlencode "query=$q" | jq -r '.data.result[] | "\(.metric.type // "-") \(.value[1])"'
done
```

Expected: the LAPI and the agent each Ready (a new LAPI pod, because the values changed). `cs_features{capi}` is `1` on the `lapi` series, and `cs_capi_last_pull_timestamp` is a Unix timestamp from the last few minutes — not `0`. A `0` after five minutes means registration failed: read `kubectl -n security logs deploy/crowdsec-lapi | grep -i capi`.

- [ ] **Step 2: Read the blocklist decisions, and prove the LAN is absent**

`kubectl exec` answers `403` on the read-only kubeconfig that Step 1 exported, so this step and the
next two run in a shell with the admin one. The operator runs them, as the runbook says:

```bash
export KUBECONFIG="$HOME/.kube/config"
set -a; . "$HOME/.config/secrets/homelab.env"; set +a
kubectl -n security exec deploy/crowdsec-lapi -- cscli decisions list --origin CAPI -o json \
  | jq -r '.[].value'
```

Expected: a list of public addresses. Any line in `192.168.1.*` or `10.8.0.*` is a failure of Task 1, not a note: stop, and check that `capi_whitelists.yaml` is mounted and that `cscli capi status` names the file.

- [ ] **Step 3: Confirm the kernel set moved, and the burst rule stayed quiet**

In a shell on `streaming` itself — `nft` reads this host's kernel, and the cluster's read-only
kubeconfig does not reach a node's shell — count what the kernel actually holds and prove no LAN
address is among it:

```bash
sudo nft -j list set ip crowdsec crowdsec-blacklists \
  | jq '[.nftables[] | select(.set) | .set.elem // []] | add | length'
sudo nft -j list set ip crowdsec crowdsec-blacklists \
  | jq -r '.nftables[] | select(.set) | .set.elem // [] | .[]' \
  | grep -cE '^(192\.168\.1|10\.8\.0)\.' || true
```

Expected: a number in the hundreds or thousands, then `0`. A non-zero second line is the LAN being banned: stop and read Task 3's proof 5. Then read the rule's expression:

```bash
curl -sG --cacert "$HOME/.config/secrets/kub-local/local-ca.pem" \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/v1/provisioning/alert-rules" \
  | jq -r '.[] | select(.title=="SecurityBanBurst") | "\(.title) \(.data[0].model.expr)"'
```

Expected: the local-origin expression, and no firing instance of the rule in Grafana's alert list. If it fired at the moment of the pull, the live rule still held the old expression — Task 2's Step 5 did not land before the merge.

- [ ] **Step 4: Prove the identity survives the pod**

```bash
kubectl -n security rollout restart deploy/crowdsec-lapi
kubectl -n security rollout status deploy/crowdsec-lapi --timeout=180s
kubectl -n security exec deploy/crowdsec-lapi -- cscli capi status
```

Expected: registered, with the same machine id as before the restart (the credentials file on the claim was read, not rewritten). The machine id is in `online_api_credentials.yaml`; read it with `cat` on the claim if the command does not print it.

- [ ] **Step 5: Write the results into the runbook, and commit**

Replace the opening sentence of `## When these were last run` — the one that says `Not yet.` — with
the dated result, one line per proof actually run (1–5), each naming the command and the output it
gave. Then set the page's banner field `**Verified** not yet: the five proofs below have not been
run` to `**Verified** <date>: the five proofs were run`, replace the page's own line
`**Status: pending — the five proofs below have not been run.**` with `**Status: proved <date>.**`,
and in `docs/runbooks/README.md` replace the `crowdsec.md` row's status cell
`pending — the four proofs have not been run` with `proved <date>`. Then:

```bash
git add docs/runbooks/crowdsec.md docs/runbooks/README.md
git commit -m "📝 docs(crowdsec): record the five proofs, CAPI included"
```

---

## What this plan does not do

Workstreams B and C are designed in the same spec and deliberately not planned here: B changes how
an existing workload (the agent) runs, and C puts a WAF in front of the sign-in page — each is its
own branch, its own merge and its own proof. The spec's §7 gates them behind this one.
