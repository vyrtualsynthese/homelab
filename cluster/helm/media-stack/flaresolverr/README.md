# flaresolverr

FlareSolverr answers a request by opening the target page in a headless Chromium, waiting out a
Cloudflare or DDoS-GUARD challenge, and returning the HTML and cookies to the caller. It is an
internal API, not a web UI: indexer clients such as Prowlarr (which indexers are configured to
search) call it when a site blocks plain HTTP clients.

It is built from the reference chart
[`../autobrr`](../autobrr) and the contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md), but it is not a copy of it. The
differences are all consequences of what this app is, and they are listed below.

## Upstream

- Source and docs: https://github.com/FlareSolverr/FlareSolverr
- Image: `ghcr.io/flaresolverr/flaresolverr`

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/flaresolverr.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `flaresolverr`, and it already
contains the chart name, so every object is named `flaresolverr` — no `flaresolverr-flaresolverr`
suffix.

```bash
# what the cluster will render, exactly
helm template flaresolverr cluster/helm/media-stack/flaresolverr --namespace media-stack
```

It renders three objects: the Deployment, a ClusterIP Service on port 8191, and a ServiceAccount.

## Why this chart differs from the reference

| Reference (autobrr) | Here | Why |
|---|---|---|
| `IngressRoute` template, enabled | **No IngressRoute at all** | Upstream says a reachable instance can be abused. Nothing needs to reach it from a browser: Prowlarr calls `http://flaresolverr.media-stack.svc.cluster.local:8191` inside the cluster |
| `storage` block and PVC template | **No claim** | The app keeps no state. Sessions live in memory and the image only writes a log file if `LOG_FILE` is set. `/config` exists in the image but nothing uses it |
| `strategy: Recreate` | **`RollingUpdate`** | `Recreate` exists for apps holding a `ReadWriteOnce` claim. There is no claim here, so the replacement pod can be ready before the old one goes away |
| Lease-free ServiceAccount token | `automount: false` | The app never calls the Kubernetes API |
| Probes on `/` | Probes on **`/health`** | `/health` is the app's own health endpoint and, unlike `/`, it does not write a log line. The old chart probed `/` every 10s, which was 8,640 log lines a day |
| No metrics | **Prometheus exporter wired in** | The image ships one; see below |

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. Tag is always pinned. | `ghcr.io/flaresolverr/flaresolverr`, `v3.5.2` |
| `strategy` | Deployment strategy. `RollingUpdate`, because there is no claim to fight over. | `RollingUpdate` |
| `service.port` | Port the Service exposes. | `8191` |
| `container.port` | Only set when the app listens on a different port than the Service. | `null` (falls back to `service.port`) |
| `metrics.*` | Prometheus exporter: on/off, port, path. Also renders the `prometheus.io/*` annotations and the `PROMETHEUS_*` variables, so all four cannot disagree. | enabled, `8192`, `/metrics` |
| `env` | Non-secret variables, rendered **inline** into the container. Changing one rolls the pod. | `TZ`, `LOG_LEVEL` |
| `extraEnv` | Variables sourced from a Secret, as `{ name, valueFrom }`. | `[]` |
| `nodeSelector` | Node pin. Every workload is pinned. | `kubernetes.io/hostname: streaming` |
| `resources` | Memory request and limit. | `256Mi` request, `1Gi` limit |
| `probes.{liveness,readiness}` | HTTP probes on `/health`. | enabled |
| `serviceAccount.{create,automount}` | Dedicated account, no mounted token. | `true`, `false` |

## Prometheus

FlareSolverr exports its own metrics. They are off by default upstream and enabled here:

- `flaresolverr_request_total{domain, result}` — challenges per domain, with `result` being `solved`,
  `not_detected` or `error`.
- `flaresolverr_request_duration_seconds{domain}` — a histogram of how long each challenge took.

`metrics.enabled: true` does three things at once: it sets `PROMETHEUS_ENABLED=true` and
`PROMETHEUS_PORT`, it opens the named container port `metrics`, and it writes the pod annotations that
the cluster's Prometheus uses for pod discovery (`kubernetes-pods` job):

```yaml
prometheus.io/scrape: "true"
prometheus.io/scheme: http
prometheus.io/port: "8192"
prometheus.io/path: "/metrics"
```

The metrics port is deliberately **not** on the Service. Prometheus scrapes the pod IP directly, and
adding a second Service port would only widen the app's surface for nothing.

To check it from your machine once the pod runs:

```bash
kubectl -n media-stack port-forward deploy/flaresolverr 8192:8192
curl -s localhost:8192/metrics | grep flaresolverr_
```

## Probes

| Probe | Path | Behaviour |
|---|---|---|
| liveness | `/health` | 200 while the process serves; no log line per check |
| readiness | `/health` | Same endpoint, on purpose |

The two are the same endpoint because FlareSolverr has nothing else to check: no database, no
upstream service. A probe that exercised Chromium would mean launching a browser every 15 seconds, so
readiness proves only that the server answers. The browser itself is tested once at startup and the
process exits if that test fails, which is why the first liveness check waits 20 seconds.

## Deferred hardening

Not in this chart yet, on purpose, because it changes the identity the process runs as and has to be
verified against a live pod first:

1. **Pin the UID.** The image runs as user `flaresolverr` (UID 1000, created by `useradd` in the
   Dockerfile). Setting `runAsNonRoot: true` **without** `runAsUser` makes the pod fail to start,
   because the kubelet cannot verify a name — it needs the number.
2. **Drop capabilities** and set `allowPrivilegeEscalation: false`.
3. **seccomp.** `RuntimeDefault` is the usual free win, but Chromium is a browser: it is worth one
   sync with the pod logs open rather than assuming it.
4. **RAM-backed `/tmp`.** Every challenge creates and deletes a temporary Chromium profile under
   `/tmp`. Left alone that lands on the node's SSD (upstream recommends `tmpfs` for exactly this
   reason). The mount is cheap; the catch is that an `emptyDir` mounted by the kubelet is not
   necessarily writable by UID 1000 without an explicit `fsGroup`, so it belongs in the same
   verification pass as the UID change.

Chromium is started with `--no-sandbox --disable-dev-shm-usage`, so the usual `/dev/shm` problem does
not apply here and no extra volume is needed for it.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment, with env inline and the metric port |
| `templates/service.yaml` | the ClusterIP Service |
| `templates/serviceaccount.yaml` | the ServiceAccount |
| `templates/NOTES.txt` | the install notes, including the in-cluster URL |

There is deliberately no `persistentvolumeclaim.yaml` and no `ingressroute.yaml`. A chart for an app
that starts keeping state, or that genuinely needs to be reachable from a browser, adds them back —
the reference chart has both.

## Uninstalling

Nothing is created outside the namespace and nothing holds data, so deleting the release is enough.
