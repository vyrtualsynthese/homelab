# cross-seed

cross-seed matches a torrent you already seed on one tracker against the same release on another
tracker, so the same files seed in both places. It reads qBittorrent's torrent files, searches your
Torznab indexers (the feed format tracker indexers expose), and injects the matching torrent through
qBittorrent's API.

## Upstream

- Website: https://www.cross-seed.org/
- Source: https://github.com/cross-seed/cross-seed

The image comes from GHCR only: `ghcr.io/cross-seed/cross-seed`. The Docker Hub namespace is a legacy
mirror and is not what this chart pulls.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/cross-seed.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `cross-seed`, so `app.fullname`
stays `cross-seed` and the object names do not change.

Four objects render:

| Object | Name |
|---|---|
| Deployment | `cross-seed` |
| Service | `cross-seed` (ClusterIP, port 2468) |
| ServiceAccount | `cross-seed`, with no API token mounted |
| PersistentVolumeClaim | `cross-seed-config`, only when `storage.enabled` and no `storage.existingClaim` |

There is no IngressRoute: the UI is not exposed.

```bash
# what the cluster will render, exactly
helm template cross-seed cluster/helm/media-stack/cross-seed --namespace media-stack
```

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. Tag is always pinned, never `latest`. | `ghcr.io/cross-seed/cross-seed`, `6.13.9` |
| `strategy` | Deployment strategy. Must stay `Recreate` with a `ReadWriteOnce` config claim. | `Recreate` |
| `args` | Arguments after the image entrypoint. The entrypoint is `catatonit` plus `cross-seed`, so only the subcommand belongs here. | `[daemon]` |
| `service.port` | Port the Service exposes, and what the container listens on. | `2468` |
| `container.port` | Only set when the container listens on a different port than the Service. | `null` (falls back to `service.port`) |
| `storage.*` | The config claim: class, size, mount path, or reuse an existing claim. | `local-path`, `128Mi`, `/config` |
| `configFile.*` | The Secret holding `config.js`, its key, and where it mounts. | Secret `cross-seed`, key `config.js`, `/config/config.js` |
| `torrents.*` | qBittorrent's `BT_backup` claim, sub-path, mount path and read-only flag. | NFS claim, `qBittorrent/BT_backup`, `/torrents`, read-only |
| `probes.*` | Liveness on `/api/ping`; readiness disabled on purpose. | liveness on, 20s/30s |
| `resources.*` | Requests and limits. There is a memory limit but no CPU limit, so torrent parsing is never throttled. | `50m`/`128Mi` request, `512Mi` limit |
| `securityContext` | Runs as UID/GID 1100, drops all capabilities, no privilege escalation. | non-root |
| `podSecurityContext` | `seccompProfile: RuntimeDefault`. No `fsGroup`: it does nothing on a hostPath-backed claim. | seccomp default |
| `nodeSelector` | Node pin. Every workload is pinned. | `kubernetes.io/hostname: streaming` |

`env` is rendered inline into the container: `TZ=Europe/Paris` and `CONFIG_DIR=/config`. `extraEnv`
and `envFrom` exist for Secret-sourced variables; neither is used here, because the configuration is
a file, not variables.

## Configuration

`config.js` is not written by the chart. It comes from the Secret `cross-seed`, key `config.js`, and
is mounted **read-only** at `/config/config.js` with `subPath`. The chart only points at the Secret;
it does not template or validate its content.

The Secret **must already exist in the namespace before the first sync**. The chart does not create
it, and a missing Secret leaves the pod in `CreateContainerConfigError`.

Two consequences:

- **A change to `config.js` needs a pod restart.** A `subPath` mount is not updated in place when the
  Secret changes, and the process reads the file at start. Update the Secret, then
  `kubectl -n media-stack rollout restart deploy/cross-seed`.
- **The file on the claim is not the file the daemon runs.** After the refactor, `/config/config.js`
  is the read-only Secret mount, so an edit there fails rather than landing in the claim. Change the
  Secret, not the pod.

