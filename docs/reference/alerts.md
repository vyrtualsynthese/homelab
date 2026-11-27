# Alerts: the rules, the silences and the gaps

> **In one line.** Every alert rule in Grafana's two `homelab-core` groups, what each one protects,
> why the three remaining silences exist, the pattern a rule must follow to fire at all, and the holes
> nobody watches.
>
> **Use it when** an alert fired and you want to know what it means, you are writing or editing a
> rule, or you are asking whether something is monitored at all · **Verified** 2026-11-26 · **Kind:** reference
>
> **Sources** the live rules, read from Grafana's API on 2026-11-26 ·
> `cluster/helm/monitoring/grafana/values.yaml`, `docs/observability-conventions.md`, and the two
> exports under `cluster/helm/monitoring/grafana/export/{traefik,security}/rules.json`

The overview — what runs where, its sizing, and how to query the stack:
[`observability-conventions.md`](../observability-conventions.md).

## Alerting

Grafana unified alerting, folder `Homelab`, **two rule groups split by cadence
on 2026-11-18**: `homelab-core-fast` at **60 s** (28 rules) and `homelab-core` at
**300 s** (35 rules). **63 rules**, **two contact points**, and four child routes: two by severity and
two on `board=card` — one to each contact point.

The split rule is the `for` duration: a rule sits in the fast group when its `for`
is 5 minutes or less, or when it reports that something is down and its `for` is
longer — `CoreDeploymentDown`, `CoreStatefulSetDown`, `CoreDaemonSetDown`,
`MediaDeploymentDown` and, since 2026-11-20, `CloudDnsCheckFailing` at ten minutes,
and `ScrapeTargetDown` at fifteen because its whole job is noticing that data
stopped arriving. Everything else is
checked every five minutes: `CertificateExpiring`, `CloudTlsCertificateExpiring` and
`RestoreDrillOverdue` (`for: 1h`), the three disk-fill predictions (`for: 30m`), the temperature and
SLO-ish checks (`for: 10m` and above). The slowest ratio is the `for: 10m` rules in
`homelab-core`: half their own `for` between checks, so an alert is delayed by up to five
minutes and never by more than that. At 58 rules the load is **33.2 evaluations a minute** —
27 rules every 60 s plus 31 every 300 s — against 35.0 when the split was made, with all 35 rules
of that day on the fast cadence.

**Delivery**: contact point `discord-homelab` (type `discord`).
`group_by [alertname, instance]`, `group_wait 30s`, `group_interval 5m`.
Warnings repeat every **12 h**; a child route matching `severity="critical"`
repeats every **1 h**. A third child matches `board=card` and repeats every **8760 h** — Grafana
stores it as `1y`. A rule carrying that label is one a person wants told once: the notice lands in
Discord and as a card on the `alerts` board, and nothing follows while the condition holds. The
webhook URL is not readable through the API (it is a
secure field), so the contact point is never rewritten by automation. The Discord
webhook exists only in that contact point.

**The second contact point is what writes the card.** `vikunja-alerts` posts to `alert-relay` in
`monitoring`, which opens one card per incident on the `alerts` board, writes nothing while that card
sits in Backlog or Doing, opens a fresh card when the newest one is in Validation or Done, and never
moves or closes one ([`runbooks/alert-relay.md`](../runbooks/alert-relay.md)). Two child
routes reach it: `severity="critical"` every **6 h**, and `board=card` every **1 y**. The relay itself
has no severity filter — the routing tree decides, and the relay writes a card for whatever arrives.

**The lifecycle is enforced by a job, not remembered.** `alert-relay-lifecycle` runs every five
minutes in the same chart. It reads the `alerts` board and follows each alert card's `blocked` fix
cards — the relations a session writes when it opens the work: the alert moves to Doing while any fix
is open, and to Validation once every fix is in Validation or Done. Validation is terminal, nothing is
moved backwards or closed, and the job writes only the `alerts` board. It reads the repository boards
to learn each fix's column, so `bot-alertmanager` holds read-only on boards 2, 3, 5, 8 and 12, and
`bot-opencode` holds read+write on `alerts` to write the relations and the cause. A fix card it cannot
read fails that alert card loudly rather than closing it early.

