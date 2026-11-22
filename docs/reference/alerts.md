# Alerts: the rules, the silences and the gaps

> **In one line.** Every alert rule in Grafana's two `homelab-core` groups, what each one protects,
> why five silences exist, the pattern a rule must follow to fire at all, and the holes nobody
> watches.
>
> **Use it when** an alert fired and you want to know what it means, you are writing or editing a
> rule, or you are asking whether something is monitored at all · **Verified** 2026-11-22 · **Kind:** reference
>
> **Sources** the live rules, read from Grafana's API on 2026-11-22 ·
> `cluster/helm/monitoring/grafana/values.yaml`, `docs/observability-conventions.md`

The overview — what runs where, its sizing, and how to query the stack:
[`observability-conventions.md`](../observability-conventions.md).

## Alerting

Grafana unified alerting, folder `Homelab`, **two rule groups split by cadence
on 2026-11-18**: `homelab-core-fast` at **60 s** (21 rules) and `homelab-core` at
**300 s** (28 rules). **49 rules**, one contact point, two routes by severity.

The split rule is the `for` duration: a rule sits in the fast group when its `for`
is 5 minutes or less, or when it reports that something is down and its `for` is
10 minutes (`NodeDown`, `KubeNodeNotReady`, `MonitoringDown`,
`KubeStateMetricsDown`, `CoreDeploymentDown`, `CoreStatefulSetDown`,
`CoreDaemonSetDown`, `MediaDeploymentDown`, and since 2026-11-20
`CloudEndpointDown` and `CloudDnsCheckFailing`) — `ScrapeTargetDown` joins them
because its whole job is noticing that data stopped arriving. Everything else is
checked every five minutes: `CertificateExpiring`, `CloudTlsCertificateExpiring` and
`RestoreDrillOverdue` (`for: 1h`), the three disk-fill predictions (`for: 30m`), the temperature and
SLO-ish checks (`for: 10m` and above). The slowest ratio is the `for: 10m` rules in
`homelab-core`: half their own `for` between checks, so an alert is delayed by up to five
minutes and never by more than that. Measured at 35 rules: 35.0 rule evaluations per minute
before the split, 20.5 after.

**Delivery**: contact point `discord-homelab` (type `discord`).
`group_by [alertname, instance]`, `group_wait 30s`, `group_interval 5m`.
Warnings repeat every **12 h**; a child route matching `severity="critical"`
repeats every **1 h**. The webhook URL is not readable through the API (it is a
secure field), so the contact point is never rewritten by automation. The Discord
webhook exists only in that contact point.

