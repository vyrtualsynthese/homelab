# The NFS provisioner charts — design

2026-11-19 · branch `feature/nfs-charts-cleanup` · approach A, chosen by the operator

## Why

`cluster/helm/nfs/` holds nine directories for eight exports on the NAS, and no two are packaged the
same way:

| Directory | Shape | State |
|---|---|---|
| `books-media` | values only, upstream chart fetched by the Application (`sources:`) | works |
| `nfs-audio` | values only, upstream chart fetched by the Application | **broken**: the Application points at `nfs/audio/values.yaml`, a path that does not exist |
| `nfs-qbittorrent-config` | values only, and the Application uses `path:` | **broken**: no `Chart.yaml`, so ArgoCD renders an empty directory and reports Synced/Healthy while deploying nothing |
| `nfs-qbittorrent-download` | full vendored copy of the upstream chart | works |
| `nfs-syncthing` | full vendored copy | works |
| `plexmedia` | full vendored copy, chart `name: plex-media` | **broken**: the Application points at `nfs/plex-media`, and the directory is `plexmedia` |
| `stash-nfs` | full vendored copy, chart `name: nfs-stash` | works, claim mounted by nothing |
| `traefik-nfs` | full vendored copy | works, holds the live ACME store |
| `minio-nfs` | full vendored copy | dead: no Application, MinIO is gone |

Three of the eight Applications therefore never render what the cluster runs. Two provisioners —
`nfs-hdd` (class `nfs-plex`) and `nfs-ssd` (class `nfs-ssd`, pod stuck `ContainerCreating` since
2026-09-03) — run in the `default` namespace from Helm releases that exist nowhere in git. Five of the
six vendored copies still pin `node-type: general`, a label retired in AGENTS.md §4, so their pods are
unschedulable the moment they are rescheduled.

Upstream has released nothing since chart `4.0.18` (2023-03-13, image `v4.0.2`), and the repo's copies
are that release byte for byte except `Chart.yaml`'s `name:` and `values.yaml`. There is no version to
move to: the upgrade is packaging.

## What success looks like

- One chart directory, one values file per export, one Application per export.
- `helm template` renders exactly the objects the cluster runs, with the same names, so a rename
  cannot silently orphan a mounted claim.
- The two unmanaged provisioners are gone, and the two StorageClasses they served with them.
- An upstream chart bump is one line in one `Chart.yaml`.
- Nothing an application mounts today changes name, so no pod is restarted into `Pending`.

## Goals

- Package the provisioners the way this repo packages every other external chart: a `v2` wrapper
  chart with the upstream chart as a pinned dependency (AGENTS.md §3.D).
- Delete the dead and the broken: `minio-nfs`, the three mis-referenced Applications' packaging.
- Keep the eight exports, including books and stash, which the operator wants kept although nothing
  mounts stash today.
- Remove the retired `node-type` affinity.

## Non-goals

- Moving any application from a static claim to dynamic provisioning. The libraries live at the root
  of each export; dynamic provisioning would create subdirectories under it.
- Deleting the four PV/PVC pairs stuck in `Terminating`.
- Renaming StorageClasses for tidiness (`plex-media` stays, `nfs-plex` dies with its release).
- Converting any other chart outside `cluster/helm/nfs/`.
- Backups, or the Longhorn leftovers.

## Anti-goals — failure that still looks like success

- **Values at the top level of the wrapper's `values.yaml`.** The subchart reads its values under its
  own name (`nfs-subdir-external-provisioner: { … }`). A top-level `nfs:` block is ignored silently,
  the subchart renders its defaults (`server:` empty, path `/nfs-storage`, class `nfs-client`) and the
  sync creates a ninth class pointing nowhere. The render diff is the gate.
- **An empty `mountOptions`.** The PV and PVC templates shipped inside the upstream chart are gated on
  `{{ if .Values.nfs.mountOptions }}`. Dropping the list removes both objects from the render, and the
  sync prunes them. This is how the four `Terminating` pairs were made. The wrapper always sets the
  list.
- **A changed `fullnameOverride`.** PV, PVC, ServiceAccount, ClusterRole and provisioner name all
  derive from it. A drift creates a second claim with a new PV and leaves the running pods mounted on
  the orphan.
- **A missing `nameOverride`.** The Deployment's `app:` selector comes from the chart name, and a
  selector is immutable. Four of today's charts carry their own name, so their live selector reads
  `app: plex-media`, `app: nfs-syncthing`, `app: traefik-nfs` and `app: nfs-stash` — not
  `app: nfs-subdir-external-provisioner`, which is what the subchart would render. Without the
  override the sync fails on four releases with `field is immutable`.
