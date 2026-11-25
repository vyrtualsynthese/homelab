# Alert lifecycle enforcement — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move an alert card to Doing once it has blocked fix cards, and to Validation once every blocked fix is in Validation or Done.

**Architecture:** A small Python poller, `lifecycle.py`, ships in the alert-relay ConfigMap and runs as a five-minute `CronJob`. It reads the `alerts` kanban view, follows each alert card's `blocked` relations, reads each fix card's column, and moves the alert card with the bucket then position endpoints. Sessions create the relations and the cause; the job is the backstop.

**Tech Stack:** Python 3 standard library, `unittest`, Vikunja REST v2, Helm, Kubernetes `CronJob`, ArgoCD.

**Spec:** `docs/superpowers/specs/2026-11-25-alert-lifecycle-enforcement-design.md`

**Branch:** continue on `feature/alert-relay-column-gate`. PR #1542 is still open; if it has merged by handover time, open a fresh PR for these commits instead.

## Global Constraints

- The `alerts` board is project `9`, kanban view `59`, Doing bucket `111`, Validation bucket `116`, Backlog `115`.
- A fix counts as closed when `done` is true, or its column is `Validation` or `Done`.
- Validation is terminal: never move a card out of it.
- Only cards carrying the `alert` label are considered; `frozen` and done cards are skipped.
- The job runs as `bot-alertmanager`; it writes only `alerts`. `bot-opencode` gets read+write on `alerts`; `bot-alertmanager` gets read-only on boards `2, 3, 5, 8, 12`.
- A card with a blocked fix whose bucket data is missing fails loudly: no move for that card, non-zero exit.
- Schedule `*/5 * * * *`, `concurrencyPolicy: Forbid`, `restartPolicy: OnFailure`, `backoffLimit: 2`.

## Review Focus

- A fix card in Validation with `done=false` counts as closed — the deliberate exception.
- A blocked fix with no bucket data fails its card loudly; it is never guessed open or closed.
- An alert card already in Validation is never dragged back to Doing, even if a fix reopens.
- A non-`alert` card on the `alerts` board (a rule-change ticket) with blocked relations is untouched.
- A `frozen` alert card with open fixes stays exactly where it is.

---

### Task 1: The poller and its tests

**Files:**
- Create: `cluster/helm/monitoring/alert-relay/files/lifecycle.py`
- Create: `hack/tests/test_alert_lifecycle.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `closed(fix: dict) -> bool` — raises `ValueError` when a fix is not done and carries no bucket.
  - `target_column(card: dict, fixes: list[dict]) -> str | None` — `"Doing"`, `"Validation"` or `None`.
  - `band(card: dict) -> int` and `position_for(card: dict, column_cards: list[dict]) -> float`.
  - `Board.__init__(base: str, token: str, project: int, view: int, doing_bucket: int, validation_bucket: int)`.
  - `Board.alert_cards() -> list[dict]` — flattens the view and sets `card["column"]`.
  - `Board.fix_card(task_id: int) -> dict`, `Board.move(card: dict, target: str) -> None`.
  - `run_once(board) -> tuple[int, int]` — `(moved, failed)`.

- [ ] **Step 1: Write the failing tests for the pure helpers**

Create `hack/tests/test_alert_lifecycle.py`:

```python
import importlib.util
import pathlib
import sys
import unittest

LIFECYCLE = pathlib.Path(__file__).resolve().parents[1] / ".." / "cluster" / "helm" / "monitoring" / "alert-relay" / "files" / "lifecycle.py"
spec = importlib.util.spec_from_file_location("lifecycle", LIFECYCLE.resolve())
lifecycle = importlib.util.module_from_spec(spec)
sys.modules["lifecycle"] = lifecycle
spec.loader.exec_module(lifecycle)


def labels(*names):
    return [{"title": name} for name in names]


