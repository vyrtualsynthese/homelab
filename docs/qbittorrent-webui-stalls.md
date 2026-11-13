# qBittorrent's Web UI stops answering during downloads

Status: **open**, documented 2026-11-13. Nothing has been changed to fix it yet; this file records
what was measured and what the evidence points at, so the next session starts from data instead of
from the symptom.

## What is seen

The Web UI and the Web API become unresponsive while torrents are downloading. The page hangs, and
anything that talks to the API — the exporter, cross-seed, autobrr — fails at the same time. It
clears on its own, usually within an hour or two, and it can be gone for a whole day.

There is no restart, no crash and no OOM involved. The BitTorrent session itself stays healthy
throughout: it keeps seeding, keeps its DHT nodes and keeps the peer connections it had.

## The measurement

Prometheus and Loki, 13 November:

| Observation | Value |
|---|---|
| Pod | `qbittorrent-649c57d59c-wbpn9`, started 05:20, **0 restarts** |
| Unresponsive window | API calls over 5 s from **05:20 to 12:08**, then clean |
| Exporter scrape duration | 0.03–0.05 s normally, **pinned at exactly 5.00 s** in that window (5 s is `exporter.timeoutSeconds`) |
| Exporter log | `qBittorrent is timing out` ×153, one per minute |
| cross-seed at the same time | `Request failed after N retries: fetch failed`, `No torrents found in client` |
| Torrents | 216 total, all seeding, none downloading by 17:00 |
| Session health | DHT 358 nodes, peers 20–1900, up to 30 MB/s down, `connection_status` never left 1 |
| Container CPU | 0.18 cores, flat, before, during and after |
| Container memory | 3.0–3.7 GiB working set during the stall (peak 9.81 GiB later in the day, of which 0.24 GiB is RSS) |
| Web UI probe from the workstation, 17:00 | 403 in **4.2–4.7 ms** (unauthenticated, so it measures the listener, not the token check) |

## What it is not

- **Not CPU.** 0.18 cores throughout, and the node was at 10–30 % of sixteen cores.
- **Not memory pressure.** Working set sat below a third of the 12 GiB limit for the whole stall.
- **Not storage pressure on `nas`.** Between 10:00 and 11:00 the API was timing out continuously
  while `nas` was idle: load 1.2, iowait 0.2 %, disk busy 2.5 %. The client-side NFS RPC
  retransmission counter was zero for the entire window.
- **Not a crash or an OOM.** Zero restarts, zero OOM kills in 39 h.
- **Not the whole session.** The exporter kept returning fresh peer counts and speeds — the
  in-memory data kept flowing while something else took over five seconds.

## What the evidence points at

The exporter makes several calls per scrape. During the stall **only one** of them crossed the 5 s
ceiling: the metric values it returned were fresh, so the failure was in one request, not in the
collection. The per-scrape requests are `app/webapiVersion`, `sync/maindata`, `app/version` and
`app/preferences`. The first two are served from memory; **`app/preferences` reads the
configuration**, and `/config` is an NFS claim:

```
pv-nfs-qbittorrent-config-nfs-subdir-external-provisioner
  nfs.server=192.168.1.41  path=/mnt/storage/nfs/qbittorrent-config
  mountOptions: nfsvers=4.2, rsize=1Mi, wsize=1Mi, nolock, async
```

That volume also holds one `.fastresume` file per torrent — 216 of them — plus `qBittorrent.conf`,
which is rewritten on every preference change and on every resume-data save. `docs/charts/README.md`
requires `local-path` for config and NFS only for shared media; this claim predates that rule and
was never converted.

So the leading hypothesis is: **a disk-backed request (preferences, resume data) blocks for seconds
while memory-backed requests stay fast, and the reason it blocks for seconds is that the config
volume is on NFS.** It is consistent with every measurement above. It is not proven: no per-request
timing exists in the exporter, so which call timed out is inference, not observation.

## Why nothing was changed yet

- Moving `/config` to `local-path` needs the pod stopped. Restarts are not free here: the chart
  records two restarts that dropped 8 and 14 torrent entries (`Failed to restore torrent`) when the
  downloads volume was not ready. A migration is a scheduled operation, not a quick edit.
- Adding a liveness probe on the Web UI would restart the pod on exactly this symptom, which makes
  the data loss worse. Do not do it before the cause is fixed.

## How to confirm it is happening again

```bash
# is the API slow right now? (value > 4 means at least one call crossed 5s)
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=max(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"})'

# how many timeouts in the last 30 minutes
curl -sG http://loki.kub.local/loki/api/v1/query_range \
  --data-urlencode 'query=sum(count_over_time({namespace="media-stack",container="qbittorrent-exporter"} |= "timing out" [30m]))'

# the same symptom seen from another consumer
curl -sG http://loki.kub.local/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="media-stack",container="cross-seed"} |= "fetch failed"'
```

The decisive test for the hypothesis is moving `/config` to `local-path` and watching whether
`scrape_duration_seconds` stays under a second through a download-heavy day.

## A related defect, already fixed

The qBittorrent pod runs `dnsPolicy: None` with public resolvers (1.1.1.1, 1.0.0.1), so it cannot
resolve in-cluster Service names at all. Its "run external program on torrent finished" hook calls
`curl http://cross-seed:2468`, which failed either immediately (`Could not resolve host`) or after
134 seconds — the time Linux waits for a TCP SYN to be answered — once per finished torrent.

That was fixed on 2026-11-13 by the commit that adds `hostAliases` to the pod
(`🐛 fix(qbittorrent): let the completion hook resolve cross-seed`). The alias points at a literal
ClusterIP, so if the `cross-seed` Service is ever recreated the hook breaks silently; that caveat is
recorded in the known gaps of `observability-conventions.md`. It was never related to the stall.
