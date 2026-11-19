# Metrics: what the exporters publish and what was measured

> **In one line.** The metric and log side of the `monitoring` stack: the traps in each exporter, what
> Prometheus and Fluent Bit throw away before storing, how long data is kept, and the numbers taken
> from the live cluster on 2026-11-19.
>
> **Use it when** a panel is blank or a metric reads wrong, before trusting a series or volume count
> or a retention claim, or before proposing a new metric · **Verified** 2026-11-19 · **Kind:** living page

The overview — what runs where, its sizing, and how to query the stack:
[`observability-conventions.md`](../observability-conventions.md).

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

**Fluent Bit** writes the message and nothing else. Since 2026-11-18 the Loki
output carries `Remove_Keys kubernetes,stream,time,_p` and `Drop_Single_Key raw`,
so a text log arrives as the app's own message, unquoted, and a pod that logs
JSON arrives as that JSON. Labels and structured metadata are read from the
record *before* the line is packed — `pack_labels()` runs ahead of
`pack_record()` in the output plugin — so `namespace`, `container`, `node` and
`pod` all still resolve. **No query unwraps anything.**

Measured on 2,000 lines the day it changed: the message was 26 % of the line;
63 % was the `kubernetes` block (pod labels 258 B, annotations 134 B, container
hash 98 B, docker id 66 B) repeating what Loki already holds as stream labels;
and 9 % was the CRI `time`, `stream` and `_p` fields, duplicating the timestamp
and the container name.

Labels are `job=fluent-bit`, `namespace`, `container`, `node`; the pod name is
structured metadata, not a label. Loki computes `detected_level` from the line
it stores, so `{job="fluent-bit"} | detected_level="error"` keeps working on
both shapes: it reads a `level` field when the line is JSON, and sniffs the text
otherwise.

**The media stack's exporters** — the `exportarr` sidecars on `sonarr`,
`sonarr-anime`, `radarr`, `radarr-anime`, `lidarr` and `prowlarr`, plus
`qbittorrent-exporter`, the Plex sidecar and FlareSolverr's built-in exporter.
Measured 2026-11-18: ten targets, 979 series, of which the Media board read 15
names. Eight of the ten now carry `prometheus.io/scrape_slow` and are scraped
every 5 minutes; Prometheus drops 731 series at the scrape (see the comment on
`kubernetes-pods` in `cluster/helm/monitoring/prometheus/values.yaml`). What a
person needs to know to add a panel:

- **One series per thing, in four of these exporters.** `lidarr_artists_genres_total`
  is a series per genre (224); Prowlarr publishes ten families per indexer, 20
  indexers each; qBittorrent writes one series per category (17) and per tag
  (34); Plex writes one per library item for its top-item, watch-age,
  resolution and codec families. Those are the dropped ones. The per-app
  totals (`*_missing_total`, `*_queue_total`, `*_system_health_issues`,
  `plex_library_items`, `plex_library_storage_bytes`, the four
  `qbittorrent_global_*` ones the board reads) are single series and stay.
- **`exportarr` logs one line per API call at INFO**, and a scrape is a dozen
  calls: 6–14 MB a day per sidecar, the largest log writer in the namespace
  after `unpackerr`. The sidecar sets `LOG_LEVEL=warn`; a stale API key still
  shows there as a 500 on `/metrics`, which is what the target's `up` reports.
- **`plex_session_bandwidth_kbps` exists only while something is playing**, and
  it is per session (`session`, `user`, `location` labels), which is why the
  Media board's playback panel is blank at idle. It is not a broken exporter.
- **FlareSolverr keeps its exporter** even though no panel reads it: its
  `LOG_LEVEL=warning` costs the per-request log lines, and
  `flaresolverr_request_total{result="solved"}` is the only numeric record
  left of what the solver did. 15 series, scraped every 5 minutes.

