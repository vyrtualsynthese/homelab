# FileBrowser pins its node — design

- **Date:** 2026-11-20
- **Status:** approved (operator chose the local chart on 2026-11-20)
- **Card:** #427 (`FileBrowser Quantum: pin the pod to a node`)
- **Supersedes:** the pattern D decision for `cluster/helm/filebrowser` in
  [`2026-11-19-filebrowser-quantum-design.md`](2026-11-19-filebrowser-quantum-design.md)

## 1. Intent

The repo rule is that a workload is pinned to a named node and its `local-path` claim lands on that
same node. FileBrowser Quantum breaks it: `Softwaredam/filebrowser-chart`, pinned at `2.0.2-stable`,
renders no `nodeSelector`, `affinity` or `tolerations` field, and the released chart and its `main`
branch still do not. The pod currently runs on `nuc0` by luck and would be rescheduled anywhere,
including the ARM64 `rbp2`, whose images may not build.

The task offered three fixes: an upstream PR, a fork, or a local chart. The operator chose the local
chart, because it is the only one that ends the dependency on someone else's chart.

## 2. Goals and non-goals

**Goals**

- The pod carries `nodeSelector: kubernetes.io/hostname: nuc0`, so its `local-path` config claim stays
  attachable.
- The chart is ours: `cluster/helm/filebrowser` renders its own Deployment, Service, ConfigMap,
  Secret, claims and ServiceAccount.
- Every live object keeps its name, so ArgoCD patches in place and the database claim is not
  orphaned.
- The chart meets [`docs/charts/README.md`](../../charts/README.md): pinned `appVersion`, one port
  value, probes, memory limits, container hardening, logs at `warning`.

**Non-goals**

- No change to the route, host, TLS, middlewares or the public allowlist entry.
- No change to the NFS exports or their claims.
- No app upgrade: the image stays `2.0.2-stable`.
- No new feature. The 1 Gi claims stay 1 Gi; shrinking a bound `local-path` claim is not possible
  without recreating it.

## 3. Approaches considered

| Approach | Trade-off |
|---|---|
| Upstream PR on `Softwaredam/filebrowser-chart` | Correct for everyone, but the pod stays unpinned until the maintainer ships a release; the repo's guarantee waits on a third party |
| Fork the chart and depend on the fork | Keeps pattern D and the upstream values surface, but inherits the upstream templates and creates a fork to rebase |
| **Local chart (chosen)** | We own four small templates and the values that already live here; no second repository, no wait |

## 4. Constraints the builder cannot choose

- **Names are fixed by the live cluster.** The Deployment, Service, ConfigMap and Secret are
  `filebrowser-filebrowser-quantum`, and its selector is `app: filebrowser-quantum` +
  `release: filebrowser`. A Deployment selector is immutable, so those two labels stay even though
  the chart's own labels are the `app.kubernetes.io/*` set.
- **Claim names are fixed.** `filebrowser-filebrowser-quantum-config` holds `database.db` (users,
  scopes, OIDC linkage); `-data` holds the cache. `app.fullname` therefore renders
  `<release>-filebrowser-quantum`, which keeps `fullname-config` and `fullname-data` identical to the
  live claims.
- **The node is `nuc0`.** The config claim is already bound there.
- **The route is public on purpose.** `traefik.middlewares` keeps `security` only; adding `lan-only`
  makes the editor and share links unreachable from outside.
- **The app writes as uid 1100, primary group 1000.** Reads and uploads depend on it; see the
  FileBrowser chart README for the mergerfs reason.

## 5. What the chart does

- `image` is `ghcr.io/gtsteffaniak/filebrowser:2.0.2-stable`, `appVersion` `2.0.2`.
- `service.port: 8080` drives the container port, the Service, every probe and `http.port` in
  `config.yaml`.
- `nodeSelector` pins `nuc0`; `tolerations: []` and `affinity: {}` are rendered empty on purpose so a
  nil value cannot be mistaken for a missing feature.
- Probes: readiness and liveness both on `/health`. FileBrowser's only dependency is its local
  SQLite database, which stays up with the process, so there is no second endpoint to separate.
- Logs: `server.logging` enables `warning|error` only.
- Container hardening: no privilege escalation, all capabilities dropped, seccomp `RuntimeDefault`.
- The ConfigMap is mounted with `subPath`, so a config change needs a restart; the
  `checksum/config` annotation forces one on the next sync.

## 6. What is left to the builder

Template layout, helper names and comment wording. The rendered object set, names and selectors are
fixed above.
