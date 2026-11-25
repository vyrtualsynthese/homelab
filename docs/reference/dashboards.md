# Dashboards: the twelve Homelab screens

> **In one line.** The twelve Grafana dashboards in the `Homelab` folder, what each is for, and the API
> calls that change one.
>
> **Use it when** you need to know where to look, are adding or editing a panel, or are explaining why
> a dashboard is not in git · **Verified** 2026-11-25 · **Kind:** reference
>
> **Sources** `cluster/helm/monitoring/grafana/export/traefik/` and `cluster/helm/monitoring/grafana/export/security/`,
> the two snapshots in git and nothing that applies them; the live Grafana instance and its Prometheus
> and Loki datasources, read through the HTTP API on 2026-11-21 and 2026-11-25.

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
| Homelab · Backups | `homelab-backups` | 6 | Backup health: stream age, repository size, snapshot count, each server's etcd upload age, estimated monthly cost, failed jobs |
| Homelab · Traefik | `homelab-traefik` | 13 + rows | The router in front of everything: traffic by router and by entry point, status classes, p95/p99, 5xx share, bytes and open connections by entry point, failed and slow requests from the access log, the certificate store, config reloads, scrape health |
| Homelab · Security | `homelab-security` | 9 | Danger state, bans, volume, rejected share, top addresses, sign-in outcomes, host blocks, posture |

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
than Prometheus. The path and the query string are absent from those lines by design, so the panel
names the router, not the URL; the hostname and the backend's pod address are still in a line. Why,
and what each panel expects, is in
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

All twelve refresh on a one-minute cadence or faster — Traefik every 30 s, the rest every 1 m — and
hold a 6 h–24 h default range, except Homelab · Backups: it refreshes
every 30 minutes over 7 days, because a backup ages in hours and the panels read the verifier's
nightly log lines rather than a scrape.

The dashboards are not in git: they live in Grafana's database, and the API is how they change.
That database is no longer a single copy — the `grafana` claim is one row of the nightly
`nodes-nas` backup — but the row holds a database, not a reviewable diff, so a dashboard change is
still described here and applied through the API.

**Two exceptions, and both are copies, not sources.** Homelab · Traefik and its three rules are
exported to `cluster/helm/monitoring/grafana/export/traefik/` on 2026-11-21, and Homelab · Security to
`cluster/helm/monitoring/grafana/export/security/` on 2026-11-25: each `dashboard.json` is a
ready-to-post API body and each `rules.json` the rules as the API returns them. They are a snapshot to
read in a pull request and to restore after a wipe — the security one's `rules.json` arrives with
Task 14's seven rules, so until then only the dashboard is there. Nothing applies either copy, no
sidecar or configmap reads them, and editing them changes nothing in Grafana — the database stays
authoritative for every dashboard and every rule, including these.

**Homelab · Backups** was added on 2026-11-20 with the backup work. Two of its panels read
Prometheus (`kube_cronjob_status_last_successful_time`, `kube_job_status_failed`) and four read the
verifier's log lines in Loki: `{namespace="backup"} | logfmt`, unwrapping `bytes`, `snapshots` and
`age_h`. The stream name cannot come from a CronJob label: kube-state-metrics
does not expose custom labels on `kube_cronjob_status_last_successful_time`, so it is derived from the
job name with two `label_replace` calls (`backup-node-nas` → `nodes-nas`, `backup-nfs` → `nfs`). The
cost panel is arithmetic on the measured size — 34.1 GB at €7.10 per TB-month — not a bill, and it is
the number to watch: a stream that grows by an order of magnitude is a media directory added to a row
by mistake, which is what `budgetGiB` and the verifier already fail on.

## Homelab · Security

**Homelab · Security** was added on 2026-11-25 with the detection work, and it is the screen to open
when the question is "is anything happening?" rather than "is anything broken?". Nine panels: the
**danger state** and **bans active** in the top row, the **posture** counts beside them; then requests
per host over 24 h, the rejected share over time, the top refused addresses, sign-in outcomes by
result, requests by host and status over 15 minutes, and the host firewall's `UFW BLOCK` lines. The
danger state is one expression, not six, because a Grafana target reads one datasource — the rejected
share therefore comes from Traefik's own counters rather than the log, which is the same number in one
query.

```promql
# 2 = alerting, 1 = watch, 0 = calm. `bool` on every comparison, each term
# `or vector(0)` so a missing family counts as false, then clamp_max: a sum.
clamp_max(
    2 * (delta(sum(fw_bouncer_banned_ips)[1h:1m]) > bool 20 or vector(0))
  + 2 * (count(homelab_security_ungated_routes > 0)
         or count(homelab_security_public_hosts != 9)
         or count(homelab_security_privileged_workloads != 9) or vector(0))
  + 2 * (((time() - homelab_security_exporter_last_success_timestamp_seconds > 600)
          or absent(homelab_security_exporter_last_success_timestamp_seconds)
          or absent(fw_bouncer_banned_ips) or absent(cs_active_decisions)
          or absent(cs_parser_hits_ok_total) or vector(0)) > bool 0)
  + (sum(fw_bouncer_banned_ips) > bool 0 or vector(0))
  + (100 * sum(rate(traefik_router_requests_total{code=~"4.."}[15m]))
      / sum(rate(traefik_router_requests_total[15m])) > bool 85 or vector(0))
  + (sum(homelab_authentik_signins_15m{outcome="failure"} > bool 20) or vector(0)),
  2)
```

