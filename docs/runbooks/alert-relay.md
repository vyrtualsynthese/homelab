# Turning Grafana alerts into an investigation queue

Date: 2026-11-16

**Changes the cluster:** yes. Adds a Deployment, a Service and a ConfigMap in `monitoring`, and an
ArgoCD Application for them. One Secret is created by hand. No route to the internet, no ingress.

The relay listens on one endpoint and turns a Grafana alert into a card on the `alerts` board. It is
the only thing that translates between Grafana's JSON and the board's task shape; neither product can
be told to speak the other's format, and Vikunja has no inbound webhook.

Design: [`2026-11-16-alerts-to-board-design.md`](../superpowers/specs/2026-11-16-alerts-to-board-design.md).
The one decision taken there: the card **waits for a session** — no scheduled agent investigates and
closes it.

## What it does, exactly

| Grafana sends | The relay does |
|---|---|
| `firing`, severity not `critical` | logs `ignored: not critical`, writes nothing |
| `firing`, no open card for this incident | creates one card: the rule and where, with labels, annotations, the Grafana link and a `Next step:` |
| `firing`, a card is already open | comments `Fired again …`, at most once per `commentCooldownSeconds` |
| `resolved` | comments `Resolved …`, and **never moves or closes the card** |
| a body that is not JSON, or has no alerts | answers 400 and logs why — it is never dropped in silence |

One incident is Grafana's `groupKey`: the same rule on the same labels is the same card, however many
times it fires. The card is found again by that key, which the relay writes into the description.

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

Then **Alerting → Notification policies**, a child route with `severity = critical` → `vikunja-alerts`.
Leave the existing `discord-homelab` route alone: critical keeps going there too, because Discord is
still where you notice it, and the board is where it is worked.

Nothing else changes: the rules, their severities and the silences stay as
[`2026-11-12-alerting-design.md`](../superpowers/specs/2026-11-12-alerting-design.md) describes.

## Step 5 — prove it

Send a synthetic critical alert straight at the relay, then look at the `alerts` board:

```bash
curl -s -X POST localhost:8080/alert -H 'Content-Type: application/json' -d '{
  "status":"firing","groupKey":"test/diskfull/nas",
  "alerts":[{"status":"firing","fingerprint":"test1","startsAt":"2026-11-16T20:00:00Z",
    "generatorURL":"https://grafana.kub.local/alerting/rule/1",
    "labels":{"alertname":"TestDiskFull","severity":"critical","instance":"nas"},
    "annotations":{"summary":"synthetic test"}}]}'
```

Expected: `firing: opened #<id>`, and one card in `Backlog`. Send the same body again → `commented on
#<id>`, and still one card. Send it with `"status":"resolved"` → a comment saying the card stays open.

Then delete the test card, and repeat once through Grafana itself with a rule you know fires, to prove
the contact point and the policy route as well as the relay.

## Failure modes

| Symptom | Means | Do |
|---|---|---|
| Pod `CreateContainerConfigError` | The Secret is missing or the key is not `TOKEN` | Step 2 |
| Logs `failed: 401` | The bot's token is wrong or expired | Re-mint it, restart the Deployment |
| Logs `failed: 404` | `vikunja.project` does not match the `alerts` project id | Check the project, update `values.yaml` |
| Grafana reports a failed delivery | The relay is down or the Service name is wrong | `kubectl -n monitoring logs deploy/alert-relay` |
| Cards stop appearing, everything else looks fine | The relay is the single point of failure, and this is what that looks like | Accepted in the design; check the pod |

## What it deliberately does not do

- It does not close a card. Only an investigation does, in a session.
- It does not investigate. The card carries the payload and a `Next step:`; the session brings the
  tools.
- It does not mute anything. Silences, with an expiry date, are how noise is handled.
