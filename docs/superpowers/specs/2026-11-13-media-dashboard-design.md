# Media dashboard — design

## Intent

The media stack now publishes app-level metrics that no screen reads. Six Servarr apps, Plex,
autobrr and flaresolverr have answered Prometheus since the exporter work of 2026-11-13, but the six
existing dashboards only see Kubernetes objects: replicas, pods, CPU, memory. An operator cannot see
a full download queue, an indexer that went down, or whether anyone is streaming without opening
four web UIs.

The new screen answers one question: **what is the media stack doing, and where is it stuck?** It
is a desk dashboard for the operator, not the TV screen (`homelab-showcase`) and not the
Kubernetes triage view (`homelab-applications`, already 9 panels).

Dashboards are not in git. They live in Grafana's SQLite database; this document and the update to
[`docs/observability-conventions.md`](../../observability-conventions.md) are the only record.

## What the stack publishes (measured 2026-11-13)

| Source | Port | Data worth charting |
|---|---|---|
| exportarr × 6 (`sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`, `lidarr`, `prowlarr`) | 9707 | `<app>_system_health_issues` with a readable `message` label, queue by status, missing items, library size |
| Plex exporter | 9594 | sessions tracked, active transcodes, playback bandwidth, per-library items and bytes |
| autobrr | 9074 | IRC links connected/healthy, release pushes approved/errored |
| flaresolverr | 8192 | request outcomes by result |
| n8n | 5678 | active workflows, execution counts |

Facts that shaped the choices:

- `sonarr_queue_total`, `radarr_queue_total` and `lidarr_queue_total` carry one series per app
  instance and one `status` value each; only `downloading` is present today.
- All five root folders report the same 38 TB free (`/movies/*`, `/audio` on one NFS share), so a
  per-app free-space panel would draw five identical bars and support no decision. Dropped.
- `plex_session_bandwidth_kbps` only exists while someone is streaming (max 21 Mbps over 30 d);
  `plex_sessions_tracked` and `plex_active_transcode_sessions` are always present, at 0 when idle.
- `flaresolverr_request_total` has one probe series (`not_detected`) and no real request traffic.
  Dropped.
- `qbittorrent-exporter` is down, so no torrent-client panel is possible.
- `or` between two metrics that share a label set keeps only the left one. Every union in these
  panels is safe only because the instances differ by `app_kubernetes_io_instance`.

## Goals

- Eight data panels in three labelled rows, each answering one question.
- Every query validated against Prometheus before the dashboard is saved.
- Tables reduced to the columns a person reads; no leaked Kubernetes metadata.
- Empty state is an empty table, never a row of zeros or a `vector(0)` filler.

## Non-goals

- Alert rules for the media stack. The ask was the screen; media alerting is a separate change.
- Any panel on torrent client state, flaresolverr, root-folder free space (above), Traefik or
  CoreDNS.
- Changing the six existing dashboards, except the target-count and silence facts in the conventions
  document.
- Rebuilding anything from the media stack itself.

## Approaches considered

### A. Extend `Homelab · Applications` with a media row

Cheapest diff, reuses the existing `$namespace` variable. Rejected: Applications already holds 9
panels and answers "is this namespace healthy in Kubernetes terms". Media questions are about app
content — queue depth, missing items, indexers — which that screen has no vocabulary for, and 14
panels on one page defeats the glance it exists for.

### B. One panel per app

A grid of six near-identical Servarr widgets. Rejected: it burns the panel budget on repetition,
tells the operator nothing at a glance, and the per-app differences are one number each.

### C. Three rows, one question per row (chosen)

Needs attention / Intake / Playback and library. Each row is a decision, the panels inside it are
the evidence, and any panel that stops earning its place can be deleted without disturbing the rest.

## Panel set

Sizes are the 24-column grid. Refresh 30 s, default range 6 h.

### Row: Needs attention

| # | Panel | Type | Size | Question | Query |
|---|---|---|---|---|---|
| 1 | Apps reporting a problem | table | 12×7 | Which app says something is wrong, and what? | `sum by (app_kubernetes_io_instance, type, message) (sonarr_system_health_issues or radarr_system_health_issues or lidarr_system_health_issues or prowlarr_system_health_issues) > 0` |
| 2 | Downloads queued | table | 12×7 | Is the queue moving, and where is it parked? | `sum by (app_kubernetes_io_instance, status) (sonarr_queue_total or radarr_queue_total or lidarr_queue_total)` |

