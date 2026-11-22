# `backup`

Nightly copies of everything on the cluster that a rebuild cannot reconstruct, into one OVH Object
Storage bucket. Four restic repositories, one per stream: `nodes-nas`, `nodes-nuc0`,
`nodes-streaming` and `nfs`.

## The contract

1. **`values.yaml` is the coverage matrix.** A claim is backed up because a row names it, and for no
   other reason. Adding a stateful application means adding its row in the same change, with the
   measured size beside it. A row whose claim directory cannot be resolved on its node **fails that
   node's job** — silently backing up less than the matrix says is the failure this chart exists to
   prevent. The one tolerated shape is a `sqlite` row whose claim exists but holds no database yet:
   an application installed and never started prints `row-warning` and the job carries on, with the
   claim still backed up whole. A database that is present and cannot be staged is the opposite and
   still fails the job, because that is data going unbacked. Decided 2026-11-22 for Plex and Kavita,
   after the `first-streaming` job of 2026-11-20 failed on exactly those two shapes.
2. **No job initialises a repository.** `restic init` is run once by hand, four times, and never by a
   CronJob: a job that could create a repository could also create an empty one where a lost one used
   to be, and the next night's backup would look healthy.
3. **Every read is mounted read-only.** The jobs read the `local-path` roots and the NFS exports
   directly, because the claims live in eleven namespaces and a claim mount would need eleven pods.
   Nothing writes into a claim; the only writable volume is the staging `emptyDir`.
4. **Databases are copied, not read.** A `kind: sqlite` row stages each database through SQLite's
   backup API, which is consistent while the application is writing. Restic then reads the staged copy
   and skips the live file, including its `-wal`, `-shm` and `-journal` siblings. The staged copy is
   the one a restore uses. The verifier fails any `sqlite` row whose stream snapshot holds no staged
   database, so a `db:` pattern that went stale loses the copy for one night, not forever.
5. **A row's `exclude` means "not backed up".** It is applied to the claim copy *and* to staging, so an
   excluded database is not quietly staged back in. It is reserved for what is large and rebuilt from
   something else: poster art, logs, caches, an application's own rotating database copies. An entry
   without a slash is a name and excludes that name at any depth; an entry with a slash is relative to
   the claim's root. Until 2026-11-22 every entry was anchored at the root, which matched nothing for
   Plex and carried 6.73 GiB of its art in the stream; `files/prepare.py` and the top of `values.yaml`
   state the rule, and `hack/tests/test_backup_prepare.py` holds both shapes.
6. **`pv:` pins a row that would otherwise be ambiguous.** The local-path provisioner names a directory
   `<pvName>_<namespace>_<claimName>`, and a deleted claim leaves its directory behind. Two rows today
   match two directories for that reason (`pgadmin-config`, `calibre-web-automated-config`); the pin
   is what lets the job be exact instead of lucky.

## What each job does

| Objects | Runs | Reads |
|---|---|---|
| `backup-node-nas`, `backup-node-nuc0`, `backup-node-streaming` | 04:30, 05:00, 05:30 Europe/Paris | the node's `local-path` roots, its host configuration, and on the two control planes the K3s snapshot directory |
| `backup-nfs` | 06:00 | the `nas` exports: syncthing, koillection uploads, the ACME store, the qBittorrent config export |
| `backup-prune` | Sunday 12:00 | every repository, each with its own policy: `forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12`, and on the `nfs` stream `--keep-monthly 24 --keep-yearly 10` |
| `backup-verify` | 08:30 | every repository: newest snapshot age, size against budget, each `sqlite` row's staged database, the etcd copy's age |

Each node job is two containers: `prepare` (Python, stages the databases and writes the two path
lists) and `restic` (one backup run). The prune and verify jobs own no data; they are here so the
retention policy and the health check live beside what they check.

## Facts proven against the cluster

- A WAL database on a **read-only** mount opens with `mode=ro` and `backup()` produces an
  `integrity_check`-clean copy. Verified 2026-11-20 on `sonarr.db` through a temporary pod.
- `--host "$NODE"` is not optional: without it restic names each snapshot after the pod, finds no
  parent snapshot, and rescans the whole stream every night instead of only what changed.
- Restored databases are the **staged** copies under `/staging/claims/<namespace>/<claim>/`, never the
  live file in the claim.

## Sizes

Measured 2026-11-20: 12.2 GB in scope. The books library was dropped from scope by the operator on
2026-11-20, which is why this is not the 34.1 GB the first measurement found. The per-row numbers and their
exclusions are in the design,
[`docs/superpowers/specs/2026-11-20-backups-design.md`](../../../docs/superpowers/specs/2026-11-20-backups-design.md),
and the budget each stream is allowed is `streams[].budgetGiB` in `values.yaml`.

That 12.2 GB was a design figure, not a repository measurement. Measured from the bucket on
2026-11-22, `nodes-streaming`'s nightly snapshot held 10.04 GB, of which 6.73 GiB was Plex's claim
with its exclusions matching nothing; after that fix a snapshot holds about 2.7 GB, and the
repository's raw data read 6.75 GiB because the snapshots taken before it still hold the art. The
living page is [`docs/how-it-works/backups.md`](../../../docs/how-it-works/backups.md).

## Operating it

- The restore procedure and its drill log: `docs/runbooks/restore.md` (written by the same work stream,
  before the first off-box snapshot is trusted).
- The credentials: [`docs/secrets.md`](../../../docs/secrets.md).
- A failed job's log names the row that failed; fix the row, not the job. `kubectl -n backup get
  cronjob` and `kubectl -n backup logs job/<name>` are the two commands that answer most questions.
