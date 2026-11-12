# Observability conventions

Homelab-specific facts for the `monitoring` stack. The general practice — how to
choose a threshold, lay out a screen, verify a panel — lives in the project skill
at `.opencode/skills/observability-and-alerting/SKILL.md`. This file only records
what is true **here** and would be wrong elsewhere.

State: 2026-11-12, verified against the live cluster. Re-verify the "measured"
numbers before trusting them in a new document.

## What runs where

| Component | Version | Placement | Data |
|---|---|---|---|
| Prometheus | 3.16.0 | `nas`, 2 GiB limit | 25 GiB `local-path`, 90 d / 20 GiB cap |
| Grafana | 13.3.1 | `nas`, 512 MiB limit | 5 GiB `local-path` |
| Loki | 3.7.9 | `nas`, monolithic | 20 GiB `local-path`, 30 d retention |
| Fluent Bit | 5.1.3 | DaemonSet, all 4 nodes | tails `/var/log/containers/*.log` only |

All four are wrapper charts under `cluster/helm/monitoring/`, one ArgoCD
Application each; the design is
`docs/superpowers/specs/2026-11-12-observability-stack-design.md`.

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
JSON envelope; unwrap it in the query (see below). The UI is at `/ui`.

**Grafana** — `http://grafana.kub.local`. The API needs a bearer token from
`$GRAFANA_TOKEN`, exported by `~/.config/secrets/homelab.env`:

```bash
source ~/.config/secrets/homelab.env
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  http://grafana.kub.local/api/search?type=dash-db
```

The token belongs to a service account with the Admin role; the only interactive
user is `admin`. Dashboards, alert rules, contact points and silences all live in
Grafana's SQLite database, **not in git**. The Discord webhook exists only in the
`discord-homelab` contact point. There is exactly one copy of all of it, on the
Grafana claim.

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

**Measured on 2026-11-12:** 116,742 head series, ~97 M samples appended, 45
scrape targets (38 up, 7 down). Series by job: `kubernetes-api-servers` 47 409,
`kubernetes-service-endpoints` 27 488, `kubernetes-nodes-cadvisor` 21 311,
`kubernetes-pods` 7 143, `kubernetes-nodes` 7 101, `prometheus` 1 363, `rbp0` 1
343, `rbp1` 1 341.

## Exporter quirks

**SMART** (`matusnovak/prometheus-smartctl`, metrics `smartprom_*`, not
`smartctl_*`):

- The plain metric is the drive's **normalized SMART value** (0–100 health
  score); the measurement is `_raw`. Charting the plain name shows 132 °C on a
  49 °C disk.
- NVMe drives publish only the plain form (`smartprom_temperature`,
  `smartprom_percentage_used`); SATA drives publish both.
- Life coverage differs per vendor: Crucial → `percent_lifetime_remain`,
  NVMe → `100 - percentage_used`, Samsung SATA → `wear_leveling_count`.
  Coalesce the three, or most SSDs render blank.
- Drives that do not report an attribute render blank, never 0.
- 18 series cover 18 drives: `nas` 11 (9 SATA, 2 NVMe), `nuc0` 2,
  `streaming` 2, `rbp2` 1, `rbp0` 1 and `rbp1` 1. The two edge Pi drives carry
  no `node` label; use `instance` to name them.

**Fluent Bit** writes `Line_Format json`, so every Loki line is an envelope
(`time`, `stream`, `log`, and a ~1 KB `kubernetes` block of labels, annotations
and image hashes). Log panels unwrap it in the query:

```
{job="fluent-bit"} | json | line_format "{{if .log}}{{.log}}{{else}}{{__line__}}{{end}}"
```

Labels are `job=fluent-bit`, `namespace`, `container`, `node`; the pod name is
structured metadata, not a label.

**Temperature**: use `node_thermal_zone_temp` for host temperature. The NAS
reports 95 °C on `nct6775 temp6`, an unconnected header; alerting on
`node_hwmon_temp_celsius` would fire there permanently. GPU temperature is
`DCGM_FI_DEV_GPU_TEMP` from the NVIDIA exporter on `streaming`.

