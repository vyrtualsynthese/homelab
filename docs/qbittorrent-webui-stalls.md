# qBittorrent's Web UI stops answering during downloads

> **In one line.** The Web UI and API go dead because qBittorrent's own write to `/downloads` wedges
> the NFS client on `streaming`: one page is left waiting to be written for an I/O nothing performs,
> and a `close()` on that file then blocks for minutes at a time. Moving `/config` off NFS did not
> stop it and neither did exporting the download volume `async`; in-flight downloads moved to local
> disk on 2026-11-25, and the disk they moved to stalls the app the same way, so they went back to the
> export the same evening, which restores the load that wedges (board cards 758, 777 and 782). The fix
> that shipped is the app itself: the pod runs on `nas` since 2026-11-25 23:43 and `/downloads` is a
> directory there, not an NFS mount.
>
> **Use it when** qBittorrent's Web UI hangs, the exporter stops being scraped, or you want the
> evidence gathered on 13 and 25 November, the suspects that were cleared, and what cleared the hang
> · **Verified** 2026-11-25 22:13: five live stalls read from `/proc`, the client waiting on an I/O
> that nothing performs while the server and the network measured clean, then a write burst that
> pinned the node's own SSD — caught in the act with the two file descriptors that name the copy, and
> later with the file-level `nfs_debug` naming the file one `close()` sat on for six minutes; the
> router's access log and two 15-minute replays of the app's own write pattern against the export
> · **Kind:** record
>
> **Sources** `cluster/helm/media-stack/qbittorrent/values.yaml`,
> `cluster/helm/media-stack/qbittorrent-exporter/values.yaml`, live `up` and
> `scrape_duration_seconds` from Prometheus, kernel stacks read from `streaming` and `nas`

