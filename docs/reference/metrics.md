# Metrics: what the exporters publish and what was measured

> **In one line.** The metric side of the `monitoring` stack: the traps in each exporter, how long
> data is kept, and the numbers taken from the live cluster on 2026-11-13.
>
> **Use it when** a panel is blank or a metric reads wrong, before trusting a series count or a
> retention claim, or before proposing a new metric · **Verified** 2026-11-17 · **Kind:** living page

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

**Fluent Bit** writes `Line_Format json`, so every Loki line is an envelope
(`time`, `stream`, `log`, and a ~1 KB `kubernetes` block of labels, annotations
and image hashes). Log panels unwrap it in the query:

```
{job="fluent-bit"} | json | line_format "{{if .log}}{{.log}}{{else}}{{__line__}}{{end}}"
```

Labels are `job=fluent-bit`, `namespace`, `container`, `node`; the pod name is
structured metadata, not a label.

**qBittorrent** (`martabal/qbittorrent-exporter`, metrics `qbittorrent_*`):
qBittorrent 5.2 answers `/api/v2/auth/login` with `204 No Content` and sets a
`QBT_SID_<webui port>` cookie, so the exporter needs
`QBITTORRENT_COOKIE_NAME=QBT_SID_8080` in this cluster. The Java exporter it
replaced (`caseyscarborough` v1.3.5) expects `200` and a cookie named `SID` and
cannot log in at all. Tracker collection costs one Web API call per distinct
tracker — about 40 here — which pushed the collection past Prometheus's 10 s
scrape timeout on 2026-11-13; the chart turns it off.

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
- `streaming` runs hot: `k10temp` reads **Tctl 81–91 °C** at 10–28 % CPU on
  2026-11-13, and the value follows load, so `NodeTemperatureHigh` firing for it
  is correct. The `gigabyte_wmi` zone mirrors Tctl; the GPU is at 40 °C and the
  NVMe at 46–57 °C. Check fans and airflow — the alert is telling the truth.
