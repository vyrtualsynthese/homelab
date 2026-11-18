# Observability load review — design

2026-11-18. Status: awaiting operator approval. Stage 1 of three; stages 2 and 3 are shaped here and
designed when they are reached.

## 1. What this is, and why

The monitoring stack is the largest consumer on `nas`: Prometheus alone holds 1.46 GiB at its p95 and
writes to the `local-path` disk whose SMART life is at 0 %. It got there by collecting whatever the
targets published. Nothing has ever asked which of those numbers a panel or a rule reads.

Measured on 2026-11-18 against the live cluster, before any change:

| Component | Load | p95 working set | Request | Limit |
|---|---|---|---|---|
| Prometheus | 151,103 head series, 2,230 samples/s | 1,456 MiB | 1Gi | 2Gi |
| Loki | 1.6 GB/day, 1.55 M lines/day | 377 MiB | 256Mi | 1Gi |
| Grafana | 35 rules @60 s, 8 dashboards | 391 MiB | 256Mi | 768Mi |
| Fluent Bit | 4 pods | 37 MiB each | 32Mi | 64Mi |

Three findings follow from that measurement, and each becomes one stage:

1. **66,391 of the 151,103 series (44 %) are written and never read.** No dashboard and no alert rule
   reads a histogram bucket on the control-plane jobs, 17 of the 76 cAdvisor families, the
   kube-state-metrics Secret/ReplicaSet/Toleration families, the qBittorrent per-torrent families, or
   the observability stack's own `/metrics` histograms.
2. **Three quarters of every log line is packaging, and 37 % of the lines are the stack talking to
   itself.** Fluent Bit writes a ~1 KB `kubernetes` block around a 90–580 byte message that the labels
   already describe; Argo CD's notifications controller, the repo-server health probe, Grafana's
   plugin logger, Loki's housekeeping and one hand-applied log loop account for 578k of 1.55 M daily
   lines.
3. **The alerting and dashboard load is small but untuned.** 35 rules share one 60-second group
   whatever their window (`SomethingDown` needs 60 s, `CertificateExpiring` looks at a 21-day
   horizon), and five of eight dashboards refresh every 30 s.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Stop writing series that no reader consumes, and prove each one has no reader before dropping it.
2. Cut the log volume the stack produces and ships, without losing a line somebody would open.
3. Keep every existing panel and every existing rule working, unchanged, through stage 1.
4. Make each stage measurable on `develop` by itself: series count, sample rate, memory, disk.

**Non-goals**

- What is monitored. No new exporter, no new alert, no new panel in this work.
- Retention policy. 90 days / 20 GiB and 30 days stay as they are.
- Moving alerting out of Grafana, or adding a watchdog.
- Re-sizing requests and limits. That is a separate pass with its own method
  ([`chart-sizing.md`](../reference/chart-sizing.md)); the numbers above are inputs to it, not part of
  this design.

**Anti-goals**

- A panel that renders blank because a family was dropped. A blank panel is indistinguishable from a
  broken exporter, so it is worse than the load.
- A log query shape that changes without every panel that uses it changing in the same commit.
- Reducing load by weakening a rule or widening a threshold.

## 3. Constraints

- Scraping is annotation-based only: no Prometheus Operator, no `ServiceMonitor`. Every change goes
  through `cluster/helm/monitoring/prometheus/values.yaml` → `prometheus.scrapeConfigs`, which
  already carries the two control-plane drop rules this design extends.
- Wrapper charts, pattern D. One ArgoCD Application per component; no Application carries
  `syncPolicy.automated`, deliberately.
