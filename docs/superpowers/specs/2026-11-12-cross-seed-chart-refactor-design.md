# cross-seed chart refactor — design

- Date: 2026-11-12
- Status: approved 2026-11-12
- Scope: `cluster/helm/media-stack/cross-seed`, `docs/charts/README.md`,
  `cluster/helm/media-stack/autobrr/README.md`, and a new chart README.

## Intent

The cross-seed chart is the scaffold inherited from the external chart at the September migration. It
violates the repo's chart contract in several ways and hides cross-seed's real needs behind raw pod
spec. This refactor rebuilds it around the repo's chart philosophy — using `autobrr` as a reference
for principles, not as a file to copy — so that it is easy to maintain and matches how the image
actually works.

The operator does not use the cross-seed web UI. The chart will not expose it.

## What the application needs

Facts taken from the image and the running pod, not guessed:

| Fact | Source |
|---|---|
| Everything persists under `CONFIG_DIR=/config`: `config.js`, `cross-seed.db` (+ WAL/SHM), `cross-seeds/`, `torrent_cache/`, `logs/` | image `Dockerfile`, `cross-seed/src/configuration.ts` |
| `config.js` is read from `/config`; it is supplied by the existing Secret `cross-seed`, key `config.js` | live Deployment, chart values |
| Torrent files are read from qBittorrent's `BT_backup` over NFS; cross-seed reads them and injects through the qBittorrent API, so the mount can be read-only | live Deployment, `cross-seed/src/clients/` |
| HTTP daemon listens on 2468 | image `EXPOSE 2468`, live Service |
| `GET /api/ping` is the unauthenticated health endpoint, returns `200 OK` | `cross-seed/src/routes/baseApi.ts` |
| `GET /api/status` requires an API key, so the kubelet cannot use it | same file |
| No unauthenticated dependency-aware endpoint exists | same file |
| `/cross-seed` is not used by v6; all state is in `/config` | `cross-seed/src/constants.ts`, `configuration.ts` |
| Latest release is `v6.13.9` (22 Oct 2026), a security patch over the deployed `6.13.7` | GitHub releases |
| The image entrypoint is `catatonit -- /usr/local/bin/cross-seed`; the chart supplies only `args: [daemon]` | image `Dockerfile` at tag `v6.13.9` |
| The image sets no `USER`, so it runs as root unless the chart sets a UID | image `Dockerfile` at tag `v6.13.9` |
| `config.js` is read at startup; `generateConfig()` returns immediately when the file exists and never rewrites it | `cross-seed/src/configuration.ts` at tag `v6.13.9` |
| `ghcr.io/cross-seed/cross-seed` is the only image source the project documents | upstream README |

Observed usage on `streaming`: about 64 MiB resident memory and 54m CPU while scanning RSS feeds.
The current 512Mi limit has held for 60 days without an OOM kill.

## Goals

1. Follow the repo chart contract: names from `app.*` helpers, no hardcoded object names, no tabs,
   `helm lint` and `helm template` clean.
2. Give cross-seed's real needs named values: `torrents`, `configFile`, `probes`, `resources`,
   `securityContext`.
3. Make the probes honest: liveness on `/api/ping`; no fake readiness probe.
4. Run non-root as UID/GID 1100, matching the PUID/PGID used by the rest of the stack, with a
   documented one-time `chown` on the node.
5. Move to image `6.13.9` and keep `appVersion` equal to `image.tag`.
6. Delete the parts that exist only because of the copy: the `busybox` init container, the unused
   `/cross-seed` mount and `cross-seed2` claim, the dead `ingress` values, and the raw `service.port`
   duplication where a `container.port` fallback is enough.
7. Rewrite `docs/charts/README.md` so the reference is explicitly a philosophy and a contract, not a
   byte-for-byte copy. Update the autobrr README's "use this chart as a template" section to match.

## Non-goals

- No IngressRoute and no web UI exposure.
- No change to `cluster/argocd/projects/media-stack/cross-seed.yaml`.
- No data migration: the new chart keeps the live claim name `cross-seed-config`.
- No automatic synchronization of `appVersion` with `image.tag`. Renovate still bumps `image.tag`
  only; the mismatch is fixed now and the README says to update both on an upgrade.

## Anti-goals

- Copying `autobrr` byte-for-byte and compensating with raw pod spec in values.
- A readiness probe that points at the same endpoint as liveness.
- Hardcoding secret or claim names in templates.
- Changing the UID before the config volume is chowned: that produces a pod that starts and then
  fails on the first database write.

## Approaches considered

**A. Literal copy of autobrr, extras in raw `volumes` / `volumeMounts`.**
Cheap, and future contract fixes copy mechanically. But cross-seed's two real needs (an NFS torrent
mount and a config file from a Secret) would live as unnamed pod spec in values, which is exactly the
kind of per-app peculiarity nobody remembers.

