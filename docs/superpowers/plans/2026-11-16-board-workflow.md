# The homelab Board Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `homelab` Vikunja project into a six-column board whose 152 existing tickets and 29 merged PRs all land in the right column, with a protocol and a seed script that keep it that way.

**Architecture:** Two scripts and five documents. `hack/vikunja-seed.sh` (bash, already idempotent) keeps owning the board's *shape*: columns, view settings, labels, filters, the project front page. A new one-shot `hack/vikunja_migrate.py` owns the *content*: it consumes a reviewed JSON mapping and writes features, parents, titles, descriptions and columns, with `--dry-run` printing what it would do. The mapping is data, committed, reviewable, and re-runnable.

**Tech Stack:** bash + `jq` + `curl` (existing script idioms), Python 3 standard library (`unittest`, `urllib`) for the migration, Vikunja REST API v1.

**Spec:** `docs/superpowers/specs/2026-11-16-board-workflow-design.md`

## Global Constraints

- Columns, in this order and spelled exactly: `Idea`, `Shaping`, `Backlog`, `Doing`, `Validation`, `Done`.
- Kinds are the labels `feature`, `story`, `task`. A subtask is a `- [ ]` line inside a task's description, never a card.
- Only `Doing` and `Validation` are task-only columns; `Backlog` deliberately mixes containers and tasks.
- Only the `homelab` project is touched. The other six keep their current shape.
- The five existing bucket ids must not change: `Ideas` 10, `Doing` 11, `Done` 12, `Backlog` 13, `Revision` 14.
- Exactly one kanban view per project. Never create a second one; never delete a view.
- A view write sends the **whole** view — title, position, `bucket_configuration_mode`, `default_bucket_id`, `done_bucket_id`. Omitted fields are zeroed.
- `Done` is the operator's. An agent never moves a ticket there; the migration writes there only from an operator-approved mapping.
- Every write path is idempotent: a second run prints `existing` and writes nothing.
- Documents are written in English, plain language, one glossary gloss per domain term, per `AGENTS.md`.
- Commits use a Gitmoji prefix; no commit to `develop` or `main`.

## Review Focus

- **A rename meeting a bucket that holds cards.** Renaming moves the column, not the cards, so a rename over a non-empty bucket silently relabels work. Expected: the script refuses to rename a bucket that holds tasks and says so.
- **A view write that drops a field.** The endpoint replaces the record; a body missing `done_bucket_id` silently turns the done bucket off, and every accepted card becomes an open task again. Expected: a re-run repairs it and `--verify` fails loudly.
- **A filter saved before its buckets exist.** `bucket_id = 0` matches nothing, so the operator's queue looks empty rather than broken. Expected: filters are written only after the buckets are read back, and a re-run repairs stale ids.
- **A second migration run after a partial failure.** Features are matched by title, so a half-written board gains no duplicates and the run resumes.
- **A title that contains a colon but no plan prefix.** Stripping by regex eats real words. Expected: only the exact `plan:` value from the ticket's own description is stripped, case-sensitively.

---

### Task 1: Six columns by rename, a scoped run, and the stock view

**Files:**
- Modify: `hack/vikunja-seed.sh` (constants at lines 37–61, `seed_project` at 148, `main` at 526)
- Test: the script's own `--dry-run`, run against the live board

**Interfaces:**
- Produces: `BUCKETS=(Idea Shaping Backlog Doing Validation Done)`, `BUCKET_RENAMES` (pairs `old:new`), `VIEW_TITLE="Board"`, `REPO_SCOPE` (empty = all repos), and the positional argument `--repo <name>`.
- Produces: every bucket write now goes through one helper, `put_bucket project view id title position`, which sends `{"title":…,"position":…,"limit":0}`.
- Produces: `adopt_view project` returning the id of the project's kanban view, renamed `Board` and wired in one full-record write.

- [ ] **Step 1: Read the three sections being changed**

