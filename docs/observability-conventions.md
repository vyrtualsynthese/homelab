# Observability conventions

> **In one line.** What watches this cluster, where each component runs and how big it is, how to
> query Prometheus, Loki and Grafana without cluster access, and how metrics get in.
>
> **Use it when** you want a number out of the stack, or you are adding an exporter · **Verified**
> 2026-11-17 · **Kind:** living page

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

Fluent Bit tails `/var/log/containers/*.log` only: container output, no host logs and no systemd
journal. The claim sizes and how long each keeps data are in
[`reference/metrics.md#storage-and-retention`](reference/metrics.md#storage-and-retention).

All four are wrapper charts under `cluster/helm/monitoring/`, one ArgoCD
Application each; the design is
`docs/superpowers/specs/2026-11-12-observability-stack-design.md`.

**Resource sizing, measured 2026-11-12 over 15 h** (working set, per container):

| Workload | avg | p95 | peak | request | limit |
|---|---|---|---|---|---|
| Prometheus | 1.26 GiB | 1.74 GiB | 1.76 GiB | 1Gi | 2Gi |
| Grafana | 292 MiB | 346 MiB | 400 MiB | 256Mi | 768Mi |
| Loki | 134 MiB | 166 MiB | 217 MiB | 256Mi | 1Gi |
| Fluent Bit (per pod) | 6-9 MiB | 9.8 MiB | 11.6 MiB | 32Mi | 64Mi |
| config-reloader | 9 MiB | 9.6 MiB | 9.9 MiB | 16Mi | 32Mi |

Grafana's limit moved from 512 MiB to 768 MiB on 2026-11-13: the rebuilt pod
peaked at 370 MiB, filling the old limit to 74 %, and Grafana evaluates and
delivers every alert in the cluster. The other rows are unchanged; the wider
stack review of that date is the table in
[`reference/chart-sizing.md`](reference/chart-sizing.md).

Prometheus keeps its 2 GiB ceiling because it sizes its own GC from the cgroup
limit and was OOM-killed at 1 GiB; only its request moved up to the measured
steady state. Loki's rules sidecar is disabled — it watched for
Prometheus-Operator rule ConfigMaps this cluster does not install, and held
78 MiB with no limit. There are no CPU limits, by house rule. The numbers are
low because the cluster produces little log and query traffic: a 30-day Loki
query or a compaction burst is the case the headroom exists for, so re-measure
before lowering Loki further.

Three older exporters live beside them unchanged: `mktxp` and `pihole-exporter`
on `nuc0`, and `prometheus-smartctl` as a DaemonSet (its pods on `nas`, `nuc0`,
`streaming`, `rbp2`).

## How to query the stack

Agents do not need cluster access for any of this.

**Prometheus** — `http://prometheus.kub.local`, no authentication:

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=count(up==1)'
```

Useful endpoints: `/api/v1/query`, `/api/v1/query_range`, `/api/v1/targets`,
`/api/v1/rules`, `/api/v1/status/flags`, `/api/v1/status/config`.
It holds **no alert rules and no Alertmanager** (`alertmanager.enabled: false`,
`rule_files` empty): alerting lives entirely in Grafana.

**Loki** — `http://loki.kub.local`, no authentication. Every line is a Fluent Bit
JSON envelope; unwrap it in the query (see the exporter notes in
[`reference/metrics.md`](reference/metrics.md#exporter-quirks)). The UI is at `/ui`.

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
- Global scrape interval is **1 minute** (a few jobs use 5 m).
- **K3s runs the whole control plane in one process.** Each control-plane node's
  kubelet endpoint republishes apiserver, etcd, scheduler and workqueue metrics,
  three times over. The `kubernetes-nodes` job drops those families and keeps
  kubelet/volume/storage; `kubernetes-api-servers` drops `apiserver_*_bucket`.
  Without both trims Prometheus needs ~196k series and OOMs at 2 GiB.
- **The two edge Pis are static jobs**, not discovery: `rbp0` 192.168.1.22 and
  `rbp1` 192.168.1.38, ports 9100 (node-exporter), 9902 (smartctl), 9323
  (Docker). They carry no `node` label, only `instance`, so dashboards label
  them by IP or map the IP to a name.
- Prometheus is pinned to `nas`. Its memory limit is 2 GiB and the GC target
  follows the cgroup limit, so lowering it below the working set restarts the
  OOM loop of 2026-11-12.

**Measured on 2026-11-13:** 121,431 head series, ~1,980 samples/s, every scrape
target up. Series by job: `kubernetes-api-servers` 49 243,
`kubernetes-service-endpoints` 27 748, `kubernetes-nodes-cadvisor` 22 105,
`kubernetes-pods` 8 841, `kubernetes-nodes` 7 207, `rbp0` 1 343, `rbp1` 1 341,
`prometheus` 1 309. The `kubernetes-pods` growth is the exportarr sidecars and
the Plex, autobrr, n8n and qBittorrent exporters.

## Where the detail lives

| Question | Page |
|---|---|
| What does an exporter really publish, how long is data kept, what did the stack look like on 2026-11-13? | [`reference/metrics.md`](reference/metrics.md) |
| Which dashboard do I open, and how do I change one? | [`reference/dashboards.md`](reference/dashboards.md) |
| What do the rules check, why is this silenced, and what is not monitored? | [`reference/alerts.md`](reference/alerts.md) |