The old chart copied the Secret onto the claim at every start, so the claim may hold an edited copy
that differs from the Secret. Compare the two against the **pre-refactor pod**, before the rollout,
when `/config/config.js` is still the copy on the claim:

```bash
kubectl -n media-stack get secret cross-seed -o jsonpath='{.data.config\.js}' | base64 -d > /tmp/secret-config.js
kubectl -n media-stack exec deploy/cross-seed -- cat /config/config.js > /tmp/claim-config.js
diff -u /tmp/secret-config.js /tmp/claim-config.js
```

Run it **before** the new chart is live. Afterwards `/config/config.js` is the read-only Secret
mount, so the same command diffs the Secret with itself and always reports no change. If the diff
shows a difference before the rollout, stop and decide which copy wins: the Secret is the intended
source, and the claim's copy disappears at rollout.

The UI is not exposed. It is not in the chart's objects and there is no IngressRoute. To reach it,
port-forward:

```bash
kubectl -n media-stack port-forward svc/cross-seed 2468:2468
```

## State

Everything cross-seed persists lives in the claim mounted at `/config`:

| Path | Contents |
|---|---|
| `cross-seed.db` | the SQLite database: decisions, matches, seeding history |
| `cross-seeds/` | hard links and cross-seeded copies it creates |
| `torrent_cache/` | torrent files fetched from indexers |
| `logs/` | the daemon's logs |

The claim is named `<fullname>-config` (`cross-seed-config`) and is created by the chart. Set
`storage.existingClaim` to bind an existing claim instead; the chart then renders no
PersistentVolumeClaim. `local-path` is hostPath-backed, so the claim lands on the node the pod is
pinned to (`streaming`).

