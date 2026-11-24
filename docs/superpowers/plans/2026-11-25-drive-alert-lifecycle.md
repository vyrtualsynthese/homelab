# Drive alerts: worn, failing, and the board — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A worn drive asks to be replaced once and then stays quiet; a failing drive pages hourly; and every notification Grafana routes to the relay becomes a card on the `alerts` board.

**Architecture:** The relay stops deciding what belongs on the board — Grafana's routing tree decides, with a `board=card` label on the rules that should produce a card. Two Grafana rules replace `DriveNeedsReplacement`: `DriveWorn` (life under 10 %, or smartctl exit bit 32) warns once, `DriveFailing` (SMART failed, sector counters, the damage bits of the exit code) repeats hourly as a critical. The Disks dashboard drops six KPI stats for a verdict table.

**Tech Stack:** Python 3 (`http.server`, `unittest` via `pytest`), Helm chart ConfigMap, Grafana HTTP API (`/api/v1/provisioning/*`, alertmanager config API), PromQL, Vikunja REST v2.

**Spec:** [`docs/superpowers/specs/2026-11-25-drive-alert-lifecycle-design.md`](../specs/2026-11-25-drive-alert-lifecycle-design.md)

## Global Constraints

- **The rules and the dashboard live in Grafana's database, not in git** (`AGENTS.md` §9). They are applied with `$GRAFANA_TOKEN` from `~/.config/secrets/homelab.env` against `https://grafana.kub.local` (TLS is internal: always `-k`). Only the relay code and the docs are in git.
- **Never `kubectl apply/edit` anything.** The relay is deployed by ArgoCD from `develop`; a hand-applied fix is reverted by self-heal.
- **The relay ships by pull request, and the operator merges it.** Tasks 4–8 depend on the new pod: do not start them until `kubectl -n monitoring logs deploy/alert-relay` shows the new line format.
- **Agent shells are read-only** (`KUBECONFIG=~/.kube/mcp-readonly.yaml`): `kubectl exec`, `cp` and `port-forward` answer 403. Grafana's API is the way in.
- **Never use `$VIKUNJA_API_TOKEN`.** The agent's token is `$VIKUNJA_MCP_TOKEN`; project 9 (`alerts`) is not a session's project and the agent cannot read it — a card there is verified through the relay's own log line and the operator's glance.
- **The `label_replace` chain is byte-for-byte the live one.** Nine calls: `host` from `node` with `(.*)`, then six instance overrides on port 9100 (`nas` 192.168.1.41, `nuc0` .27, `streaming` .46, `rbp2` .48, `rbp0` .22, `rbp1` .38) and two on port 9902 (`rbp0` .22, `rbp1` .38), all setting `host`.
- **Commit messages use a gitmoji prefix** per `AGENTS.md` §4.
- **The full suite must pass before any completion claim:** `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`, and `hack/check-docs.sh` with no failure.

## Review Focus

Inputs the spec implies and no happy-path test covers. Each has a test in the task that owns the code.

1. **A resolve that arrives with no open card.** A test notification, or a rule deleted mid-flight, sends `status: resolved` for an incident that never opened one. Today the relay would *open a card for a resolve*. It must write nothing. (Task 1.)
2. **A group carrying both a warning and a critical.** The retired filter dropped the warning from the group and the card under-reported it. Every alert in the group reaches the card now. (Task 1.)
3. **`exit_code` = 32 on a healthy drive.** The whole point: bit 32 must raise `DriveWorn` and must not raise `DriveFailing`. (Task 4.)
4. **A drive with no `smart_passed` series** — exporter down, drive pulled. It disappears from the table and from both rules; `ScrapeTargetDown` is the rule that covers it, and the "Drives monitored" stat is what makes the disappearance visible. (Task 6.)
5. **A rule that is both worn and failing.** Two rules, two notifications, two cards with different group keys. Accepted; the page is the one that matters. (Task 4.)

---

### Task 1: The relay creates a card for every notification it receives

**Files:**
- Modify: `cluster/helm/monitoring/alert-relay/files/relay.py`
- Test: `hack/tests/test_alert_relay.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `delivery_line(payload: dict, outcome: str) -> str`, and `handle(payload: dict, board, cooldown: float, now: datetime | None = None) -> str` whose outcomes are `opened #N`, `commented on #N`, `throttled on #N`, `resolved on #N`, `resolved with no card`. `is_critical` is deleted.

- [ ] **Step 1: Write the failing tests**

In `hack/tests/test_alert_relay.py`:

