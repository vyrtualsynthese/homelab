# plex

Plex Media Server streams the lab's video and music libraries to clients on the LAN and, through
Traefik, to clients anywhere. It is the **only internet-facing app in the lab**, which is why this
chart differs from the internal ones and why its public host is treated as part of its identity.

The chart is built from the reference chart `../autobrr` and the contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md). The differences are listed below;
each one follows from what Plex is.

## Upstream

- Image: `plexinc/pms-docker` (the official image), pinned to `1.43.5.11079-b3b13e506`.
- Docs: <https://github.com/plexinc/pms-docker>

The previous chart used `ghcr.io/linuxserver/plex`. The official image is the maintainer's own, has
no package-channel selector (`VERSION`), and is the one Plex documents. The swap is not
drop-in: the environment variables and the container's startup differ (next section).

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/plex.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `plex`, and it contains the
chart name, so every object is named `plex` — no `plex-plex` suffix.

```bash
# what the cluster will render, exactly
helm template plex cluster/helm/media-stack/plex --namespace media-stack
```

It renders one Deployment, one Service (ClusterIP, 32400/TCP), one ServiceAccount, one
PersistentVolumeClaim, two IngressRoutes (secure + HTTP redirect), two Middlewares (redirect and
security headers) and one TLSOption.

## Why this chart differs from the reference

| Reference (autobrr) | Here | Why |
|---|---|---|
| `web` entry point, no TLS | **Secure route on `websecure` with ACME + TLSOption, plus a `web` → HTTPS redirect** | This is the only app exposed to the internet; Plex clients expect TLS |
| No auth middleware | **No auth middleware here either, deliberately** | Plex clients cannot complete a forward-auth flow. The server's own account model is the gate |
| One config claim | Config claim **plus two shared media claims** | `/media` and `/audio` come from NFS provisioner pools shared with sonarr, radarr, bazarr, lidarr and filebrowser |
| No GPU | `runtimeClassName: nvidia`, `NVIDIA_*` variables | NVENC hardware transcoding. The Intel `/dev/dri` path is deliberately absent |
| Exporter off | **Exporter sidecar on 9594** | The images ship no exporter; `cplieger/plex-exporter` is the maintained one |
| `runAsUser: 1100`, caps dropped | **Root entrypoint, caps `ALL` minus `CHOWN/SETUID/SETGID`** | The official image's s6 init must chown `/config` and drop to the `plex` user |
| Transcode absent | **`emptyDir` 10Gi at `/transcode`** | Transcoder scratch is ephemeral; the old 128Mi claim filled on the first 4K transcode |

## Environment variables, old image → official image

| LinuxServer | Official image | Note |
|---|---|---|
| `PUID` / `PGID` | `PLEX_UID` / `PLEX_GID` | Both `1100`, matching the existing `/config` ownership |
| `VERSION=docker` | — | LinuxServer package-channel selector; the tag now decides the version |
| `HOSTNAME=plex` | pod `hostname: plex` | `HOSTNAME` is a Docker flag, not an app variable |
| `TZ`, `PLEX_CLAIM`, `ALLOWED_NETWORKS` | unchanged | `PLEX_CLAIM` is first-run only and ignored on a claimed server |
| `ADVERTISE_IP=https://…:443` | `https://…:443/` | The official image expects a full URL with a trailing slash |
| `NVIDIA_VISIBLE_DEVICES`, `NVIDIA_DRIVER_CAPABILITIES` | unchanged | NVENC through the NVIDIA runtime |
| — | `PLEX_TOKEN` (sidecar only) | From Secret `plex/plex_token`; never given to the Plex container |

## Documented exceptions to the chart contract

1. **Probes share one endpoint.** Plex exposes `/identity` and nothing that separates "process up"
   from "library database usable". Readiness therefore uses `/identity`, like liveness; a guessed
   second endpoint would turn a slow scan into a restart loop.
2. **Capabilities are re-added.** `CHOWN`, `SETUID` and `SETGID` are required by the s6 entrypoint.
   Dropping `ALL` makes Plex fail to start while the pod still reports Running.
3. **No log-level variable.** Plex has no supported environment knob for its log level; the level
   lives in the server's preferences. Fluent Bit therefore receives Plex's default INFO lines. The
   playback and error records survive in Plex's own database and in the exporter's metrics.

## Secrets

The chart expects an existing Secret `plex` in `media-stack`:

| Key | Used by | Note |
|---|---|---|
| `plex_claim` | Plex container, `PLEX_CLAIM` | First-run only; ignored once claimed |
| `plex_token` | Exporter sidecar | Plex admin token; **required**, the sidecar does not start without it |

Add the token without dropping the claim key:

```bash
kubectl -n media-stack patch secret plex --type merge \
  -p "{\"data\":{\"plex_token\":\"$(printf %s "$PLEX_TOKEN" | base64 -w0)\"}}"
```

## The config claim is 128Mi and cannot grow

`local-path` does not support volume expansion, so raising `storage.size` on the existing
`plex-config` claim makes ArgoCD fail on an immutable field. Replacing the claim means re-claiming
the server and rescanning every library. HostPath does not enforce the number, so 128Mi is a label,
not a quota. Leave it.

## Upgrading and rolling back

The first deploy of this chart moves the image from 1.43.3.10828 to 1.43.5.11079. Plex upgrades its
library database on start, and that upgrade is one-way. Before merging:

```bash
# with the old pod stopped and auto-sync disabled in the ArgoCD UI
cp -a /mnt/ssd0/pvc-05380ca4-64ce-40c2-a838-5b801c8aad37_media-stack_plex-config \
      /mnt/ssd0/plex-config-backup-2026-11-12
```

If the migration fails: scale Plex down, restore that copy over the hostPath, set `image.tag` back
to `1.43.3.10828-00f62d37d`, and scale up.

## Checking a chart against the contract

```bash
helm lint . --namespace media-stack
helm template plex . --namespace media-stack | kubeconform -strict -summary

grep -rnP '\t' templates/ || echo "no tabs"
grep -rn 'latest' values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io' templates/ || echo "no core Ingress"
grep -rn 'prometheus.io/scrape' templates/ || echo "no scrape annotation"
```