**qBittorrent** (`martabal/qbittorrent-exporter`, metrics `qbittorrent_*`):
qBittorrent 5.2 answers `/api/v2/auth/login` with `204 No Content` and sets a
`QBT_SID_<webui port>` cookie, so the exporter needs
`QBITTORRENT_COOKIE_NAME=QBT_SID_8080` in this cluster. The Java exporter it
replaced (`caseyscarborough` v1.3.5) expects `200` and a cookie named `SID` and
cannot log in at all. Tracker collection costs one Web API call per distinct
tracker — about 40 here — which pushed the collection past Prometheus's 10 s
scrape timeout on 2026-11-13; the chart turns it off.

**MikroTik switches** (`ghcr.io/akpw/mktxp`, metrics `mktxp_*`, on `nuc0`):

- Two devices, and their labels disagree on purpose. `routerboard_name` is the
  model (`CR5326` for the CRS326, `CRS309`); `name` on
  `mktxp_system_identity_info` is the router's own identity string — `CR326`, a
  typo in the device config, and the untouched default `MikroTik`. Chart by
  `routerboard_name` or map `routerboard_address` to a name; never by `name`.
- The CRS326 (`192.168.1.11`) publishes 26 ports with status (27 interfaces with
  the bridge) and one populated SFP+ (`sfp-sfpplus2`, copper DAC). The CRS309
  (`192.168.1.43`) publishes 9 ports with status and four SFP+ (`sfp-sfpplus1`
  is 10G-SR at 850 nm; the other three are copper DACs). 838 series on
  2026-11-18: 576 and 262.
- Throughput is `mktxp_interface_{rx,tx}_byte_total`, a counter; `_rate` is the
  negotiated **link speed** in Mbit/s, not bytes moved. Errors and drops have
  their own counters, and `mktxp_link_downs_total` counts link transitions, so
  a flapping cable shows there and not in the error counters.
- Optical health is three families: `mktxp_interface_sfp_rx_loss` and
  `_tx_fault` are 0/1 per port, `mktxp_interface_sfp_info` carries vendor, part
  number and connector type as labels, and `mktxp_interface_sfp_wavelength`
  exists only for optics that report one. A blank wavelength is correct for a
  copper DAC, not a missing scrape.
- An unreachable device publishes no `up == 0`; its series simply stop. That is
  why the `SwitchDown` rule uses `absent()` per address instead of `1 - up`.
- `mktxp_interface_comment_info` is empty here: neither switch has port
  descriptions, so a port table cannot show a human label for a cable.
- `mktxp` publishes no semver tags on ghcr: only `latest`, `stable-<timestamp>`
  (last built 2024-08-21) and `gha-<run>`. Pinning the GitHub release name
  `v2.1.0` produced `ImagePullBackOff` on 2026-11-18 and took both switches
  offline, so the chart keeps `latest` with `pullPolicy: Always`. Pinning this
  image means pinning a digest; it is not done yet.
- Ports with no cable report `mktxp_interface_status == 0`. That is normal on
  both switches (17 of the CRS326's 26 ports and 5 of the CRS309's 9), so the
  alert rules watch SFP faults and link flaps, never a bare "port down".