- delete `class SeverityTests` entirely (its function is going away);
- change `FakeBoard.open_card` to record the description: `self.calls.append(("open", title, description))`;
- replace `test_a_warning_writes_nothing` with the three tests below;
- add the `DeliveryLineTests` class.

```python
class DeliveryLineTests(unittest.TestCase):
    def test_the_line_names_the_rule_and_the_outcome(self):
        self.assertEqual(relay.delivery_line(payload(), "opened #42"),
                         "firing: DiskFull on nas: opened #42")
```

```python
class HandleTests(unittest.TestCase):
    def test_a_warning_opens_a_card(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(severity="warning"), board, 600), "opened #42")
        self.assertEqual(board.calls[0][0], "open")

    def test_a_mixed_group_keeps_the_warning(self):
        p = payload()
        p["alerts"].append({
            "status": "firing", "fingerprint": "w1",
            "labels": {"alertname": "DiskFull", "severity": "warning", "instance": "nuc0"},
            "annotations": {},
        })
        board = FakeBoard()
        relay.handle(p, board, 600)
        self.assertIn("carries 2 alerts", board.calls[0][2])

    def test_a_resolve_without_a_card_writes_nothing(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(status="resolved"), board, 600),
                         "resolved with no card")
        self.assertEqual(board.calls, [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_relay.py`
Expected: `AttributeError: module 'relay' has no attribute 'delivery_line'`, and the warning test asserting `ignored: not critical`.

- [ ] **Step 3: Change `relay.py`**

- In the module docstring, replace the rule `* a warning is logged and dropped — only critical reaches the board;` with `* every delivered notification gets a card — Grafana's routing decides what belongs here;`.
- Delete `is_critical` and its comment block.
- In `handle`, delete the `alerts = [a for a in … if is_critical(a)]` filter and the `if not alerts: return "ignored: not critical"` branch, and drop `payload = dict(payload, alerts=alerts)`.
- In `handle`, before `board.find_open_card`, add:

```python
    resolved = (payload.get("status") or "").lower() == "resolved"
```

- After `card = board.find_open_card(key)`, add:

```python
    if card is None and resolved:
        return "resolved with no card"
```

and remove the later `resolved = …` assignment (it is now computed above).

- Add above `handle`:

```python
def delivery_line(payload: dict, outcome: str) -> str:
    """One line per delivery, so a silent relay is obvious in its own log."""
    status = str(payload.get("status") or "firing").lower()
    return f"{status}: {render_title(payload)}: {outcome}"
```

- In `do_POST`, replace `print(f"{payload.get('status')}: {outcome}", flush=True)` with `print(delivery_line(payload, outcome), flush=True)`.

One detail the docstring must keep: a resolve is still commented on an open card (`resolved on #N`) even inside the cooldown, as today.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_relay.py`
Expected: all pass, 0 failures.

- [ ] **Step 5: Run the whole suite**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
Expected: no failure (the count is one higher than before: one class removed, four tests added).

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/monitoring/alert-relay/files/relay.py hack/tests/test_alert_relay.py
git commit -m "🚀 feat: the relay cards whatever Grafana routes to it, and drops a stray resolve"
```

---

### Task 2: The relay's page says what the relay now does

**Files:**
- Modify: `docs/runbooks/alert-relay.md`

**Interfaces:**
- Consumes: Task 1's behaviour (`opened` / `commented` / `throttled` / `resolved` / `resolved with no card`, no severity filter).
- Produces: nothing code-facing; the page Task 7 links to.

- [ ] **Step 1: Rewrite the behaviour table**

In `docs/runbooks/alert-relay.md`, the table row `| firing, severity not critical | logs ignored: not critical, writes nothing |` becomes two facts:

- **who decides**: "Grafana's routing tree decides what reaches the relay: a rule routed to `vikunja-alerts` gets a card. The relay has no severity filter — it used to, and the filter hid a path that carried nothing for nine days."
- **what it does per delivery**: open one card per incident, comment on a repeat, comment on a resolve, never close; a resolve that arrives with no open card writes nothing.

- [ ] **Step 2: Add the test-send procedure**

Add a "Prove it end to end" section with the exact call, which is the only way to exercise the path without waiting for a rule:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -sk -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" -H "Content-Type: application/json" \
  -d '{"integration":{"type":"webhook","settings":{"url":"http://alert-relay.monitoring.svc.cluster.local:8080/alert"}}}' \
  "https://grafana.kub.local/apis/notifications.alerting.grafana.app/v0alpha1/namespaces/default/receivers/dmlrdW5qYS1hbGVydHM/test"
