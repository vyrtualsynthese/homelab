# lidarr

Lidarr is the music manager of the media stack. It watches indexers, grabs releases through
qBittorrent and files them into the audio library Plex reads. It is internal-only:
`lidarr.kub.local` on the `web` entry point, no TLS, no authentication in front of it.

The chart is built from its sibling `../sonarr` — the same LinuxServer-plus-exportarr shape — and the
contract in [`docs/charts/README.md`](../../../../docs/charts/README.md). The differences are listed
below; each one follows from what Lidarr is.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/lidarr.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `lidarr`, and it contains the
chart name, so every object is named `lidarr` — no `lidarr-lidarr` suffix.

```bash
# what the cluster will render, exactly
helm template lidarr cluster/helm/media-stack/lidarr --namespace media-stack
```

It renders one Deployment, one Service (ClusterIP, 8686/TCP), one ServiceAccount, one
PersistentVolumeClaim (`lidarr-config`) and one IngressRoute (`lidarr`).

## Why this chart differs from its sibling

| Reference (sonarr) | Here | Why |
|---|---|---|
| `/downloads` and `/movies` | `/downloads` and **`/audio`** | Lidarr's library is the audio pool Plex mounts at `/audio`; the mount is read-write because imports rename and move files |
| Config claim with existing data | **The chart creates the claim** | The previous `lidarr-config` was a Longhorn volume that vanished in the 2026 disk failure. There is nothing to migrate |
| `SONARR__LOG__LEVEL`, `SONARR__LOG__CONSOLELEVEL` | `LIDARR__LOG__LEVEL`, `LIDARR__LOG__CONSOLELEVEL` | Lidarr's default level is `debug`, the noisiest in the stack |
| Exporter argument `sonarr` | `lidarr` | Same sidecar, different collector |

## Documented exceptions to the chart contract

1. **Probes share one endpoint, and it is a real dependency check.** `/ping` is Lidarr's
   unauthenticated health endpoint, and Lidarr's `PingController` reads the configuration repository
   on every call — so it answers 500 when the database on `/config` cannot be opened. Readiness uses
   the same path as liveness because the database is the only dependency and the endpoint already
   reports it. The API's `/health` endpoint would be stronger but needs the API key, which a probe
   cannot read.
2. **No `runAsNonRoot`, no `runAsUser`, no `fsGroup`.** The LinuxServer s6 init must start as root to
   prepare `/config`, then drops to `PUID`/`PGID` 1100. `fsGroup` would do nothing: `local-path` is
   hostPath-backed and the kubelet does not apply group ownership to it.
3. **The config claim is 128Mi and cannot grow.** `local-path` does not support volume expansion, and
   hostPath does not enforce the number, so 128Mi is a label rather than a quota.

## A fresh install

This release starts empty. The previous config volume does not exist, so the app creates a new
database and **mints a new API key at first start**. After the pod is `Running`, in the UI:

1. **Settings → Media Management → Root Folder** → `/audio`.
2. **Settings → Profiles** → set the quality profile the collection needs.
3. **Settings → Download Clients** → qBittorrent at `http://qbittorrent:8080`, with a category for
   Lidarr.
4. In Prowlarr: **Settings → Apps → Add → Lidarr**, URL `http://lidarr:8686`, and the API key from
   **Settings → General → API Key**.

## Secrets

The chart expects an existing Secret `lidarr` in `media-stack`:

| Key | Used by | Note |
|---|---|---|
| `secret` | Exporter sidecar, `APIKEY` | Lidarr → Settings → General → API Key. Without it the exporter target stays down |

The key does not exist yet — Lidarr has not started since the disk failure. Add it to
`~/.config/secrets/homelab.env` (`LIDARR_API_KEY`, already present and empty) after the first start,
without printing the value:

```bash
source ~/.config/secrets/homelab.env
kubectl -n media-stack create secret generic lidarr \
  --from-literal=secret="$LIDARR_API_KEY"      # first time
kubectl -n media-stack patch secret lidarr --type merge \
  -p "{\"data\":{\"secret\":\"$(printf %s "$LIDARR_API_KEY" | base64 -w0)\"}}"   # later rotations
kubectl -n media-stack rollout restart deployment/lidarr
```

**The restart is part of the change, not an afterthought.** `APIKEY` is an environment value read
when the container starts, so a patched Secret is invisible to the running sidecar until its pod is
replaced. Without the restart the target stays down and the Secret looks innocent.

Until the Secret exists, the sidecar container does not start and the pod reports `1/2`. The app
container is unaffected either way.

## Metrics

The `exportarr` sidecar listens on 9707 and is scraped by pod annotation (`prometheus.io/scrape`,
`prometheus.io/scheme`, `prometheus.io/port`, `prometheus.io/path`), all rendered from the `exportarr`
block. The metrics port is deliberately not on the Service: Prometheus scrapes the pod IP. Disabling
`exportarr.enabled` removes the sidecar and the annotations together.

If the target is **down with HTTP 500**, exportarr is passing Lidarr's 401 through: the `secret` key
in the `lidarr` Secret is stale or the pod has not been restarted since it was patched, not the chart.

## Resources are unmeasured

Lidarr has not run since the 2026 disk failure, and Prometheus keeps no series for it. The values
follow sonarr's measured profile (`10m` CPU, `256Mi` memory request) with a `1Gi` memory limit for the
first full library scan. Replace both numbers from Prometheus after a week of real traffic; an OOM
during a scan shows as a restarted container.

## Upgrading and rolling back

The chart pins `3.1.6.5079-ls48`. Lidarr migrates its database forward on first start and does not
migrate it back, so a rollback to an older image means deleting `lidarr-config` and starting from an
empty library, not restoring a schema. Take a copy of `/config` before a version bump:

```bash
# resolve lidarr-config to its hostPath on the streaming node first
sudo cp -a /mnt/ssd0/<pv-name>_media-stack_lidarr-config /mnt/ssd0/lidarr-config-backup
```
