# Observability conventions

> **In one line.** What watches this cluster, where each component runs and how big it is, how to
> query Prometheus, Loki and Grafana without cluster access, and how metrics get in.
>
> **Use it when** you want a number out of the stack, or you are adding an exporter · **Verified**
> 2026-11-25 · **Kind:** reference
>
> **Sources** `cluster/helm/monitoring/prometheus/values.yaml`,
> `cluster/helm/monitoring/loki/values.yaml`, `cluster/helm/monitoring/grafana/values.yaml`,
> `cluster/helm/monitoring/fluent-bit/values.yaml`, `cluster/helm/monitoring/blackbox-exporter/values.yaml`,
> `cluster/helm/platform/traefik/values.yaml`, `ansible/roles/traefik_logs/tasks/main.yml`,
> `ansible/roles/traefik_logs/defaults/main.yml` (the 200 MB and seven copies),
> `docs/reference/metrics.md` and `docs/reference/alerts.md`. The live stack was read through
> Prometheus and Grafana on 2026-11-25.

The general practice — how to choose a threshold, lay out a screen, verify a panel — lives in the
project skill at `.opencode/skills/observability-and-alerting/SKILL.md`. This file only records what
is true **here** and would be wrong elsewhere.

Each fact carries its measurement date: the sizing below and the counts behind
[`reference/metrics.md`](reference/metrics.md) were taken on 2026-11-13 against the live cluster,
after the media chart repairs. Re-verify a measured number before trusting it in a new document.

## What runs where

| Component | Version | Placement |
|---|---|---|
| Prometheus | 3.16.0 | `nas`, 2 GiB limit |
| Grafana | 13.3.1 | `nas`, 768 MiB limit |
| Loki | 3.7.9 | `nas`, monolithic |
| Fluent Bit | 5.1.3 | DaemonSet, all 4 nodes |
| blackbox exporter | v0.29.1 | `nas`, 64 MiB limit |

