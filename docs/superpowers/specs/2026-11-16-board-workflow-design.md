# Design — the homelab board's workflow, wording and structure

- **Date:** 2026-11-16
- **Status:** awaiting operator approval.
- **Owner:** operator. Every decision marked **(operator)** was taken in conversation on 2026-11-16;
  every decision marked **(design)** is this document's call and can be overruled.
- **Supersedes:** §9.2 (board shape), §9.3 (what a ticket is) and §9.4 (state machine) of
  `2026-11-15-agent-work-tracking-and-docs-site-design.md`, and the five-line task-board snippet in
  `~/.config/opencode/AGENTS.md`. Everything else in that design stands.
- **Scope:** the `homelab` project only. The other six repository projects stay empty and untouched.

## 1. Intent

The board must answer three questions at three speeds, and today it answers none of them: it has
152 tickets, no labels, no column membership and no history.

| Question | Who asks it | How often |
|---|---|---|
| "What am I thinking about?" | the operator | weekly or slower |
| "What is planned and not started?" | the operator | daily |
| "What are agents running, and what needs my yes or no?" | the operator | hourly |

The old four-state model answered only the third question. It had nowhere to keep an idea, so ideas
lived in the operator's head, and it had no vocabulary for a feature against a task, so every ticket
was the same size on screen.

## 2. What exists today

- `homelab` project (id 2), 152 tickets, all `done = false`, all `bucket_id = 0` (in no column), no
  labels, no assignees.
- Tickets came from 27 plan files, one ticket per plan task, titled `<plan-name>: <task title>` —
  readable to whoever wrote the plan, opaque to anyone else.
- The real board is Vikunja's **stock `Kanban` view** (id 13), carrying five buckets renamed by hand:
  `Ideas`, `Backlog`, `Doing`, `Revision`, `Done`. No shaping stage, and the validation stage is not
  called that.
- Default views: `List` (5), `Gantt` (6), `Table` (7). The Gantt view has nothing to draw, because no
  ticket has a start or end date.
- `feature/board-columns` is an unmerged, broken attempt at this same problem: it rewrites the seed
  script to adopt the stock view, never create a second one, and rebuild the whole view record in one
  POST because the endpoint zeroes omitted fields. Its `--verify` and `--dry-run` die on a `jq` type
  error, so it is superseded by this design rather than continued; its two lessons are kept (§9.2).
- One saved filter, `Awaiting my review`, built before `bucket_id` was a filterable field.
- `hack/vikunja-seed.sh` creates the six repo projects, one view, four buckets and one filter; it and
  `docs/agent-protocol.md` and `docs/runbooks/vikunja-board-seed.md` all encode the four-state model.
- 29 PRs merged into `develop` (numbers 1108–1137), none of them represented on the board.
- The other six projects are empty; no plan file exists only on a branch.

## 3. Goals

1. One screen shows, in order, what the operator is thinking about, what is planned, what is running,
   and what is waiting on a decision.
2. Ideas can be captured in seconds and are never lost to a session that ends.
3. Finished work that predates the board is visible on the board, with the PR that finished it.
4. Every ticket says what kind of thing it is and where its documentation lives.
5. The wording is the operator's own, and the same words appear in the UI, the protocol and the
   agent instructions.

## 4. Non-goals

- Boarding the other six repositories. They stay empty until they have work.
- Automating GitHub: no webhooks (they are disabled on this instance), no sync service, no Actions.
- A second user account, SSO on the board, or notifications by e-mail. One human, one account.
- Reworking the documentation site. Doc links point at GitHub until that site exists.

## 5. Anti-goals

- A column whose contents mean two different things, so the board cannot be read at a glance.
- Two places claiming to know whether a task is done.
- Backfilled history invented from memory. Only merged PRs and merged commits are evidence.
- A board an agent can finish on its own behalf.

## 6. Constraints

Vikunja **v2.8.0**, verified against the instance and the tagged source:

- A kanban column is a *bucket* belonging to a *view*; a task's column is stored per view. Two kanban
  views over one project would therefore hold two independent answers to "where is this task", so
  there is exactly one kanban view.
- Buckets in `manual` mode are moved by hand or over the API; `filter` mode assigns by rule. Manual is
  kept: the state must be a decision, not a side effect of editing a label.
- A view has a `done_bucket_id`: a task moved there is marked done, and a task marked done appears
  there. Exactly one bucket carries this.
- `bucket_id` **is** filterable in v2.8, so a saved filter can name a column. It stores numeric bucket
  ids, so it must be rebuilt if a bucket is recreated.
- Tasks relate as `parenttask`/`subtask` with no depth limit below the cycle guard; the operator UI
  renders nesting. Relations also carry `blocking`/`blocked`, available but not used by the protocol.
- There are no custom fields. Structured metadata therefore lives in the description as a fixed block,
  or in a label.