**Rule set and live state on 2026-11-13 (the three `Cloud*` rows read back on
2026-11-20, the counts and `GpuNotAllocatable` on 2026-11-22,
`ArgoAppRenderFailing` added and proved on 2026-11-22, the two `Postgres*` rows
added on 2026-11-22, the `DriveWorn`/`DriveFailing` split and the `DiskTooHot` / `BackupStreamStale`
threshold corrections on 2026-11-25, and
`SnapraidParityStale`, which is created through Grafana's provisioning API after
its metric reached `develop` — the state below is the state it was written with,
confirmed by the first read-back):**

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
| `DriveWorn` (`bg5bcigrw9xxcb`) | rated life under 10 %, or smartctl exit bit 32 — the error log holds records, nothing is failing yet | 1h | warning | **firing** on `nas /dev/sdf` |
| `DriveFailing` (`eg5bcfrf3qs5cf`) | SMART failed, a pending or offline-uncorrectable sector, or exit bits 1–16 / 64 — the damage bits | 10m | critical | inactive |
| `ScrapeTargetDown` | any non-node target down, including the edge Pis' SMART and Docker exporters; Prometheus and kube-state-metrics excluded (own rules) | 15m | warning | inactive |
| `NodeCpuHigh` | >90 % by host | 15m | warning | inactive |
| `NodeMemoryHigh` | >90 % of `MemAvailable` | 15m | warning | inactive |
| `NodeTemperatureHigh` | hottest CPU or SoC sensor, selected by hwmon driver >80 °C | 10m | warning | **firing** on `streaming` |
| `GpuTemperatureHigh` | `DCGM_FI_DEV_GPU_TEMP` >80 °C | 10m | warning | inactive |
| `GpuNotAllocatable` | allocatable `nvidia.com/gpu` reads 0 on a node that registered the card | 10m | warning | inactive |
| `DiskTooHot` | drive >55 °C, or an NVMe >70 °C | 15m | warning | inactive |
| `DiskFillingUp` | filesystem >85 % | 30m | warning | inactive |
| `DiskWillFill` | predicted full in 24 h above 80 % used | 30m | warning | inactive |
| `PVCFillingUp` | claim >85 % (NFS claims only) | 30m | warning | inactive |
| `DiskReallocatingSectors` | any new sector in 1 h | 5m | warning | inactive |
| `RestartStorm` | 5+ restarts in an hour | 5m | warning | **firing, silenced** (the three long-broken apps) |
| `OOMKilled` | terminated by OOM in the last hour | 5m | warning | inactive |
| `JobFailed` | any Job with a failed pod, no success, and younger than 2 h | 5m | warning | inactive |
| `ArgoAppRenderFailing` | an Application carrying a `ComparisonError` — Argo CD could not render it, so it no longer reports sync state | 10m | warning | inactive |
| `RecyclarrNotSucceeding` | no successful recyclarr run in 26 h | 5m | warning | inactive |
| `BackupStreamStale` | a CronJob in namespace `backup` whose last success is older than its window — 26 h nightly, 8 days for the weekly `backup-prune` | 10m | warning | **firing** on `backup-nfs` |
| `BackupJobAbsent` | a backup CronJob with no `kube_cronjob_status_last_successful_time` series, so it is gone or has never succeeded | 5m | warning | inactive |
| `PostgresDumpStale` | the `databases/postgres-dump` CronJob's last success is more than 26 h old | 10m | warning | inactive |
| `PostgresDumpAbsent` | no `kube_cronjob_status_last_successful_time` series for `databases/postgres-dump`, so it is gone or has never succeeded | 5m | warning | inactive |
| `RestoreDrillOverdue` | newest restore-drill timestamp older than 90 days | 1h | warning | inactive |
| `SnapraidParityStale` | no completed snapraid sync on `nas` for 7 days, or no marker at all | 10m | warning | inactive |
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
| `EdgeVpnAddressUnheld` | neither edge host reports `wg_vpn_vrrp_master 1`, so nothing holds `192.168.1.9` and clients have nowhere to dial (`noDataState: Alerting`) | 10m | critical | inactive |
| `EdgeVpnSnapshotStale` | `time() - wg_vpn_snapshot_timestamp_seconds > 600` on the authority, or the series absent — the copy stops advancing and a failover would serve an old peer set | 10m | warning | inactive |
| `EdgeVpnStandbyStale` | `time() - maximum(wg_vpn_sync_timestamp_seconds) > 900`, or the series absent on the standby — the pull has stopped; the timestamp only moves on a run that ended current | 10m | warning | inactive |
| `EdgeVpnSyncDiverged` | `wg_vpn_sync_diverged == 1` — the standby's copy was edited locally and the sync refuses to overwrite it, so the two disagree until somebody decides | 10m | warning | inactive |
| `CloudDnsCheckFailing` | `1 - probe_success{job="blackbox-dns"}` — a zone's check failed at one authoritative server | 10m | critical | inactive |
| `CloudTlsCertificateExpiring` | days to the earliest public certificate, firing below 21 | 1h | warning | inactive |
| `SecurityBanBurst` | more than 20 new bans on the `streaming` bouncer in 10 minutes | 5m | warning | inactive |
| `AuthentikSignInFailures` | more than 20 failed authentik sign-ins in 15 minutes | 5m | warning | inactive |
| `AuthentikSignInSuccessAfterBurst` | a sign-in that worked, from a source with more than 20 failures in the same 15 minutes | 5m | critical | inactive |
| `HostSshBruteForce` | more than 10 failed SSH passwords on one host in 10 minutes | 5m | warning | inactive |
| `HostFirewallBlockBurst` | more than 200 `UFW BLOCK` lines on one host in 5 minutes | 5m | warning | inactive |
| `SecurityExposureDrift` | an ungated route, or the public-host or privileged-controller count off its expected value | 10m | critical | inactive |
| `SecurityStackBlind` | the exporter stale or a family gone, or no access-log line in 10 minutes (`noDataState: Alerting`) | 5m | critical | inactive |
| `SecurityBouncerStalled` | the bouncer's decision-stream requests flat for 10 minutes | 5m | critical | inactive |

