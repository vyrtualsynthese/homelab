# Backups

> **In one line.** Every night, four `restic` repositories (restic is the backup program: it stores
> encrypted, deduplicated snapshots — one point-in-time copy each — in one OVH bucket) carry the data
> a rebuild cannot recreate: application configuration, the PostgreSQL dumps and their roles,
> syncthing, koillection's uploads, and the etcd snapshot copies.
>
> **Use it when** you are adding a stateful application, a job went red, a stream looks stale, or you
> are weighing what a loss would cost · **Verified** 2026-11-20 · **Kind:** living page

The design is [`superpowers/specs/2026-11-20-backups-design.md`](../superpowers/specs/2026-11-20-backups-design.md);
the code is [`cluster/helm/backup/`](../../cluster/helm/backup/README.md); the restore procedure and
its drill log are [`runbooks/restore.md`](../runbooks/restore.md).

## One bucket, four repositories

One OVH Object Storage bucket in `GRA`, 1-AZ Standard class, €7.10 per TB-month. Each stream is its
own restic repository inside it, so a broken stream cannot damage another's history.

| Repository | Written by | Runs (Europe/Paris) | Holds |
|---|---|---|---|
| `nodes-nas` | `backup-node-nas` | 04:30 | the PostgreSQL dumps and their `globals.sql`, Grafana's database |
| `nodes-nuc0` | `backup-node-nuc0` | 05:00 | eleven applications' configuration, the node's `/etc`, the etcd copy |
| `nodes-streaming` | `backup-node-streaming` | 05:30 | the media stack's configuration, the node's `/etc`, the etcd copy |
| `nfs` | `backup-nfs` | 06:00 | syncthing, koillection's uploads, the ACME store, the qBittorrent config |

`backup-prune` runs Sunday at noon: 7 daily, 4 weekly, 12 monthly. `backup-verify` runs at 08:30 and
is the one that says whether a stream is still advancing.

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

The first snapshot costs about €0.09; with the retention tail the repositories settle between 22 and
48 GB, roughly €0.16–0.34 a month. Egress is free, so a restore costs nothing but time.

## The three ways it fails

1. **A row resolves to nothing.** The claim was renamed, deleted, or moved to another node. That
   node's job fails and `JobFailed` fires; the fix is the row, not the job. The job never silently
   backs up less than the matrix says.
2. **A stream stops advancing.** Every job can be green while a repository receives nothing. The
   verifier compares each repository's newest snapshot against 26 hours and its size against
   `budgetGiB`; `BackupStreamStale` alerts when a job's last success is older than 26 hours. A stream
   that grows past its budget means a row is carrying something it should not.
3. **The bucket is unreachable or the password is lost.** The jobs fail loudly; the password is in
   the password manager and nowhere else, and without it the bucket is ciphertext. That is the one
   loss no monitoring can repair.

## What has been proved, and what has not

Proved on 2026-11-20: a WAL database on a read-only mount can be copied through SQLite's backup API
into an `integrity_check`-clean file; the two ambiguous rows fail until they name their PV; the
values file resolves every row against the live cluster's directories.

Not yet proved: that a snapshot restores. No drill is recorded in
[`runbooks/restore.md`](../runbooks/restore.md), so the honest statement is that the jobs run and
the files are in the bucket — not that the data comes back.