- A view update **replaces** the record: `PATCH /projects/{p}/views/{v}` sends the whole view, and
  every field left out is zeroed. A partial body silently clears `done_bucket_id` or the bucket mode.
- Filters are per user. Saved filters appear in the project list as pseudo-projects with negative ids.
- Labels are global to the account, coloured, and applied across projects.

From the repository: everything reaches the cluster through git and ArgoCD; the board is a service,
not a manifest source. The board protocol is documentation, edited in `docs/`.

## 7. Approaches considered

| Option | What it costs | Verdict |
|---|---|---|
| **A. One board, one card type, kind as a label** | One convention to hold: containers only in the left three columns | **Chosen (operator)** |
| B. Two boards, shaping and delivery, a ticket changes project when cut | Cleanest split of thinking from agent traffic; promotion becomes an operation, and no single screen | Rejected |
| C. A project per feature, cards are tasks | Every feature gets a page; the board fragments, and each new feature means new views and filters | Rejected |

A was chosen because it keeps the operator's own flow literally as described, and pays for it with one
rule rather than with maintenance.

## 8. Decisions taken (operator, 2026-11-16)

1. Approach A: one kanban, one project.
2. The six columns are **Idea**, **Shaping**, **Backlog**, **Doing**, **Validation**, **Done**.
3. The kinds are **Feature**, **Story**, **Task** as cards. A **Subtask** is a checklist line inside a
   task, not a card.
4. Only the `homelab` project is boarded now.
5. Finished work is backfilled from merged PRs, with their links.
6. **(operator)** The other six projects are left exactly as they are: empty, with the old column
   shape. A session working in one of those repositories files no ticket until that repository is
   boarded, and the seed script takes `--repo` so it can touch one project.
7. **(operator)** The 152 seeded descriptions are rewritten into the fixed block of §10.1.
8. **(operator)** The Backlog order is carried by card position: features in the programme's revised
   workstream sequence, tasks in plan order under their feature. `priority` stays unused at 0.

## 9. The board

### 9.1 Project

`homelab` (id 2). Its description becomes the project's front page:

```markdown
Repository: https://github.com/vyrtualsynthese/homelab
Branches: `develop` (integration), `main` (production)
Docs: https://github.com/vyrtualsynthese/homelab/tree/develop/docs
Protocol: https://github.com/vyrtualsynthese/homelab/blob/develop/docs/agent-protocol.md

Kind: label `feature` / `story` / `task`. A subtask is a checklist line inside a task.
```

### 9.2 Views

Vikunja gives every project four views, one of them kanban. The board **is** that kanban view, renamed
`Board`; no second kanban view is ever created, because two would hold two answers to "where is this
task". `List`, `Gantt` and `Table` are left as Vikunja ships them: they are stock, harmless, and
deleting a view is a destructive step this design does not need to take.

The view is written in a **single full record update**, carrying every field — title, position 1,
`bucket_configuration_mode: manual`, `default_bucket_id: Backlog`, `done_bucket_id: Done` — because
the endpoint replaces the record and zeroes whatever the body leaves out. This is the trap that broke
`feature/board-columns`.

| Setting | Value | Why |
|---|---|---|
| Title | `Board` | Says what it is; the stock name is `Kanban` |
| Position | 1 | The board opens first |
| Bucket mode | `manual` | State is a decision, never a side effect of a label |
| Default bucket | `Backlog` | A ticket created without a column joins the queue, not `Idea` |
| Done bucket | `Done` | Exactly one bucket marks its tasks done |

### 9.3 Columns and what may sit in them

| Column | Meaning | Kinds allowed |
|---|---|---|
| `Idea` | A thought worth keeping. Not yet a commitment; often one paragraph. | Feature, Story |
| `Shaping` | Validated as worth doing. Being cut into stories and tasks. | Feature, Story |
| `Backlog` | Cut, planned, independent, not started. Carries the priority order. | Feature, Story, Task |
| `Doing` | An agent is working on it, or is blocked on it. | Task |
| `Validation` | The agent believes it is finished; the operator has not looked. | Task |
| `Done` | The operator accepted it. Nothing else puts a ticket here. | Feature, Story, Task |

`Backlog` is the only column that mixes kinds, deliberately: it is the plan, and a plan reads as a
feature with its tasks underneath it.

### 9.4 Labels

| Label | Colour | Applied to |
|---|---|---|
| `feature` | `#1f6feb` | A bundle of work with one why. Top-level card, no parent. |
| `story` | `#8250df` | A slice of a feature a user would recognise. Child of a feature. |
| `task` | `#1a7f37` | One session's work, one thing to validate. Child of a story, or of a feature when it has no stories. |
| `agent` | `#57606a` | Author of the ticket or of the change was an agent |
| `operator` | `#bf8700` | Author was the operator |
| `implementer` | `#0e8a16` | The agent session wrote the change |
| `reviewer` | `#6e7781` | The agent session reviewed it |

