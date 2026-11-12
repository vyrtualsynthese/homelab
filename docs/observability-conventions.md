# Observability conventions

Homelab-specific facts for the `monitoring` stack. The general practice — how to
choose a threshold, lay out a screen, verify a panel — lives in the project skill
at `.opencode/skills/observability-and-alerting/SKILL.md`. This file only records
what is true **here** and would be wrong elsewhere.

State: 2026-11-12, after the stack was rebuilt and the dashboards were added.

## What runs where

| Component | Version | Placement | Data |
|---|---|---|---|
| Prometheus | v3.16.0 | `nas`, 2 GiB limit | 25 GiB `local-path`, 90 d / 20 GiB cap |
| Grafana | 13.3.1 | `nas` | 5 GiB `local-path` |
| Loki | 3.7.9 | `nas`, monolithic | 20 GiB `local-path`, 30 d retention |
| Fluent Bit | 5.1.3 | DaemonSet, all 4 nodes | tails `/var/log/containers/*.log` only |

All four are wrapper charts under `cluster/helm/monitoring/`, one ArgoCD
Application each, snapshots in `docs/superpowers/specs/2026-11-12-observability-stack-design.md`.

## How metrics get in

- Scraping is **annotation-based only** (`prometheus.io/scrape`). There is no
  Prometheus Operator, no `ServiceMonitor`, and the CRDs are not installed.
  A new exporter needs the annotation, not a chart change.
- **K3s runs the whole control plane in one process.** Each control-plane node's
  kubelet endpoint republishes apiserver, etcd, scheduler and workqueue metrics,
  three times over. The `kubernetes-nodes` job drops those families and keeps
  kubelet/volume/storage; `kubernetes-api-servers` drops `apiserver_*_bucket`.
  Without both trims Prometheus needs ~196k series and OOMs at 2 GiB.
- **The two edge Pis are static jobs**, not discovery: `rbp0` 192.168.1.22 and
  `rbp1` 192.168.1.38, ports 9100 (node-exporter), 9902 (smartctl), 9323
  (Docker). They carry no `node` label, only `instance`, so dashboards label
  them by IP or map the IP to a name.
- Prometheus is pinned to `nas`. Its own memory limit is 2 GiB and the GC target
  follows the cgroup limit, so lowering it below the working set restarts the
  loop that took it down on 2026-11-12.

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
  Coalesce the three, or 7 of the 11 SSDs render blank.
- Drives that do not report an attribute render blank, never 0.

**Fluent Bit** writes `Line_Format json`, so every Loki line is an envelope
(`time`, `stream`, `log`, and a ~1 KB `kubernetes` block of labels, annotations
and image hashes). Log panels unwrap it in the query:

```
{job="fluent-bit"} | json | line_format "{{if .log}}{{.log}}{{else}}{{__line__}}{{end}}"
```

**Temperature**: use `node_thermal_zone_temp` for host temperature. The NAS
reports 95 °C on `nct6775 temp6`, an unconnected header; alerting on
`node_hwmon_temp_celsius` would fire there permanently. GPU temperature is
`DCGM_FI_DEV_GPU_TEMP` from the NVIDIA exporter on `streaming`.

## Where dashboards and rules live

In Grafana's database, created through the HTTP API. **Not in git** — the
approved design makes dashboards-as-code a non-goal, and the Discord webhook
lives only in the contact point (`discord-homelab`), never in the repository.

Folder **Homelab**:

| Dashboard | uid | Purpose |
|---|---|---|
| Homelab · Health | `homelab-health` | presentation view: 6 KPI tiles, status strip, sections |
| Homelab · Investigate | `homelab-investigate` | deep dive: workloads, nodes, control plane, Prometheus |
| Homelab · Logs | `homelab-logs` | volume, errors, live stream |
| Homelab · Disks | `homelab-disks` | SMART per drive, wear forecast |

Consequence of living in the DB: they are not backed up. Grafana's 5 GiB claim
is the only copy.

## Alert set

13 rules in group `homelab-core`, all delivered to Discord.
`group_by [alertname, namespace]`, `group_wait 30s`, `group_interval 5m`,
`repeat_interval 4h`.

| Rule | Threshold | for |
|---|---|---|
| `NodeDown` | node-exporter absent, all 6 hosts | 5m |
| `NodeCpuHigh` | >90 % | 15m |
| `NodeMemoryHigh` | >90 % of `MemAvailable` | 15m |
| `NodeTemperatureHigh` | thermal zone >80 °C | 10m |
| `GpuTemperatureHigh` | `DCGM_FI_DEV_GPU_TEMP` >80 °C | 10m |
| `DiskTooHot` | drive >55 °C | 15m |
| `DiskFillingUp` | filesystem >85 % | 30m |
| `PVCFillingUp` | claim >85 % | 30m |
| `DriveNeedsReplacement` | SMART failed, exit != 0, <10 % life, or pending sectors | 10m |
| `DiskReallocatingSectors` | any new sector in 1 h | 5m |
| `PodCrashLooping` | CrashLoopBackOff | 15m |
| `PodPending` | pending, created <2 h ago | 15m |
| `MonitoringDown` | Prometheus self-scrape missing; `execErrState: Alerting` | 5m |

`noDataState: OK` everywhere, because every expression is written to return no
series while healthy. Only `MonitoringDown` alerts on evaluation errors, so a
Prometheus restart produces one message, not thirteen.

## Silences and current state

A 30-day silence matches `pod=~"ghostfolio-.*|wiki-app-.*|monitorss-bot-.*"` —
three apps that have been crash-looping since the Longhorn loss. They still show
`firing`; the silence only suppresses the notification. Remove it when the apps
are fixed.

`nas /dev/sdf` (Crucial BX500, holds `local-path`, therefore Prometheus, Loki,
Grafana and every config volume) is at **100 % of its rated write endurance**,
0 % life left, SMART exit code 32. It needs replacing; moving the claims is a
separate job.

## Known gaps

- 7 scrape targets down, all `media-stack` exporters (`radarr`, `radarr-anime`,
  `sonarr`, `sonarr-anime`, `prowlarr`, `qbittorrent`, `unpackerr`) — app-side
  API keys and ports, deliberately deferred.
- No backup-freshness metric, no blackbox probe, no certificate expiry check.
- 17 pods `Pending` on claims deleted with Longhorn; excluded from `PodPending`
  by the 2-hour creation window.
- No metrics for the NFS provisioners or the Pi-hole resolver itself (only the
  exporter).