**Level 2 means act now**: more than 20 bans in an hour, the posture counts moved, the security
pipeline is blind (the exporter stale or a family it publishes gone, including the agent's), or
refusals above 85 % of everything. **Level 1 means watch**: any ban is active, or more than 20
sign-ins failed in 15 minutes.

The first version had a top term that counted `ALERTS{alertname=~"Security.*"}` — and it was dead
on arrival. **Grafana evaluates every rule in this cluster**, so Prometheus's `ALERTS` family is
empty: the panel's term could never be true, and the tile's "alerting" state was proved with a
`vector(2)` probe rather than by a real rule. It now reads the signals the rules are written on, and
the `Alerting` state was then proved by stopping the detector for real — see the paragraph on
`SecurityStackBlind` in [`alerts.md`](alerts.md#alerting).

Three habits in the expression were found by running it, not by reading it. Every comparison is
`bool`: without it a filtered comparison keeps the sample it matched, so
`(sum(fw_bouncer_banned_ips) > 0) * 1` read `3` on the screen instead of `1`. The terms are
**added**, not `or`-ed: with `bool` a false term still returns a `0` sample, and `or` stops at the
first *defined* operand rather than the first *true* one, so the `or` chain answered `Calm` while
three bans were in force. And each term ends in `or vector(0)` so a family that has never been
scraped reads `0` instead of emptying the sum; `delta` on a subquery rather than `increase`, because
the bouncer's family is a gauge.

**Top addresses refused shows who was *answered* 401–499, not who the bouncer dropped.** A dropped
request never reaches Traefik and has no line in the access log, so an address that is banned and an
address that is merely being refused look the same in the log and not the same here. The exact ban
list is `cscli decisions list`, and the count is the *Bans active* tile. **Sign-in outcomes is a count
with no address attached**: the exporter reads authentik's event API and publishes a total per result,
never who signed in — [the design](../superpowers/specs/2026-11-25-security-detection-design.md) §6.5
says why.

Both thresholds were measured on 2026-11-25, before either was set. The screen's nine panels and the
danger-state expression that reads these two numbers are described above; nothing here is a round
number chosen by feel.

**The rejected share** is the percentage of Traefik's requests that were answered `4xx`, by hour, from
`traefik_router_requests_total` — the same family the panel queries, so the check can be repeated by
anyone with the page open. The series is younger than the query: `traefik_router_requests_total` first
appears on 2026-11-21 18:39, when Traefik 3 replaced 2.11, and Prometheus keeps 90 days, so the
measurement covers **the whole life of the family** — 92 hourly samples, not the seven days the command
asks for:

```bash
curl -sG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/PBFA97CFB590B2093/api/v1/query_range" \
  --data-urlencode 'query=100 * sum(rate(traefik_router_requests_total{code=~"4.."}[1h])) / sum(rate(traefik_router_requests_total[1h]))' \
  --data-urlencode "start=$(date -d '7 days ago' +%s)" --data-urlencode "end=$(date +%s)" \
  --data-urlencode 'step=3600'
```

The band ran from **0.33 %** to **81.13 %** (2026-11-22 11:00), with quartiles at 5.21 %, 17.16 % and
42.94 %. The danger state's threshold is **85 %** — the top of that band plus a margin of about four
points, and not the 20 % this work started from.

The 20 % was measured against and dropped, because it sits *inside* the normal band: **42 of the 92
hours were above it**, and those hours are the routine ones on a public address — a bot burst against
`ashuvidz`, `ashudev`, FreshRSS or thelounge answers 401–404 to 500–1,300 requests an hour and pushes
the share to 60–80 % without anything being wrong that a person would act on. A threshold that is true
half the time is not a signal. At 85 % the term speaks only when refusal is near-total: the forward-auth
gate answering everything with `403`, or an address flood drowning the legitimate traffic. The attack
with a name and an address is the *Top addresses refused* panel's job — it reads the log rather than a
share — and the rule that once paged on it, `SecurityAttackSustained`, was retired on 2026-11-26
because it only reported scanning CrowdSec had already banned.

**The silence gap** decides how long `SecurityStackBlind` waits before calling the pipeline dead. It is
measured on the same counter, at the finest window the scrape interval allows — the global interval is
1 m, so `increase(...[1m])` covers a single sample and answers nothing, and `2m` is the narrowest window
that returns a number:

```bash
curl -sG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/PBFA97CFB590B2093/api/v1/query_range" \
  --data-urlencode 'query=sum(increase(traefik_router_requests_total[2m]))' \
  --data-urlencode "start=$(date -d '7 days ago' +%s)" --data-urlencode "end=$(date +%s)" \
  --data-urlencode 'step=120'
```

Across 2,740 two-minute windows (2026-11-21 17:57 → 2026-11-25 13:41) **not one held zero requests**;
the quietest held **4** and the median 8. At five-minute steps the answer is the same, so a
"two gaps of five minutes" rule would have been built on a rhythm this cluster does not have. The
observed gap is therefore **below the measurement floor**, and the rule waits **10 minutes**: five times
that floor, and still far shorter than an outage anyone would tolerate silently. Over the following
day the quietest ten-minute window actually observed held **50 lines** — more than the two-minute
rhythm extrapolates, because quiet stretches do not last ten minutes in a row.

## Changing things

- Dashboards: `POST /api/dashboards/db` with `{dashboard, folderUid: "homelab",
  overwrite: true}`. Read one with `GET /api/dashboards/uid/<uid>`.

Dashboards are changed through that API and nowhere else: how to authenticate is in
[observability-conventions.md](../observability-conventions.md), and the ban on hand edits and
`kubectl` is at the end of [alerts.md](alerts.md#changing-things).