**B. Autobrr's shape, with named values for the app-specific needs.**
Templates carry `torrents`, `configFile`, and probe wiring. More template code, but each cross-seed
need is a documented key, the chart can evolve without changing its public values, and a future
reader sees why the two extra mounts exist.

**Chosen: B.** The operator's priority is charts that are easy to maintain and follow the image's
specific requirements; a copy stops satisfying that as soon as an app needs something the reference
does not have.

## Values (shape)

```yaml
image:
  repository: ghcr.io/cross-seed/cross-seed
  tag: "6.13.9"
  pullPolicy: IfNotPresent

strategy: Recreate

# Image entrypoint is catatonit + cross-seed; only the subcommand is supplied.
args:
  - daemon

service:
  type: ClusterIP
  port: 2468
container:
  port: null

env:
  TZ: "Europe/Paris"
  CONFIG_DIR: "/config"

storage:
  enabled: true
  existingClaim: ""          # defaults to <fullname>-config = cross-seed-config
  className: local-path
  accessModes: [ReadWriteOnce]
  size: 128Mi
  mountPath: /config

configFile:
  secretName: cross-seed     # must exist in the namespace
  key: config.js
  mountPath: /config/config.js

torrents:
  existingClaim: pvc-nfs-qbittorrent-config-nfs-subdir-external-provisioner
  subPath: qBittorrent/BT_backup
  mountPath: /torrents
  readOnly: true

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    memory: 512Mi            # no CPU limit, so scans are never throttled

probes:
  liveness:
    enabled: true
    path: /api/ping
    initialDelaySeconds: 20
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    enabled: false
```

`volumes` / `volumeMounts` stay as the raw escape hatch, as in the reference, but they are empty.

## Decisions

### Named values, per-app templates

`configFile` and `torrents` are first-class values. The templates build the Secret volume, the
`subPath` mount, and the NFS mount from them. `torrents.readOnly: true` is deliberate: with
qBittorrent, cross-seed only reads torrent files and injects through the API.

### Config delivery: mount the Secret directly, drop the init container

The old chart copied `config.js` from the Secret onto the `/config` claim with a `busybox` init
container at every start. That hides the source of truth and makes edits to the claim silently
disappear on the next restart. The new chart mounts the Secret key at `/config/config.js` with
`subPath`. Consequences:

- `config.js` is read-only. The daemon reads it; it does not rewrite it, and the UI that could is
  not exposed.
- A Secret change does not update a `subPath` mount in place, so changing `config.js` requires a pod
  restart. The old chart also only picked it up on restart; this makes the behavior explicit.
- The rest of `/config` stays writable on the claim.

### Probes

Liveness is `GET /api/ping` with a 20s initial delay and a generous 30s period: startup includes
database migrations, and a failed liveness probe during migration would restart-loop the pod.

Readiness is disabled. There is no unauthenticated endpoint that reports dependency health;
`/api/status` needs the API key, and pointing readiness at `/api/ping` would duplicate liveness and
gate no traffic. The Service exists for port-forwarding and future use, not for routing.

### Resources

Keep the measured footprint: request `128Mi` memory and `50m` CPU, limit `512Mi` memory, no CPU
limit. The CPU request is `50m`, rounded down from the observed ~54m; a request is a scheduling
reservation, not a cap, so rounding down is safe and does not throttle the process. The memory
request is `128Mi`, above the observed ~64Mi, to leave headroom for torrent parsing; the `512Mi`
limit is what the process has stayed under for 60 days without an OOM kill. This replaces the
previous `10m` CPU request, which was far below what the process actually uses.

### Security and the UID

`seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`, all capabilities dropped,
`runAsNonRoot`, `runAsUser`/`runAsGroup` 1100. The image runs as root by default.

Pre-flight, before the new pod starts:

1. **Config drift check.** The old chart copies the Secret onto the claim at every start, so the
   file the daemon runs may have been edited in place since the pod started. Compare the two before
   switching to a read-only mount:

   ```bash
   kubectl -n media-stack get secret cross-seed -o jsonpath='{.data.config\.js}' | base64 -d > /tmp/secret-config.js
   kubectl -n media-stack exec deploy/cross-seed -- cat /config/config.js > /tmp/claim-config.js
   diff -u /tmp/secret-config.js /tmp/claim-config.js
   ```

   If they differ, stop and decide which one wins. The Secret is the intended source; a difference
   means someone edited the claim and that change would otherwise vanish at rollout.

2. **`cross-seed2` emptiness check.** See the removal section above:
   `kubectl -n media-stack exec deploy/cross-seed -- du -sh /cross-seed`.

3. **Ownership.** Scale the Deployment to zero (safe: auto-sync is off, so no self-heal reverts it),
   resolve `cross-seed-config` to its PV and the PV to its host path, then
   `chown -R 1100:1100` that directory on `streaming`.

4. Apply the chart and confirm the pod can write `/config` (the database is the real test).

The NFS torrent mount is read-only and owned by qBittorrent's PUID 1100, so it needs no change.

