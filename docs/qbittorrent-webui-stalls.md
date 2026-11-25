# qBittorrent's Web UI stops answering during downloads

> **In one line.** The Web UI and API stop answering because one file on the downloads export leaves
> a page stuck in the node's NFS client; the config claim moved to `local-path` on 2026-11-25 and the
> stall came back six minutes later, so the config volume was not the cause.
>
> **Use it when** qBittorrent's Web UI hangs, the exporter stops being scraped, or you want the
> evidence gathered on 13 and 25 November and what cleared the hang · **Verified** 2026-11-25: 627
> failed scrapes in the previous 24 h, every one pinned at the exporter's 5 s timeout, and the hang
> reproduced and cleared on the same evening · **Kind:** record
>
> **Sources** `cluster/helm/media-stack/qbittorrent/values.yaml`,
> `cluster/helm/media-stack/qbittorrent-exporter/values.yaml`, live `up` and
> `scrape_duration_seconds` from Prometheus, kernel stacks read from `streaming` and `nas`

Status: **open. The config claim was moved off NFS on 2026-11-25 and it was not the cause**; the
stall reproduced at 18:10 the same evening. The wedge is a stuck writeback on the **downloads**
export, and the evidence, the recovery and the open question are under *The stall came back* below.
Documented 2026-11-13, measured again on 2026-11-25 (board card 758). The exporter's alert stays
silenced until it is closed or handed over; the note under *Why the app was not changed until now*
says what to do with that silence.

## What is seen

The Web UI and the Web API become unresponsive while torrents are downloading. The page hangs, and
anything that talks to the API — the exporter, cross-seed, autobrr — fails at the same time. It
sometimes clears on its own, usually within an hour or two, and it can be gone for a whole day; since
2026-11-25 it has also been seen to stay wedged until the client's mount is cleared, which is the
case under *The stall came back*.

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

## The 25 November measurement

The same symptom, twelve days later, measured from Prometheus instead of a single day's logs:

| Observation | Value |
|---|---|
| Failed exporter scrapes, last 24 h | **627** samples with `up = 0` at one per minute, about 10.4 h |
| Target availability over 48 h | **28 % down** (163 of 575 five-minute samples) |
| Scrape duration | mean 1.35 s, **pinned at 5.002 s** while failing — `exporter.timeoutSeconds` is 5 |
| Exporter log | `qBittorrent is timing out` once a minute, with gaps where a scrape succeeded |
| `ScrapeTargetDown` transitions | **106 firing/resolved in four days**, 57 of them on 25 November |

The shape matters: the target is not down for hours at a time any more. It fails for roughly a
quarter of an hour, recovers for a few scrapes, and fails again. Each success resets the rule's
15-minute `for`, so the alert cycles — 25 November alone had transitions at 10:40, 10:55, 11:29,
11:40, 11:55, 12:29, 12:40 and on through the afternoon. That is the notification spam, and it is a
property of the target flapping, not of the threshold.

The chart is already as tight as it can be without a fix: `enableTracker: false` drops the one call
that was known to be slow, and `timeoutSeconds` is 5. Neither helps when `app/preferences` blocks.

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

So the leading hypothesis was: **a disk-backed request (preferences, resume data) blocks for seconds
while memory-backed requests stay fast, and the reason it blocks for seconds is that the config
volume is on NFS.** It is consistent with every measurement above, and the measurements alone do not
prove it: no per-request timing exists in the exporter, so which call timed out is inference, not
observation.

**The move tested it and the inference was wrong.** Six minutes after `/config` came off NFS on
2026-11-25 the stall was back, and this time every request hung, memory-backed ones included: that
is a blocked session, not a slow disk-backed call. The volume that wedges is `/downloads`, on the
same server. See *The stall came back*.

## The migration, 2026-11-25

Measured before it started: 379 torrents, so 379 `.torrent` files and 379 `.fastresume` files in
`qBittorrent/BT_backup` (156 MB), and 435 MB in the whole `/config` tree.

1. `qbittorrent-config` was created on `local-path` at `2Gi`, and a copy Job mounted the NAS export
   read-only beside it and `rsync`ed the tree across while qBittorrent still ran. That first pass is
   a bulk copy, not a backup: the client kept writing.
2. Automation for the ArgoCD Application was switched off, the Deployment scaled to zero, and the
   same Job run again as a delta, with deletions. This is the only window in which the app was
   down, and it is why the copy is not a snapshot of a moving tree: 379 `.fastresume` on both sides
   and a matching listing afterwards.