**Rule set and live state on 2026-11-13 (the three `Cloud*` rows read back on
2026-11-20, the counts and `GpuNotAllocatable` on 2026-11-22, and
`ArgoAppRenderFailing` added and proved on 2026-11-22, the two `Postgres*` rows
added on 2026-11-22):**

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
| `GpuNotAllocatable` | allocatable `nvidia.com/gpu` reads 0 on a node that registered the card | 10m | warning | inactive |
| `DiskTooHot` | drive >55 °C | 15m | warning | inactive |
| `DiskFillingUp` | filesystem >85 % | 30m | warning | inactive |
| `DiskWillFill` | predicted full in 24 h above 80 % used | 30m | warning | inactive |
| `PVCFillingUp` | claim >85 % (NFS claims only) | 30m | warning | inactive |
| `DiskReallocatingSectors` | any new sector in 1 h | 5m | warning | inactive |
| `RestartStorm` | 5+ restarts in an hour | 5m | warning | **firing, silenced** (the three long-broken apps) |
| `OOMKilled` | terminated by OOM in the last hour | 5m | warning | inactive |
| `JobFailed` | any Job with a failed pod, no success, and younger than 2 h | 5m | warning | inactive |
| `ArgoAppRenderFailing` | an Application carrying a `ComparisonError` — Argo CD could not render it, so it no longer reports sync state | 10m | warning | inactive |
| `RecyclarrNotSucceeding` | no successful recyclarr run in 26 h | 5m | warning | inactive |
| `BackupStreamStale` | a CronJob in namespace `backup` whose last success is more than 26 h old | 10m | warning | inactive |
| `BackupJobAbsent` | a backup CronJob with no `kube_cronjob_status_last_successful_time` series, so it is gone or has never succeeded | 5m | warning | inactive |
| `PostgresDumpStale` | the `databases/postgres-dump` CronJob's last success is more than 26 h old | 10m | warning | inactive |
| `PostgresDumpAbsent` | no `kube_cronjob_status_last_successful_time` series for `databases/postgres-dump`, so it is gone or has never succeeded | 5m | warning | inactive |
| `RestoreDrillOverdue` | newest restore-drill timestamp older than 90 days | 1h | warning | inactive |
| `FluentBitDroppingLogs` | output batches abandoned after retries, or records dropped from the logger queue | 10m | warning | inactive |
| `CertificateExpiring` | newest Traefik cert per SAN <21 days | 1h | warning | inactive |
| `TraefikRouterErrorRate` | 5xx share above 10 % per router, with a 0.01 req/s floor so one failed probe on an idle router cannot fire it | 10m | warning | inactive |
| `TraefikRouterLatencyHigh` | p95 above 1 s per router, same request floor | 10m | warning | inactive |
| `TraefikConfigReloadFailing` | any reload failure in 15 min, or no successful reload in 24 h | 10m | warning | inactive |
| `PodCrashLooping` | CrashLoopBackOff | 15m | warning | **firing, silenced** ×3 |
| `PodPending` | Pending, created <2 h ago | 15m | warning | **firing** on `databases/pgadmin-67c5f5c85` (missing Secret, 2026-11-14) |
| `PodNotStarting` | Scheduled, container still not started, older than 2 h | 5m | warning | inactive |
| `LokiSilent` | no Fluent Bit lines in 10 min (`noDataState: Alerting`) | 10m | warning | inactive |
| `SwitchDown` | `absent()` of the identity metric for either switch | 5m | critical | inactive |
| `SwitchSfpDegraded` | SFP+ receive loss or transmit fault | 10m | warning | inactive |
| `SwitchTemperatureHigh` | switch CPU >80 °C | 15m | warning | inactive |
| `SwitchLinkFlapping` | >5 link-down events in 1 h on a port | 10m | warning | inactive |
| `LokiWriteFailing` | sustained 5xx on the Loki push route | 10m | warning | inactive |
| `GrafanaAlertEvaluationFailing` | sustained rule-evaluation errors | 10m | warning | inactive |
| `CloudEndpointDown` | `1 - probe_success{job="blackbox-http"}` — a public name answered something other than 200 | 5m | critical | inactive |
| `CloudDnsCheckFailing` | `1 - probe_success{job="blackbox-dns"}` — a zone's check failed at one authoritative server | 10m | critical | inactive |
| `CloudTlsCertificateExpiring` | days to the earliest public certificate, firing below 21 | 1h | warning | inactive |

**`JobFailed`** is the batch-workload rule. Its expression is
`((kube_job_status_failed > 0) unless on(namespace, job_name) (kube_job_status_succeeded > 0)) and on(namespace, job_name) ((time() - kube_job_status_start_time) < 7200)`:
it fires for a Job whose pod failed and whose retry did not succeed, and it
resolves by itself after two hours even if the failed Job object stays in the
history. That window is what keeps one bad night from firing for days.

**`ArgoAppRenderFailing`** (2026-11-22) is the rule for an Application Argo CD cannot render at all.
Its expression is `argocd_app_condition{condition="ComparisonError"} > 0`: the gauge's value is the
count of conditions of that type, so the series exists only while the condition does, and the
threshold is simply "above 0". The message names the Application and its project, which is what the
alternative — the share of failed repo-server requests, the card's other suggestion — cannot do.

