# Alerting redesign — design

2026-11-12. Status: operator said proceed. This replaces the rule set created earlier the same
day after reviewing it against live data.

## 1. Why

Discord is the operator's only channel for a homelab they do not watch daily. The current 13
rules look reasonable but several **cannot fire**:

- `NodeDown`, `MonitoringDown`: the expression is `up == 0`, which returns the value `0`; the
  rule's threshold is "above 0", so the condition is false exactly when the target is down.
- `DriveNeedsReplacement`: same defect for the `smart_passed == 0` term. It fires today only
  because `/dev/sdf` also has `exit_code = 32`, which returns a positive value.
- `DiskReallocatingSectors`: invalid PromQL (`[1h]` on an aggregated expression), so it errors
  and never fires.
- `PVCFillingUp`: only 25 of 36 claims publish `kubelet_volume_stats_*`; `local-path` claims are
  invisible, and that is where Prometheus, Loki and Grafana live.
- Nothing reports a **scrape target being down**. Seven media-stack exporters have been dead for
  weeks without a message.
- No alert covers a node not ready, a core deployment at zero replicas, a read-only filesystem,
  a certificate about to expire, a restart storm or the log pipeline going silent.

## 2. Goals and non-goals

**Goals**

1. Every rule fires when its condition is true, proven against the live expression.
2. One message names the host or workload, the value and the action.
3. Critical and warning are routed differently: a critical repeats hourly, a warning twice a day.
4. Noise is handled by silences with an expiry date, not by weakening a rule.
5. The set stays small enough to read: 25 rules in one group.

**Non-goals**

- A watchdog outside Grafana. The operator accepts the risk that a dead Grafana or NAS is silent.
- Alertmanager. Grafana unified alerting stays.
- Backup-freshness alerts: backups do not run today, so there is no honest metric.
- Blackbox probes and synthetic HTTP checks.

## 3. The pattern that must not repeat

A rule expression must return a **positive value when and only when the condition is true**.
Comparisons like `up == 0` or `x == 1` return `0` or `1`; with a threshold of "above 0", the
`== 0` case is silently dead. Write `1 - up` for absence, `replicas - ready` for shortfall,
`0 - predict_linear(...)` for a negative predictor, and `1 - kube_node_status_condition` for a
false condition.

## 4. Rule set

| Rule | Expression shape | Severity | for |
|---|---|---|---|
| NodeDown | `1 - up` for six hosts, host label mapped | critical | 5m |
| KubeNodeNotReady | `1 - kube_node_status_condition{Ready}` | critical | 5m |
| CoreDeploymentDown | `1 - replicas_available` in kube-system/monitoring/argocd | critical | 10m |
| CoreStatefulSetDown | `replicas - ready` in the same namespaces | critical | 10m |
| CoreDaemonSetDown | `desired - available` in the same namespaces | critical | 10m |
| KubeStateMetricsDown | `absent(up{service="prometheus-kube-state-metrics"})` | critical | 5m |
| NodeFilesystemReadOnly | `node_filesystem_readonly == 1` (returns 1) | critical | 5m |
| MonitoringDown | `1 - up{job="prometheus"}`; `execErrState: Alerting` | critical | 5m |
| DriveNeedsReplacement | `1 - smart_passed`, exit != 0, life < 10, pending > 0 | critical | 10m |
| ScrapeTargetDown | `1 - up`, node exporters excluded | warning | 15m |
| NodeCpuHigh | > 90 % by host | warning | 15m |
| NodeMemoryHigh | > 90 % by host | warning | 15m |
| NodeTemperatureHigh | hottest real sensor > 80 °C | warning | 10m |
| GpuTemperatureHigh | > 80 °C | warning | 10m |
| DiskTooHot | drive > 55 °C | warning | 15m |
| DiskFillingUp | filesystem > 85 % | warning | 30m |
| DiskWillFill | predicted full in 24 h above 80 % used | warning | 30m |
| PVCFillingUp | claim > 85 % (NFS claims only) | warning | 30m |
| DiskReallocatingSectors | `delta(...[1h:]) > 0` | warning | 5m |
| RestartStorm | `increase(restarts[1h]) >= 5` | warning | 5m |
| OOMKilled | terminated by OOM in the last hour | warning | 5m |
| CertificateExpiring | newest Traefik cert per SAN < 21 days | warning | 1h |
| PodCrashLooping | CrashLoopBackOff | warning | 15m |
| PodPending | Pending, created < 2 h ago | warning | 15m |
| LokiSilent | no Fluent Bit lines in 10 min; `noDataState`/`execErrState` Alerting | warning | 10m |

`NodeTemperatureHigh` moves from `node_thermal_zone_temp` to
`node_hwmon_temp_celsius{chip!~".*nct6775.*"}`: the thermal zones read 16.8 °C on `streaming` and
0 °C on two Pis, and the NAS `nct6775 temp6` reads 95 °C on an unconnected header.

## 5. Delivery and routing

One contact point (`discord-homelab`) stays as it is; the API redacts its webhook URL, so it is
not rewritten. Default policy `group_by [alertname, instance]`, `group_wait 30s`,
`group_interval 5m`. A child route on `severity="critical"` repeats every hour; warnings every
12 hours. Every rule carries `summary` (host/workload and value) and `description` (what to do).

## 6. Silence

The three crash-looping apps (`ghostfolio`, `wiki`, `monitorss-bot`) keep their existing 30-day
silence. A new 14-day silence covers `job="kubernetes-pods", namespace="media-stack"` while the
seven exporters are repaired by a separate job; without it every evaluation would notify. Both
silences have an expiry date and a comment saying when to remove them.

## 7. Risks

- **A rule with a wrong shape is worse than no rule**, because it looks like coverage. Every new
  expression is run against Prometheus first and its value checked for the firing case.
- **ScrapeTargetDown is noisy by nature.** It is the rule that would have caught the seven dead
  exporters, so it stays; the silence is temporary.
- **OOMKilled depends on `kube_pod_container_status_last_terminated_timestamp`**, which only
  exists after a container has terminated. A cluster with no terminations returns no series and
  the rule stays quiet, which is correct.
- **Certificate metric includes historical certs.** Use `max by (sans)` so an expired copy in
  Traefik's store does not fire while the current certificate is valid.

## 8. Verification

1. Run every expression through Prometheus; any non-success is a defect.
2. Confirm each rule's state after two evaluation intervals: `health=ok` for all, no `Error`.
3. Send one synthetic alert through Alertmanager and confirm the Discord message.
4. Confirm the silenced rules show `firing` but produce no notification.
5. Record the final set in `docs/observability-conventions.md`.
