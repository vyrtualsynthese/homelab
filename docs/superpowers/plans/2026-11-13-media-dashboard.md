# Media dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Homelab · Media` dashboard to Grafana that answers "what is the media stack doing, and where is it stuck?" from the exporters enabled on 2026-11-13.

**Architecture:** One dashboard object posted to Grafana's SQLite database through the HTTP API. Eight data panels in three labelled rows, every query validated against Prometheus first and the saved panels re-queried through Grafana's own datasource proxy afterwards. No code, no chart, no cluster access.

**Tech Stack:** Grafana 13 HTTP API (`/api/dashboards/db`, `/api/ds/query`), Prometheus 3.16 HTTP API, `curl`, `python3`.

**Spec:** `docs/superpowers/specs/2026-11-13-media-dashboard-design.md`

## Global Constraints

- Datasource uid is `PBFA97CFB590B2093`, type `prometheus`. Every target and every panel carries this datasource.
- Folder uid is `homelab`. Dashboard uid is `homelab-media`, title `Homelab · Media`.
- `refresh: "30s"`, `time: {from: "now-6h", to: "now"}`, `schemaVersion: 39`, `timezone: "browser"`.
- Series names come from the `app_kubernetes_io_instance` label, never from `pod`.
- No `or vector(0)` and no filler row anywhere: a healthy state is an empty result, not a zero.
- No threshold overrides: none of the eight values has a defensible line yet.
- `$GRAFANA_TOKEN` comes from `~/.config/secrets/homelab.env`. It is read, never printed or written.
- Do not use `kubectl`. The dashboard lives only in Grafana.

## Review Focus

- **An app whose exporter dies** disappears from every panel instead of showing an error; the operator must see that from `ScrapeTargetDown`, not from this screen. Accepted, documented in the spec's non-goals.
- **A newly added Servarr instance** (a third sonarr, say) is invisible until its metric name is added to the union in panel 1, 2 or 3. The union is per metric family, so a new instance of an existing app appears automatically; a new *app type* does not.
- **Playback bandwidth at idle** returns no series. The panel must render blank, not 0, and not "No data" error state.
- **Table metadata leak**: any table that keeps `instance`, `pod`, `node`, `job`, `namespace` or the `app_kubernetes_io_*` echo labels is wrong even if the value is right.
- **Indexer table with one query filtered and one not** merges to 21 rows instead of 2. Both queries must be scoped to the down indexers.

---

### Task 1: Record the baseline each panel must reproduce

**Files:**
- No file changes.

**Interfaces:**
- Produces: the expected row counts used by Task 2 Step 6.

- [ ] **Step 1: Confirm the stack answers**

```bash
source ~/.config/secrets/homelab.env
curl -s -o /dev/null -w '%{http_code}\n' http://prometheus.kub.local/-/ready
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $GRAFANA_TOKEN" http://grafana.kub.local/api/health
```

Expected: `200` twice.

- [ ] **Step 2: Run all eight queries and record the result**

```bash
q(){ curl -sG 'http://prometheus.kub.local/api/v1/query' --data-urlencode "query=$2" \
  | python3 -c "import json,sys;r=json.load(sys.stdin);print('$1', r['status'], len(r['data']['result']))"; }

q P1 'sum by (app_kubernetes_io_instance, type, message) (sonarr_system_health_issues or radarr_system_health_issues or lidarr_system_health_issues or prowlarr_system_health_issues) > 0'
q P2 'sum by (app_kubernetes_io_instance, status) (sonarr_queue_total or radarr_queue_total or lidarr_queue_total)'
q P3 'max by (app_kubernetes_io_instance) (sonarr_episode_missing_total or radarr_movie_missing_total or lidarr_albums_missing_total)'
q P4a 'max by (indexer) (prowlarr_indexer_unavailable) > 0'
q P4b 'max by (indexer) (increase(prowlarr_indexer_failed_queries_total[1h])) and on(indexer) (max by (indexer) (prowlarr_indexer_unavailable) > 0)'
q P5 'sum(autobrr_irc_connected_total)'
q P6 'max(plex_sessions_tracked)'
q P7 'max(plex_session_bandwidth_kbps)'
q P8 'max by (library) (plex_library_storage_bytes)'
```

Expected on 2026-11-13: P1 4, P2 4, P3 5, P4a 2, P4b 2, P5 1, P6 1, P8 3. P7 may be 0 (nobody streaming); that is the documented idle state, not a failure.

- [ ] **Step 3: Confirm the two empty states are empty for the right reason**

```bash
curl -sG 'http://prometheus.kub.local/api/v1/query' \
  --data-urlencode 'query=sonarr_system_health_issues' \
  | python3 -c "import json,sys;[print(s['metric'].get('app_kubernetes_io_instance'), s['value'][1], 'message' in s['metric']) for s in json.load(sys.stdin)['data']['result']]"
```