```

Expected: `{"status":"success",…}` and a `…: opened #N` line in `kubectl -n monitoring logs deploy/alert-relay`. Delete the test card afterwards — it is titled after the test alert, not a real incident.

- [ ] **Step 3: Correct the failure modes list**

Where the page lists what can go wrong, add the one that actually happened: "nothing arrives at all — check `kubectl -n monitoring logs deploy/alert-relay`; an empty log means Grafana never dispatched, and the routing tree is the place to look, not the relay."

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/alert-relay.md
git commit -m "📝 docs: the routing tree decides what reaches the board, not the relay"
```

---

### Task 3: Pull request, merge, and the pod that proves it

**Files:** none.

**Interfaces:**
- Consumes: Tasks 1–2 on `feature/drive-alert-lifecycle`.
- Produces: the deployed relay, which every later task depends on.

- [ ] **Step 1: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push -u origin feature/drive-alert-lifecycle
```

- [ ] **Step 2: Open the pull request**

Use `github_create_pull_request`, base `develop`, title `The relay cards what Grafana routes to it`, body: what changed, the test counts before and after, and the note that the Grafana-side change is applied after merge. Search the repo for a pull request template first and follow it if one exists.

- [ ] **Step 3: Ask the operator to merge**

Do not merge it yourself. Say in one line that the rest of the plan waits on the merge, then continue when the operator says it is merged.

- [ ] **Step 4: Confirm the pod runs the new code**

```bash
KUBECONFIG=$HOME/.kube/mcp-readonly.yaml kubectl -n monitoring rollout status deploy/alert-relay --timeout=180s
KUBECONFIG=$HOME/.kube/mcp-readonly.yaml kubectl -n monitoring get deploy alert-relay \
  -o jsonpath='{.spec.template.metadata.annotations.checksum/config}{"\n"}'
```

Expected: rollout complete, and the checksum differs from the one recorded in Task 1 (the ConfigMap annotation changes with `relay.py`).

- [ ] **Step 5: The end-to-end proof**

Run the call from Task 2 Step 2.

Expected: Grafana answers `success`; the relay logs `…: opened #N`; a card exists on the `alerts` board. This is the check that has never passed — record the log line and the card number in the pull request as a comment. Ask the operator to delete the test card.

---

### Task 4: The two rules, and the route that carries the worn notice

**Files:** none in the repo. The rules live in Grafana; their UIDs go into the docs in Task 7.

**Interfaces:**
- Consumes: the live rule `bg41fw2txhpfke` (its `data` array is the template, and its label_replace chain is reused unchanged), and the live routing policy.
- Produces: rules `DriveWorn` and `DriveFailing`, a routing child matched on `board=card`, and their UIDs.

- [ ] **Step 1: Build both payloads from the live rule**

Run this from `bash` with `$GRAFANA_TOKEN` exported. It fetches the current rule, keeps `data[1]` (the threshold node) as-is, and rewrites only `data[0].model.expr`, the metadata and the labels. Keep it in `/tmp/opencode`, not in the repo.

```python
import json, os, copy, urllib.request

BASE = "https://grafana.kub.local"
H = {"Authorization": f"Bearer {os.environ['GRAFANA_TOKEN']}", "Content-Type": "application/json"}

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, method=method, headers=H),
                                context=__import__("ssl")._create_unverified_context()) as r:
        return json.loads(r.read() or b"null")

CHAIN = [("node", "$1", "(.*)"), ("instance", "nas", "192.168.1.41:9100"),
         ("instance", "nuc0", "192.168.1.27:9100"), ("instance", "streaming", "192.168.1.46:9100"),
         ("instance", "rbp2", "192.168.1.48:9100"), ("instance", "rbp0", "192.168.1.22:9100"),
         ("instance", "rbp1", "192.168.1.38:9100"), ("instance", "rbp0", "192.168.1.22:9902"),
         ("instance", "rbp1", "192.168.1.38:9902")]

def chain(expr):
    for src, value, regex in CHAIN:
        expr = f'label_replace({expr}, "host", "{value}", "{src}", "{regex}")'
    return expr

B = "max by (node, instance, drive, model_name, serial_number, user_capacity, type)"
LIFE = f"({B} (smartprom_percent_lifetime_remain) or (100 - {B} (smartprom_percentage_used)) or {B} (smartprom_wear_leveling_count))"
EXIT = f"{B} (smartprom_exit_code)"

WORN = chain(f"((10 - {LIFE}) > 0) or ((floor({EXIT} / 32) % 2 == 1) > 0)")
FAILING = chain(
    f"(((1 - {B} (smartprom_smart_passed)) > 0)"
    f" or (({EXIT} % 32) > 0)"
    f" or ((floor({EXIT} / 64) % 2 == 1) > 0)"
    f" or ({B} (smartprom_current_pending_sector_raw) > 0)"
    f" or ({B} (smartprom_offline_uncorrectable_raw) > 0))"
)

for name, expr in (("worn", WORN), ("failing", FAILING)):
    open(f"/tmp/opencode/{name}.expr", "w").write(expr)
    print(name, len(expr), "bytes")
```