Run: `sed -n '37,61p;148,260p;520,538p' hack/vikunja-seed.sh`
Expected: the constants block, `seed_project`, and the main loop that iterates `REPOS`.

- [ ] **Step 2: Replace the constants**

In `hack/vikunja-seed.sh`, replace lines 51–55 with:

```bash
# The view Vikunja gives every project. The script adopts it and renames it; it
# never creates a second kanban view, because two would hold two answers to
# "where is this task".
VIEW_TITLE="Board"
# A bucket renamed from an earlier shape is adopted in place, so its id — which
# the saved filters store — survives. Only buckets holding no tasks are renamed.
BUCKET_RENAMES=("Ideas:Idea" "Revision:Validation")
# Reading order. Positions are written explicitly so the order is the design's,
# not whatever the UI last dragged.
BUCKETS=(Idea Shaping Backlog Doing Validation Done)
DONE_TITLE="Done"
DEFAULT_BUCKET_TITLE="Backlog"
BUCKET_MODE="manual"
FILTER_TITLE="Awaiting my review"
```

- [ ] **Step 3: Parse `--repo` before the usage block runs**

In `usage()` change the `sed` range to `2,44p`, and in the argument loop (the `while [ $# -gt 0 ]` block near line 500) add:

```bash
    --repo)
      shift
      [ $# -gt 0 ] || die "--repo needs a project name"
      REPO_SCOPE="$1"
      ;;
```

Declare `REPO_SCOPE=""` beside `DRY_RUN=0`, and after `usage` parsing add:

```bash
if [ -n "$REPO_SCOPE" ]; then
  found=0
  for r in "${REPOS[@]}"; do [ "$r" = "$REPO_SCOPE" ] && found=1; done
  [ "$found" = 1 ] || die "no repository named \"$REPO_SCOPE\" in this script's list"
  REPOS=("$REPO_SCOPE")
fi
```

- [ ] **Step 4: Add the bucket helper and the rename pass**

Add above `seed_project()`:

```bash
put_bucket() {
  local project="$1" view="$2" id="$3" title="$4" position="$5" body=""
  body="$(jq -cn --arg t "$title" --argjson p "$position" '{title: $t, position: $p, limit: 0}')"
  write_or_plan POST "/projects/$project/views/$view/buckets/$id" "$body"
}
```

Inside `seed_project`, immediately after the `buckets_json` read, insert the rename pass: for each `pair` in `BUCKET_RENAMES`, split `old`/`new`; when `new` is absent and `old` is present, refuse if the bucket's `count` is not 0 (`die "bucket \"$old\" in \"$repo\" holds $count cards; move them before renaming"`), otherwise `put_bucket` with the new title and the position `BUCKETS` gives it, print `say bucket "$repo / $VIEW_TITLE / $old" renamed "id=$id -> $new"`, and refresh `buckets_json` with a fresh `read_endpoint`.

- [ ] **Step 5: Adopt the stock kanban view in one full-record write**

Replace view creation with `adopt_view`: read `GET /projects/{p}/views`, select the entry whose `view_kind` is `kanban` — never create one, `die` when there is none — and post the whole record back with `title: "Board"`, `position: 1`, `bucket_configuration_mode: "manual"`, `default_bucket_id: <Backlog id>`, `done_bucket_id: <Done id>`:

```bash
  body="$(jq -cn --arg t "$VIEW_TITLE" --argjson d "$done_id" --argjson b "$default_id" \
    '{title: $t, position: 1, bucket_configuration_mode: "manual",
      done_bucket_id: $d, default_bucket_id: $b}')"
  write_or_plan POST "/projects/$project_id/views/$view_id" "$body"
```

The order is fixed: buckets first, then this write, because it needs both ids.

- [ ] **Step 6: Set positions after creation**

After the existing creation loop, add a pass over `BUCKETS` computing `position=$((100 * (index + 1)))`, reading the bucket's current position, and calling `put_bucket` only when it differs — printing `positioned` or `existing`.