def alert_card(column="Backlog", done=False, label="alert", priority=None):
    card = {"id": 7, "title": "DiskFull on nas", "done": done, "column": column,
            "labels": labels("task", label) + ([priority] if priority else []),
            "related_tasks": {"blocked": [{"id": 90}]}}
    return card


def fix(column="Doing", done=False):
    return {"id": 90, "done": done,
            "buckets": [{"title": column, "project_view_id": 13}]}


class ClosedTests(unittest.TestCase):
    def test_validation_closes_a_fix_even_when_not_done(self):
        self.assertTrue(lifecycle.closed(fix(column="Validation")))

    def test_done_closes_a_fix(self):
        self.assertTrue(lifecycle.closed(fix(column="Done", done=True)))

    def test_doing_is_open(self):
        self.assertFalse(lifecycle.closed(fix(column="Doing")))

    def test_a_fix_without_a_bucket_fails_loudly(self):
        with self.assertRaises(ValueError):
            lifecycle.closed({"id": 90, "done": False, "buckets": []})


class TargetTests(unittest.TestCase):
    def test_no_fixes_leaves_the_card_alone(self):
        self.assertIsNone(lifecycle.target_column(alert_card(), []))

    def test_open_fixes_move_backlog_to_doing(self):
        self.assertEqual(lifecycle.target_column(alert_card("Backlog"), [fix("Doing")]), "Doing")

    def test_open_fixes_leave_a_doing_card_alone(self):
        self.assertIsNone(lifecycle.target_column(alert_card("Doing"), [fix("Doing")]))

    def test_all_closed_moves_backlog_to_validation(self):
        self.assertEqual(lifecycle.target_column(alert_card("Backlog"), [fix("Validation")]), "Validation")

    def test_all_closed_moves_doing_to_validation(self):
        self.assertEqual(lifecycle.target_column(alert_card("Doing"), [fix("Done", done=True)]), "Validation")

    def test_validation_is_terminal(self):
        card = alert_card("Validation")
        self.assertIsNone(lifecycle.target_column(card, [fix("Backlog")]))

    def test_a_non_alert_card_is_untouched(self):
        self.assertIsNone(lifecycle.target_column(alert_card(label="task"), [fix("Doing")]))

    def test_a_frozen_card_is_untouched(self):
        self.assertIsNone(lifecycle.target_column(alert_card(label="frozen"), [fix("Doing")]))

    def test_a_done_card_is_untouched(self):
        self.assertIsNone(lifecycle.target_column(alert_card(done=True), [fix("Doing")]))


class PositionTests(unittest.TestCase):
    def test_an_empty_column_starts_at_one(self):
        self.assertEqual(lifecycle.position_for(alert_card(), []), 1)

    def test_a_no_priority_card_lands_last(self):
        cards = [{"id": 1, "position": 10, "labels": labels("priority/high")},
                 {"id": 2, "position": 20, "labels": []}]
        self.assertEqual(lifecycle.position_for(alert_card(), cards), 20 + 1_000_000)

    def test_a_high_priority_card_lands_first(self):
        cards = [{"id": 1, "position": 10, "labels": labels("priority/medium")}]
        self.assertEqual(lifecycle.position_for(alert_card(priority="priority/high"), cards), 5)

    def test_a_card_lands_between_its_band_neighbours(self):
        cards = [{"id": 1, "position": 10, "labels": labels("priority/high")},
                 {"id": 2, "position": 20, "labels": labels("priority/low")}]
        self.assertEqual(lifecycle.position_for(alert_card(priority="priority/medium"), cards), 15)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_lifecycle.py`
Expected: FAIL — the module `lifecycle.py` does not exist.

- [ ] **Step 3: Implement the pure helpers**

Create `lifecycle.py` with `from __future__ import annotations`, standard-library imports only, and:

- `CLOSED_COLUMNS = ("Validation", "Done")`; `BANDS = {"priority/critical": 0, "priority/high": 1, "priority/medium": 2, "priority/low": 3}`.
- `has_label(card, name)` scans `card.get("labels")` for a matching `title`.
- `band(card)` returns the lowest priority band on the card, else `4`.
- `closed(fix)` returns true on `done`, else true when any bucket title is in `CLOSED_COLUMNS`; if neither and `buckets` is empty, raise `ValueError(f"fix #{fix.get('id')} carries no bucket")`.
- `target_column(card, fixes)` returns `None` when the card is done, frozen, not `alert`, not in `Backlog`/`Doing`, or has no fixes; otherwise `"Validation"` when `all([closed(f) for f in fixes])` (build the list first, so every fix is checked), else `"Doing"` when the column is `Backlog`, else `None`.
- `position_for(card, column_cards)` implements the band placement: empty → `1`; insert index is the first card whose band is `>=` the moving card's; index 0 → `column_cards[0]["position"] / 2`; end → last position `+ 1_000_000`; otherwise the midpoint of the neighbours.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_lifecycle.py`
Expected: PASS for the pure-helper tests.