3. The chart now points `/config` at the claim it owns, so the sync that followed started the pod on
   the local disk. Automation was switched back on, with the same `prune`/`selfHeal` it had before.
4. The `nfs-qbittorrent-config` export is still on `nas`, and nothing mounts it. It is the rollback
   copy: a re-copy is a two-minute Job, while the restic snapshots of it are days old. The follow-up
   card retires it, and the eight-line export list in `ansible/roles/nfs/templates/exports.j2` with
   it.

The backup row moved with the claim: `qbittorrent-config` is now a `nodes` row on `streaming` rather
than an `nfs` row, so the snapshot still holds the seed list and the resume data.

One side effect on `nas`: `snapraid diff` had been reporting about 321 changed files every run, all
of them under this export, because the client rewrites resume data continuously. Nothing writes
there any more.

## The stall came back, 2026-11-25 18:10

The exporter answered again for the first two minutes (`scrape_duration_seconds` 0.119 s, 379
torrents, `app/preferences` in 0.8 ms). At 18:11 `up` went to 0 and the duration pinned at 5.00 s
again — and this time the Web UI itself did not answer: `/`, `/api/v2/app/preferences`,
`/api/v2/torrents/info` and `/api/v2/sync/maindata` all timed out, and the listening socket held 31
connections that nothing accepted.

What the node showed, read from a privileged pod with `hostPID` and the node's root at `/host`:

| Where | Observation |
|---|---|
| qBittorrent process | one worker thread in state `D` for over 25 minutes, its syscall `close()` |
| its kernel stack | `nfs4_file_flush` → `nfs_wb_all` → `filemap_write_and_wait_range` → `folio_wait_bit`: waiting for one page to finish being written |
| which volume | `/downloads` is the only NFS mount the pod still has; `/config` is on `local-path` |
| `nas` at the same moment | every `nfsd` thread idle, `jbd2` idle, no task blocked on the disk |
| the data path | a 64 MiB direct write with `fsync` on `/mnt/ultrafaststorage` and an 8 MiB write with `fsync` through the pod's `/downloads` both returned at once; `smartctl` PASSED on both 970 EVO |
| client `dmesg` | `NFS: server 192.168.1.41 error: fileid changed`, in bursts before and after the cutover |

So the server and both disks were healthy and the filesystem was writable throughout: one page of one
file on that export was pinned in the client, and the session cannot do anything while a `close()`
waits for it.

Two things made this worse than the earlier shape of the stall:

- **A restart did not clear it.** The container was restarted at 18:41 and the fresh process was
  wedged again inside a minute. The state lives in the node's NFS client, not in the app.
- **The liveness probe cannot see it.** The probe is a TCP connect on the WebUI port; the socket
  still accepts connections while the event loop is stuck, so the probe passes and the container is
  never restarted. The 15 restarts in 12 h on 25 November were the probe catching a listener that
  had gone; now the listener stays up and the hang is invisible to it.

### The recovery that worked

`streaming` held **eight** mounts of `192.168.1.41:/mnt/ultrafaststorage/nfs`, one per qBittorrent pod
generation — the live one plus seven kubelet had never unmounted. They share a single NFS client
instance, so the pinned page belonged to all of them.

```bash
# 1. no process may hold the export: scale to 0 first
kubectl -n media-stack scale deploy qbittorrent --replicas=0

# 2. detach every leftover mount, in the host's mount namespace
#    (from a privileged pod with hostPID, the node root at /host)
for d in /var/lib/kubelet/pods/*/volumes/kubernetes.io~nfs/pv-nfs-qbittorrent-download-*; do
  umount "$d"
done

# 3. kubelet mounts it fresh when the pod comes back
kubectl -n media-stack scale deploy qbittorrent --replicas=1
```

All seven detached on the first `umount`, none needed `-f`, and the count went to zero. The new pod
answered `app/preferences` in 0.8–0.9 ms, listed 379 torrents, and Prometheus scraped it in 0.119 s.
The only errors the consumers saw were inside the 18:44–18:46 window when the app was deliberately
down: one `Failed to connect to qBittorrent` in `sonarr` and one in `sonarr-anime`.

### What is still open

Why the page ends up stuck is not settled. The `fileid changed` bursts are what a client reports when
a name it has cached now points at a different inode — what a replaced file looks like, after a mover
renames over it or through qBittorrent's own temp-file pattern — while the client still holds dirty
pages for the old inode. Which file that was, and which of the two writes caused it, needs the file's
identity, and the kernel does not expose it from userspace.

