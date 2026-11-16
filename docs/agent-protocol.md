# The operator↔agent protocol

How a unit of work travels from a plan file to a ticket on the board, and back. It binds every agent
that works in these repositories: the short form is five lines in `~/.config/opencode/AGENTS.md`, which
every session reads; this page is the long form, with the transitions, the fields, and two specimens —
a handover and a refusal — made to be copied in shape, not in wording.

The board is Vikunja at <https://vikunja.kub.local>, reachable from the LAN only. It exists because a
plan file cannot answer "is this done, and did anyone check?". A ticked checkbox says an agent ran out
of steps; it does not say the result was accepted.

## The board in one paragraph

One project per GitHub repository, seven of them: `homelab`, `config`, `ashuvidz-website`,
`ashudev-website`, `selfhosted-helmcharts`, `ashuworld`, `ashuvidz-creator`. Each project has one kanban
view titled `Board` whose buckets — the board's word for a kanban column — are `Ideas`, `Backlog`, `Doing`, `Revision` and `Done`, in that order. `Done` is the view's **done bucket**: a task moved there is marked
done by Vikunja itself, and a task marked done elsewhere appears there. One saved filter, `Awaiting my
review`, lists every open task across all projects, oldest first; it is the operator's backstop, not a
column.

## The four states, and who may move a ticket

| State | What it means |
|---|---|
| `Ideas` | An unformed thought. Nothing has been sized, specified or promised. |
| `Backlog` | The task exists in a plan and nobody has started it. |
| `Doing` | A session is working on it, or is blocked on it. |
| `Revision` | The work is finished as far as the agent can tell, and the operator has not looked yet. |
| `Done` | The operator accepted the work. Nothing else. |

| From | To | Who may make the move | When |
|---|---|---|---|
| — | `Ideas` | operator or agent | Something worth doing someday, with nothing behind it yet. |
| `Ideas` | `Backlog` | operator | The thought is now a sized task, usually a plan step. |
| — | `Backlog` | operator or agent | The task exists in a plan and has not been started. |
| `Backlog` | `Doing` | agent | The session starts the task. Assign the ticket to your own account in the same move. |
| `Doing` | `Revision` | agent | The branch is pushed, the PR is open, the verification has been run and its output is in the description. |
| `Revision` | `Doing` | **operator only** | The operator refuses the work. The comment says why. |
| `Revision` | `Done` | **operator only** | The operator accepts the work. |

Nothing else moves a ticket. In particular an agent never moves a ticket to `Done`, never moves one out
of `Done`, never empties `Revision` on its own behalf, and never moves a ticket that it is
not assigned to. There is no transition from `Ready` straight to `Revision`: if you did
the work, the ticket passed through `Doing`, and the board should be able to show that it did.

## What a ticket carries

| Field | Content |
|---|---|
| Title | The plan task's title, unchanged. Not a summary of it. |
| Description | The repository, the plan path, the task number, what was done, the verification evidence (the command and its output, or the runbook step and its result), and the PR link once one exists. |
| Bucket | The state above. |
| Assignee | The account your token acts as. Vikunja assignees are users, and one MCP token acts as exactly one account, so this is the agent account and never the operator. |
| Label | `agent` for a ticket an agent made, `operator` for the operator's own. Plus the role the agent played — `implementer` or `reviewer` — because the assignee cannot carry it: one token, one account, whichever role that session played. Plus the workstream ID (`W2`) where the plan has one. |
| Priority | Copies the plan's ordering. It is not a second judgement, and an agent does not re-rank work. |

Two habits that keep the fields honest:

- The description is the evidence, not the claim. "The check passes" is not evidence; the command and
  what it printed are.
- The description is edited in place as the work lands. A PR link appended to a ticket in
  `Revision` is expected; a second ticket describing the same task is not.

## A handover, as a shape

What follows is **a shape to copy, not a transcript.** Every identifying value is a stand-in, and the
command and its output are invented to show where they go. A real ticket replaces each one with what
its own session did, and pastes the output its own command actually printed — which is the whole
point of the rule that the description is the evidence. No value below is quoted from a real ticket,
and nothing below is a claim about the state of the cluster.

The situation it shows: a one-line defect in a chart. The session finds the ticket in `Ready` in the
repository's project, moves it to `Doing`, assigns it to the account its token acts as, does the work,
and parks it like this.

Bucket: `Revision`.

Description, as written on the ticket:

```text
repo: <repository>
branch: <branch>
PR: <pull request URL>

What changed
- <path>: <the change, in a line or two>. Nothing else in the chart was touched.

Verification
$ helm template test <chart path> -f <chart path>/values.yaml | grep -A1 'name: URL'
            - name: URL
              value: "http://<service name>:<port>"

Expected the port that chart's Service exposes, since the two must agree; the render above shows
that same port. helm template exits 0.
```

The `plan:` line is left out of this sketch on purpose: the field table above says the description
carries the plan path and the task number, and there is no plan task standing behind this shape to
name. A real ticket carries it, pointing at the plan file the task actually came from.

