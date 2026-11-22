# Restore drill overdue alert — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Grafana rule warns when the newest recorded restore drill is more than 90 days old, and reports Alerting when it cannot read the log at all.

**Architecture:** The Prometheus chart renders a ConfigMap from a list of drill rows in `values.yaml`. Every `prometheus-node-exporter` pod mounts it read-only and serves the rows through its textfile collector as `homelab_restore_drill_timestamp_seconds{stream,drill,date}`. The Grafana rule computes the age of the newest row and compares it in a threshold, so a recent drill is Normal and a missing metric is NoData, which the rule reports as Alerting.

**Tech Stack:** Helm 3, Prometheus node-exporter textfile collector, Grafana unified alerting (provisioning API v1).

**Spec:** `docs/superpowers/specs/2026-11-22-restore-drill-alert-design.md`

## Global Constraints

- Rule group `homelab-core` (300 s), `for: 1h`, `noDataState: Alerting`, `execErrState: Alerting`, `severity: warning`.
- Metric name exactly `homelab_restore_drill_timestamp_seconds`; labels `stream`, `drill`, `date`; value is Unix seconds.
- 90 days = `7776000` seconds.
- The ConfigMap volume is `optional: true`: a missing log must never stop node-exporter.
- Rules are never committed: created through `POST /api/v1/provisioning/alert-rules` and recorded in `docs/reference/alerts.md`.
- Chart work follows the repo's conventions: 2-space YAML, Gitmoji commit prefix, `hack/check-charts.sh` must pass.

## Review Focus

- A recent drill must be **Normal**, not Alerting: the 90-day comparison lives in the Grafana threshold, never in PromQL, because a false comparison returns an empty vector and the rule's no-data state is Alerting.
- A missing ConfigMap or a renamed metric must be **Alerting**: this is the case the card exists for.
- The `date` label must keep rendering correctly after a value edit: it is derived from `at` in UTC, so no timezone-dependent diff between the operator's machine and ArgoCD.
- A new drill row in the runbook without a matching `restoreDrills` entry must be visible as a review miss, not silently ignored: both files point at each other.
- Epochs must print as integers, never `1.79e+09`.

---

### Task 1: The drill metric in the Prometheus chart

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (new top-level `restoreDrills:`; new keys under `prometheus.prometheus-node-exporter`)
- Create: `cluster/helm/monitoring/prometheus/templates/configmap-restore-drills.yaml`

**Interfaces:**
- Produces: ConfigMap `monitoring/restore-drills`, key `restore-drills.prom`, mounted at `/restore-drills`; metric `homelab_restore_drill_timestamp_seconds{stream,drill,date}` served on every node-exporter `:9100`.

- [ ] **Step 1: Add the values**

In `values.yaml`, after the `ingress:` block:

```yaml
# The restore-drill log the RestoreDrillOverdue rule reads. One entry per row of
# the table in docs/runbooks/restore.md under "The drill log"; update both in the
# same change. `at` is Unix seconds at UTC midnight of the drill date:
#   date -u -d 2026-11-21 +%s
# The `date` label is derived from it in templates/configmap-restore-drills.yaml,
# so a row stores one fact.
restoreDrills:
  - stream: nodes-nas
    drill: postgresql-dumps
    at: 1795219200
  - stream: nodes-streaming
    drill: sonarr-database
    at: 1795219200
  - stream: nfs
    drill: syncthing-file
    at: 1795219200
  - stream: all
    drill: last-resort
    at: 1795305600
```

Under `prometheus.prometheus-node-exporter`, before `image:`:

```yaml
    # The textfile collector reads the drill log that
    # templates/configmap-restore-drills.yaml renders. `optional: true` because a
    # missing ConfigMap must not stop node-exporter; the rule reports the missing
    # data as Alerting, which is the failure it exists for.
    extraArgs:
      - --collector.textfile.directory=/restore-drills
    extraVolumes:
      - name: restore-drills
        configMap:
          name: restore-drills
          optional: true
    extraVolumeMounts:
      - name: restore-drills
        mountPath: /restore-drills
        readOnly: true
```

- [ ] **Step 2: Add the ConfigMap template**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: restore-drills
  namespace: {{ .Release.Namespace }}
  labels:
    app.kubernetes.io/name: {{ .Chart.Name }}
    app.kubernetes.io/instance: {{ .Release.Name }}
