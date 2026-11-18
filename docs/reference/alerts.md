# Alerts: the rules, the silences and the gaps

> **In one line.** Every alert rule in Grafana's `homelab-core` group, what each one protects, why
> five silences exist, the pattern a rule must follow to fire at all, and the holes nobody watches.
>
> **Use it when** an alert fired and you want to know what it means, you are writing or editing a
> rule, or you are asking whether something is monitored at all · **Verified** 2026-11-17 · **Kind:**
> living page

The overview — what runs where, its sizing, and how to query the stack:
[`observability-conventions.md`](../observability-conventions.md).

## Alerting

Grafana unified alerting, folder `Homelab`, rule group `homelab-core`,
evaluation interval **60 s**. **29 rules**, one contact point, two routes by
severity.

**Delivery**: contact point `discord-homelab` (type `discord`).
`group_by [alertname, instance]`, `group_wait 30s`, `group_interval 5m`.
Warnings repeat every **12 h**; a child route matching `severity="critical"`
repeats every **1 h**. The webhook URL is not readable through the API (it is a
secure field), so the contact point is never rewritten by automation. The Discord
webhook exists only in that contact point.

**Rule set and live state on 2026-11-13:**

| Rule | Condition | for | Severity | State |
|---|---|---|---|---|
| `NodeDown` | `1 - up` for the six node exporters | 5m | critical | inactive |
| `KubeNodeNotReady` | node Ready condition false | 5m | critical | inactive |
| `CoreDeploymentDown` | 0 available replicas in kube-system/monitoring/argocd | 10m | critical | inactive |
| `CoreStatefulSetDown` | ready < desired in those namespaces | 10m | critical | inactive |
| `CoreDaemonSetDown` | available < desired in those namespaces | 10m | critical | inactive |
| `MediaDeploymentDown` | a `media-stack` Deployment at 0 available | 10m | warning | inactive |
| `KubeStateMetricsDown` | `absent(up{service=...} == 1)` | 5m | critical | inactive |
| `NodeFilesystemReadOnly` | filesystem mounted read-only | 5m | critical | inactive |
| `MonitoringDown` | `1 - up{job="prometheus"}`; `execErrState: Alerting` | 5m | critical | inactive |
| `DriveNeedsReplacement` | SMART failed, exit != 0, <10 % life, or pending sectors | 10m | critical | **firing** on `nas /dev/sdf` |
| `ScrapeTargetDown` | any non-node target down, including the edge Pis' SMART and Docker exporters; Prometheus and kube-state-metrics excluded (own rules) | 15m | warning | inactive |
| `NodeCpuHigh` | >90 % by host | 15m | warning | inactive |
| `NodeMemoryHigh` | >90 % of `MemAvailable` | 15m | warning | inactive |
| `NodeTemperatureHigh` | hottest CPU or SoC sensor, selected by hwmon driver >80 °C | 10m | warning | **firing** on `streaming` |
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
| `PodPending` | Pending, created <2 h ago | 15m | warning | **firing** on `databases/pgadmin-67c5f5c85` (missing Secret, 2026-11-14) |
| `PodNotStarting` | Scheduled, container still not started, older than 2 h | 5m | warning | **firing, silenced** (the abandoned `nfs-ssd` provisioner) |
| `LokiSilent` | no Fluent Bit lines in 10 min (`noDataState: Alerting`) | 10m | warning | inactive |

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

**`PodPending` and `PodNotStarting` split one symptom by cause.** `Pending` covers
two different faults: a pod that was never scheduled (unbound claim, node selector,
resource request) and a pod that was scheduled but whose container never started
(image pull, volume that will not mount, missing Secret). `kube_pod_status_phase`
cannot tell them apart, and the first version of `PodPending` asserted the first
cause in its message — which sent the investigation of `pgadmin` looking at claims
and node selectors while the real fault was a missing `pgadmin-oidc` Secret.
`PodPending` keeps the broad half: anything Pending in its first two hours,
`for: 15m`, and a message that now names both causes. `PodNotStarting` takes over
at the two-hour mark with no upper bound:

```
(kube_pod_status_phase{phase="Pending"} == 1)
  and on(namespace, pod) (kube_pod_status_scheduled{condition="true"} == 1)
  and on(namespace, pod) ((time() - kube_pod_created) > 7200)
  unless on(namespace, pod) kube_pod_deletion_timestamp
```

`PodScheduled` is what separates "never placed" from "never started", and `unless
kube_pod_deletion_timestamp` keeps a pod that is waiting on its own deletion out of
the count. The two rules hand over at exactly 7200 s, so a pod stays covered without
a gap and without two messages for one fault. A pod that is **unschedulable** is
still only covered for its first two hours: the six pods left unschedulable by the
Longhorn loss have sat that way for 72 days, and any rule without that ceiling would
page for them continuously. Deleting those workloads is what closes that gap, not a
wider expression.

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