- [ ] **Step 7: Prove the dry run writes nothing and plans the right shape**

Run:

```bash
VIKUNJA_API_TOKEN="$VIKUNJA_MCP_TOKEN" ./hack/vikunja-seed.sh --repo homelab --dry-run
```

Expected: `bucket "homelab / Board / Ideas" renamed … -> Idea`, `… / Revision … -> Validation`, `… / Shaping … would-create`, one view POST carrying all five settings, five `positioned` lines, no `FAIL`, and no other project named.

- [ ] **Step 8: Prove the rename refusal**

Create a scratch card through MCP (`tasks_create`) in the `Ideas` bucket of the homelab board, then run the dry run of Step 7.
Expected: `FAIL`/`die` naming the bucket and its card count, and no rename planned. Delete the scratch card with `tasks_delete`, then re-run the dry run and see the rename planned again.

- [ ] **Step 9: Prove the view write did not drop a field**

After the real run (Task 6), read the view through MCP and check all five settings.
Expected: `title: Board`, `position: 1`, `bucket_configuration_mode: manual`, `done_bucket_id` = the `Done` bucket, `default_bucket_id` = the `Backlog` bucket.

- [ ] **Step 10: Commit**

```bash
git add hack/vikunja-seed.sh
git commit -m "🚀 feat(board): six columns by rename, a scoped run, and the stock view"
```

---

### Task 2: Labels, the project front page and the two filters

**Files:**
- Modify: `hack/vikunja-seed.sh` (`seed_filter` at 348, `main` at 526)

**Interfaces:**
- Consumes: Task 1's `BUCKETS`, `BUCKET_RENAMES`, `REPO_SCOPE`, `put_bucket`.
- Produces: `LABELS=(feature:1f6feb story:8250df task:1a7f37 agent:57606a operator:bf8700 implementer:0e8a16 reviewer:6e7781)`, `seed_labels`, `seed_filters`, `retire_old_filter`, and the project front page written from inside `seed_project`.
- Produces: `verify()` gaining the eight checks of Step 5.

- [ ] **Step 1: Add `seed_labels`, called once before the repo loop**

```bash
seed_labels() {
  local read_json="" pair title colour id body=""
  read_endpoint /labels
  read_json="$API_BODY"
  for pair in "${LABELS[@]}"; do
    title="${pair%%:*}"; colour="${pair##*:}"
    id="$(jq -r --arg t "$title" '[.[] | select(.title == $t)][0].id // empty' <<<"$read_json")"
    if [ -n "$id" ]; then say label "$title" existing "id=$id"; C_EXISTING=$((C_EXISTING + 1)); continue; fi
    body="$(jq -cn --arg t "$title" --arg c "#$colour" '{title: $t, hex_color: $c}')"
    write_or_plan PUT /labels "$body"
    if [ "$DRY_RUN" = 1 ]; then say label "$title" would-create; else
      say label "$title" created; C_CREATED=$((C_CREATED + 1))
    fi
  done
}
```

- [ ] **Step 2: Replace `seed_filter` with `seed_filters`, writing both filters from the homelab board's ids**

```bash
seed_filters() {
  local validation_id="$1" doing_id="$2" title="" query="" id="" body=""
  for pair in "Needs my validation:$validation_id" "Agent work in flight:$doing_id"; do
    title="${pair%:*}"; query="bucket_id = ${pair##*:} && done = false"
    read_endpoint /filters
    id="$(jq -r --arg t "$title" '[.[] | select(.title == $t)][0].id // empty' <<<"$API_BODY")"
    body="$(jq -cn --arg t "$title" --arg q "$query" \
      '{title: $t, is_favorite: ($t == "Needs my validation"), filters: {filter: $q, sort_by: ["created"], order_by: ["asc"]}}')"
    if [ -n "$id" ]; then
      write_or_plan POST "/filters/$id" "$body"; say filter "$title" updated "id=$id"
    else
      write_or_plan PUT /filters "$body"; say filter "$title" created
    fi
  done
}
```