Both rules keep the copied rule's `folderUID: homelab` and `ruleGroup: homelab-core` — the 300 s group,
which is right for a `for` of 1 h and of 10 m.

- [ ] **Step 2: Check both expressions before creating anything**

Evaluate each through the datasource proxy — the same Prometheus the rules read:

```bash
DS=PBFA97CFB590B2093
curl -sk -H "Authorization: Bearer $GRAFANA_TOKEN" \
  --data-urlencode "query=$(cat /tmp/opencode/worn.expr)" \
  "https://grafana.kub.local/api/datasources/proxy/uid/$DS/api/v1/query"
```

Expected, measured 2026-11-25:
- `DriveWorn` → exactly one series, `drive="/dev/sdf"`, `host="nas"`.
- `DriveFailing` → zero series. A drive that is both worn and failing raises both, which is accepted:
  the page is the one that matters.

- [ ] **Step 3: Create the rules**

Copy the live rule twice, set `title`, `for`, `labels`, `annotations` and the expression, and clear `id`, `uid`, `updated`, `version`:

| | `DriveWorn` | `DriveFailing` |
|---|---|---|
| `for` | `1h` | `10m` |
| `labels` | `{"severity": "warning", "board": "card"}` | `{"severity": "critical"}` |
| `notification_settings` | `{"repeat_interval": "8760h"}` | `null` |
| `condition` | `C` | `C` |
| `noDataState` / `execErrState` | `OK` / `OK` | `OK` / `OK` |
| `annotations.summary` | `{{ $labels.host }} {{ $labels.drive }} has used up its rated life` | `{{ $labels.host }} {{ $labels.drive }} is failing` |
| `annotations.description` | `Nothing is failing yet: SMART passes and no sectors are pending. Replace it when convenient, then close this. https://grafana.kub.local/d/homelab-disks` | `Move its data off now. A hard failure signal is set: SMART failed, a sector counter is above zero, or the checker reported an error. https://grafana.kub.local/d/homelab-disks` |

POST each to `/api/v1/provisioning/alert-rules`, then `GET` each by UID and confirm `for`, `labels` and `notification_settings` came back as sent.

- [ ] **Step 4: Prove the one-time repeat is real**

`GET /api/alertmanager/grafana/api/v2/status` and find the network's generated config. Expected: a route carrying `"repeat_interval": "8760h"` in the alertmanager subtree for `DriveWorn`.

If it is absent, Grafana ignored the per-rule setting. Fallback, and note it in the docs: leave `notification_settings` unset and add a routing child `{"receiver": "vikunja-alerts", "object_matchers": [["alertname", "=", "DriveWorn"]], "repeat_interval": "8760h"}` alongside the `board=card` route in Step 5.

- [ ] **Step 5: Route `board=card` to the relay**

```bash
curl -sk -H "Authorization: Bearer $GRAFANA_TOKEN" "https://grafana.kub.local/api/v1/provisioning/policies" > /tmp/opencode/policy.json
```

Add one child to `routes`, keeping the two existing ones untouched:

```json
{"receiver": "vikunja-alerts", "object_matchers": [["board", "=", "card"]], "group_wait": "30s", "group_interval": "5m"}
```

PUT the whole policy back to the same path. Then re-read `/api/alertmanager/grafana/api/v2/status` and confirm a route matched on `board` exists and names `vikunja-alerts`.

The worn notice's Discord copy needs nothing: it is a warning, so the root route delivers it to `discord-homelab`, and the 8760 h override replaces the root's 12 h repeat. Confirm in the same generated config that the root still names `discord-homelab`.

No commit: this task changes Grafana, and Task 7 records the UIDs.

---

### Task 5: Retire the old rule and its silence

**Files:** none.

**Interfaces:**
- Consumes: Task 4's rules, both proven by evaluation.
- Produces: one drive rule pair instead of one rule plus one mute.