The reason this is not a rule on the log line is measured. Since card 643 the `found in Chart.yaml,
but missing in charts/` line is known to be routine: 789 of them in the 24 hours to 2026-11-22, each
one followed by a `helm dependency build` and a successful second render **inside the same request**.
That is why the repo-server recorded zero non-`OK` gRPC codes between its 04:00 start and the
measurement, and why a failed-request ratio would have been blind to the line while telling nobody
which Application broke. The condition is
the fault itself, and Argo CD sets it only when generation fails.

The metric is not free: the controller publishes no condition at all until
`controller.metrics.application.conditions` names the types to publish, which
`cluster/helm/argocd/values.yaml` now sets to `ComparisonError`. Its labels are `namespace`, `name`,
`project` and `condition`. The proof on the card: a throwaway Application pointing at a path that
does not exist went `pending` → `firing`, the summary named it, and deleting it cleared the rule;
`for: 10m` is what keeps a render that fails once and succeeds on the retry quiet.

**`RecyclarrNotSucceeding`** covers the other half: a run that never starts or
never finishes leaves no failure to alert on. It fires when
`kube_cronjob_status_last_successful_time` is more than 26 h old — one daily slot
plus two hours of slack — and resolves on the next successful run. It is scoped
to recyclarr because KSM 2.20 no longer publishes `kube_cronjob_spec_schedule`,
so a generic rule cannot know how often each CronJob should succeed.

**`BackupStreamStale`** (2026-11-20) is the same condition for the backup jobs, and it can be
generic because everything it watches lives in one namespace and runs daily:
`(time() - kube_cronjob_status_last_successful_time{namespace="backup"}) > 93600`. 93600 s is 26 h,
one daily slot plus two hours. It covers `backup-node-nas`, `backup-node-nuc0`,
`backup-node-streaming` and `backup-nfs`; `backup-prune` and `backup-verify` are in the same namespace
and are watched by the same rule, which is intended — a verifier that stops running is exactly the
silence this rule exists to break. Its `for` is 10 minutes rather than 5, which is what keeps it in
`homelab-core`: the split rule puts a 5-minute `for` in the fast group, and a nightly job gains
nothing from a 60 s evaluation. The summary names `{{ $labels.cronjob }}` rather than a stream
label because kube-state-metrics does not publish CronJob labels on
`kube_cronjob_status_last_successful_time`; `backup-node-nas` is the job, `nodes-nas` is the stream,
and the mapping is in the chart. The verifier has no rule of its own for that reason: a verifier whose
last success is 26 hours old is already firing this one.

A stale backup and a *failing* backup are different faults, and only the second one reaches `JobFailed`
(the pod failed). A job whose pod starts and exits non-zero is both — `JobFailed` within its two-hour
window and this rule 26 h later — but a job that never runs at all, because the schedule stopped or
the sync removed it, only reaches this rule. That is the case worth having: a backup repository that
nobody writes to looks exactly like a healthy one until the day it is needed.

**`BackupJobAbsent`** (2026-11-22) covers the third shape of the same silence, and the one
`BackupStreamStale` cannot see. That rule compares the last-success timestamp against 26 hours;
when the CronJob is deleted, renamed, or has never once succeeded, kube-state-metrics publishes no
`kube_cronjob_status_last_successful_time` series at all, and a threshold over a series that does not
exist stays green. One `absent()` term per job turns the missing series into a labelled alert: the
summary names the job, and a chart deleted altogether arrives as one notification carrying six
instances rather than six messages. It watches all six jobs — the four nightlies, `backup-verify`
and `backup-prune` — and is gated on kube-state-metrics answering —
`and on() (up{service="prometheus-kube-state-metrics"} == 1)` — because a dead kube-state-metrics
makes every term match at once, and `KubeStateMetricsDown` is the critical message for that morning.