Call it from `main` only when `REPO_SCOPE` is `homelab` or empty, passing the `Validation` and `Doing` bucket ids captured in `seed_project`.

- [ ] **Step 3: Delete the retired filter, explicitly and by name**

Add after `seed_filters`:

```bash
retire_old_filter() {
  local id=""
  read_endpoint /filters
  id="$(jq -r --arg t "$FILTER_TITLE" '[.[] | select(.title == $t)][0].id // empty' <<<"$API_BODY")"
  if [ -z "$id" ]; then say filter "$FILTER_TITLE" absent; return 0; fi
  write_or_plan DELETE "/filters/$id" ""
  say filter "$FILTER_TITLE" retired "id=$id"
}
```

- [ ] **Step 4: Write the project front page**

In `seed_project`, after the project id is known:

```bash
  body="$(jq -cn --arg t "$repo" \
    --arg d "Repository: https://github.com/vyrtualsynthese/homelab
Branches: \`develop\` (integration), \`main\` (production)
Docs: https://github.com/vyrtualsynthese/homelab/tree/develop/docs
Protocol: https://github.com/vyrtualsynthese/homelab/blob/develop/docs/agent-protocol.md

Kind: label \`feature\` / \`story\` / \`task\`. A subtask is a checklist line inside a task." \
    '{title: $t, description: $d}')"
  write_or_plan POST "/projects/$project_id" "$body"
```

Skip the write when the project already carries that description (`jq -e --arg d … '.description == $d'`).

- [ ] **Step 5: Extend `verify()` to the new shape**

In `verify()`, replace the four-title loop with checks that report one `PASS`/`FAIL` line each:

| Line | Check |
|---|---|
| `columns` | the six titles exist, in `position` order |
| `done bucket` | `done_bucket_id` equals the `Done` bucket's id |
| `default bucket` | `default_bucket_id` equals the `Backlog` bucket's id |
| `bucket mode` | `bucket_configuration_mode` is `manual` |
| `labels` | `GET /labels` carries all seven titles |
| `filters` | `Needs my validation` has `bucket_id = <Validation>` and `Agent work in flight` has `bucket_id = <Doing>`, both with `done = false` |
| `kind labels` | every task in the project carries exactly one of `feature`/`story`/`task` |
| `column kinds` | `Doing` and `Validation` hold no task labelled `feature` or `story` |

Exit 1 when any line fails, naming the project and the check.

- [ ] **Step 6: Prove `--verify` fails before it passes**

Run it against the board as it is now (old columns, no labels).
Expected: `FAIL columns`, `FAIL labels`, `FAIL column kinds`-style output and exit 1. That failure is the proof the checks are real.

- [ ] **Step 7: Dry-run the whole shape and read it back**

Run: `VIKUNJA_API_TOKEN="$VIKUNJA_MCP_TOKEN" ./hack/vikunja-seed.sh --repo homelab --dry-run`
Expected: seven labels (`would-create` or `existing`), two filters planned with `bucket_id = 14` and `bucket_id = 11`, `filter "Awaiting my review" retired`, and one project POST.

Then with MCP after the real run in Task 6: `projects_list` shows the homelab description, `labels_list` returns seven.

- [ ] **Step 8: Commit**

```bash
git add hack/vikunja-seed.sh
git commit -m "🚀 feat(board): seven labels, the front page, two saved filters, and the checks"
```

---

### Task 3: The mapping — 27 features and 152 tasks

**Files:**
- Create: `hack/vikunja-migrate-2026-11.json`
- Test: `hack/tests/test_vikunja_migrate.py` (validation tests land in Task 5)

**Interfaces:**
- Produces: the map file, with this exact shape:

