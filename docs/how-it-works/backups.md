# Backups

> **In one line.** Every night, four `restic` repositories (restic is the backup program: it stores
> encrypted, deduplicated snapshots — one point-in-time copy each — in one OVH bucket) carry the data
> a rebuild cannot recreate: application configuration, the PostgreSQL dumps and their roles,
> syncthing and koillection's uploads. The cluster's own database sits in the same bucket, uploaded
> by K3s itself.
>
> **Use it when** you are adding a stateful application, a job went red, a stream looks stale, or you
> are weighing what a loss would cost · **Verified** 2026-11-22: a claim was rebuilt from its snapshot
> and the application ran from it, and a production etcd snapshot restored into a throwaway cluster ·
> **Kind:** explanation
>
> **Sources** `cluster/helm/backup/values.yaml` — the matrix, one row per claim —
> `cluster/helm/backup/files/verify.py`, `cluster/helm/backup/files/prepare.py`,
> `cluster/helm/databases/postgres/templates/configmap-dump-script.yaml`

The design is [`superpowers/specs/2026-11-20-backups-design.md`](../superpowers/specs/2026-11-20-backups-design.md);
the code is [`cluster/helm/backup/`](../../cluster/helm/backup/README.md); the restore procedure and
its drill log are [`runbooks/restore.md`](../runbooks/restore.md). When the cluster itself is gone, the
page is [`runbooks/last-resort.md`](../runbooks/last-resort.md): a laptop, the vault and the bucket,
with no `kubectl` at all.

## One bucket, four repositories

One OVH Object Storage bucket in `GRA`, 1-AZ Standard class, €7.10 per TB-month. Each stream is its
own restic repository inside it, so a broken stream cannot damage another's history.

| Repository | Written by | Runs (Europe/Paris) | Holds |
|---|---|---|---|
| `nodes-nas` | `backup-node-nas` | 04:30 | the PostgreSQL dumps and their `globals.sql`, Grafana's database |
| `nodes-nuc0` | `backup-node-nuc0` | 05:00 | ten claims from nine applications, the node's `/etc` |
| `nodes-streaming` | `backup-node-streaming` | 05:30 | the media stack's configuration, the node's `/etc` |
| `nfs` | `backup-nfs` | 06:00 | syncthing, koillection's uploads, the ACME store, the qBittorrent config |

The syncthing application's own chart carried a restic CronJob until 2026-11-20. It pointed at an
AWS bucket that never existed, so it had never written a snapshot; the `nfs` row above is what
covers that folder now.

`backup-prune` runs Sunday at noon and keeps one snapshot a day for a week, one a week for a month,
one a month for a year. The `nfs` repository reaches further, because it is the one holding data no
source can re-supply: two years of monthly snapshots and ten years of yearly ones. `backup-verify`
runs at 08:30 and is the one that says whether a stream is still advancing.

### How far back an undo reaches

| The snapshot you get | `nodes-nas`, `nodes-nuc0`, `nodes-streaming` | `nfs` |
|---|---|---|
| the last week | nightly | nightly |
| the last month | weekly | weekly |
| the last year | monthly | monthly |
| the last two years | gone | monthly |
| years three to ten | gone | yearly |
| older than ten years | gone | gone |

The horizon is the operator's decision of 2026-11-22, recorded in
[`cluster/helm/backup/values.yaml`](../../cluster/helm/backup/values.yaml) as `retention:` on the
`nfs` stream. A mistake found within a week has a nightly snapshot on every stream, and one found a
few months later has a monthly; on `nfs`, a photo deleted nine years ago is still in a yearly
snapshot.

## The coverage matrix is `values.yaml`

A claim is backed up because a row in
[`cluster/helm/backup/values.yaml`](../../cluster/helm/backup/values.yaml) names it, and for no other
reason. That file is therefore the list to edit when an application with a claim is added or removed,
in the same change as the application.

| Row kind | What the job does |
|---|---|
| `sqlite` | copies each database through SQLite's backup API into the staging volume, then hands restic the staged copy and the claim *without* its live database files |
| `files` | hands restic the claim directory as it is |

A row may carry an `exclude` list, and that list is the statement of what is not backed up: poster
art, caches, an application's own rotating copies. It is reserved for what is large and rebuilt from
something else. A row may also carry `pv:` when two directories on the disk match the same claim name
— a deleted claim leaves its directory behind, and the job refuses to guess.

Alongside the claims, the two control-plane jobs copy `/host/etc/rancher/k3s`, `/host/etc/exports`,
`/host/etc/netplan` and a short list of systemd units. That is not a rebuild; it stops the files a
rebuild needs from vanishing with the disk.

