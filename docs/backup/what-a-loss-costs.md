# What a loss costs

> **In one line.** The configuration, the databases and syncthing leave the machine every night for
> one OVH bucket; the media library, the music, the stash library and the books stay on one disk by
> decision; and the bucket has been read back twice — once through a pod, once from a laptop with no
> cluster.
>
> **Use it when** a risk needs weighing, a disk died, or you need to know what is left ·
> **Verified** 2026-11-22 · **Kind:** explanation
>
> **Sources** `cluster/helm/backup/values.yaml`, `docs/how-it-works/backups.md`,
> `docs/runbooks/restore.md`, the drill log

The nightly mechanism is [how backups work](../how-it-works/backups.md); the procedure with a cluster
is [restore.md](../runbooks/restore.md), and the one for when the cluster is gone is
[last-resort.md](../runbooks/last-resort.md). This page weighs what a loss costs; the procedures that
put it back are the rest of this section.

## What leaves the machine

| Stream | Holds | Where it lands |
|---|---|---|
| `nodes-nas` | the PostgreSQL dumps and their `globals.sql`, Grafana's database and alert rules | OVH bucket, nightly 04:30 |
| `nodes-nuc0` | ten claims from nine applications, the node's `/etc` | OVH bucket, nightly 05:00 |
| `nodes-streaming` | the media stack's configuration, the node's `/etc` | OVH bucket, nightly 05:30 |
| `nfs` | syncthing, koillection's uploads, the ACME certificate store, the qBittorrent config | OVH bucket, nightly 06:00 |
| `etcd/` (not a restic repository) | the cluster's object state: every Secret, Deployment, ArgoCD Application and claim object, uploaded by each control-plane server | OVH bucket, every six hours from each server |

Every application's configuration is a `local-path` claim, so a node's disk dying costs that day's
changes and not the settings themselves — as long as the claim has a row in
[`cluster/helm/backup/values.yaml`](../../cluster/helm/backup/values.yaml), which is the authority on
what is covered. The claim-by-claim list is [the coverage table](coverage.md), generated from that
file.

## What would still be lost

| Where | Lost with it |
|---|---|
| `nas`: the mechanical array, and the music, stash, books and media exports | the media library and the books, one copy each, by decision |
| the etcd snapshots | the cluster's object state, from the bucket's `etcd/` prefix. It is there every six hours per server, and one was restored into a throwaway cluster on 2026-11-22; what would be lost is the last few hours of object changes |
| anything a row does not name | a claim added since the last edit of `values.yaml`, a host directory outside the four in the matrix |
| the two edge Pis | their own state, unchanged: outside the cluster, and their restic path is in the [Ansible audit](../audit/ansible-hosts.md) |
| a Secret created by hand and not filed in the vault | its value is in etcd and in the bucket's snapshots, but no page records it and `view` RBAC hides it ([secrets.md](../secrets.md)) |
| the internal CA's root key | only a password-manager note and an offline USB stick, made by hand ([internal-ca.md](../runbooks/internal-ca.md)) |
| a file changed after 06:00 | up to a day of it, which is what the schedule costs |

The bucket does not change the shape of a NAS failure: one machine, one power supply, and the media
library and the books offsite are out of scope, so a fire still ends them. What the bucket buys is
the configuration and the databases — the parts a rebuild cannot invent — not the bulk.

## What is proved, and what is not

Proved: the jobs run, the rows resolve against the live cluster's directories, a WAL database on a
read-only mount copies cleanly through SQLite's backup API, the verifier reports each repository's age
and size, and a restore reads back — a PostgreSQL dump set, Sonarr's database through
`pragma integrity_check` and a Syncthing file whose hash matched. All four repositories also open from
a laptop with no cluster, using only the values in Bitwarden.

Not proved: **that a broken application is rebuilt from a restore.** The destructive put-back onto a
live claim is untried, and an etcd snapshot has been restored and read back but never put in front of
the cluster's real servers; the drill log in
[`restore.md`](../runbooks/restore.md#the-drill-log) says so. The honest description is "the data
comes back and is readable", not "a cluster comes back".

## The order to rebuild in

1. **Keep what survived.** `local-path` reclaims `Retain`, so bytes outlive a deleted claim object;
   never wipe a disk before looking at it
   ([postgres-restore.md](../runbooks/postgres-restore.md#restoring-the-production-instance)).
2. **Hosts before cluster.** Inventory, K3s role, labels, NFS exports, GPU driver, WireGuard keys —
   the four `/etc` directories in the bucket help, but a full rebuild is still W8's work
   ([ansible-hosts.md](../audit/ansible-hosts.md#7-what-a-rebuild-would-not-reproduce)).
3. **Cluster from git**, root Application first, every Secret present first
   ([secrets.md](../secrets.md)). The restic password and the K3s join token are the two that are not
   in the cluster at all.
4. **Data last**, from the bucket: claims through
   [restore.md](../runbooks/restore.md) section 2, PostgreSQL through
   [postgres-restore.md](../runbooks/postgres-restore.md) after restoring `globals.sql`, the board's
   columns from the [seed script](../runbooks/vikunja-board-seed.md) and its cards from the restored
   dump ([use-the-board.md](../running-it/use-the-board.md)).
5. **The unknowns.** Whether a restored claim matches its chart, and which Secret values exist only
   in the cluster.

## What is still owned elsewhere

- **W8 (Ansible and docs)**: hosts described in git, playbooks that build one, and a procedure tested
  against the first real restore.
- **The edge Pis**: their own backups, deferred by decision.

## The one thing you can do today

Run a drill. The three read-only ones in
[`restore.md`](../runbooks/restore.md) need nothing but `kubectl` and the restic password, take under
an hour, and are the difference between a schedule and a backup: a copy nobody has restored from is a
hope with a timestamp.
