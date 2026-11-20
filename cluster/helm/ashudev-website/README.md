# ashudev-website

`ashudev.com`, the public site: nginx serving static files that are pulled from the site's own git
repository at runtime. Shaped from [`../ashuvidz-website`](../ashuvidz-website), which it should never
drift from without a reason written down — the chart is hand-written against the contract in
[`docs/charts/README.md`](../../../docs/charts/README.md).

## Upstream

- Image: `nginxinc/nginx-unprivileged:1.31.7-alpine` — the nginx team's non-root build, listening on
  8080, so no process in the pod is root.
- Content helper: `alpine/git`, pinned, used by the init container and the sidecar.
- Content: https://github.com/vyrtualsynthese/ashudev-website, branch **`gh-pages`**.

`gh-pages` is not a source branch: the site is built by webpack and published there, and GitHub Pages
served that branch until 2026-11-20. The cluster serves the same bytes and runs no build, so
publishing a new version of the site is still "push the build to `gh-pages`", and the sidecar picks
it up within a minute.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/ashudev.yaml` into the
`ashudev` namespace, release name `ashudev`. There are no chart dependencies:

```bash
helm template ashudev ./cluster/helm/ashudev-website --namespace ashudev
```

| File | Renders |
|---|---|
| `Chart.yaml` | the chart, `appVersion` matching the nginx image tag |
| `values.yaml` | image, content source, resources, node pin, probes, routes |
| `templates/deployment.yaml` | the Deployment: clone init container, nginx, git-sync sidecar |
| `templates/configmap.yaml` | `nginx.conf` — the served root, `/healthz`, the `.git` deny, gzip |
| `templates/service.yaml` | the ClusterIP Service on port 80 → the container's 8080 |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no API token mounted |
| `templates/ingressroute.yaml` | the `websecure` IngressRoute, the `web` redirect, and the `www` pair |
| `templates/NOTES.txt` | install notes |

## How a content change reaches the cluster

There is no build step and no image: the content is the branch.

1. Publish to `gh-pages` on `vyrtualsynthese/ashudev-website`, the way Pages was published.
2. Within about a minute the `git-sync` sidecar fetches `content.ref` and resets `/site` to it.
   No commit here, no sync, no restart.

The content lives in an `emptyDir` mounted at `/site`:

- The init container `clone-content` clones `content.repository` at `content.ref` before nginx
  starts, and fails the pod if `index.html` is not there — a blank site must not look healthy.
- The sidecar **fetches and resets** rather than `git pull --ff-only`, which is the one place this
  chart deliberately differs from `ashuvidz-website`. Pages rewrites `gh-pages` on every publish, so
  a fast-forward-only pull can fail forever after one rewrite: the site would keep serving the
  previous build with nothing red anywhere. A failed fetch is logged and the previous content stays.
- The reset is **not atomic**: `git reset --hard` rewrites `/site` file by file while nginx serves
  it, so a request landing in that window can read a new `index.html` beside a bundle that is not
  written yet. The window is the length of one reset of a small static tree, and a request that
  loses the race gets a 404 for one asset and a reload fixes it. Making it atomic would need a
  second directory and a symlink swap; that cost is not paid for a personal site. A failed fetch
  leaves the previous tree intact; a failed reset leaves a half-written one, and the sidecar says so
  in its log rather than claiming otherwise.
- Nothing survives a restart: the pod re-clones. The repository is the only copy.

## Paths

`/name` serves `name.html` when no such file exists, because that is what GitHub Pages did and what
the site's own links still assume (`/services` answered 200 there). `/index.html` is served as
`index.html` rather than redirected to `/`; Pages redirected it, and the difference is a duplicate
URL, not a broken one.

## Routing

`ashudev.com` is on the cluster's public list, so the route carries **no** `lan-only` middleware and
no authentik gate: it is reachable from the internet by design. TLS is `certResolver: myresolver`
(Let's Encrypt); the only middleware on the apex is `kube-system/security`, which sets the security
headers.

Three routers:

| Router | Entry point | What it does |
|---|---|---|
| `ashudev` | `websecure` | the site, TLS from `myresolver` |
| `ashudev-redirect` | `web` | 301 to HTTPS through `kube-system/redirect-https` |
| `ashudev-www` | `web`, `websecure` | 301 `www.ashudev.com/<path>` → `ashudev.com/<path>`, with its own `tls` |

The `www` router is not decoration. Traefik only asks the resolver for a certificate for a name some
router matches, so a `www` that resolves with no router answers with a TLS name mismatch — the exact
failure `www.ashuvidz.com` has today. Its `tls` block is what makes `https://www.ashudev.com` work at
all; the redirect is only what it does once the certificate exists.

## Probes, resources, placement

| Setting | Value | Why |
|---|---|---|
| liveness | `GET /healthz` | A `return 200` in the chart's `nginx.conf`: it answers "is nginx up", never "is the clone right" |
| readiness | `GET /` | `/` is `index.html` out of the clone, so a failed clone takes the pod out of the Service instead of restarting it |
| nginx resources | requests 10m CPU / 32Mi, limit 128Mi | Measured on the sibling chart over the 5 days Prometheus retains: p95 23.8Mi, peak 23.9Mi. The limit is above 1.5× that because the clone init container has no sample to size from |
| content resources | requests 10m CPU / 32Mi, limit 64Mi | The sidecar's stand-in measurement is the syncer it replaces: p95 21.2Mi, peak 25Mi |
| ephemeral-storage | requests 50Mi, limit 2Gi, all containers | `/site` is a clone on disk; without a limit one bad clone fills the node |
| node | `kubernetes.io/hostname: nuc0` | The site keeps no state; `nuc0` is where the other small front ends live |

## Hardening

- UID 101 everywhere (`runAsNonRoot`), the image's own user; `fsGroup: 101` on two `emptyDir`s, where
  the kubelet does apply it — unlike `local-path`, which is why there is no claim here.
- `allowPrivilegeEscalation: false`, all capabilities dropped, `seccompProfile: RuntimeDefault`,
  `readOnlyRootFilesystem: true` (nginx's temp paths are moved into the `/tmp` `emptyDir`).
- `/site` is mounted read-only into nginx. Dotfiles are denied in the config, so `/.git` is not
  downloadable.
- No ServiceAccount token is mounted, and no credential is involved at all: the repository is public.

## Known debt

- **The access log is on**, to stdout, so request lines reach Loki. On a public host the request
  record is the only evidence of what arrived; if the volume becomes a problem, the lever is the log
  format.
- **No metrics.** nginx exposes no Prometheus endpoint out of the box.
- **No staging copy.** A pushed typo is live within a minute.
- **No `404.html`.** The branch carries none, so nginx answers its own 404 — GitHub Pages answered
  its own as well, so nothing got worse and nothing got better.
