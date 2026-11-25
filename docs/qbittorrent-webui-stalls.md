# qBittorrent's Web UI stops answering during downloads

> **In one line.** The Web UI and API go dead because qBittorrent's own write to `/downloads` wedges
> the NFS client on `streaming`: one page is left waiting to be written for an I/O nothing performs,
> and every `close()` of that file then blocks forever. Moving `/config` off NFS did not stop it and
> neither did exporting the download volume `async`; in-flight downloads move to local disk on
> 2026-11-25, and the disk they moved to stalls the app the same way (board cards 758, 777 and 782).
>
> **Use it when** qBittorrent's Web UI hangs, the exporter stops being scraped, or you want the
> evidence gathered on 13 and 25 November, the suspects that were cleared, and what cleared the hang
> · **Verified** 2026-11-25 20:30: three live stalls read from `/proc`, the client waiting on an I/O
> that nothing performs while the server and the network measured clean, then a write burst that
> pinned the node's own SSD · **Kind:** record
>
> **Sources** `cluster/helm/media-stack/qbittorrent/values.yaml`,
> `cluster/helm/media-stack/qbittorrent-exporter/values.yaml`, live `up` and
> `scrape_duration_seconds` from Prometheus, kernel stacks read from `streaming` and `nas`

Status: **second fix deployed 2026-11-25, gate not passed yet.** The config claim was moved off NFS
on 2026-11-25 and it was not the cause; the downloads export was made `async` that evening and the
stall came back under it too. The in-flight downloads moved off the export the same evening, under
*The fix: in-flight downloads off the export*, and the WebUI stalled again within three minutes —
from the local disk the temp path landed on (*The temp path landed on the node's slowest disk*).
Documented 2026-11-13, measured again on 2026-11-25 (board cards 758, 777 and 782). The exporter's alert stays silenced until it is closed or handed over; the
note under *Why the app was not changed until now* says what to do with that silence.

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

- **A finished torrent is copied, not renamed.** The temp path and the save path are different
  filesystems, so qBittorrent copies each file and deletes the source. The torrent reaches 100 % when
  that copy finishes, and only then does the completion hook fire, so an importer never sees a
  half-copied file.
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

### What is still open

- **The temp claim's device.** `/downloads-temp` is on `sda`, the slowest disk in the node and the one
  every media config also lives on. The relocation itself is a one-off burst, but the 205 GiB still to
  download keep writing there at whatever the disk allows. A global download limit low enough for the
  disk is a preference, not a chart change, and it is the cheap test.
- **Settled, and no longer candidates:** `sync` off (tested, wedge returned); ext4 fast-commit (the
  feature is not enabled on this filesystem); a failing disk (SMART clean, no `nvme` error); the
  network and the server (no retransmission, no timeout, `nfsd` idle); the config volume (moved);
  `DiskIOWriteMode=DisableOSCache` (tested, wedge returned).
- **Untested, and the fix makes it moot for downloads:** whether the client ever *re-sends* the call
  it is waiting for. `retrans` stayed 0 through both wedges, so the call is not queued again and no
  timeout fires; `rpcdebug -m rpc -s xprt,trans` on `streaming` is what would show it. Worth running
  only if the stall returns with the write load off the export.
- **Still untested — replaced names in the page cache.** `lookupcache=none` on the downloads mount is
  the general form of the `fileid changed` messages: 48 of them today, 39 of them in the 17h hour.
  Changing it means recreating the volume, not editing the chart, because mount options come from the
  volume once it exists.
- **Still untested — a stack trace with symbols.** The instrument that names the lock and the line is
  a qBittorrent build that carries its symbols, running under the same NFS volume until the event loop
  parks.
- **Still untested — the kernel-side shape, if it returns.** The server's `rpcdebug -m nfsd -s file`
  names every file handle it serves; the client's `rpcdebug -m nfs -s file` names every file it opens.
  Run both while the `close()` stack is live and the file is named, not inferred.

The way out of the app when the event loop is parked is still the kernel-side one: stop the pod, detach
the pod's NFS mounts in the node's mount namespace, start the pod. A container restart alone does not
clear it, because the wedged state lives in the node's NFS client, which every new pod on `streaming`
joins.


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

The in-flight downloads have the same way back, and it is one preference pair rather than a copy:
clear `Session\TempPathEnabled` (WebUI: Downloads, "Keep incomplete torrents in"), or point it back at
`/downloads/incomplete/`. Nothing is lost either way — `qbittorrent-temp` holds unfinished torrents,
which the client re-fetches. The claim itself can stay: an unused empty directory costs nothing.

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