The **four edge VPN rules** (2026-11-27) are one story in four pieces: two hosts answering for one
address, a database copy that must keep arriving, and a refusal that must be seen when it fires. Each
sits in `homelab-core` at `300 s` with `for: 10m`, and `noDataState: Alerting` where the series is the
signal — a stopped timer writes nothing, so absence is the failure, not a quiet state. The staleness
rules subtract from `time()` because the timestamp series never ages by itself: a script that stops
leaves its last value standing, which is what makes the subtraction work.

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
generic because everything it watches lives in one namespace:
`(time() - kube_cronjob_status_last_successful_time{namespace="backup", cronjob!="backup-prune"}) > 93600`.
93600 s is 26 h, one daily slot plus two hours, and it covers the four nightlies — `backup-node-nas`,
`backup-node-nuc0`, `backup-node-streaming` and `backup-nfs` — plus `backup-verify`. The weekly
`backup-prune` cannot share that window: it runs on Sunday at noon, so for six days of every seven a
26-hour threshold is already true and the rule paged through the week on a job that was working. The
2026-11-25 correction gives it a second branch and its own window: 691200 s is 8 days, one weekly slot
plus one day, and the branch carries a `window` label so the summary says which one it measured
(`backup stream {{ $labels.cronjob }} has not succeeded in {{ $labels.window }}`). That keeps the
verifier under the nightly window with no rule of its own — a verifier whose last success is 26 hours
old is already firing this one — while the prune is still watched, just on the cadence it actually
runs. Its `for` is 10 minutes rather than 5, which is what keeps it in `homelab-core`: the split rule
puts a 5-minute `for` in the fast group, and a nightly job gains nothing from a 60 s evaluation. The
summary names `{{ $labels.cronjob }}` rather than a stream label because kube-state-metrics does not
publish CronJob labels on `kube_cronjob_status_last_successful_time`; `backup-node-nas` is the job,
`nodes-nas` is the stream, and the mapping is in the chart.

