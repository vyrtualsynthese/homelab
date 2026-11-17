# Where the data lives

> **In one line.** Each application's configuration sits on one machine's own disk, the media library
> sits on `nas` over NFS, and neither has a second copy or a working backup.
>
> **Use it when** you are adding a claim, a pod will not start because of one, or you want to know what
> a backup would save · **Verified** 2026-11-17 · **Kind:** living page

## Two storage worlds

**Configuration** — what an app writes for itself: settings, a small database, its index — goes on
`local-path`, the default class: a directory on the disk of whichever machine the pod lands on, with
`strategy: Recreate` so only one pod opens it at a time.

**Shared media** goes on an NFS class: a directory that already exists on a `nas` export, mounted by
whichever pod reads it, so one library serves them all.

A `local-path` directory is a path on one node's filesystem, so a pod elsewhere cannot mount it —
hence the pinning rule in [nodes.md](nodes.md). NFS serves one copy to many readers; replicated
storage, the same volume on two machines, was Longhorn, gone for the reason in
[the-shape-and-why.md](the-shape-and-why.md).

## Claims and names

A configuration claim is `<app>-config`, `128Mi` unless there is a reason: configuration is small, and
GiB-sized guesses proved wrong. Two exceptions keep book-sized files in their own volume:
`calibre-web-automated` at `10Gi`, `kavita` at `2Gi`.

The [stabilisation design](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#82-storage-classes)
keeps a smaller named set and retires the rest; that sweep has not happened, so the last row is
leftovers.

| Class | Binding | Used by |
|---|---|---|
| `local-path` (default) | WaitForFirstConsumer | every configuration claim, on that node's own disk |
| `nfs-plex` | Immediate | film and television |
| `nfs-audio` | Immediate | music |
| `nfs-books` | Immediate | the books library |
| `qbittorrent-download` | Immediate | qBittorrent's output |
| `nfs-syncthing` | Immediate | Syncthing's folder |
| `stash-nfs` | Immediate | assets and documents |
| `traefik-nfs` | Immediate | Traefik's certificate store |
| `plex-media`, `nfs-qbittorrent-config`, `nfs-ssd` | Immediate | **strays** — a duplicate, an unused leftover, and one whose export cannot be mounted; do not build on them |

Every other class is an NFS claim onto a `nas` export, working or not.

`local-path` is hostPath-backed: `fsGroup` does nothing there, and `runAsUser` on a claim another UID
has written makes `/config` unreadable. The safe order is in the chart contract —
[changing the UID breaks the config volume](../charts/README.md#changing-the-uid-breaks-the-config-volume).

## The binding trap

`local-path` binds on first consumer: the directory is created on the node where the first pod using
the claim lands. That schedule decides where the data lives; a pin added later does not move it.

The failure is a chart rebuild. `lidarr`, 2026-11-13: the claim bound to `nuc0` because the old pod's
affinity made that node eligible, then the sync replaced the pod with one pinned to `streaming`. It
stayed `Pending` with `1 node(s) didn't find available persistent volumes to bind` — an event naming
where the pod may not run, not where the volume is.

Recovery while the claim holds nothing: delete the claim, let ArgoCD recreate it, then **delete the
pod**; otherwise the scheduler keeps queueing it against the old claim's identity. A `Retain` volume
stays behind as `Released` — delete it and its directory. The habit that avoids it: scale the old
Deployment to zero before the first sync that creates the claim.

## Longhorn's corpse

Longhorn is the replicated-storage layer that failed in 2026. It was not rebuilt, and the claims it left
in the application namespaces are stuck in `Terminating`, each carrying a finalizer naming a storage
driver that is gone, so nothing will clear them. Their contents were abandoned by decision.

They are inert: no data, nothing blocked, visible in `kubectl get pvc -A` and `kubectl get pv`.
Clear one by hand: patch the volume's finalizers away, then the claim's. The cause is in the audit
([§2.1](../audit/observability-storage.md#21-longhorn-removed-not-cleaned-up)); the list is in
[`status.md`](../status.md), which owns the counts.

## Backups

Plainly: no backup of this cluster works. The audit's
[backup table](../audit/observability-storage.md#3-backups) is the evidence: the K3s script dumps a
SQLite file this etcd cluster lacks; the NAS script writes to a MinIO path deleted in July 2024
([ansible audit](../audit/ansible-hosts.md)); host restic covers only the two edge Pis, to an S3
bucket; PostgreSQL's `pg_dumpall` writes into a dead Longhorn claim; Syncthing's restic job covers its
configuration alone.

The one thing with two copies is the internal CA's root key: a password-manager note and an offline
USB stick, both hand-made, while the automated offsite path is broken too
([runbooks/internal-ca.md](../runbooks/internal-ca.md)). W4, the design's offsite backup workstream,
owns the fix; until it lands, treat a lost disk as lost data. Which claim belongs to which app is in
[applications.md](applications.md).
