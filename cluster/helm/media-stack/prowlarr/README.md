# prowlarr

Prowlarr manages indexers and proxies search requests to Sonarr, Radarr and the other *arr apps. It
follows the chart contract in [`docs/charts/README.md`](../../../../docs/charts/README.md); the shape
is copied from `media-stack/autobrr`, with the differences this app needs.

## Upstream

- Website: https://prowlarr.com/
- Docs: https://wiki.servarr.com/prowlarr
- Source: https://github.com/Prowlarr/Prowlarr
- Image: https://docs.linuxserver.io/images/docker-prowlarr/

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/prowlarr.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `prowlarr`, so every object lands
on the `prowlarr` / `prowlarr-config` names.

```bash
# what the cluster will render, exactly
helm template prowlarr cluster/helm/media-stack/prowlarr --namespace media-stack
```

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. Tag is always pinned. | `lscr.io/linuxserver/prowlarr`, `2.7.0.5723-ls168` |
| `strategy` | Deployment strategy. Must stay `Recreate` with a config volume. | `Recreate` |
| `service.port` | Port the Service exposes, and what the IngressRoute points at. | `9696` |
| `container.port` | Only set when the app listens on a different port than the Service. | `null` (falls back to `service.port`) |
| `ingressRoute.*` | Traefik CRD routing: host, entry points, middlewares, TLS. | `prowlarr.kub.local`, `web`, no TLS |
| `storage.*` | The config claim: class, size, mount path, or reuse an existing claim. | `local-path`, `128Mi`, `/config` |
| `env` | Non-secret variables, rendered **inline** into the container. Changing one rolls the pod. | `TZ`, `PUID`, `PGID`, log level |
| `extraEnv` | Variables sourced from a Secret, as `{ name, valueFrom }`. | `[]` |
| `nodeSelector` | Node pin. Every workload is pinned. | `kubernetes.io/hostname: streaming` |
| `probes.{startup,liveness,readiness}` | HTTP probes on `/ping`. Startup gates the other two through a first start after an upgrade. | enabled; startup 5s × 30, liveness 20s delay, readiness 10s delay |
| `securityContext` | Container hardening that does not break the LinuxServer init. | no privilege escalation |
| `resources` | Scheduler reservation and memory limit, sized from Prometheus. No CPU limit. | `10m`/`256Mi` request, `1Gi` memory limit |
| `exportarr.*` | The metrics sidecar: image, port, API-key Secret reference and resources. See **Metrics** for rotation. | enabled on port 9707 |

## LinuxServer image and the UID trap

This is a LinuxServer image: it starts as **root**, prepares `/config`, then drops to `PUID`/`PGID`.
That is why the values set `PUID`/`PGID` to `1100` and **do not** set `runAsNonRoot` or `runAsUser`.
Running the container directly as 1100 prevents the init from doing its job and the app never starts.

The consequence is that this chart cannot take the non-root hardening `autobrr` uses. It does set
`allowPrivilegeEscalation: false` and the `RuntimeDefault` seccomp profile, which the init tolerates.
Dropping all capabilities is also not possible here: the init needs to change ownership and switch
user, and without that it cannot hand the app to 1100. Say so in review rather than adding a setting
that looks stricter and stops the pod.

`local-path` is hostPath-backed, so the kubelet does **not** apply `fsGroup` ownership — which is why
`fsGroup` is absent rather than set to something plausible. Prowlarr's config directory is already
owned by 1100 because the app has been writing it that way. A freshly provisioned claim starts
root-owned, so a claim recreated from scratch needs a `chown -R 1100:1100` on its node before the pod
can write; the `autobrr` README describes the procedure.

## Probes

| Probe | Path | Behaviour |
|---|---|---|
| startup | `/ping` | Runs before the other two; 5s × 30 = 150s budget |
| liveness | `/ping` | `{"status":"OK"}` once the web stack is serving |
| readiness | `/ping` | Same endpoint on purpose (see below) |

`/ping` is Prowlarr's own unauthenticated health endpoint. The API also has `/api/v1/health`, which
would be a stronger readiness check, but it requires the API key and a Kubernetes probe cannot read a
Secret. Putting the key in a literal header would put it in the Deployment manifest, so the chart
does not.

