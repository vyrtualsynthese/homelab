# seerr chart refactor — design

## Intent

The `cluster/helm/media-stack/seerr` chart still carries the scaffold it was generated from. Its
values, templates and live objects disagree: the Service hardcodes 5055 while `service.port` says
32400, the `ingress` block is dead, the security Middleware is rendered but never attached to the
route, and the TLSOption contains cipher names Traefik does not accept. The app also runs without
probes, without container hardening, and with `PUID`/`PGID` variables that Seerr explicitly ignores.

The refactor makes the chart match the repo contract
([`docs/charts/README.md`](../../../docs/charts/README.md)) and the Plex chart's public-route shape,
without renaming a single live object or replacing the existing config claim.

## Goals

- One values-driven chart in the `autobrr`/`plex` shape, with `app.*` helpers.
- `image.tag` and `Chart.yaml.appVersion` agree on `v3.4.1`.
- Service port 5055, container port 5055, one port name (`http`), no hardcoded duplicates.
- A working Traefik stack: secure route on `websecure`, plain-HTTP redirect on `web`, TLS through
  `myresolver` with a hardened TLSOption, security headers with a host allowlist, and a rate limit.
- Seerr runs as the UID it already uses (1000), with capabilities dropped, no privilege escalation
  and the runtime seccomp profile.
- Probes on the app's own health endpoint `/api/v1/status` for startup, liveness and readiness.
- Logs at `warn` so Fluent Bit does not ship the per-minute debug chatter to Loki.
- Resources sized from the measured 30-day Prometheus data.

## Non-goals

- Upgrading the Seerr image. Renovate owns the tag; `v3.4.1` stays.
- Adding a metrics exporter. Seerr ships none and no maintained sidecar exists.
- Changing the public host `seerr.kub.home.ashudev.com`.
- Adding forward authentication. Seerr's own login is the gate; a forward-auth flow would break the
  mobile and TV clients that talk to the same host.
- A Content Security Policy. The Next.js UI uses inline scripts; a CSP written without testing
  against the app breaks it.
- A shared library chart or a wrapper around an upstream chart.
- Replacing the config claim. It holds the request database and is not expandable under `local-path`.

## Approaches considered

### A. Patch the scaffold in place

Fix `service.port`, attach the existing security Middleware, correct the TLSOption and add probes.
Smallest diff, but leaves unprefixed helpers, dead `ingress` values, hardcoded host and port names,
and two unrelated template styles in one directory. Rejected: the next person still has to compare
it against the reference to know which parts are real.

### B. Rebuild in the reference shape, preserving live names (chosen)

Copy the Plex/autobrr structure: unprefixed `app.*` helpers, one values block per concern, a
values-driven IngressRoute plus a redirect route, Middlewares and a TLSOption rendered from the same
values. Every Kubernetes object keeps its current name, so ArgoCD adopts the objects in place. The
only renamed field is the Service/container port name (`seerr-webui` → `http`), which nothing outside
this chart references. Larger diff, but the result is reviewable against the contract and does not
need a second pass.

### C. Wrapper chart around an upstream chart

Rejected: the route, probes and hardening would still be ours, the upstream chart's values would
become a second source of truth, and there is no maintained upstream chart worth the dependency for
a single-component app.

## Decisions

### Live object names stay

| Object | Name |
|---|---|
| Deployment | `seerr` |
| Service | `seerr` |
| ServiceAccount | `seerr` (new; previously `default`) |
| PersistentVolumeClaim | `seerr-config` (created by the chart) |
| IngressRoute | `seerr-public` |
| IngressRoute | `seerr-public-redirect` |
| Middleware | `seerr-security` |
| Middleware | `seerr-redirect-https` |
| Middleware | `seerr-ratelimit` (new) |
| TLSOption | `seerr-tlsoption` |

The claim name is derived (`app.configClaimName` → `<fullname>-config`), which still resolves to
`seerr-config`. No object is deleted or recreated by ArgoCD.

### Edge policy

Seerr is not internet-facing; `kub.home.ashudev.com` is the internal name. It still gets the full
Plex edge treatment because the cost is one values block and the alternative is a second public-route
dialect in the repo:

- TLS 1.2 floor, AEAD suites only, X25519 first, `sniStrict: true`.
- HSTS one year with subdomains and preload.
- `frameDeny`, `contentTypeNosniff`, `browserXssFilter`, `referrerPolicy: no-referrer`,
  `permissionsPolicy` denying camera, microphone, geolocation, payment and USB.
- `allowedHosts` limited to the ingress host and `host:443`.
- Rate limit 100 requests/second per source IP with a burst of 200, switchable in values.
- No auth Middleware: Seerr's own account model is the gate, and the same host serves API clients.

### Workload

- Image `ghcr.io/seerr-team/seerr:v3.4.1`.
- `runAsNonRoot`, `runAsUser: 1000`, `runAsGroup: 1000`, `allowPrivilegeEscalation: false`,
  `capabilities.drop: [ALL]`, pod `seccompProfile: RuntimeDefault`.
- `PUID`/`PGID` removed. Seerr supports neither; the image runs as the baked-in `node` user (1000).
  The explicit pin matches what the pod already does, so the existing claim stays readable.
- `LOG_LEVEL: warn`. Seerr defaults to `debug`; the live pod writes a debug line per minute plus an
  info line per scheduled job. Failures still log at `warn` and `error`, and the app keeps its own
  rotating file logs under `/app/config/logs`.
- Probes:
  - startup, liveness and readiness: `/api/v1/status` — unauthenticated, answers while the process
    serves. Seerr exposes no endpoint that separates "process up" from "database usable": the
    public settings endpoint reads an in-memory object, not the database. Readiness therefore shares
    the liveness endpoint, a documented exception to the contract, because a guessed second endpoint
    would turn a slow database into a restart loop.
- Resources: requests 10m CPU / 320Mi memory, limit 512Mi memory, no CPU limit. Measured 30 days on
  `streaming`: p50 322MiB, p95 325MiB, max 336MiB; CPU p95 1m, max 5m. 320Mi is the steady state,
  512Mi is roughly 1.5× the peak.

### Config claim

`local-path`, `ReadWriteOnce`, 128Mi, mounted at `/app/config`. `local-path` cannot expand an
existing claim, so the size is a label rather than a quota. The claim already holds the database;
replacing it would lose every request.

## Constraints

- `helm lint` and `helm template` must pass on the real tree.
- No tabs, no `networking.k8s.io` Ingress, no floating tags, no copy-paste leftovers.
- Every object carries `app.labels`.
- Values hold every tunable; templates hold no host, port, claim name or secret name.
- Live objects are adopted in place; the change must not prune the claim or create a second route
  for the same host.

## What is left to the implementer

Exact template wording, the README structure, and the commit split. The plan fixes the object list,
values keys, route shape and verification commands.