A stale backup and a *failing* backup are different faults, and only the second one reaches `JobFailed`
(the pod failed). A job whose pod starts and exits non-zero is both — `JobFailed` within its two-hour
window and this rule 26 h later — but a job that never runs at all, because the schedule stopped or
the sync removed it, only reaches this rule. That is the case worth having: a backup repository that
nobody writes to looks exactly like a healthy one until the day it is needed.

**`BackupJobAbsent`** (2026-11-22) covers the third shape of the same silence, and the one
`BackupStreamStale` cannot see. That rule compares the last-success timestamp against a window — 26
hours, or 8 days for the weekly `backup-prune`; when the CronJob is deleted, renamed, or has never
once succeeded, kube-state-metrics publishes no
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

**`SnapraidParityStale`** (2026-11-25) covers the other half of the same question, one layer below the
backups. SnapRAID keeps a redundant copy — the **parity** — of the six data disks on `nas`, and a
nightly run (a **sync**) brings that copy up to date; a disk lost while the parity is old costs every
write since the last finished sync. `ansible/roles/snapraid` writes the Unix time of each sync that
exited cleanly into `/var/lib/node-exporter/textfile/snapraid.prom`, and a second node-exporter on
`nas`, with **every default collector off**, serves that value as
`homelab_snapraid_last_sync_timestamp_seconds`. The chart is `snapraid-exporter`, at
`cluster/helm/monitoring/snapraid-exporter`.
Why a second exporter rather than the one already running: `--collector.textfile.directory` is a
**single** directory, and the DaemonSet's is `/restore-drills`; adding a host directory "beside" it is
not possible, and moving the drill log would break its refresh.