- [ ] **Step 1: Confirm the old rule fires nothing new**

`GET /api/v1/provisioning/alert-rules/bg41fw2txhpfke` and check its state through `/api/alertmanager/grafana/api/v2/alerts` — the `/dev/sdf` alert must now appear as `DriveWorn`, not as `DriveNeedsReplacement`. If the old rule still owns the only alert, give the new rules one evaluation cycle (`homelab-core` runs every 300 s) and re-read.

- [ ] **Step 2: Delete the old rule**

```bash
curl -sk -X DELETE -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/v1/provisioning/alert-rules/bg41fw2txhpfke"
```

- [ ] **Step 3: Delete the silence**

```bash
curl -sk -X DELETE -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/alertmanager/grafana/api/v2/silence/0110b899-d27f-4a8f-9af8-59e8b157de05"
```

Then `GET …/api/v2/silences` and expect only the two thermal silences and the expired ones.

- [ ] **Step 4: Confirm the state is one warning and no page**

`GET /api/alertmanager/grafana/api/v2/alerts`: `/dev/sdf` appears once, `alertname=DriveWorn`, `severity=warning`, `silencedBy` empty. No `DriveNeedsReplacement` anywhere.

No commit.

---

### Task 6: The dashboard says which drives need action

**Files:** none in the repo; the dashboard is in Grafana (`uid: homelab-disks`, folder `Homelab`).

**Interfaces:**
- Consumes: the expressions of Task 4 (score reuses them).
- Produces: six panels and one saved dashboard version.

- [ ] **Step 1: Save the current dashboard as a file first**

```bash
curl -sk -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/dashboards/uid/homelab-disks" > /tmp/opencode/disks-before.json
```

This is the rollback if the new layout is worse.

- [ ] **Step 2: Build the nine table queries around one join key**

Every table query keeps the live table's pattern: the two edge-Pi `label_replace` overrides (`"node","rbp0","instance","192.168.1.22:9902"`, `"node","rbp1","instance","192.168.1.38:9902"`) and `label_join(…, "id", "|", "instance", "drive")`, so Grafana joins them into one row per drive.

Columns and their expressions, with `B` and `LIFE` as defined in Task 4 Step 1:

| Column | Query |
|---|---|
| Verdict | `(failing * 3) or ((((life) < bool 10) * 2) > 0) or ((((life) < bool 25) * 1) > 0) or (0 * B (smartprom_smart_passed))` |
| Host | `B (smartprom_smart_passed)` (the `host` label; two edge drives have none) |
| Drive | `B (smartprom_smart_passed)` |
| Model | `B (smartprom_smart_passed)` |
| Life % | `LIFE` |
| Temp °C | `B (smartprom_temperature_celsius_raw) or B (smartprom_temperature)` |
| Realloc | `B (smartprom_reallocated_sector_ct_raw)` |
| Pending | `B (smartprom_current_pending_sector_raw)` |
| Age (y) | `(B (smartprom_power_on_hours_raw) or B (smartprom_power_on_hours)) / 8766` |

The Verdict query uses the **unchained** forms of `life` and `failing` — the same inner expressions
as Task 4 Step 1 without the nine `label_replace` calls. `or` matches on the whole label set, so a
chained `failing` (which carries an extra `host` label) would never dedupe against an unchained `life`,
and one drive would appear twice with two verdicts. `failing` must be the filter form, not a bool:
`(1 - smart_passed) > 0`, the exit-code remainder, and the two sector counters, each `> 0`. Verified on
2026-11-25: 18 rows, `/dev/sdf` at 2, everything else at 0.

- [ ] **Step 3: Assemble the six panels**

| Row | Panels | Grid |
|---|---|---|
| `Needs attention` | stat `Drives monitored` = `count(B (smartprom_smart_passed))` · stat `Replace soon` = `count(WORN)` · stat `Failing now` = `count(FAILING)` | y=0, x=0/8/16, w=8, h=5 |
| `Fleet` | table, sort by Verdict descending | y=5, x=0, w=24, h=13 |
| `Trends` | timeseries `Life left` · timeseries `Temperature` · timeseries `Reallocated and pending` | y=18: two at w=12, the third at y=26 |

`WORN` and `FAILING` are the two expressions built in Task 4 Step 1. Verdict value mappings, so the
word carries the colour: `3 → failing` (red), `2 → replace` (orange), `1 → watch` (yellow), `0 → OK`
(green). `Replace soon` and `Failing now` turn red above 0; `Drives monitored` turns red below 18,
which is how a drive that stopped publishing becomes visible.

