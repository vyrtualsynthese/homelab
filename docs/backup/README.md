# Backups

> **In one line.** The configuration and the databases leave the house every night for one OVH bucket
> in Gravelines, the bulk media stays on one disk by decision, and the bytes have been read back with
> the cluster and without it.
>
> **Use it when** you are weighing a risk, a disk has died, a claim will not start, or you are
> planning the morning after a total loss · **Verified** 2026-11-22 · **Kind:** explanation
>
> **Sources** `cluster/helm/backup/values.yaml`, `docs/how-it-works/backups.md`,
> `docs/runbooks/restore.md`, `docs/runbooks/last-resort.md`

Two honest facts first, because they decide everything else on this page.

**The bulk media is one copy by decision.** The film, television and music libraries are large and
replaceable, and copying them nightly would cost more than the collection is worth to this homelab. A
fire, a flood or a failed array ends them. What the bucket buys is the part a rebuild cannot invent:
the configuration, the databases, the certificate store and the keys.

**"The data comes back and is readable" is proved. "A cluster comes back" is not.** Four restores have
been run read-only, and one claim was put back onto its live volume and served from the restored
database. A total rebuild — hosts, etcd, then data — has never been rehearsed end to end. The drill log
in [restore.md](../runbooks/restore.md#the-drill-log) says which rows are proved and which are not, and
it is the only evidence that counts.

## What is protected

One bucket, four restic repositories, six CronJobs, every night, in this order: the `nas` configuration
and the PostgreSQL dumps at 04:30, `nuc0` at 05:00, `streaming` at 05:30, the NFS exports at 06:00, then
a verifier and a prune. `BackupStreamStale` tells you when one stream stops advancing; a job that fails
says so the same night.

The coverage matrix — which claim, which stream, which row — is [the coverage table](coverage.md),
generated from `cluster/helm/backup/values.yaml` so it cannot lag behind it. What each stream
holds in prose is [how backups work](../how-it-works/backups.md).

## What is not

- The media libraries, the music and the books, by decision.
- Observability history beyond its thirty days.
- Group membership in authentik, which is an access decision rather than configuration.
- The edge Pis' own state, in their compose files outside this repository.

## Pick the page for the situation

| If this happened | Read |
|---|---|
| nothing yet, and you are weighing the risk | [what a loss costs](what-a-loss-costs.md) |
| you want to know how the nightly jobs are built | [how backups work](../how-it-works/backups.md) |
| a file, a directory or a whole claim is gone | [a file, or a whole claim](a-file-or-a-claim.md) |
| an application's database is broken but its claim is fine | [a database inside an application](a-database.md) |
| a database on the shared PostgreSQL is broken | [PostgreSQL](postgresql.md) |
| a whole machine is dead | [a dead node](a-node.md) |
| the cluster is gone, and only the bucket is left | [no cluster at all](no-cluster.md) |
| everything is gone, including the house | [everything is gone](everything.md) |
| you want the full command sequence, or the drill log | [restore.md](../runbooks/restore.md) |

One thing to know before the first real loss: a restore reads the bucket with the restic password from
the Bitwarden item `homelab-backup-last-resort`. Without it the bucket is ciphertext, and that is the
one loss no monitoring can repair.