- [ ] **Step 5: Write the failing tests for the board calls and the poller**

Append to the test file:

```python
class FakeBoard:
    def __init__(self, cards, fixes):
        self.cards, self.fixes, self.moves = cards, fixes, []

    def alert_cards(self):
        return self.cards

    def fix_card(self, task_id):
        return self.fixes[task_id]

    def move(self, card, target):
        self.moves.append((card["id"], target))


class RunTests(unittest.TestCase):
    def test_a_backlog_card_with_open_fixes_moves_to_doing(self):
        board = FakeBoard([alert_card("Backlog")], {90: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (1, 0))
        self.assertEqual(board.moves, [(7, "Doing")])

    def test_a_doing_card_with_open_fixes_is_not_moved(self):
        board = FakeBoard([alert_card("Doing")], {90: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (0, 0))
        self.assertEqual(board.moves, [])

    def test_all_closed_moves_the_card_to_validation(self):
        board = FakeBoard([alert_card("Doing")], {90: fix("Validation")})
        self.assertEqual(lifecycle.run_once(board), (1, 0))
        self.assertEqual(board.moves, [(7, "Validation")])

    def test_a_non_alert_card_is_not_inspected(self):
        board = FakeBoard([alert_card("Backlog", label="task")], {90: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (0, 0))
        self.assertEqual(board.moves, [])

    def test_a_frozen_card_is_not_moved(self):
        board = FakeBoard([alert_card("Backlog", label="frozen")], {90: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (0, 0))
        self.assertEqual(board.moves, [])

    def test_a_broken_fix_fails_its_card_and_does_not_stop_the_other(self):
        good = alert_card("Backlog")
        good["id"] = 8
        board = FakeBoard([alert_card("Backlog"), good],
                          {90: {"id": 90, "done": False, "buckets": []}})
        self.assertEqual(lifecycle.run_once(board), (0, 2))
        self.assertEqual(board.moves, [])


class BoardCallTests(unittest.TestCase):
    class Stub(lifecycle.Board):
        def __init__(self):
            super().__init__("http://board", "token", 9, 59, 111, 116)
            self.calls = []

        def call(self, method, path, body=None):
            self.calls.append((method, path, body))
            if path.endswith("/buckets/tasks"):
                return {"items": [
                    {"id": 115, "title": "Backlog",
                     "tasks": [{"id": 7, "title": "a", "done": False,
                                "labels": [{"title": "alert"}], "related_tasks": {}}]},
                    {"id": 116, "title": "Validation", "tasks": []}]}
            return None

    def test_the_board_read_names_each_card_s_column(self):
        cards = self.Stub().alert_cards()
        self.assertEqual(cards[0]["column"], "Backlog")

    def test_a_move_uses_the_bucket_then_sets_the_position(self):
        board = self.Stub()
        board.move({"id": 7, "position": 0, "labels": []}, "Validation")
        self.assertEqual(board.calls[0],
                         ("PUT", "/api/v2/projects/9/views/59/buckets/116/tasks", {"task_id": 7}))
        self.assertEqual(board.calls[1][0], "PUT")
        self.assertIn("/api/v2/tasks/7/position", board.calls[1][1])
        self.assertEqual(board.calls[1][2]["project_view_id"], 59)
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_lifecycle.py`
Expected: FAIL — `lifecycle.Board` and `lifecycle.run_once` do not exist.