Delete the six KPI stats, the `Wear` row, `Life left per drive` and `Oldest drive`, and the duplicated trend panels the new rows replace.

- [ ] **Step 4: Save it**

`POST /api/dashboards/db` with `{"dashboard": <the object>, "folderUid": "homelab", "overwrite": true, "message": "verdict table: worn asks once, failing pages"}`.

- [ ] **Step 5: Read the new dashboard back and compare with Prometheus**

```bash
curl -sk -H "Authorization: Bearer $GRAFANA_TOKEN" "https://grafana.kub.local/api/dashboards/uid/homelab-disks" | python3 -c \
 'import json,sys; d=json.load(sys.stdin)["dashboard"]; [print(p["type"], p["title"]) for p in d["panels"]]'
```

Expected: 3 stats, 1 table, 3 timeseries, 3 rows; no `Need replacement` stat, no bargauge. Then query the score expression through the datasource proxy again and confirm the table would show `/dev/sdf` as `replace` and 17 drives as `OK`, and that `Drives monitored` reads 18.

No commit.

---

### Task 7: The pages that describe the rules

**Files:**
- Modify: `docs/reference/alerts.md`
- Modify: `docs/observability-conventions.md` (only if it names `DriveNeedsReplacement` or the relay's severity filter)

**Interfaces:**
- Consumes: the UIDs and the routing tree from Task 4, the panel list from Task 6.
- Produces: the catalogue that matches the live stack, which `hack/check-docs.sh` validates.

- [ ] **Step 1: Update the rule catalogue**

In `docs/reference/alerts.md`:
- the count in the opening line becomes **50** (49 − `DriveNeedsReplacement` + `DriveWorn` + `DriveFailing`), with the group split 21 fast / 29 slow;
- delete the `DriveNeedsReplacement` row and add two rows: `DriveWorn` (life < 10 %, or smartctl exit bit 32 — error log has records) warning, `for` 1h, **firing** on `nas /dev/sdf`; `DriveFailing` (SMART failed, pending or offline-uncorrectable sectors, exit bits 1–16 or 64) critical, `for` 10m, inactive;
- the severity-routing paragraph gains the third child: "and a route matched on `board=card` to the same relay, which is how the one-time worn notice becomes a card";
- the relay sentence changes from "warnings are logged and dropped" to "the relay cards whatever the routing tree sends it; the filter used to be in Python and hid a path that carried nothing".
- record the rule UIDs beside the names, as the page does elsewhere.

- [ ] **Step 2: Update the observability page if it names the old rule**

`grep -n "DriveNeedsReplacement\|alert-relay\|vikunja" docs/observability-conventions.md` — rewrite every hit to the new pair, or leave the page untouched if there is none.

- [ ] **Step 3: Run the documentation checks**

```bash
hack/check-docs.sh
```

Expected: no failure, and the staleness report prints.

- [ ] **Step 4: Commit and push**

```bash
git add docs/reference/alerts.md docs/observability-conventions.md
git commit -m "📝 docs: fifty rules, and the worn drive that asks once"
git push
```

---

### Task 8: Close the loop

**Files:** none.

**Interfaces:**
- Consumes: everything above.
- Produces: the card in `Validation`, with the evidence a person needs to accept or refuse.

- [ ] **Step 1: The whole suite, once more**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
hack/check-docs.sh
hack/check-charts.sh
```

Expected: no failures in any of the three.

- [ ] **Step 2: Collect the evidence**

- the relay log lines for the test send and for `/dev/sdf`;
- the two rule UIDs and the routing tree read back from Grafana;
- the score query returning `/dev/sdf = 2` and 17 `OK`;
- the pull request URL.

- [ ] **Step 3: Comment on the pull request**

Post the evidence as a comment, one short paragraph plus the log lines. No claim without the command that produced it.

- [ ] **Step 4: Move card #717 to `Validation`**

Through the Vikunja REST v2 API with `$VIKUNJA_MCP_TOKEN`: `PUT /projects/2/views/13/buckets/…` into the Validation column, then `PUT /tasks/717?format=markdown` with the description carrying the branch, the pull request link and the verification. Then read the card back through the view and check the five things the board requires.

- [ ] **Step 5: Say what is not proven**

In the report: the 24-hour silence of the worn rule is **not** observed yet — it is proven by the generated routing config carrying the 8760 h repeat, not by waiting. Say so in those words.
