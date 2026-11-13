# Observability conventions

Homelab-specific facts for the `monitoring` stack. The general practice — how to
choose a threshold, lay out a screen, verify a panel — lives in the project skill
at `.opencode/skills/observability-and-alerting/SKILL.md`. This file only records
what is true **here** and would be wrong elsewhere.

State: 2026-11-13, verified against the live cluster; the scrape targets, the
media-stack silence and the media dashboard were re-checked after the media chart
repairs. Re-verify the "measured" numbers before trusting them in a new document.

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

**Resource sizing, measured 2026-11-12 over 15 h** (working set, per container):

| Workload | avg | p95 | peak | request | limit |
|---|---|---|---|---|---|
| Prometheus | 1.26 GiB | 1.74 GiB | 1.76 GiB | 1Gi | 2Gi |
| Grafana | 292 MiB | 346 MiB | 400 MiB | 256Mi | 512Mi |
| Loki | 134 MiB | 166 MiB | 217 MiB | 256Mi | 1Gi |
| Fluent Bit (per pod) | 6-9 MiB | 9.8 MiB | 11.6 MiB | 32Mi | 64Mi |
| config-reloader | 9 MiB | 9.6 MiB | 9.9 MiB | 16Mi | 32Mi |

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
JSON envelope; unwrap it in the query (see below). The UI is at `/ui`.

**Grafana** — `http://grafana.kub.local`. `server.root_url` is set to that host
in the wrapper chart, because Grafana builds the Source and Silence links of
every Discord alert from it; leaving it unset produced
`http://localhost:3000/`. The API needs a bearer token from `$GRAFANA_TOKEN`,
exported by `~/.config/secrets/homelab.env`:

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

**Measured on 2026-11-13:** 121,431 head series, ~1,980 samples/s, 48 scrape
targets (all up). Series by job: `kubernetes-api-servers` 49 243,
`kubernetes-service-endpoints` 27 748, `kubernetes-nodes-cadvisor` 22 105,
`kubernetes-pods` 8 841, `kubernetes-nodes` 7 207, `rbp0` 1 343, `rbp1` 1 341,
`prometheus` 1 309. The `kubernetes-pods` growth is the exportarr sidecars and
the Plex, autobrr, n8n and qBittorrent exporters.

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

`streaming` needs the same treatment: it publishes no CPU package sensor (no
`k10temp` or `coretemp` chip), and its hottest `node_hwmon_temp_celsius` series
are `pci0000:00/0000:00:18.3` and a `wmi_bus` sensor at 86–89 °C. That is what
`NodeTemperatureHigh` matches, so the rule has been firing for it since
2026-11-13T01:18 with no CPU reading behind it. It needs a chip exclusion, the
way `nct6775` was excluded for `nas`.

**Volume stats are partial, and the reason is structural**: the K3s kubelet
publishes `kubelet_volume_stats_*` only for volumes whose plugin can measure
bytes — the NFS claims here. `local-path` is a hostPath directory, so its 17
claims (including Prometheus 25 GiB, Loki 20 GiB and Grafana 5 GiB) publish no
per-claim bytes at all. The panels show blank for them, and `PVCFillingUp` is
NFS-only by construction. Their capacity is still covered: each local-path
directory sits on a real filesystem (`/mnt/ssd0` on `nas`, `streaming` and
`rbp2`), and `DiskFillingUp` / `DiskWillFill` watch every filesystem. `/mnt/ssd0`
on `nas` was 5.2 % used on 2026-11-12.

The live `local-path-config` ConfigMap has a typo: its map entry is `nuc`, while
the node is `nuc0`. `nuc0` therefore falls back to
`/opt/local-path-provisioner` on its root filesystem instead of `/mnt/ssd0`.
Harmless today (the root disk is monitored), wrong if that node is meant to use
its SSD.

## Dashboards

Folder **Homelab**, created through the HTTP API:

| Dashboard | uid | Panels | Purpose |
|---|---|---|---|
| Homelab · Health | `homelab-health` | 12 | First look: four counts, pods and targets needing attention, crash loops, restarts, firing alerts, errors, disk |
| Homelab · Applications | `homelab-applications` | 9 | Debug one namespace: replicas, pods, CPU, memory, restarts, claims, dead exporters, recent errors |
| Homelab · Platform | `homelab-platform` | 15 | Nodes, control plane and Prometheus when the problem is not an app |
| Homelab · Logs | `homelab-logs` | 5 + rows | Volume, errors and warnings by container, log volume by node, live stream |
| Homelab · Disks | `homelab-disks` | 11 | SMART per drive: status, life, temperature, hours, sectors, wear |
| Homelab · Showcase | `homelab-showcase` | 13 | Presentation screen: drawn topology, four big numbers, network, storage, GPU |
| Homelab · Media | `homelab-media` | 8 + rows | Media stack: app health issues, queues, missing items, indexers, release intake, Plex sessions and library |

The old `homelab-investigate` was replaced by `homelab-platform` on 2026-11-12: its workload
row duplicated the new Applications dashboard, and its node/control-plane/Prometheus panels are
what Platform keeps. Tables use `labelsToFields` + `merge` + `organize`; a plain multi-query table
shows only the first frame and leaks Kubernetes metadata columns. Legend names come from the
`node` label, with `instance` mapped for `rbp0`/`rbp1` (they have no `node`).

Media panels name their series from `app_kubernetes_io_instance`, not `pod`, because a pod name
changes on every restart. `Playback bandwidth` is blank while nobody streams:
`plex_session_bandwidth_kbps` is only published during playback, and a zero would be a lie.

All seven have a 30 s–1 m refresh and a 6 h–24 h default range. They are not
backed up: Grafana's 5 GiB claim is the only copy. A `docker save` of the
dashboard JSON is not a backup either — the SQLite file is what would be lost.

## Alerting

Grafana unified alerting, folder `Homelab`, rule group `homelab-core`,
evaluation interval **60 s**. **27 rules**, one contact point, two routes by
severity.

**Delivery**: contact point `discord-homelab` (type `discord`).
`group_by [alertname, instance]`, `group_wait 30s`, `group_interval 5m`.
Warnings repeat every **12 h**; a child route matching `severity="critical"`
repeats every **1 h**. The webhook URL is not readable through the API (it is a
secure field), so the contact point is never rewritten by automation.

**Rule set and live state on 2026-11-13:**

| Rule | Condition | for | Severity | State |
|---|---|---|---|---|
| `NodeDown` | `1 - up` for the six node exporters | 5m | critical | inactive |
| `KubeNodeNotReady` | node Ready condition false | 5m | critical | inactive |
| `CoreDeploymentDown` | 0 available replicas in kube-system/monitoring/argocd | 10m | critical | inactive |
| `CoreStatefulSetDown` | ready < desired in those namespaces | 10m | critical | inactive |
| `CoreDaemonSetDown` | available < desired in those namespaces | 10m | critical | inactive |
| `KubeStateMetricsDown` | `absent(up{service=...} == 1)` | 5m | critical | inactive |
| `NodeFilesystemReadOnly` | filesystem mounted read-only | 5m | critical | inactive |
| `MonitoringDown` | `1 - up{job="prometheus"}`; `execErrState: Alerting` | 5m | critical | inactive |
| `DriveNeedsReplacement` | SMART failed, exit != 0, <10 % life, or pending sectors | 10m | critical | **firing** on `nas /dev/sdf` |
| `ScrapeTargetDown` | any non-node target down, including the edge Pis' SMART and Docker exporters; Prometheus and kube-state-metrics excluded (own rules) | 15m | warning | **firing, silenced** (qbittorrent-exporter only) |
| `NodeCpuHigh` | >90 % by host | 15m | warning | inactive |
| `NodeMemoryHigh` | >90 % of `MemAvailable` | 15m | warning | inactive |
| `NodeTemperatureHigh` | hottest real sensor >80 °C | 10m | warning | **firing** on `streaming` (sensor identity under review) |
| `GpuTemperatureHigh` | `DCGM_FI_DEV_GPU_TEMP` >80 °C | 10m | warning | inactive |
| `DiskTooHot` | drive >55 °C | 15m | warning | inactive |
| `DiskFillingUp` | filesystem >85 % | 30m | warning | inactive |
| `DiskWillFill` | predicted full in 24 h above 80 % used | 30m | warning | inactive |
| `PVCFillingUp` | claim >85 % (NFS claims only) | 30m | warning | inactive |
| `DiskReallocatingSectors` | any new sector in 1 h | 5m | warning | inactive |
| `RestartStorm` | 5+ restarts in an hour | 5m | warning | **firing, silenced** (the three long-broken apps) |
| `OOMKilled` | terminated by OOM in the last hour | 5m | warning | inactive |
| `JobFailed` | any Job with a failed pod, no success, and younger than 2 h | 5m | warning | inactive |
| `RecyclarrNotSucceeding` | no successful recyclarr run in 26 h | 5m | warning | inactive |
| `CertificateExpiring` | newest Traefik cert per SAN <21 days | 1h | warning | inactive |
| `PodCrashLooping` | CrashLoopBackOff | 15m | warning | **firing, silenced** ×3 |
| `PodPending` | Pending, created <2 h ago | 15m | warning | inactive |
| `LokiSilent` | no Fluent Bit lines in 10 min | 10m | warning | inactive |