- Until 2027-03-01: `host="streaming"`, `severity="warning"`,
  `__alert_rule_uid__="cg433wfzcwiyob"` — the node runs at its thermal ceiling and
  the cooling fix is deferred
  ([`decisions/2026-11-13-streaming-overheating-deferred.md`](../decisions/2026-11-13-streaming-overheating-deferred.md)).
  The rule still evaluates, still reports `firing`, and still shows on the Health
  dashboard.
- Until 2027-03-01: the same matchers with `host="nuc0"` — the chassis is too small
  for its CPU, the condition is accepted
  ([`decisions/2026-11-13-nuc0-throttling-accepted.md`](../decisions/2026-11-13-nuc0-throttling-accepted.md)),
  and the action it would ask for (a new node) is already the plan.
  `NodeTemperatureHigh` is one host-agnostic rule, so a silence per host beats
  narrowing the expression.
- Until 2026-12-12: `alertname="DriveNeedsReplacement"` with the `nas /dev/sdf`
  labels — the drive is known dead
  ([`docs/audit/ansible-hosts.md`](../audit/ansible-hosts.md)) and waiting on a
  replacement. Only the notification is suppressed; the rule still fires.
- Until 2026-12-12: `pod=~"nfs-ssd-nfs-subdir-external-provisioner-.*"` — this pod
  has been unable to mount `192.168.1.41:/mnt/ssd0/nfs` since 2026-09-03, because
  that export does not exist on `nas`. It belongs to no chart and no ArgoCD
  Application; it is hand-applied drift, and `PodNotStarting` would report it
  every few hours forever. Fix the export or delete the workload, then remove the
  silence.

The `alertname="ScrapeTargetDown", namespace="media-stack"` silence was **removed
on 2026-11-13**, together with its narrow `pod=~"qbittorrent-exporter-.*"`
revision: the exporter was replaced and now answers, the rule has no match, and
only the notification was ever suppressed. A media exporter that breaks from now
on will notify.

A silence changes notification delivery, not alert state. A silenced rule still
reports `firing` on the Health dashboard.

## Known gaps

- **`PVCFillingUp` is blind for `local-path` claims.** The rule is NFS-only by
  construction; why the metric cannot exist for a `local-path` claim is in
  [`metrics.md`](metrics.md#exporter-quirks). The node filesystem rule covers the
  same disk, at a coarser grain.
- **No backup-freshness metric.** Backups do not run today, so an alert would
  either never fire or lie.
- **No vulnerability scanning.** Nothing reports which CVEs affect the running
  images, so there is no metric to alert on. Design and cost are recorded in
  [`superpowers/specs/2026-11-14-vulnerability-alerting-design.md`](../superpowers/specs/2026-11-14-vulnerability-alerting-design.md);
  it is deferred, not built.
- **No blackbox probe, no certificate check outside Traefik.** A public hostname
  that resolves but serves nothing is not detected.
- **No GPU XID/ECC alert.** The NVIDIA exporter on this cluster does not publish
  those families; only temperature, utilisation and frame-buffer use exist.
- **Application coverage is limited to pods, restarts, `media-stack` and the
  core namespaces.** `CoreDeploymentDown` covers kube-system, monitoring and
  argocd; `MediaDeploymentDown` covers `media-stack` with no exclusions. One
  exclusion was retired on 2026-11-13 when the app got a real claim and a running
  pod, and a second was dropped when its abandoned app was deleted instead of
  rebuilt. A broken app in any other namespace shows up through
  `PodCrashLooping`, `PodPending`, `PodNotStarting` or `RestartStorm`, not through a replica
  check. `JobFailed` covers every Job, but only `recyclarr` has per-schedule
  staleness coverage; the other CronJobs fail loudly but a run that never starts
  is silent.
- **qBittorrent's `cross-seed` alias is a literal ClusterIP.** The pod keeps
  `dnsPolicy: None`, so `hostAliases` maps `cross-seed` to `crossSeedServiceIP`
  in the chart. If that Service is recreated with a new IP, the completion hook
  fails silently; re-check the value.
- **Alerting is a single point of failure**: Grafana evaluates and delivers. If
  Grafana, Prometheus or `nas` goes down, no message is sent. The operator
  accepts this risk (watchdog declined on 2026-11-12).
- No metrics for the NFS provisioners or the Pi-hole resolver itself (only its
  exporter).
- Dashboards and rules are not in git and are not backed up.

## Changing things

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