### Image and version

`ghcr.io/cross-seed/cross-seed:6.13.9`. GHCR is the documented source; the Docker Hub namespace is a
legacy mirror. `appVersion` becomes `6.13.9` to match the image tag, and the chart version becomes
`1.0.0` because the values schema is not backward compatible.

The image move itself is a patch bump (`6.13.7` → `6.13.9`), but today's `appVersion` is `5.9.2`, a
major version behind the `6.13.9` image, so this change also corrects the
`app.kubernetes.io/version` label rather than only advancing it.

### Removals

- `templates/configmap-env.yaml`: its `conf.content` path was never populated in values.
- `templates/pvc-cross-seed.yaml` (`cross-seed2`) and the `/cross-seed` mount: v6 does not use them.
- The `ingress` values block and all `networking.k8s.io` leftovers: no web UI exposure.
- The init container and `busybox` image.

The `cross-seed2` claim is still bound in the cluster. Removing it from the chart stops managing it;
ArgoCD will **not** delete it: the Application has no automated sync and no prune policy, and every
sync in its history was manual. It becomes an untracked leftover, and the Application still reports
`Synced`. The removal is therefore an explicit, separate step:

1. Before the rollout, confirm the claim is empty:
   `kubectl -n media-stack exec deploy/cross-seed -- du -sh /cross-seed`.
2. Merge and manually sync the Application.
3. After the new pod is healthy, `kubectl -n media-stack delete pvc cross-seed2`.

If step 1 shows data, stop and copy it off before deleting anything. Under v6 the claim is not used.

### Names

The release name is `cross-seed`, so `app.fullname` stays `cross-seed`; the Service, Deployment, and
ServiceAccount names do not change. The config claim stays `cross-seed-config`. The selector labels
do not change, so the rollout replaces the pod without orphaning anything.

## Documentation changes

`docs/charts/README.md`:

- Replace "Copy `autobrr`, change the four values…" with the principle: autobrr is a reference for
  naming, labels, claim shape, values-driven routing and probe honesty; the chart is expected to
  adapt to the image it serves.
- State the priority: maintainable charts that follow each image's specific requirements over
  literal similarity to the reference.
- Explain what to take, what to adapt, and what to leave out when an app does not need it (for
  example, an IngressRoute for an app that is not exposed).
- Add cross-seed as the example of an adapted chart with named values.
- Keep the contract table unchanged; it is the part that is not negotiable.

`cluster/helm/media-stack/autobrr/README.md`: rename "Using this chart as a template" to "Using this
chart as a reference" and apply the same principle.

New `cluster/helm/media-stack/cross-seed/README.md`: upstream links, what values control, why the
config is a Secret mount, why readiness is off, the UID chore, and the upgrade rule (`image.tag` and
`appVersion` move together).

## Verification

Local, before committing:

```bash
helm lint cluster/helm/media-stack/cross-seed --namespace media-stack
helm template cross-seed cluster/helm/media-stack/cross-seed --namespace media-stack >/dev/null
grep -rnP '\t' cluster/helm/media-stack/cross-seed/templates || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/cross-seed/values.yaml || echo "no floating tags"
grep -rn 'cross-seed2\|networking.k8s.io' \
  cluster/helm/media-stack/cross-seed/templates \
  cluster/helm/media-stack/cross-seed/values.yaml || echo "no leftovers"
```

The leftover check is scoped to `templates/` and `values.yaml` on purpose: the new chart README
mentions `cross-seed2` deliberately, to explain that the claim was removed from the chart and has to
be deleted by hand, so an unscoped grep over the chart directory would match that note.

After the merge, sync the Application manually (auto-sync is off) and check:

- The Application reports `Synced` with the new chart.
- The Deployment rolls once; the pod runs as 1100 and stays `Running`, not `CrashLoopBackOff`.
- The liveness probe passes.
- Logs show the scheduler jobs (`rss`, `inject`) resuming.
- `cross-seed.db` in `/config` is still the same database (seeding history preserved).
- `cross-seed2` is deleted separately, after the emptiness check.

## Risks

- **Config drift:** if `/config/config.js` on the claim differs from the Secret, the rollout reverts
  the claim to the Secret. The pre-flight diff above catches it.
- **UID change:** the one-time `chown` is mandatory and requires downtime. Doing it in the wrong
  order produces a write failure after a seemingly healthy start.
- **`cross-seed2`:** deletion is destructive and manual, after an emptiness check. It is not tied to
  the chart sync.
- **NFS permissions:** if `BT_backup` turns out not to be readable by 1100, cross-seed logs a read
  error. The fix is an NFS-side ownership change, not a chart change.
- **Secret rotation:** `config.js` changes need a pod restart because of `subPath`, documented in the
  README.

## Left to the builder

- Exact template file contents and ordering.
- Wording of the READMEs.
- Splitting the work into commits (chart, docs, version bump) once the plan exists.