**`JobFailed`** is the batch-workload rule. Its expression is
`((kube_job_status_failed > 0) unless on(namespace, job_name) (kube_job_status_succeeded > 0)) and on(namespace, job_name) ((time() - kube_job_status_start_time) < 7200)`:
it fires for a Job whose pod failed and whose retry did not succeed, and it
resolves by itself after two hours even if the failed Job object stays in the
history. That window is what keeps one bad night from firing for days.

**`RecyclarrNotSucceeding`** covers the other half: a run that never starts or
never finishes leaves no failure to alert on. It fires when
`kube_cronjob_status_last_successful_time` is more than 26 h old — one daily slot
plus two hours of slack — and resolves on the next successful run. It is scoped
to recyclarr because KSM 2.20 no longer publishes `kube_cronjob_spec_schedule`,
so a generic rule cannot know how often each CronJob should succeed.

`ScrapeTargetDown` excludes the Prometheus self-scrape and kube-state-metrics
targets; `MonitoringDown` and `KubeStateMetricsDown` own those two conditions, and
without the exclusion one dead component sent a critical and a warning at once. It
also covers the edge Pis' SMART (`:9902`) and Docker (`:9323`) exporters, which
`NodeDown` does not watch — that rule only sees their node exporter on `:9100`.
Those targets are static, so the alert names the instance IP rather than a host
label.

`noDataState` is `OK` on every rule **except `LokiSilent`**, which is `Alerting`
on no data: its expression returns an empty vector when no lines arrive, and that
is exactly the condition it must report. `MonitoringDown` and `LokiSilent` also
raise on evaluation errors (`execErrState: Alerting`), so a Prometheus restart or
a dead Loki produces one message rather than none.

**The rule pattern matters.** A PromQL comparison such as `up == 0` returns the
value `0` when true, and the threshold condition is "above 0" — so it can never
fire. Rules must return a **positive value when firing**: `1 - up`,
`replicas - ready`, `0 - predict_linear(...)`, `1 - kube_node_status_condition`,
or an explicit `> 85` / `> 55` / `> 80` inside the expression. The original set
had `NodeDown` and `MonitoringDown` silently dead this way, and
`DriveNeedsReplacement` survived only because the failing drive also reported
`exit_code = 32`.

**`DiskReallocatingSectors`** uses a subquery,
`delta(max by (...)(metric)[1h:]) > 0`. The previous form without the colon was
invalid PromQL and never evaluated.

**Silences** (each has an expiry and a comment):

- Until 2026-12-12: `pod=~"ghostfolio-.*|wiki-app-.*|monitorss-bot-.*"` — three
  apps crash-looping since the Longhorn loss. They still show `firing`; only the
  notification is suppressed.
- Until 2026-12-12: `alertname="DriveNeedsReplacement"` with the `nas /dev/sdf`
  labels — the drive is known dead (`docs/audit/ansible-hosts.md`) and waiting on
  a replacement. Only the notification is suppressed; the rule still fires.

The `alertname="ScrapeTargetDown", namespace="media-stack"` silence was **removed
on 2026-11-13**, together with its narrow `pod=~"qbittorrent-exporter-.*"`
revision: the exporter was replaced and now answers, the rule has no match, and
only the notification was ever suppressed. A media exporter that breaks from now
on will notify.

A silence changes notification delivery, not alert state. A silenced rule still
reports `firing` on the Health dashboard.

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