Expected: `sonarr` and `radarr` appear with value `0` and no `message` label; the four apps in P1 carry `message`. This is why the `> 0` filter is correct rather than a `== 1`.

---

### Task 2: Build and save the dashboard

**Files:**
- Create: `/tmp/opencode/dash/homelab-media.json` (payload only, not committed)

**Interfaces:**
- Consumes: the row counts from Task 1 Step 2.
- Produces: live dashboard `homelab-media` in folder `homelab`.

- [ ] **Step 1: Write the payload with three row headers and eight panels**

Panel ids and grid positions, in this order:

| id | type | title | x,y | w×h |
|---|---|---|---|---|
| 100 | row | Needs attention | 0,0 | 24×1 |
| 1 | table | Apps reporting a problem | 0,1 | 12×7 |
| 2 | table | Downloads queued | 12,1 | 12×7 |
| 101 | row | Intake | 0,8 | 24×1 |
| 3 | bargauge | Missing per app | 0,9 | 8×7 |
| 4 | table | Indexers down | 8,9 | 8×7 |
| 5 | stat | Release intake | 16,9 | 8×7 |
| 102 | row | Playback and library | 0,16 | 24×1 |
| 6 | stat | Plex now | 0,17 | 6×8 |
| 7 | timeseries | Playback bandwidth | 6,17 | 9×8 |
| 8 | table | Library | 15,17 | 9×8 |

Every query in this task is an instant query: `"format": "table"` and `"instant": true` for tables, `"instant": true` alone for stat and bargauge.

Panel 1 — one target, refId A:
```
sum by (app_kubernetes_io_instance, type, message) (sonarr_system_health_issues or radarr_system_health_issues or lidarr_system_health_issues or prowlarr_system_health_issues) > 0
```
Transformations: `filterFieldsByName` include `["app_kubernetes_io_instance","type","message","Value"]`, then `organize` renaming `app_kubernetes_io_instance`→`app`, `type`→`severity`, `message`→`problem`, `Value`→`issues`.

Panel 2 — one target, refId A:
```
sum by (app_kubernetes_io_instance, status) (sonarr_queue_total or radarr_queue_total or lidarr_queue_total)
```
Same two transformations; rename `app_kubernetes_io_instance`→`app`, `Value`→`items`.

Panel 3 — one target, refId A, bargauge, unit `short`:
```
max by (app_kubernetes_io_instance) (sonarr_episode_missing_total or radarr_movie_missing_total or lidarr_albums_missing_total)
```
`legendFormat: "{{app_kubernetes_io_instance}}"`.

Panel 4 — two targets, refIds A and B, table:
```
A: max by (indexer) (prowlarr_indexer_unavailable) > 0
B: max by (indexer) (increase(prowlarr_indexer_failed_queries_total[1h])) and on(indexer) (max by (indexer) (prowlarr_indexer_unavailable) > 0)
```
Transformations in this order: `labelsToFields` (`merge: true`, `mode: "columns"`), `merge`, `filterFieldsByName` include `["indexer","Value #A","Value #B"]`, `organize` renaming `Value #A`→`down`, `Value #B`→`failed 1 h`. Query B is guarded so both sides return the same two indexers; without the `and on(indexer)` guard the merge produces 21 rows with blanks.

Panel 5 — three targets, stat, `legendFormat` per target:
```
A: sum(autobrr_irc_connected_total)                        legendFormat "IRC links up"
B: sum(increase(autobrr_release_push_approved_total[1h]))  legendFormat "Pushed 1 h"
C: sum(increase(autobrr_release_push_error_total[1h]))     legendFormat "Push errors 1 h"
```

Panel 6 — two targets, stat:
```
A: max(plex_sessions_tracked)          legendFormat "Sessions"
B: max(plex_active_transcode_sessions) legendFormat "Transcodes"
```

Panel 7 — one target, timeseries, unit `Kbits`, `legendFormat: "bandwidth"`:
```
max(plex_session_bandwidth_kbps)
```

Panel 8 — two targets, table:
```
A: max by (library) (plex_library_storage_bytes)   legendFormat "{{library}}"
B: max by (library) (plex_library_items)
```
Transformations: `labelsToFields` (`merge: true`, `mode: "columns"`), `merge`, `filterFieldsByName` include `["library","Value #A","Value #B"]`, `organize` renaming `Value #A`→`storage`, `Value #B`→`items`; override unit `bytes` on `storage`.

- [ ] **Step 2: Post it**

