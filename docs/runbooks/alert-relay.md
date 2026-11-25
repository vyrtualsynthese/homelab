# Turning Grafana alerts into an investigation queue

> **In one line.** A small in-cluster relay turns whatever Grafana routes to it into a card on the
> `alerts` board, opens one only when the firing is new work, and a five-minute job moves that card
> with the fix work it spawned; neither moves a card on its own, and neither closes one.
>
> **Use it when** a Grafana alert should reach the board, a card never appeared, or you are changing
> what the relay does · **Verified** 2026-11-25 · **Kind:** how-to
>
> **Sources** the live contact point and routing tree read from Grafana's API, and
> `cluster/helm/monitoring/alert-relay/`

Date: 2026-11-16

**Changes the cluster:** yes. Adds a Deployment, a Service, a ConfigMap and a CronJob in `monitoring`,
and an ArgoCD Application for them. One Secret is created by hand. No route to the internet, no
ingress.

The relay listens on one endpoint and turns a Grafana alert into a card on the `alerts` board. It is
the only thing that translates between Grafana's JSON and the board's task shape; neither product can
be told to speak the other's format, and Vikunja has no inbound webhook.

Design: [`2026-11-16-alerts-to-board-design.md`](../superpowers/specs/2026-11-16-alerts-to-board-design.md),
revised on 2026-11-25 by the column gate
([`2026-11-25-alert-relay-column-gate-design.md`](../superpowers/specs/2026-11-25-alert-relay-column-gate-design.md))
and the card lifecycle
([`2026-11-25-alert-lifecycle-enforcement-design.md`](../superpowers/specs/2026-11-25-alert-lifecycle-enforcement-design.md)).
The one decision taken there: the card **waits for a session** — no scheduled agent investigates and
closes it. The job only moves a card along the path the session's own work defines.

## Who decides what reaches the board

**Grafana's routing tree, not the relay.** A rule that should produce a card is routed to
`vikunja-alerts`; the relay writes a card for whatever arrives. It used to drop anything below
`critical` in Python, three hops from the screen where a person looks for the wiring — and that
filter hid a path that carried no request at all between 16 and 25 November. The filter is gone, and
the tree is the one place to read.

Two routes point at the relay today: `severity = critical`, and `board = card` — the label a rule
carries when it wants one card and no page, which is how a worn drive asks to be replaced once.

## What it does, exactly

| Grafana sends | The relay does |
|---|---|
| `firing`, no card for this incident | opens one card in Backlog: the rule and where, labels, annotations, the Grafana link and a `Next step:`. The card is labelled `task` and `alert` as it is created, because an unlabelled card fails the board's own `--verify` |
| `firing`, the newest card is in Backlog or Doing | writes nothing, and logs `ignored, #N in <column>` |
| `firing`, the newest card is in Validation, Idea, Shaping or Done | opens a new card in Backlog |
| `resolved` | writes nothing, whatever card exists, and **never moves or closes one** |
| a body that is not JSON, or has no alerts | answers 400 and logs why — it is never dropped in silence |

The newest card for the key decides. One key recurs over months, so the latest card is the current
incident, not the oldest — and a card closed in Done does not stop the next one from opening a card.
A card in Idea or Shaping is not an active investigation, so it does not swallow a firing either.

A resolve is not validation. It writes nothing at all and never moves a card; only an investigation
that wrote a cause moves a card to Validation.

Every delivery leaves one log line naming the rule and the outcome —
`firing: DriveWorn on nas: opened #12`, `firing: DriveWorn on nas: ignored, #12 in Doing`,
`resolved: DriveWorn on nas: ignored, #12 in Backlog` — so a relay that carries nothing is visible in
its own log rather than inferred from an empty board.

One incident is Grafana's `groupKey`: the same rule on the same labels is the same card, however many
times it fires. The card is found again by a 16-character SHA-256 prefix of that key, which is what
the description's `Alert key:` line carries — not the key itself. Vikunja stores a description as
HTML, so a group key's quotes come back as `&#34;` and a search on the raw key matches nothing; that
is how three test cards were opened by three identical deliveries on 2026-11-24. The raw group key is
kept on the next line as `Group:`, for a person reading the card.

## After the investigation

A card in Backlog is a card nobody has looked at. The session that investigates it records its fixes
on the card, and a five-minute CronJob — `alert-relay-lifecycle`, same chart, same Secret, same
token — moves the card as the work moves.

**A fix is attached as a relation, not as a link in the text.** The fix card goes on its own
repository board, and the alert card waits on it:

```bash
# as bot-opencode, which holds read+write on `alerts` and the repository boards
curl -s -X POST "https://vikunja.kub.local/api/v2/tasks/<alert>/relations" \
  -H "Authorization: Bearer $VIKUNJA_MCP_TOKEN" -H 'Content-Type: application/json' \
  -d '{"relation_kind":"blocked","other_task_id":<fix>}'
```