`backup-prune` was the last term to arrive, on 2026-11-22. Its first scheduled run, that Sunday at
noon, failed: `restic` refused the `nfs` repository because the laptop read test earlier that day had
left a lock behind, so the job had never recorded a success. An `absent()` term added before that
first success would have fired on day one, which is the false page the delay existed to avoid. The
lock was cleared by hand, a manual run exited 0 on all four repositories, and the sixth term went in
the same evening.

**`PostgresDumpStale` and `PostgresDumpAbsent`** (2026-11-22) close the same hole one namespace over,
around the job that produces the only backup of every database on the instance. Until that day the
two rules above watched namespace `backup` alone, so they saw the restic jobs and not
`databases/postgres-dump`; `JobFailed` only fires when a pod fails, and a CronJob that stops being
created has no pod at all. The failure that shape hides is slow and quiet: the claim directory stays,
so every row still resolves and every stream still succeeds, a little smaller each night, and the
14-day retention takes the last dump without anything saying so. The stale rule is the arithmetic of
`BackupStreamStale` against that one job — `(time() -
kube_cronjob_status_last_successful_time{namespace="databases",cronjob="postgres-dump"}) > 93600` —
and the absent rule is its `absent()` twin in the fast group. `backup-verify` carries the content
half: the `postgres-dumps` row declares `mustMatch: ["*.dump"]`, so the newest `nodes-nas` snapshot
must hold a dump under that claim, and a row that resolves to an empty directory fails the run naming
the row.

**`RestoreDrillOverdue`** (2026-11-22) is the only rule whose input is a hand-written date. The drill
log in [`../runbooks/restore.md`](../runbooks/restore.md) is prose in a repository, so the dates it
holds are rendered by the `prometheus` chart into a ConfigMap that every node-exporter pod mounts
read-only; the textfile collector serves them as
`homelab_restore_drill_timestamp_seconds{stream,drill,date}`, one series per row of that table. The
rule takes the newest row (`topk(1, …)`) and compares its age against **90 days** — three missed
monthly drills — in the **threshold**, not in PromQL: a false PromQL comparison returns an empty
vector, and this rule's `noDataState` is `Alerting`, so that shape would page on every recent drill.
A missing ConfigMap, a renamed metric or an evaluation error is exactly the silence it reports, and
that is why its no-data state is the opposite of every other rule here. Its `for` is 1 h because the
input changes once a month, so a short gate buys nothing. The message names the newest drill's date
and stream, so a firing rule reads as "the last drill was on this date", not as an anonymous stale
counter. **`[no value]` in that message means the log could not be read at all** — the ConfigMap or
the metric is missing — so the fault is the exporter, not a missed drill; the description says so,
and the proof is on card #406. When two drills share a UTC date, either stream may be named:
`topk(1, …)` breaks a tie arbitrarily, and the alert reports the newest *date*, not one stream.

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

**Six rules added on 2026-11-18 watch data the cluster already had but nobody
alerted on.**