- [ ] **Step 7: Implement the board client and the poller**

- `Board.__init__` stores `base` (rstrip `/`), `token`, `project`, `view`, `doing_bucket`, `validation_bucket`.
- `Board.call` mirrors the relay's: JSON body, Bearer token, five-second timeout.
- `Board.alert_cards` calls `GET /api/v2/projects/{project}/views/{view}/buckets/tasks`, iterates `items`, and copies every task with `task["column"] = bucket["title"]`.
- `Board.fix_card` calls `GET /api/v2/tasks/{id}?expand=buckets`.
- `Board.move(card, target)` maps `Doing`/`Validation` to the stored bucket id, calls the bucket `PUT`, re-reads `alert_cards()` excluding the moved id, computes `position_for(card, column_cards)`, then calls `PUT /api/v2/tasks/{id}/position` with `{"position": …, "project_view_id": view}`.
- `run_once(board)` skips cards that fail the same guards as `target_column` (not `alert`, frozen, done, column outside Backlog/Doing, no blocked tasks), fetches each blocked fix, computes the target, catches `ValueError` per card (prints `failed #<id>: <error>` to stderr and counts it), calls `board.move`, and prints one line per move: `#<id> <title>: <from> -> <target> (<n> fixes open)`. It prints a summary line only when something moved or failed, then returns `(moved, failed)`.
- `main()` reads `VIKUNJA_BASE`, `VIKUNJA_TOKEN`, `ALERTS_PROJECT` (9), `ALERTS_VIEW` (59), `ALERTS_DOING_BUCKET` (111), `ALERTS_VALIDATION_BUCKET` (116), runs once, and returns `1` when `failed` is non-zero.

- [ ] **Step 8: Run the tests to verify they pass, then the whole suite**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_alert_lifecycle.py`
Expected: PASS, all rows of the ladder and the failure case.

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
Expected: 0 failures.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/monitoring/alert-relay/files/lifecycle.py hack/tests/test_alert_lifecycle.py
git commit -m "✨ feat: enforce the alert card lifecycle"
```

---

### Task 2: The CronJob and the chart

**Files:**
- Modify: `cluster/helm/monitoring/alert-relay/values.yaml`
- Modify: `cluster/helm/monitoring/alert-relay/templates/configmap.yaml`
- Create: `cluster/helm/monitoring/alert-relay/templates/cronjob.yaml`
- Modify: `cluster/helm/monitoring/alert-relay/README.md`

**Interfaces:**
- Consumes: `lifecycle.py` and its env names from Task 1.
- Produces: a rendered `CronJob` named `{{ .Release.Name }}-lifecycle`, running `python /app/lifecycle.py`.

- [ ] **Step 1: Add the coordinates and schedule to `values.yaml`**

Under `vikunja`, add `doingBucket: 111` and `validationBucket: 116`. Add a top-level:

```yaml
lifecycle:
  schedule: "*/5 * * * *"
```

- [ ] **Step 2: Ship the script in the ConfigMap**

In `templates/configmap.yaml`, after the `relay.py` entry:

```yaml
  lifecycle.py: |
{{ .Files.Get "files/lifecycle.py" | indent 4 }}
```

- [ ] **Step 3: Add the CronJob**