Liveness and readiness share the endpoint, as in `flaresolverr`: Prowlarr's only dependency is its
SQLite database on the config volume, and a database that cannot be opened stops the process from
starting instead of leaving a running pod that cannot serve. There is therefore no dependency whose
failure readiness could report separately.

## Logs

The app logs at `warn`, not at the default `info`. Fluent Bit tails every container on every node and
ships every line to Loki without a filter, so the app's own level is the only place the volume can be
cut. At `info`, Prowlarr writes a line per HTTP request, indexer sync and search.

Both knobs are set, because they address different sinks:

| Variable | Value | Sink |
|---|---|---|
| `PROWLARR__LOG__LEVEL` | `warn` | the log file on the config volume |
| `PROWLARR__LOG__CONSOLELEVEL` | `warn` | stdout, which Fluent Bit ships to Loki |

Raise both while debugging, then put them back. The operational record is the indexer, history and
health views in the UI, not the log stream.

## Metrics

The chart runs `exportarr` as a sidecar (a second container in the same pod). The `exportarr` values
block owns the listener, the `prometheus.io/*` annotations and the API-key reference together, so
disabling it removes the sidecar and the annotations in one edit and the announced port cannot drift
from the listener. The metrics port stays off the Service: Prometheus scrapes the pod IP through the
`kubernetes-pods` job.

The sidecar is pinned to `v2.3.0`, the newest published exportarr release (August 2025). A v3 rewrite
exists on `main` with no tag and no image; do not move to it. `APIKEY` is exportarr v1's spelling of
`API_KEY`, and v2.3 still accepts it, which is why this chart uses that name.

The key comes from the `prowlarr` Secret (`secret` key); the app's own `config.xml` is not mounted
into the sidecar. A key that no longer matches the app makes every collector answer HTTP 401,
`/metrics` answer 500 and the Prometheus target go down — while the pod stays `Ready`, because no
probe covers the exporter. The query that catches it:

```
up{job="kubernetes-pods", pod=~"prowlarr.*"}
```

`PROWLARR_API_KEY` in `~/.config/secrets/homelab.env` holds the key Prowlarr is running with (see
[`docs/observability-conventions.md`](../../../../docs/observability-conventions.md)). To rotate the
Secret, from the operator's shell:

```bash
. ~/.config/secrets/homelab.env
kubectl -n media-stack patch secret prowlarr --type merge \
  -p "$(printf '{"data":{"secret":"%s"}}' "$(printf %s "$PROWLARR_API_KEY" | base64 -w0)")"
kubectl -n media-stack delete pod -l app.kubernetes.io/name=prowlarr
```

`patch` rather than `create secret --dry-run | kubectl apply`: the latter replaces the whole `data`
map and would drop any other key in that Secret. The pod has to be replaced afterwards, because the
sidecar reads the variable when the container starts. The value must be 20–32 alphanumeric
characters; exportarr refuses to start otherwise, so a stray newline is a crash-loop, not a 401.

Wiring verified working on 2026-11-12 after rotating the key: target `up`, `/metrics` HTTP 200, 21
indexers reported.

## Ingress

Always a Traefik `IngressRoute`, never `networking.k8s.io/v1` Ingress. The API group is a value
(`ingressRoute.apiVersion`) so the Traefik v3 migration becomes a values edit rather than a template
edit. It must stay `traefik.containo.us/v1alpha1` while the cluster runs Traefik 2.x.

`kub.local` hosts use the `web` entry point and no TLS; they resolve through the Pi-hole wildcard
record. Hosts under `kub.home.ashudev.com` use `websecure` with `certResolver: myresolver`.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment, the inline env and the exportarr sidecar |
| `templates/service.yaml` | the ClusterIP Service |
| `templates/serviceaccount.yaml` | the ServiceAccount |
| `templates/persistentvolumeclaim.yaml` | the config claim, only when `storage.enabled` and no `existingClaim` |
| `templates/ingressroute.yaml` | the Traefik IngressRoute |
| `templates/NOTES.txt` | the install notes |

There is deliberately no ConfigMap: Prowlarr takes its configuration from environment variables and
its own SQLite database on the claim.

## Uninstalling

The config claim is created by this chart, so deleting the release deletes the claim. Take a copy
first if it holds anything you want.