Stories are optional. A feature with one obvious chunk gets tasks directly; the `story` label exists
for features that read better split, and the migration in §11 does not invent stories to fill the
level.

`agent` and `operator` describe **who wrote the ticket from now on**, not history: every push leaves
under one GitHub account and the instance does not record who created the 152 seeded tickets, so
applying either label to them would be invention. A ticket created before the migration therefore
carries a kind and nothing else.

### 9.5 Saved filters

| Filter | Query | Why |
|---|---|---|
| `Needs my validation` | `bucket_id = <Validation> && done = false` | The operator's queue. Favourite. |
| `Agent work in flight` | `bucket_id = <Doing> && done = false` | What is running right now |

Both are rebuilt by the seed script after the buckets exist, because they store bucket ids. The
existing `Awaiting my review` filter is deleted: it predates `bucket_id` and lists every open ticket,
which is the board itself.

## 10. The cards

### 10.1 Ticket anatomy

Every ticket's description opens with a fixed block, so the same facts sit in the same place:

```text
repo: vyrtualsynthese/homelab
docs: docs/superpowers/plans/2026-11-15-vikunja-board.md
branch: feature/vikunja-board
pr: #1134

What changed
- ...

Verification
$ <command>
<output>
```

`docs:` points at a path in the repository, linked to `https://github.com/vyrtualsynthese/homelab/blob/develop/<path>`.
`pr:` and `branch:` are filled as they exist; `none` is written rather than left blank, so a reader
can tell "no PR" from "not filled in".

### 10.2 Hierarchy

Vikunja's own parent/subtask relation carries the tree. A story is a subtask of its feature; a task is
a subtask of its story or of its feature; a subtask is a `- [ ]` line in the task's description. A
checklist line that later deserves to be tracked on its own becomes a card by hand, and the checklist
line goes away with it.

### 10.3 Titles

Titles are plain sentences in the imperative, with no plan prefix: *"Rebuild the cross-seed chart"*,
*"Inventory the Longhorn volumes and record a disposition"*. The feature card above them carries the
context the prefix used to carry, and the `docs:` line carries the path.

## 11. Who moves what

| From | To | Who | When |
|---|---|---|---|
| — | `Idea` | operator or agent | A thought is captured |
| `Idea` | `Shaping` | operator | Decided it is worth doing |
| `Idea` | *(deleted or left)* | operator only | Decided it is not worth doing |
| `Shaping` | `Backlog` | operator or agent | Cut into tasks, each with a docs line, in priority order |
| `Backlog` | `Doing` | agent | A session starts the task and says so in a comment |
| `Doing` | `Validation` | agent | Branch pushed, PR open, verification run and pasted into the description |
| `Validation` | `Done` | **operator only** | The operator accepts the work |
| `Validation` | `Doing` | **operator only** | The operator refuses it; the comment says why |
| `Backlog`/`Doing` | `Backlog` | operator or agent | New tickets for follow-up work a refusal created |
| any | `Done` on a Feature or Story | **operator only** | The last child was accepted |

Rules that keep the columns honest:

- **Agents move tasks.** An agent never moves a Feature or Story card, and never moves anything to
  `Done`.
- **A refusal creates tickets, not rework in place.** The refused task goes back to `Doing` when the
  same session fixes it; work that needs a separate session becomes its own task in `Backlog`, so it
  is visible and can be taken by someone else.
- **Blocked is a comment, not a column.** A task that cannot finish stays in `Doing` with a comment
  naming what blocks it.
- **A container's column is the operator's judgement**, not a computed roll-up: a feature is in
  `Shaping` until it is cut, in `Backlog` while its tasks are planned, and in `Done` when the operator
  says the feature is finished.

## 12. Migration of the 152 tickets

One pass, scripted and idempotent, in this order:

1. **Buckets, by renaming in place.** `Ideas` → `Idea`, `Revision` → `Validation`, `Backlog`,
   `Doing` and `Done` keep their names, `Shaping` is created once, positions set to 100–600 in reading
   order. Renaming rather than recreating keeps every bucket id, which is what the two saved filters
   store.
2. **The view**, as one full-record update (§9.2).
3. **Labels.** Create the seven labels of §9.4.
4. **Features.** One feature per plan file (27), titled from the plan's own title, description holding
   the `repo:`/`docs:` block and the plan's one-paragraph intent.
5. **Tasks.** Each existing ticket keeps its id, gains `task`, gets its parent feature, loses its
   `<plan-name>: ` prefix from the title, has its description rewritten into the §10.1 block, and is
   placed in the column its evidence supports: `Done` when a merged PR closed it (§13), otherwise
   `Backlog`.
