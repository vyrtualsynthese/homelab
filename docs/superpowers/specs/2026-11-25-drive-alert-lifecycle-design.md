# Drive alerts: worn, failing, and what reaches the board — design

Card #717 · branch `feature/drive-alert-lifecycle` · 2026-11-25

## Intent

A drive that has used up its rated life should say **replace me, when convenient** once, and then stay
quiet until it starts to fail. A drive that is failing should page every hour until it is dealt with.
Today one rule does both jobs, so a worn-but-healthy SSD pages forever and a mute hides it, and the
board path has never carried a single card.

## What is true today, measured 2026-11-25

- **One rule, two meanings.** `DriveNeedsReplacement` (`bg41fw2txhpfke`), critical, `for: 10m`, group
  `homelab-core`. It fires when SMART says the drive failed, when the checker exits non-zero, when
  life is under 10 %, **or** when pending sectors are above zero.
- **One drive is worn, not failing.** `nas /dev/sdf` (Crucial CT1000BX500SSD1) reports 0 % life left,
  522 TB written against a 360 TB rating, SMART **passed**, zero reallocated and zero pending sectors.
- **Its checker exit code is 32, and that is the whole difference.** `smartctl` exit bits are a
  bitmask: 32 means "the drive's error log holds records", not "the drive is failing" (8 is failing,
  16 is a prefail attribute under threshold, 64 is a self-test log with errors). Every other drive
  returns 0. So a bare `exit_code != 0` cannot tell a historic error-log entry from damage — and it
  is the only reason `/dev/sdf` is critical today, independently of its life.
- **A mute is the only thing silencing it.** Silence `0110b899` matches that drive and that rule and
  expires 2026-12-12. Under a silence the alert still fires; only delivery stops.
- **The board path works and has never been used.** A test notification sent from Grafana to the
  `vikunja-alerts` contact point answered `{"status":"success","duration":"21ms"}` and the relay logged
  `firing: ignored: not critical`. In the same seven days, the 36 annotations of the only other
  critical rule (`CloudDnsCheckFailing`) are all `Pending → Normal (NoData)` — it never reached
  `Alerting`. So no critical notification has ever been delivered to the relay, and the one firing
  critical is muted before delivery.
- **The relay drops warnings by design.** `is_critical()` refuses everything below critical, writes
  nothing and — until today — had never logged a line at all. The filter that hid this for nine days
  is inside the relay, where nobody looks; the routing tree, where a person does look, says nothing
  about it.

## Decisions

1. **Two rules, not one.** `DriveWorn` carries life; `DriveFailing` carries damage. `DriveNeedsReplacement`
   is deleted.
2. **The worn notice is sent once.** A per-rule repeat override on `DriveWorn`; the failing rule keeps
   the normal hourly repeat. `for:` still applies to both.
3. **Grafana decides what belongs on the board, not the relay.** The relay creates a card for every
   notification it receives; a routing entry (`board=card`) is what puts the worn notice there.
4. **The dashboard shows a verdict, not a wall of gauges.** Six panels, one table, a column that says
   *OK · watch · replace · failing* and sorts failing first.
5. **Rules and the dashboard are applied through Grafana's API** — they live in its database, not in
   git, per `AGENTS.md` §9. The relay code is in git and ships through a pull request. The change to
   the live rules is recorded in the docs and on the card, with the rule UIDs.

## The rules

