# Backups

> **In one line.** Every night, four `restic` repositories (restic is the backup program: it stores
> encrypted, deduplicated snapshots — one point-in-time copy each — in one OVH bucket) carry the data
> a rebuild cannot recreate: application configuration, the PostgreSQL dumps and their roles,
> syncthing, koillection's uploads, and the etcd snapshot copies.
>
> **Use it when** you are adding a stateful application, a job went red, a stream looks stale, or you
> are weighing what a loss would cost · **Verified** 2026-11-22: a claim was rebuilt from its snapshot
> and the application ran from it · **Kind:** living page

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
| `nodes-nuc0` | `backup-node-nuc0` | 05:00 | ten claims from nine applications, the node's `/etc`, the etcd copy |
| `nodes-streaming` | `backup-node-streaming` | 05:30 | the media stack's configuration, the node's `/etc`, the etcd copy |
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
rebuild needs from vanishing with the disk. The etcd copy rides in the same stream until the official
K3s S3 upload lands with the 1.31 upgrade (W5).

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
| etcd copies | 1.11 GB | 1.11 GB |

The first snapshot costs about €0.09; with the shared tail the three rebuildable repositories settle
between 15 and 25 GB, roughly €0.11–0.18 a month. The `nfs` repository is 3.6 GB today and grows only
as the photos and documents change. Egress is free, so a restore costs nothing but time.

`budgetGiB` in `values.yaml` is the same number as a ceiling: the verifier fails a stream whose
repository passes it. The four budgets sum to 60 GiB, well above the top of the expected range: the
`nodes-streaming` ceiling carries a separate etcd copy per retained snapshot, and the `nfs` one holds
ten years of yearly versions of the photos and documents. So a stream that picks up a directory it
should not have still trips the check long before the bill notices. Past its budget, raise the number
deliberately; a stream hundreds of gigabytes over it is a directory that should not be in a row.

## The four ways it fails

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
   past its budget means a row is carrying something it should not.
3. **A `sqlite` row stops staging its database.** The job stays green: the claim itself is still
   backed up, so nothing fails, and a `db:` pattern that upstream moved or renamed matches nothing
   every night from then on. What disappears is the consistent copy the row exists for, the one a
   restore uses. The verifier reads the file list of the stream's newest snapshot and fails any
   `sqlite` row with nothing under `claims/<namespace>/<claim>/`, naming the row, so the fix is the
   row and not the job. A freshly installed application looks the same on its first night, which is
   the point: a database that never appears is visible the next morning rather than never.
4. **The bucket is unreachable or the password is lost.** The jobs fail loudly; the password and the
   bucket's key pair are in Bitwarden (`homelab-backup-last-resort`) and, for the password, nowhere
   else, and without it the bucket is ciphertext. That is the one loss no monitoring can repair, and
   it is why one vault holds the whole chain: [`runbooks/last-resort.md`](../runbooks/last-resort.md)
   is the page for the morning it happens.

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

Still not proved: restoring an etcd snapshot. That is destructive in a way the others are not — it
replaces the cluster's whole object state — so it is rehearsed into a test cluster, and it is card #8.