## Current health facts (2026-11-13)

- 6 hosts: `nas` (.41), `nuc0` (.27), `streaming` (.46), `rbp2` (.48), `rbp0`
  (.22), `rbp1` (.38). All answer.
- 132 pods known, 112 Running, 11 Pending, 1 Failed, 8 Completed.
- 36 PVCs; 12 are stuck `Terminating` from Longhorn.
- 11 Pending, none younger than the 2-hour `PodPending` window, so the alert is
  correctly inactive. The pending set is W0's cleanup, not monitoring's.
- All 48 scrape targets answer on 2026-11-13. The rebuilt `radarr`,
  `radarr-anime`, `lidarr`, `sonarr`, `sonarr-anime` and `prowlarr` exporters
  answer with real data; `unpackerr` and the `bazarr` pair publish no target on
  purpose. `qbittorrent-exporter` now runs
  `ghcr.io/martabal/qbittorrent-exporter:v2.0.4` on port 8090 and publishes 58
  `qbittorrent_*` names; the caseyscarborough v1.3.5 image it replaced could not
  log in to qBittorrent 5.2 at all. `ScrapeTargetDown` is inactive and its
  media-stack silence is gone.
- `nas /dev/sdf` (Crucial BX500, holds `local-path`, therefore Prometheus, Loki,
  Grafana and every config volume) is at **0 % life left**, SMART exit code 32.
  It needs replacing; `DriveNeedsReplacement` fires for it and is silenced until
  2026-12-12.

## Known gaps

- **`PVCFillingUp` is blind for `local-path` claims** (25 of 36 claims publish
  volume stats, mostly NFS). The node filesystem rule covers the same disk, at a
  coarser grain.
- **No backup-freshness metric.** Backups do not run today, so an alert would
  either never fire or lie.
- **No blackbox probe, no certificate check outside Traefik.** A public hostname
  that resolves but serves nothing is not detected.
- **No GPU XID/ECC alert.** The NVIDIA exporter on this cluster does not publish
  those families; only temperature, utilisation and frame-buffer use exist.
- **Application coverage is limited to pods, restarts, core namespaces and
  batch jobs.** `CoreDeploymentDown` covers kube-system, monitoring and argocd
  only. A broken app in another namespace shows up through `PodCrashLooping`,
  `PodPending` or `RestartStorm`, not through a replica check. `JobFailed`
  covers every Job, but only `recyclarr` has per-schedule staleness coverage;
  the other CronJobs fail loudly but a run that never starts is silent.
- **`NodeTemperatureHigh` may be reading a non-CPU sensor on `streaming`.** On
  2026-11-13 it fires at 83–91 °C on two ACPI/WMI zones while node CPU is about
  20 % and the GPU reports 40 °C. Identified, not yet resolved.
- **Alerting is a single point of failure**: Grafana evaluates and delivers. If
  Grafana, Prometheus or `nas` goes down, no message is sent. The operator
  accepts this risk (watchdog declined on 2026-11-12).
- No metrics for the NFS provisioners or the Pi-hole resolver itself (only its
  exporter).
- Dashboards and rules are not in git and are not backed up.

## Changing things

- Dashboards: `POST /api/dashboards/db` with `{dashboard, folderUid: "homelab",
  overwrite: true}`. Read one with `GET /api/dashboards/uid/<uid>`.
- Alert rules: `GET/POST /api/v1/provisioning/alert-rules`, groups under
  `/api/v1/provisioning/folder/homelab/rule-groups/homelab-core`. Delivery is
  `/api/v1/provisioning/contact-points` and `/api/v1/provisioning/policies`.
- Testing a contact point: use its **Test** button. Grafana 13 has no working
  HTTP test route for a receiver created through the provisioning API — the
  legacy paths return 404, and the `/apis/notifications.alerting.grafana.app`
  route answers "Receiver not found" because the webhook lives in the legacy
  store and its URL is a redacted secure field.
- Validate every PromQL expression against Prometheus before saving it. The
  `DiskReallocatingSectors` bug is what skipping this costs.
- Never `kubectl apply` a dashboard or rule; nothing in the repo produces them.
  Never `kubectl` at all on this workstation: the harness blocks it and the MCP
  account is read-only. Use the HTTP APIs above.
