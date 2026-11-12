# radarr

Radarr manages a movie library: it watches indexers for releases that match the films you have asked
for, hands the chosen release to a download client, then imports the finished download into the movie
folder under its final name. It follows the chart contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md); the shape is copied from
`media-stack/prowlarr`, with the differences this app needs: it reads two media claims, and its
metrics exporter runs against Radarr's own API.

## Upstream

- Website: https://radarr.video/
- Source: https://github.com/Radarr/Radarr
- Image: https://docs.linuxserver.io/images/docker-radarr/
- Registry: `lscr.io`, the official one. The previous chart pulled from the
  `ghcr.io/linuxserver/radarr` mirror rather than `lscr.io`; this chart uses
  `lscr.io/linuxserver/radarr` directly.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/media-stack/radarr.yaml`,
using the chart in this directory and the `values.yaml` next to it. The release name is the
Application name, `radarr`, so every object lands on the `radarr` / `radarr-config` names.

That yields exactly five objects:

| Kind | Name |
|---|---|
| Deployment | `radarr` |
| Service | `radarr` |
| ServiceAccount | `radarr` |
| IngressRoute | `radarr` |
| PersistentVolumeClaim | `radarr-config` |

```bash
# what the cluster will render, exactly
helm template radarr cluster/helm/media-stack/radarr --namespace media-stack
```

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image and pinned tag. | `lscr.io/linuxserver/radarr`, `6.4.4.10685-ls326` |
| `strategy` | Deployment strategy. Must stay `Recreate` with a config volume. | `Recreate` |
| `service.port` | Port the Service exposes, and what the IngressRoute points at. | `7878` |
| `container.port` | Only set when the container listens on a different port than the Service. | `null` (falls back to `service.port`) |
| `storage.*` | The config claim: class, size, mount path, or reuse an existing claim. | `local-path`, `128Mi`, `/config` |
| `downloads.existingClaim`, `downloads.mountPath` | qBittorrent's completed-downloads claim, and where it is mounted. | `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner`, `/downloads` |
| `media.existingClaim`, `media.mountPath` | The Plex movie library claim and its mount. | `pvc-plex-media`, `/movies` |
| `ingressRoute.*` | Traefik CRD routing: host, entry points, middlewares, TLS. | `radarr.kub.local`, `web`, no TLS |
| `env` | Non-secret variables, rendered inline into the container. Changing one rolls the pod. | `TZ`, `PUID`, `PGID`, log level |
| `exportarr.*` | The metrics sidecar. See **Metrics**. | enabled on port 9707 |
| `probes.{startup,liveness,readiness}` | HTTP probes on `/ping`. | enabled; startup 5s × 60, liveness 20s delay, readiness 10s delay |
| `resources.*` | Scheduler reservation and memory limit, sized from Prometheus. No CPU limit. | `10m`/`256Mi` request, `1Gi` memory limit |
| `securityContext` | Container hardening that does not break the LinuxServer init. | no privilege escalation |
| `nodeSelector` | Node pin. Every workload is pinned, and the config claim is node-bound. | `kubernetes.io/hostname: streaming` |

## The media mounts

Radarr needs two volumes beyond its config, and both are read-write:

| Claim | Mount | Why |
|---|---|---|
| `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` | `/downloads` | qBittorrent writes the finished download here; Radarr imports from it, and can also delete the leftover folder after a successful import. |
| `pvc-plex-media` | `/movies` | The library Plex reads. Radarr writes the imported file into it under its final name. |

Both are shared NFS claims, not claims this chart creates: `existingClaim` is filled in, so no
`PersistentVolumeClaim` object is rendered for either, and the app must never be the only writer.
Read-only would break the import — a mount without write access cannot create the destination
folder, and Radarr also deletes the source once the file is in the library.

The two paths are separate NFS exports on the NAS — `/mnt/ultrafaststorage/nfs` for downloads and
`/mnt/storage/nfs/plexmedia` for the library — so inside the pod they are different filesystems. A
rename across filesystems fails, so the import is a copy-then-delete, not a rename: Radarr writes a
full copy into the library and then removes the source. Keeping downloads and the library on the
same export is what restores the rename.

## Probes

| Probe | Path | Behaviour |
|---|---|---|
| startup | `/ping` | Runs before the other two; 5s × 60 = 300s budget |
| liveness | `/ping` | Answers `{"status":"OK"}` once the web stack is serving |
| readiness | `/ping` | Same endpoint on purpose (see below) |

`/ping` is Radarr's own unauthenticated health endpoint. The API's `/health` endpoint would be a
stronger readiness check, but it requires the API key, and a Kubernetes probe cannot read a Secret.

Liveness and readiness share the endpoint because Radarr's only dependency is its SQLite database on
the config volume, and a database that cannot be opened stops the process from starting rather than
leaving a pod that runs but cannot serve. There is no dependency whose failure readiness could report
while liveness stays green.

Startup gets the full 300s, longer than the other charts' 150s, because the first start after a
version bump migrates that database and can take noticeably longer than a restart.

## Logs

The app logs at `warn`, not at the default `info`. Fluent Bit tails every container on every node and
ships every line to Loki without a filter, so the app's own level is the only place the volume can be
cut. At `info`, Radarr writes a line per HTTP request, per library scan and per import.

Both knobs are set, because they address different sinks:

| Variable | Value | Sink |
|---|---|---|
| `RADARR__LOG__LEVEL` | `warn` | the log file on the config volume |
| `RADARR__LOG__CONSOLELEVEL` | `warn` | stdout, which Fluent Bit ships to Loki |

Raise both while debugging, then put them back. The operational record is the Activity, Wanted and
History views in the UI — what was grabbed, imported and failed — not the log stream.

## Metrics

The chart runs `exportarr` (a Prometheus exporter for the *arr apps) as a sidecar — a second
container in the same pod — and, while `exportarr.enabled` is true, renders the `prometheus.io/*`
annotations Prometheus uses to discover pods. Prometheus scrapes `:9707/metrics` on the pod.

The sidecar's `args` must be `- radarr`. That argument selects which app's API exportarr talks to;
left at `- prowlarr` it queries the Prowlarr endpoints against Radarr and every query answers 404 or
the wrong shape. This is the one line that must differ from the reference chart.

**The Secret `radarr` must hold Radarr's current API key**, under the key `secret`. A key that no
longer matches the app — the usual cause is a restore, a reinstall, or a database rebuilt from
scratch — makes `/metrics` answer HTTP 500 while the sidecar log shows Radarr returning **401** for
every request, and Prometheus reports the target down. Replacing the Secret value is an operator
step, not a chart change: this chart reads the key, it does not repair it.

## UID and a recreated claim

This is a LinuxServer image: it starts as **root**, prepares `/config`, then drops to
`PUID`/`PGID`. That is why the values set those to `1100` and **do not** set `runAsNonRoot` or
`runAsUser`; running the container directly as 1100 prevents the init from doing its job.

Radarr already runs as 1100 on the live cluster, so its config directory is owned correctly today. A
claim recreated from scratch does not inherit that: `local-path` is hostPath-backed, the provisioner
creates the directory as root, and the kubelet does not apply `fsGroup` to it either. The first pod
on a fresh claim then fails to write, so `chown -R 1100:1100` the volume directory on `streaming`
before the pod starts. The `autobrr` README describes the procedure.

## Upgrading

Renovate moves `image.tag` only; bump `appVersion` in the same commit by hand so the two stay equal.
The `appVersion` becomes the `app.kubernetes.io/version` label and is what tells you which version a
pod is actually running, so a chart left behind is a chart that reports the wrong version.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment, the inline env, the two media mounts and the exportarr sidecar |
| `templates/service.yaml` | the ClusterIP Service |
| `templates/serviceaccount.yaml` | the ServiceAccount |
| `templates/persistentvolumeclaim.yaml` | the config claim, only when `storage.enabled` and no `existingClaim` |
| `templates/ingressroute.yaml` | the Traefik IngressRoute |
| `templates/NOTES.txt` | the install notes |

There is deliberately no ConfigMap: Radarr takes its configuration from environment variables and
its own SQLite database on the claim.

## Uninstalling

The config claim is created by this chart, so deleting the release deletes the claim — which holds
the movie database, the indexer and download-client settings, and the API key. Take a copy first if
it holds anything you want. The two media claims are not created here and are left alone.