Create `templates/cronjob.yaml` with the same image, Secret, ConfigMap volume and security context as the Deployment:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ .Release.Name }}-lifecycle
  labels:
    app.kubernetes.io/name: {{ .Release.Name }}-lifecycle
spec:
  schedule: {{ .Values.lifecycle.schedule | quote }}
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      activeDeadlineSeconds: 120
      template:
        metadata:
          labels:
            app.kubernetes.io/name: {{ .Release.Name }}-lifecycle
          annotations:
            checksum/config: {{ .Files.Get "files/lifecycle.py" | sha256sum }}
        spec:
          restartPolicy: OnFailure
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
          containers:
            - name: lifecycle
              image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
              command: ["python", "/app/lifecycle.py"]
              env:
                - name: VIKUNJA_BASE
                  value: {{ .Values.vikunja.base | quote }}
                - name: ALERTS_PROJECT
                  value: {{ .Values.vikunja.project | quote }}
                - name: ALERTS_VIEW
                  value: {{ .Values.vikunja.view | quote }}
                - name: ALERTS_DOING_BUCKET
                  value: {{ .Values.vikunja.doingBucket | quote }}
                - name: ALERTS_VALIDATION_BUCKET
                  value: {{ .Values.vikunja.validationBucket | quote }}
                - name: VIKUNJA_TOKEN
                  valueFrom:
                    secretKeyRef:
                      name: {{ .Values.vikunja.secretName }}
                      key: {{ .Values.vikunja.secretKey }}
              resources:
{{ toYaml .Values.resources | indent 16 }}
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities:
                  drop: ["ALL"]
              volumeMounts:
                - name: code
                  mountPath: /app
                  readOnly: true
                - name: tmp
                  mountPath: /tmp
          volumes:
            - name: code
              configMap:
                name: {{ .Release.Name }}-relay
            - name: tmp
              emptyDir: {}
          nodeSelector:
{{ toYaml .Values.nodeSelector | indent 12 }}
```

- [ ] **Step 4: Correct the chart README**

Add to the bullets: the CronJob runs every five minutes, moves an alert card with blocked fixes to Doing, and moves it to Validation when every blocked fix is in Validation or Done. It reads the repository boards and writes only `alerts`.

- [ ] **Step 5: Render and lint the chart**

Run: `helm template test cluster/helm/monitoring/alert-relay -f cluster/helm/monitoring/alert-relay/values.yaml | grep -A3 'kind: CronJob'`
Expected: the CronJob renders with schedule `*/5 * * * *`.

Run: `hack/check-charts.sh --only alert-relay`
Expected: `result: OK`.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/monitoring/alert-relay/values.yaml cluster/helm/monitoring/alert-relay/templates/configmap.yaml cluster/helm/monitoring/alert-relay/templates/cronjob.yaml cluster/helm/monitoring/alert-relay/README.md
git commit -m "🔧 chore: run the alert lifecycle every five minutes"
```

---

### Task 3: The pages a person reads

**Files:**
- Modify: `docs/runbooks/alert-relay.md`
- Modify: `docs/reference/alerts.md`

**Interfaces:**
- Consumes: the ladder and log lines from Task 1, the CronJob from Task 2.
- Produces: the procedure a session follows, the permission steps, and the failure modes.

- [ ] **Step 1: Add the lifecycle section**

After **What it does, exactly**, add **After the investigation**: the ladder table from the spec, the relation direction (`blocked` on the alert card), the no-fix case (cause, then Validation), and the rule that Validation is terminal. State that a session moves the card to Doing and the job enforces it within five minutes; the job moves it to Validation when every blocked fix is in Validation or Done.

- [ ] **Step 2: Add the permission steps**

In **Step 1 — the bot**, add the two sharing changes with API calls that create or update a share:

