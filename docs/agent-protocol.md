# The operator↔agent protocol

How a unit of work travels from an idea to a ticket on the board, and back. It binds every agent that
works in these repositories: the short form is five lines in `~/.config/opencode/AGENTS.md`, which
every session reads; this page is the long form, with the states, the kinds, the transitions and two
specimens — a handover and a refusal — made to be copied in shape, not in wording.

The board is Vikunja at <https://vikunja.kub.local>, reachable from the LAN only. It exists because a
plan file cannot answer "is this done, and did anyone check?". A ticked checkbox says an agent ran out
of steps; it does not say the result was accepted.

## The board in one paragraph

One project per GitHub repository, and **only `homelab` is boarded today**: the other six are empty,
and a session working in one of them files no ticket until that repository is boarded. The `homelab`
project has one kanban view, `Board`, whose columns are `Idea`, `Shaping`, `Backlog`, `Doing`,
`Validation` and `Done`. `Done` is the view's **done column**: a card moved there is marked done by
Vikunja itself. Two saved filters read across the board — `Needs my validation` and `Agent work in
flight` — and they are the operator's queue, not columns.

## Three kinds of card, and what a column may hold

| Kind | Label | What it is | Allowed columns |
|---|---|---|---|
| Feature | `feature` | A bundle of work with one why. Top-level, no parent. | `Idea`, `Shaping`, `Backlog`, `Done` |
| Story | `story` | A slice of a feature a person would recognise. Child of a feature. | `Idea`, `Shaping`, `Backlog`, `Done` |
| Task | `task` | One session's work, and one thing to validate. Child of a story, or of a feature when it has no stories. | `Backlog`, `Doing`, `Validation`, `Done` |

`Backlog` is the only column that mixes kinds, deliberately: it is the plan, and a plan reads as a
feature with its tasks underneath it. `Doing` and `Validation` hold tasks only, which is what keeps
those two columns readable — a feature card there would duplicate the children it contains.

A **subtask is not a card.** It is a `- [ ]` line inside a task's description. A line that later
deserves to be tracked on its own becomes a card by hand.

## The six states

| State | What it means | Who may put a card there |
|---|---|---|
| `Idea` | A thought worth keeping. Not yet a commitment. | operator or agent |
| `Shaping` | Decided it is worth doing; being cut into tasks. | **operator** |
| `Backlog` | Cut, planned, independent, not started. Carries the reading order. | operator or agent |
| `Doing` | A session is working on it, or is blocked on it. | agent |
| `Validation` | The work is finished as far as the agent can tell; the operator has not looked. | agent |
| `Done` | The operator accepted the work. Nothing else. | **operator only** |

| From | To | Who | When |
|---|---|---|---|
| — | `Idea` | operator or agent | A thought is captured |
| `Idea` | `Shaping` | operator only | Decided it is worth doing |
| `Shaping` | `Backlog` | operator or agent | Cut into tasks, each with a docs line, in reading order |
| `Backlog` | `Doing` | agent | The session starts the task |
| `Doing` | `Validation` | agent | The branch is pushed, the PR is open, the verification is in the description |
| `Validation` | `Done` | **operator only** | The operator accepts the work |
| `Validation` | `Doing` | **operator only** | The operator refuses it; the comment says why |
| any column | `Done` on a feature or story | **operator only** | The last child was accepted |

Rules that keep the columns honest:

- **Agents move tasks.** An agent never moves a feature or story card, and never moves anything to
  `Done`. The board is worth its overhead only because the last move is somebody else's.
- **A refusal creates tickets.** The refused task goes back to `Doing` when the same session fixes it.
  Work that needs a separate session becomes its own task in `Backlog`, in the same breath, so it is
  visible and can be taken by someone else.
- **Blocked is a comment, not a column.** A task that cannot finish stays in `Doing` with a comment
  naming what blocks it. There is no fifth state.
- **A container's column is the operator's judgement**, not a computed roll-up: a feature sits in
  `Shaping` until it is cut, in `Backlog` while its tasks are planned, and in `Done` when the operator
  says the feature is finished.
- **`Idea` is for thinking, not for stashing.** A card that is really a commitment belongs in
  `Shaping` or `Backlog`; a card that is not worth doing is deleted.

## What a ticket carries

| Field | Content |
|---|---|
| Title | A plain sentence in the imperative: *"Rebuild the cross-seed chart"*. No plan prefix — the feature above it carries the context. |
| Description | The fixed block below, then what changed and the verification evidence. |
| Column | The state above. |
| Label | Exactly one kind (`feature`, `story`, `task`), plus `implementer` or `reviewer` for the role the session played. Who wrote the card is not a label: it is the account, below. |
| Parent | A feature or a story, never a task for a task that has one. Subtasks live in the description. |
| Priority | Unused. The reading order is the card's position in `Backlog`. |