`blocked` on the alert card means the alert is waiting for that fix. Vikunja writes the inverse
`blocking` on the fix card for you, and the two cards need not share a project.

| Blocked fix cards | Current column | The job moves it to |
|---|---|---|
| none | any | nothing — a card with no fix is the session's to place |
| at least one, not all closed | Backlog | Doing |
| at least one, not all closed | Doing | nothing |
| at least one, all closed | Backlog or Doing | Validation |
| any | Validation | nothing |

**Closed** means the fix is in Validation or Done, or is marked done. A fix in Validation is finished
work waiting for acceptance, and the alert follows it — a deliberate exception to the board's
"only Done releases" rule. Nothing else counts: a fix in Backlog or Doing keeps the alert in Doing.

The rest is guarded:

- a card without the `alert` label, a `frozen` card, a done card, and a card in Idea, Shaping or
  Validation is never touched;
- Validation is terminal — a reopened fix does not pull the card back to Doing;
- nothing moves a card to Done, and nothing closes one;
- a fix card whose column cannot be read fails that card loudly instead of guessing: the job prints
  `failed #<id>: …`, moves nothing for it, and exits non-zero.

Renaming a column is safe for the moves — the bucket ids are in `values.yaml` — but not for this
check: the job matches the titles `Backlog`, `Doing`, `Validation` and `Done`. Those four names are
part of the contract, and a rename is a code change.

Each move leaves one line, so the job's own log is the record:

```
#757 CloudEndpointDown on https://ashuvidz.com: Backlog -> Doing (2 fixes open)
lifecycle: 1 moved, 0 failed
```

A run that moves nothing prints nothing at all; a run that only fails prints the `failed` lines and
the summary.

**The session's part**, in order:

1. Investigate the card in Backlog.
2. Open each required fix card on its own board, as that board's rules say.
3. Relate each fix to the alert card with the call above.
4. Write the cause on the alert card: what happened, what was fixed, what stops it recurring.
5. Move the alert card to Doing — or let the job do it within five minutes.
6. Nothing to fix? Write the cause and move the card straight to Validation. The job leaves a card
   with no blocked fix alone, so this is the only path for it.
7. When the last fix reaches Validation or Done, the job moves the alert to Validation. Nobody has to
   remember it.

A fix card that is deleted leaves no relation behind; if that empties the list, the job leaves the
alert where it is and a session closes it by hand. A fix card that is `frozen` is still open, so the
alert correctly waits.

## Step 1 — the bot

`bot-alertmanager` is a second bot, owned by the operator, separate from `bot-opencode` so an alert
card is never mistaken for an agent's work.

1. In the board: **Settings → Bots → create**, name it `alertmanager`.
2. Copy its token once.
3. Share the `alerts` project with it, permission read+write.
4. Share the five repository boards with it, **read-only**: `homelab` (2), `config` (3),
   `ashudev-website` (5), `ashuvidz-creator` (8) and `infra-main` (12). It writes nothing there; it
   reads each fix card's column, and a board it cannot read fails the alert card that waits on it.

`bot-opencode` is the other half: it needs **read+write on `alerts`**, because a session relates the
fix cards to the alert card and moves it.

These six changes are the operator's, made once — one on `alerts`, five on the repository boards. They
need `VIKUNJA_API_TOKEN`, the operator's token, never the bot token an agent uses. `POST` creates the
share and `PUT` changes an existing one:

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

A `400` or `409` means the share already exists: run the same call against
`$B/projects/{project}/users/{username}` with `PUT` instead. Check it took with
`GET $B/projects/9` as `bot-opencode` (`max_permission: 1`) and `GET $B/tasks/<a fix card>` as
`bot-alertmanager` (200, not 403).

## Step 2 — the Secret

The chart reads `TOKEN` from the Secret `alert-relay` in `monitoring`.

```bash
kubectl -n monitoring create secret generic alert-relay \
  --from-literal=TOKEN='<bot-alertmanager token>'
```

A missing Secret shows up as `CreateContainerConfigError` on the pod — that is the intended loud
failure, not a silent one.

## Step 3 — deploy

The Application is `cluster/argocd/projects/monitoring/alert-relay.yaml`; commit it, then sync it in
the ArgoCD UI like every other Application here (auto-sync is enabled per Application, never in git).

```bash
kubectl -n monitoring rollout status deploy/alert-relay --timeout=120s
kubectl -n monitoring port-forward svc/alert-relay 8080:8080 &
curl -s localhost:8080/healthz   # → ok
kubectl -n monitoring get cronjob alert-relay-lifecycle   # → */5 * * * *
```

