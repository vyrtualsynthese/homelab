# ashuvidz-website

`ashuvidz.com`, the public site: nginx serving static files that are pulled from the site's own git
repository at runtime. The chart is a wrapper (pattern D) around the Bitnami `nginx` chart, so the
Deployment, Service, ServiceAccount, NetworkPolicy and PodDisruptionBudget come from upstream and
this directory owns the values plus the two Traefik routes.

## Upstream

- Chart: https://github.com/bitnami/charts/tree/main/bitnami/nginx, pinned to `23.0.6` in
  `Chart.yaml`
- Image: `docker.io/bitnami/nginx:1.28.0-debian-12-r0`
- Content: https://github.com/vyrtualsynthese/ashuvidz-website, branch `main`

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/ashuvidz.yaml` into the
`ashuvidz` namespace, release name `ashuvidz`, so every object is named `ashuvidz-nginx` after the
wrapped chart's fullname.

`charts/` and `Chart.lock` are gitignored: ArgoCD runs `helm dependency build` at sync time, so a
local render needs the same step first.

```bash
helm dependency build cluster/helm/ashuvidz-website
helm template ashuvidz cluster/helm/ashuvidz-website --namespace ashuvidz
```

| File | Renders |
|---|---|
| `Chart.yaml` | the chart, `appVersion` matching the nginx image tag, the upstream dependency |
| `values.yaml` | every knob for the wrapped chart: image, git source, resources, node pin, probes |
| `templates/ingress.yaml` | the `websecure` IngressRoute on `ashuvidz.com` |
| `templates/ingress-redirect.yaml` | the `web` IngressRoute that redirects to it |

Everything else — Deployment, Service, ServiceAccount, NetworkPolicy, PodDisruptionBudget — is
rendered by the wrapped chart. `values.yaml` is a full copy of that chart's defaults at `23.0.6`
plus the overrides below, deliberately: a future release changing a default cannot change what runs
here without the edit showing up in review.

## How a content change reaches the cluster

There is no build step and no image: the content is the repository.

1. Push to `main` on `vyrtualsynthese/ashuvidz-website`.
2. Within about a minute the `git-repo-syncer` sidecar runs `git pull` in `/app` and the new files
   are served. No commit here, no sync, no restart.

`cloneStaticSiteFromGit` is what does it: an init container clones the repository into an `emptyDir`
mounted at `/app`, and the sidecar repeats the pull every 60 seconds (`interval`). So:

- The pod starts serving whatever `main` holds at that moment; a restart always re-clones.
- A failed pull leaves the last good copy in place and the site keeps answering.
- `/app` is `emptyDir`, so nothing survives a restart. The content is never stored in the cluster,
  and the repository is the only copy.
- Changing the site's repository or branch, or the pull cadence, is a chart change: it lands in
  `values.yaml` and rolls the pod.

## Routing

`ashuvidz.com` is on the cluster's public list, so the route carries **no** `lan-only` middleware
and no authentik gate: it is reachable from the internet by design. TLS is
`certResolver: myresolver` (Let's Encrypt); the only middleware is `kube-system/security`, which
sets the headers (`strict-transport-security`, `x-frame-options` and the rest).

Both routes are unchanged from before this chart was aligned to the contract: `websecure` for the
site, `web` for the redirect. `hack/check-exposure.sh --only ashuvidz` passes because a public host
is allowed to be ungated.

## Probes, resources, placement

| Setting | Value | Why |
|---|---|---|
| liveness | TCP connect to the `http` port (8080), 30s delay | It answers whether nginx is alive, not whether the content is right |
| readiness | `GET /` | `/` is `index.html`, so a failed clone or a missing tree takes the pod out of the Service instead of restarting it |
| nginx resources | requests 10m CPU / 32Mi, limit 128Mi | Measured 2026-11-18 over the 5 days Prometheus retains: p95 23.8Mi, peak 23.9Mi. The limit is above 1.5× that because the same block covers the `git clone` init container, which runs too briefly to be scraped |
| git-syncer resources | requests 10m CPU / 32Mi, limit 64Mi | Same window, `container=git-repo-syncer`: p95 21.2Mi, peak 25Mi |
| ephemeral-storage | requests 50Mi, limit 2Gi, both containers | `/app` holds the clone on disk; without a limit one bad clone fills the node |
| node | `nas` → **`nuc0`**, via `nodeSelector` | The site keeps no state, and `nuc0` is where the other small front ends live (`authentik`, `filebrowser`, `vikunja`) |

The two probes are separate on purpose but share nothing with a dependency: a static site has no
backend to interrogate, so readiness checks that the files are being served and liveness checks
that the process is up. A crash loop from a bad content push is avoided this way — a broken tree
shows as `0/1 Ready`, not as restarts.

`helm template ... | kubeconform -strict` reports 7 objects, 5 valid and 2 skipped (the Traefik
CRDs), 0 invalid.

## Known debt

- **The exporter is off.** `metrics.enabled: false`. The image ships `nginx-exporter` as a sidecar
  and the contract wants it scraped, but exporters are turned on one chart at a time in this repo
  (`flaresolverr` was the first) and this pass did not take that on. The related
  `metrics.serviceMonitor` and `metrics.prometheusRule` flags are set `false` as well: there is no
  Prometheus Operator here, so both would render CRDs nobody reconciles.
- **Per-request access lines still reach Loki.** nginx writes its access log at `notice`, and the
  wrapped chart exposes no value for the log level. Nothing was invented to hide it; if the volume
  matters, the lever is a `serverBlock` entry, which also removes the only record of who got what.
- **The chart is a Bitnami wrapper.** The repository is exiting Bitnami charts (`prometheus`,
  `grafana`, `postgres` are the named cases). This one keeps the wrapper because the static-site
  clone-and-pull logic is exactly what the site needs and hand-writing it would be more code than
  the chart.
- **No CI on the content repository.** A pushed typo goes live within a minute; there is no
  staging copy of the site.

## After a change lands on `develop`

1. ArgoCD syncs the `ashuvidz` Application (auto-sync is per Application, in the UI, not in git).
   The pod is replaced on `nuc0` — it moves off `nas` the first time — and the new pod re-clones the
   site before it becomes ready.
2. Nothing is lost: the content comes from the repository and `/app` was an `emptyDir` before.
3. The certificate is untouched by this change; TLS stays `myresolver` on `websecure`.
4. Check it: `curl -sSI https://ashuvidz.com/ | head -3` answers `200`, and
   `kubectl -n ashuvidz logs deploy/ashuvidz-nginx -c git-repo-syncer --tail=5` shows the pull.
