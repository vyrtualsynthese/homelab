# sonarr-anime

A second Sonarr instance, dedicated to the anime library. It runs the same image and the same chart
shape as [`../sonarr`](../sonarr), with its own config claim, host, API key and memory size. It is
internal-only: `sonarr-anime.kub.local` on the `web` entry point, no TLS, no authentication in front
of it.

The chart is built from the reference chart `../prowlarr` and the contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md). The differences are listed below; each
one follows from what this instance is.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/sonarr-anime.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `sonarr-anime`, and it contains
the chart name, so every object is named `sonarr-anime` — no `sonarr-anime-sonarr-anime` suffix.

```bash
# what the cluster will render, exactly
helm template sonarr-anime cluster/helm/media-stack/sonarr-anime --namespace media-stack
```

It renders one Deployment, one Service (ClusterIP, 8989/TCP), one ServiceAccount, one
PersistentVolumeClaim (`sonarr-anime-config`) and one IngressRoute (`sonarr-anime`).

## Why this chart differs from `sonarr`

| sonarr | Here | Why |
|---|---|---|
| `sonarr-config` | **`sonarr-anime-config`** | Derived from the release name, so the two instances never share a database |
| `sonarr.kub.local` | **`sonarr-anime.kub.local`** | One host per instance |
| request `256Mi`, limit `768Mi` | **request `384Mi`, limit `1Gi`** | This library is larger: 302Mi average, 380Mi peak over 30 days |
| Secret `sonarr` | **Secret `sonarr-anime`** | Separate API key, separate exporter identity |

The media claims are shared with `sonarr`: both instances import from
`pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` and file into `pvc-plex-media`.

## Documented exceptions to the chart contract

1. **Probes share one endpoint.** `/ping` is Sonarr's unauthenticated health endpoint. Readiness uses
   the same path as liveness because the only dependency is the SQLite database on `/config`, and a
   database that cannot be opened stops the process from starting — there is no dependency that can
   fail while the process stays up, so a second endpoint would only invent a way to restart a
   healthy pod. The API's `/health` endpoint needs the API key, which a probe cannot read.
2. **No `runAsNonRoot`, no `runAsUser`, no `fsGroup`.** The LinuxServer s6 init must start as root to
   prepare `/config`, then drops to `PUID`/`PGID` 1100. `fsGroup` would do nothing: `local-path` is
   hostPath-backed and the kubelet does not apply group ownership to it.
3. **The config claim is 128Mi and cannot grow.** `local-path` does not support volume expansion, and
   hostPath does not enforce the number, so 128Mi is a label rather than a quota. Replacing the claim
   means rescanning the library.

## Secrets

The chart expects an existing Secret `sonarr-anime` in `media-stack`:

| Key | Used by | Note |
|---|---|---|
| `secret` | Exporter sidecar, `APIKEY` | Sonarr → Settings → General → API Key. Without it the exporter target stays down |

Restore it from the key in `~/.config/secrets/homelab.env` (`SONARR_ANIME_API_KEY`) without printing
the value:

```bash
source ~/.config/secrets/homelab.env
kubectl -n media-stack patch secret sonarr-anime --type merge \
  -p "{\"data\":{\"secret\":\"$(printf %s "$SONARR_ANIME_API_KEY" | base64 -w0)\"}}"
```

## Metrics

The `exportarr` sidecar listens on 9707 and is scraped by pod annotation
(`prometheus.io/scrape`, `prometheus.io/port`, `prometheus.io/path`), all rendered from the
`exportarr` block. The target is built from the release name and the Service port, so it cannot
drift from the port Sonarr listens on. The metrics port is deliberately not on the Service:
Prometheus scrapes the pod IP.

## Upgrading and rolling back

The chart pins `4.0.20.3014-ls4`; the previous release was the series tag `4.0.19`. Sonarr migrates
its database forward on first start and does not migrate it back, so rollback means restoring the
`/config` copy taken before the sync, then pinning the old image. Take that copy while the old pod is
stopped:

```bash
# resolve sonarr-anime-config to its hostPath on the streaming node first
sudo cp -a /mnt/ssd0/<pv-name>_media-stack_sonarr-anime-config /mnt/ssd0/sonarr-anime-config-backup
```

After the sync, confirm the route was adopted in place rather than duplicated:

```bash
kubectl -n media-stack get ingressroute sonarr-anime
# expect exactly one, named sonarr-anime
```