## The etcd snapshots, which restic does not carry

The cluster's own database is a fifth prefix in the bucket, `etcd/`, and not a repository inside it.
Each control-plane server uploads its own snapshot there every six hours, named
`etcd-snapshot-<server>-<unix seconds>.zip`; three servers at four a day keeps the newest 180
objects, about fifteen days. The access key is not on the nodes: it lives in the Secret
`kube-system/k3s-etcd-snapshot-s3-config` (type `etcd.k3s.cattle.io/s3-config-secret`), which K3s
reads on every snapshot, and `/etc/rancher/k3s/config.yaml` names it along with the bucket, the
`etcd/` prefix and the retention.

Those uploads are the live record of `nuc0`, `streaming` and `rbp2`, and the one piece of the
cluster a rebuild cannot re-derive from git: every Secret, every claim object, the ArgoCD
Applications and the PVCs come back with them. [`runbooks/restore-etcd.md`](../runbooks/restore-etcd.md)
is the procedure and the drill log.

From 2026-11-20 to 2026-11-25 the two control-plane jobs also copied the snapshot directory into
their restic streams, as the bridge until this upload landed. Those copies are still inside the
snapshots the monthly tail keeps, and are still restorable; no night adds one any more, and
`backup-verify` reads the uploads rather than the copies.

## What is deliberately not backed up

| Not backed up | Why |
|---|---|
| music, the media library, the stash library, downloads | by the operator's decision: bulk data that a source can re-supply, and far larger than the rest combined |
| the books library (22 GB on `nas`) | by the operator's decision on 2026-11-20: re-acquiring a book is possible, and it was the single largest thing in scope — dropping it took the bill from €0.24 to €0.09 for the first snapshot |
| Loki logs, Prometheus metrics | reconstructed by what writes them; the history is not worth €7.10 per TB here |
| the edge Pis' own state | deferred; they are outside the cluster and their restic path is documented in the Ansible audit |
| poster art, thumbnails, caches, application log directories | rebuilt from the metadata providers on the next refresh |

## What the numbers are

Measured on 2026-11-20, after every exclusion: **12.2 GB** in scope. The first measurement was
34.1 GB; the books library left scope the same day.

| Stream | On disk | After exclusions |
|---|---|---|
| `nodes-streaming` | 12.9 GB | 3.7 GB |
| `nodes-nuc0` | 0.94 GB | 0.25 GB |
| `nodes-nas` | 3.10 GB | 0.10 GB |
| `nfs` | ≈ 3 087 GB | 6.9 GB |

The etcd snapshots left the streams on 2026-11-25. The 1.11 GB row they used to add is what the
older `nodes-nuc0` and `nodes-streaming` snapshots still hold, one copy per retained snapshot; no
night adds a new one, and the uploads themselves are 6–7 MB each per server.

Those are the design's numbers. The repository disagreed with the first row from its first night,
and the reason was a defect rather than a larger library: on 2026-11-22 the `nodes-streaming`
snapshot held **10.04 GB**, of which 6.73 GiB was Plex's claim. Every exclusion in that row named
a directory Plex keeps three levels down, under `Library/Application Support/Plex Media Server/`,
and the job anchored each entry at the claim's root, so none of them matched — poster art, the
transcoder cache, the thumbnails and Plex's own eight dated database copies had been carried
since 2026-11-20. The row and the job were fixed that evening. The next run read **2.56 GiB**:
2.05 GiB of staged databases, 0.51 GiB of etcd copies, a few megabytes of configuration, and 785
files instead of 46 500. An exclusion without a slash is now a name at any depth, and one with a
slash is relative to the claim's root.

The three snapshots taken before the fix were then forgotten by hand, and that was a choice rather
than a necessity: `keep-daily 7` drops them once they are eight days old, so the prune of
2026-11-29 would have freed the art on its own — the monthly tail keeps the newest snapshot of
November, which is a post-fix one, not an old one. What the hand forget bought was the measurement
and the budget below, a week early, at the cost of the restore points for 2026-11-21 and the
morning of 2026-11-22. A snapshot of the same evening was read first, and its staged Sonarr
database restored with `pragma integrity_check` → `ok`.

One gap is worth naming, because the tail cannot close it: `forget` groups snapshots by host and
paths, and the `etcd-pre131-streaming` copy left from the 1.31 move carries a host of its own, so it
is a group of one and no `keep-*` rule will ever remove it. It costs almost nothing — it holds the
same streaming snapshot directory the nightly already carries — and dropping it is a by-hand decision,
not a scheduled one. Two companions tagged `etcd-pre131-{rbp2,nas}` held that same directory and were
forgotten by hand on 2026-11-22, once the 1.31 run's file names were read: they were one server's
state under two other node names.

