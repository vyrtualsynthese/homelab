# alert-relay

One endpoint that turns a Grafana alert into a card on the `alerts` board, and
nothing else. It is the only translation layer in the alert path: Grafana sends
its own JSON, the board wants a task, and neither speaks the other's shape.

- `files/relay.py` — the whole service. Pure helpers at the top, tested in
  `hack/tests/test_alert_relay.py`; the board calls and the HTTP handler below.
- The code ships in a ConfigMap, so there is no image to build.
- One card per **incident** (Grafana's `groupKey`). A repeat is a comment, capped
  at one per incident per `commentCooldownSeconds`.
- A resolve is a comment and never a close: the card waits for an investigation.
- Only `severity=critical` reaches the board. Warnings stay on Discord.

Procedure: [`docs/runbooks/alert-relay.md`](../../../../docs/runbooks/alert-relay.md).