What the agent did, in order: moved the ticket `Doing` → `Revision`, added the label
`implementer`, and stopped. It also ticked that step's box in the plan, in the same commit as the chart
change, because the step was executed — that tick says "ran", not "accepted", and the ticket is what
says the work is finished. It did not touch `Done`.

What the operator does next, and the agent does not: read the description, run the command if the
evidence looks thin, then either accept — move to `Done`, which marks the task done — or refuse, which
is the next section.

## A refusal, as a shape

The operator opens the same ticket — still the illustrative one — runs the check from outside the
chart, and refuses. The comment they leave:

```text
Refused. The render shows the port you set, but the exporter container is not reading that URL in
the live pod: `kubectl -n <namespace> exec deploy/<app> -- env | grep -i URL` still prints the old
value, and the exporter's /health answers 502. Either the env var is set twice (check the sidecar
block in values.yaml, not only the template) or the change needs the pod restarted, which is a step
this PR does not do. Please close whichever it is and paste the exec output here.
```

They then move the ticket `Revision` → `Doing`. That is the whole refusal: one comment
and one move, both the operator's.

When the same agent — or another session — picks the ticket up again, it reads, in this order:

1. **The newest comment on the ticket first**, before the description and before the plan. The
   refusal is the reason the ticket moved, and a description that still claims success is exactly what
   the operator just contradicted.
2. Then the description, to see what was already tried and what evidence was already offered.
3. Then the plan task, for the scope.

It does not re-read the plan first and start again from the top. The refusal names the gap; the work is
to close that gap and paste the evidence the operator asked for, in a new comment on the ticket, and to
move it back to `Revision` only when that evidence exists.

## When a task cannot be finished

An agent that cannot finish leaves the ticket in `Doing` and writes a comment naming the blocker. The
comment is the blocked signal, and it is the only one: **there is no fifth state**, no `Blocked` column,
and no separate "parked" label. A ticket that sits in `Doing` with an unread blocker comment is the
correct representation of work that is stuck, and the comment is what makes the stuck state legible.

What the comment carries: what was attempted, what stopped it, and who or what can unblock it. Examples
of the three shapes that occur:

```text
Blocked: the chart renders, but the Deployment needs the Secret `sonarr`, and the read-only
ServiceAccount cannot read Secrets, so I cannot confirm the env var reaches the container. Needs the
operator to run `kubectl -n media-stack exec deploy/sonarr -- env | grep -i url` and paste it here.
```

```text
Blocked: the plan's Step 3 requires a VPN peer on rbp1, and I have no access to that host. Nothing
else in this task is held up; tasks 4 and 6 in this plan can proceed in parallel.
```

```text
Blocked, and I think the plan is wrong: the task says to point the route at `plex.kub.local`, but that
hostname serves the operator's browser UI and the kubelet cannot resolve it. Asking before changing
the plan rather than guessing.
```

Leaving it in `Doing` is not a soft `Done`, and it is not a reason to move it back to `Ready`: `Ready`
means nobody has started, and somebody has. The board's job is to show that work stopped here quietly,
if it stops.

## What this supersedes

The parent design, `docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md`, is wrong about
one thing, and this page corrects it.

- §8.8 rule 4 says: *"An agent claims a Vikunja task, works, and reports back with what changed and what
  it verified. A task that an agent cannot claim and close through the API is not a task an agent
  should be given."*
- W7's acceptance criterion says an agent *"can create and close a Vikunja task"*.

**This document supersedes both, for agents.** An agent may create a ticket, claim one, and move it to
`Revision`. Only the operator moves it to `Done`. The reason is the one the board exists
for: an agent that grades its own work is the failure mode this whole arrangement is meant to catch.
Closing through the API is cheap to build and produces a board that always agrees with whoever last
wrote to it — a second todo list with better colours. The board is worth its overhead only because the
last move is somebody else's.

The runtime follows the same rule: the MCP token is on the typed read+write preset, so nothing stops an
agent from writing to `Done` at the API level. The restriction is the protocol, which means an agent
that breaks it is visible in the board's history rather than blocked by a permission error — which is
the intended trade. The parent design still carries the old wording; until the operator amends it, this
page is the rule.

## Plan checkboxes are the plan, the board is the state

Plan files keep their `- [ ]` boxes, and agents keep ticking them as they go, because the plans' own
skills read them and a plan that is not ticked is not readable as a plan. But a tick means **planned and
executed**, never **done**: it records that the step ran, not that anyone accepted the result.

Whether a task is ready, being worked, awaiting review or done is answered by the board and nowhere
else. When the two disagree, **the board wins** — a ticked box with the ticket still in `Doing` means the
step ran and the work is not finished, and a ticket in `Done` with the box unticked means the operator
accepted the work and the plan is stale. Fix the plan to match the board, never the other way round.

The same rule bites outside plans: no markdown todo list, no "remaining work" section in a report, no
scratch checklist in a PR description that is consulted later. If it is work, it is a ticket.