```bash
source ~/.config/secrets/homelab.env
python3 - <<'EOF'
import json, os
payload = {"dashboard": json.load(open("/tmp/opencode/dash/homelab-media.json")),
           "folderUid": "homelab", "overwrite": True, "message": "media dashboard"}
json.dump(payload, open("/tmp/opencode/dash/post.json","w"))
EOF
curl -s -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  --data @/tmp/opencode/dash/post.json http://grafana.kub.local/api/dashboards/db
```

Expected: `{"status":"success","uid":"homelab-media","version":1,...}`.

- [ ] **Step 3: Read it back and confirm the layout**

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" http://grafana.kub.local/api/dashboards/uid/homelab-media \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)['dashboard']
print(d['title'], d['uid'], len(d['panels']), 'panels')
for p in d['panels']:
    print(p['id'], p['type'].ljust(11), (p.get('title') or '-').ljust(28), p['gridPos'])
"
```

Expected: 11 entries, 3 of type `row`, the eight data panels with the grid positions from Step 1, and no two panels overlapping.

- [ ] **Step 4: Run every panel query through Grafana's own proxy**

```bash
source ~/.config/secrets/homelab.env
curl -s -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  --data '{"queries":[{"refId":"A","instant":true,"format":"table","expr":"max by (library) (plex_library_storage_bytes)","datasource":{"type":"prometheus","uid":"PBFA97CFB590B2093"}}],"from":"now-5m","to":"now"}' \
  http://grafana.kub.local/api/ds/query | python3 -c "
import json,sys
r=json.load(sys.stdin)
print(r['results']['A']['status'], len(r['results']['A']['frames']))
"
```

Expected: `200 1`. Grafana answers `400`/`500` with an error message when a query or datasource is wrong; that is the failure this step catches, not Prometheus itself.

- [ ] **Step 5: Repeat Step 4 for the other six queries**

Use the same body with `expr` replaced by each of: the panel 1, 2, 3, 4A, 4B, 5A, 5B, 5C, 6A, 6B, 7 and 8B expressions. Expected: `200` for each; panel 7 may return `200` with zero frames at idle.

- [ ] **Step 6: Compare row counts against Task 1**

For each table panel, count rows in the returned frame and compare with the Task 1 baseline (P1 4, P2 4, P3 5, P4 2, P5 1, P8 3). A lower count means a transformation is dropping fields; a higher count means a filter is missing.

---

### Task 3: Record the dashboard and refresh the stale facts

**Files:**
- Modify: `docs/observability-conventions.md` (the Dashboards table, the "Current health facts" list, the Silences list)

**Interfaces:**
- Consumes: the saved dashboard from Task 2.
- Produces: the documentation commit for the branch.

- [ ] **Step 1: Add the dashboard to the Dashboards table**

Add a row: `Homelab · Media` / `homelab-media` / `8 + 3 rows` / "Media stack: app health issues, queues, missing items, indexers, release intake, Plex sessions and library". Also add one sentence after the table saying the media panels source their names from `app_kubernetes_io_instance`, not `pod`, and that Plex bandwidth is blank at idle by design.

- [ ] **Step 2: Correct the scrape-target facts**

Replace the "45 scrape targets (38 up, 7 down)" figure and the "4 scrape targets down on 2026-11-13" bullet with the measured state: 48 active targets, 47 up, one down (`qbittorrent-exporter`), measured 2026-11-13.

- [ ] **Step 3: Correct the stale silence**

The `media-stack` `ScrapeTargetDown` silence now covers one exporter, not four. Leave the silence in place and its expiry unchanged; only the sentence describing it changes, plus a note that it can be removed once `qbittorrent-exporter` answers.

- [ ] **Step 4: Verify the document**

```bash
grep -n 'homelab-media' docs/observability-conventions.md
grep -n '48 ' docs/observability-conventions.md
grep -n 'qbittorrent-exporter' docs/observability-conventions.md
```

Expected: one hit each for the dashboard row, the target count and the exporter note.

- [ ] **Step 5: Commit the spec, the plan and the document update**

```bash
git add docs/observability-conventions.md docs/superpowers/specs/2026-11-13-media-dashboard-design.md docs/superpowers/plans/2026-11-13-media-dashboard.md
git commit -m "📝 docs(monitoring): add the media dashboard and correct the scrape facts"
```

Expected: one commit, three files, no changes under `cluster/`.

---

## Self-review

- Spec coverage: all eight panels and three rows are in Task 2 Step 1; the empty-state rule in Task 1 Step 3 and Task 2 Step 6; the documentation changes in Task 3.
- Deviation from the spec: panel 4's second query gained an `and on(indexer)` guard, because the spec's version merges 21 rows instead of 2. The spec's intent (one row per down indexer) is preserved.
- No task adds a threshold or an alert rule, matching the spec's non-goals.
