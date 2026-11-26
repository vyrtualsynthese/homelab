# Alert relay: the card's column decides — design

- **Date:** 2026-11-25
- **Status:** awaiting operator approval.
- **Relates to:** `2026-11-16-alerts-to-board-design.md` (the relay), `2026-11-25-drive-alert-lifecycle-design.md`
  (which moved the severity filter into Grafana's routing tree).

## 1. Intent

Today the relay finds any card for the incident that is not done, and writes a comment on it every
time the alert repeats or resolves. That is noise in two places: a card someone is actively
investigating collects comments, and a card that resolved keeps a written trail that reads like work
happened.

The relay should be a gate, not a diary:

- open a card when a firing is **new work**;
- write nothing when the card is already in someone's hands, or waiting on the board untouched;
- never write anything for a resolve, and never move a card.

Resolution is not validation. A card reaches Validation because an investigation wrote a cause, not
because the alert went quiet.

## 2. Decisions (operator, 2026-11-25)

1. An existing card **not in Done** receives no write at all: no new card, no comment.
2. An alert that fires while the same alert's card is in **Validation** opens a new card.
3. An alert that fires while the card is in **Doing** or **Backlog** does nothing.
4. A **resolved** alert never writes and never moves a card.
5. Lookup reads the card's column in the same call as the card: v2 project task list, filtered on
   the key, newest first, `expand=buckets`.
6. A new card is created **in Backlog explicitly**, not in whatever column Vikunja treats as default.

## 3. The gate

The relay keeps one card per incident key. When a delivery arrives it looks up the **newest** card
carrying that key — newest because one key recurs over months, and the current incident is the latest
card — and decides from that card's column:

| Newest card for the key | `firing` | `resolved` |
|---|---|---|
| no card | open a card in Backlog | nothing |
| Done (`done=true`) | open a card in Backlog | nothing |
| Backlog | nothing | nothing |
| Doing | nothing | nothing |
| Validation | open a card in Backlog | nothing |
| any other column (Idea, Shaping) | open a card in Backlog | nothing |

The last row is **my call**: the `alerts` board never files cards in Idea or Shaping, so a card found
there is not an active investigation and must not swallow a firing. Treating it like Validation keeps
a firing from being lost.

Every delivery still leaves one log line naming the decision — `firing: DriveWorn on nas: opened #N`,
`firing: DriveWorn on nas: ignored, #N in Doing`, `resolved: DriveWorn on nas: ignored, #N in Backlog`
— because a relay that carries nothing must be visible in its own log.

## 4. What changes

| Part | Change |
|---|---|
| `cluster/helm/monitoring/alert-relay/files/relay.py` | lookup moves to v2 with `expand=buckets`; the column picks the action; comment, cooldown and `last_comment_epoch` code is deleted; create moves to v2 with an explicit bucket |
| `cluster/helm/monitoring/alert-relay/values.yaml` | `vikunja.backlogBucket: 115`; `commentCooldownSeconds` removed |
| `cluster/helm/monitoring/alert-relay/templates/deployment.yaml` | passes the backlog bucket; drops the cooldown variable |
| `hack/tests/test_alert_relay.py` | one test per row of the table; comment/cooldown tests removed |
| `docs/runbooks/alert-relay.md` | the behaviour table, the log examples, and the rule that a resolve never moves a card |
| `docs/reference/alerts.md` | the paragraph describing the relay's comments |

The chart's README gets the same one-line summary, because it is the first page a reader of the chart
opens. The existing design specs are left as history; the runbook is the page a person reads before
changing the relay.

## 5. Verification

1. Unit tests cover every row: no card, Done, Backlog, Doing, Validation, an unknown column, and both
   statuses. A resolve asserts **zero** board writes.
2. Against the live board: a synthetic firing opens a card in Backlog; the same firing again writes
   nothing; move the card to Doing — firing writes nothing; move it to Validation — firing opens a
   second card; mark it Done — firing opens a third; a resolve writes nothing in each state. Delete
   the test cards afterwards.
3. The relay's log names each decision, and the board is read back to prove no comment was written.
4. `hack/check-charts.sh` and `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
   pass.

## 6. Non-goals

- A scheduled investigator, or any automatic move to Validation or Done.
- Keeping a comment history of repeats and resolves; the card's own `Started:` and the relay log carry
  that.
- Auto-closing cards, or treating a resolve as a close.
- Changing the routing tree or which alerts reach the relay.

## 7. Approaches

**A. Targeted lookup (chosen).** One v2 call filtered on the key, newest first, with `expand=buckets`,
returns the card and its column. One call per delivery, as today, and Done cards are included so a
closed incident can open a fresh card. Trade-off: the filter language and the expand field must keep
working; both answer correctly on this instance.

**B. Read the whole board.** One call returns every column with every card. No filter, no expand.
Trade-off: the response grows with every Done card forever, and the relay parses every description.
Fine at forty cards, wasteful at four thousand.

A was chosen on 2026-11-25.
