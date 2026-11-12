---
name: Observability and Alerting
description: Use when building or fixing Grafana dashboards, Prometheus or Loki queries, and alert rules — designing a view for a screen, choosing thresholds, writing rules that notify, or working out why a panel is empty, misleading or noisy.
---

# Observability and Alerting

A dashboard is read at a glance and an alert is read half asleep. Both are
judged by the decision they support, not by the amount of data they show.

This skill is scoped to the homelab repository. The cluster's own facts —
exporter quirks, the thresholds in use, the static scrape of the edge Pis,
where dashboards and rules live — are in `docs/observability-conventions.md`.
Read that alongside this file when the work is in this repo.

## Build a view from a question

Write down the question the screen answers before choosing a panel. If a
panel does not answer one, delete it.

**Panel budget for a screen people watch** (TV, stream, wall display):

- 6–9 panels on the first screen. Ten or more means it is a report, not a
  view; move the rest below a labelled row or into a second dashboard.
- At most 3 panels per row, at most 2 KPI rows.
- One idea per row, with a row header naming it ("Needs attention",
  "Nodes", "Storage").
- A grid of one cell per entity works up to about 6 entities (your hosts).
  Past that it is unreadable at distance: 18 drives belong in a table,
  never in 18 stat cells.

**Sizes that hold up at 1080p** — 24-column grid:

| Panel | Size | Notes |
|---|---|---|
| KPI stat | `w=4 h=5` | value size 40–46, one number, unit in the panel |
| Timeseries | `w=8 h=8` | legend at the bottom, two series per host at most |
| Table | `w=12 h=8` | only the columns a person reads |
| Log panel | `w=24 h=18+` | full width, no metadata envelope |

**Rendering rules:**

- Every panel carries a unit and, where a human acts on it, thresholds.
- The panel title is the question ("Disk used"), not the metric name.
- Legends: bottom or table; never a right-side legend that eats a third
  of the plot and shrinks it.
- Do not invent a composite "health score" unless the formula is on the
  panel. A number nobody can derive is a number nobody can trust.
- No flashing, no colour for its own sake. Colour is for thresholds only.

## Metrics: read the data before you chart it

1. List the metric families the exporter actually publishes. Do not assume
   a prefix, and do not trust a name because it looks canonical.
2. Sanity-check one value against physical reality before wiring it into a
   panel. A spinning disk at 132 °C, a four-year-old drive with 0 hours,
   a temperature of 0 — these are signs you picked the wrong series or the
   wrong unit field, not signs of a sick machine.
3. Exports often publish the vendor's **normalized** score (0–100 health)
   and the **raw** measurement under separate names. Chart the one that
   answers the question; verify which by comparing two drives.
4. A missing attribute must render **blank**, never `0`. Never append
   `or vector(0)` to a measurement, only to a counter where zero is true.

## Alerts: one condition, one message, one action

**Expression shape.** Filter in PromQL so the series exists only when the
condition is true (`up == 0`, `> 85`, `== 1`), then the rule threshold is
"above 0". The alert payload should let a person act without opening
Grafana: host, device, and the value.

**`for` durations** — long enough that a backup, an image pull or a
transcode does not page you:

| Condition | for |
|---|---|
| Host down | 5m |
| Service/exporter down | 5m |
| CPU or memory sustained | 15m |
| Disk or volume filling | 30m |
| Temperature | 10m |
| Predicted failure (wear, sectors) | 10m |

**De-duplicate at the query, not at the phone.** One filesystem alert per
mountpoint turns one full disk into a dozen messages. Reduce with
`max by (instance, mountpoint)`, and exclude pseudo filesystems
(`tmpfs`, `overlay`, `squashfs`) and remote mounts you do not own.

**Decide what happens when the data dies.** This is the failure everyone
forgets:

- `noDataState: OK` when your expression is designed to return no series
  while healthy.
- `execErrState: Alerting` on the rule that watches the monitoring stack
  itself, `OK` on everything else — otherwise a Prometheus restart fires
  every rule at once.
- Have one rule for the monitoring stack, so "blind" is itself an alert.

**Notification policy, not rule count, is what makes it spammy:**

- group by `alertname` and host, `group_wait 30s`, `group_interval 5m`,
  `repeat_interval 4h`.
- A silence suppresses notifications; it does not change the alert state.
  A firing-but-suppressed alert is the expected result, not a bug.
- Silencing a known-broken app for a fixed period is better than weakening
  the rule for everything.

**Thresholds worth starting from:** CPU 90 %, memory 90 % (use
`MemAvailable`, not `MemFree`), disk 85 %, host temperature 80 °C, drive
temperature 55 °C. Adjust with evidence, then leave them alone.

## Verify before it ships

Untested panels lie and untested rules stay silent.

- Execute every panel query through the datasource API before saving the
  dashboard. A query that errors or returns nothing is a panel that lies.
- Open each rule once and confirm it goes pending → firing with the host
  and value you expect, then let it resolve.
- Test the missing-attribute case explicitly: a drive without temperature
  must show blank, not 0.
- Confirm the message names what to do. "Something is wrong" is not an
  alert.

## Red flags

| Thought | Reality |
|---|---|
| "The panel looks right" | You looked at a render, not at the data. Run the query. |
| "More panels = more observability" | A viewer reads ~8 things; the rest is decoration. |
| "One alert per mountpoint is more precise" | It is twelve messages for one disk. |
| "I'll add the `for` later" | The first spike pages you tonight. |
| "noDataState is a detail" | It decides whether a dead Prometheus pages you or hides. |
| "Units are obvious" | They are obvious to you and invisible to the viewer. |
| "It fires, so the rule works" | Firing once with the right labels and a readable message is the test. |

## Quick reference

| Task | Start here |
|---|---|
| Empty panel | Is the series present at all? `count(metric)` first, then filter |
| Wrong-looking value | Check for a normalized vs raw pair; compare two devices |
| Noisy alert | Add `for`, reduce labels, then grouping — in that order |
| Alert fired but no Discord | Check the silence, then the notification policy route |
| Alert silent while everything is down | The rule watches the thing that died; add a stack-level rule |
| Dashboard unreadable on a TV | Cut to 9 panels, 3 per row, double the stat value size |