```bash
B=https://vikunja.kub.local/api/v2
# bot-opencode: read+write on alerts
curl -s -X POST "$B/projects/9/users" -H "Authorization: Bearer $VIKUNJA_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"username":"bot-opencode","permission":1}'
# bot-alertmanager: read-only on each repository board
for p in 2 3 5 8 12; do
  curl -s -X POST "$B/projects/$p/users" -H "Authorization: Bearer $VIKUNJA_API_TOKEN" \
    -H 'Content-Type: application/json' -d '{"username":"bot-alertmanager","permission":0}'
done
```

Say that a `400`/`409` on an existing share means run the same call against
`/projects/{p}/users/{username}` with `PUT`. Note the operator runs these, never an agent.

- [ ] **Step 3: Extend the failure modes and the non-goals**

Add rows: `CronJob` Failed means a fix card could not be read (permissions) or the board call failed — read `kubectl -n monitoring logs job/<job>`; an alert stuck in Doing with all fixes closed means the job cannot read that board. Add to **What it deliberately does not do**: it does not move a card backwards, does not close one, and does not touch a card without the `alert` label.

- [ ] **Step 4: Update the reference page**

In `docs/reference/alerts.md`, add one paragraph: the lifecycle CronJob moves an alert card to Doing once its blocked fix cards exist, and to Validation once every blocked fix is in Validation or Done; Validation is terminal, and the job writes only the `alerts` board.

- [ ] **Step 5: Check the docs**

Run: `hack/check-docs.sh --base origin/develop`
Expected: exit 0, `0 broken relative links`.

Run: `grep -rn "comment\|cooldown" docs/runbooks/alert-relay.md docs/reference/alerts.md cluster/helm/monitoring/alert-relay/README.md`
Expected: no stale comment wording.

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/alert-relay.md docs/reference/alerts.md
git commit -m "📝 docs: write down the alert card lifecycle"
```

---

### Task 4: Gates, permissions and handover

**Files:**
- No source changes unless a gate fails.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: a pushed branch, the updated or new pull request, the permission changes handed to the operator, and the live proof.

- [ ] **Step 1: Run the gates**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
hack/check-charts.sh
hack/check-exposure.sh
hack/check-docs.sh --base origin/develop
```

Expected: 0 test failures, `result: OK` from both chart and exposure checks, docs exit 0.

- [ ] **Step 2: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push origin "$(git branch --show-current)"
```

If the rebase pulls in the merged gate, re-run Step 1.

- [ ] **Step 3: Update the pull request**

If PR #1542 is still open, update its title to `✨ feat: gate and enforce the alert card lifecycle` and its body with the lifecycle ladder, the permission changes and the CronJob. If it has merged, open a new PR from this branch to `develop` with the lifecycle commits and the same body.

- [ ] **Step 4: Hand the permission changes to the operator**

Give the four blocks from Task 3 Step 2. They need `VIKUNJA_API_TOKEN`; no agent uses it. Confirm afterwards that `GET /api/v2/tasks/<alert>` as `bot-opencode` reports `max_permission: 1`, and a fix card read as `bot-alertmanager` answers 200.

- [ ] **Step 5: Hand the live proof to the operator**

After the permissions and ArgoCD sync:

1. Send a synthetic firing through the relay to open a probe alert card (unique `groupKey`), and note its id.
2. As `bot-opencode`, create a probe fix card on `homelab` (project 2), then relate it:
   `POST /api/v2/tasks/<alert>/relations {"relation_kind":"blocked","other_task_id":<fix>}`.
3. Run the job once instead of waiting: `kubectl -n monitoring create job --from=cronjob/alert-relay-lifecycle lifecycle-probe`; read its logs.
4. Expect the alert card in `Doing`. Move the fix to `Validation` (project 2, view 13, bucket 14), run the job again, expect the alert in `Validation`.
5. Create no-fix probe: a second firing whose card stays untouched. Mark one probe `frozen` and confirm it does not move.
6. Trash both probe cards and delete the probe job.

The agent can create the fix card and the relation; the operator must port-forward and create the job. Delete the probes afterwards.
