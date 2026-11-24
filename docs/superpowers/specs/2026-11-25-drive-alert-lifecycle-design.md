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

Life is under 10 %, using the three vendor shapes the exporter publishes — Crucial's
`percent_lifetime_remain`, NVMe's `100 - percentage_used`, Samsung SATA's `wear_leveling_count`:

```
(10 - max by (node, instance, drive, model_name, serial_number, user_capacity, type)
   (smartprom_percent_lifetime_remain)
 or 10 - (100 - max by (…) (smartprom_percentage_used))
 or 10 - max by (…) (smartprom_wear_leveling_count)) > 0
```

- Labels: `severity=warning`, `board=card`.
- Notification: **repeat turned off** (a year's interval), so the notice arrives when the drive
  crosses the line and again only if it recovers and recrosses.
- Message: "`nas /dev/sdf` has used up its rated life. Replace it when convenient — nothing is failing
  yet", with a link to the Disks dashboard and to the replacement procedure.
- Annotation explains why the drive is not paged: SMART passes, no sectors pending.

### `DriveFailing` — critical, `for: 10m`, group `homelab-core`

Any of: SMART self-test failed, the checker exited non-zero, pending sectors above zero, or
offline-uncorrectable sectors above zero.

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
  resolve, never close. One log line per delivery — `firing: created #N`, `firing: commented on #N`,
  `resolved: commented on #N` — so a silent relay is visible next time.
- **Routing.** Add a third child route: matcher `board=card` → `vikunja-alerts`, no `continue`. The
  existing `severity=critical` → `vikunja-alerts` route stays. The worn rule therefore creates one card
  and one Discord message, and nothing after.
- **Why move the filter.** A rule that says `board=card` is readable on the routing screen. A Python
  `if` three hops away is not, and it cost nine days of a path that looked wired and carried nothing.

## The dashboard

Question the screen answers: *which drives need action, and which are quietly fine?*

| Row | Panels |
|---|---|
| **Needs attention** | `Replace soon` (worn, not failing) · `Failing now` — two stats, each opening the alert |
| **Fleet** | one table, sorted failing first: host, drive, model, capacity, age, life %, temperature, SMART, reallocated, pending, **verdict** |
| **Trends** | life left over time · temperature · reallocated and pending sectors |

Six panels, down from fourteen. The six KPI stats, the two bargauges and the duplicate trend panels go.

**Verdict** is one numeric score per drive, value-mapped to words in the panel, so the table can sort
on it: `3` failing (any hard signal), `2` replace (life under 10 %), `1` watch (life under 25 %), `0` OK.
The score is computed as `(failing * 3) or (life<10 * 2) or (life<25 * 1) or (0 * temperature)`, so every
drive appears once and the leftmost match wins.

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