The value is a **timestamp, not an age**. The expression is
`time() - homelab_snapraid_last_sync_timestamp_seconds`, so a runner that stops makes the number grow
instead of freezing the last age it wrote, and the **7-day** comparison sits in the threshold for the
reason given under the drill rule. Seven days is a week of missed or failed nights, and longer than
the first full sync, which reads 13 TiB and resumes across nights. The rule never calls
`snapraid status`: it refuses while a sync holds the **content lock** (the lock a running sync keeps
on the array's own files), which is exactly when someone would want it. A **`[no value]` summary**
means the marker or the exporter is missing — this rule reports no data as Alerting — not that a sync
is slow: the runner's failure is a number that keeps growing, the exporter's is a number that is gone.

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

`noDataState` is `OK` on every rule **except `LokiSilent`**, `RestoreDrillOverdue` and
`SnapraidParityStale`, which are `Alerting` on no data: `LokiSilent`'s expression returns an empty
vector when no lines arrive, `RestoreDrillOverdue`'s metric disappears with its ConfigMap, and
`SnapraidParityStale`'s disappears with the host marker — each exactly the condition it must report.
`MonitoringDown` and `LokiSilent` also raise on evaluation errors (`execErrState:
Alerting`), so a Prometheus restart or a dead Loki produces one message rather than none;
`RestoreDrillOverdue` does the same, because a rule that cannot read the log must not report health.
`SnapraidParityStale` is the one exception in the other direction: its `execErrState` is `OK`,
because a query that fails is the monitoring stack's fault — `MonitoringDown` and
`GrafanaAlertEvaluationFailing` own that — while a *missing* value is the marker's, and the no-data
state is what reports it.

**The rule pattern matters.** A PromQL comparison such as `up == 0` returns the
value `0` when true, and the threshold condition is "above 0" — so it can never
fire. Rules must return a **positive value when firing**: `1 - up`,
`replicas - ready`, `0 - predict_linear(...)`, `1 - kube_node_status_condition`,
or an explicit `> 85` / `> 55` / `> 80` inside the expression. The original set
had `NodeDown` and `MonitoringDown` silently dead this way, and the retired
`DriveNeedsReplacement` survived only because the failing drive also reported
`exit_code = 32`.

**The drive rules split on 2026-11-25: one asks, one pages.** `DriveWorn` reads
life under 10 % or smartctl's exit bit 32 — a history of errors, with SMART still passing and no
sector waiting to be remapped — and tells a person once, because a worn drive can be replaced on a
quiet afternoon. `DriveFailing` reads the signals that mean move the data now: SMART failed, a
pending or offline-uncorrectable sector, or the exit-code damage bits. The split is why the
`/dev/sdf` silence could go: a warning that arrives once does not need suppressing.

**`DiskTooHot` uses one threshold per device class** (2026-11-25). A spinning or SATA drive warns
above 55 °C, an NVMe above 70 °C, because the number does not mean the same thing on the two: the
NAS's two 970 EVO Plus drives sit at 58–59 °C under normal load, their NAND sensor runs at 73–79 °C,
and Samsung's own warning threshold for the part is 81 °C. The single 55 °C limit fired eight times
in three days on hardware that was behaving. The expression tests each class inside the same
`label_replace` chain rather than duplicating it, so the host label still resolves exactly as before.
For an NVMe the usable reading is `smartprom_temperature` (the composite); `smartprom_temperature_sensor2`
is the flash sensor and runs much hotter.

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
`nas`, whose two static jobs, 3 + 22 targets and public-resolver choice are in
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
delegation answers, not which provider answers it. The zone moved to OVHcloud on 2026-11-21 and the
probes followed on 2026-11-27; asserting the provider stays a separate decision, and the runbook
owns the target-list edit for a future move.

Because all three are `noDataState: OK`, a selector that stops matching looks exactly like a healthy
rule: the proof they still look at something is `count(probe_success{job="blackbox-http"})` = 3 and
`count(probe_success{job="blackbox-dns"})` = 22. A dead prober is the one case where that silence is
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

**The eight security rules** (six on 2026-11-25, `SecurityBouncerStalled` and
`AuthentikSignInSuccessAfterBurst` on 2026-11-26) are the ones whose job is a person rather than an
application; only the first five read a log rather than a metric. Seven sit in
`homelab-core-fast`; `SecurityExposureDrift` sits in `homelab-core` because its `for` is 10 minutes,
which is the split above, and a posture change is a slow fault that gains nothing from a 60-second
check.

| Rule | Reads | Why that threshold |
|---|---|---|
| `SecurityBanBurst` | `delta(sum(fw_bouncer_banned_ips)[10m:1m])`, Prometheus | one or two bans a day is the normal rate; twenty in ten minutes is a scenario matching a stream |
| `AuthentikSignInFailures` | `homelab_authentik_signins_15m{outcome="failure"}` | the exporter's own count, so the rule cannot disagree with the dashboard's tile |
| `AuthentikSignInSuccessAfterBurst` | `homelab_authentik_signins_15m_by_source{outcome="failure"} > 20` joined on `source` to `outcome="success" > 0` | the same twenty failures, plus the one event that turns a nuisance into a compromise; a measured typo burst is three |
| `HostSshBruteForce` | `{source="journal", unit="ssh"}` filtered to failed passwords | 4 in 24 hours across the whole cluster on the day it was written |
| `HostFirewallBlockBurst` | `{source="journal", unit="kernel"}` filtered to `UFW BLOCK` | about eight per five minutes per node is the background; 200 is a scan |
| `SecurityExposureDrift` | the exporter's three posture gauges | expected 0 ungated, 9 public hosts, 7 privileged controllers |
| `SecurityStackBlind` | the exporter's age, three families, and the access-log count | ten minutes — no ten-minute window has ever held zero lines |
| `SecurityBouncerStalled` | `rate(cs_lapi_bouncer_requests_total[10m])`, Prometheus | 0.1 req/s is the normal poll, one every 10 s; zero across ten minutes is a dead stream, not a quiet one |

**`SecurityBouncerStalled`** (2026-11-26) is the half `SecurityStackBlind` cannot see. The bouncer's
listener answers, the exporter is fresh and all three families are present — every green signal —
while the service has stopped asking the LAPI for decisions. The ban set on `streaming` then freezes:
new attacks are neither refused nor forgotten, and nothing else notices. The expression is
`1 - (sum(rate(cs_lapi_bouncer_requests_total[10m])) > bool 0)`, and the `bool` is the reason it
fires at all: a bare comparison keeps the sample's value, so a flat counter would return `0`, and the
condition tests "above 0" (the trap under *The rule pattern matters*). `bool` turns it into a clean
`1`. The rate window is ten minutes and the `for` is five more; a counter that disappears entirely
returns no sample, which this rule reports as NoData and stays quiet about, because the missing family
is `SecurityStackBlind`'s case — one fault, one message. The metric carries one series for the cluster,
so the message cannot name a host; the action is the bouncer's systemd unit on `streaming`.

**`SecurityStackBlind` is the rule that makes the other six believable**: each of them reports an
event, and this one reports that nobody is watching for events. It is the only rule here with two
datasources, so it is a classic condition over two queries rather than one expression —
`A > 0 || B < 1`, where `A` is the Prometheus half (exporter older than 600 s, or a family missing)
and `B` the Loki half (access-log lines in 10 minutes).

Both halves end in `or vector(0)`, and that is not decoration. On the first evaluation the rule sat
**Pending (NoData) on a healthy cluster**: `A` returns nothing when everything is fine, a classic
condition over an empty query is NoData, and this rule's `noDataState` is `Alerting` because a dead
exporter looks exactly like NoData. `or vector(0)` gives the healthy case a `0` to compare against, so
NoData now means what it should. `SecurityExposureDrift` had the same shape for a different reason: an
`or` chain of three comparisons returns nothing when the posture is correct, so it is written as a sum
of absolute deviations from the expected counts, which is `0` when nothing moved and always a sample.

The node is **not** a label on the journal stream — Fluent Bit ships `unit` and `source`, and
`_HOSTNAME` stays inside the JSON line. The two host rules therefore read
`| json | label_format host=_HOSTNAME`, and a rule that forgets it reports all four nodes as one
number with no way to say which one. The journal input selects `_COMM=sshd` rather than a unit name
for the same class of reason: Ubuntu socket-activates sshd, and a unit filter would ship nothing on
exactly the hosts where a guesser is most visible.

**`SecurityAttackSustained` was retired on 2026-11-26, after one day.** It had been proved the way this
page asks for on 2026-11-25 — threshold temporarily lowered to `> 0`, the message named the live address
(`45.138.12.215 was refused 2 times in 10 minutes`), both receivers were notified, and the relay opened
card **768** on the `alerts` board — and the next day's rule history showed why it should not exist:
seven firings in seven hours, each a different address, each held above the threshold for 7–11 minutes,
and every one of them already banned at the firewall by CrowdSec. It reported that the internet scans,
which is not something a person acts on. The per-address record is the dashboard's *Top addresses
refused* panel, and `SecurityStackBlind` already reports the failure that matters, a detector that
stopped. The Discord message cannot be read back from here: the webhook is a secure field and no bot
token exists, so card 768 is the artifact this repository keeps.
`SecurityStackBlind` was proved by stopping the detector: `kubectl -n security scale
deploy/crowdsec-agent --replicas=0` removed the agent, `cs_parser_hits_ok_total` went stale five
minutes later, the rule went `pending` and then fired, and scaling it back resolved it. That is the
one proof that is not an expression, and it is the one worth doing: it shows that a dead detector
reaches a person.

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
- Until 2026-12-09: `alertname="ScrapeTargetDown"` with
  `app_kubernetes_io_instance="qbittorrent-exporter"` — the exporter cannot be scraped while
  qBittorrent's Web UI is stalled. Measured 2026-11-25: 627 failed scrapes in 24 h, the target down
  28 % of 48 h, every scrape pinned at the exporter's 5 s timeout, and 106 firing/resolved
  transitions in four days as the stall crossed the rule's 15-minute gate. The rule is right and the
  target is the fault. The config claim's move off NFS on 2026-11-25 did not clear it; the app itself
  moved to `nas` the same evening so its download path is local, and the exporter answered in 0.086 s
  again from 23:43
  ([`docs/qbittorrent-webui-stalls.md`](../qbittorrent-webui-stalls.md), board card 777). Remove the
  silence once that move has passed its 24-hour gate, or renew it with a new end date.
- **Removed 2026-11-25 — the silence on `DriveNeedsReplacement` went with the rule.** The drive
  `nas /dev/sdf` is still known dead and waiting on a replacement
  ([`docs/audit/ansible-hosts.md`](../audit/ansible-hosts.md)), but it is now a `DriveWorn` warning
  that asks once a year rather than a critical that had to be muted.
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

- **The host journal reaches Loki, and the node is not a label.** Since 2026-11-25 Fluent Bit reads
  each node's own journal, selected to `sshd` (`unit="ssh"`) and to kernel messages (`unit="kernel"`,
  which is where ufw logs) — the change that added the security detection, and the two rules that
  read it are above. Two things are still out: the two edge Pis run compose stacks rather than the
  DaemonSet, so their journals and their Docker logs are nowhere in the cluster, and the hostname
  inside each line (`_HOSTNAME`) is not a stream label, so a rule has to extract it. The other units
  on every node stay unshipped by decision, and the volume is sized in
  [`log-budget.md`](log-budget.md#the-host-journal-first-numbers).
- **CrowdSec is watched for silence, not for a dead poll.** `SecurityStackBlind` fires when the
  firewall bouncer, the LAPI or the agent stops exporting its metrics. A bouncer that keeps exporting
  while it no longer polls the LAPI — the scrape answers, the decision stream is dead — freezes the ban
  set with every rule green. `rate(cs_lapi_bouncer_requests_total[10m])` read **0.1 req/s** (one poll
  every 10 s) on 2026-11-26, so zero for ten minutes would name that case; no rule reads it yet.
- **The success-after-a-burst rule sees one window and one shape.** `AuthentikSignInSuccessAfterBurst`
  reads the exporter's per-source family: a source with more than 20 failures in fifteen minutes that
  also signed in inside the same fifteen minutes. Two cases stay invisible by construction. A slow
  spray — five attempts an hour against the same account — never reaches twenty in one window, and a
  success twenty minutes after the burst falls out of it. And the label is a salted pseudonym, so the
  message cannot print the address: it names the failure count, and the address is read in authentik's
  Events page or the gated access log. The family carries the twenty busiest repeatedly failing
  sources; `homelab_authentik_signins_by_source_omitted` says when the cap left one out.
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
  all four repositories from a laptop on 2026-11-22, and the etcd snapshot restored into a throwaway
  K3s the same day ([`../runbooks/restore-etcd.md`](../runbooks/restore-etcd.md)).
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
  reviewable change, so a rule is still edited through the API and written down here. Two exports are
  the exception, and they are **copies**: snapshots in
  `cluster/helm/monitoring/grafana/export/traefik/` and `export/security/` to read in a pull request
  and to restore after a wipe. Nothing applies them; the database stays the authority.

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
