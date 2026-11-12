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
| `env` | Non-secret variables, rendered **inline** into the container. Changing one rolls the pod. | `TZ`, `LOG_LEVEL: warning` |
| `extraEnv` | Variables sourced from a Secret, as `{ name, valueFrom }`. | `[]` |
| `nodeSelector` | Node pin. Every workload is pinned. | `kubernetes.io/hostname: streaming` |
| `resources` | Memory request and limit. | `256Mi` request, `1Gi` limit |
| `probes.{liveness,readiness}` | HTTP probes on `/health`. | enabled |
| `serviceAccount.{create,automount}` | Dedicated account, no mounted token. | `true`, `false` |
| `securityContext`, `podSecurityContext` | Non-root UID 1000, capabilities dropped, no privilege escalation, seccomp `RuntimeDefault`, `fsGroup` for the `/tmp` volume. See **Hardening**. | pinned |
| `volumes`, `volumeMounts` | The RAM-backed `/tmp` Chromium writes its profile to. | 256Mi `emptyDir` on memory |

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

There is no startup probe, unlike `autobrr`: FlareSolverr starts serving about two seconds after the
container starts, so there is no slow phase for one to cover.

## Logging

`LOG_LEVEL: warning`. Fluent Bit tails every container on the node and ships the lines to Loki
unfiltered, so the level in the app is the only place to cut the volume. At `info` FlareSolverr
writes one line per challenge request and one per response, plus the startup banner — a few thousand
lines a day for an app nobody watches, which buries the lines that matter when something breaks.

What still reaches Loki at `warning`:

- a failed browser test at startup, and Chromium launch errors (`logging.error`);
- uncaught exceptions while handling a challenge, with the message;
- warnings about request parameters removed in v2 (`headers`, `userAgent`, `returnRawHtml`, ...).

What disappears: the startup banner, the per-request lines, and the challenge result text. The
result is returned to the caller anyway, and the exported counters keep the outcome visible in
Prometheus — `flaresolverr_request_total{domain, result}` with `result` set to `solved`,
`not_detected` or `error`.

For a single troubleshooting session, raise it with `--set env.LOG_LEVEL=debug`; the value is inline
in the Deployment, so changing it rolls the pod.

## Hardening

| Control | Value | Why it is safe here |
|---|---|---|
| `runAsNonRoot` + `runAsUser` / `runAsGroup` | `1000` / `1000` | The image already runs as `flaresolverr`, uid 1000 / gid 1000 — read from the image's own `/etc/passwd`. The number is required as well as the flag: the image's `USER` is a name, and the kubelet refuses to start a container whose non-root status it cannot verify |
| `capabilities.drop` | `[ALL]` | Nothing in the app needs a capability; Chromium is launched with `--no-sandbox` |
| `allowPrivilegeEscalation` | `false` | No setuid path is used |
| `seccompProfile` | `RuntimeDefault` | The runtime's syscall filter. Chromium does not need the syscalls its own sandbox would (it is started unsandboxed) |
| `/tmp` | `emptyDir` on memory, 256Mi | Each challenge creates and deletes a temporary Chromium profile there; unmounted it lands on the node's SSD. Upstream recommends `tmpfs` for exactly this |
| `fsGroup` | `1000` | Makes that `emptyDir` writable for uid 1000. Normally `fsGroup` is a trap in this repo because `local-path` is hostPath-backed and ignores it — this pod has no claim, so it does what the README says it does |

The one thing not done is `readOnlyRootFilesystem`: the app writes a patched `chromedriver` into its
working directory at startup, so a read-only root would need a second writable volume for `/app` and
another for Chromium's config directory. The gain over `drop: ALL` and a non-root UID is small, so it
is left out rather than half-done.

Chromium is started with `--no-sandbox --disable-dev-shm-usage`, so the usual `/dev/shm` problem does
not apply here and no extra volume is needed for it. If a future image drops `--no-sandbox`, both the
seccomp profile and the `/dev/shm` size become live questions again.

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