**Self-monitoring**: Loki and Grafana publish their own `/metrics`, scraped by
two static jobs added on 2026-11-18. Without them the stack watched every
application but not itself: `loki_request_duration_seconds_count` on the push
route is the only evidence that Fluent Bit writes are being rejected, and
`grafana_alerting_rule_evaluation_failures_total` is the only evidence that the
single alert evaluator stopped evaluating rules. Both jobs publish histogram
buckets that Prometheus drops at the scrape (see
[below](#what-prometheus-throws-away-before-storing)); the counters the rules
read are kept.

**Temperature**: `node_hwmon_chip_names` carries the driver name for each hwmon
`chip`, and the `chip` label itself is a device path — `k10temp` on `streaming`
appears as `pci0000:00_0000:00:18_3`, which looks like a motherboard sensor and
is not one. `NodeTemperatureHigh` and the Platform panel therefore select by
driver, not by path:

```
node_hwmon_temp_celsius
  and on(instance, chip)
node_hwmon_chip_names{chip_name=~"k10temp|coretemp|zenpower|cpu_thermal"}
```

That reads the CPU package on the three x86 nodes (`coretemp` on `nas` and
`nuc0`, `k10temp` on `streaming`) and the SoC on the three Pis
(`cpu_thermal`), and skips `nct6796` (the NAS's unconnected header, 95 °C),
`acpitz` (16.8 °C on `streaming`, meaningless), `gigabyte_wmi`, `nvme` and
`iwlwifi_1`.

`streaming` is genuinely hot: `Tctl` 81–91 °C at 10–28 % CPU on 2026-11-13, and
the reading follows load. `NodeTemperatureHigh` fires for it and that is
correct — the fix is cooling, not a wider or narrower selector. Per-host maxima
on 2026-11-13: `nas` 43, `nuc0` 44, `streaming` 82, `rbp2` 54, `rbp0` 50,
`rbp1` 51.

Both x86 nodes that run hot are accepted faults with a dated record rather than
a tunable threshold:
[`decisions/2026-11-13-streaming-overheating-deferred.md`](../decisions/2026-11-13-streaming-overheating-deferred.md)
(`streaming`, cooling deferred for budget) and
[`decisions/2026-11-13-nuc0-throttling-accepted.md`](../decisions/2026-11-13-nuc0-throttling-accepted.md)
(`nuc0`, 95 °C peak and 2 005 hardware throttle events in the 39 h to
2026-11-13, already repasted, no alert on purpose). Read those before filing a
new temperature finding.

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

## What Prometheus throws away before storing

A scrape returns everything an exporter publishes; Prometheus stores what something reads. Since
2026-11-18 ten `metric_relabel_configs` entries in `cluster/helm/monitoring/prometheus/values.yaml`
— one regex widened, nine added — drop the rest at the scrape, so those series never reach the
head, the WAL or the SSD. Every family below was checked against the eight dashboards' PromQL and
the 35 alert rules' expressions first; none of them appears in either.

| Job | Dropped | Series | Why it has no reader |
|---|---|---|---|
| `kubernetes-api-servers` | every histogram bucket (`apiserver_*`, `etcd_*`, `workqueue_*`, `scheduler_*`, `storage_operation_*`, `kubelet_*`, `rest_client_*`) | 28,964 | the Platform panel divides `_sum` by `_count`; a bucket is never touched |
| `kubernetes-nodes` | the same treatment for the kubelet's own histograms | 4,932 | no panel reads a kubelet duration; `kubelet_volume_stats_*` is kept |
| `kubernetes-nodes-cadvisor` | 17 `container_*` families (`tasks_state`, `memory_failures_total`, `memory_failcnt`, `memory_max_usage_bytes`, `memory_mapped_file`, `memory_swap`, `memory_kernel_usage`, `memory_cache`, `blkio_device_usage_total`, `fs_reads_total`, `fs_writes_total`, `cpu_load_average_10s`, `file_descriptors`, `processes`, `sockets`, `threads`, `threads_max`) | 10,058 | the Applications board reads CPU and memory only; the identity and limit families stay |
| `kubernetes-service-endpoints` | `kube_secret_*`, `kube_pod_tolerations`, `kube_replicaset_*`, `kube_pod_status_qos_class` | 6,156 | one series per object: 421 Secrets, and the replica panels read Deployments and StatefulSets |
| `kubernetes-pods` | `qbittorrent_torrent_*` (17 fields × 365 torrents), `n8n_*_bucket` and `traefik_*_bucket` | 9,393 | the Media board reads the four `qbittorrent_global_*` totals |
| `grafana`, `loki`, `prometheus` | each job's own histogram buckets | 6,968 | the two stack rules read counters, which are kept |

`kubernetes-pods-slow` and `kubernetes-service-endpoints-slow` carry the same two rules as their
fast twins, and that is not hypothetical: on 2026-11-18 the media-stack pods carrying
`prometheus.io/scrape_slow` — the `*arr` exporters among them — are scraped only by the slow pod
job, at a 5-minute cadence, because the fast job drops a pod with that annotation outright. None of
them publishes a dropped family, so those rules remove nothing there; they exist so that a pod
carrying both annotations cannot bring the families back by the side door.

**Measured on 2026-11-18, before the drop:** 151,103 head series, 2,230 samples/s, p95 working set
1,456 MiB. **Two hours after** (config reloaded 21:18Z, measured 23:22Z): **73,835 head series, 1,151
samples/s**, and a working set that has not moved — 1,195 MiB p95 over the half hour after the change
against 1,185 MiB over the 24 hours before it, with the Go heap in use going from 964 to 899 MiB.

That is the honest result: the drop halves what is stored and written, and does not fix the memory.
Prometheus requests 1 GiB and uses about 1.2; removing 72,000 series moved the Go heap by 7 %, so the
head was not what filled the process. The request is a sizing question with its own method
([`chart-sizing.md`](chart-sizing.md)), not a scrape-config one; do not expect a drop rule to buy
memory here.

Two things this page teaches by being measured rather than assumed:

- **`scrape_samples_scraped` is not a series count.** It reports what the target exposed, before
  these rules run, so it kept showing the dropped families after the drop was live. Count what is
  stored with `count by (job) ({__name__!=""})` or `prometheus_tsdb_head_series`.
- **A regex that matches nothing looks exactly like one that works** in the rendered config. Only
  the head can tell the difference:

```bash
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=count({job="kubernetes-nodes-cadvisor", __name__=~"container_(tasks_state|threads|file_descriptors)"})'
# no result = the drop is working; a number = the regex matches nothing
```

## Storage and retention

| Volume | Claim | Size | Retention |
|---|---|---|---|
| Prometheus TSDB | `monitoring/prometheus-server` | 25 GiB | `90d`, hard cap `20GiB` (`retention.size`) |
| Grafana database | `monitoring/grafana` | 5 GiB | n/a |
| Loki chunks + index | `monitoring/storage-loki-0` | 20 GiB | `720h` via the compactor |

**Loki fills slowly.** Measured on 2026-11-19 over a clean half hour
(02:00–02:30Z, no pod starting inside it): about **0.09 GB/day** pushed before
compression, at **19×**, so roughly **5 MB/day** and **0.14 GB** for the full
30-day retention against a 20 GiB claim. The line fell from **1,013 to 207
bytes**. Before the change the same six-hour average was 1.55 GB/day and a busy
hour reached 2.24 GB/day, so the pipeline writes **17 to 25 times less** than it
did.

Quote a settled window and say which one: six minutes after the last restart the
same query read 0.09 GB/day for a different reason (the stack was not warm), and a
half hour that happened to contain two pod restarts read 0.11 GB/day on startup
bursts alone. What is left of the 0.09 GB/day is mostly a symptom, not chatter:

- **authentik, 22 MB/day** — request logs at `info`, one line per request
  (server 12, worker 10), including the health probes the kubelet sends.
- **argocd, 18 MB/day** — all but 1 MB of it repo-server repeating the two
  manifest errors for paths that do not exist, `cluster/helm/nfs/plex-media` and
  `cluster/helm/nfs/audio` (#383). The server joined the controllers at `warn` on
  2026-11-19 and fell from 12.9 MB/day to 1.
- **monitoring, 14 MB** — `prometheus-smartctl`, which is talking about the
  failing `nas` drive.
- **kube-system, 6 MB** — traefik errors, now only `koillection`: it has a claim
  and a running neighbour but crash-loops on a database host that does not exist
  (#386).
- **vikunja, 5 MB** — request logs at `info`, again including health probes.
- **n8n, 3 MB** — the `Pruning failed` loop stopped when the app was rebuilt on
  `local-path`; what is left is a telemetry retry that cannot connect.

The throughput limit is 4 MB/s against an average of about 1 KB/s, so the
ingestion limits are not the constraint either. What the line looked like before,
and why: the [Fluent Bit note above](#exporter-quirks). The panels that show
these numbers are in [`dashboards.md`](dashboards.md).

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
- `streaming` runs hot: `k10temp` reads **Tctl 81–91 °C** at 10–28 % CPU on
  2026-11-13, and the value follows load, so `NodeTemperatureHigh` firing for it
  is correct. The `gigabyte_wmi` zone mirrors Tctl; the GPU is at 40 °C and the
  NVMe at 46–57 °C. Check fans and airflow — the alert is telling the truth.