Both keep the existing `label_replace` chain that maps `instance` to `host` (six nodes, and the edge
Pis' SMART exporter on port 9902), so the tables and messages stay readable.

### `DriveWorn` — warning, `for: 1h`, group `homelab-core`

Either signal below, and nothing above it:

- Life is under 10 %, using the three vendor shapes the exporter publishes — Crucial's
  `percent_lifetime_remain`, NVMe's `100 - percentage_used`, Samsung SATA's `wear_leveling_count`;
- the checker's exit code carries bit 32 — the drive's error log holds records. It means "look at
  this drive", not "this drive is dying": with `smart_passed` at 1 and every sector counter at zero,
  it is a history, not a symptom.

Labels: `severity=warning`, `board=card`. Notification: **repeat turned off**, so the notice arrives
when the drive crosses the line and again only if it recovers and recrosses. Message: "`nas /dev/sdf`
has used up its rated life. Replace it when convenient — nothing is failing yet", with a link to the
Disks dashboard and to the replacement procedure.

A drive that is both worn and failing raises both rules; the page is the one that matters, and the
worn notice on the same day carries no extra cost.

### `DriveFailing` — critical, `for: 10m`, group `homelab-core`

Any of: SMART self-test failed, pending sectors above zero, offline-uncorrectable sectors above zero,
a self-test log with errors (exit bit 64), or any exit bit below 32 (1 parse error, 2 device could not
be opened, 4 command failed, 8 disk failing, 16 prefail attribute under threshold) — expressed as
`exit_code % 32 > 0`. Bit 32 alone is **not** in this rule.

- Labels: `severity=critical`.
- Notification: the existing hourly critical route, unchanged.
- Message: "`nas /dev/sdX` is failing — move its data now", naming which signal tripped.

### Kept, not touched

`DiskReallocatingSectors` (warning, `for: 5m`) already watches reallocated sectors **growing** and stays
as the early "it has started to die" signal. `DiskTooHot` (19 firings in 30 days) and the flapping
`ScrapeTargetDown` are separate noise and out of scope.

## The board

- **Relay.** `is_critical()` goes. Every delivered notification creates or comments on a card, exactly
  as the code already does for criticals: open one card per incident, comment on repetition, comment on
  resolve, never close. One log line per delivery — `firing: DriveWorn on nas: opened #N`, `…: commented on #N`,
  `…: resolved on #N` — so a silent relay is visible next time.
- **Routing.** Add a third child route: matcher `board=card` → `vikunja-alerts`, no `continue`. The
  existing `severity=critical` → `vikunja-alerts` route stays. The worn rule therefore creates one card
  and one Discord message, and nothing after.
- **Why move the filter.** A rule that says `board=card` is readable on the routing screen. A Python
  `if` three hops away is not, and it cost nine days of a path that looked wired and carried nothing.

## The dashboard

Question the screen answers: *which drives need action, and which are quietly fine?*

| Row | Panels |
|---|---|
| **Needs attention** | `Drives monitored` · `Replace soon` (worn, not failing) · `Failing now` — three stats, the last two opening the alert |
| **Fleet** | one table, sorted failing first: host, drive, model, capacity, age, life %, temperature, SMART, reallocated, pending, **verdict** |
| **Trends** | life left over time · temperature · reallocated and pending sectors |

Seven panels, down from fourteen. `Drives monitored` stays because a drive that stops publishing
disappears from the table and from both rules: the count going down is the only trace. The six KPI
stats, the two bargauges and the duplicate trend panels go.

**Verdict** is one numeric score per drive, value-mapped to words in the panel, so the table can sort
on it: `3` failing (any hard signal), `2` replace (worn), `1` watch (life under 25 %), `0` OK. The score
is `(failing * 3) or (worn * 2) or (watch * 1) or (0 * smart_passed)`; the base is `smart_passed`
because it is the one series every one of the eighteen drives publishes, and the `or` keeps the
leftmost match, so the worst state wins. Comparison branches use `bool` and a `> 0` filter, or they
pass the life value through and a drive at 0 % scores 0.

## What lives where

| Part | Home | How it lands |
|---|---|---|
| `relay.py` and its tests | git, `cluster/helm/monitoring/alert-relay/` | pull request → `develop` → ArgoCD |
| The two rules, the routing entry, the silence removal | Grafana's database | Grafana API, recorded in the docs and on the card |
| The dashboard | Grafana's database | Grafana API, the panel list above in the docs |
| The pages | `docs/runbooks/alert-relay.md`, `docs/reference/alerts.md`, `docs/observability-conventions.md` | same pull request |

## Verification

1. **Relay unit tests:** a warning now creates a card; a repeat is a comment; a malformed body is still
   refused. Existing tests for dedupe and cooldown keep passing.
2. **End to end:** Grafana's receiver test returns 200 **and** a card appears on `alerts`, with the
   relay's `created` line in its log. This is the check that has never passed.
3. **The rules themselves:** both are evaluated for two cycles — `DriveWorn` on, `DriveFailing` off for
   `/dev/sdf`, and the worn rule sends exactly once over 24 h. Then the old rule and its silence are
   deleted.
4. **The dashboard:** every panel is read back from the API with a real query, and the numbers are
   compared with Prometheus directly. The verdict column is checked against `smartctl` on the drive it
   names.
5. **The one real case:** `/dev/sdf` produces one Discord message and one card, then silence.

## Non-goals

- Moving alert rules into git as provisioned files.
- Auto-closing cards, or a scheduled investigator.
- Fixing `DiskTooHot`, `ScrapeTargetDown` or any other rule's noise.
- Replacing the drive: card #468 stays frozen until the operator acts.

## Open risk

The per-rule repeat override (`notification_settings.repeat_interval`) is **unverified on Grafana
13.3.1**. If it is ignored, the fallback is a dedicated route matched on `drive_state=worn` with a
year-long repeat — one API call apart. The plan proves whichever mechanism on a throwaway rule before
the real one is touched.