**Volume stats are partial**: `kubelet_volume_stats_*` exists for only 25 of the
36 claims, and some of those are reported under control-plane instances. Claims
on `local-path` bound to `nas` mostly do not appear, so `PVCFillingUp` and the
Volume-usage panels see the NFS claims and miss the rest. Treat a missing line
as unknown, not as healthy.

## Dashboards

Folder **Homelab**, created through the HTTP API:

| Dashboard | uid | Panels | Purpose |
|---|---|---|---|
| Homelab · Health | `homelab-health` | 26 | Debug overview: KPIs, attention strip, nodes, workloads, storage, logs |
| Homelab · Investigate | `homelab-investigate` | 27 | Deep dive: workloads, nodes, control plane, Prometheus |
| Homelab · Logs | `homelab-logs` | 8 | Volume, errors, live stream with namespace/container filters |
| Homelab · Disks | `homelab-disks` | 14 | SMART per drive, wear forecast |
| Homelab · Showcase | `homelab-showcase` | 13 | Presentation screen: drawn topology, four big numbers, network, storage, GPU |

All four have a 30 s–1 m refresh and a 6 h–24 h default range. They are not
backed up: Grafana's 5 GiB claim is the only copy. A `docker save` of the
dashboard JSON is not a backup either — the SQLite file is what would be lost.

## Alerting

Grafana unified alerting, folder `Homelab`, rule group `homelab-core`,
evaluation interval **60 s**. 13 rules, one contact point, one default route.

**Delivery**: contact point `discord-homelab` (type `discord`), default
notification policy `group_by [alertname, namespace]`, `group_wait 30s`,
`group_interval 5m`, `repeat_interval 4h`. Severity is a label only; it does not
change the route, the channel or the repeat interval.

**Rule set and live state on 2026-11-12:**

| Rule | Threshold | for | State |
|---|---|---|---|
| `NodeDown` | node-exporter absent, all 6 hosts | 5m | inactive |
| `NodeCpuHigh` | >90 % | 15m | inactive |
| `NodeMemoryHigh` | >90 % of `MemAvailable` | 15m | inactive |
| `NodeTemperatureHigh` | thermal zone >80 °C | 10m | inactive |
| `GpuTemperatureHigh` | `DCGM_FI_DEV_GPU_TEMP` >80 °C | 10m | inactive |
| `DiskTooHot` | drive >55 °C | 15m | inactive |
| `DiskFillingUp` | filesystem >85 % | 30m | inactive |
| `PVCFillingUp` | claim >85 % | 30m | inactive (blind for `local-path`, see above) |
| `DriveNeedsReplacement` | SMART failed, exit != 0, <10 % life, or pending sectors | 10m | **firing** on `nas /dev/sdf` |
| `DiskReallocatingSectors` | any new sector in 1 h | 5m | **broken, never fires** |
| `PodCrashLooping` | CrashLoopBackOff | 15m | **firing** ×3, silenced |
| `PodPending` | pending, created <2 h ago | 15m | inactive |
| `MonitoringDown` | Prometheus self-scrape missing; `execErrState: Alerting` | 5m | inactive |

`noDataState: OK` everywhere, because every expression is written to return no
series while healthy. Only `MonitoringDown` alerts on evaluation errors, so a
Prometheus restart produces one message, not thirteen.

**`DiskReallocatingSectors` is invalid PromQL**:
`delta(max by (...)(metric)[1h])` fails with *ranges only allowed for vector
selectors*. The fix is a subquery: `delta(max by (...)(metric)[1h:])`. Until
then a drive that starts reallocating sectors is not reported.

A 30-day silence matches
`pod=~"ghostfolio-.*|wiki-app-.*|monitorss-bot-.*"` — three apps that have been
crash-looping since the Longhorn loss. They still show `firing`; the silence only
suppresses the notification. Remove it when the apps are fixed.

## Storage and retention

