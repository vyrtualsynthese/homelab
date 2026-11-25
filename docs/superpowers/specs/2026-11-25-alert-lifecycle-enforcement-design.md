# Alert lifecycle: who moves the card, and what moves it — design

- **Date:** 2026-11-25
- **Status:** awaiting operator approval.
- **Relates to:** `2026-11-16-alerts-to-board-design.md` (the board path),
  `2026-11-25-alert-relay-column-gate-design.md` (the relay gate this sits behind).

## 1. Intent

An alert card is investigated in `Backlog`, but nothing today records that the investigation
started, and nothing closes the loop when the fix cards it spawned are accepted. The card can sit in
`Backlog` forever while the real work happens elsewhere.

Two rules, enforced rather than remembered:

1. Once an alert has been analysed and its required fix cards exist, the alert card is in `Doing`.
2. When every fix card attached to it is in `Validation` or `Done`, the alert card is in `Validation`.

Resolution still decides nothing: a resolved alert writes nothing and moves nothing. The ladder is
driven by the investigation and its fix cards, not by Grafana going quiet.

## 2. Decisions (operator, 2026-11-25)

1. **Sessions own the alert card.** `bot-opencode` gains read+write on the `alerts` project; a session
   can relate fix cards to it, write the cause and move it.
2. **One job enforces the moves.** A five-minute `CronJob` reads the alert board, follows each alert
   card's blocked fix cards, and moves the alert card when the rules say so. It is the backstop for a
   session that forgets, not a replacement for the session.
3. **"Attached" means a Vikunja relation.** Each fix card is recorded as `blocked` on the alert card:
   the alert waits on the fix. Cross-project relations are supported; the caller needs write on the
   alert card and read on the fix card.
4. **Validation or Done closes a fix.** This is a deliberate exception to the general board rule that
   only `Done` releases a waiting card: a fix waiting for acceptance is finished work, and the alert
   should follow it.
5. **No fix cards means no wait.** A session that concludes nothing needs fixing writes the cause and
   moves the alert straight to `Validation`.
6. **Validation is terminal.** The job never moves a card backwards; a reopened fix does not pull the
   alert out of `Validation`. A new firing after that opens a new card, per the relay gate.
7. **The job uses `bot-alertmanager`.** That bot gains read-only on the repository boards; it keeps
   write on `alerts` and writes nothing anywhere else. No new bot, no new Secret.
8. **Only alert cards are touched.** The job acts on cards carrying the `alert` label, skips `frozen`,
   and leaves `Idea`, `Shaping` and `Validation` alone.

## 3. The ladder

The job reads the alert board's kanban view and, for every card that is not done, carries `alert`, is
not `frozen`, and sits in `Backlog` or `Doing`:

| Blocked fix cards | Current column | The job moves it to |
|---|---|---|
| none | any | nothing — the session owns that card |
| at least one, not all in Validation/Done | Backlog | Doing |
| at least one, not all in Validation/Done | Doing | nothing |
| at least one, all in Validation/Done | Backlog or Doing | Validation |
| any | Validation | nothing |

A fix counts as closed when `done` is true or its column is `Validation` or `Done`. A card with no
bucket data is a broken answer: the job logs it, moves nothing for that card, and exits non-zero.
Guessing would either close an alert with open work or duplicate it.

The move is the bucket endpoint, then a position so the card sits in its band: alert cards carry no
priority, so they land below the prioritised cards. The job logs one line per move —
`#757 CloudEndpointDown on https://ashuvidz.com: Backlog -> Doing (2 fixes open)` — and a summary
line. A run that moves nothing is silent.

## 4. The permissions

| Who | Today | After |
|---|---|---|
| `bot-opencode` | read on `alerts` (0) | read+write on `alerts` (1) |
| `bot-alertmanager` | read+write on `alerts` (1), nothing elsewhere | read-only on `homelab` (2), `config` (3), `ashudev-website` (5), `ashuvidz-creator` (8), `infra-main` (12) |

The operator makes the four sharing changes once, through Vikunja's API or the project's **Share**
dialog. The API calls are in the runbook; the read-only level is `permission: 0`.

Why both: `bot-opencode` creates the relation, which requires write on the alert card; the job must
read each fix card's board to tell `Validation` from `Doing`, and its token has no access there today.

