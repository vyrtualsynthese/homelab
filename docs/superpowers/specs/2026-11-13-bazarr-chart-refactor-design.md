# bazarr chart refactor — design

- Date: 2026-11-13
- Status: awaiting approval
- Scope: `cluster/helm/media-stack/bazarr`, `cluster/helm/media-stack/bazarr-anime`,
  their two ArgoCD Applications, `docs/charts/README.md`, `AGENTS.md`, and two key slots in
  `~/.config/secrets/homelab.env`.

## Intent

Both Bazarr instances have been dead since 2026-09-02. Their config claims disappeared with the
Longhorn loss, their PVs are stuck `Terminating`, and the Deployment still carries a
`node-type: general` affinity for a label the nodes no longer have. Nothing can schedule.

The chart itself is also the problem. It is `selfhosted-helmcharts` 0.1.3, the `helm create`
scaffold from a repository that has stalled. It puts the environment in a ConfigMap, exposes the app
through a core `networking.k8s.io` Ingress, probes `/` with no startup budget, sets no resources, pins
no node, and has no exporter.

This rebuild replaces both chart directories with hand-maintained local charts that follow
[`docs/charts/README.md`](../../charts/README.md), keeps the behaviour the instances already depend on
(image, port, hosts, media mount), and makes the two API keys first-class in `homelab.env` for the
day the exporter is switched on.

## What is true today (verified 2026-11-13)

| Fact | Evidence |
|---|---|
| Both pods are `Pending`, created 2026-09-02 | `pods_list` |
| `bazarr-config` and `bazarr-anime-config` claims do not exist; the event is `persistentvolumeclaim "bazarr-config" not found` | pod event, `PersistentVolumeClaim` list |
| The matching PVs are `Terminating`, `longhorn-static`, `Retain` | `PersistentVolume` list |
| Both Deployments require `node-type: general`; the live nodes no longer carry that label | Deployment spec, node list |
| `pvc-plex-media` is `Bound` (NFS subdir provisioner) and is mounted at `/media` by the old Deployment | claim spec, pod volumes |
| Live image is `ghcr.io/linuxserver/bazarr:1.6.0` | pod spec |
| The old chart renders a core `Ingress` for `bazarr.kub.local` / `bazarr-anime.kub.local` on `web,websecure` with the HTTPS redirect middleware | live Ingress |
| Newest stable Bazarr is `1.6.2` (2026-09-26); the `1.6.2` image tag exists on ghcr | GitHub releases, registry manifest |
| Exportarr `v2.3.0` supports Bazarr, but its scrape walks every episode and takes tens of seconds; the switch to disable that walk (`DISABLE_EPISODE_METRICS`) is unreleased | exportarr README, tag list |
| Prometheus scrapes every 60 s here | `docs/observability-conventions.md` |
| Bazarr's logger only has INFO and DEBUG; there is no warning level and no environment variable for it | `bazarr/app/logger.py` |

## Goals

1. Two full local charts, one per instance, built from the contract and the `sonarr` /
   `sonarr-anime` pair as the nearest reference.
2. Recreate the config claim as `<fullname>-config` on `local-path`, 128Mi, with the pod pinned to
   `streaming` so the claim lands there.
3. Mount the shared media claim `pvc-plex-media` at `/media`, read-write: Bazarr writes subtitle
   files next to the videos.
4. Replace the core Ingress with a Traefik `IngressRoute` on the `web` entry point, same two hosts.
5. Probes on `/` with a real startup budget; requests and memory limits on both containers.
6. Keep the `exportarr` block in the values and template, `enabled: false`, with no scrape
   annotations rendered until it is turned on.
7. Add `BAZARR_API_KEY` and `BAZARR_ANIME_API_KEY` to `~/.config/secrets/homelab.env` with the same
   comment shape as the other app keys.
8. Give each chart a README that records the probe, log-level, metrics and key decisions, and update
   the repo chart contract with the pair as a worked example.

## Non-goals

- No exporter scraping now. The block is present and off; this is the operator's decision.
- No Bazarr configuration: Sonarr/Radarr connections, language profiles and folder paths are UI work
  after first start.
- No Secret creation. With the exporter off, neither chart references a Secret, so both pods can
  start before any key exists.
- No change to `pvc-plex-media`, Plex or the NFS provisioner.
- No Bazarr `1.6.3` beta.
- No shared chart or subchart.

## Anti-goals

- A scrape annotation while no exporter runs. That is the `unpackerr` failure this repo already
  documented.
