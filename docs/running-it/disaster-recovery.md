# Disaster recovery

> **In one line.** Nothing is backed up: a dead node disk takes its app configuration, a dead `nas`
> disk takes the databases, the dashboards, the board and the media library, and nothing the cluster
> holds is known to be restorable from offsite.
>
> **Use it when** a risk needs weighing, or a disk died and you need to know what is left ·
> **Verified** 2026-11-17 · **Kind:** living page

No rebuild runbook exists: this states the position only.

## What would be lost

An application's configuration is a `local-path` claim: a directory on one machine's disk, copied
nowhere, so that disk's loss takes its settings, credentials and index
([applications.md](../how-it-works/applications.md)).

| Where | Lost with it |
|---|---|
| a node's `local-path` directory | its apps come back empty |
| `nas`: PostgreSQL and the dumps beside it | every app database, and the board's cards — the queue of what was being fixed |
| `nas`: Grafana | the dashboards and alert rules, which live only there |
| `nas`: the mechanical array | the media library, one copy |
| the control plane's etcd | every Secret ([inventory](../secrets.md)) |
| the internal CA root key | a new root, every client re-trusting ([runbook](../runbooks/internal-ca.md)) |
| an Ansible inventory outside git | the machines' only description ([§1](../audit/ansible-hosts.md#1-the-playbooks-cannot-run-from-this-repository)), and the secrets the playbooks copy ([§2](../audit/ansible-hosts.md#2-setup-nodeyaml-216-lines)) |

A NAS is not redundancy: one machine, one power supply; media offsite is out of scope, so a fire
ends it.

## What exists

- **The internal CA root key**: a password-manager note and an encrypted USB stick
  ([internal-ca.md](../runbooks/internal-ca.md)).
- **Git**: the cluster's shape, none of its bytes.
- **The dated records** — audits, designs, plans: the only description of the old configuration.
- **The nightly `pg_dump`** on `postgres-dumps`: a guard against a dropped table, not a
  dead disk.
- **Syncthing's restic CronJob**: `restic backup /data` daily, repository from the
  `syncthing-backup` Secret, so the destination is outside git and its failure domain unknown.
- **The old host restic path**: the edge Pis into AWS S3, the only documented offsite copy,
  covering no cluster host
  ([ansible audit](../audit/ansible-hosts.md#4-backups-broken-since-2024-provably),
  [storage audit](../audit/observability-storage.md#3-backups)).

## What does not exist

| Missing | Decided in |
|---|---|
| W4's offsite repository, schedule, retention, staleness alert, monthly drill | [W4](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#w4-offsite-domains-dns-and-backups) |
| The rewritten playbooks and the recovery runbook | [W8](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#w8-ansible-and-documentation) |
| A restore anyone has performed: no dump, no claim, the etcd runbook pending | [`runbooks/README.md`](../runbooks/README.md) |
| Anything in git that could rebuild a host | [`ansible-hosts.md` §7](../audit/ansible-hosts.md#7-what-a-rebuild-would-not-reproduce) |
| A second copy of the metrics or the logs | [`observability.md`](../how-it-works/observability.md) |

## Who owns the fix

W4 owns the offsite repository: etcd snapshots, configuration claims, the PostgreSQL directory,
the dumps, the inventory describing them. W8 owns the machines and the runbook — an inventory in
git, playbooks that build a host, a procedure tested against W4's first restore. Both are
builds, not repairs. W4 was deferred and W5 was ordered ahead of it, so every platform upgrade runs with no
restore path
([§10](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#10-ordering)).

## The one thing you can do today

Neither action is a backup: a backup has a schedule, retention, a place off the machine it
protects, a proof it restores, a record; these have only memory.

**Carry a dump off `nas`.** The claim is ReadWriteOnce: check the 03:30 dump is not running, then
mount `postgres-dumps` read-only in a helper pod pinned to `nas` (step 1 of
[postgres-restore.md](../runbooks/postgres-restore.md)) and `kubectl cp` the newest file out. What
it lacks is a different failure domain; the restore half is that runbook, unrun.

**Carry a claim off its node.** `kubectl get pv` names the node and the path; tar that directory and
take the tarball away. It returns files, not an application: claim name, chart and Secret must be
reassembled, and no page says how.

Limits: no schedule, so it covers this month's memory; no encryption, so the tarball carries
credentials in the clear; no verification, so it is a hope with a timestamp; no coverage of the
Secrets in etcd.

## Read this before a rebuild

1. **Keep what survived.** `local-path` reclaims `Retain`, so bytes outlive a deleted claim object;
   never wipe a disk before looking at it
   ([postgres-restore.md](../runbooks/postgres-restore.md#restoring-the-production-instance)).
2. **Hosts before cluster.** Inventory, K3s role, labels, NFS exports, GPU driver, WireGuard keys —
   none in git, and nothing starts until they exist.
3. **Cluster from git**, root Application first, every Secret present first
   ([secrets.md](../secrets.md)).
4. **Data last.** Claims into directories, ownership checked before the pod starts; databases through
   [postgres-restore.md](../runbooks/postgres-restore.md), the board's columns from the
   [seed script](../runbooks/vikunja-board-seed.md); its cards come back only in a restored dump
   ([use-the-board.md](use-the-board.md)).
5. **The unknowns.** Whether an etcd restore works. Whether a restored claim matches its chart. Which
   Secret values exist only in the cluster.
