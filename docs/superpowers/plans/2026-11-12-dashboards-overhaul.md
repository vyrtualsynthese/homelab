# Dashboards overhaul — implementation plan

> **For agentic workers:** this plan is executed with the Grafana HTTP API. Each task ends
> with a validation step (every query returns data) and a screenshot check at 1920×1080.
> Steps use `- [x]` for tracking.

**Goal:** Make the five Grafana dashboards honest, readable and quick to debug from, without
changing what is stored in Prometheus or Loki.

**Architecture:** Dashboards live in Grafana's database and are written with
`POST /api/dashboards/db` (`folderUid: homelab`, `overwrite: true`). Each dashboard is generated
by a throwaway script under `/tmp/opencode/vis/`, built from the queries validated against
`http://prometheus.kub.local`. Deletion of the old Investigate dashboard is a
`DELETE /api/dashboards/uid/homelab-investigate` after the Platform replacement renders.

**Tech Stack:** Grafana 13.3.1 HTTP API, PromQL, LogQL, Playwright-core + headless Chromium for
screenshots.

**Spec:** `docs/superpowers/specs/2026-11-12-showcase-dashboard-design.md` (showcase, already
built). This plan covers the four working dashboards.

## Global constraints

- Folder `Homelab`, dark theme, English labels, plain words on the showcase only.
- Datasource Prometheus uid `PBFA97CFB590B2093`, Loki uid `P8E80F9AEF21F6940`.
- Every panel query must be executed against Prometheus/Loki before the dashboard is saved.
- Tables must never show `app_kubernetes_io_*` or `Value #A` columns; use explicit
  `organize` transformations.
- Legends must name the host, not the IP, wherever a `node` label exists; `rbp0` 192.168.1.22 and
  `rbp1` 192.168.1.38 are mapped with `label_replace`.
- No dashboard stores a token or a webhook.
- Backups of the four current dashboards are taken before the first write:
  `/tmp/opencode/grafana/backup/`.

## Review focus

- A table built from several instant vectors must merge them into one frame; otherwise Grafana
  shows the first frame only (the `Every drive` bug).
- `kube_pod_status_phase{phase="Succeeded"}` is not a problem and must never appear in an
  attention table.
- A `Pending` pod created months ago and one created a minute ago are different incidents: age
  must be visible.
- Traefik access logs contain the word "error" without being errors; the log error panel must not
  be dominated by them.
- A drive without a temperature attribute must render blank, never 0.

---

### Task 1: Backup and baseline

**Files:** `/tmp/opencode/grafana/backup/*.json`

- [x] Save `homelab-health`, `homelab-investigate`, `homelab-logs`, `homelab-disks` JSON.
- [x] Record current panel counts (26 / 27 / 8 / 14) and uid list.

### Task 2: Rebuild Health as an overview

**Files:** `/tmp/opencode/vis/build-health.js`

- [x] 11 panels: four counts (machines, containers, data stored, pods not running), an attention
      table with namespace/pod/status/reason/age, a dead-targets table, firing alerts, apps not
      fully running, restarts in the last hour, error rate by namespace, disk use by host.
- [x] Tables use `joinByField` + `organize`; age is `time() - kube_pod_created`, unit `s`.
- [x] Every query validated; screenshot at 1080p shows no metadata columns.

### Task 3: New Applications dashboard

**Files:** `/tmp/opencode/vis/build-applications.js`

- [x] Eight panels with a `namespace` multi-variable: apps not fully running, pods not running,
      CPU and memory per pod, restarts, PVC usage, dead targets in the namespace, recent errors.
- [x] Screenshot shows readable pod names and reasons.

### Task 4: Platform replaces Investigate

**Files:** `/tmp/opencode/vis/build-platform.js`

- [x] Keep nodes, control plane and Prometheus panels; add node temperature and filesystem use.
- [x] New uid `homelab-platform`; delete `homelab-investigate` only after the screenshot passes.
- [x] Every host series named `nas`, `nuc0`, `streaming`, `rbp2`, `rbp0`, `rbp1`.

### Task 5: Logs polish

**Files:** `/tmp/opencode/vis/patch-logs.js`

- [x] Legends `{{container}}` / `{{node}}`; error query with word boundaries and a default
      exclude list for access-log noise; level column visible in the stream.
- [x] Error count drops to a believable number; screenshot checked.

### Task 6: Disks polish

**Files:** `/tmp/opencode/vis/patch-disks.js`

- [x] Rebuild the drive table with host, device, model, capacity, life, temperature, power-on
      years, SMART status, reallocated and pending sectors, merged into one frame.
- [x] Legend text `host /dev/x · model`; `Oldest` gains a years unit; no `Infinity`.
- [x] Screenshot checked; blank stays blank.

### Task 7: Documentation and commit

**Files:** `docs/observability-conventions.md`

- [x] Dashboard table updated: uids, panel counts, one-line purpose each.
- [x] Commit the doc change; dashboards remain in Grafana's database only.

## Verification

For each dashboard: `node validate.js <uid>` reports `0 invalid, 0 empty`, then
`node shot.js <url> out.png 1920 1080` and read the image. A panel that errors, a column that
shows metadata, or a legend that names an IP fails the task.
