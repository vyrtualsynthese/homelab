# vikunja

Vikunja is the lab's task board: the one place tasks are written down and their state lives. The
agents read and write it, and so does a browser. This chart is a wrapper over the upstream `vikunja`
chart 2.6.0 published as an OCI artifact (`oci://ghcr.io/go-vikunja/helm-chart`), pinned to app
version `2.8.0`.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/vikunja.yaml`, using the
chart in this directory and `values.yaml` next to it. The release name is the Application name,
`vikunja`, in namespace `vikunja`.

```bash
helm dependency build cluster/helm/vikunja
helm template vikunja cluster/helm/vikunja -n vikunja
```

`charts/` and `Chart.lock` are **not committed**. ArgoCD runs `helm dependency build` at sync time,
which is why `.gitignore` lists both paths. A local render needs the same command once.

The chart renders six objects: ConfigMap `vikunja-config`, PVC `vikunja-data` (5Gi), Service
`vikunja` on 3456, Deployment `vikunja`, IngressRoute `vikunja`, IngressRoute `vikunja-redirect`.

## Where state lives

Task state lives in **PostgreSQL**, in database `vikunja` on `postgres.databases.svc.cluster.local`;
the password comes from the Secret `vikunja-db`, key `VIKUNJA_DATABASE_PASSWORD`. Nothing durable is
in the claim.

The claim `vikunja-data` (5Gi, `local-path`, `ReadWriteOnce`) holds only the files a user attaches
to a task: uploads under `/app/vikunja/files`. It is `local-path`, which is backed by a directory on
one node, so the pod is pinned to `nuc0`, the node that directory is on. Losing the claim loses
attachments; it does not lose a single task. That is the point of the split, and the reason
`persistence.database` is off — a SQLite file inside the claim would put the state back on a node's
disk, where the 2026 Longhorn failure already proved it does not belong.

Restoring is therefore two operations: the nightly PostgreSQL dump brings the database back, and a
copy of `/app/vikunja/files` brings the attachments back. **Neither leaves the house today.** The
dump CronJob (`postgres-dump` in namespace `databases`, 03:30, 14-day retention) writes to the
`postgres-dumps` claim, which is `local-path` on `nas`, so it dies with that node or with the cluster,
and the offsite copy is W4, which is unbuilt — see `AGENTS.md` §7.3. Until W4 lands there is no
recovery path for the board beyond what the live cluster itself holds. This README does not promise
one.

## Why the upstream ingress is disabled

The upstream chart renders a `networking.k8s.io/v1` Ingress. This cluster routes only through
Traefik CRDs (`traefik.containo.us/v1alpha1` IngressRoute), and an Ingress object here would be
inert. So `vikunja.ingress.main.enabled` is `false` and the route lives in
`templates/ingressroute.yaml`, outside the `vikunja:` values block.

## Routing

One host, `vikunja.kub.local`, on `websecure`, with **TLS from the cluster's internal certificate**:
the route asks for TLSOption `internal-tls` in `kube-system` and sets neither `secretName` nor
`certResolver`, so Traefik serves the default `TLSStore` certificate. A second IngressRoute on `web`
sends plain HTTP to the same host, through `redirect-to-https`, so `http://` always lands on `https://`.

The two paths carry different gates:

| Path | Middlewares | Why |
|---|---|---|
| `/` (everything else) | `kube-system-lan-only`, `kube-system-authentik-forwardauth` | A browser is a human: the LAN check first, then the Authentik single sign-on gate. |
| `/api` (`priority: 100`) | `kube-system-lan-only` | An API client sends a token, not a browser session, and cannot complete a sign-in redirect. |

`lan-only` stays on **both** paths. The API is not public: it is reachable from the LAN only, with
Vikunja's own API token doing the authorising. Dropping `lan-only` from `/api` would expose the whole
board to the internet through the home router's port forward; the higher `priority` is what makes
Traefik pick the API route over the catch-all one.

## Metrics

`metrics.enabled: true` in `config.yml` turns on Prometheus output at `/api/v1/metrics` on port
3456. The pod carries `prometheus.io/scrape: "true"`, `prometheus.io/port: "3456"` and
`prometheus.io/path: /api/v1/metrics`, which is how Prometheus finds every app in this repo.
Prometheus scrapes the pod **directly, pod to pod**; the request never goes through Traefik, so no
middleware applies to it at all — that is also why the endpoint needs no credentials.

## Logs

`log.level: INFO`, text format to stdout, with the database and event log streams off. Fluent Bit
ships container stdout to Loki.

## Version floor

The **MCP endpoint** (the Model Context Protocol server agents call) requires Vikunja app **2.7.0 or
later**. `image.tag` is `2.8.0` and must never be pinned below 2.7.0; Renovate may raise it, never
lower it.

## Values layout

Every override sits under `vikunja.vikunja`, doubly nested. The outer key is the ordinary
parent→subchart scope. The inner one is required because the upstream template copies its own
`.Values.vikunja` block over the top-level values with `mergeOverwrite`, so a value written only at
the outer level is silently reset to the chart default. `ingressRoute:` is our own template and stays
outside both.

The upstream chart ships its own bjw-s `common` subchart (library of shared templates, version
1.5.1) inside its artifact, so nothing extra is needed here. The schema is bjw-s 1.5.1: `controller:`
(singular, with `strategy` under it), plus `nodeSelector`, `resources`, `securityContext` and
`podAnnotations` at the top level of the inner block — not the newer `controllers.main.*` shape.