- **`syncPolicy.automated` in an Application file** (AGENTS.md §7.1).
- Editing `cluster/helm/nfs/provisioner/charts/` — it is a build artifact, ignored by git.
- Clearing a `Terminating` PV whose claim a pod still mounts.

## Constraints

- CRLF on every file under `cluster/argocd/**`; chart files stay LF.
- The agent has read-only cluster access: the two `helm uninstall` commands are the operator's.
- ArgoCD resolves `helm.valueFiles` relative to `spec.source.path` (ArgoCD docs,
  `docs/operator-manual/application.yaml`), which is what makes one chart serve eight releases.
- Auto-sync is enabled per Application in the UI, never in git.

## The inventory that must not move

`fullname` is the string the chart's helper resolves to; it names the Deployment, ServiceAccount,
ClusterRole, ClusterRoleBinding, the PV (`pv-<fullname>`) and the PVC (`pvc-<fullname>`). The
provisioner name defaults to `cluster.local/<fullname>`, which the StorageClass records. `nameOverride`
pins the `app:` label and the Deployment selector to what the cluster already carries.

| Export (NAS path) | Application = release | Namespace | `nameOverride` | `fullnameOverride` | StorageClass | Mounted by |
|---|---|---|---|---|---|---|
| `/mnt/storage/nfs/plexmedia` | `plex-media` | media-stack | `plex-media` | `plex-media` | `plex-media` | plex, sonarr, sonarr-anime, radarr, radarr-anime, bazarr, bazarr-anime, maintainerr |
| `/mnt/storage/nfs/audio` | `nfs-audio` | media-stack | `nfs-subdir-external-provisioner` | `nfs-audio-nfs-subdir-external-provisioner` | `nfs-audio` | plex, lidarr |
| `/mnt/storage/nfs/books` | `books-media` | media-stack | `nfs-subdir-external-provisioner` | `books-media-nfs-subdir-external-provisioner` | `nfs-books` | calibre-web-automated, kavita |
| `/mnt/ultrafaststorage/nfs` | `nfs-qbittorrent-download` | media-stack | `nfs-subdir-external-provisioner` | `nfs-qbittorrent-download-nfs-subdir-external-provisioner` | `qbittorrent-download` | qbittorrent, sonarr, sonarr-anime, radarr, radarr-anime, lidarr, unpackerr |
| `/mnt/storage/nfs/qbittorrent-config` | `nfs-qbittorrent-config` | media-stack | `nfs-subdir-external-provisioner` | `nfs-qbittorrent-config-nfs-subdir-external-provisioner` | `nfs-qbittorrent-config` | qbittorrent |
| `/mnt/storage/nfs/syncthing` | `nfs-syncthing` | syncthing | `nfs-syncthing` | `nfs-syncthing` | `nfs-syncthing` | syncthing (app and backup) |
| `/mnt/storage/nfs/stash` | `stash-nfs` | media-stack | `nfs-stash` | `stash-nfs-nfs-stash` | `stash-nfs` | nothing (kept on purpose) |
| `/mnt/storage/traefik` | `traefik-nfs` | kube-system | `traefik-nfs` | `traefik-nfs` | `traefik-nfs` | traefik's ACME store |

Every value in the release and namespace columns stays as it is, because ArgoCD passes the
Application name as the Helm release name and the two overrides pin the rest. The `nameOverride` values
are the chart names in the vendored copies that the live selectors came from; the four that read
`nfs-subdir-external-provisioner` need no override beyond the subchart's own name.

## The target shape

```
cluster/helm/nfs/provisioner/
  Chart.yaml            # apiVersion v2, dependency pinned to 4.0.18
  values.yaml           # shared defaults, under the dependency's key
  values/<release>.yaml # one per export: path, fullnameOverride, StorageClass name
  README.md             # what the chart is, why mountOptions is mandatory, how to add an export
  charts/               # build artifact, gitignored, built by ArgoCD at sync
```

`Chart.yaml`:

```yaml
apiVersion: v2
name: nfs-provisioner
description: NFS subdir external provisioners — one release per NAS export
type: application
version: 0.1.0
appVersion: "4.0.2"
dependencies:
  - name: nfs-subdir-external-provisioner
    version: 4.0.18
    repository: https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner
```

`values.yaml` carries the settings all eight share, nested under the dependency's name: image,
`strategyType: Recreate`, the server, the mount-option list, reclaim policies, `storageClass.create`,
RBAC, resources and placement. A `values/<release>.yaml` adds only what differs — `nfs.path`,
`nameOverride`, `fullnameOverride`, `storageClass.name`.

