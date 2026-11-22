# Traefik request record without the URLs — design

**Status:** draft for approval · **Date:** 2026-11-22 · **Board:** homelab #544 (#410)
**Read first:** `docs/audit/traefik-2026-11-21.md` finding "no access log"

## Intent

Give Traefik a per-request record — enough to say *which route* returned 502 or took four seconds —
without copying credentials into Loki. Today the router logs only errors: 0.9 MB/day, no request
lines at all.

## What decided the shape

Three facts, verified on 2026-11-22:

1. Traefik 3.7.19's access log field `RequestPath` is the request URI. Since 3.7.3 it can drop the
   query string (`fields.queryParameters`), but the path cannot be redacted in part, and the path
   alone still carries FileBrowser's `/share/<hash>` capability links. Header and field handling is
   keep/drop/redact on the whole field.
2. Loki runs `auth_enabled: false` and its route is `lan-only` with no gate. Every device on the LAN
   can read all of it for 30 days. Grafana's sign-in protects Grafana, not Loki.
3. URLs on this router carry bearer credentials: Plex `?X-Plex-Token=…` in client and "open in Plex"
   links (query, droppable), FileBrowser `/share/<hash>` (path, not droppable), and authentik OIDC
   `?code=…` callbacks (query, droppable).

So a full or errors-only log would put working credentials into an unauthenticated store. Cost is not
the objection: ~12,700 requests/day at ~200 bytes is ~2.5 MB/day raw, ~0.15 MB/day stored, against a
pipeline that carries 0.09 GB/day.

## Approaches considered

| | Approach | Verdict |
|---|---|---|
| 1 | **Access log, JSON, `RequestPath` and the client address dropped, query parameters dropped too** | **chosen** — keeps router, status, duration, size, TLS; no URL and no client IP ever logged |
| 2 | Full or errors-only access log into Loki | rejected: writes `?X-Plex-Token=` and `/share/<hash>` into a store with no authentication, 30-day retention |
| 3 | Metrics only, decision recorded | viable and safest, but discards the per-request outliers the card exists for |

Approach 1 keeps what the metrics cannot give — an individual slow or failed request with its
timestamp — at the granularity of the router, which on this cluster is effectively the application.

## Goals

1. `cluster/infrastructure/traefik/values-v3.yaml` — the file the runbook names as the live
   release's values, chart `42.4.1` / Traefik `3.7.19` — enables the access log as JSON, buffered,
   with `RequestPath`, `ClientAddr`, `ClientHost`, `ClientPort` and `ClientUsername` dropped and
   query parameters dropped; headers stay dropped by the chart default.
2. The live release is moved to the file with `helm` per `docs/runbooks/traefik-release.md`, and the
   release values are confirmed to equal the file.
3. Two panels on **Homelab · Traefik**, reading Loki: failed requests (4xx/5xx) in a table with time,
   router, service and status, and the slowest requests over a chosen window. No path column, because
   there is no path.
4. The byte/day figure is measured, and the query used is recorded so it can be re-measured.
5. `docs/observability-conventions.md` gains a short section saying what the router logs, what it
   deliberately does not, and why; `docs/reference/metrics.md` gains the volume row;
   `docs/reference/dashboards.md` gains the panel note.

## Non-goals

- Logging any URL, query string, header or client address. If a path is ever needed, the fix is to
  authenticate Loki first, not to widen this log.
- Authenticating `loki.kub.local`. That is its own card, filed with this work.
- Traefik v3, the 2.11 bump, or changing which apps are public.

## Constraints

- Chart `42.4.1` exposes `accessLog.{enabled,format,bufferingSize,fields}` with `defaultMode` in
  camel case under `fields`; `fields.queryParameters.defaultMode` needs Traefik 3.7.3 or later. The
  key names above come from the chart's `values.yaml`, which validates with
  `additionalProperties: false`.
- Traefik is outside GitOps: the file takes effect when `helm upgrade` runs against it, and
  `--reset-values` is required.
- One pod, `Recreate`: the change restarts Traefik, so ingress blips for a few seconds.
- The Grafana dashboard lives in Grafana's database; the change is made through the API and the panel
  is documented, not committed as JSON.

## What is left to the builder

Panel queries, the exact wording of the docs section, and the measurement window. A 24-hour figure is
preferred; if the session ends first, record the rate measured over a settled window plus the
request count from Prometheus, label it an extrapolation, and leave the re-measure query in the page.
