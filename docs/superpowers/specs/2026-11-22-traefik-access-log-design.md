# Traefik request record without the URLs — design

**Status:** approved and shipped 2026-11-22 (PR #1410) · **Date:** 2026-11-22 · **Board:** homelab #544 (#410)
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
the objection: the log measured ~15 MB/day at the night-time rate once enabled, against a pipeline that
carries 0.09 GB/day. The planning estimate here was ~2.5 MB/day; the measurement superseded it on
2026-11-22 and the living pages carry the measured figure.

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
3. Two panels on **Homelab · Traefik**, reading Loki: *Failed requests (4xx/5xx)*, a logs panel of the
   lines whose `DownstreamStatus` is 400 or more, and *Requests slower than 1 s, by router*, a bar count
   of the lines whose `Duration` passed one second, per router. No path column, because there is no
   path. (Planned as a table of failed requests and a "slowest requests" ranking; a logs panel keeps
   the whole line in view, and the per-router count is what a metric query can group on without
   promoting a high-cardinality field.)
4. The byte/day figure is measured, and the query used is recorded so it can be re-measured.
5. `docs/observability-conventions.md` gains a short section saying what the router logs, what it
   deliberately does not, and why; `docs/reference/metrics.md` gains the volume row;
   `docs/reference/dashboards.md` gains the panel note.

## Non-goals

- Logging any URL, query string, header or client address. If a path is ever needed, the fix is to
  authenticate Loki first, not to widen this log.
- Stripping the hostname, the method or the backend's pod address. `defaultMode: keep` keeps them, and
  the hostname is what makes a record answer *which application* failed. They are named here as the
  residual exposure: a name and a cluster-internal address in an unauthenticated store, not a
  credential. Narrowing further means enumerating every other field, which then breaks silently when
  Traefik adds one.
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