The first snapshot costs about €0.09; with the shared tail the three rebuildable repositories settle
between 15 and 25 GB, roughly €0.11–0.18 a month. The `nfs` repository is 3.6 GB today and grows only
as the photos and documents change. Egress is free, so a restore costs nothing but time.

`budgetGiB` in `values.yaml` is the same number as a ceiling: the verifier fails a stream whose
repository passes it. The four budgets sum to 55 GiB, well above the top of the expected range: the
`nodes-streaming` ceiling was raised for a separate etcd copy per retained snapshot, which the older
snapshots still hold, and the `nfs` one holds ten years of yearly versions of the photos and
documents. So a stream that picks up a directory it should not have still trips the check long
before the bill notices. Past its budget, raise the number deliberately; a stream hundreds of
gigabytes over it is a directory that should not be in a row.

## The five ways it fails

1. **A row resolves to nothing.** The claim was renamed, deleted, or moved to another node. That
   node's job fails and `JobFailed` fires; the fix is the row, not the job. The job never silently
   backs up less than the matrix says.

   One shape is deliberately not a failure: a `sqlite` row whose claim exists but holds no database
   yet, which is an application installed and never started. The job prints `row-warning` for that
   row and backs the claim up whole, so a first night does not read as a broken backup — the case
   that made two jobs look failed on 2026-11-20. A database that is present and cannot be staged is
   the opposite and still fails the job: that is data the backup could not read. Both halves of the
   rule are stated in `cluster/helm/backup/README.md` and at the top of `values.yaml`, and the
   tolerance is not permanent — case 3 is what notices.
2. **A stream stops advancing.** Every job can be green while a repository receives nothing. The
   verifier compares the newest snapshot *the nightly job wrote* against 26 hours and the
   repository's size against `budgetGiB`; `BackupStreamStale` alerts when a job's last success is
   older than 26 hours, and `BackupJobAbsent` when the job itself is gone or has never succeeded —
   the case with no timestamp left to compare. Only snapshots tagged `stream=<name>` count: a copy
   taken by hand in between, like the etcd copy from the 1.31 upgrade, lands in the same repository
   with a later timestamp, and reading it would make a stopped stream look fresh. A stream that grows
   past its budget means a row is carrying something it should not. The etcd uploads are checked on
   the same run: `backup-verify` asks each control-plane server for its newest object in `etcd/`,
   prints `etcd-s3 server=<name> file=… age_h=…`, and fails the morning when one is missing or older
   than 26 hours — a server that stops uploading looks exactly like a healthy one until the day it is
   needed.
3. **A `sqlite` row stops staging its database.** The job stays green: the claim itself is still
   backed up, so nothing fails, and a `db:` pattern that upstream moved or renamed matches nothing
   every night from then on. What disappears is the consistent copy the row exists for, the one a
   restore uses. The verifier reads the file list of the stream's newest snapshot and fails any
   `sqlite` row with nothing under `claims/<namespace>/<claim>/`, naming the row, so the fix is the
   row and not the job. A freshly installed application looks the same on its first night, which is
   the point: a database that never appears is visible the next morning rather than never.
4. **A `files` row stops holding what it exists for.** A row can resolve, be copied, and be empty:
   the PostgreSQL dumps live on a claim whose directory survives a stopped `postgres-dump` CronJob,
   so every stream stays green on a claim that stopped growing, and the 14-day retention removes the
   last usable dump without a single failed night. Two alert rules watch the producer itself —
   `PostgresDumpStale` past 26 hours and `PostgresDumpAbsent` when it is gone — and the verifier is
   the second half: a `files` row may declare `mustMatch`, and the `postgres-dumps` row declares
   `*.dump`, so a newest snapshot with no dump under that claim fails the morning run and names the
   row.
5. **The bucket is unreachable or the password is lost.** The jobs fail loudly; the password and the
   bucket's key pair are in Bitwarden (`homelab-backup-last-resort`) and, for the password, nowhere
   else, and without it the bucket is ciphertext. That is the one loss no monitoring can repair, and
   it is why one vault holds the whole chain: [`runbooks/last-resort.md`](../runbooks/last-resort.md)
   is the page for the morning it happens.