Status: **third fix deployed 2026-11-25 23:43, gate not passed yet.** The config claim was moved off NFS
on 2026-11-25 and it was not the cause; the downloads export was made `async` that evening and the
stall came back under it too. The in-flight downloads moved off the export the same evening, under
*The fix: in-flight downloads off the export*, and the WebUI stalled again within three minutes —
from the local disk the temp path landed on (*The temp path landed on the node's slowest disk*). They
went back to the export's NVMe at 20:44 (*The decision*), which is the load the wedge comes from:
that is a trade, not a fix. The local claim was tried again at 21:27 and taken away at 21:32 on the operator's
decision, so what follows describes the export and not the claim (*Back to the export, 21:32*). Then
the app left the NFS client altogether: the pod moved to `nas` with `/downloads` a host path, and both
configuration claims followed it — 86 seconds of downtime, 317 torrents restored, and the exporter
back at 0.086 s from a pinned 5.00 (*The fix: the app leaves the NFS client*). Documented
2026-11-13, measured again on 2026-11-25 (board cards 758, 777 and 782). The exporter's alert stays
silenced until the 24-hour gate passes or the card is handed over; the note under *Why the app was not
changed until now* says what to do with that silence.

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
4. The `nfs-qbittorrent-config` export stayed on `nas`, mounted by nothing, as the rollback copy: a
   re-copy is a two-minute Job, while the restic snapshots of it are days old. Card 769 retired it,
   and the export list in `ansible/roles/nfs/templates/exports.j2` lost its line with it.

The backup row moved with the claim: `qbittorrent-config` is a `nodes` row rather than an `nfs` row,
so the snapshot still holds the seed list and the resume data. It moved once more with the pod that
evening, from the `streaming` row to the `nas` row.

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

**It held for three minutes.** At 18:50 `scrape_duration_seconds` was pinned at 5.00 s again and a
worker was back in the same `close()` → `nfs_wb_all` → `folio_wait_writeback` stack — with a single
mount this time, so the leftover mounts are not the trigger. Detaching the mount clears the wedge; it
does not stop it.

### The stuck object is one file's fsync on the server

Read from `nas` while the second hang was live:

| Where | Observation |
|---|---|
| one `nfsd` thread | `nfsd4_write` → `vfs_fsync_range` → `ext4_sync_file` → `ext4_fc_commit` → `jbd2_log_wait_commit`, waiting for a transaction that never commits |
| `jbd2` itself | running, 5.5 ms average commit, no task blocked on the disk |
| the same filesystem | an 8 MiB write with `fsync` at 456 MB/s, at that moment |

So the disk is fine, the journal is fine, and the write that hangs is one file's fsync, waiting on a
journal commit that does not arrive. The client waits for the page that write would have completed,
and the session waits for the `close()`. Because it is one file, a fresh mount resumes onto the same
file and wedges again within minutes.

The `ext4_fc_commit` in that stack is not the fast-commit path: this filesystem has no `fast_commit`
feature at all (`tune2fs -l` on `/mnt/ultrafaststorage` does not list one), so `ext4_fc_commit` fell
straight through to `jbd2_complete_transaction` and the ordinary full commit. Turning fast-commit off
— the second candidate in the list below — is therefore not an option here: it was never on.

### The async export, and why it did not help

The first candidate — take `sync` off the downloads export — was taken on 2026-11-25 at 19:00:34:
`/mnt/ultrafaststorage/nfs` is exported `async` (commit `0f218818`), so `nfsd` acknowledges a write
without fsyncing it and a client's `COMMIT` returns at once.

The pod that came back at 18:59 mounted that export under the new options and answered normally at
19:02 (`scrape_duration_seconds` 0.098 s). By 19:07 it was pinned at the exporter's 5 s ceiling again,
and at 19:30 it was still pinned. A further pod, started at 19:24 with its own fresh mount of the same
export, was dead by 19:28. So the server-side fsync is not what wedges the client: with `sync` off
there is nothing left for a write to wait on, and the WebUI stopped answering anyway.

Measured on `streaming` at 19:20, WebUI dead, every scrape failing:

| Observation | Value |
|---|---|
| downloads still moving | **39,992 `WRITE` calls in 20 s** on the mount, about 2,000 a second and ~32 MB/s |
| other traffic on the same mount | `statfs`, `ls` and a fresh create all answer in under 5 s |
| remote procedure calls ("RPC", one NFS request) | `retrans` 0, every per-operation timeout 0, no `server not responding` in the kernel log |
| the client's connection | one TCP session to 192.168.1.41:2049, connected, nothing queued either way |
| writeback backlog | `Dirty` 1.2 MB, `Writeback` 536 kB |
| `nas` | every `nfsd` thread idle, `jbd2` idle |
| the two disks | 0 media errors, 6–7 % of life used, no `nvme` line in the day's kernel log |
| the filesystem | 66 % full, 1 % of the inodes used, no `fast_commit` feature |

And on the app itself, from `/proc`:

| Observation | Value |
|---|---|
| threads | 9 in all: one disk thread cycling through `D` for milliseconds at a time, four in `poll`, four waiting on a lock |
| the thread that serves the WebUI | parked in the kernel's lock wait (`futex_wait`, a contended mutex) on every sample over 20 minutes, never back in its event loop |
| the listener | 30 connections waiting to be accepted, and the app accepts none of them |
| metadata volume | 6.1 M `DELEGRETURN`, 71 M `OPEN` and 71 M `CLOSE` since boot, for 379 torrents |

So the two shapes are not the same. At 18:50 a thread waited **inside the kernel** for one file's
writeback — the class upstream tracks in
[#21783](https://github.com/qbittorrent/qBittorrent/issues/21783) and
[#23599](https://github.com/qbittorrent/qBittorrent/issues/23599), both closed as not planned, both
carrying this same `close()` → `nfs_wb_all` stack. At 19:20 nothing was waiting in the kernel: the
mount answered, other files were being written, and only the program's own event loop was stuck.

Whether the second is a leftover of the first (a lock taken before the stall and never released) cannot
be told from outside the binary. The image ships a static, stripped `qbittorrent-nox`, so a backtrace
of its threads resolves no function: an `addr2line` on those addresses prints `??`.

### The wedge is the client's own write, 19:30

A pod started at 19:24 with a fresh mount was dead by 19:28, and this time the blocked thread's own
kernel stack was readable. Three samples of the same thread, seconds apart:

| Sample | Stack | Meaning |
|---|---|---|
| 1 | `nfs4_file_flush` → `nfs_wb_all` → `filemap_write_and_wait_range` → `folio_wait_writeback` | `close()`, waiting for a page to finish being written |
| 2 | `nfs4_run_open_task` → `rpc_wait_for_completion_task` → `rpc_wait_bit_killable` | `open()`, waiting for the reply to an `OPEN` |
| 3 | `nfs4_do_close` → `rpc_wait_for_completion_task` → `rpc_wait_bit_killable` | `close()`, waiting for the reply to a `CLOSE` |

So the thread is not parked on one call: it cycles through them, and each waits for a remote
procedure call ("RPC", one NFS request) that never completes. What the client reported about those
calls at the same moment:

| Where | Observation |
|---|---|
| the transport's own state | `0x12`, which is `XPRT_CONNECTED` and `XPRT_BOUND`: not congested, not waiting for socket buffer space |
| the request counters | `retrans 0`, so nothing was ever re-sent |
| the socket | `ESTAB` to `192.168.1.41:2049`, both directions active within the last millisecond |
| `nas` | every `nfsd` thread idle, `jbd2` idle, no task in `D` |
| the lease | `seconds from last renew: 0`, so `SEQUENCE` calls were reaching the server |
| the page that `close()` was waiting for | `Dirty: 764 kB`, **`Writeback: 0 kB`** |

The last line names the fault: the kernel is waiting for a page that is marked "being written" while
nothing is writing it. A page in that state never finishes, so every `close()` of that file blocks
forever, and the thread that serves the WebUI waits on the session lock the blocked thread holds. It
is a wedge on the client, not a slow server and not a slow disk — no request was on the wire, and
none was ever retried.

The load that produces it, from the app's own `/proc/<pid>/io` and the client's `/proc/net/rpc/nfs`:

| Operation | Rate while downloading |
|---|---|
| `write` | ~2,000 a second, 16 KiB pieces, ~33 MB/s |
| `open` and `close` | ~2,200 a second each |
| the transport carrying it | one TCP connection, shared by all 21 mount entries `streaming` holds from `nas` |

`filemap_update_page` in one of the samples is the read side of the same file: a reader waiting for a
page the writer has left locked. Nothing inside the app can break that deadlock.

### `DisableOSCache` was tried, and it is not the trigger

`Session\DiskIOWriteMode` was `DisableOSCache` — libtorrent's "disable OS cache", which opens torrent
files `O_DIRECT`, a mode a network filesystem does not like and which forces the open/close churn
above. It was changed to `EnableOSCache` at 19:39 and the pod restarted: the API answered
`torrents/info` in 0.033 s with 381 torrents, and it was dead again at 19:41.

The setting stays at `EnableOSCache` (the sane choice for NFS, and it removes the churn). It is a
preference, so it lives on the claim and nowhere in git; the pre-change file is kept beside it as
`/config/qBittorrent/qBittorrent.conf.bak-20261125-1939`.

### The fix: in-flight downloads off the export

The write load above is what has to leave the export. `cluster/helm/media-stack/qbittorrent` now
renders `qbittorrent-temp`, a `local-path` claim mounted at `/downloads-temp`, and the app is told to
keep incomplete torrents there (`Session\TempPath`, plus `Session\TempPathEnabled=true` which was
never set). Downloading writes to `streaming`'s own SSD from then on, and the export sees one
sequential copy per finished torrent instead of thousands of small random writes.

Two consequences worth knowing:

- **A finished torrent is copied, not renamed, while the temp path is on another filesystem.** With
  the temp path on the local claim, qBittorrent copies each file and deletes the source. The torrent
  reaches 100 % when that copy finishes, and only then does the completion hook fire, so an importer
  never sees a half-copied file. **Not true after 20:44**: with the temp path back on the export, temp
  and save path share a filesystem and the finish is a rename (see *The decision*).
- **The two settings live on the config claim, not in the chart.** A rebuilt `qbittorrent-config`
  comes back with `TempPath` unset and the NFS load returns, silently. Check both keys after any
  rebuild of that claim.

### The temp path landed on the node's slowest disk, 20:10

The fix above is directionally right and it moved the load — onto `streaming`'s only large disk, which
is the slowest device in the path. `/downloads-temp` is a `local-path` claim, and `local-path` on that
node is `/dev/sda1`, mounted at `/mnt/ssd0`:

| Where | Observation |
|---|---|
| the device | `CT1000BX500SSD1`, 1 TB, **25,241 power-on hours**, SMART PASSED, no reallocated sectors, 33 °C |
| what else it holds | every media app's `local-path` config claim (the *arrs, cross-seed, maintainerr, calibre) |
| the work it was given | 22 incomplete torrents, 65 GiB already downloaded, queued for relocation into the temp path |

Between 20:12 and 20:27 that relocation wrote 39 → 92 GB. Measured on the node in the middle of it:

| Observation | Value |
|---|---|
| dirty pages waiting to be written | **1.8–2.2 GB** |
| those actually in flight | **~12 MB** |
| `sda` throughput | **24 MB/s** at 513 writes/s and a queue depth of ~15 |
| tasks in `D` | `jbd2/sda1` in `jbd2_journal_commit_transaction`, `kworker/u32:1+flush-8:0` in `wbt_wait` |
| qBittorrent's blocked thread | `ext4_buffered_write_iter` → `generic_perform_write` → `balance_dirty_pages_ratelimited` → **`balance_dirty_pages`** |

`balance_dirty_pages` is the kernel throttling a writer because too many of its pages are dirty. At
24 MB/s the disk drains far slower than the copy fills it, so the writer sleeps there — minutes at a
time — and while it does, qBittorrent's event loop stops running.

The app says so itself. The log has a 6 m 44 s gap and then names the cause:

```
20:12:33 - Start moving torrent. Torrent: "Blade Runner 2099 S01E07 One More Kiss Dear ..."
20:19:17 - System wake-up event detected. Re-announcing to all the trackers...
20:19:17 - Moved torrent successfully. Torrent: "Blade Runner 2099 S01E07 ..."
```

Through that gap the WebUI was dead to every consumer (one `Failed to connect to qBittorrent` each in
`sonarr` and `radarr`, `fetch failed` in `cross-seed`), the main thread sat in `futex_wait_queue` at
about one context switch a second, and the listening socket's accept queue grew from 25 to 32 of its
50. Not one RPC was pending: this is the local disk, not NFS.

So there are **two stalls with the same symptom**. The one this page is about is the NFS client wedge
on the export. This one is a write burst against a DRAM-less consumer SSD that has been powered on for
2.9 years; its write speed after the cache is exhausted is what the copy is limited by, not the line
rate.

What the node can offer instead is thin: the root filesystem is `nvme0n1p2`, 227 GB with 91 GB free —
fast, but not enough for a claim that holds 65 GiB of unfinished torrents today. The export's NVMe on
`nas` measured 456 MB/s under `fsync` at the worst moment of the NFS wedge. Either the download rate
is capped so `sda` keeps up, or the node needs fast storage of its own; a 400 GiB claim is not the
problem, the device under it is.

### The decision: back to the export, 20:44

The disk above is not what the temp path should sit on, and there is no fast local alternative: the
node's root is 91 GB free, the claim wants 65 GiB today. So the temp path went back to the export's
NVMe — same server, same mount, different directory:

| Setting | Value |
|---|---|
| `Session\TempPath` | `/downloads/incomplete` |
| `Session\TempPathEnabled` | `true` |
| how | `POST /api/v2/app/setPreferences` with `{"temp_path": "/downloads/incomplete", "temp_path_enabled": true}`, 200, no restart |

The move back ran at **~350 MB/s**: `/downloads-temp` fell from 102 GB to single digits in six
minutes, one torrent every few seconds, against the 40–90 s each one took onto `sda`. That is not a
surprise in hindsight — `streaming`'s `enp4s0` is a **10 GbE** link, so the export was never limited
by the wire.

Two consequences, stated so nobody rediscovers them:

- **The per-block NFS write churn is back.** This is knowingly the configuration the wedge comes from:
  the trade is a disk that keeps up and a wedge that a mount detach clears, over a disk that freezes
  the app by itself. The gate queries below are how the trade gets judged.
- **A finished torrent is renamed, not copied.** Temp and save path are on the same filesystem now, so
  the "copy, then delete the source" note under *The fix* no longer applies. The completion hook fires
  on a rename, at once.

The chart no longer renders `qbittorrent-temp`, and nothing mounts `/downloads-temp` any more: the
claim, its template, its helper and its mount were removed on 2026-11-25 (board #540), with the note
in `cluster/helm/backup/values.yaml` that explained why it was never backed up.

Ten minutes later the bill arrived: the exporter stopped answering at **20:54** and started again at
**21:16**, with one container restart in between (the pod's first since 20:09). The relocation ran
first — about 100 GB back onto the export and 96 GB of unlinks on `sda` — so that stall is the burst
and the steady churn together, not a clean test of either. What it does show is the shape of the trade:
the same load that froze `sda` is taken by the export without complaint, and still costs a stall.

### The steady churn alone wedges the client, 21:13

The 20:54 stall above cannot separate the burst from the churn. The one that followed can, because the
relocation had finished at 20:51 and nothing was moving files any more:

| Where | Observation |
|---|---|
| the WebUI | times out to `sonarr`; `curl` to the pod's own loopback times out at 6 s |
| the listening socket | **51** connections nothing accepts, backlog 50 |
| the blocked thread | `folio_wait_writeback` → `nfs_wb_all`, then `rpc_wait_for_completion_task` → `nfs4_do_close` |
| `Dirty` / `Writeback` on the node | **396 kB** / 1 MB |
| `sda` | 1.5 MB written per 10 s — the local disk is idle |
| download rate at the time | **12.8 MB/s**, 18 torrents, all writing to `/downloads/incomplete` |

So 12.8 MB/s of download is enough: the export's 456 MB/s and the 10 GbE link never enter into it. What
the client cannot take is the shape — 2,000 `open` + `write` + `close` a second — and the replay above
shows the same shape at 14,000 a second through a plain `pwrite` loop without wedging, so it is the
combination of that shape with what qBittorrent does around it, not the syscall rate on its own.

### Back to the local claim, 21:27

Measured against each other, the two placements are not close: the export wedged twice in 25 minutes,
the local claim held for 35 with the WebUI answering in 14 ms. The temp path went back to
`/downloads-temp` and the app came back at 21:27 with 91 GB to relocate onto the SSD — the burst that
freezes it in stretches, once.

The recovery has three traps that are not obvious from the 18:44 version above:

- **Argo CD's `selfHeal` puts the deployment back to one replica.** `scale --replicas=0` was reverted
  within seconds and a new pod appeared, which would have re-mounted the export mid-recovery.
  Automation is switched off on the Application for the window —
  `kubectl -n argocd patch application qbittorrent --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'`
  — and restored and verified afterwards.
- **Count the mounts inside the host's namespace, not through `/host/proc/mounts`.** The latter is
  `/host/proc/self/mounts`, so it reports the *reading process's* namespace with the host root grafted
  at `/host`, and it showed a `pv-nfs-qbittorrent-config` mount that the host no longer had. The check
  that settles it is `nsenter -t 1 -m -- grep -c pv-nfs-qbittorrent /proc/mounts`. It answered **0**:
  kubelet had already detached the download volume, and the stale config mount from pod `05412ba7`
  went with it. Nothing needed `umount`, which is a first for this procedure.
- **Edit the preference on the claim, and keep its owner.** `Session\TempPath` is a preference, so it
  lives in `qBittorrent.conf` and not in git. `sed -i` replaces the inode and leaves the file
  `root`-owned, which the app cannot write; `sed > /tmp/x && cat > file` keeps `1100:1100` and `0644`.

After the restart the app restored 325 torrents, relocated 91 GB onto the claim at roughly 4 GB a
minute, and answered `200` in **7–15 ms** throughout. That held for five minutes; *Back to the export, 21:32*
records why it did not, and the churn itself is still unexplained.

### Back to the export, 21:32

The local claim above lasted five minutes: the owner of card 777 took the export again at 21:32, on the
operator's instruction, so the state this page leaves behind is the export and the claim above holds
16 K. What is worth keeping from the five minutes is the reading that names the work, and the recovery
that follows a queue of copies rather than a stall.

**The stack names the work.** A thread of `qbittorrent-nox` in `D` (uninterruptible sleep) parked in
`balance_dirty_pages` → `ext4_buffered_write_iter` → `pwrite64`, with two descriptors open on the same
film: one in `/downloads/incomplete`, one in `/downloads-temp`. That pair is the whole diagnosis — the
thread is copying a torrent off the export onto `sda`, and no NFS state is involved.

| Reading, 21:30 | Value |
|---|---|
| dirty pages waiting to be written | **1.8 GB** |
| actually in flight | 12 MB |
| `sda` throughput | 24–50 MB/s |
| `/downloads-temp` | 16 K → 48 GB in twenty minutes |

**Three traps the 21:27 recovery did not hit**, so they sit beside its three rather than replacing them:

- **A queued move lives in memory only.** `setPreferences` re-queues the copies in the right direction,
  but the forward queue already inside the app keeps running first, and the same bytes then cross the
  disk twice. Replacing the pod drops the queue: each torrent keeps the location it had recorded, and an
  interrupted copy leaves its source whole.
- **The two trees left in `/downloads-temp` were not the same kind of thing.** The *Blade Runner 2099*
  season pack was a duplicate — its 37 GB was intact on the export — and was deleted. *Rings of Power*
  S03E07 was the only copy of a partial, so it was moved back to `/downloads/incomplete` and rechecked,
  which returned it to **42%** instead of a fresh download.
- **`POST /api/v2/torrents/recheck` takes `hashes` in the body.** A `GET` answers `405`, and a `POST`
  with the hash in the query string answers `400`.

Afterwards: `/downloads-temp` at 16 K, dirty pages 172 kB, `sda` at 0.12 MB/s, 325 torrents, the API at
3–36 ms. Eight torrents report `missingFiles`; all eight are at 0% and predate the change. The claim is
now mounted, empty, and the one place a preference can send writes onto the failing SSD — card 785
carries its removal.

### What a stall looks like from outside

Thirty minutes of samples, one every two minutes, across both of the stalls above. Times are
Europe/Paris, as the app's own log writes them.

| Observation | Value |
|---|---|
| stalls | 20:14–20:20 and 20:35–20:37, each cleared by itself in two to four minutes |
| restarts | **0** |
| healthy windows | exporter scrape 0.085–0.108 s, API 0.026–0.039 s, 323 torrents |

Three measurements taken while the app was frozen, and one around them:

| Where | Observation |
|---|---|
| the router's access log (`/var/log/traefik/access.log` on `streaming`) | the only routes that hang are qBittorrent's. In the eleven minutes of the 20:12 stall the router logged 340 requests — `authentik`, `prometheus`, `vikunja`, the public sites — and qBittorrent accounted for all six that crossed five seconds |
| qBittorrent's own static files | `GET /`, `/sw.js` and `/workbox-c35ca7bb.js` hung for 60–492 s. They are served from the program's own image and never touch the export |
| a write probe from inside the pod, between stalls | 200 MB written with `fsync` in 0.8 s; 200 files created and unlinked in under 100 ms each. The same probe from `sonarr`: 0.4 s |
| `nas` during the 20:12–20:20 stall | load 1.2–1.6, nothing blocked at the samples taken |

So every consumer on the node keeps answering while qBittorrent's HTTP workers are blocked: the frozen
part is the process. That holds for both stalls, and it is what makes them one symptom with two
different waits inside it — NFS writeback in the earlier ones, `balance_dirty_pages` on `sda` in these.

One of the two is also a user's own action, recorded because it is easy to misread as automation: at
20:14:10 a delete of 61 torrents arrived from a browser as `POST /api/v2/torrents/delete` from
`192.168.1.13` (Firefox on Windows), 2.6 KB of ids, and it answered at 20:20:20 — **six minutes ten
seconds** — with the content deleted along with the entries.

### A replay of the write pattern does not wedge the export

To hold the export and the client apart from the app, a privileged pod on `streaming` mounted the same
`192.168.1.41:/mnt/ultrafaststorage/nfs` — the kernel hands back the mount that already exists, so it
is the same NFS client instance and the same TCP connection — and replayed the shape the app produces.

| Replay | Load | Result |
|---|---|---|
| 1, 15 min | 12 threads: `open`, 16 KiB `pwrite` at a random offset, `close`, on 64 preallocated files | 12.9 M writes, **14,376 a second** (seven times the app's), worst call **1.0 s**, none over 5 s, 0 errors |
| 2, 15 min | 8 writers as above plus 6 readers on the same files — the seed-while-download shape — 300 files, `fallocate` instead of `ftruncate` | 16,000 operations a second (7,000 writes, 9,000 reads), worst call **1.0 s**, none over 5 s, 0 errors |

The two mount points are worth stating plainly: a second mount of the same `server:/path` reuses the
kernel's existing superblock, so an experiment cannot give itself different mount options on the live
export — `vers=4.1`, `nconnect=4` and `lookupcache=none` are silently ignored, and testing one means
its own export path on `nas`.

Throughput measured the same way, quiet times only:

| Test | Result |
|---|---|
| 19 GB copied from `/downloads` to `/downloads-temp` | **39.5 s**, 485 MB/s |
| 2 GB written sequentially to `/downloads` | 4.4 s, 456 MB/s |

The 485 MB/s matters against the 24 MB/s above: `sda` is not a slow device at rest, and one sequential
copy satisfies it. It collapsed under the relocation because that was thousands of random 16 KiB writes
across 22 torrents at once, which is the pattern a download actually produces.

For the export, the replay is a negative result and an important one: it carried seven times the app's
write rate for a quarter of an hour, with reads mixed in, without a single call over a second. So the
wedge is not "many small writes with an open and a close on NFS" by itself. What the replay does not
have is the app's fd handling and its own locking, which is where the next instrument points.

### The catch, 22:06: one close, six minutes

The catcher was armed again at 21:44 and it fired at 22:06:40, after three consecutive four-second
timeouts on the app's own API. The app had stopped answering at 22:06:18.

| Where | What the snapshot held |
|---|---|
| the whole node | **one** thread in `D`: `qbittorrent-nox`, an unnamed worker of the app's BitTorrent engine (`libtorrent`). qBittorrent's main thread and every other process on `streaming` were running or sleeping |
| its kernel stack | `close()` → `nfs_file_release` → `nfs4_close_context` → `nfs4_close_sync` → `nfs4_do_close` → `rpc_wait_for_completion_task` → `rpc_wait_bit_killable`: the client was waiting for that CLOSE's remote call to finish. `retrans` stayed 0, so it was never retransmitted: the request had either not gone out or gone and never been answered. A minute later it was in the same syscall at `folio_wait_bit_common`, waiting for writeback |
| its descriptors | two, and neither on the export: `/config/qBittorrent/lockfile` and the log. The descriptor being closed had already left the table, so the blocked call was on a file the thread had just written |
| the file, from `nfs_debug` | `Storage.Hunter.Simulator.No.Refunds.Edition-TENOKE/tenoke-storage.hunter.simulator.no.refunds.edition.r55` |
| the shape, in the same 14 ms | twelve cycles of `open`, `llseek`, `write` 16 KiB at an advancing offset, `flush`, `release` on that one file |
| the mount, over ten seconds of it | CLOSE 105,304,616 → 105,328,557, about **2,400 a second**, with 408 kB dirty and 328 kB in flight. `retrans` stayed 0 |
| the app | it did not answer again. The wedge ran six minutes fifty seconds and was ended by a pod replacement, not by itself |

That rules the export out as the thing that stalls, and it moves the question one level in. The mount
answered two thousand four hundred closes a second while **one** close, on one file, sat unfinished
for minutes, and no other process on the node waited on the mount. So the wait is a single file's
open context, not the server's throughput, the network, or the client's transport.

The rate limit matters when reading the window: `nfs_debug` output goes through `printk`, which allows
a burst and then throttles, so those twelve cycles are the burst the kernel let out, not a census of
the traffic.

**A defect in the catcher, for the next run.** It slices `dmesg` by absolute line number, and the ring
buffer on this kernel is full at 1,441 lines, so the slice for the fifteen-second window reads empty:
the messages are there, having pushed the oldest ones out. The `tail -60` it appends is what carried
the evidence above.

At 22:13 the pod was replaced, because the merge that removed the `qbittorrent-temp` claim and its
mount (board 540) changed the pod template, and the app came back answering in 0.00–0.01 s. Which of
the two ended the wedge cannot be told from these samples: a new pod is the exit described under
*What is still open*, and the two happened inside the same minute.

### What is still open

- **Settled, and no longer candidates:** `sync` off (tested, wedge returned); ext4 fast-commit (the
  feature is not enabled on this filesystem); a failing disk (SMART clean, no `nvme` error); the
  network and the server (no retransmission, no timeout, `nfsd` idle); the config volume (moved);
  `DiskIOWriteMode=DisableOSCache` (tested, wedge returned).
- **The instrument fired, and the reading is above** (*The catch, 22:06*): one close on one file, left
  unfinished for minutes, while the mount served 2,400 closes a second and `retrans` stayed 0. What it
  does not say is why that one CLOSE did not complete, and that is now the question: the file's state
  on the client, or `nfsd` on `nas` holding that inode. The client half is reachable with a build that
  carries symbols; the server half with `rpcdebug -m nfsd -s file` run while the client's stack is
  live.
- **A second cause, on the server, worth its own look.** `nas` carried load average 20–28 from 19:45
  to 19:55 while both 970 EVO reported idle traffic and the disk queue stayed at 0.04–0.09: two dozen
  tasks were blocked long enough to move the load average and not long enough to be caught at a
  sample. The app's 19:52 stall starts inside that window, and it is the only stall tonight that the
  client cannot explain on its own.
- **Mount options cannot be tried on the live export.** A second mount of the same
  `server:/path` reuses the kernel's existing superblock, so `vers=4.1`, `nconnect=4` or
  `lookupcache=none` are silently ignored — the option has to live on the mount that exists, which
  means its own export path on `nas` for an experiment.
- **Still untested — replaced names in the page cache.** `lookupcache=none` on the downloads mount is
  the general form of the `fileid changed` messages: 48 of them today, 39 of them in the 17h hour.
  Changing it means recreating the volume, not editing the chart, because mount options come from the
  volume once it exists.
- **Still untested — a stack trace with symbols.** The instrument that names the lock and the line is
  a qBittorrent build that carries its symbols, running under the same NFS volume until the event loop
  parks.
- **Answered for the client, still open on the server.** The client's `nfs_debug` named the file the
  blocked `close()` was on, so that half of the kernel-side shape is no longer inferred. What is left
  is the server's: `rpcdebug -m nfsd -s file` on `nas` while the client's `close()` stack is live, to
  see whether `nfsd` was serving that file, waiting on its disk, or not holding it at all.

The way out of the app when the event loop is parked is still the kernel-side one: stop the pod, detach
the pod's NFS mounts in the node's mount namespace, start the pod. A container restart alone does not
clear it, because the wedged state lives in the node's NFS client, which every new pod on `streaming`
joins.


## The fix: the app leaves the NFS client, 2026-11-25

Everything above points the same way, and none of it points at throughput, the server, or the network.

- Every recorded stuck stack was NFSv4-specific — `nfs4_do_close`, `nfs4_run_open_task` — or a page
  left in writeback for an I/O nothing performs. Nothing in the stack was in qBittorrent's own code.
- `retrans` stayed 0, `nfsd` on `nas` was idle, and the other six consumers of the same export kept
  working throughout. A saturated line or a failing disk has neither shape.
- A synthetic replay of the same pattern at seven times the rate did not wedge the export. So the
  trigger is not the volume of writes; it is the app's own pattern of open, 16 KiB write and close,
  through this client.
- On a local filesystem the pattern did not wedge it: 35 minutes of real downloads on a `local-path`
  claim answered the API in 14 ms throughout. What failed in that attempt was the disk underneath —
  `streaming`'s 25,241-hour BX500, at 24–50 MB/s — not the local path itself.

So the app moves to the machine that exports the downloads, and mounts the export as a directory on
that machine. `nas` writes `/mnt/ultrafaststorage/nfs` from its own NVMe, the same disk that already
answers the other clients at 456 MB/s. The chart change is `nodeSelector: nas` plus
`storage.downloads.hostPath`; the two configuration claims follow the pod, because a `local-path`
claim is bound to the node that first mounted it. The procedure, with the rollback, is
[`runbooks/qbittorrent-to-nas.md`](runbooks/qbittorrent-to-nas.md).

It ran on 2026-11-25: 86 seconds from stop to `2/2 Running` on `nas`, a 134 MB archive copied and
restored with the same three counts on both sides (5,935 files, 361 `.torrent`, 361 `.fastresume`), and
the app reporting 317 torrents afterwards — the number it had before the stop. The exporter's scrape
went from a pinned 5.00 s to 0.086 s, the tunnel handshook, and the *arrs were importing within a
minute. What is not yet known is whether it holds for a day; that is the gate below.

**What was not chosen, and why.** NFSv3 instead of 4.2, and `lookupcache=none` on the downloads mount,
are both plausible and both weaker: neither has evidence behind it here, both need the same
coordinated restart of seven consumers, and the second needs a volume recreated because mount options
come from the volume once it exists. They leave the client in the write path, which is where every
stack that was caught points. `DisableOSCache` was already tested and did not help.

**What this does not claim.** It does not make downloads faster and it does not explain the server
half: the one `close()` that never returned was on the client, and whether `nfsd` on `nas` was serving
that file at the time is still the open question above. If the stall comes back with the app on `nas`
and `/downloads` a local path, the NFS client is out of the picture and what is left is qBittorrent
itself — which is the answer the next catch would be worth having.

**The two complaints, answered separately.** "Unreachable from outside" was the stall, not a routing
change: `qbittorrent.kub.local` is `lan-only` behind the authentik gate by design, and it answered
`302` to the sign-in page throughout. "Downloads are slow" was the app, not the line: over the six
hours before the last wedge the reported download rate had a median of 28 MB/s with 800 peer
connections open, and no rate limit set. One `firewalled` sample appeared in that window beside
`connected`, which is worth a look on its own — a tunnel that does not forward its listening port
still downloads, from outbound peers only — but it is not what took the UI down.


## The gate, and the way back

The card closes when `scrape_duration_seconds` stays under a second for 24 h and the torrent list is
still what the app restored. **The gate failed at its first observation**: the stall was back six
minutes after the config claim moved, which is what ruled the config volume out. It is now the test
for the move to `nas`, and it is unchanged — same two queries, same 24 hours, same torrent count.

The count to compare against is the one the app reported at the move: **317** at 2026-11-25 23:43,
not the 379 the claim listed. The app has always held fewer torrents than `BT_backup` holds files
(383 at the top of the day, 316 at the bottom, and every restart drops a few), so a claim-side count
would fail a healthy move. Both queries:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
# the ceiling over the last day
curl -sG -u "ops:$PROMETHEUS_AUTH_PASSWORD" http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=max_over_time(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"}[24h])'

# failed scrapes in the same window
curl -sG -u "ops:$PROMETHEUS_AUTH_PASSWORD" http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=count_over_time(up{app_kubernetes_io_instance="qbittorrent-exporter"}==0[24h])'
```

The torrent count is the WebUI's own: `curl` from a pod on the pod network against
`http://qbittorrent.media-stack.svc:8080/api/v2/torrents/info` (the login bypass in
`qBittorrent.conf` covers `10.42.0.0/16`), or the exporter's own metric page.

Those two queries cannot tell this app stalling from `nas` losing the pod network, and on 2026-11-26
it was the second. Between 02:04 and 03:44 every `kubernetes-pods` target on a node other than `nas`
went `up = 0` — 19 of them in the 03:39 sample — while every `nas` pod target and every hostNetwork
target stayed up, `nas` retransmitted 11–20 segments a second, and the app's own state never moved:
322 torrents, `connection_status` 1, no download load. A failed scrape inside a window like that is
a different defect (board card 807). Check `count(up{job=~"kubernetes-pods.*"}==0)` beside this
exporter's `up` before reading a zero as the stall coming back.

If the local claim turns out wrong — a torrent count short, a resume file missing — the rollback copy is
gone: card 769 retired `nfs-qbittorrent-config`, its Application and its export line on 2026-11-26.
What is left is the nightly snapshot. `qbittorrent-config` is a `nodes` row of the `nas` repository,
so the way back is to stop the pod, restore that claim's directory into a fresh `local-path` claim
([`runbooks/restore.md`](runbooks/restore.md)), point the chart's `storage.config` at it, and start
it.

The in-flight downloads have the same way back, and it is one preference pair rather than a copy. They
are back where they started — `Session\TempPath=/downloads/incomplete`, `Session\TempPathEnabled=true`
— so the whole setting is that pair, applied through `app/setPreferences`, no restart and no copy.
Nothing is lost either way: an unfinished torrent is re-fetched. The `qbittorrent-temp` claim was
empty and used by nothing, and it is gone from the chart (board #540).

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

Both Grafana calls pass `--cacert ~/.config/secrets/kub-local/local-ca.pem`, because
`grafana.kub.local` is served by the cluster's internal CA; `-k` would accept whatever answered and
hand it the token.

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
# is the API slow right now? (value > 4 means at least one call crossed 5s)
curl -sG -u "ops:$PROMETHEUS_AUTH_PASSWORD" http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=max(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"})'

# how many timeouts in the last 30 minutes (Loki is gated; ask through Grafana's datasource proxy)
curl -sG --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query_range" \
  --data-urlencode 'query=sum(count_over_time({namespace="media-stack",container="qbittorrent-exporter"} |= "timing out" [30m]))'

# the same symptom seen from another consumer
curl -sG --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="media-stack",container="cross-seed"} |= "fetch failed"'
```

The decisive test is the WebUI answering while torrents download. A restart is not a test: on
2026-11-25 it left the wedge in place, so check the qBittorrent process for a thread in `D` before
concluding the app itself is at fault.

## A related defect, already fixed

The qBittorrent pod runs `dnsPolicy: None` with its tunnel's resolver (AirVPN's `10.4.0.1`;
DNS4EU's unfiltered `86.54.11.100` and `86.54.11.200` until 2026-11-27, and Cloudflare's
`1.1.1.1` and `1.0.0.1` before that), so it cannot resolve in-cluster Service names at all. Its
"run external program on torrent finished" hook calls `curl http://cross-seed:2468`, which failed
either immediately (`Could not resolve host`) or after 134 seconds — the time Linux waits for a TCP
SYN to be answered — once per finished torrent.

That was fixed on 2026-11-13 by the commit that adds `hostAliases` to the pod
(`🐛 fix(qbittorrent): let the completion hook resolve cross-seed`). The alias points at a literal
ClusterIP, so if the `cross-seed` Service is ever recreated the hook breaks silently; that caveat is
recorded in the known gaps of `observability-conventions.md`. It was never related to the stall.