An Application becomes:

```yaml
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/nfs/provisioner
    helm:
      valueFiles:
        - values/plex-media.yaml
```

Each keeps the destination namespace it has today.

## Decisions I made

- **No local templates.** I diffed the repo's copies against the released `4.0.18` tarball: the PV and
  PVC templates are upstream's, not the repo's addition. The wrapper therefore contains no
  `templates/` directory at all.
- **One chart, eight values files, eight releases.** Helm's dependency docs and the upstream README
  ("Install Multiple Provisioners": a distinct `storageClass.provisionerName` and `storageClass.name`
  per release) both describe the release as the unit. ArgoCD documents one Application per release.
- **Every provisioner pinned to `streaming`** instead of the current split (two on `streaming`, the
  rest on the retired `node-type: general` affinity). It is the media stack's node, it has the
  headroom for eight 10m/64Mi pods, and the provisioner holds no data.
- **`podSecurityContext` 1100:1100:1100** for all eight, matching the media stack's PUID/PGID. It
  decides the owner of any directory a dynamic claim would create. Today's values are 1100 for three,
  911 for two, unset for three.
- **Keep the StorageClasses**, one per export, under the names that exist today. `nfs-plex` and
  `nfs-ssd` disappear with their releases; no PVC references any of them, and the claim name, not the
  class, is what applications mount.
- **Keep `stash-nfs`** — koillection's uploads claim is provisioned into that export and filebrowser
  reads the same directory, so the class is in use. (Corrected 2026-11-19: what is unused is the
  static pair below, not the class.)

## Cleanup

In git:

- delete `cluster/helm/nfs/minio-nfs/`;
- delete the eight directories the wrapper replaces;
- rewrite the eight Application files;
- add `cluster/helm/nfs/provisioner/charts/` and `Chart.lock` to `.gitignore`.

In the cluster, the operator's two commands:

```bash
helm uninstall nfs-hdd nfs-ssd -n default
```

That removes both orphaned deployments, their RBAC, and the `nfs-plex` and `nfs-ssd` StorageClasses.
Verified read-only: no PVC carries either class. `nfs-ssd` points at `/mnt/ssd0/nfs`, an export the
NAS no longer serves; `nfs-hdd` points at the same path as the `plex-media` export.

## Risks

- **A live PV is immutable in its source.** If a values file disagreed with the export path, the sync
  fails with `PersistentVolumeSource is immutable` rather than re-pointing a library at the wrong
  directory. Loud and safe.
- **Four PV/PVC pairs sit in `Terminating`** (`pv-plex-media`, `pv-nfs-qbittorrent-download-…`,
  `pv-traefik-nfs`, `pv-stash-nfs-nfs-stash`). Three are mounted by running pods and `pv-traefik-nfs`
  holds the ACME store. The refactor keeps every name, so they are patched, never recreated, and their
  finalizers stay untouched.
- **`pvc-stash-nfs-nfs-stash` is `Terminating` and unused.** Reserved for a later hand operation
  (delete the claim, patch the volume's finalizers, let the new release recreate the pair), and left
  alone in this change.
- **ArgoCD adopts the objects that exist.** They carry no ArgoCD tracking annotation today — the
  provisioners were installed with `helm` at some point — so the first sync takes them over by name.
  Nothing is deleted.

## Verification

1. `helm dependency build cluster/helm/nfs/provisioner`
2. For each of the eight values files, render and assert the object set:
   `helm template <release> cluster/helm/nfs/provisioner -f values/<release>.yaml` must produce
   `Deployment/<release>`, `ServiceAccount`, `ClusterRole`, `ClusterRoleBinding`, `Role`, `RoleBinding`,
   `PersistentVolume/pv-<fullname>`, `PersistentVolumeClaim/pvc-<fullname>` and
   `StorageClass/<class>`, with the names from the inventory table and no others. The Deployment's
   selector must equal the live one, read from the read-only MCP before the rewrite.
3. The repo's chart loop (AGENTS.md §6) and both reference checks, CRLF-safe.
4. `hack/check-exposure.sh` — no route changes, so it must be unchanged.
5. After the merge: every Application Synced/Healthy, all eight provisioner pods Running on
   `streaming`, and a `kubectl get pv,pvc` that still shows the same bound pairs.

## Rollback

Revert the commit. ArgoCD re-renders the old chart directories, which the revert restores, and every
object name matches again; the nine releases keep their names throughout, so nothing is deleted and no
claim is recreated.
