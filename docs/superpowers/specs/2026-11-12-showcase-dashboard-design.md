# Showcase dashboard — design

2026-11-12. Audience: non-technical people on a screen share, presented by the
operator. Status: awaiting operator approval.

## 1. Intent

One Grafana screen that explains the homelab to someone who has never seen a
server: what the machines are, what they store, and what they are doing. It is a
presentation view, not a debug view — Homelab · Health stays the debug dashboard.

It must be honest. The lab currently has one dying SSD and three crash-looping
apps. The screen may show those as "worth a look", but it must not claim a health
it does not have, and it must not invent activity (Plex, downloads, photo counts)
that no exporter reports today. Exporters arrive when each app is refactored; this
dashboard gains panels then.

## 2. Goals, non-goals, anti-goals

**Goals**

1. A stranger understands the shape of the lab in ten seconds: internet comes in,
   six machines share the work, storage holds the data.
2. Readable at 1920×1080 in a screen share at 100 % zoom, dark theme.
3. Every number comes from Prometheus; no hardcoded figures.
4. Problems are visible but do not turn the screen red for things a guest does
   not care about (7 dead media exporters, 17 old Pending pods).

**Non-goals**

- Debugging. No namespace variables, no per-pod tables, no control-plane panels.
- TV/kiosk tuning beyond 1080p.
- Exporters for Plex, the arr apps or the downloader. Separate work.
- Dashboards as code. It lives in Grafana's database like the other four.

**Anti-goals**

- Fake or estimated numbers ("approximately 40 000 films").
- A green "everything is perfect" badge that stays green while a drive dies.
- Jargon on screen: no "pods", "PVC", "scrape", "namespace", "Prometheus".
- More than nine panels, or a panel without a query behind it.

## 3. Constraints

- Grafana 13.3.1, dark theme, folder `Homelab`, uid `homelab-showcase`.
- Datasource Prometheus, uid `PBFA97CFB590B2093`.
- 24-column grid; 1080p shows about 18 grid units of height after the header.
- Hosts: `nas` (.41), `nuc0` (.27), `streaming` (.46), `rbp2` (.48) carry a `node`
  label; `rbp0` (.22) and `rbp1` (.38) only `instance`, matched by job.
- The map is a Canvas panel: static boxes and text, no data binding, no
  connectors (text arrows instead, which survive Grafana upgrades).

## 4. Approaches

**A — Status board.** Three labelled rows of big numbers, no map. Safe, readable,
and says nothing about how the pieces connect.

**B — Poster.** Four giant numbers and a sparkline. Striking, but there is nothing
to narrate after thirty seconds and a fault ruins the poster.

**C — Map plus numbers.** *(chosen by the operator)* A drawn topology — internet,
router, switch, six machines with their role in plain words — then honest numbers
underneath. It explains the lab at a glance, which is exactly the audience. Cost:
the map is hand-built and static; adjusting it is manual work.

## 5. The screen

Thirteen panels, 24 grid units tall (about 720 px at default row height) plus the
Grafana header. Designed for 1920×1080 and 2560×1440: the map uses percentage
positions (`constraint.horizontal: scale`), the panels grow with the grid.

| Row | Panel | Size | What it says |
|---|---|---|---|
| 1 | Canvas map | 24×6 | Internet → router → switch → six machines, each with one plain-word role |
| 2 | Machines online | 6×5 | `6` — every host answering |
| 2 | Containers running | 6×5 | `107` — applications running right now |
| 2 | Data stored | 6×5 | `24 TiB` |
| 2 | Drives healthy · of 18 | 6×5 | `17` in amber while `nas /dev/sdf` is dying |
| 3 | Network traffic | 12×8 | Bytes in and out per second, two lines |
| 3 | Fullest disk on each machine | 12×8 | Each machine's most-used filesystem, sorted |
| 4 | GPU in use / GPU temperature | 4×5 each | The card that transcodes media |
| 4 | Four counts | 4×5 each | Drives, applications, services, machines |

The map's machine boxes: `nas` "Storage & backups", `streaming` "Media & GPU",
`nuc0` "Apps & databases", `rbp2` "Cluster control", `rbp0` / `rbp1` "DNS & VPN".
The canvas makes no health claim, so it cannot go stale: the Machines tile does.

### Status semantics

- Machines: green at 6/6, amber at 5/6, red below.
- Drives: green at 18/18, amber at 16–17/18, red below 16/18.
- Container count: neutral, no threshold — a count is not a health signal.
- Data stored: neutral, no threshold.
- No global "all systems go" tile. It would have to lie today, and it would be the
  first thing to rot.

### Palette

Dark background, one accent family. Teal `#2DD4BF` for healthy, amber `#F59E0B`
for "worth a look", red `#EF4444` for down, slate `#94A3B8` for labels. No other
colours. Thresholds only — no decorative colour.

### Queries

| Panel | Expression (sketch) |
|---|---|
| Machines | `count(up{job="kubernetes-service-endpoints",service="prometheus-node-exporter"}==1) + count(up{job=~"rbp0\|rbp1",instance=~".*:9100"}==1)` over 6 |
| Containers | `count(kube_pod_status_phase{phase="Running"}==1)` |
| Data stored | `sum(node_filesystem_size_bytes{fstype=~"ext4\|xfs",mountpoint!~"/var/lib/.*\|/run/.*\|/snap/.*"} - node_filesystem_avail_bytes{...})` |
| Drives | `17 - count(<DriveNeedsReplacement expression>)` |
| Network | `sum(rate(node_network_receive_bytes_total{device!~"lo\|veth.*\|br.*\|cni.*\|flannel.*\|tun.*\|wg.*"}[5m]))` and transmit |
| Storage by machine | `max by (instance) (100 * (1 - avail/size))` on data mounts, renamed to hostnames |
| GPU | `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_GPU_TEMP` from `streaming` |
| Footer | counts of hosts, `smartprom_smart_passed`, deployments, services |

`rbp0`/`rbp1` have no `node` label: the queries map `192.168.1.22` → `rbp0` and
`192.168.1.38` → `rbp1` with `label_replace`, as the Disks dashboard already does.

## 6. Verification

1. Every query run against Prometheus first; a query returning an error never
   reaches the dashboard.
2. Screenshot the rendered dashboard at 1920×1080 through headless Chromium and
   read it: nothing clipped, nothing overlapping, labels legible.
3. Check the blank case: a drive without a temperature shows blank, not 0.
4. Check the amber case: `Drives 17 / 18` must be amber today.
5. Re-open it as a second person would: no metric names visible.

## 7. Left to the builder

- Exact canvas coordinates; legibility at 1080p is the test.
- Whether the footer is a text panel or part of the canvas.
- Wording of the roles and section titles (English, plain words).

## 8. Risks

- **Canvas fragility.** The panel's JSON is large and its schema changes between
  Grafana majors. Mitigation: only rectangles and text, no connectors, no data
  binding; a broken map can be replaced by a text row without touching the rest.
- **Numbers age.** The design quotes today's counts; the panels read them live.
- **A red drive is on screen.** Deliberate: amber `17 / 18` is the truth and a
  good thing to explain.
