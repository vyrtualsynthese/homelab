# Where the data lives

> **In one line.** Each application's configuration sits on one machine's own disk, the media library
> sits on `nas` over NFS, and the configuration leaves the machine nightly while the media library does
> not.
>
> **Use it when** you are adding a claim, a pod will not start because of one, or you want to know what
> a backup would save · **Verified** 2026-11-20 · **Kind:** explanation
>
> **Sources** `cluster/helm/nfs/provisioner/`, `docs/charts/README.md`,
> `cluster/helm/backup/values.yaml`

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

![Configuration on local-path per node, the media library on NFS classes from nas, and the Longhorn claims that remain](../assets/diagrams/storage.svg)

The [stabilisation design](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#82-storage-classes)
keeps a smaller named set and retires the rest; the NFS tail of that sweep landed on 2026-11-19, and
the classes below are what remains.

| Class | Binding | Used by |
|---|---|---|
| `local-path` (default) | WaitForFirstConsumer | every configuration claim, on that node's own disk |
| `plex-media` | Immediate | film and television |
| `nfs-audio` | Immediate | music |
| `nfs-books` | Immediate | the books library |
| `qbittorrent-download` | Immediate | qBittorrent's output |
| `nfs-qbittorrent-config` | Immediate | qBittorrent's state directory |
| `nfs-syncthing` | Immediate | Syncthing's folder |
| `stash-nfs` | Immediate | assets and documents: koillection's uploads are provisioned into this export, and FileBrowser reads the same directory |
| `traefik-nfs` | Immediate | Traefik's certificate store |

Every NFS class there is a directory on a `nas` export, declared by its own release of the wrapper
chart at `cluster/helm/nfs/provisioner/`: one chart, eight releases, one values file each. Two more
classes, `nfs-plex` and `nfs-ssd`, belonged to provisioners that existed only in the cluster — no
chart, no Application, no claim on either. They were uninstalled on 2026-11-19 with
`helm uninstall nfs-hdd nfs-ssd -n default`.

**Helm does not deploy the eight.** They were installed by hand in 2023 and 2024, before ArgoCD, so
the cluster also carried eight Helm release records — Helm's own note of what it installed, kept as
Secrets — each claiming the same nine objects the chart renders. All eight were deleted on
2026-11-20 with `kubectl -n <namespace> delete secret -l owner=helm,name=<release>`, which removes
the note and leaves the objects to ArgoCD. Running `helm uninstall` there would have deleted the
live provisioner Deployment, its RBAC and its class, and only a sync would bring them back. The two
stray provisioners above are the opposite case: nothing in git declared their objects, so
`helm uninstall` was the right tool for them and is not for these eight.

`local-path` is hostPath-backed: `fsGroup` does nothing, and `runAsUser` on a claim another UID wrote
makes `/config` unreadable. The safe order is in the chart contract —
[changing the UID breaks the config volume](../charts/README.md#changing-the-uid-breaks-the-config-volume).

## The binding trap

`local-path` binds on first consumer: the directory is created on the node where the first pod using
the claim lands. That schedule decides where the data lives; a pin added later does not move it.

The failure is a chart rebuild. `lidarr`, 2026-11-13: the claim bound to `nuc0` because the old pod's
affinity made it eligible, then the sync replaced the pod with one pinned to `streaming`. It stayed
`Pending` with `1 node(s) didn't find available persistent volumes to bind` — an event naming where the
pod may not run, not where the volume is.

Recovery while the claim holds nothing: delete the claim, let ArgoCD recreate it, then **delete the
pod**; otherwise the scheduler keeps queueing it against the old claim's identity. A `Retain` volume
stays as `Released` — delete it and its directory. The habit that avoids it: scale the old Deployment
to zero before the first sync that creates the claim.

## Four volumes that carried a deletion mark

Longhorn is gone: the driver, its classes and custom resources, the namespace, all twenty-six volumes
and all six claims were cleared on 2026-11-18 and 2026-11-19. The record of how is
[the Longhorn page](../cleanup/longhorn-disposition.md).

Four volumes were asked to delete on 2023-12-06. The deletion was blocked because each was bound to a
live claim, and a deletion timestamp cannot be cleared on a running object, so the mark survived
every rebuild since:

| Volume | Claim | Mounted by | What it holds |
|---|---|---|---|
| `pv-traefik-nfs` | `kube-system/pvc-traefik-nfs` | the `traefik` DaemonSet | the ACME certificate store, `/mnt/storage/traefik` |
| `pv-plex-media` | `media-stack/pvc-plex-media` | `plex`, `sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`, `bazarr`, `bazarr-anime` | film and television, `/mnt/storage/nfs/plexmedia` |
| `pv-stash-nfs-nfs-stash` | `media-stack/pvc-stash-nfs-nfs-stash` | the `stash-nfs` provisioner | assets and documents, `/mnt/storage/nfs/stash`, the export FileBrowser reads |
| `pv-nfs-qbittorrent-download-nfs-subdir-external-provisioner` | `media-stack/pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` | `qbittorrent`, `unpackerr`, `sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`, `lidarr` | the downloads queue, `/mnt/ultrafaststorage/nfs` |

ArgoCD reads an object marked for deletion as still going away, so those four Applications —
`plex-media`, `nfs-qbittorrent-download`, `stash-nfs`, `traefik-nfs` — reported `Progressing` and
never `Healthy`, while their provisioners and every claim they served worked normally. The four
classes whose volume was not marked (`books-media`, `nfs-audio`, `nfs-qbittorrent-config`,
`nfs-syncthing`) read `Healthy` throughout, which was the tell.

They were kept as they were by decision on 2026-11-20, then rebuilt on **2026-11-21**, because a
`Progressing` Application that can never settle is a standing false alarm on the health page, and its
cause was a leaked mark rather than a fault worth watching.

Replacing one costs a few seconds and no downtime. The order per Application:

1. delete the claim (`kubectl delete pvc … --wait=false`). The claim then sits `Terminating` behind
   `kubernetes.io/pvc-protection`, so clear that one finalizer from the claim — safe, because the
   volume's reclaim policy is `Retain` and the directory on `nas` is not touched by any of this;
2. with the claim gone, the volume loses the `kubernetes.io/pv-protection` finalizer and its pending
   deletion from 2023 completes;
3. ArgoCD's self-heal recreates claim and volume from the provisioner chart in the same pass.

Do the least critical Application first and `traefik-nfs` last, since it holds the ACME store.

Running pods keep their NFS mounts: the kubelet holds the mount until the pod goes, and no pod is
restarted by this. The NFS directory is never touched — the volumes are all `Retain`, and the data
lives on `nas` at the paths above. Verified after the four swaps: no volume carries a deletion
timestamp, the four Applications are `Synced`/`Healthy`, every consumer pod kept its start time and
restart count, and `acme.json` in `pv-traefik-nfs` stayed byte-identical (417,415 bytes), so no
certificate was re-issued.

The deletion marks were never in git and a sync can neither create nor clear them, so no manifest in
this repository changed for them. To check for a new one:

```bash
kubectl get pv -o json \
  | jq -r '.items[] | select(.metadata.deletionTimestamp != null) | .metadata.name'
```

Expected: nothing. A name in that list is a volume to rebuild with the procedure above, in the same
change as its claim. Patching the volume's finalizer alone deletes the object under a live claim and
leaves the claim `Lost`; the data survives on the NAS, but nothing is mounted.

## Backups

Every stateful claim a row names is copied to one OVH bucket nightly, encrypted and deduplicated,
with the PostgreSQL dumps and their roles, four host `/etc` directories and the books library:
[Backups](backups.md) is the list and the schedule. What that page does not claim is a proved
restore — neither has been drilled.

The media library, the music and the stash library are **not** backed up: one copy on `nas`, by
decision, and a fire ends them. One failed disk is the case that is covered: the six ext4 disks carry
SnapRAID parity above them — spare data that rebuilds one disk from the others — and
[`../runbooks/snapraid-rebuild.md`](../runbooks/snapraid-rebuild.md) is the procedure, including why a
parity that was not synced rebuilds an older library rather than the current one. The old paths — the K3s script that dumps a SQLite file this etcd
cluster lacks, the NAS script writing to a MinIO path deleted in July 2024, PostgreSQL's `pg_dumpall`
into a dead Longhorn claim, Syncthing's restic job that never succeeded — are recorded in the
[audit](../audit/observability-storage.md#3-backups) and are not what runs now.

The one thing with two copies by hand is the internal CA's root key: a password-manager note and an
offline USB stick ([runbooks/internal-ca.md](../runbooks/internal-ca.md)). Which claim belongs to
which app: [applications.md](applications.md).
