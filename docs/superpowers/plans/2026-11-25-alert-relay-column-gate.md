# Alert relay: the card's column decides — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the alert relay open a card only when a firing is new work, and write nothing for a repeat, a resolve, or a card someone is already holding.

**Architecture:** The relay looks up the newest card for an incident key through Vikunja's v2 project-task list, with `expand=buckets` so the same answer carries the card's column. That column picks the action: Backlog and Doing are silent, Validation and any other non-Done column open a new card, Done opens a new card, no card opens a card. A resolve always writes nothing. The comment path, its cooldown and their tests are deleted.

**Tech Stack:** Python 3 standard library, `unittest`, Vikunja REST v2, Helm, ArgoCD.

**Spec:** `docs/superpowers/specs/2026-11-25-alert-relay-column-gate-design.md`

## Global Constraints

- The `alerts` board is project `9`; its kanban view is `59`; its Backlog bucket is `115`.
- A resolve never writes to the board and never moves a card.
- No comment is ever written, and no cooldown remains.
- A new card is created explicitly in the Backlog bucket, with `format=markdown`.
- Every new card is labelled `task` and `alert` as it is created.
- The runbook `docs/runbooks/alert-relay.md` is the live page; older design specs stay as history.

## Review Focus

- A card in a column that is neither Backlog, Doing, Validation nor Done (Idea, Shaping, a renamed column) must open a new card, not swallow the firing.
- One key with an old Done card and a newer Validation card: the newest card decides, never the first match.
- A card that comes back without bucket data must fail the delivery visibly (`502`) rather than open a duplicate.
- A `status` of `Resolved` in mixed case must be treated as a resolve, with no write.
- A key with several cards must select the highest id, not the oldest.

---

### Task 1: The gate in `relay.py`

**Files:**
- Modify: `cluster/helm/monitoring/alert-relay/files/relay.py`
- Modify: `hack/tests/test_alert_relay.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `find_query(key: str) -> str` — no `done = false` clause.
  - `bucket_title(card: dict, view: int) -> str` — the card's bucket title in that view; raises `ValueError` when the card carries none.
  - `Board.__init__(base: str, token: str, project: int, view: int, backlog_bucket: int)`.
  - `Board.find_card(key: str) -> dict | None` — newest first, with buckets expanded.
  - `Board.open_card(title: str, description: str) -> dict` — v2 create in Backlog.
  - `handle(payload: dict, board) -> str` — no cooldown and no `now` parameter.
  - `render_comment`, `should_comment`, `Board.comment`, `Board.last_comment_epoch` are gone.

- [ ] **Step 1: Replace the lookup and rendering tests**

In `hack/tests/test_alert_relay.py`, drop `render_comment` assertions, `ThrottleTests`, `ResolveBypassTests`, and the imports they alone use (`datetime`, `timezone`). Then change the query test and add the lookup and bucket tests:

```python
class QueryTests(unittest.TestCase):
    def test_the_filter_searches_a_hex_slug_not_the_raw_key(self):
        p = payload(group='{}/{alertname="DiskFull"}:{alertname="DiskFull", instance="nas"}')
        self.assertRegex(relay.find_query(relay.key_slug(p)),
                         r"^description like '[0-9a-f]{16}$")

    def test_the_slug_is_stable_for_the_same_group(self):
        self.assertEqual(relay.key_slug(payload(group="g1")), relay.key_slug(payload(group="g1")))

    def test_a_quote_in_the_key_cannot_break_the_filter(self):
        self.assertEqual(relay.find_query("a'b"), "description like 'ab'")


class BucketTests(unittest.TestCase):
    def test_the_title_comes_from_the_configured_view(self):
        card = {"id": 7, "buckets": [{"title": "Doing", "project_view_id": 59},
                                     {"title": "List", "project_view_id": 56}]}
        self.assertEqual(relay.bucket_title(card, 59), "Doing")

    def test_a_card_without_a_bucket_fails_loudly(self):
        """Silently opening would duplicate a card that is already in someone's hands."""
        with self.assertRaises(ValueError):
            relay.bucket_title({"id": 7, "buckets": []}, 59)


