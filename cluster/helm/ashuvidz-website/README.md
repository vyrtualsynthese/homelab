# ashuvidz-website

`ashuvidz.com`, the public site: nginx serving static files that are pulled from the site's own git
repository at runtime. The chart is hand-written against the contract in
[`docs/charts/README.md`](../../../docs/charts/README.md) — it used to wrap the Bitnami `nginx`
chart, and no longer does: Bitnami stopped publishing the versioned tags that chart pins, and the
site was one reschedule away from `ImagePullBackOff`. See the history at the end.

## Upstream

- Image: `nginxinc/nginx-unprivileged:1.31.7-alpine` — the nginx team's non-root build, listening on
  8080, so no process in the pod is root.
- Content helper: `alpine/git`, pinned, used by the init container and the sidecar.
- Content: https://github.com/vyrtualsynthese/ashuvidz-website, branch `main`.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/ashuvidz.yaml` into the
`ashuvidz` namespace, release name `ashuvidz`. There are no chart dependencies, so `helm dependency
build` is no longer needed:

```bash
helm template ashuvidz cluster/helm/ashuvidz-website --namespace ashuvidz
```

| File | Renders |
|---|---|
| `Chart.yaml` | the chart, `appVersion` matching the nginx image tag |
| `values.yaml` | image, content source, resources, node pin, probes, routes |
| `templates/deployment.yaml` | the Deployment: clone init container, nginx, git-sync sidecar |
| `templates/configmap.yaml` | `nginx.conf` — the served root, `/healthz`, the `.git` deny, gzip |
| `templates/service.yaml` | the ClusterIP Service on port 80 → the container's 8080 |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no API token mounted |
| `templates/ingressroute.yaml` | the `websecure` IngressRoute and the `web` redirect |
| `templates/NOTES.txt` | install notes |

## How a content change reaches the cluster

There is no build step and no image: the content is the repository.

1. Push to `main` on `vyrtualsynthese/ashuvidz-website`.
2. Within about a minute the `git-sync` sidecar runs `git -C /site pull --ff-only` and the new files
   are served. No commit here, no sync, no restart.

The content lives in an `emptyDir` mounted at `/site`:

- The init container `clone-content` clones `content.repository` at `content.ref` before nginx
  starts, and fails the pod if `index.html` is not there — a blank site must not look healthy.
- The sidecar repeats the pull every `content.intervalSeconds` (60). A failed pull is logged and the
  previous content keeps being served.
- Nothing survives a restart: the pod re-clones. The repository is the only copy.
- `--ff-only` means an upstream force-push does not silently rewrite the served tree; it errors in
  the sidecar log until the pod is recreated.

## Routing