```json
{
  "features": [
    {"key": "w0-cleanup", "title": "Longhorn cleanup", "position": 100,
     "docs": "docs/superpowers/plans/2026-11-10-w0-cleanup.md", "why": "One paragraph."}
  ],
  "tasks": [
    {"id": 3, "kind": "task", "title": "Inventory the Longhorn volumes and record a disposition",
     "feature": "w0-cleanup", "column": "Backlog",
     "docs": "docs/superpowers/plans/2026-11-10-w0-cleanup.md",
     "branch": "none", "pr": "none", "body": ""}
  ],
  "backfill": [
    {"title": "Give the prowlarr probes the route's own Host header",
     "feature": "media-tools-authentik", "pr": "#1113", "merged": "2026-11-15"}
  ]
}
```

- [ ] **Step 1: Dump the board as the task source**

Run this against the MCP-derived listing and save it to `/tmp/tickets.json`:

```bash
# one line per ticket: id \t <plan path> \t task number \t title
```
Produce it with `do_action project_tasks_list` twice (pages 1 and 2, `fields: ["id","title","description"]`), then transform with `jq -r '.[] | .id, …'`.

- [ ] **Step 2: Build the feature list from the 27 plans**

For each distinct `plan:` path in the dump, read the plan's first heading and its "Goal"/intent paragraph, and write one feature entry: `key` = file name without the date and `.md`, `title` = a sentence a stranger understands (not the file name), `position` = 100 apart, in the programme order **W0 → W5 → W6 → W1 → W2 → W3 → W7 → W4 → W8**, then the app-level plans alphabetically, `docs` = the plan path, `why` = one paragraph taken from the plan's own intent, not invented.

Top of the order:

```json
{"key": "w0-cleanup", "title": "Longhorn cleanup and dispositions", "position": 100, ...}
{"key": "w5-platform", "title": "Platform upgrades: Traefik, K3s, GPU", "position": 200, ...}
```

- [ ] **Step 3: Build the task list**

For each ticket: `id` unchanged; `feature` = the key of its plan; `column` = `Backlog` for now (Task 4 moves the merged ones); `docs` = its plan path; `title` = the current title with its **exact** `<plan-name>: ` prefix removed, first letter capitalised, and the plan-name taken from the ticket's own description rather than a regex over the title; `branch`/`pr` = `none`; `body` = `""`.

- [ ] **Step 4: Check the count and the shape**

Run:

```bash
jq -e '(.features|length) == 27' hack/vikunja-migrate-2026-11.json &&
jq -e '(.tasks|length) == 152' hack/vikunja-migrate-2026-11.json &&
jq -r '.features[].key' hack/vikunja-migrate-2026-11.json | sort | uniq -d | wc -l
```

Expected: both `-e` checks pass, and the duplicate count is `0`.

- [ ] **Step 5: Commit**

```bash
git add hack/vikunja-migrate-2026-11.json
git commit -m "📝 docs(board): the reviewed mapping of 27 features and 152 tasks"
```

---

### Task 4: The merged work — 29 PRs to `Done`

**Files:**
- Modify: `hack/vikunja-migrate-2026-11.json` (the `backfill` array, and `pr`/`column` on matched tasks)

**Interfaces:**
- Consumes: Task 3's `tasks[]` and `features[]`.
- Produces: `backfill[]` entries and updated `tasks[]` where a PR closed a plan task.

- [ ] **Step 1: List the merged PRs with their files**

Use the GitHub tools for `vyrtualsynthese/homelab`, `state=closed`, `base=develop`, one page of 100: for each merged PR record number, title, `merged_at`, head branch, and the file paths it touched.

- [ ] **Step 2: Match each PR to a task or to nothing**

A match needs two of: the branch name's topic appears in the plan's scope, the PR's files are the paths the plan task names, the PR title names the plan. Write the evidence next to each match in a scratch list.

- [ ] **Step 3: Write the matches and the rest**

