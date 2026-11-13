# bazarr-anime

The second Bazarr instance, for the anime library. Same image and shape as `bazarr`, with its own
config claim, host and API key. It is internal-only: `bazarr-anime.kub.local` on the `web` entry
point, no TLS, no authentication in front of it.

It is a separate chart rather than a shared one because the repo contract forbids a shared/library
chart: each instance keeps its own values and can diverge without leaking into the other.

The chart is built from the contract in [`docs/charts/README.md`](../../../../docs/charts/README.md)
and the shape of the `sonarr` chart. The differences are listed below; each one follows from what
Bazarr is.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/bazarr-anime.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `bazarr-anime`, and it contains
the chart name, so every object is named `bazarr-anime` — no `bazarr-anime-bazarr` suffix.

```bash
# what the cluster will render, exactly
helm template bazarr-anime cluster/helm/media-stack/bazarr-anime --namespace media-stack
```

It renders one Deployment, one Service (ClusterIP, 6767/TCP), one ServiceAccount, one
PersistentVolumeClaim (`bazarr-anime-config`) and one IngressRoute (`bazarr-anime`).

## Why this chart differs from the reference

| Reference (sonarr) | Here | Why |
|---|---|---|
| Two media mounts: downloads and media | **One** media claim, `pvc-plex-media` at `/media` | Bazarr only reads the library and writes subtitle files next to the videos; it never imports from the download directory |
| Exportarr sidecar enabled | Exportarr block **present but off** | Exportarr v2.3.0's Bazarr collector walks every episode; see the metrics exception below |
| Probes on `/ping` | Probes on `/` | Bazarr has no unauthenticated `/ping`; `/` is the only endpoint it answers without the API key |
| Two Sonarr log-level variables | No log-level variable | Bazarr's logger has only INFO and DEBUG and no variable selects either |

## Documented exceptions to the chart contract

1. **Probes share `/`.** It is the only unauthenticated endpoint. The API's status endpoint needs the
   API key, and a probe cannot read a Secret. Readiness uses the same path as liveness because
   Bazarr's dependencies — Sonarr, Radarr, the subtitle providers — can fail without stopping Bazarr's
   own web stack, and there is no endpoint the kubelet could use to tell the two states apart. A
   second path would only invent a way to restart a healthy pod.

2. **No warning log level.** Bazarr's logger has only INFO and DEBUG, selected by the `general.debug`
   flag in `/config/config/config.yaml`; there is no environment variable and no warning level. The
   chart therefore sets nothing rather than a variable that does nothing. The INFO volume is accepted
   debt: the UI's history and the exporter, once it lands, are the operational record. If the volume
   ever matters, set `general.debug` deliberately after reading the config, not by guessing a
   variable.

3. **The exporter is off until exportarr v3.** Exportarr v2.3.0 supports Bazarr, but its scrape walks
   every episode and takes tens of seconds; Prometheus scrapes every 60 s here, so overlapping
   scrapes would be skipped and Bazarr's CPU would go to the walk. The flag that removes the walk
   (`DISABLE_EPISODE_METRICS`) is on `main` and unreleased. The block stays in `values.yaml` with
   `enabled: false`, so **no `prometheus.io/*` annotation renders** and the instance is absent from
   the target list rather than present and down.

   To enable it when v3 ships:

   ```bash
   # paste the key from Bazarr -> Settings -> General -> API Key into homelab.env
   source ~/.config/secrets/homelab.env
   kubectl -n media-stack create secret generic bazarr-anime \
     --from-literal=secret="$BAZARR_ANIME_API_KEY"
   ```

   Then set `exportarr.enabled: true`, pin the v3 image tag, add `DISABLE_EPISODE_METRICS: "true"`,
   and change the sidecar's env from `APIKEY` to `API_KEY` — v3 renamed the variable. Restart the
   Deployment: the sidecar reads the key once, at start.

4. **The image starts as root.** The LinuxServer s6 init must prepare `/config` and then drops to
   `PUID`/`PGID` 1100. `runAsNonRoot`, `runAsUser` and `fsGroup` are therefore absent; `fsGroup`
   would do nothing anyway because `local-path` is hostPath-backed and the kubelet does not apply
   group ownership to it. `allowPrivilegeEscalation: false` and the runtime seccomp profile still
   apply.

## Media

`pvc-plex-media` is mounted read-write at `/media`. Bazarr writes `.srt` files next to the videos, so a
read-only mount would leave every download unwritten. The same claim holds the TV and movie trees;
Bazarr's folder settings select the subdirectories.

## Config claim

`bazarr-anime-config` is created by this chart on `local-path`, 128Mi. `local-path` cannot expand a
claim, and hostPath does not enforce the number, so 128Mi is a label rather than a quota. The claim
holds the SQLite database and `config.yaml`, including the API key; replacing it means reconfiguring
Bazarr from scratch.

## Upgrading and rolling back

Bazarr migrates its database forward on first start and does not migrate it back. Take a copy of
`/config` before an image bump, and roll back by restoring that copy and pinning the old tag.
