# Dashboards: the eleven Homelab screens

> **In one line.** The eleven Grafana dashboards in the `Homelab` folder, what each is for, and the API
> calls that change one.
>
> **Use it when** you need to know where to look, are adding or editing a panel, or are explaining why
> a dashboard is not in git · **Verified** 2026-11-21 · **Kind:** living page

## Dashboards

Folder **Homelab**, created through the HTTP API — except Health, Applications, Logs, Media and
Showcase, which still sit in the default `General` folder:

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
| Cloud | `cloud-services` | 6 | Internet-facing side: endpoints answering, DNS checks passing, days to the earliest certificate, per-probe success and latency, every check in one table |
| Homelab · Backups | `homelab-backups` | 6 | Backup health: stream age, repository size, snapshot count, etcd copy age, estimated monthly cost, failed jobs |
| Homelab · Traefik | `homelab-traefik` | 13 + rows | The router in front of everything: traffic by router and by entry point, status classes, p95/p99, 5xx share, bytes and open connections by entry point, failed and slow requests from the access log, the certificate store, config reloads, scrape health |

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

**Cloud** was added on 2026-11-20, when the public names got a prober
([`runbooks/cloud-monitoring.md`](../runbooks/cloud-monitoring.md)). Six panels answer "is the
internet-facing side healthy, and what needs attention?": three numbers on top — endpoints answering
of 3, DNS checks passing of 28, days to the earliest certificate — then `probe_success` and
`probe_duration_seconds` per endpoint over 24 h, and an `Every check` table listing one row per probe
with failures first. That table is the panel to read when a `CloudDnsCheckFailing` message names a
zone, a check and a server. The two denominators in the top-row titles are written into those titles
by hand, not computed by a query: adding a target means editing the title as well.

**Homelab · Traefik** was added on 2026-11-21, and it closed a hole rather than adding
convenience: none of the ten dashboards that existed that morning read a single `traefik_*` series, so
a router that stopped answering was visible only through the app behind it. The `/metrics` endpoint
that had been answering `404` since the 2023 install ([audit page](../audit/traefik-2026-11-21.md),
board card 531) went unnoticed for the same reason.

Four rows: traffic per router, traffic per entry point and by status class; latency and errors; bytes
and open connections; health. A fifth, *Request record*, was added on 2026-11-22 when Traefik got an
access log: *Failed requests (4xx/5xx)* and *Requests slower than 1 s, by router* read Loki rather
than Prometheus. The URL is absent from those lines by design, so the panel names the router, not the
path — why, and what is dropped, is in
[observability-conventions.md](../observability-conventions.md#what-the-router-logs). `Requests per second by entry point` exists because
`metrics.prometheus.addEntryPointsLabels` was turned on on 2026-11-22
([traefik-release.md](../runbooks/traefik-release.md#the-37-configuration-audit-2026-11-22-revision-12)):
before that, `traefik_entrypoint_requests_total` did not exist. It separates plain HTTP (redirects)
from TLS, Minecraft and the scraper when a router panel says traffic stopped but not where. The latency row is new data, not a new view — the p95 and p99 (the response time under which
95 and 99 of every 100 requests fall) only exist because Prometheus stopped dropping
`traefik_.*_bucket` and the release widened the buckets that day, so their history starts on
2026-11-21 and the 1 s alert threshold was chosen the same evening, against the hours that existed.
`Certificates in the store` lists **every** certificate
Traefik holds, which includes the eighteen names no route serves any more: they renew like the rest
until card 543 removes them, so a short list is not the healthy state here. The three counters
on the right watch the parts that fail silently: a config that stops reloading keeps serving the last
good one, so the age of the last successful load is the only early sign.

All eleven refresh on a one-minute cadence or faster — Traefik every 30 s, the rest every 1 m — and
hold a 6 h–24 h default range, except Homelab · Backups: it refreshes
every 30 minutes over 7 days, because a backup ages in hours and the panels read the verifier's
nightly log lines rather than a scrape.

The dashboards are not in git: they live in Grafana's database, and the API is how they change.
That database is no longer a single copy — the `grafana` claim is one row of the nightly
`nodes-nas` backup — but the row holds a database, not a reviewable diff, so a dashboard change is
still described here and applied through the API.

**One exception, and it is a copy, not a source.** Homelab · Traefik and its three rules are exported
to `cluster/helm/monitoring/grafana/export/traefik/` on 2026-11-21: `dashboard.json` is a ready-to-post API body and `rules.json` the three rules as the
API returns them. They are a snapshot to read in a pull request and to restore after a wipe. Nothing
applies them, no sidecar or configmap reads them, and editing them changes nothing in Grafana — the
database stays authoritative for every dashboard and every rule, including these.

**Homelab · Backups** was added on 2026-11-20 with the backup work. Two of its panels read
Prometheus (`kube_cronjob_status_last_successful_time`, `kube_job_status_failed`) and four read the
verifier's log lines in Loki: `{namespace="backup"} | logfmt`, unwrapping `bytes`, `snapshots` and
`age_h`. The stream name cannot come from a CronJob label: kube-state-metrics
does not expose custom labels on `kube_cronjob_status_last_successful_time`, so it is derived from the
job name with two `label_replace` calls (`backup-node-nas` → `nodes-nas`, `backup-nfs` → `nfs`). The
cost panel is arithmetic on the measured size — 34.1 GB at €7.10 per TB-month — not a bill, and it is
the number to watch: a stream that grows by an order of magnitude is a media directory added to a row
by mistake, which is what `budgetGiB` and the verifier already fail on.

## Changing things

- Dashboards: `POST /api/dashboards/db` with `{dashboard, folderUid: "homelab",
  overwrite: true}`. Read one with `GET /api/dashboards/uid/<uid>`.

Dashboards are changed through that API and nowhere else: how to authenticate is in
[observability-conventions.md](../observability-conventions.md), and the ban on hand edits and
`kubectl` is at the end of [alerts.md](alerts.md#changing-things).