- Dashboards and rules live in Grafana's database, not in git, and are changed only through the HTTP
  API ([`alerts.md`](../reference/alerts.md#changing-things)).
- A drop is verified by asking the live APIs which metric names the dashboards and the rules
  reference, not by reading them by eye.

## 4. Approaches

### Metrics

- **A. Drop at the scrape** (`metric_relabel_configs`). One file, reversible, visible in the config
  that Prometheus serves at `/api/v1/status/config`, and it costs nothing when a family comes back.
- **B. Turn collectors off at the source** (KSM `--resources`, cAdvisor paths). Saves the producer's
  CPU as well, but the KSM collector list is 20-odd entries that must be kept in step with three
  dashboards, and a name that mistypes silently removes a family the next audit will not find.
- **C. Lengthen the scrape interval.** Cuts the sample rate without dropping a name, but changes the
  resolution of every panel and every `rate()` behind a rule.

**Recommended: A.** B's extra saving is 50 MiB of kube-state-metrics, against a config that fails
quietly; C is a real lever, but it is a per-panel decision and belongs after A, if the sample rate is
still a problem. C stays on the table for the two control-plane jobs only.

### Logs

- **A. Quiet the producers.** Argo CD's notifications controller, its repo-server health probe,
  Grafana's plugin logger and Loki's housekeeping run at `info`; the lines are per-request chatter. No
  query changes.
- **B. Remove the envelope.** Copy the four fields the labels need into top-level keys, drop the
  `kubernetes` block, ship the message. 60–87 % of the bytes, but every log panel and the documented
  `| json | line_format` idiom change with it.
- **C. Filter or sample lines in Fluent Bit.** Cheapest to write, and it deletes evidence nobody can
  get back.

**Recommended: A, then B in the same stage, with the panels updated in the same commit.** C is out: a
log pipeline that silently drops lines is the failure mode this stack exists to catch.

### Alerting

- **A. Split the rule group by cadence**: one group at 60 s for the conditions that must page quickly,
  one at 5 m for the ones whose `for` is already 10–30 minutes.
- **B. Raise the interval on each slow rule individually.** Same saving, 20 places to get wrong.

**Recommended: A.**

## 5. Stage 1 — the metric drops, in detail

Every family below was checked against the eight dashboards' PromQL and the 35 rules' expressions,
pulled from the Grafana API. None of them appears in either.

| Job | Family | Series | Reader today |
|---|---|---|---|
| `kubernetes-api-servers` | `etcd_request_duration_seconds_bucket` | 28,892 | none; `_sum`/`_count` stay and the Platform panel uses those |
| `kubernetes-api-servers` | `workqueue_*_bucket` | 3,278 | none |
| `kubernetes-api-servers` | `scheduler_plugin_execution_duration_seconds_bucket` | 1,218 | none |
| `kubernetes-api-servers` | `storage_operation`, `kubelet_runtime_operations`, `rest_client` `_bucket` | 2,254 | none |
| `kubernetes-nodes` | `kubelet_*`, `storage_operation`, `volume_operation`, `kubeproxy_*` `_bucket` | 4,932 | none; `kubelet_volume_stats_*` and every `_sum`/`_count` stay |
| `kubernetes-nodes-cadvisor` | 17 unused `container_*` families (`tasks_state`, `memory_failures_total`, `memory_failcnt`, `memory_max_usage_bytes`, `memory_mapped_file`, `memory_swap`, `memory_kernel_usage`, `memory_cache`, `blkio_device_usage_total`, `fs_reads_total`, `fs_writes_total`, `cpu_load_average_10s`, `file_descriptors`, `processes`, `sockets`, `threads`, `threads_max`) | 10,058 | the Applications board reads `container_cpu_usage_seconds_total` and `container_memory_working_set_bytes` only |
| `kubernetes-service-endpoints` | `kube_secret_*`, `kube_pod_tolerations`, `kube_replicaset_*`, `kube_pod_status_qos_class` | 6,148 | none; Deployments and StatefulSets carry the replica panels |
| `kubernetes-pods` | `qbittorrent_torrent_*` | 6,240 | the Media board reads four `qbittorrent_global_*` totals |
| `kubernetes-pods` | `qbittorrent_torrent_*`, `n8n_*_bucket` (three families), `traefik_*_bucket` (two) | 9,393 | none |
| `grafana`, `loki`, `prometheus` | each job's own `_bucket` families | 6,968 | the rules read counters (`loki_request_duration_seconds_count`, `grafana_alerting_rule_evaluation_failures_total`) |
| **Total** | | **66,391** | 44 % of head series, ~1,107 of 2,230 samples/s |

The shape of the change is ten `metric_relabel_configs` entries: one regex replaced on
`kubernetes-api-servers`, and nine added. Two of the nine are on `kubernetes-pods-slow` and
`kubernetes-service-endpoints-slow` and drop nothing today — no target carries
`prometheus.io/scrape_slow` on 2026-11-18 — they exist so that one annotation added later cannot
resurrect the same families through the slow jobs. The existing comment above `kubernetes-api-servers` says the job is "half
the series in this cluster … almost all of it histogram buckets that no dashboard reads"; stage 1
finishes that sentence rather than starting a new policy.

**Why the counts can be trusted.** Each is `count by (__name__)` for its job from
`/api/v1/query`, not an estimate. The reader check is `/api/v1/label/__name__/values` plus the
dashboards' and rules' expressions, also from the API.

**What is deliberately kept**, though it has no reader today: `kube_persistentvolume_status_phase`
(240 series, and `pv` states are part of the Longhorn cleanup), the cAdvisor families that describe
identity and limits (`container_last_seen`, `container_start_time_seconds`,
`container_spec_memory_limit_bytes`, `container_oom_events_total`), and every `_sum`/`_count` beside a
dropped bucket, because an average latency is what makes a bucket worth having later.

## 6. What is left to the builder

- The exact regex per job. One drop rule per job listing the families is easier to read than one
  combined regex, and Prometheus applies them in order.
- Whether the two control-plane jobs also move to a 2-minute interval once the drop lands, measured.
  Not part of stage 1's acceptance.
- The commit split. One commit per job, or one for the whole config, is a matter of review taste; the
  renders must pass either way.

## 7. Verification

Before and after, from the APIs, recorded on the card:

1. `prometheus_tsdb_head_series` and `sum by (job) (scrape_samples_scraped)/60`.
2. `prometheus_tsdb_head_chunks_storage_bytes`, `prometheus_tsdb_storage_blocks_bytes`.
3. `quantile_over_time(0.95, container_memory_working_set_bytes{...}[1d])` for the Prometheus pod.
4. `count(up)` — 51 targets, all up, none lost to a relabel typo.
5. Every query behind the two panels that read the affected jobs (`container_cpu_usage_seconds_total`,
   `container_memory_working_set_bytes`, `kubelet_volume_stats_*`, the etcd/apiserver latency panels,
   the four `qbittorrent_global_*` panels) executed against Prometheus and returning rows.
6. `helm template` for the chart, `hack/check-exposure.sh`, and the CRLF-safe reference check from
   `AGENTS.md` §6.

A drop that turns a panel blank is a defect: the render is not the test, the query result is.

## 8. Stages 2 and 3, shaped not designed

**Stage 2 — the log pipeline.** Quiet the five producers (`argocd-notifications-controller` and
`argocd-repo-server` log level, Grafana's `log.level=warn`, Loki's `log_level=warn`, and a decision on
the hand-applied `loop-device-cleaner`, which is drift and not in any chart), then remove the Fluent
Bit envelope and update the log panels and
[`metrics.md`](../reference/metrics.md#exporter-quirks) in the same change. Target: 1.6 GB/day →
under 0.5 GB/day.

**Stage 3 — Grafana and alerting.** Split `homelab-core` into a 60-second group and a 5-minute group,
and move five dashboards from 30 s to 1 m. Small in absolute terms; it costs one API call per object
and removes the reason a TV left on a dashboard competes with the alert evaluator.

## 9. What this supersedes

Nothing. It extends the two drop rules already in
`cluster/helm/monitoring/prometheus/values.yaml` and the sizing record in
[`chart-sizing.md`](../reference/chart-sizing.md) without contradicting either.
