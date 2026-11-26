# alert-relay

One endpoint that turns a Grafana alert into a card on the `alerts` board, and
nothing else. It is the only translation layer in the alert path: Grafana sends
its own JSON, the board wants a task, and neither speaks the other's shape.

- `files/relay.py` — the whole service. Pure helpers at the top, tested in
  `hack/tests/test_alert_relay.py`; the board calls and the HTTP handler below.
- The code ships in a ConfigMap, so there is no image to build.
- One card per **incident** (Grafana's `groupKey`). A repeat and a resolve write
  nothing; the card is opened only when the firing is new work or the previous
  card is in Validation or Done.
- A resolve never closes and never moves a card: only an investigation that
  wrote a cause moves one to Validation.
- Grafana's routing tree decides what reaches the relay; the relay writes a card
  for whatever arrives.
- `files/lifecycle.py` — a `CronJob` every five minutes. It reads the alerts
  board, follows each alert card's `blocked` fix cards, moves the alert to
  `Doing` while any fix is open, and to `Validation` once every fix is in
  `Validation` or `Done`. It writes only the alerts board, never moves a card
  backwards, and never closes one.

Procedure: [`docs/runbooks/alert-relay.md`](../../../../docs/runbooks/alert-relay.md).