class BoardCallTests(unittest.TestCase):
    class Stub(relay.Board):
        def __init__(self):
            super().__init__("http://board", "token", 9, 59, 115)
            self.paths, self.calls = [], []

        def call(self, method, path, body=None):
            self.paths.append(path)
            self.calls.append((method, path, body))
            if method == "GET":
                return {"items": [{"id": 9, "done": False,
                                   "buckets": [{"title": "Backlog", "project_view_id": 59}]},
                                  {"id": 7, "done": True,
                                   "buckets": [{"title": "Done", "project_view_id": 59}]}]}
            return {"id": 42}

    def test_the_lookup_asks_for_the_newest_card_with_its_bucket(self):
        board = self.Stub()
        self.assertEqual(board.find_card("abc")["id"], 9)
        self.assertIn("/api/v2/projects/9/tasks", board.paths[0])
        self.assertIn("expand=buckets", board.paths[0])
        self.assertIn("sort_by=id", board.paths[0])
        self.assertIn("order_by=desc", board.paths[0])
        self.assertIn("description%20like", board.paths[0])

    def test_a_new_card_is_created_in_backlog_with_markdown(self):
        board = self.Stub()
        board.open_card("DiskFull on nas", "body")
        method, path, body = board.calls[-1]
        self.assertEqual(method, "POST")
        self.assertIn("/api/v2/projects/9/tasks?format=markdown", path)
        self.assertEqual(body["bucket_id"], 115)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_relay.py`
Expected: FAIL — `relay.bucket_title` does not exist, and `relay.Board` does not accept five arguments.

- [ ] **Step 3: Implement the lookup, the column and the create**

In `relay.py`:

- `find_query` returns `f"description like '{key.replace(chr(39), '')}'"` — no `done` clause.
- Add `bucket_title(card, view)` above the `Board` class, raising `ValueError(f"card #{card.get('id')} carries no bucket for view {view}")` when no bucket matches.
- `Board.__init__` takes `view` and `backlog_bucket` and stores them.
- `Board.find_card` calls `GET /api/v2/projects/{project}/tasks?per_page=10&sort_by=id&order_by=desc&expand=buckets&filter=<quoted find_query>`, reads `items` (a bare list is also accepted), and returns `items[0]` or `None`.
- `Board.open_card` calls `POST /api/v2/projects/{project}/tasks?format=markdown` with `{"title", "description", "bucket_id": self.backlog_bucket}`.
- Delete `Board.comment` and `Board.last_comment_epoch`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_relay.py`
Expected: PASS for the query, bucket and board-call tests; the `handle` tests still fail because the signature still carries `cooldown`.

- [ ] **Step 5: Replace the `handle` tests with one per row of the gate**

Replace `HandleTests` and `FakeBoard` with:

```python
def card(id=7, done=False, bucket="Backlog"):
    return {"id": id, "done": done,
            "buckets": [{"title": bucket, "project_view_id": 59}]}


class FakeBoard:
    view = 59

    def __init__(self, card=None):
        self.card, self.calls, self.looked_up = card, [], []

    def find_card(self, key):
        self.looked_up.append(key)
        return self.card

    def open_card(self, title, description):
        self.calls.append(("open", title, description))
        return {"id": 42}

    def label(self, task, name):
        self.calls.append(("label", task, name))


class HandleTests(unittest.TestCase):
    def write_modes(self, board):
        return [c[0] for c in board.calls]

    def test_a_new_alert_opens_one_card(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(), board), "opened #42")
        self.assertEqual(self.write_modes(board), ["open", "label", "label"])

    def test_a_new_card_carries_its_labels(self):
        board = FakeBoard()
        relay.handle(payload(), board)
        self.assertEqual([c[2] for c in board.calls if c[0] == "label"], ["task", "alert"])

    def test_a_card_in_backlog_is_left_alone(self):
        board = FakeBoard(card(bucket="Backlog"))
        self.assertEqual(relay.handle(payload(), board), "ignored, #7 in Backlog")
        self.assertEqual(board.calls, [])

    def test_a_card_in_doing_is_left_alone(self):
        board = FakeBoard(card(bucket="Doing"))
        self.assertEqual(relay.handle(payload(), board), "ignored, #7 in Doing")
        self.assertEqual(board.calls, [])

    def test_a_card_in_validation_opens_a_new_card(self):
        board = FakeBoard(card(bucket="Validation"))
        self.assertEqual(relay.handle(payload(), board), "opened #42")
        self.assertEqual(self.write_modes(board), ["open", "label", "label"])

    def test_a_done_card_opens_a_new_card(self):
        board = FakeBoard(card(done=True, bucket="Done"))
        self.assertEqual(relay.handle(payload(), board), "opened #42")

    def test_an_unknown_column_opens_a_new_card(self):
        board = FakeBoard(card(bucket="Idea"))
        self.assertEqual(relay.handle(payload(), board), "opened #42")

    def test_a_resolve_with_a_card_writes_nothing(self):
        for bucket in ("Backlog", "Doing", "Validation"):
            board = FakeBoard(card(bucket=bucket))
            self.assertEqual(relay.handle(payload(status="resolved"), board),
                             f"resolved, ignored, #7 in {bucket}")
            self.assertEqual(board.calls, [])

    def test_a_resolve_without_a_card_writes_nothing(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(status="resolved"), board), "resolved, no card")
        self.assertEqual(board.calls, [])

    def test_a_capitalised_resolve_is_a_resolve(self):
        board = FakeBoard(card(bucket="Doing"))
        self.assertEqual(relay.handle(payload(status="Resolved"), board),
                         "resolved, ignored, #7 in Doing")
        self.assertEqual(board.calls, [])

    def test_a_mixed_group_keeps_the_warning(self):
        p = payload()
        p["alerts"].append({
            "status": "firing", "fingerprint": "w1",
            "labels": {"alertname": "DiskFull", "severity": "warning", "instance": "nuc0"},
            "annotations": {},
        })
        board = FakeBoard()
        relay.handle(p, board)
        self.assertIn("carries 2 alerts", board.calls[0][2])
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_relay.py`
Expected: FAIL — `handle` still requires `cooldown`, and a Backlog card still receives a comment.