The old chart also mounted a second claim, `cross-seed2`, at `/cross-seed`; nothing is mounted there
now, and that claim is deleted by hand only after the emptiness check (see
[Uninstalling](#uninstalling)).

Losing the claim loses the database and the seeding history. It does not lose the torrents
themselves, which stay with qBittorrent.

## Torrents

cross-seed reads qBittorrent's `BT_backup` directory over NFS, mounted **read-only** at `/torrents`.
The mount is a sub-path (`qBittorrent/BT_backup`) of the existing qBittorrent config claim.

cross-seed never writes there. It injects the matched torrent through qBittorrent's API, and
qBittorrent creates the files. That is why the mount is read-only and why cross-seed needs
qBittorrent's credentials in `config.js` rather than filesystem access.

The NFS directory is owned by qBittorrent's `PUID`, 1100, so the read-only mount needs no ownership
change when cross-seed moves to UID 1100. If it turns out unreadable, the fix is on the NFS side, not
in the chart.

## Probes and readiness

| Probe | Path | Behaviour |
|---|---|---|
| liveness | `/api/ping` | 200 while the process is alive, no authentication needed |
| readiness | — | deliberately disabled |

Readiness is **deliberately absent**, not forgotten. cross-seed has no unauthenticated endpoint that
reports dependency health: `/api/status` needs the API key, and pointing readiness at `/api/ping`
would just duplicate liveness, turning a qBittorrent or indexer blip into a restart loop. The Service
carries no user traffic — the UI is reached by port-forward, not through it — so a pod that is
alive but temporarily unable to reach qBittorrent does not need to be pulled out of a load balancer.

There is deliberately no readiness values block: no unauthenticated dependency-aware endpoint exists,
and the Service carries no user traffic.

## Running as UID 1100

The image runs as root by default. This chart runs it as UID/GID 1100, matching the `PUID`/`PGID` the
rest of the stack uses. That only works because the config claim is chowned to 1100:1100 on the node
first.

`local-path` is hostPath-backed, and the kubelet does **not** apply `fsGroup` ownership to hostPath
volumes. Setting `fsGroup` looks right in review and changes nothing, which is why it is absent
rather than set to something plausible. A newly provisioned claim also starts root-owned, because
the provisioner runs its setup script as root.

The order matters, and it is one-time per claim:

1. **Run the drift check above while the old pod still exists.** After the rollout, the check compares
   the Secret with itself.
2. **Confirm `cross-seed2` is empty, while the old pod still exists**:
   ```bash
   kubectl -n media-stack exec deploy/cross-seed -- du -sh /cross-seed
   ```
   This has to happen **before scaling to zero**: the new chart does not mount `/cross-seed`, and
   scaling down destroys the only pod that can run the check. If it shows data, copy it off before
   going further. The deletion itself happens after the rollout, in the order under
   [Uninstalling](#uninstalling).
3. **Scale the Deployment to zero.** Safe: the Application's auto-sync is off, so nothing reverts it.
4. **Resolve the claim to its host path.**
   ```bash
   kubectl -n media-stack get pvc cross-seed-config -o jsonpath='{.spec.volumeName}'
   kubectl get pv <pv-name> -o jsonpath='{.spec.hostPath.path}'
   ```
5. **Chown that directory** over SSH on `streaming`: `chown -R 1100:1100 <host-path>`.
6. **Apply the chart** (the ArgoCD sync sets replicas back to 1).
7. **Confirm a write**, not just a start. `kubectl -n media-stack exec deploy/cross-seed -- id` should
   show uid 1100, and the daemon must be able to write `cross-seed.db` in `/config`. A pod that starts
   and then fails on a write is the failure this order avoids.

A fresh claim starts root-owned again. Either re-chown per volume, or set `userPattern`/`groupPattern`
on the local-path provisioner and stop doing this by hand.

## Upgrading

`image.tag` in `values.yaml` and `appVersion` in `Chart.yaml` move **together**. `appVersion` feeds the
`app.kubernetes.io/version` label on every object, so a chart whose tag moved and whose `appVersion`
did not reports the wrong version to anything that reads labels.

Renovate updates `image.tag` only. When its pull request bumps the tag, update `appVersion` in the
same commit. cross-seed image tags drop the release's leading `v`: the release `v6.13.9` is the tag
`6.13.9`.

The chart `version` moves on its own; it is not tied to the image.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment: args, inline env, both mounts, liveness, resources |
| `templates/service.yaml` | the ClusterIP Service |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no token mounted |
| `templates/persistentvolumeclaim.yaml` | the config claim, only when `storage.enabled` and no `existingClaim` |
| `templates/_helpers.tpl` | the naming helpers, including `app.configClaimName` |
| `templates/NOTES.txt` | the install notes |

There is no ConfigMap and no IngressRoute. The configuration is a file from a Secret, and the UI is
not exposed. The shared helpers and the generic Service, ServiceAccount and claim templates are the
same as `autobrr`'s; the Deployment and the values encode cross-seed's own needs.

## Uninstalling

The config claim is created by this chart, so deleting the release deletes the claim and the seeding
database with it. Take a copy first if it holds anything you want.

The old `cross-seed2` claim is no longer in the chart. Removing it from the chart stops managing it;
it does not delete it, and it is not used by v6. Delete it by hand, in this order:

1. **Confirm it is empty before the sync that removes it.** Run `du -sh /cross-seed` on the old pod:
   ```bash
   kubectl -n media-stack exec deploy/cross-seed -- du -sh /cross-seed
   ```
   If it shows data, copy it off first. Do this before syncing, because if prune is enabled on the
   Application in the ArgoCD UI, the claim can be deleted on sync — before anyone has looked.
2. **Sync the Application** and wait for the new pod to be healthy.
3. **Delete the claim only then.** `kubectl -n media-stack delete pvc cross-seed2`.

Deleting it after the new pod is healthy means the emptiness check ran against the pod that still
had it mounted, and that a failed rollout can be rolled back without having destroyed the old state.