The job runs on its own schedule. To take one pass by hand — after a permission change, or to prove a
move without waiting — start it from the CronJob:

```bash
kubectl -n monitoring create job --from=cronjob/alert-relay-lifecycle lifecycle-probe
kubectl -n monitoring logs job/lifecycle-probe
kubectl -n monitoring delete job lifecycle-probe
```

## Step 4 — point Grafana at it

In Grafana: **Alerting → Contact points → New contact point**.

| Field | Value |
|---|---|
| Name | `vikunja-alerts` |
| Integration | Webhook |
| URL | `http://alert-relay.monitoring.svc.cluster.local:8080/alert` |

Then **Alerting → Notification policies**, a child route with `severity = critical` → `vikunja-alerts`, and
a second child with `board = card` → `vikunja-alerts`. Leave the existing `discord-homelab` route
alone: critical keeps going there too, because Discord is still where you notice it, and the board is
where it is worked.

Nothing else changes: the rules, their severities and the silences stay as
[`2026-11-12-alerting-design.md`](../superpowers/specs/2026-11-12-alerting-design.md) describes.

## Step 5 — prove it

The receiver test proves the whole path — Grafana, the routing, the contact point, the relay, the
board — without waiting for a rule. It is the check that had never passed:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -sk -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" -H "Content-Type: application/json" \
  -d '{"integration":{"type":"webhook","settings":{"url":"http://alert-relay.monitoring.svc.cluster.local:8080/alert"}}}' \
  "https://grafana.kub.local/apis/notifications.alerting.grafana.app/v0alpha1/namespaces/default/receivers/dmlrdW5qYS1hbGVydHM/test"
```

Expected: `{"status":"success",…}`, a `…: opened #N` line in `kubectl -n monitoring logs deploy/alert-relay`,
and one test card on the `alerts` board. Delete the card afterwards: it is named after the test alert,
not a real incident.