data:
  restore-drills.prom: |
    # Restore drills, one series per row of the table in
    # docs/runbooks/restore.md's "The drill log". Update both in the same change.
    {{- range .Values.restoreDrills }}
    homelab_restore_drill_timestamp_seconds{stream="{{ .stream }}",drill="{{ .drill }}",date="{{ dateInZone "2006-01-02" (int .at) "UTC" }}"} {{ int .at }}
    {{- end }}
```

- [ ] **Step 3: Render and check the ConfigMap**

```bash
tmp=$(mktemp -d); cp -r cluster/helm/monitoring/prometheus/. "$tmp"/
helm dependency build "$tmp" >/dev/null
helm template prometheus "$tmp" --show-only templates/configmap-restore-drills.yaml
```

Expected: four metric lines; each ends in a bare integer (`1795219200`, `1795305600`), never exponent notation; `date="2026-11-21"` on the first three and `date="2026-11-22"` on the last.

- [ ] **Step 4: Check the DaemonSet mount renders**

```bash
helm template prometheus "$tmp" --show-only templates/../charts/prometheus-node-exporter/templates/daemonset.yaml
```

If the subchart path differs, render the whole chart and grep the DaemonSet:

```bash
helm template prometheus "$tmp" | awk '/kind: DaemonSet/,/^---/' | grep -E 'textfile|restore-drills|optional|mountPath'
```

Expected: `--collector.textfile.directory=/restore-drills`, the `restore-drills` volume with `optional: true`, and the read-only mount.

- [ ] **Step 5: Run the chart gate**

Run: `hack/check-charts.sh --only monitoring/prometheus`
Expected: the prometheus chart is reported clean (exit 0).

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/monitoring/prometheus/values.yaml cluster/helm/monitoring/prometheus/templates/configmap-restore-drills.yaml
git commit -m "🚀 feat(monitoring): serve the restore-drill log as a node-exporter textfile"
```

---

### Task 2: The documented rule, docs and changelog

**Files:**
- Modify: `docs/runbooks/restore.md` (the "The drill log" section)
- Modify: `docs/reference/alerts.md` (counts, table, prose, known gap)
- Modify: `docs/how-it-works/backups.md` ("The four ways it fails")
- Modify: `CHANGELOG.md` (Unreleased → Added)

**Interfaces:**
- Consumes: the metric name and labels from Task 1; the rule definition recorded here is created in Task 4.

- [ ] **Step 1: Point the runbook's log at the machine copy**

At the end of "The drill log" in `restore.md`, add a paragraph: the machine-readable copy is `restoreDrills` in `cluster/helm/monitoring/prometheus/values.yaml` (one entry per row, Unix seconds, `date -u -d <date> +%s`), the `RestoreDrillOverdue` rule reads it, and a row added to the table without the matching entry is invisible to the alert — so both change together. Link `../reference/alerts.md`.

- [ ] **Step 2: Record the rule in the alerts page**