Panel 1 columns: app, severity, problem. The `message` label is the human sentence the app itself
produced. Healthy apps emit a series with value 0 and no message, so `> 0` removes them.

### Row: Intake

| # | Panel | Type | Size | Question | Query |
|---|---|---|---|---|---|
| 3 | Missing per app | bargauge | 8×7 | How much wanted content has not arrived? | `max by (app_kubernetes_io_instance) (sonarr_episode_missing_total or radarr_movie_missing_total or lidarr_albums_missing_total)` |
| 4 | Indexers down | table | 8×7 | Which indexer failed, and is it still failing? | A: `max by (indexer) (prowlarr_indexer_unavailable) > 0`; B: `max by (indexer) (increase(prowlarr_indexer_failed_queries_total[1h]))` |
| 5 | Release intake | stat | 8×7 | Is autobrr connected and pushing releases? | `sum(autobrr_irc_connected_total)`, `sum(increase(autobrr_release_push_approved_total[1h]))`, `sum(increase(autobrr_release_push_error_total[1h]))` |

Panel 4 merges on `indexer` with `labelsToFields` + `merge` + `organize`; columns indexer, down,
failed 1 h. Panel 5 names its three series "IRC links up", "Pushed 1 h", "Push errors 1 h".

### Row: Playback and library

| # | Panel | Type | Size | Question | Query |
|---|---|---|---|---|---|
| 6 | Plex now | stat | 6×8 | Is anyone watching, and is it transcoding? | `max(plex_sessions_tracked)`, `max(plex_active_transcode_sessions)` |
| 7 | Playback bandwidth | timeseries | 9×8 | How much was streamed, and when? | `max(plex_session_bandwidth_kbps)` |
| 8 | Library | table | 9×8 | What does the server hold? | A: `max by (library) (plex_library_storage_bytes)`; B: `max by (library) (plex_library_items)` |

Panel 7 is blank while nothing plays; that is the truth, not a hole. Panel 8 columns: library,
storage (bytes), items.

## Rules applied

- Legends and table rows name the app through `app_kubernetes_io_instance`, never the pod, which
  changes on every restart.
- Units on every numeric panel: bytes for storage, kbit/s for bandwidth, none on counts.
- No thresholds except where the operator would act: none of these eight values has a defensible
  line today, so none gets a colour. Colour without a threshold is decoration.
- Tables hide `instance`, `pod`, `node`, `job`, `namespace`, `helm_sh_chart` and the
  `app_kubernetes_io_*` echo labels.

## Verification

1. Run each query through `/api/v1/query` and record the row count before saving.
2. Save through `POST /api/dashboards/db` into folder `homelab`, uid `homelab-media`.
3. Read the dashboard back and confirm eight data panels, three rows, no query errors
   (`GET /api/ds/query` on each panel), and that panel 1 renders four rows and panel 4 two rows.
4. Confirm the empty state: panel 1 shows no row for `sonarr` and `radarr`, which are healthy.
5. Update `docs/observability-conventions.md`: the new dashboard row, the current target count
   (48 targets, 47 up, only `qbittorrent-exporter` down), and the stale `media-stack` silence.

## Left for later

- Media alerting: a queue stuck at `error`, `*_system_health_issues` lasting over an hour, an
  indexer down for a day. Not in this change.
- `qbittorrent-exporter` stays a `ScrapeTargetDown` warning until it answers.

## Addendum 2026-11-13: the qBittorrent panel

The non-goal "any panel on torrent client state" was written while
`qbittorrent-exporter` was dead. The chart now runs the maintained Go exporter
(PR #1056), so the Intake row gained a fourth panel, `qBittorrent`, at 6×7:
download speed, upload speed, torrent count and free space on the download
volume, each with its own unit.

This breaks the three-panels-per-row budget by one. Accepted: the four panels
are four numbers from one source each, not a second idea, and the row answers a
single question — "is anything stopping content from arriving?". The other three
Intake panels narrowed from 8 to 6 columns to keep the row on one line.