**The receiver test cannot prove that a repeat finds its card.** Every send through that endpoint
carries its own group key, so two sends open two cards — correct behaviour for two incidents that
Grafana considers different. That is how three cards (#719, #720, #721) appeared on 2026-11-24 while
the duplicate guard looked broken. To prove the guard, give a rule a short repeat and read the log:

```bash
# a temporary route, so the probe never reaches Discord:
#   {"receiver":"vikunja-alerts","object_matchers":[["alertname","=","RelayProbe"]],
#    "group_wait":"5s","group_interval":"60s","repeat_interval":"660s"}
# then a rule in homelab-core-fast, labels {"severity":"critical"}, expr vector(1), for 0s.
kubectl -n monitoring logs deploy/alert-relay -f | grep RelayProbe
```

Expected, measured 2026-11-25: `firing: RelayProbe: opened #727` at once, then eleven minutes later
`firing: RelayProbe: ignored, #727 in Backlog` — one card, and the repeat wrote nothing. Delete the
rule, remove the route and delete the card afterwards.

To prove the relay alone, from inside the cluster, send a body straight at it:

```bash
kubectl -n monitoring port-forward svc/alert-relay 8080:8080 &
curl -s -X POST localhost:8080/alert -H 'Content-Type: application/json' -d '{
  "status":"firing","groupKey":"test/diskfull/nas",
  "alerts":[{"status":"firing","fingerprint":"test1","startsAt":"2026-11-16T20:00:00Z",
    "generatorURL":"https://grafana.kub.local/alerting/rule/1",
    "labels":{"alertname":"TestDiskFull","severity":"critical","instance":"nas"},
    "annotations":{"summary":"synthetic test"}}]}'
```

Expected: `firing: TestDiskFull on nas: opened #<id>`, and one card. Send the same body again →
`ignored, #<id> in Backlog`, still one card and no comment. Send it with `"status":"resolved"` →
`resolved, ignored, #<id> in Backlog`, and the card stays where it is. Send a `resolved` whose
`groupKey` was never seen → `resolved, no card`, and no card.

**To prove the lifecycle**, with the shares in place and the probe card from above still in Backlog:
open a probe fix card on `homelab`, relate it, run the job once, and watch the probe card move.

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
B=https://vikunja.kub.local/api/v2
FIX=$(curl -sk -X POST "$B/projects/2/tasks?format=markdown" \
  -H "Authorization: Bearer $VIKUNJA_MCP_TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Lifecycle probe fix","description":"probe","bucket_id":13}' | jq -r .id)
curl -sk -X POST "$B/tasks/<alert>/relations" \
  -H "Authorization: Bearer $VIKUNJA_MCP_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"relation_kind\":\"blocked\",\"other_task_id\":$FIX}"
kubectl -n monitoring create job --from=cronjob/alert-relay-lifecycle lifecycle-probe
kubectl -n monitoring wait --for=condition=complete job/lifecycle-probe --timeout=60s
kubectl -n monitoring logs job/lifecycle-probe
# → #<alert> TestDiskFull on nas: Backlog -> Doing (1 fixes open)

# close the fix the way a session would, then run once more
curl -sk -X PUT "$B/projects/2/views/13/buckets/14/tasks" \
  -H "Authorization: Bearer $VIKUNJA_MCP_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"task_id\":$FIX}"
kubectl -n monitoring create job --from=cronjob/alert-relay-lifecycle lifecycle-probe-2
kubectl -n monitoring wait --for=condition=complete job/lifecycle-probe-2 --timeout=60s
kubectl -n monitoring logs job/lifecycle-probe-2
# → #<alert> TestDiskFull on nas: Doing -> Validation (0 fixes open)

# the card is in Validation and is NOT done: entering Validation is not an acceptance
curl -sk -H "Authorization: Bearer $VIKUNJA_MCP_TOKEN" "$B/tasks/<alert>?expand=buckets" \
  | jq '{done, buckets: [.buckets[].title]}'
# → {"done": false, "buckets": ["Validation"]}
```

**Measured 2026-11-25** on the live board, with the shares in place and the scheduled runs (no
hand-started job):

- card #772 with one `blocked` fix (#775, then in `homelab` Backlog) — the first run logged
  `#772 Lifecycle probe: open fix: Backlog -> Doing (1 fixes open)` and the card was in Doing;
- #775 moved to `homelab` Validation (bucket 14, `done: false`) — the next run logged
  `#772 …: Doing -> Validation (0 fixes open)`, and #772 read `done: false` in Validation;
- card #773, `frozen` with an open fix, and #774, with no relation, both stayed in Backlog through
  both runs.

The two calls a reviewer could not verify from outside held: `GET /tasks/{id}?expand=buckets` returns
the card's column, and a cross-project `blocked` relation is accepted with write on the alert card and
read on the fix. `POST /projects/{p}/tasks` ignores a `labels` array — a card is labelled afterwards
with `PUT /tasks/{id}/labels/bulk`, which is why the relay labels after creating.

The two negative cases need their own cards, because a card already in Validation is never moved:

- **No fix, no move.** A probe card with no `blocked` relation sits in Backlog through a run.
- **`frozen` holds it.** Attach a fix to a second probe card, leave that fix in Doing, and set
  `frozen` on the alert card. It stays in Backlog; the move the job would otherwise make is the one
  under test, so freezing a card that has already settled proves nothing.

Then trash the probe cards, the probe fix and the probe jobs.

## Failure modes

| Symptom | Means | Do |
|---|---|---|
| Pod `CreateContainerConfigError` | The Secret is missing or the key is not `TOKEN` | Step 2 |
| Logs `failed: 401` | The bot's token is wrong or expired | Re-mint it, restart the Deployment |
| Logs `failed: 404` | `vikunja.project` does not match the `alerts` project id | Check the project, update `values.yaml` |
| Grafana reports a failed delivery | The relay is down or the Service name is wrong | `kubectl -n monitoring logs deploy/alert-relay` |
| Cards stop appearing, everything else looks fine | The relay is the single point of failure, and this is what that looks like | Accepted in the design; check the pod |
| **The relay log is empty, not even an `opened`** | Grafana never dispatched: the fault is in the routing tree, not the relay | Read `GET /api/alertmanager/grafana/api/v2/status` and check the rule's route |
| Job `alert-relay-lifecycle` Failed | A card could not be read or moved. `failed #N: HTTP Error 403` is a missing share on that card's board; `failed #N: fix #M carries no bucket` means the fix read returned no column at all | `kubectl -n monitoring logs job/<job>`; the line `failed #<id>: …` names the card. Fix the share (or the read), then run a pass by hand |
| Job Failed with a traceback and no `failed #` line | The board itself could not be read: `GET …/buckets/tasks` answered non-2xx, so no card was even inspected | `kubectl -n monitoring logs job/<job>`; check the `alerts` share and the Service name |
| An alert stays in Doing with every fix closed | The job cannot read one of those fix cards' boards, so it cannot tell Validation from Doing | Check the share on that board; a 403 in the log is the tell |
| An alert stays in Backlog with fix cards attached | The relation was never written, or the job does not see the card in Doing/Backlog | `GET /api/v2/tasks/<alert>?expand=buckets` and read `related_tasks.blocked` |

## What it deliberately does not do

- It does not close a card. Only an investigation does, in a session.
- It does not investigate. The card carries the payload and a `Next step:`; the session brings the
  tools.
- It does not mute anything. Silences, with an expiry date, are how noise is handled.
- The job does not move a card backwards, does not close one, and does not touch a card that has no
  `alert` label, is `frozen`, or is done.