- [ ] **Step 7: Implement the gate and delete the comment path**

Replace `handle` with:

```python
def handle(payload: dict, board) -> str:
    key = key_slug(payload)
    resolved = (payload.get("status") or "").lower() == "resolved"
    card = board.find_card(key)
    if resolved:
        if card is None:
            return "resolved, no card"
        return f"resolved, ignored, #{card['id']} in {bucket_title(card, board.view)}"
    if card is not None and not card.get("done"):
        column = bucket_title(card, board.view)
        if column in ("Backlog", "Doing"):
            return f"ignored, #{card['id']} in {column}"
    created = board.open_card(render_title(payload), render_body(payload, key))
    for name in ("task", "alert"):
        board.label(created["id"], name)
    return f"opened #{created.get('id')}"
```

Delete `render_comment`, `should_comment`, `Handler.cooldown`, and the cooldown lines in `main`. Remove the imports that nothing uses any more (`re`, `time`, `datetime`, `timezone`). `main` builds `Board(..., view=int(os.environ.get("ALERTS_VIEW", "59")), backlog_bucket=int(os.environ.get("ALERTS_BACKLOG_BUCKET", "115")))`. Update the module docstring's rule list to the gate in four lines: Backlog and Doing are silent; Validation and any other open column open a card; Done opens a card; a resolve writes nothing.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_relay.py`
Expected: PASS, including the table's seven firing rows and both resolve rows.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/monitoring/alert-relay/files/relay.py hack/tests/test_alert_relay.py
git commit -m "✨ feat: gate alert cards on their column"
```

---

### Task 2: Chart wiring

**Files:**
- Modify: `cluster/helm/monitoring/alert-relay/values.yaml`
- Modify: `cluster/helm/monitoring/alert-relay/templates/deployment.yaml`
- Modify: `cluster/helm/monitoring/alert-relay/README.md`

**Interfaces:**
- Consumes: `ALERTS_VIEW` and `ALERTS_BACKLOG_BUCKET` from Task 1's `main`.
- Produces: the rendered Deployment sets both variables; `commentCooldownSeconds` is gone.

- [ ] **Step 1: Set the board coordinates in `values.yaml`**

Under `vikunja`, add `view: 59` and `backlogBucket: 115`, with the comment that the view is the kanban view whose buckets the relay reads. Delete the whole `commentCooldownSeconds` entry and its comment.

- [ ] **Step 2: Pass them to the container**

In `templates/deployment.yaml`, replace the `COMMENT_COOLDOWN_SECONDS` env entry with:

```yaml
            - name: ALERTS_VIEW
              value: {{ .Values.vikunja.view | quote }}
            - name: ALERTS_BACKLOG_BUCKET
              value: {{ .Values.vikunja.backlogBucket | quote }}
