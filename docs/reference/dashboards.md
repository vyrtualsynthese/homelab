# Dashboards: the eight Homelab screens

> **In one line.** The eight Grafana dashboards in the `Homelab` folder, what each is for, and the API
> calls that change one.
>
> **Use it when** you need to know where to look, are adding or editing a panel, or are explaining why
> a dashboard is not backed up · **Verified** 2026-11-19 · **Kind:** living page

## Dashboards

Folder **Homelab**, created through the HTTP API:

| Dashboard | uid | Panels | Purpose |
|---|---|---|---|
| Homelab · Health | `homelab-health` | 12 | First look: four counts, pods and targets needing attention, crash loops, restarts, firing alerts, errors, disk |
| Homelab · Applications | `homelab-applications` | 9 | Debug one namespace: replicas, pods, CPU, memory, restarts, claims, dead exporters, recent errors |
| Homelab · Platform | `homelab-platform` | 15 | Nodes, control plane and Prometheus when the problem is not an app |
| Homelab · Network | `homelab-network` | 8 | Switches: reachability, CPU temperature, SFP faults, throughput, port errors, optics and linked ports |
| Homelab · Logs | `homelab-logs` | 8 + rows | Volume, errors and warnings by container, log volume by node, ingest rate, compression and rejected writes, live stream |
| Homelab · Disks | `homelab-disks` | 11 | SMART per drive: status, life, temperature, hours, sectors, wear |
| Homelab · Showcase | `homelab-showcase` | 13 | Presentation screen: drawn topology, four big numbers, network, storage, GPU |
| Homelab · Media | `homelab-media` | 9 + rows | Media stack: app health issues, queues, missing items, indexers, release intake, qBittorrent transfer, Plex sessions and library |

The old `homelab-investigate` was replaced by `homelab-platform` on 2026-11-12: its workload
row duplicated the new Applications dashboard, and its node/control-plane/Prometheus panels are
what Platform keeps. Tables use `labelsToFields` + `merge` + `organize`; a plain multi-query table
shows only the first frame and leaks Kubernetes metadata columns. Legend names come from the
`node` label, with `instance` mapped for `rbp0`/`rbp1` (they have no `node`).

Media panels name their series from `app_kubernetes_io_instance`, not `pod`, because a pod name
changes on every restart. `Missing per app` is a table with an `app` column: as a bargauge its
bars were named from the raw label set (`{app_kubernetes_io_instance="sonarr"}`), because an
instant query returns one frame per series and the datasource sets `displayNameFromDS` from the
labels, which overrides `legendFormat`. `Playback bandwidth` is blank while nobody streams:
`plex_session_bandwidth_kbps` is only published during playback, and a zero would be a lie.

**Homelab · Network** was added on 2026-11-18 because mktxp had been publishing
838 series for two switches that no dashboard read. It is a separate screen
rather than a row on Platform: Platform already carries 15 panels, and the
switch views answer a different question (what is plugged into what, and is the
optic healthy). Four counters on top, then throughput, the ports that moved
errors, the SFP inventory with its two fault bits, and the ports that have a
link. It names the switches from `routerboard_address`, because the RouterOS
identity strings are `CR326` and the default `MikroTik`. An empty "errors and
drops" table is the healthy state, not a broken panel.

**Homelab · Logs** gained a "Storage and writes" row the same day: ingest bytes
per day, the chunk compression ratio, and rejected writes in 24 h. They exist
because Loki and Grafana only became scrape targets then, and the numbers answer
whether the 20 GiB claim will hold the 30-day retention (it will, with room:
[`metrics.md`](metrics.md#storage-and-retention)).

All eight refresh every 1 m and hold a 6 h–24 h default range. They are not
backed up: Grafana's 5 GiB claim is the only copy. A `docker save` of the
dashboard JSON is not a backup either — the SQLite file is what would be lost.

## Changing things

- Dashboards: `POST /api/dashboards/db` with `{dashboard, folderUid: "homelab",
  overwrite: true}`. Read one with `GET /api/dashboards/uid/<uid>`.

Dashboards are changed through that API and nowhere else: how to authenticate is in
[observability-conventions.md](../observability-conventions.md), and the ban on hand edits and
`kubectl` is at the end of [alerts.md](alerts.md#changing-things).