| Volume | Claim | Size | Retention |
|---|---|---|---|
| Prometheus TSDB | `monitoring/prometheus-server` | 25 GiB | `90d`, hard cap `20GiB` (`retention.size`) |
| Grafana database | `monitoring/grafana` | 5 GiB | n/a |
| Loki chunks + index | `monitoring/storage-loki-0` | 20 GiB | `720h` via the compactor |

Verify rather than trust:

```bash
curl -s http://prometheus.kub.local/api/v1/status/flags | grep retention
curl -s http://loki.kub.local/config | sed -n '/limits_config:/,/^[a-z]/p'
```

`nas`'s `/mnt/ssd0` holds all `local-path` claims and currently has far more free
space than the 50 GiB provisioned. Loki's compactor retains by time; confirm it
runs (config shows `retention_enabled: true`,
`delete_request_store: filesystem`) before assuming old logs disappear.

## Current health facts (2026-11-12)

- 6 hosts: `nas` (.41), `nuc0` (.27), `streaming` (.46), `rbp2` (.48), `rbp0`
  (.22), `rbp1` (.38). All answer.
- 133 pods known, 104 Running, 16 Pending, 1 ContainerCreating, 2
  CrashLoopBackOff, 1 Error, 1 Terminating. 8 Completed.
- 36 PVCs; 12 are stuck `Terminating` from Longhorn.
- 17 pods Pending are older than the 2-hour `PodPending` window, so they are
  deliberately outside the alert. The underlying claims are W0's cleanup, not
  monitoring's.
- 7 scrape targets down, all `media-stack` exporters (`radarr`, `radarr-anime`,
  `sonarr`, `sonarr-anime`, `prowlarr`, `qbittorrent`, `unpackerr`) — wrong app
  side API keys and ports, deliberately deferred. **No alert reports them.**
- `nas /dev/sdf` (Crucial BX500, holds `local-path`, therefore Prometheus, Loki,
  Grafana and every config volume) is at **0 % life left**, SMART exit code 32.
  It needs replacing; `DriveNeedsReplacement` is firing for it.

## Known gaps

- **No alert on scrape targets being down.** `up == 0` is only covered for the
  six node-exporter jobs. The 7 dead media exporters have been invisible since
  the rebuild; a target that dies silently takes its app's metrics with it.
- **`PVCFillingUp` is blind for `local-path` claims** (25 of 36 claims publish
  volume stats, mostly NFS).
- **No backup-freshness metric**, no certificate-expiry check, no blackbox probe,
  no GPU XID/ECC alert, no node filesystem read-only alert, no deployment
  replica alert.
- **No service-level alerting**: a pod can disappear and nothing fires unless it
  is crash-looping or pending.
- **Alerting is a single point of failure**: Grafana evaluates and delivers. If
  Grafana, Prometheus or `nas` goes down, there is no channel left to say so.
  `MonitoringDown` only helps while Grafana itself still runs.
- **Summaries name the instance IP** (`NodeDown`, `NodeCpuHigh`) instead of the
  host, and no rule carries a `runbook_url` or an action line.
- No metrics for the NFS provisioners or the Pi-hole resolver itself (only its
  exporter).
- Dashboards and rules are not in git and are not backed up.

## Changing things

- Dashboards: `POST /api/dashboards/db` with `{dashboard, folderUid: "homelab",
  overwrite: true}`. Read one with `GET /api/dashboards/uid/<uid>`.
- Alert rules: `GET/POST /api/v1/provisioning/alert-rules`, groups under
  `/api/v1/provisioning/folder/homelab/rule-groups/homelab-core`. Delivery is
  `/api/v1/provisioning/contact-points` and `/api/v1/provisioning/policies`.
- Validate every PromQL expression against Prometheus before saving it. The
  `DiskReallocatingSectors` bug is what skipping this costs.
- Never `kubectl apply` a dashboard or rule; nothing in the repo produces them.
  Never `kubectl` at all on this workstation: the harness blocks it and the MCP
  account is read-only. Use the HTTP APIs above.