- Matched: set that task's `pr` to `#<number>` and its `column` to `Done`.
- Unmatched: append a `backfill` entry, `title` = the PR title without its emoji, `pr` = `#<number>`, `merged` = the merge date, `feature` = the matching feature key or `null`.

- [ ] **Step 4: Produce the report the operator approves**

Run `jq -r '.tasks[] | select(.pr != "none") | "\(.pr) closes #\(.id) \(.title)"' hack/vikunja-migrate-2026-11.json` plus the `backfill` list, and paste both into the ticket for this task. Expected: 29 PRs across the two lists, and the operator's approval recorded as a comment before Task 6 runs.

- [ ] **Step 5: Commit**

```bash
git add hack/vikunja-migrate-2026-11.json
git commit -m "📝 docs(board): map 29 merged PRs onto the board"
```

---

### Task 5: The migration script, tested

**Files:**
- Create: `hack/vikunja_migrate.py`
- Create: `hack/tests/test_vikunja_migrate.py`

**Interfaces:**
- Produces: `clean_title(title: str, plan_key: str) -> str`, `render_description(docs: str, branch: str, pr: str, body: str) -> str`, `validate(map_doc: dict) -> list[str]`, `plan_actions(map_doc: dict, state: dict) -> list[dict]`, and `main(argv)` accepting `--dry-run`, `--apply`, `--map`, `--base-url`.
- Consumes: the map file of Task 3/4; the API through `urllib.request` with `Authorization: Bearer $VIKUNJA_API_TOKEN`.

- [ ] **Step 1: Write the failing tests**

```python
import unittest
import vikunja_migrate as m

class TitleTests(unittest.TestCase):
    def test_strips_only_the_plans_own_prefix(self):
        self.assertEqual(
            m.clean_title("sonarr: Rebuild the chart", "sonarr-chart-refactor"),
            "Rebuild the chart")
    def test_leaves_a_colon_that_is_not_the_prefix(self):
        self.assertEqual(
            m.clean_title("Rebuild `radarr`: the chart", "radarr-chart-refactor"),
            "Rebuild `radarr`: the chart")
    def test_capitalises_the_first_letter(self):
        self.assertEqual(m.clean_title("w0-cleanup: inventory it", "w0-cleanup"), "Inventory it")

class DescriptionTests(unittest.TestCase):
    def test_block_with_no_pr(self):
        self.assertEqual(
            m.render_description("docs/x.md", "none", "none", ""),
            "repo: vyrtualsynthese/homelab\n"
            "docs: https://github.com/vyrtualsynthese/homelab/blob/develop/docs/x.md\n"
            "branch: none\npr: none")
    def test_body_is_kept_under_its_heading(self):
        got = m.render_description("docs/x.md", "feature/a", "#7", "What changed\n- one")
        self.assertTrue(got.startswith("repo: vyrtualsynthese/homelab\n"))
        self.assertTrue(got.endswith("branch: feature/a\npr: #7\n\nWhat changed\n- one"))

class ValidateTests(unittest.TestCase):
    def test_unknown_feature_key_is_reported(self):
        problems = m.validate({"features": [], "tasks": [{"id": 1, "feature": "nope"}], "backfill": []})
        self.assertEqual(len(problems), 1)
        self.assertIn("nope", problems[0])
    def test_task_column_outside_the_six_is_reported(self):
        problems = m.validate({"features": [{"key": "a"}],
                               "tasks": [{"id": 1, "feature": "a", "kind": "task", "column": "Ready"}],
                               "backfill": []})
        self.assertEqual(len(problems), 1)
        self.assertIn("Ready", problems[0])
    def test_container_in_doing_is_reported(self):
        problems = m.validate({"features": [{"key": "a"}],
                               "tasks": [{"id": 1, "feature": "a", "kind": "feature", "column": "Doing"}],
                               "backfill": []})
        self.assertEqual(len(problems), 1)
        self.assertIn("Doing", problems[0])

class PlanTests(unittest.TestCase):
    def test_a_matching_board_produces_no_actions(self):
        map_doc = {"features": [{"key": "a", "title": "A", "position": 100, "docs": "d", "why": "w"}],
                   "tasks": [{"id": 1, "title": "T", "feature": "a", "column": "Backlog",
                              "docs": "d", "branch": "none", "pr": "none", "body": ""}],
                   "backfill": []}
        state = {"features": {"A": 9}, "tasks": {1: {"title": "T", "bucket": "Backlog", "labels": ["task"]}}}
        self.assertEqual(m.plan_actions(map_doc, state), [])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd hack && python3 -m unittest tests.test_vikunja_migrate -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'vikunja_migrate'`.

