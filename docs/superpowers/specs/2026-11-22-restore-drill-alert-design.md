# Restore drill overdue: design

Card #406 · branch `feature/restore-drill-alert` · 2026-11-22

## Intent

The drill log in `docs/runbooks/restore.md` is the only proof that backup contents come back. Nothing
notices when it stops being written: a year without a drill looks exactly like a healthy backup
system. The alert has to make the manual habit fail loudly.

## Goals

- One Grafana rule, `RestoreDrillOverdue`, warning, group `homelab-core`, `for: 1h`, whose message
  names the newest drill's date, its stream and the runbook.
- The date source is in git, reviewed by pull request, and reaches the rule without a new workload.
- No data and evaluation errors report **Alerting**, never OK.
- A fabricated old date fires; a recent date is Normal.

## Non-goals

- Per-stream recency: the monthly drill rotates through sections 1–3, so a per-stream 90-day
  threshold would fire on a healthy rotation. The rule watches the **habit** (the newest drill of any
  stream), and the message names which stream was drilled last.
- The etcd rehearsal: card #8. When it lands it adds one row.
- Proving a restore automatically: the rule ages a log, it does not restore.

## Where the date comes from

A file in `docs/` cannot be scraped: the cluster has no copy of the repository. The dates must be
carried in by a chart. Three ways were considered:

- **A — textfile collector (chosen).** The Prometheus chart renders a ConfigMap from an epoch list in
  its `values.yaml`; every node-exporter pod mounts it and serves
  `homelab_restore_drill_timestamp_seconds{stream,drill,date}`. node-exporter already runs the
  textfile collector (`node_scrape_collector_success{collector="textfile"}` = 1 on all four cluster
  nodes) with no directory set, so this adds one argument and one read-only mount. No new pod, no
  new job, and one Application sync owns both the data and the mount.
- **B — a small exporter pod in `backup`.** Keeps the dates in the backup chart, but costs an
  always-on pod and a scrape target for one gauge.
- **C — pushgateway and a CronJob.** Most moving parts, and a dead pusher is indistinguishable from
  an old drill.

Chosen: A. The cost is that the backup dates live in the monitoring chart, and the human table in
`restore.md` repeats them; both files say so and point at each other, and the duplication is four
dates that change once a month.

## Parts

1. `cluster/helm/monitoring/prometheus/values.yaml` gains `restoreDrills:`, one entry per drill row
   in the runbook table: `stream`, `drill`, `at` (Unix seconds, UTC midnight of the drill date). The
   `date` label is derived in the template with `dateInZone … "UTC"`, so a row stores one fact.
2. `templates/configmap-restore-drills.yaml` renders the textfile body. Epochs are printed through
   `int` so Helm's number handling cannot produce exponent notation.
3. The `prometheus-node-exporter` subchart gets
   `--collector.textfile.directory=/restore-drills`, a read-only ConfigMap volume named
   `restore-drills` with `optional: true` (a missing log must not stop the exporter), and the mount.
   The ConfigMap lives in `monitoring`, the node-exporter namespace; a cross-namespace mount is not
   possible.
4. The rule, created through the Grafana provisioning API and recorded in
   `docs/reference/alerts.md`:
   - A: `time() - topk(1, homelab_restore_drill_timestamp_seconds)` — the age of the newest drill,
     one series, its labels intact. The comparison stays in the Grafana threshold (`gt 7776000`,
     90 days) so a recent drill is Normal rather than an empty vector, which the no-data state would
     turn into an alert.
   - C: threshold, last, `gt 7776000`.
   - `for: 1h`, `noDataState: Alerting`, `execErrState: Alerting`, `severity: warning`.
   - summary: `restore drill overdue: newest was <date> (<stream>)`; description names the runbook
     and the monthly rotation.

## Verification

- `helm template` (through `hack/check-charts.sh`) renders the ConfigMap with the expected lines.
- Prometheus serves the metric after the sync: `topk(1, homelab_restore_drill_timestamp_seconds)`
  returns the newest row with `stream`, `drill` and `date`.
- A temporary copy of the rule (muted, deleted afterwards) is exercised three ways through
  `/api/v1/eval` and the live state: real data → Normal; age inflated by 90 days → Firing with
  `for: 0s`; a selector matching nothing → the no-data state, which the saved rule reports as
  Alerting.
- Evidence (API calls and their answers) is written into card #406.

## Docs touched

`docs/runbooks/restore.md` (the log points at the machine copy and the rule), `docs/reference/alerts.md`
(row, count, prose, the stale known-gap bullet), `docs/how-it-works/backups.md` (the failure mode),
`CHANGELOG.md`.
