# sonarr

Sonarr is the PVR for TV series. It watches indexers, grabs releases through qBittorrent and files
them into the library tree Plex reads. It is internal-only: `sonarr.kub.local` on the `web` entry
point, no TLS, no authentication in front of it.

The chart is built from the reference chart `../prowlarr` and the contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md). The differences are listed below; each
one follows from what Sonarr is.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/sonarr.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `sonarr`, and it contains the
chart name, so every object is named `sonarr` — no `sonarr-sonarr` suffix.

```bash
# what the cluster will render, exactly
helm template sonarr cluster/helm/media-stack/sonarr --namespace media-stack
```

It renders one Deployment, one Service (ClusterIP, 8989/TCP), one ServiceAccount, one
PersistentVolumeClaim (`sonarr-config`) and one IngressRoute (`sonarr`).

## Why this chart differs from the reference

| Reference (prowlarr) | Here | Why |
|---|---|---|
| One config volume | Config claim **plus two shared media claims** | `/downloads` and `/movies` come from NFS provisioner pools shared with the other *arr apps, qBittorrent and Plex |
| Exporter targets the Service port | Same, but the target is **derived from the release name and `service.port`** | The old chart typed 7878 into the target and 8989 into the Service; the target never came up |
| `warn` on two app-specific variables | `SONARR__LOG__LEVEL` and `SONARR__LOG__CONSOLELEVEL` | File and stdout levels are separate knobs in Sonarr; Fluent Bit reads stdout |

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

The chart expects an existing Secret `sonarr` in `media-stack`:

| Key | Used by | Note |
|---|---|---|
| `secret` | Exporter sidecar, `APIKEY` | Sonarr → Settings → General → API Key. Without it the exporter target stays down |

Restore it from the key in `~/.config/secrets/homelab.env` (`SONARR_API_KEY`) without printing the
value:

```bash
source ~/.config/secrets/homelab.env
kubectl -n media-stack patch secret sonarr --type merge \
  -p "{\"data\":{\"secret\":\"$(printf %s "$SONARR_API_KEY" | base64 -w0)\"}}"
```

## Metrics

The `exportarr` sidecar listens on 9707 and is scraped by pod annotation
(`prometheus.io/scrape`, `prometheus.io/port`, `prometheus.io/path`), all rendered from the
`exportarr` block. The metrics port is deliberately not on the Service: Prometheus scrapes the pod
IP. Disabling `exportarr.enabled` removes the sidecar and the annotations together.

If the target answers HTTP 500, exportarr is passing Sonarr's 401 through: the `secret` key in the
`sonarr` Secret is stale, not the chart. Replace it with the API key from Sonarr's settings; a
missing or wrong key is the one failure this sidecar cannot report any other way.

## Upgrading and rolling back

The chart pins `4.0.20.3014-ls4`; the previous release was the series tag `4.0.19`. Sonarr migrates
its database forward on first start and does not migrate it back, so rollback means restoring the
`/config` copy taken before the sync, then pinning the old image. Take that copy while the old pod is
stopped:

```bash
# resolve sonarr-config to its hostPath on the streaming node first
sudo cp -a /mnt/ssd0/<pv-name>_media-stack_sonarr-config /mnt/ssd0/sonarr-config-backup
```

After the sync, confirm the route was adopted in place rather than duplicated:

```bash
kubectl -n media-stack get ingressroute sonarr
# expect exactly one, named sonarr
```
