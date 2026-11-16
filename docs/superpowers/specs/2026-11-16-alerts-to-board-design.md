# Design — Grafana alerts become an investigation queue

- **Date:** 2026-11-16
- **Status:** awaiting operator approval on one decision (§8).
- **Owner:** operator. Decisions marked **(operator, 2026-11-16)** were taken in conversation.
- **Relates to:** `2026-11-12-alerting-design.md` (the rules, the severity split, the Discord contact
  point), `2026-11-16-board-workflow-design.md` (the board and its six columns).

## 1. Intent

An alert today is a Discord message that scrolls away. The operator is the only person watching, so
what matters is not that the alert was delivered but that **somebody looked at it, and that the same
alert does not wake the same person forty times**.

This design turns a critical alert into a ticket on its own board: opened once per incident, updated
while the incident repeats, and closed only after an investigation says why it happened and what will
stop it happening again.

## 2. Decisions (operator, 2026-11-16)

1. All alerts come from **Grafana**, not from Alertmanager directly.
2. One **incident** ticket per alert, no matter how many times it fires.
3. A **specialised LLM investigates every alert ticket**, and either closes it as resolved or opens
   the tickets that fix it for good.
4. Only **critical** alerts reach the board. Warnings keep going to Discord only.
5. Repeating alerts **never** open a second ticket — they append to the existing one.
6. A resolved alert does **not** close the ticket: it is investigated, then closed.
7. Alerts live in a dedicated board.
8. Signals that are not worth an investigation — a rule that misfires, a false positive — are fixed
   by changing the rule or the filter, and that change is itself a ticket on the `alerts` board.
9. The relay is a **single point of failure**; the operator accepts it.
10. A separate bot, `bot-alertmanager`, writes to the board.
11. Everything runs **in the cluster**.

## 3. Goals

1. A critical alert appears on the board within seconds, with what fired, where, and the Grafana link.
2. Firing forty times produces one card and forty comments, never forty cards.
3. Closing a card requires an investigation summary, not a button press.
4. The alert set gets quieter over time: every noisy rule ends as a ticket that changes or removes it.

## 4. Non-goals

- Replacing Discord. Critical alerts still go there; the board is where they are worked.
- Auto-remediation. The investigation may propose a fix; nothing runs a fix unattended.
- Ticket-per-warning. Warnings are not investigations.
- Paging, escalation policies, or a second human.

## 5. Anti-goals

- A board that fills with noise, so nobody reads it.
- A closing that nobody wrote, so "resolved" means "gave up".
- Two tickets for one incident, which makes the history lie.

## 6. Constraints

- Grafana's webhook contact point sends Grafana's own JSON; Vikunja creates a task from `{"title":…}`.
  Nothing translates between them, so a relay is required. Vikunja has no inbound webhook, and its
  own webhooks are outbound and switched off on this instance.
- The board is LAN-only behind `kube-system-lan-only`; the relay runs in-cluster and calls
  `http://vikunja.vikunja.svc.cluster.local` directly, so no route is exposed.
- A Vikunja filter stores column ids, so the `alerts` board's ids must be added to the two saved
  filters, or the operator's queue silently skips it.
- Grafana's existing contact point `discord-homelab` stays; this adds a second one.

## 7. The shape

```text
Grafana rule (severity=critical)
        │  contact point: vikunja-alerts (webhook)
        ▼
relay  POST /alert   (namespace: monitoring, Deployment, Service, Secret)
        │  1. keep only severity=critical
        │  2. dedupe key = rule UID + sorted label set
        │  3. open, comment, or resolve-comment on the board
        ▼
Vikunja  project "alerts"  ──►  investigation ──►  Done, or fix tickets it opens
```

### 7.1 The board

One project, `alerts`, shaped like the seven repository boards — the same six columns, the same
labels, the same view filter — plus:

| Label | Colour | Meaning |
|---|---|---|
| `alert` | `#d73a4a` | This card was opened by the relay, not by a human |
| `noise` | `#6e7781` | The investigation concluded this rule should be changed or removed |

Its column ids join the two saved filters. Cards arrive in `Backlog`; the investigation moves them.

### 7.2 The relay

A `Deployment` in `monitoring`, one container, one endpoint `POST /alert`, no ingress. It holds:

- `VIKUNJA_TOKEN` — `bot-alertmanager`'s token, from a Secret.
- `VIKUNJA_BASE` — the in-cluster Service URL.
- `ALERTS_PROJECT` — the `alerts` project id.

Rules it enforces, in order:

| Situation | What it does |
|---|---|
| `status=firing`, severity not `critical` | logs and returns 202, no write |
| `status=firing`, no open card for the key | creates the card: title = rule name, description = the payload block |
| `status=firing`, an open card exists | appends a comment: fired again, timestamp, count |
| `status=resolved`, card open | appends a comment: resolved, timestamp, duration. **Does not move or close** |
| anything malformed | 400, logged with the body, never silently dropped |

The card's description carries the same block the protocol uses everywhere, plus the alert's own
facts:

```text
repo: vyrtualsynthese/homelab
docs: none
branch: none
pr: none

Alert: <rule name>
Severity: critical
Fingerprint: <key>
Started: <first fired>
Grafana: [open the rule](<url>)

Labels
- instance: <value>
- job: <value>

Annotations
<summary>
<description>
```

### 7.3 Noise control

- **Severity filter** at the relay, so a warning never creates a card even if the contact point is
  misconfigured.
- **Dedupe by key**, so repetition is a comment.
- **One card per incident**, not per host: the key is the rule *and* the label set Grafana groups by,
  so five dead exporters remain one incident if Grafana groups them.
- **A cap**: no more than one comment per key per 10 minutes, so a flapping rule cannot write a
  hundred comments in an hour.
- **Silences keep their expiry**, per the alerting design; the relay does not invent its own mute.

### 7.4 Credentials and attribution

`bot-alertmanager` is a second bot owned by the operator, shared on the `alerts` board with
read+write and **not** on the repository boards. Its token lives in a Secret created by hand; the
relay reads it from the environment. Cards and comments it writes are stamped with its name, so an
alert ticket is never mistaken for an agent's work.

## 8. The one open decision: what investigates

The ticket says an LLM must investigate and close. Nothing in the cluster can start an LLM today —
there is no agent runtime, and the models this operator uses are driven from OpenCode sessions on the
workstation. Two ways to get there:

**A. A job in the cluster runs an agent.** A `CronJob` every 15 minutes picks up `alerts` cards in
`Backlog`, gives each an investigation context (Prometheus, Loki, and the Kubernetes API, all
read-only), and writes back a conclusion: `Done` with a written cause, or a `Backlog` card per fix
with the cause in its description. It needs model credentials in a Secret, a prompt that is itself
versioned in git, and a definition of "concluded".

**B. The card waits for a session.** No credentials in the cluster, no autonomy: the card sits in
`Backlog`, and the next session — mine or yours — investigates it with the tools it already has. The
card carries a `Next step:` line the relay writes, so the investigation starts from the payload
rather than from a blank page.

A is what the request describes. B is what exists today and costs nothing. They are not exclusive:
A can add the scheduled conclusion later, on top of B, without changing the board.

**Costs of A, stated plainly:** a model key in a Secret, an agent that can move cards to `Done`
without a human (which contradicts the rule everywhere else on the board, so it must be written as
an exception limited to `alerts`), a prompt that can be wrong in new ways every week, and an
investigation that can conclude "no idea" while marking a card resolved. **Costs of B:** a critical
alert waits for a human to open a session, which is what happens today with Discord.

## 9. Failure, and what is accepted

- **Relay down:** no tickets, and nothing says so. Accepted as a single point of failure. Mitigation
  left on the table: a `Watchdog`-style rule that fires when the relay has written nothing for 24
  hours — which is itself an alert, and therefore also lost if the relay is the thing that is down.
- **Board down:** the relay logs and returns 5xx; Grafana records the failed delivery.
- **Duplicate tickets after a board outage:** the dedupe key lives in the board, so if the board was
  down when the card should have been found, a second card can appear. Accepted, and the investigation
  merges them.

## 10. Verification

- A synthetic critical alert through Grafana creates exactly one card, and a second identical alert
  creates one comment, not a second card.
- A warning creates nothing.
- A resolve appends a comment and leaves the card where it is.
- `hack/check-exposure.sh` still passes: the relay has no route.
- The seven repository boards are unchanged; the `alerts` ids appear in both saved filters.