- Update the header counts to the live values: 46 rules, `homelab-core` 26, `homelab-core-fast` 20, marked checked 2026-11-22.
- Add a table row: `| `RestoreDrillOverdue` | newest restore-drill timestamp older than 90 days | 1h | warning | inactive |`.
- Add the prose paragraph: why the mechanism is a textfile-fed ConfigMap, why the comparison is in the threshold rather than PromQL (a false PromQL comparison is an empty vector, and this rule's no-data state is Alerting), why 90 days is three missed monthly drills, and that a no-data or evaluation error reports Alerting by design.
- Replace the known-gap sentence that says the drill log "is empty today": the log now holds the four entries, the rule makes a stopped habit loud, but the date is written by hand — the rule does not prove a drill happened.

- [ ] **Step 3: Add the failure mode to the backups page**

In `docs/how-it-works/backups.md`, change "The four ways it fails" to "The five ways it fails" and add item 5: nobody restores anything — the log is the only proof, `RestoreDrillOverdue` ages its newest row past 90 days, and the fix is a drill, not a job.

- [ ] **Step 4: Add the changelog entry**

Under `## [Unreleased]` → `### Added`, a bullet naming the rule, the metric, the 90-day threshold, and the no-data behaviour.

- [ ] **Step 5: Check the docs**

```bash
grep -n "RestoreDrillOverdue" docs/reference/alerts.md docs/runbooks/restore.md CHANGELOG.md docs/how-it-works/backups.md
grep -n "restoreDrills" cluster/helm/monitoring/prometheus/values.yaml docs/runbooks/restore.md
```

Expected: every file names the rule or the values key where the step says it does.

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/restore.md docs/reference/alerts.md docs/how-it-works/backups.md CHANGELOG.md
git commit -m "📝 docs(backup): record RestoreDrillOverdue and the machine-readable drill log"
```

---

### Task 3: The gate and the pull request

**Files:** none.

- [ ] **Step 1: Run the checks**

```bash
hack/check-charts.sh
hack/check-exposure.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```

Expected: every chart clean, exposure check passes, zero test failures.

- [ ] **Step 2: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push origin feature/restore-drill-alert
```

- [ ] **Step 3: Open the PR**

Base `develop`. Body: what the metric is, where the data lives, why the threshold is in Grafana rather than PromQL, and the verification from Task 1's render. Note that the rule is created after the merge, because a rule that reads a metric that does not exist yet is Alerting by design.

---

### Task 4: Create the rule and prove its three states (after the merge)

**Files:** none (Grafana database; evidence goes into card #406).

**Interfaces:**
- Consumes: the metric from Task 1 after ArgoCD synced `develop`.

- [ ] **Step 1: Confirm the metric is served**

Query Prometheus through the Grafana datasource proxy:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -s -G -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/PBFA97CFB590B2093/api/v1/query" \
  --data-urlencode 'query=topk(1, homelab_restore_drill_timestamp_seconds)'
```

Expected: one series whose `date` is `2026-11-22`, `stream` either `all` or `nodes-streaming` (`topk(1, …)` breaks the tie between `last-resort` and `sonarr-live-claim` arbitrarily), value `1795305600`.

- [ ] **Step 2: Create the rule**

`POST https://grafana.kub.local/api/v1/provisioning/alert-rules` with:

- `folderUID: homelab`, `ruleGroup: homelab-core`, `title: RestoreDrillOverdue`, `condition: C`, `for: 1h`, `noDataState: Alerting`, `execErrState: Alerting`, `labels: {severity: warning}`, `isPaused: false`.
- A: `datasourceUid: PBFA97CFB590B2093`, instant, `expr: time() - topk(1, homelab_restore_drill_timestamp_seconds)`.
- C: `__expr__` threshold, expression `A`, reducer `last`, evaluator `{type: gt, params: [7776000]}`.
- annotations: summary `restore drill overdue: newest was {{ $labels.date }} ({{ $labels.stream }})`; description naming the 90-day (three-monthly) threshold, the runbook at `https://github.com/vyrtualsynthese/homelab/blob/develop/docs/runbooks/restore.md`, that this is a warning at 90 days, not an outage, and that `[no value]` in place of the date and stream means the log could not be read at all — the ConfigMap or the metric is missing, so the fault is the exporter; `runbook_url` the same link.

Expected: 201 with a `uid`; the rule appears in `homelab-core`.

- [ ] **Step 3: Prove the recent-drill case is Normal**

`POST /api/v1/eval` with the saved rule's `condition` and `data`. Expected: C returns a value of roughly 85,000 (seconds since 2026-11-22) and the condition `gt 7776000` is false. Then read `GET /api/prometheus/grafana/api/v1/rules` after one evaluation interval: `state: inactive`, `health: ok`.

- [ ] **Step 4: Prove an old date fires**

Create a muted temporary copy named `RestoreDrillOverdue-TEST` in the same group with `for: 0s`, `notification_settings: {mute_all: true}` (if the API rejects the field, record that and accept one Discord message), and A's expression `time() - topk(1, homelab_restore_drill_timestamp_seconds) + 7776000` — the real newest row aged by exactly 90 days. Wait one 300 s evaluation; read the state. Expected: `firing` (or `pending` then `firing`), with the summary naming the real date and stream.

- [ ] **Step 5: Prove no data reports Alerting**

Point the temporary rule's expression at `topk(1, homelab_restore_drill_missing_metric)`; wait one evaluation; read the state. Expected: the no-data state, not `inactive`/`ok`, because `noDataState: Alerting`.

- [ ] **Step 6: Delete the temporary rule and record the evidence**

`DELETE /api/v1/provisioning/alert-rules/{uid}` for the TEST rule. Comment on card #406 with the four API calls, their responses and the final states, then move the card to `Validation` with the PR link and the verification output.