## 5. The session procedure

1. Investigate the card in `Backlog`, with the tools already there.
2. Open each required fix card on its own board, as the board's rules already say.
3. Relate each fix to the alert card: `POST /tasks/{alert}/relations` with
   `{"relation_kind":"blocked","other_task_id":<fix>}`.
4. Write the cause on the alert card — what happened, what was fixed, what stops it recurring.
5. Move the alert to `Doing`; the job does it within five minutes if this is forgotten.
6. Nothing to fix: write the cause and move the alert to `Validation`.
7. When the last fix reaches `Validation` or `Done`, the job moves the alert to `Validation`. Nobody
   has to remember it.

A fix card that is deleted leaves no relation; if that empties the alert's list, a session closes the
alert by hand. A fix card that is `frozen` is still open, so the alert correctly waits.

## 6. The checker

One Python script, `lifecycle.py`, shipped in the same ConfigMap as the relay and run by a `CronJob`
in the same chart. It is a poller, not a daemon: no route, no Service, no probes.

- `GET /api/v2/projects/9/views/59/buckets/tasks` — the board, one call.
- `GET /api/v2/tasks/{fix}?expand=buckets` — one call per blocked fix card, for its column.
- `PUT /api/v2/projects/9/views/59/buckets/{bucket}/tasks {"task_id": N}` — the move.
- `PUT /api/v2/tasks/{N}/position {"position": p, "project_view_id": 59}` — the band.

`*/5 * * * *`, `concurrencyPolicy: Forbid`, `restartPolicy: OnFailure`, two retries, three kept
jobs. A failure exits non-zero so `kubectl get jobs` shows it; no alert rule watches the job yet.

## 7. What lives where

| Part | Home | How it lands |
|---|---|---|
| `lifecycle.py` and its tests | git, `cluster/helm/monitoring/alert-relay/` | pull request → `develop` → ArgoCD |
| The `CronJob`, values and ConfigMap entry | the same chart | the same pull request |
| The permissions | Vikunja, by hand | the runbook's four calls |
| The procedure and the ladder | `docs/runbooks/alert-relay.md`, `docs/reference/alerts.md` | the same pull request |
| The `alerts` carve-out in the global board rules | `config` repo, `docs/agent/board-and-worktrees.md` | **not this session** — project boundaries forbid it; the operator opens a `config` session |

## 8. Verification

1. **Unit tests:** every row of the ladder; `frozen`, non-`alert`, done, and `Validation` cards left
   alone; a fix with a missing bucket fails loudly; a move writes the bucket then the position.
2. **Chart:** `hack/check-charts.sh --only alert-relay` renders the `CronJob` with the schedule and
   both bucket ids; the Deployment is unchanged.
3. **Live, after the permissions and ArgoCD sync:** a synthetic firing creates a card in `Backlog`;
   a probe fix card on `homelab` is related to it; within five minutes the alert card is in `Doing`;
   moving the fix to `Validation` puts the alert in `Validation` on the next run. A card with no fix
   cards is left alone. Delete both probes afterwards.
4. **Negative:** a `frozen` alert card with fixes stays where it is; a fix in `Doing` keeps the alert
   in `Doing`.

## 9. Non-goals and open risks

- No investigator agent: the session still does the analysis.
- No backward move, and no close to `Done` — the operator accepts from `Validation`.
- No alert rule on the job's failures yet; the runbook says how to read them.
- A column rename is safe for the moves (bucket ids are configured) but not for the fix-check, which
  matches the titles `Validation` and `Done`; the six column names are the board contract.
- A job that cannot read a fix card fails loudly rather than assuming it is open, so a permissions
  mistake cannot close an alert early.

## 10. Approaches

**A. Sessions own the card; one job enforces (chosen).** `bot-opencode` gets write on `alerts`,
`bot-alertmanager` gets read-only on the repository boards, and the `CronJob` enforces both moves.
Simple to document and covers the no-fix case.

**B. Sessions never write `alerts`; the job does both moves.** Relations are recorded from the fix
side as `blocking`, which lands `blocked` on the alert. Least privilege, but a no-fix investigation
has no way to close its card, so it needs a marker the job can read — more machinery for a worse
outcome.

A was chosen on 2026-11-25.