`SwitchDown` cannot use `1 - up`: mktxp is one exporter for two devices, and an
unreachable device simply stops publishing its series.
The rule wraps `absent()` once per `routerboard_address` and labels each term
with the switch name. That also covers mktxp itself dying: a vanished scrape
target produces no `up == 0` to alert on, so this rule is the only one that
notices. The three port rules read the same 838 series: `SwitchSfpDegraded`
fires on the 0/1 `rx_loss`/`tx_fault` bits, `SwitchTemperatureHigh` at 80 °C
(the fanless CRS326 sits in the high sixties, so 80 is the fault line, not the
normal), and `SwitchLinkFlapping` only when a port moves more than five
link-down events in an hour. A port with no cable is normal on both switches
(17 of the CRS326's 26 ports) and is never alerted on.

`LokiWriteFailing` and `GrafanaAlertEvaluationFailing` watch the observability
stack itself, which needed two scrape jobs that did not exist before that day.
Both use a 5-minute rate rather than `increase`, so a single rejected write or
one bad evaluation is not a page; `LokiSilent` remains the rule for total
silence.

`FluentBitDroppingLogs` (2026-11-22) is the third component's own counter: Fluent
Bit publishes on `fluent-bit.monitoring.svc:2020`, and the rule sums, over ten
minutes, the output batches abandoned after retries and the records dropped from
its logger queue. Both counters were unscraped until then, so the pipeline that
ships every line Loki stores could lose lines while every measurement was taken
from what did arrive.

`noDataState` is `OK` on every rule **except `LokiSilent`** and `RestoreDrillOverdue`, which are
`Alerting` on no data: `LokiSilent`'s expression returns an empty vector when no lines arrive, and
`RestoreDrillOverdue`'s metric disappears with its ConfigMap, which is exactly the condition each
must report. `MonitoringDown` and `LokiSilent` also raise on evaluation errors (`execErrState:
Alerting`), so a Prometheus restart or a dead Loki produces one message rather than none;
`RestoreDrillOverdue` does the same, because a rule that cannot read the log must not report health.

**The rule pattern matters.** A PromQL comparison such as `up == 0` returns the
value `0` when true, and the threshold condition is "above 0" — so it can never
fire. Rules must return a **positive value when firing**: `1 - up`,
`replicas - ready`, `0 - predict_linear(...)`, `1 - kube_node_status_condition`,
or an explicit `> 85` / `> 55` / `> 80` inside the expression. The original set
had `NodeDown` and `MonitoringDown` silently dead this way, and
`DriveNeedsReplacement` survived only because the failing drive also reported
`exit_code = 32`.

**`GpuNotAllocatable` watches the scheduler resource, not the card.** The card stays usable through
the `nvidia` runtime class after a restart, so `GpuTemperatureHigh` and Plex are unaffected and
`nvidia-smi` still lists it; only `nvidia.com/gpu` on the node drops to 0, and a pod that requests
it stays `Pending`. The expression is `1 - kube_node_status_allocatable{resource="nvidia_com_gpu"}`
— kube-state-metrics rewrites the dots in the resource name to underscores, the series exists only
on a node whose device plugin registered the card, and the `nvidia.com/gpu.present` node label is
not available: kube-state-metrics adds node labels only for names in its
`--metric-labels-allowlist`, and none is configured (adding one is a non-goal). The rule is what
makes the accepted workaround safe:
[`runbooks/gpu-operator.md`](../runbooks/gpu-operator.md#the-gpu-drops-to-0-after-a-restart).

**`DiskReallocatingSectors`** uses a subquery,
`delta(max by (...)(metric)[1h:]) > 0`. The previous form without the colon was
invalid PromQL and never evaluated.

**Three rules added on 2026-11-20 watch the internet-facing side** — the three public names,
both DNS zones and the certificates a stranger is served. They read a `blackbox_exporter` on
`nas`, whose two static jobs, 3 + 28 targets and public-resolver choice are in
[`observability-conventions.md`](../observability-conventions.md#how-metrics-get-in). Their
procedure, including how to add a target: [`runbooks/cloud-monitoring.md`](../runbooks/cloud-monitoring.md).

`CloudTlsCertificateExpiring` is the one rule here whose value is **not** "higher means worse": its
value is the number of days remaining and it fires **below 21**, so a reader comparing it with its
neighbours — all of which return a positive number when firing — should expect the opposite shape.
It stays a warning at 21 days, matching `CertificateExpiring`, and lives in `homelab-core` because a
certificate is not an outage.

`CloudDnsCheckFailing` asserts the records mail needs (MX, SPF, DKIM, DMARC) at **every**
authoritative server the zone publishes, because a dead secondary is invisible to a probe that only
asks the primary. `dns_ns_ashuvidz` is the exception: it asserts only that `ashuvidz.com`'s
delegation answers, not which provider answers it, because the zone is mid-transfer from AWS Route 53
to OVHcloud — asserting either provider today would be throwaway work or a guaranteed false page.
The runbook owns the target-list edit for the day of the flip.

Because all three are `noDataState: OK`, a selector that stops matching looks exactly like a healthy
rule: the proof they still look at something is `count(probe_success{job="blackbox-http"})` = 3 and
`count(probe_success{job="blackbox-dns"})` = 28. A dead prober is the one case where that silence is
intended, and [`runbooks/cloud-monitoring.md` §8](../runbooks/cloud-monitoring.md#8-known-limits)
explains why.

**Three rules added on 2026-11-21 watch the router in front of everything.** They came out of the
Traefik audit ([`audit/traefik-2026-11-21.md`](../audit/traefik-2026-11-21.md), card 542) and are the
first rules that read the router's own traffic rather than the workload behind it. `CertificateExpiring`
was the only Traefik rule before them.

- **`TraefikRouterErrorRate`** is the 5xx share per router. The ratio is built inside the PromQL and
  the 10 % comparison is inside the expression too, because the condition only tests "above 0" —
  a rule whose expression returns the raw ratio would fire on every router with any traffic, the trap
  under *The rule pattern matters*. The `0.01` request-per-second floor (0.6 requests a minute) is
  what keeps a single failed health probe on an idle internal router out of the page: below the floor
  the router is excluded from the result entirely rather than reported as 100 % failed.
- **`TraefikRouterLatencyHigh`** is a p95 above 1 s, per router. Measured the day it was written: the
  busiest router averaged **76 ms**, so 1 s is an order of magnitude above normal and catches a
  backend that is queueing rather than slow by a little. The threshold is **provisional** by design:
  before 2026-11-21 Prometheus dropped `traefik_.*_bucket`, so no percentile existed to tune against,
  and the number was chosen the same evening the buckets first arrived. Re-read the panel after a
  week. Traefik itself adds microseconds, so a firing here is the app behind the route, not the
  router.
- **`TraefikConfigReloadFailing`** covers two faults that look the same from outside. A reload
  failure in the last 15 minutes is a **dynamic** configuration Traefik read and refused — a
  `Middleware` or `IngressRoute` it cannot resolve — and Traefik goes on serving the last good
  configuration, so the routes still answer and the log line is the only evidence. No successful
  reload for 24 h is the other shape: the provider delivered nothing for a day. That is not a quiet
  night here — routes change several times an hour — so it means a dead watch or a router stuck
  mid-build.

  It does **not** cover the two faults the audit found in 2023, and the record should say so: a Helm
  values file that fails to render never reaches Traefik (that is the deploy step's error, and until
  2026-11-21 it failed silently; item 9 of `AGENTS.md` §7), and the `/metrics` endpoint answering `404`
  (card 531) leaves this rule with no data, which `noDataState: OK` reports as healthy. `ScrapeTargetDown`
  is what watches for a scrape target that answers nothing.

None of the three carries a `TraefikDown` twin, and none needs one: the `Deployment` is in
`kube-system`, so `CoreDeploymentDown` covers a router that stops running, and `ScrapeTargetDown`
covers the pod's `:9100` scrape going quiet while the pod still answers.

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
- **Removed 2026-11-19 — the silence on `nfs-ssd` can go.** Until then one silence read
  `pod=~"nfs-ssd-nfs-subdir-external-provisioner-.*"`: that pod could not mount
  `192.168.1.41:/mnt/ssd0/nfs` because the export did not exist on `nas`, it belonged to no chart
  and no ArgoCD Application, and `PodNotStarting` reported it every few hours. The workload was
  uninstalled with `helm uninstall nfs-hdd nfs-ssd -n default` and its class is gone, so delete the
  silence in Alertmanager rather than renewing it.

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
- **Backup freshness is covered, backup *contents* are covered for one row only.** `BackupStreamStale`
  says a stream stopped advancing, `BackupJobAbsent` says a job is gone or never succeeded,
  `PostgresDumpStale` and `PostgresDumpAbsent` say the same about the dump producer, and
  `JobFailed` says a run failed. The verifier adds one content check, because the dumps are the only
  copy of every database: the `postgres-dumps` row fails the night when the newest `nodes-nas`
  snapshot holds no `*.dump` under it. No rule proves a file restores — the
  drills in [`../runbooks/restore.md`](../runbooks/restore.md) are the only proof of that: three
  read-only restores on 2026-11-21, the destructive put-back onto Sonarr's live claim and a read of
  all four repositories from a laptop on 2026-11-22; the etcd rehearsal is still untried.
  `RestoreDrillOverdue` makes the habit fail loudly when it stops, but it reads a date the operator
  writes, so it does not prove a drill happened.
- **No vulnerability scanning.** Nothing reports which CVEs affect the running
  images, so there is no metric to alert on. Design and cost are recorded in
  [`superpowers/specs/2026-11-14-vulnerability-alerting-design.md`](../superpowers/specs/2026-11-14-vulnerability-alerting-design.md);
  it is deferred, not built.
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
- **A failed Argo CD sync is still unalerted.** `ArgoAppRenderFailing` watches
  comparison: an Application whose manifests cannot be generated. A sync operation that runs and fails
  leaves `operationState.phase: Failed` and is visible on the Applications dashboard, but Argo CD
  sets `SyncError` only in conditions the controller has been told to publish, and only
  `ComparisonError` is listed. Listing it would be a second rule with its own noise, and a failed
  sync is usually one Kubernetes refusal with the object and the message in the event — the dashboard
  is the better reader. The card's other candidate, a share of failed repo-server requests, is no
  longer available either: it would read `grpc_server_handled_total`, which is one of the families
  the scrape drops.
- **qBittorrent's `cross-seed` alias is a literal ClusterIP.** The pod keeps
  `dnsPolicy: None`, so `hostAliases` maps `cross-seed` to `crossSeedServiceIP`
  in the chart. If that Service is recreated with a new IP, the completion hook
  fails silently; re-check the value.
- **Alerting is a single point of failure**: Grafana evaluates and delivers. If
  Grafana, Prometheus or `nas` goes down, no message is sent. The operator
  accepts this risk (watchdog declined on 2026-11-12).
- **No metrics for the NFS provisioners or the Pi-hole resolver itself** (only its
  exporter).
- **Two switches are covered, and their identity strings are wrong.** The CRS326
  calls itself `CR326` and the CRS309 keeps the default `MikroTik`, so the rules
  and the Network dashboard map `routerboard_address`, not the identity. Any
  other MikroTik device is missing from mktxp's config, which is a Secret: adding
  one is a Secret edit, not a chart edit.
- **Fluent Bit's counters are scraped now, but a silent retry loop is not a drop.** The `:2020`
  Service is read since 2026-11-22, so `FluentBitDroppingLogs` sees batches abandoned after retries
  and records dropped from the logger queue. A pipeline that retries forever without ever failing
  or dropping is still seen only when `LokiSilent` fires, ten minutes later.
- Dashboards and rules are not in git. Their database is no longer unbacked up — the `grafana` claim
  is one row of the nightly `nodes-nas` stream — but a restore brings back a database, not a
  reviewable change, so a rule is still edited through the API and written down here. The Traefik
  rules and their dashboard are the single exception, and it is a **copy**: a snapshot in
  `cluster/helm/monitoring/grafana/export/traefik/` to read in a pull request and to restore after a
  wipe. Nothing applies it; the database stays the
  authority.

## Changing things

- Alert rules: `GET/POST /api/v1/provisioning/alert-rules`, groups under
  `/api/v1/provisioning/folder/homelab/rule-groups/homelab-core-fast` and
  `…/homelab-core`. A rule changes group with one `PUT` to its own endpoint,
  carrying the new `ruleGroup`; the group endpoint then sets the interval.
  Delivery is `/api/v1/provisioning/contact-points` and `/api/v1/provisioning/policies`.
  To test one rule without waiting for its group: `POST /api/v1/eval` with `{condition, data}` from
  its own object — the Traefik rules were checked that way on 2026-11-21, and it is the same check as
  validating the PromQL by hand.
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