The candidates to bound it are all untested: stop the leftover mounts accumulating on each restart
(they are what makes one wedge reach every later pod generation), take the `sync` export option off a
volume written this heavily, or keep replaced names out of the client's page cache. No chart change
is made for any of them yet.

## The gate, and the way back

The config-move card closes when `scrape_duration_seconds` stays under a second for 24 h and the
torrent list is still 379. **The gate failed at its first observation**: the stall was back six
minutes after the cutover. It is now the test for whatever clears the wedge, not evidence that the
move did. Both queries, unchanged:

```bash
# the ceiling over the last day
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=max_over_time(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"}[24h])'

# failed scrapes in the same window
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=count_over_time(up{app_kubernetes_io_instance="qbittorrent-exporter"}==0[24h])'
```

The torrent count is the WebUI's own: `curl` from a pod on the pod network against
`http://qbittorrent.media-stack.svc:8080/api/v2/torrents/info` (the login bypass in
`qBittorrent.conf` covers `10.42.0.0/16`), or the exporter's own metric page.

If the local claim turns out wrong — a torrent count short, a resume file missing — the way back is
the export that is still on `nas`: stop the pod, point the chart's `storage.config` at the old claim
again (a `files` copy back is the same Job in reverse), and start it. Nothing on the export has been
deleted or modified by the move.

## Why the app was not changed until now

- Moving `/config` to `local-path` needs the pod stopped. Restarts are not free here: the chart
  records two restarts that dropped 8 and 14 torrent entries (`Failed to restore torrent`) when the
  downloads volume was not ready. A migration is a scheduled operation, not a quick edit — which is
  why the copy above is done in two passes with the app stopped for the second one only.
- **The liveness probe restarts the app on exactly this symptom, and it always has.** The chart has
  carried a TCP probe on the WebUI port since 2022, rebuilt in the 2026-11-18 chart pass; the
  probe's own comment said "it fails when the listener is gone, which is what a restart can fix",
  and during these stalls it does not fix anything. On 2026-11-25 it restarted the container **15
  times in 12 h**, each time mid-stall. A restart is also the moment the torrent list can lose
  entries: `wait-for-downloads` runs at pod start, not at a container restart, so a restart with the
  downloads export unready is the case that dropped torrents before. The probe stays, but since the
  evening of 2026-11-25 it is known to be blind to the wedge: the socket keeps accepting while the
  session is stuck, so it passes and nothing restarts. A different stall would bring the same gamble
  back.

The stopgap that went in on 2026-11-25 is a **silence**, not a rule change: `ScrapeTargetDown`
matched on `app_kubernetes_io_instance="qbittorrent-exporter"` until 2026-12-09. The rule is
correct; silencing it is what stopped the flap reaching Discord while card 758 scheduled the
migration. Remove the silence when the card closes, or renew it with a new end date — do not widen
`ScrapeTargetDown`, because that would hide a media exporter that has really died.

## How to confirm it is happening again

```bash
# is the API slow right now? (value > 4 means at least one call crossed 5s)
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=max(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"})'

# how many timeouts in the last 30 minutes (Loki is gated; ask through Grafana's datasource proxy)
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query_range" \
  --data-urlencode 'query=sum(count_over_time({namespace="media-stack",container="qbittorrent-exporter"} |= "timing out" [30m]))'

# the same symptom seen from another consumer
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="media-stack",container="cross-seed"} |= "fetch failed"'
```

The decisive test is the WebUI answering while torrents download. A restart is not a test: on
2026-11-25 it left the wedge in place, so check the qBittorrent process for a thread in `D` before
concluding the app itself is at fault.

## A related defect, already fixed

The qBittorrent pod runs `dnsPolicy: None` with public resolvers (1.1.1.1, 1.0.0.1), so it cannot
resolve in-cluster Service names at all. Its "run external program on torrent finished" hook calls
`curl http://cross-seed:2468`, which failed either immediately (`Could not resolve host`) or after
134 seconds — the time Linux waits for a TCP SYN to be answered — once per finished torrent.

That was fixed on 2026-11-13 by the commit that adds `hostAliases` to the pod
(`🐛 fix(qbittorrent): let the completion hook resolve cross-seed`). The alias points at a literal
ClusterIP, so if the `cross-seed` Service is ever recreated the hook breaks silently; that caveat is
recorded in the known gaps of `observability-conventions.md`. It was never related to the stall.