```

- [ ] **Step 3: Correct the chart README**

Replace the three stale bullets with the gate:

```markdown
- One card per **incident** (Grafana's `groupKey`). A repeat and a resolve write
  nothing; the card is opened only when the firing is new work or the previous
  card is in Validation or Done.
- A resolve never closes and never moves a card: only an investigation that
  wrote a cause moves one to Validation.
- Grafana's routing tree decides what reaches the relay; the relay writes a card
  for whatever arrives.
```

- [ ] **Step 4: Render the chart**

Run: `helm template test cluster/helm/monitoring/alert-relay -f cluster/helm/monitoring/alert-relay/values.yaml | grep -A1 'ALERTS_'`
Expected: `ALERTS_VIEW` = `"59"`, `ALERTS_BACKLOG_BUCKET` = `"115"`, and no `COMMENT_COOLDOWN_SECONDS`.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/alert-relay/values.yaml cluster/helm/monitoring/alert-relay/templates/deployment.yaml cluster/helm/monitoring/alert-relay/README.md
git commit -m "🔧 chore: give the alert relay its board coordinates"
```

---

### Task 3: The pages a person reads

**Files:**
- Modify: `docs/runbooks/alert-relay.md`
- Modify: `docs/reference/alerts.md`

**Interfaces:**
- Consumes: the behaviour and log lines from Task 1.
- Produces: both pages describe the gate and none of them claims a comment.

- [ ] **Step 1: Replace the runbook's behaviour table**

Replace the four rows under **What it does, exactly** with the gate, keeping the same table shape:

| Grafana sends | The relay does |
|---|---|
| `firing`, no card for this incident | opens one card in Backlog: the rule and where, labels, annotations, the Grafana link, a `Next step:`; labelled `task` and `alert` |
| `firing`, the newest card is in Backlog or Doing | writes nothing, and logs `ignored, #N in <column>` |
| `firing`, the newest card is in Validation, Idea, Shaping or Done | opens a new card in Backlog |
| `resolved` | writes nothing, whatever card exists, and never moves one |
| a body that is not JSON, or has no alerts | answers 400 and logs why — never dropped in silence |

Immediately after the table, add: "The newest card for the key decides. One key recurs over months, so the latest card is the current incident, not the oldest." Add the rule that a resolve is not validation: only an investigation that wrote a cause moves a card to Validation.

- [ ] **Step 2: Fix the log examples and the proof**

In the paragraph after the table, replace `commented on #N` with `ignored, #N in Doing`, and add `resolved, ignored, #N in Backlog`. In **Step 5 — prove it**, change the repeat expectation to `firing: RelayProbe: ignored, #727 in Backlog` and remove the eleven-minute `commented on` claim. Change the direct-curl expected outcomes: same body again → `ignored, #727 in Backlog`; a `resolved` → `resolved, ignored, #727 in Backlog`.

- [ ] **Step 3: Update the reference page**

In `docs/reference/alerts.md`, replace the sentence saying the relay "comments on repeats and on resolves" with: it opens one card per incident, writes nothing while that card sits in Backlog or Doing, opens a fresh card when the newest one is in Validation or Done, and never moves or closes one.

- [ ] **Step 4: Check the docs**

Run: `hack/check-docs.sh --base origin/develop`
Expected: exit 0, with the relative-link, cited-path and nav checks passing.

Run: `grep -rn "commented on\|cooldown\|comments on repeats" docs/runbooks/alert-relay.md docs/reference/alerts.md cluster/helm/monitoring/alert-relay/README.md`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/alert-relay.md docs/reference/alerts.md
git commit -m "📝 docs: describe the alert relay as a gate"
```

---

### Task 4: Gates and handover

**Files:**
- No source changes unless a gate fails.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: a rebased branch, a pull request to `develop`, and the live proof handed to the operator.

- [ ] **Step 1: Run the full checker suite**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
Expected: 0 failures.

- [ ] **Step 2: Lint and render every chart**

Run: `hack/check-charts.sh`
Expected: exit 0, no `FAIL`.

- [ ] **Step 3: Check exposure**

Run: `hack/check-exposure.sh`
Expected: exit 0; the relay still has no route.

- [ ] **Step 4: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push origin "$(git branch --show-current)"
```

Expected: a clean rebase; the full suite already passed on this tree (re-run Step 1 if the rebase moved anything).

- [ ] **Step 5: Open the pull request**

Open a pull request to `develop` whose body names the four outcomes (Backlog and Doing silent, Validation and Done open, resolve writes nothing) and the verification from Steps 1–3.

- [ ] **Step 6: Hand the live proof to the operator**

After the ArgoCD Application syncs, the operator runs this sequence and pastes the relay log lines; the agent cannot port-forward under the read-only kubeconfig.

```bash
kubectl -n monitoring rollout status deploy/alert-relay --timeout=120s
kubectl -n monitoring port-forward svc/alert-relay 8080:8080 &
# firing, no card: expect "opened #N"
curl -s -X POST localhost:8080/alert -H 'Content-Type: application/json' \
  -d '{"status":"firing","groupKey":"probe/column-gate","alerts":[{"status":"firing","fingerprint":"p1","startsAt":"2026-11-25T20:00:00Z","labels":{"alertname":"ColumnGateProbe","severity":"critical","host":"nas"},"annotations":{"summary":"synthetic"}}]}'
# same firing again: expect "ignored, #N in Backlog" and no new comment
# move the card to Doing, send again: expect "ignored, #N in Doing"
# move it to Validation, send again: expect "opened #M"
# send {"status":"resolved", ...}: expect "resolved, ignored, #M in Backlog"
```

Delete the probe cards afterwards and confirm the `alerts` board has no leftovers.

- [ ] **Step 7: Commit nothing further**

No commit results from this task. Report the gate outputs and the operator's live log lines on the pull request.