6. **Order.** The 27 feature cards get explicit positions in the programme's revised workstream order;
   tasks stay in plan order inside their feature, and the `priority` field stays at 0 because it is too
   coarse to order 152 tickets.
7. **Filters.** Create or replace `Needs my validation` and `Agent work in flight`, then delete
   `Awaiting my review`.

Every step matches by title or id, so a second run changes nothing and prints what it found. No bucket
holding tasks is deleted or renamed without being emptied first, and nothing here deletes a view.

The pass is a **one-shot script, committed with its mapping**, not a mode of `hack/vikunja-seed.sh`.
The seed script stays declarative — it shapes a board and is safe to run forever; the migration reads
30 plans and 29 PRs once, and a permanent `--migrate` mode would carry that weight for good. The
mapping (ticket → kind, parent, column, PR) is a committed data file the script consumes, so a partial
run is re-runnable, and the file is reviewable before anything is written.

The programme index, `2026-11-10-homelab-programme-index.md`, is not boarded: it has no tasks of its
own, and its revised workstream sequence is used as the feature order in step 6.

## 13. Backfilling finished work

Evidence is the merger, nothing else.

1. Read the 29 merged PRs into `develop` (number, title, merged date, head branch, files).
2. Match each PR to an existing ticket through the plan it belongs to, using the PR's files and
   branch name. A match means that ticket gets `pr: #<number>` and moves to `Done`.
3. A PR that matches no plan task becomes its own task, kind `task`, in `Done`, titled with the commit
   subject stripped of its emoji, description carrying `repo`, `pr`, merged date and branch, parented
   to the feature whose area it touched, or parentless when it touched no plan's area.
4. Where a fix went straight to `develop` without a PR, the commit SHA and its link are used in place
   of `pr:`, rather than leaving the work unrecorded.

**The mapping is the operator's to approve, because `Done` is.** A dry run prints the proposed
mapping — one line per PR, the ticket it would close, and the evidence that links them — and writes
nothing. Near-misses are listed beside it, since branch names are topical and several PRs touch charts
that three plans also touch. An unclear match defaults to step 3: the PR becomes its own `Done` task
rather than closing a plan task nobody accepted. The operator corrects the mapping file, then the pass
runs for real.

## 14. What changes in the repository

| File | Change |
|---|---|
| `hack/vikunja-migrate-2026-11.sh` | New, one-shot: reads the committed mapping, writes features, parents, columns, PR links; `--dry-run` prints the proposed PR mapping and writes nothing |
| `hack/vikunja-migrate-2026-11.map` | New, the reviewed mapping: one line per ticket and per PR |
| `hack/vikunja-seed.sh` | Adopt the stock kanban view and rename it `Board` in one full-record POST; six buckets by rename plus one creation; done and default bucket; the seven labels; the two filters; project description; `--repo <name>` scoping so one project can be shaped without touching the six that stay as they are; `--verify` asserts the shape in §9 and that `Doing`/`Validation` hold only `task` cards |
| `docs/agent-protocol.md` | Rewritten around §9–§11: six states, three kinds, the two audiences, the refusal path that creates tickets, and the line that only `homelab` is boarded for now |
| `docs/runbooks/vikunja-board-seed.md` | The procedure and what a failing line means, for the new shape |
| `~/.config/opencode/AGENTS.md` | The five-line task-board snippet: six states, kinds, who moves what |
| `docs/README.md` | One sentence: the board's states and where the protocol lives |

## 15. Open questions

1. **One account or two.** The instance has a single user, which is both the operator and the account
   the MCP token acts as, so an assignee cannot say "an agent has this". **(design)** Labels `agent`
   and `operator` carry that instead, and the assignee stays unused. A second `agent` account would
   make comments and assignees honest, at the cost of a second login, a second token and a share on
   every project. Deferred until the operator wants it.
2. **What happens to an idea that is dropped.** **(design)** It is deleted, because the board is a
   commitment list and a graveyard column would be read as a queue. The alternative — a `dropped`
   label and a filter — is noted here in case the operator prefers an audit trail.

## 16. Verification

- `hack/vikunja-seed.sh --verify` passes: six buckets in order, done and default set, seven labels
  present, two filters pointing at the right bucket ids, no ticket in a missing bucket.
- The five pre-existing bucket ids are unchanged (`Ideas` 10, `Doing` 11, `Done` 12, `Backlog` 13,
  `Revision` 14), so the filters and the history survive the rename.
- Every ticket carries exactly one kind label; `Doing` and `Validation` contain no Feature or Story.
- 27 features exist; the 152 tasks each have a parent or are a merged-PR backfill.
- Every `Done` ticket carries a `pr:` or commit line and a date.
- `git diff develop --stat` shows only the five documentation files and the three scripts; no chart, no
  Application, no cluster resource is touched.