Fluent Bit tails `/var/log/containers/*.log` on every node, plus `/var/log/traefik/access.log` on
`streaming`; both are files a container writes, so host logs and the systemd journal stay out. The
claim sizes and how long each keeps data are in
[`reference/metrics.md#storage-and-retention`](reference/metrics.md#storage-and-retention).

What the pipeline carries in a day — 533,803 lines and 117 MB on 2026-11-22 — and the selector that
reproduces every figure are in [`reference/log-budget.md`](reference/log-budget.md). Read it before
treating a log volume as a regression: the seven-day total is dominated by two bursts that are over.

All five are wrapper charts under `cluster/helm/monitoring/`, one ArgoCD
Application each; the design is
`docs/superpowers/specs/2026-11-12-observability-stack-design.md`.

### Resource sizing

**Measured 2026-11-19 over 7 days** (working set, per container):

| Workload | avg | p95 | peak | request | limit |
|---|---|---|---|---|---|
| Prometheus | 966 MiB | 1,102 MiB | 1,618 MiB | 1.25Gi | 2Gi |
| Grafana | 370 MiB | 391 MiB | 427 MiB | 416Mi | 768Mi |
| Loki | 144 MiB | 193 MiB | 437 MiB | 256Mi | 1Gi |
| Fluent Bit (per pod) | 6-9 MiB | 9.8 MiB | 11.6 MiB | 32Mi | 64Mi |
| config-reloader | 9 MiB | 9.6 MiB | 9.9 MiB | 16Mi | 32Mi |

The first three rows are the 2026-11-19 pass over the seven days the TSDB then
held; it retains 90 days now (`storage.tsdb.retention.time`), with 10 days on
disk on 2026-11-22, so the next pass can use a month. Fluent Bit and
config-reloader are still the 2026-11-12 15-hour sample.
Grafana was measured across the eight pod generations the rollouts left behind,
and its row is the worst generation.

Prometheus keeps its 2 GiB ceiling because it sizes its own GC from the cgroup
limit and was OOM-killed at 1 GiB; its request moved from 1 GiB to 1.25 GiB.
Seven days put the p95 at 1,102 MiB, but the worst day was 1,162 MiB and the
trend is upward, so 1.125 GiB would have sat a hair below the recent steady
state. Grafana's request moved from 256 MiB to 416 MiB because every generation
since 2026-11-17 ran above its 391 MiB p95; its limit moved from 512 MiB to
768 MiB on 2026-11-13, when the rebuilt pod filled the old limit to 74 %, and
this app evaluates and delivers every alert in the cluster. Loki's 256 MiB
request stays: its p95 is 193 MiB over the week and 216 MiB over the last 24 h,
and the 437 MiB peak on 2026-11-18 is the compaction burst its 1 GiB limit
exists for. Loki's rules sidecar is disabled — it watched for
Prometheus-Operator rule ConfigMaps this cluster does not install, and held
78 MiB with no limit. There are no CPU limits, by house rule. The numbers are
low because the cluster produces little log and query traffic: a 30-day Loki
query or a compaction burst is the case the headroom exists for, so re-measure
before lowering Loki further.

Four exporters live beside them: `mktxp` and `pihole-exporter`
on `nuc0`, `prometheus-smartctl` as a DaemonSet (its pods on `nas`, `nuc0`,
`streaming`, `rbp2`), and `snapraid-exporter` on `nas` — a second node-exporter with every default
collector off, serving the one file the snapraid runner writes after a sync it finished
([`reference/alerts.md`](reference/alerts.md#alerting)). `mktxp` reads the CRS326 and CRS309
switches; since 2026-11-18 it is scheduled by `nodeSelector` on `nuc0` rather than by the retired
`node-type` label. Its image stays on `latest`: the registry publishes no
semver or digest-friendly tags.

The blackbox exporter joined the stack on 2026-11-20: one replica on `nas`, probing the
public names and both DNS zones
([`runbooks/cloud-monitoring.md`](runbooks/cloud-monitoring.md)). It is the one pod
here that does not use cluster DNS — `dnsPolicy: None` with `1.1.1.1` and `9.9.9.9` —
because Pi-hole rewrites `ashudev.com` to the LAN address `192.168.1.46`, and a
probe resolving through it would test Traefik instead of the public zone and call a
broken name green.

## How to query the stack

Agents do not need cluster access for any of this. Prometheus and Grafana answer on their hostnames;
Loki is behind a sign-in since 2026-11-22, so its recipes carry the Grafana token instead of an open
port.

**Prometheus** — `http://prometheus.kub.local`, no authentication:

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=count(up==1)'
```

Useful endpoints: `/api/v1/query`, `/api/v1/query_range`, `/api/v1/targets`,
`/api/v1/rules`, `/api/v1/status/flags`, `/api/v1/status/config`.
It holds **no alert rules and no Alertmanager** (`alertmanager.enabled: false`,
`rule_files` empty): alerting lives entirely in Grafana.

**Loki** — `https://loki.kub.local`, behind `kube-system-authentik-forwardauth` on the `websecure`
router, Admins only. Loki authenticates nobody itself (`auth_enabled: false`) and keeps 30 days of
every namespace's logs, so the gate is the only access control, and `/loki/api/v1/*` has no
exemption: the query API is exactly what it protects. There is no page to open on that host: `/ui/`
is Loki's operational API (`/ui/api/v1/cluster/nodes` answers JSON) and its frontend is a Grafana
plugin, so any other `/ui` path answers `307` to `/ui/404?path=…`. Logs are read in Grafana —
Explore, or Logs Drilldown at `https://grafana.kub.local/a/grafana-lokiexplore-app/explore`, which
is where the authentik `Loki` tile points.

Queries from a script go through Grafana's datasource proxy, which holds the Loki datasource and
authenticates as Grafana does. `GRAFANA_TOKEN` is a Grafana service-account token in
`~/.config/secrets/homelab.env`; `-k` skips the internal CA check, which a client without the
`ashulab` root installed would otherwise fail. The uid `P8E80F9AEF21F6940` is the Loki datasource's,
and every path from [`reference/log-budget.md`](reference/log-budget.md) appends to it.

```bash
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query={job="fluent-bit"} | detected_level="error"'
```

The line is the application's own message, not a Fluent Bit envelope: the output strips the envelope
before it is stored (see the exporter notes in
[`reference/metrics.md`](reference/metrics.md#exporter-quirks)), so a query reads
`{job="fluent-bit"} | detected_level="error"` with no parser in between.

**Grafana** — `https://grafana.kub.local`, served over the cluster's internal
certificate (`*.kub.local` from the ashulab internal CA,
[`runbooks/internal-ca.md`](runbooks/internal-ca.md)); the plain-HTTP router only
redirects, so scripts must use `https` and a client without the root installed
shows a certificate warning. `server.root_url` is set to that host in the wrapper
chart, because Grafana builds the Source and Silence links of every Discord alert
from it; leaving it unset produced `http://localhost:3000/`. The OAuth redirect
URI in the authentik blueprint carries the same scheme, so the sign-in flow does
not disagree with it. The API needs a bearer token from `$GRAFANA_TOKEN`,
exported by `~/.config/secrets/homelab.env`:

```bash
source ~/.config/secrets/homelab.env
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/search?type=dash-db
```

The token belongs to a service account with the Admin role; the only interactive
user is `admin`. The dashboards, the rules, the contact points and the silences are
Grafana's own data: where they live, and what that costs, is in
[`reference/dashboards.md`](reference/dashboards.md) and
[`reference/alerts.md#known-gaps`](reference/alerts.md#known-gaps).

## How metrics get in

- Scraping is **annotation-based only** (`prometheus.io/scrape`). There is no
  Prometheus Operator, no `ServiceMonitor`, and the CRDs are not installed. A new
  exporter needs the annotation, not a chart change.
- Global scrape interval is **1 minute** (a few jobs use 5 m). Since 2026-11-18 the media
  stack's exporters are on the 5-minute jobs: they carry `prometheus.io/scrape_slow` in
  addition to `prometheus.io/scrape`, which is how the two `-slow` jobs are found and how
  the fast job refuses them. Their numbers — queue depth, library counts — move in hours.
- **K3s runs the whole control plane in one process.** Each control-plane node's
  kubelet endpoint republishes apiserver, etcd, scheduler and workqueue metrics,
  three times over. The `kubernetes-nodes` job drops those families and keeps
  kubelet/volume/storage; `kubernetes-api-servers` keeps the counters and sums and
  drops every histogram bucket. Without both trims Prometheus needs ~196k series
  and OOMs at 2 GiB.
- **Five more drop rules follow the same principle**: nothing is kept that no panel
  and no rule reads. The list, with the series each one removes, is in
  [`reference/metrics.md#what-prometheus-throws-away-before-storing`](reference/metrics.md#what-prometheus-throws-away-before-storing).
  A family with no reader is dropped at the scrape rather than dropped from a
  dashboard later: the write is what costs.
- **The two edge Pis are static jobs**, not discovery: `rbp0` 192.168.1.22 and
  `rbp1` 192.168.1.38, ports 9100 (node-exporter), 9902 (smartctl), 9323
  (Docker). They carry no `node` label, only `instance`, so dashboards label
  them by IP or map the IP to a name.
- **Loki and Grafana are static jobs too**, `loki.monitoring.svc:3100` and
  `grafana.monitoring.svc:80`, added on 2026-11-18. They publish the stack's
  own health (`loki_*` push failures, `grafana_*` rule-evaluation failures),
  which nothing scraped before. Scrape the **Service** port: Grafana listens on
  3000 in the container but its Service exposes only 80, so `:3000` times out.
- **The cloud probes are two static jobs too**, `blackbox-http` every minute against 3 endpoints and
  `blackbox-dns` every five minutes against 28 — 8 `ashudev.com` checks on its 2 authoritative
  servers, 3 `ashuvidz.com` checks on its 4. A multi-target exporter cannot be found by pod
  annotation — Prometheus has to rewrite each target's address into `__param_target` and name the
  module — so the targets are static, while the prober's own pod carries `prometheus.io/scrape` like
  any exporter. What that means for the `Cloud*` rules when the prober dies is in
  [`runbooks/cloud-monitoring.md` §8](runbooks/cloud-monitoring.md#8-known-limits).
- **mktxp is annotation discovery**, like the in-cluster exporters: one pod with
  `prometheus.io/scrape` on port 49090, and 838 series for the two switches on
  2026-11-18. An unreachable switch publishes nothing rather than `up == 0`.
- **`snapraid-exporter` is annotation discovery too**, and the only exporter here whose number is
  written rather than measured: the runner on `nas` writes the marker file, the pod reads the
  directory, and the textfile collector is the only collector it enables. It publishes a handful of
  series — the timestamp, `node_textfile_mtime_seconds` and
  `node_scrape_collector_success{collector="textfile"}` — and **none of the host's own metrics**
  (`node_cpu_*`, `node_memory_*`, `node_filesystem_*`), because the defaults are off; the two
  `node_*` names it does publish are the textfile collector's own.
  `node_scrape_collector_success` answering `0` means the file was rejected, not
  that the sync is healthy.
- **Argo CD's three components are annotation discovery since 2026-11-22**, on their **metrics
  Services** — controller 8082, repo-server 8084, server 8083 — not on the pods.
  `global.addPrometheusAnnotations` stamps the annotation; `controller.metrics` and
  `repoServer.metrics` are what make the first two Services exist. The Services are headless, so an
  endpoint is the pod's own address and the scrape is a pod-to-pod connection: **the chart's own
  policy allows the named `metrics` port to any namespace through an empty `namespaceSelector` peer,
  and this k3s does not enforce that peer** (measured 2026-11-22: with the chart's rule the only one
  on the controller, 8082 refused a pod in `monitoring`; the same pod was allowed by a rule whose
  peer was an `ipBlock`, with the named port or the numeric one). `cluster/helm/argocd/values.yaml`
  carries that numeric `ipBlock` rule for 8082 and 8084 beside the chart's own. Read the matrix the
  way it was measured: the port form is not the variable, the peer form is.
- Prometheus is pinned to `nas`. Its memory limit is 2 GiB and the GC target
  follows the cgroup limit, so lowering it below the working set restarts the
  OOM loop of 2026-11-12.

**Measured on 2026-11-18**, after the drop rules landed (the 2026-11-13 figures in brackets):
**73,835 head series** (121,431), **1,151 samples/s** (1,980), 52 scrape targets. Series by
job: `kubernetes-api-servers` 22,160 (49,243), `kubernetes-service-endpoints` 19,574 (27,748),
`kubernetes-nodes-cadvisor` 11,628 (22,105), `kubernetes-pods` 6,413 (8,841), `kubernetes-nodes`
2,590 (7,207), `grafana` 2,283 (new), `rbp0` 1,343, `rbp1` 1,341, `loki` 979 (new), `prometheus` 895
(1,309). The working set did not follow the series count down: 1,195 MiB p95 in the half hour after
the change against 1,185 MiB over the 24 hours before it. The request was then still 1 GiB against a
working set above it — a sizing question, not a scrape one, answered on 2026-11-19 by raising it to
1.25 GiB ([the sizing table](#resource-sizing)); the detail is in
[`reference/metrics.md`](reference/metrics.md#what-prometheus-throws-away-before-storing).

Argo CD joined the same job on 2026-11-22: its three components expose 3,384 series, the drop rule
keeps at most 350, and the first scrape after the restarted pods stored 189 — a process that has not
yet synced publishes fewer. The job holds 18,968 stored series that evening.

**Measured on 2026-11-18, after the media trim:** the media stack's ten targets
were 979 of `kubernetes-pods`' 6,429 live series and wrote 16.3 samples/s. They
now hold 248 series and write 1.6 samples/s, because 731 are dropped at the
scrape and eight of the ten targets moved to the 5-minute jobs
([`reference/metrics.md#exporter-quirks`](reference/metrics.md#exporter-quirks)).

## What the router logs

**The URL is not in the store, and the client is.** Since 2026-11-25 Traefik writes its access log
to `/var/log/traefik/access.log` on `streaming` — a file owned by `65532:65532`, mode `0660`, rotated
at 200 MB with seven copies — and Fluent Bit tails it. The file carries the path and the user agent
because CrowdSec's `crowdsecurity/traefik-logs` parser needs both to recognise probing;
[the security design](superpowers/specs/2026-11-25-security-detection-design.md) says why the
detector was worth that. Fluent Bit removes `RequestPath`, `request_User-Agent` and
`RequestReferer` before Loki, so what the 30-day store holds is:

| Field | In the store | Why |
|---|---|---|
| `ClientHost`, `ClientAddr` | yes | `ClientHost` is the source address and the field CrowdSec reads; `ClientAddr` is kept so the parser's destination field is not empty |
| `DownstreamStatus`, `RouterName`, `RequestHost`, `RequestMethod`, `Duration`, `TLSVersion` | yes | unchanged since the 2026-11-22 measurement, two paragraphs below |
| `RequestPath` | **no** | FileBrowser share tokens, Plex tokens; removed by Fluent Bit |
| every header except `User-Agent` | no | dropped by Traefik before the file is written |
| `User-Agent` | **no** | in the file for the detector, removed by Fluent Bit |
| `ClientUsername`, `ClientPort` | no | dropped by Traefik |

Two consequences worth knowing before writing a query: `{job="fluent-bit", container="traefik"}`
still resolves, because the file's output carries that label statically, and **a request the bouncer
dropped has no line at all** — it never reaches Traefik, so a banned address disappears from the log
rather than appearing with a 403.

**Measured 2026-11-22** over the twelve minutes after that day's last restart (01:20–01:32Z): 153 lines
and 118 kB, so 769 bytes a line and **about 15 MB/day** at the night-time rate, against a pipeline that
carries 0.09 GB/day. An evening reads higher; the day's own figure is two queries away:

```bash
# bytes carried in the trailing 24 h — this is the day's volume
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query=sum(bytes_over_time({job="fluent-bit", container="traefik"}[24h]))'
# lines in the same window; divide the two for the line size
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query=sum(count_over_time({job="fluent-bit", container="traefik"}[24h]))'
```

Two panels on **Homelab · Traefik**, row *Request record*, read it: *Failed requests (4xx/5xx)* and
*Requests slower than 1 s, by router*. A long Plex stream belongs in the second one. Lines reach Loki
a few seconds late, because Fluent Bit batches on its own flush interval; Traefik's writer does not
wait.

The store itself is gated since 2026-11-22 (`authentik-forwardauth`, Admins only), so a line is no
longer readable by every device on the LAN — but it is still 30 days of every application's output,
not only Traefik's.

## Where the detail lives

| Question | Page |
|---|---|
| What does an exporter really publish, how long is data kept, what did the stack look like on 2026-11-13? | [`reference/metrics.md`](reference/metrics.md) |
| Which dashboard do I open, and how do I change one? | [`reference/dashboards.md`](reference/dashboards.md) |
| What do the rules check, why is this silenced, and what is not monitored? | [`reference/alerts.md`](reference/alerts.md) |
