# qbittorrent-exporter

A single Go binary that reads the qBittorrent Web API and publishes Prometheus
metrics on `/metrics`. This chart deploys one replica, pinned to `streaming`, and
renders the four `prometheus.io/*` annotations itself so the scraped port and the
listening port are the same value.

## Why this image and not the old one

The chart used to run `caseyscarborough/qbittorrent-exporter:v1.3.5`. That image
was last released in October 2023 and cannot authenticate against qBittorrent
5.2.x, which is what this cluster runs. Two hardcoded expectations broke:

| What the exporter v1.3.5 expects | What qBittorrent 5.2 does |
|---|---|
| HTTP 200 from `/api/v2/auth/login` | HTTP 204 with no body |
| a cookie named `SID` | a cookie named `QBT_SID_<webui port>` |

The result was `ApiException: Could not log in: (204)` on every scrape, a 500 on
`/metrics`, `up = 0`, and no qBittorrent series at all. Upstream issue
[#34](https://github.com/caseyscarborough/qbittorrent-exporter/issues/34) tracks
it and is still open; `ghcr.io/martabal/qbittorrent-exporter:v2.0.4` handles both
changes and is the image this chart runs.

## The cookie name

`exporter.cookieName` defaults to `QBT_SID_8080` because the `qbittorrent` pod
in this namespace listens on 8080. If that port ever changes, the cookie name
changes with it, and the failure looks like an authentication error even though
the login succeeded. A 403 after "New cookie for auth stored" in the exporter
log means this value is wrong.

qBittorrent 5.2 also supports API keys (`QBITTORRENT_API_KEY`), which removes the
cookie-name coupling. That needs a key created in the Web UI; the chart uses the
existing `qbittorrent-exporter` Secret (`username`, `password`) instead, so no
manual step is required.

## Probes

`/healthz` answers without contacting qBittorrent, so both probes use it. The
contract asks readiness to fail when a dependency is down; here the only way to
do that is to probe `/metrics`, which triggers a full collection on every kubelet
check. A qBittorrent outage is caught by the `up` metric and the
`ScrapeTargetDown` alert instead, and the pod stays Ready so it can recover
without a restart loop.

## Metrics

The exporter publishes `qbittorrent_*` series in three groups:
`qbittorrent_global_*` for totals, `qbittorrent_app_*` for the version and the
rate limits, and `qbittorrent_torrent_*` per torrent.

The Java exporter used the same three prefixes but not the same series names or
label sets, so a dashboard written against it would need checking. No dashboard
in this cluster referenced any `qbittorrent_*` metric before this change, so
nothing broke.