`ashuvidz.com` is on the cluster's public list, so the route carries **no** `lan-only` middleware and
no authentik gate: it is reachable from the internet by design. TLS is `certResolver: myresolver`
(Let's Encrypt); the only middleware is `kube-system/security`, which sets the security headers.

Both routes are unchanged from what the cluster already ran — same names (`ashuvidz`,
`ashuvidz-redirect`), same `websecure`/`web` pair, the `security` headers and the `redirect-https`
middleware on Traefik's no-op service. `hack/check-exposure.sh --only ashuvidz` passes because a
public host is allowed to be ungated.

The object names are the ones the cluster already holds, too: `nameOverride: nginx` makes
`app.fullname` build `ashuvidz-nginx`, which is the Service the two routes already point at. That is
deliberate — `spec.selector` on a Deployment is immutable, so renaming would mean a deleted and
recreated Deployment and a 503 for as long as the new pod takes to clone. The routes are the one
place the release name is used instead (`ingressRoute.name`), for the same reason: a renamed
IngressRoute leaves the host unrouted until the new object is applied.

## Probes, resources, placement

| Setting | Value | Why |
|---|---|---|
| liveness | `GET /healthz` | A `return 200` in the chart's `nginx.conf`: it answers "is nginx up", never "is the clone right" |
| readiness | `GET /` | `/` is `index.html` out of the clone, so a failed clone takes the pod out of the Service instead of restarting it |
| nginx resources | requests 10m CPU / 32Mi, limit 128Mi | Measured 2026-11-18 over the 5 days Prometheus retains: p95 23.8Mi, peak 23.9Mi. The limit is above 1.5× that because the clone init container has no sample to size from |
| content resources | requests 10m CPU / 32Mi, limit 64Mi | The sidecar's stand-in measurement is the Bitnami syncer it replaces: p95 21.2Mi, peak 25Mi |
| ephemeral-storage | requests 50Mi, limit 2Gi, all containers | `/site` is a clone on disk; without a limit one bad clone fills the node |
| node | `kubernetes.io/hostname: nuc0` | The site keeps no state; `nuc0` is where the other small front ends live |

Because the probe answers live in the chart's own config, `/healthz` exists even while the clone is
empty. That is the point: a broken content push shows as `0/1 Ready`, not as a restart loop.

## Hardening

- UID 101 everywhere (`runAsNonRoot`), the image's own user; `fsGroup: 101` on two `emptyDir`s, where
  the kubelet does apply it — unlike `local-path`, which is why there is no claim here.
- `allowPrivilegeEscalation: false`, all capabilities dropped, `seccompProfile: RuntimeDefault`,
  `readOnlyRootFilesystem: true` (nginx's temp paths are moved into the `/tmp` `emptyDir`).
- `/site` is mounted read-only into nginx. Dotfiles are denied in the config, so `/.git` is not
  downloadable.
- No ServiceAccount token is mounted: nothing here talks to the API.

## Known debt

- **The access log is on.** On a public host the request record is the only evidence of what
  arrived, so `access_log /dev/stdout` stays, and the lines reach Loki. The error log runs at
  `warn`. If the volume becomes a problem, the lever is the log format, not the level.
- **No metrics.** nginx exposes no Prometheus endpoint out of the box; adding one means a sidecar
  exporter, which is the metrics work the repo does one chart at a time. The old chart had one
  wired but disabled.
- **No staging copy.** A pushed typo is live within a minute.

## History

Until 2026-11-18 this chart wrapped `bitnamicharts/nginx`. Bitnami then moved every versioned tag to
the frozen `bitnamilegacy/*` namespace and publishes only `latest` under `bitnami/*`. The pinned
`bitnami/nginx:1.28.0-debian-12-r0` stopped resolving, and the failure only showed when a pod landed
on a node without the layer cached — which is what [PR
#1205](https://github.com/vyrtualsynthese/homelab/pull/1205) caused by pinning the pod to `nuc0`.
[#1206](https://github.com/vyrtualsynthese/homelab/pull/1206) repointed both images at
`bitnamilegacy` to restore service; this chart removes the dependency instead. `bitnamilegacy` is a
read-only archive with no security fixes, so it was never a home for a public web server.

## After a change lands on `develop`

1. ArgoCD syncs the `ashuvidz` Application (auto-sync is per Application, in the UI). The Deployment
   `ashuvidz-nginx` is patched in place, so the rollout is a normal rolling update: the new pod
   clones the site on `nuc0` before it is ready and the old pod keeps serving until it is. The
   NetworkPolicy and PodDisruptionBudget the Bitnami chart rendered are pruned — the policy allowed
   every source on the two HTTP ports, and the PDB had `maxUnavailable: 1` against one replica, so
   neither did anything.
2. No traffic gap: the Service, its port and both IngressRoutes keep their names.
3. Nothing is lost: the content comes from the repository and `/site` was an `emptyDir` before.
4. Check it: `curl -sSI https://ashuvidz.com/ | head -3` answers `200`;
   `kubectl -n ashuvidz logs deploy/ashuvidz -c git-sync --tail=3` shows the pull.