6. **Nobody drills a restore.** The drill log is the only proof the bytes come back, and a year
   without a drill looks exactly like a healthy system. `RestoreDrillOverdue` warns when the newest
   row of that log is more than 90 days old, or when it cannot read the log at all; the fix is a
   drill, not a job. The log and its machine-readable copy are named in
   [`runbooks/restore.md`](../runbooks/restore.md#the-drill-log).

## The two warnings, and why they do not fail a night

A `sqlite` row whose claim holds no database yet prints `row-warning` and the job carries on
(case 1), and a row's `exclude` entry that matches nothing prints `exclude-unused` and the job
carries on as well. Both answer the same question — is this application installed and running, or is
this row describing a tree that is no longer there — and both are deliberately not failures: a
working night must not read as a broken backup, which is what an empty `cache/` on a first night
would look like.

The unused exclusion is the one that leaves nothing to spot in the snapshot: an excluded path is
absent whether or not it exists, so the verifier cannot see it. The node job therefore writes the
entries it could not match beside the staged databases, and `backup-verify` repeats them at 08:30 as
`exclude-unused stream=<name> row=<namespace>/<claim> pattern=<entry>`. It is a line, not a
`problem`: an exclusion that matches nothing does not lose data, it carries more than the matrix
says, and `budgetGiB` is the ceiling that bounds that. The five Plex entries that matched nothing
from 2026-11-20 to 2026-11-22 are the case this is written for: six green nights, 6.73 GiB of
poster art in every snapshot, and nothing anywhere that said a row's exclusion was doing nothing.
The `nfs` stream has no prepare step, so its job counts its own words: `backup-nfs.sh` prints
`exclude-unused path=<tag> pattern=<entry>` before an export's restic call when one of its words
matched nothing, with the same reading — a bare name at any depth, a path from the export's root. The
line stays in that job's log and the morning check reads it there: `kubectl -n backup logs job/<the
night's backup-nfs job>`, or in Loki, `{namespace="backup"} |= "exclude-unused"`. `backup-verify` does
not repeat it the way it does the node rows: the verifier reads snapshots, and the only writable tree
the nfs job has is the export itself, so a report it could see would have to be written into the
thing being backed up.

## What has been proved, and what has not

Proved on 2026-11-20: a database is copied through SQLite's backup API into a clean file even when
the claim is mounted read-only — a WAL database whose `-shm` cannot be created is first copied into
the staging directory, where the log is replayed and a committed frame is not lost. The two
ambiguous rows fail until they name their PV, and the values file resolves every row against the
live cluster's directories.

Proved on 2026-11-21: a snapshot restores. A scratch restic pod brought back the PostgreSQL dump
directory from `nodes-nas` (26 dumps plus `globals.sql`), Sonarr's `sonarr.db` from the staging
directory in `nodes-streaming` (which then passed `pragma integrity_check`), and one Syncthing photo
from `nfs` whose sha256 matched the live file. The three rows are in the drill log of
[`runbooks/restore.md`](../runbooks/restore.md).

Proved on 2026-11-22: the same read works with no cluster at all. `restic` on a laptop, given only
the values in the Bitwarden item, listed all four repositories and decrypted a tree from `nfs`; the
on-disk copies of the password were moved aside for the run. The row is in the drill log of
[`runbooks/restore.md`](../runbooks/restore.md) and the procedure is
[`runbooks/last-resort.md`](../runbooks/last-resort.md).

Proved on 2026-11-22, the put-back: Sonarr's claim was rebuilt from that morning's `nodes-streaming`
snapshot and the application ran from it. It was stopped, its tree moved aside inside the claim, the
restored database and configuration copied in, and it came back — 32 seconds down — answering its API
with the same 103 series and 566 episode files the database held, writing to the restored
`sonarr.db`, and syncing `Healthy` in ArgoCD. The procedure and the two traps that would trip a first
attempt (the pod selector, ArgoCD's self-heal) are in
[`runbooks/restore.md`](../runbooks/restore.md) §2.

Proved on 2026-11-22, the whole object state: the newest production etcd snapshot from
`nodes-streaming` was restored into a throwaway single-node K3s `v1.31.14+k3s1`, and 25 namespaces,
66 Deployments, 3 StatefulSets, 47 PVCs, 115 pods, 376 Secrets and 60 ArgoCD Applications came back.
A ConfigMap written before the restore was gone afterwards, the reset took 7 seconds and the start
9–11 more, and `backup-restic`'s `RESTIC_PASSWORD` was byte-identical to the local copy. The
procedure, the token requirement and the three failed attempts before it are
[`runbooks/restore-etcd.md`](../runbooks/restore-etcd.md).

Not proved: that restore on a live server, where the other two servers have to rejoin the reset node,
and a snapshot fetched from the OVH copy rather than from the node's own stream.