- [ ] **Step 3: Implement the four functions**

`clean_title`: strip `"<plan key or file stem>: "` from the front, case-insensitively on the separator only, then upper-case the first character. `render_description`: the block of §10.1, `repo:` constant, `docs:` prefixed with the blob URL and `develop`, `branch:`/`pr:` verbatim with `none` preserved, body appended after a blank line when non-empty. `validate`: the four rules of the design — unique feature keys, every task's `feature` known, `column` one of the six, and containers only in `Idea`/`Shaping`/`Backlog`/`Done` while kind `task` may sit anywhere from `Backlog` rightwards. `plan_actions`: compare the map against a board snapshot and return only the differences, each `{"op": "create"|"update"|"place"|"label", …}`.

- [ ] **Step 4: Run the tests again**

Run: `cd hack && python3 -m unittest tests.test_vikunja_migrate -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Implement the thin HTTP layer and the two modes**

`--dry-run` prints one line per action and writes nothing; `--apply` performs them in order: features, then tasks (title, description, label, parent, bucket). Print `existing` for anything already correct.

- [ ] **Step 6: Prove the dry run against the live board makes no writes**

Run: `VIKUNJA_API_TOKEN="$VIKUNJA_MCP_TOKEN" python3 hack/vikunja_migrate.py --dry-run --map hack/vikunja-migrate-2026-11.json | head -40`
Expected: 27 feature lines and 152 task lines; the board's task count read through MCP is unchanged afterwards.

- [ ] **Step 7: Commit**

```bash
git add hack/vikunja_migrate.py hack/tests/test_vikunja_migrate.py
git commit -m "🚀 feat(board): a one-shot migration that reads a reviewed mapping"
```

---

### Task 6: Apply on the board, and prove it

**Files:**
- Modify: the live `homelab` board (no repository file)
- Test: `hack/vikunja-seed.sh --verify`, plus MCP reads

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Run the seed for real, scoped to homelab**

Run: `VIKUNJA_API_TOKEN="$VIKUNJA_MCP_TOKEN" ./hack/vikunja-seed.sh --repo homelab`
Expected: five `renamed`/`existing`, one `created` bucket, seven labels, two filters, one `retired`.

- [ ] **Step 2: Confirm the bucket ids survived**

Through MCP `buckets_list` for project 2, view 13.
Expected: ids 10, 11, 12, 13, 14 unchanged, with titles `Idea`, `Doing`, `Done`, `Backlog`, `Validation`, plus one new id for `Shaping`.

- [ ] **Step 3: Apply the migration**

Run: `VIKUNJA_API_TOKEN="$VIKUNJA_MCP_TOKEN" python3 hack/vikunja_migrate.py --apply --map hack/vikunja-migrate-2026-11.json`
Expected: 27 features created, 152 tasks updated, the merged ones moved to `Done`; no `FAIL`.

- [ ] **Step 4: Prove a second run is a no-op**

Run the same command again.
Expected: every line `existing`, zero writes, and the board's task and feature counts unchanged. This is the idempotence the failure of a partial run depends on.

- [ ] **Step 5: Verify the shape**

Run: `VIKUNJA_API_TOKEN="$VIKUNJA_MCP_TOKEN" ./hack/vikunja-seed.sh --repo homelab --verify`
Expected: every line `PASS`, including "Doing and Validation hold only task cards" and "every task carries exactly one kind label".

- [ ] **Step 6: Read the board back through MCP and count**

Query the six buckets with their tasks. Expected: `Doing` and `Validation` empty, `Done` holding the 29 PRs, no ticket in bucket 0, 27 feature cards in `Backlog`.

- [ ] **Step 7: Board this plan's remaining tasks**

Create a feature "Board workflow and docs" and one task per remaining step of Tasks 7 and 8, in `Backlog`, so the protocol's own rule applies to its own execution.

- [ ] **Step 8: Record the run on the ticket**

Append the applied counts to the ticket's description, not to a file; commit nothing here beyond any script fix the run exposed.

---

### Task 7: Say it in words — protocol, runbook, README, global snippet

**Files:**
- Rewrite: `docs/agent-protocol.md`
- Rewrite: `docs/runbooks/vikunja-board-seed.md`
- Modify: `docs/README.md` (§"Where each kind of knowledge lives")
- Modify: `~/.config/opencode/AGENTS.md` (§"Task board", five lines)

**Interfaces:**
- Consumes: the six column names, three kinds and the transition table of the spec (§9–§11).

- [ ] **Step 1: Rewrite `docs/agent-protocol.md`**

Keep its voice: it is the long form, with specimens. Replace the four-state model with the six columns, state the kinds and the container rule (`Doing`/`Validation` are task-only), keep the refusal section and add that a refusal files follow-up tasks in `Backlog`, and add one line that only `homelab` is boarded and a session in another repository files nothing.

- [ ] **Step 2: Check the protocol against the board**

Run: `grep -c 'Waiting for validation\|Revision' docs/agent-protocol.md`
Expected: `0`.

- [ ] **Step 3: Rewrite the runbook**

Columns, the rename behaviour and why ids matter, what `--repo` does, the two filters, the retired filter, and a "what to do when a line fails" table covering the new failures: a non-empty bucket refusing a rename, a filter with a stale bucket id, and a view whose done bucket is 0.

- [ ] **Step 4: Update the two READMEs**

`docs/README.md`: replace the "Vikunja only, once it exists" sentence with the six states and the protocol link. `~/.config/opencode/AGENTS.md`: the five-line snippet now names the six columns, the kinds, and that agents move only tasks and never to `Done`.

- [ ] **Step 5: Prove no stale vocabulary survives in the repo**

Run: `grep -rn "Waiting for validation" docs/ README.md AGENTS.md | grep -v superpowers/specs | grep -v superpowers/plans`
Expected: no output outside the historical specs and plans.

- [ ] **Step 6: Commit**

```bash
git add docs/agent-protocol.md docs/runbooks/vikunja-board-seed.md docs/README.md
git commit -m "📝 docs(board): the six-state protocol, and the runbook that matches it"
```

---

### Task 8: Handover

**Files:**
- Modify: nothing, unless a gate fails

- [ ] **Step 1: Rebase on `develop`**

Run: `git fetch origin && git rebase origin/develop`
Expected: clean, or conflicts resolved keeping this branch's intent.

- [ ] **Step 2: Run the repository's gates**

```bash
python3 -m unittest discover -s hack/tests -v
./hack/check-exposure.sh
git status --short
```

Expected: all tests pass, the exposure check passes, working tree clean.

- [ ] **Step 3: Push and open the pull request**

Run: `git push -u origin feature/board-workflow`, then open the PR with `github_create_pull_request`, base `develop`, body naming the spec, the plan, the applied counts and the verification output.

- [ ] **Step 4: Move this plan's tickets to `Validation`**

Each ticket: description carries the PR link and the `--verify` output; column `Validation`. The operator accepts or refuses; nothing here moves to `Done`.