- A chart that stores environment in a ConfigMap.
- `runAsNonRoot`, `runAsUser` or `fsGroup`: the LinuxServer s6 init must start as root, prepare
  `/config`, then drop to `PUID`/`PGID`; `fsGroup` does nothing on hostPath-backed `local-path`.
- Pinning `latest` or leaving `appVersion` behind `image.tag`.
- Claiming a warning log level Bazarr does not have.

## Approaches considered

**A. Two full chart directories.** `bazarr/` and `bazarr-anime/` each own `Chart.yaml`, `values.yaml`
and `templates/`. Identical templates, different values. This is the `sonarr` / `sonarr-anime` and
`radarr` / `radarr-anime` pattern, and the contract explicitly refuses a shared chart.

**B. One chart, two Applications with `valueFiles`.** This is pattern B in `AGENTS.md`, kept for the
ten legacy apps that still resolve a remote chart. Using it here would put the chart in one directory
and the per-instance values in another, add a second source to each Application, and diverge from
every hand-maintained media chart. It saves duplication the contract has already accepted.

**C. Keep the remote chart and bump the image.** Cheapest, but it leaves the stalled repository in the
deployment path and keeps the scaffold defects (ConfigMap environment, core Ingress, no resources,
retired node label). It does not satisfy the request.

**Chosen: A.** The instances differ only in name, host and key, which is exactly what values.yaml is
for; the templates stay per-app so a future Bazarr peculiarity does not leak into the anime instance.

## Chart shape

Each instance renders five objects:

| Object | Name | Notes |
|---|---|---|
| Deployment | `<fullname>` | `Recreate`, one container |
| Service | `<fullname>` | ClusterIP, 6767/TCP |
| ServiceAccount | `<fullname>` | no mounted token |
| PersistentVolumeClaim | `<fullname>-config` | `local-path`, 128Mi |
| IngressRoute | `<fullname>` | `traefik.containo.us/v1alpha1`, `web` |

`Chart.yaml`: `name` is `bazarr` and `bazarr-anime`, `version: 1.0.0`, `appVersion: "1.6.2"`, matching
`image.tag`. The second instance keeps its own chart name, as `sonarr-anime` does, so its labels and
dashboards read `app.kubernetes.io/name: bazarr-anime`.

Templates are the `sonarr` set with the media block narrowed to one mount:
`_helpers.tpl`, `deployment.yaml`, `service.yaml`, `serviceaccount.yaml`,
`persistentvolumeclaim.yaml`, `ingressroute.yaml`, `NOTES.txt`. Helper names stay unprefixed
(`app.fullname`, `app.labels`, `app.selectorLabels`, …) per the contract.

### Values that differ from `sonarr`

| Key | Value | Why |
|---|---|---|
| `image.tag` | `1.6.2` | Newest stable; live was `1.6.0` |
| `service.port` | `6767` | Bazarr's own port |
| `ingressRoute.entryPoints` | `[web]` | Internal host, no TLS, no redirect |
| `ingressRoute.host` | `bazarr.kub.local` / `bazarr-anime.kub.local` | Unchanged |
| `storage.size` | `128Mi` | House standard for config |
| `media.claim` | `pvc-plex-media` | Shared with the *arr apps and Plex |
| `media.mountPath` | `/media` | Preserves the old chart's layout; one claim holds TV and movies |
| `env` | `TZ`, `PUID`, `PGID` only | Bazarr has no log-level variable (see exceptions) |
| `exportarr.enabled` | `false` | See decisions |
| `resources` | request `10m` / `256Mi`, limit `768Mi` | Same house pair as the Sonarr charts; re-measure after it runs |
| `probes` | startup `/` 5s×60, liveness 20/30, readiness 10/15 | First start initialises SQLite; no dependency endpoint without the API key |
| `nodeSelector` | `kubernetes.io/hostname: streaming` | Media work, same node as Sonarr/Radarr |

### Probes and hardening

`/` is the only unauthenticated endpoint that answers. The API's own status endpoint needs the API
key, and a probe cannot read a Secret. Readiness shares `/` with liveness because Bazarr's dependency
failures (Sonarr, Radarr) do not stop its own web stack: there is no endpoint the kubelet could use
to distinguish them. This is the same exception the Sonarr pair documents.

Hardening stops where the LinuxServer init starts: `allowPrivilegeEscalation: false` and the runtime
seccomp profile, no capability drop, no `runAsNonRoot`, no `fsGroup`. The image starts as root, drops
to `PUID`/`PGID` 1100.

