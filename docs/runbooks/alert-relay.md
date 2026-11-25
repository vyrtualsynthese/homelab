# Turning Grafana alerts into an investigation queue

> **In one line.** A small in-cluster relay turns whatever Grafana routes to it into a card on the
> `alerts` board, comments on repeats and resolves, and never closes one.
>
> **Use it when** a Grafana alert should reach the board, a card never appeared, or you are changing
> what the relay does · **Verified** 2026-11-25 · **Kind:** how-to
>
> **Sources** the live contact point and routing tree read from Grafana's API, and
> `cluster/helm/monitoring/alert-relay/`

Date: 2026-11-16

**Changes the cluster:** yes. Adds a Deployment, a Service and a ConfigMap in `monitoring`, and an
ArgoCD Application for them. One Secret is created by hand. No route to the internet, no ingress.

The relay listens on one endpoint and turns a Grafana alert into a card on the `alerts` board. It is
the only thing that translates between Grafana's JSON and the board's task shape; neither product can
be told to speak the other's format, and Vikunja has no inbound webhook.

Design: [`2026-11-16-alerts-to-board-design.md`](../superpowers/specs/2026-11-16-alerts-to-board-design.md),
revised on 2026-11-25 by
[`2026-11-25-drive-alert-lifecycle-design.md`](../superpowers/specs/2026-11-25-drive-alert-lifecycle-design.md).
The one decision taken there: the card **waits for a session** — no scheduled agent investigates and
closes it.

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
| `firing`, no open card for this incident | creates one card: the rule and where, with labels, annotations, the Grafana link and a `Next step:`. The card is labelled `task` and `alert` as it is created, because an unlabelled card fails the board's own `--verify` |
| `firing`, a card is already open | comments `Fired again …`, at most once per `commentCooldownSeconds` |
| `resolved`, a card is open | comments `Resolved …`, and **never moves or closes the card** |
| `resolved`, no card at all | writes nothing: a test notification, or a rule deleted mid-flight, is not a new incident |
| a body that is not JSON, or has no alerts | answers 400 and logs why — it is never dropped in silence |

Every delivery leaves one log line naming the rule and the outcome —
`firing: DriveWorn on nas: opened #12` — so a relay that carries nothing is visible in its own log
rather than inferred from an empty board.

One incident is Grafana's `groupKey`: the same rule on the same labels is the same card, however many
times it fires. The card is found again by a 16-character SHA-256 prefix of that key, which is what
the description's `Alert key:` line carries — not the key itself. Vikunja stores a description as
HTML, so a group key's quotes come back as `&#34;` and a search on the raw key matches nothing; that
is how three test cards were opened by three identical deliveries on 2026-11-24. The raw group key is
kept on the next line as `Group:`, for a person reading the card.

## Step 1 — the bot

`bot-alertmanager` is a second bot, owned by the operator, separate from `bot-opencode` so an alert
card is never mistaken for an agent's work.

1. In the board: **Settings → Bots → create**, name it `alertmanager`.
2. Copy its token once.
3. Share **only the `alerts` project** with it, permission read+write. It has no business on the
   seven repository boards.

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
`firing: RelayProbe: commented on #727` — one card, one comment. Delete the rule, remove the route and
delete the card afterwards.

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
`commented on #<id>`, still one card. Send it with `"status":"resolved"` → a comment saying the card
stays open. Send a `resolved` whose `groupKey` was never seen → `resolved with no card`, and no card.

## Failure modes

| Symptom | Means | Do |
|---|---|---|
| Pod `CreateContainerConfigError` | The Secret is missing or the key is not `TOKEN` | Step 2 |
| Logs `failed: 401` | The bot's token is wrong or expired | Re-mint it, restart the Deployment |
| Logs `failed: 404` | `vikunja.project` does not match the `alerts` project id | Check the project, update `values.yaml` |
| Grafana reports a failed delivery | The relay is down or the Service name is wrong | `kubectl -n monitoring logs deploy/alert-relay` |
| Cards stop appearing, everything else looks fine | The relay is the single point of failure, and this is what that looks like | Accepted in the design; check the pod |
| **The relay log is empty, not even an `opened`** | Grafana never dispatched: the fault is in the routing tree, not the relay | Read `GET /api/alertmanager/grafana/api/v2/status` and check the rule's route |

## What it deliberately does not do

- It does not close a card. Only an investigation does, in a session.
- It does not investigate. The card carries the payload and a `Next step:`; the session brings the
  tools.
- It does not mute anything. Silences, with an expiry date, are how noise is handled.