**Who wrote a card** is the account that wrote it, and there are two: the operator's, and
`bot-opencode` (`opencode`, owned by the operator), whose token the agents act through. Vikunja stamps
`created_by` on every card and comment, so the name on the work is read from the record rather than
from a label anyone could set. The bot sees only the projects shared with it — today, `homelab`.

```text
repo: vyrtualsynthese/homelab
docs: https://github.com/vyrtualsynthese/homelab/blob/develop/docs/superpowers/plans/<plan>.md
branch: feature/<branch>
pr: #<number>
```

`docs:` points at the file the task came from. `branch:` and `pr:` say `none` when they do not exist
yet, so a reader can tell "no PR" from "not filled in". Where a change landed without a pull request,
`commit: <short sha>` replaces the `pr:` line.

Two habits that keep the fields honest:

- The description is the evidence, not the claim. "The check passes" is not evidence; the command and
  what it printed are.
- The description is edited in place as the work lands. A PR link added to a ticket in `Validation` is
  expected; a second ticket describing the same task is not.

## A handover, as a shape

What follows is **a shape to copy, not a transcript.** Every identifying value is a stand-in, and the
command and its output are invented to show where they go.

Column: `Validation`.

```text
repo: vyrtualsynthese/homelab
docs: https://github.com/vyrtualsynthese/homelab/blob/develop/docs/superpowers/plans/<plan>.md
branch: feature/<branch>
pr: #<number>

What changed
- <path>: <the change, in a line or two>. Nothing else in the chart was touched.

Verification
$ helm template test <chart path> -f <chart path>/values.yaml | grep -A1 'name: URL'
            - name: URL
              value: "http://<service name>:<port>"

Expected the port that chart's Service exposes, since the two must agree; the render above shows
that same port. helm template exits 0.
```

What the agent did, in order: moved the ticket `Doing` → `Validation` and stopped. It did not touch
`Done`, and it did not move the feature card above it.

## A refusal, as a shape

The operator opens the same ticket, runs the check from outside the chart, and refuses. The comment:

```text
Refused. The render shows the port you set, but the exporter container is not reading that URL in
the live pod: `kubectl -n <namespace> exec deploy/<app> -- env | grep -i URL` still prints the old
value, and the exporter's /health answers 502. Either the env var is set twice (check the sidecar
block in values.yaml, not only the template) or the change needs the pod restarted, which is a step
this PR does not do. Please close whichever it is and paste the exec output here.
```

They then move the ticket `Validation` → `Doing`. That is the whole refusal: one comment and one
move, both the operator's. If closing the gap needs more than one session, the operator also files a
task in `Backlog` for the part that does.

When the same agent — or another session — picks the ticket up again, it reads, in this order:

1. **The newest comment on the ticket first**, before the description and before the plan.
2. Then the description, to see what was already tried and what evidence was offered.
3. Then the plan task, for the scope.

## When a task cannot be finished

An agent that cannot finish leaves the ticket in `Doing` and writes a comment naming the blocker. The
comment is the blocked signal, and it is the only one: **there is no `Blocked` column**, and no
`blocked` label. What the comment carries: what was attempted, what stopped it, and who or what can
unblock it.

```text
Blocked: the chart renders, but the Deployment needs the Secret `sonarr`, and the read-only
ServiceAccount cannot read Secrets, so I cannot confirm the env var reaches the container. Needs the
operator to run `kubectl -n media-stack exec deploy/sonarr -- env | grep -i url` and paste it here.
```

Leaving it in `Doing` is not a soft `Done`, and it is not a reason to move it back to `Backlog`:
`Backlog` means nobody has started, and somebody has.

## Where the board is, and what it is not

- **Only `homelab` is boarded.** `config`, `ashuvidz-website`, `ashudev-website`,
  `selfhosted-helmcharts`, `ashuworld` and `ashuvidz-creator` exist and are empty. A session working
  in one of them files no ticket; it says so in its report instead, and the operator boards that
  repository when the work is worth tracking.
- **The board is state.** Whether a task is planned, being worked, awaiting review or done is answered
  there and nowhere else.
- **Plan checkboxes are the plan.** Agents keep ticking them as they go, because the plans' own skills
  read them. A tick means **planned and executed**, never **done**. When the two disagree, the board
  wins: fix the plan to match the board, never the other way round.
- **No second todo list.** No markdown checklist in a report, no "remaining work" section in a
  description that is consulted later. If it is work, it is a ticket.

## What this supersedes

- `docs/superpowers/specs/2026-11-15-agent-work-tracking-and-docs-site-design.md` §9.2–§9.4: the four
  states are replaced by six, and the container kinds did not exist.
- `docs/superpowers/specs/2026-11-16-board-workflow-design.md` is the design this page implements; when
  the two disagree, that document argues the case and this page states the rule.

The parent design's §8.8 rule 4 said a task an agent cannot close through the API is not a task an
agent should be given. **This page supersedes it.** An agent may create a ticket, claim one, and move
it to `Validation`. Only the operator moves it to `Done`, because an agent that grades its own work is
the failure mode the board exists to catch.