## Decisions

**The exporter stays off, and the chart says so.** The `exportarr` block is present with
`enabled: false`, so the Deployment renders no sidecar and no `prometheus.io/*` annotations. When
exportarr v3 ships with `DISABLE_EPISODE_METRICS`, enabling it is a tag bump plus the flag: v3 also
renames the API-key variable from `APIKEY` to `API_KEY`, so the sidecar's env changes with it.
Flipping the block's flag adds the sidecar and its annotations together. Until then, Bazarr is absent
from the target list rather than present and down, which is the rule the `unpackerr` chart
established.

**The two API keys go in now, without values.** `homelab.env` gains the two names and the comment
that says where the key comes from (Bazarr → Settings → General → API Key). The value is pasted after
the app has started and generated one. Enabling the exporter later then needs only: paste the key,
create the `<instance>` Secret with key `secret`, set `exportarr.enabled: true`, add the v3 flag and
restart the Deployment. The lines have no `export ` prefix, matching `SONARR_API_KEY` and
`RADARR_API_KEY`; everything that reads them sources the file first.

**No log-level setting.** Bazarr's logger is INFO unless `general.debug` is set in `config.yaml`, and
there is no level between INFO and DEBUG. Writing `LOG_LEVEL` would do nothing and would hide the
exception. The README records the INFO volume as accepted debt, with the UI's history and the
exporter (when it lands) as the operational record.

**Claim names stay `bazarr-config` / `bazarr-anime-config`.** The old claims are gone, but keeping the
derived names means a future restore of an old volume needs no rename.

## Migration

1. Commit the two chart directories, the two Application edits, docs, and the `homelab.env` slots.
2. The Applications switch from the remote `sources:` form to the local `source:` form. Neither
   Application records `syncPolicy.automated`: auto-sync is enabled per Application in the ArgoCD UI,
   by design. Merging the PR therefore changes nothing until the operator syncs each Application
   **with prune**; the root app only rewrites the Application objects.
3. The object names do not change for `bazarr`; the `bazarr-anime` Deployment **does** change its
   selector (`app.kubernetes.io/name` moves from `bazarr` to `bazarr-anime`), and that field is
   immutable. Delete `deployment/bazarr-anime` before the first sync, or sync with Replace. Both pods
   are Pending, so nothing is lost.
4. The sync prunes the two core Ingresses and the two `*-env` ConfigMaps, adopts the Services and
   ServiceAccounts by name, and creates the two config claims.
5. On `streaming`, the new claims provision on `local-path`; the pods schedule and start with a fresh
   Bazarr config. The two old PVs are still `Terminating` under `longhorn-static`, a different
   StorageClass, so they cannot capture the new `local-path` claims; confirm both claims bind rather
   than assume it.
6. Verify: pods `Running` and ready, claims `Bound`, IngressRoutes present, `bazarr.kub.local` and
   `bazarr-anime.kub.local` answer, old Ingresses gone.

## Testing

```bash
helm lint cluster/helm/media-stack/bazarr -n media-stack
helm lint cluster/helm/media-stack/bazarr-anime -n media-stack
helm template bazarr cluster/helm/media-stack/bazarr -n media-stack >/dev/null
helm template bazarr-anime cluster/helm/media-stack/bazarr-anime -n media-stack >/dev/null

grep -rnP '\t' cluster/helm/media-stack/bazarr*/templates || echo "no tabs"
grep -rn 'networking.k8s.io\|node-type\|latest' cluster/helm/media-stack/bazarr* || echo "no stale markers"
grep -rn 'prometheus.io/scrape' cluster/helm/media-stack/bazarr* || echo "no scrape annotations while disabled"
grep -cE '^BAZARR(_ANIME)?_API_KEY=' ~/.config/secrets/homelab.env   # expect 2
```

`kubeconform` is not installed on this host; `helm lint` and `helm template` are the local gate, as
the contract says.

## Rollback

Revert the two Application files to the remote chart; it is still published. The config claims are
local and can be deleted with the release, but the data is already gone, so rolling back loses
nothing that the rebuild has not already replaced.

## What the operator still does

- Merge the PR.
- Delete `deployment/bazarr-anime` (or use Replace) before the first sync.
- After first start, paste each API key into `homelab.env`; when exportarr v3 lands, create the
  matching Secret and enable the exporter.
- Configure Bazarr in the UI: Sonarr/Radarr instances, language profiles, folder paths.
