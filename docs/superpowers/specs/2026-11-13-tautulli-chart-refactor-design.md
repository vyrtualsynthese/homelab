# tautulli chart refactor — design

- Date: 2026-11-13
- Status: approved 2026-11-13
- Scope: `cluster/helm/media-stack/tautulli`, a new chart README, and one row in
  `docs/charts/README.md`.

## Intent

Tautulli is the Plex watch-statistics dashboard. Its chart is the scaffold from the September
migration and it violates the repo's chart contract in most of the ways that matter: a hardcoded
claim name, a retired node label, an `appVersion` that never matched the image, a Service port that
disagrees with the container, and a public TLS ingress on `kub.home.ashudev.com` for an app that
should only be reachable on the LAN.

**It has not run since the 2026 disk failure.** The pod has been `Pending` for 71 days because the
Deployment mounts `tautulli-config2`, a Longhorn claim that no longer exists. The rebuild therefore
creates a fresh config volume; there is nothing to migrate.

The operator wants it reachable on the LAN only.

## What the application needs

Facts from the upstream source and image, not guessed:

| Fact | Source |
|---|---|
| Web UI listens on **8181** | `Dockerfile` `EXPOSE 8181`, upstream docs |
| State lives in `/config` (`tautulli.db`, `config.ini`, logs) | `Dockerfile` `VOLUME /config`, `CMD --datadir /config` |
| The image starts as **root**, renumbers its `tautulli` user to `PUID`/`PGID`, chowns `/config`, then `gosu`s to that user | `start.sh` at `v2.19.1` |
| `PUID`, `PGID`, `TZ` are the documented environment variables | upstream docs, `Tautulli-Docker` README |
| No log-level environment variable exists; the level lives in `config.ini`, written by the setup wizard | `start.sh`, `config.ini` handling |
| `GET /status` returns `{"result":"success","message":"Ok"}` with **no authentication** | `plexpy/webserve.py`, `def status`; the image's own `HEALTHCHECK` curls it |
| `GET /server_status` reports Plex connectivity in the **body** only, always HTTP 200 | `plexpy/webserve.py`, `def server_status` |
| `GET /status?check=database` reports SQLite integrity in the body only, and requires auth when auth is enabled | same file |
| The project publishes the same build to Docker Hub *and* GHCR | `.github/workflows/publish-docker.yml` at `v2.19.1` |
| Latest release is `v2.19.1`; GHCR carries `v2.17.2` … `v2.19.1` | GitHub releases, GHCR tag list |

## Goals

1. Follow the chart contract: `app.*` helpers for every name, no hardcoded object names, no tabs,
   `helm lint` and `helm template` clean.
2. Local-only exposure: one `web` IngressRoute on `tautulli.kub.local`, no TLS, no certificate.
3. Create the config claim from the chart (`tautulli-config`, 128Mi, `local-path`) so the app can
   actually start, and pin the pod to the node that claim lands on.
4. Honest probes: startup and liveness on `/status`; no fake readiness.
5. Pin the image to `v2.19.1` and make `appVersion` equal to `image.tag`.
6. Delete the copy-paste baggage: `ingress.hosts`/`tls`, `autoscaling`, `serviceAccount` boilerplate
   that rendered nothing, the dead security-headers middleware, the TLSOption, the redirect route.

## Non-goals

- No public exposure and no TLS. `kub.home.ashudev.com` is a public zone; the app is not on it.
- No change to `cluster/argocd/projects/media-stack/tautulli.yaml`.
- No data migration (the old claim does not exist).
- No Prometheus exporter. No maintained Tautulli exporter exists — the GitHub search returns
  projects with one or two stars — and the contract says an app with neither a built-in exporter nor
  a maintained sidecar gets nothing.

## Anti-goals

- Keeping the public route "because it exists". It is the thing being removed.
- A readiness probe pointed at the liveness path just to fill the field.
- Mounting `config.ini` from a ConfigMap to set the log level: it would replace the file the setup
  wizard owns.

## Approaches considered

**A. Minimal patch.** Fix the claim name, the port, the node label and `appVersion`, and add a
`kub.local` route next to the public ones. Cheapest, but leaves the dead middleware, TLSOption and
boilerplate values in place, and the chart stays the copy it always was.

**B. Rebuild on the reference shape with named values.**
Use `autobrr`/`prowlarr` as the reference for naming, labels, claim shape and probe honesty; expose
Tautulli's real needs as named values (`ingressRoute`, `storage`, `probes`, `resources`,
`securityContext`); delete what nothing reads.

**Chosen: B.** The operator's priority is charts that are maintainable and match the image; a patch
would leave the chart as the same copy with four fewer bugs.

## Values (shape)

```yaml
image:
  repository: ghcr.io/tautulli/tautulli
  tag: "v2.19.1"

strategy: Recreate

service:      { type: ClusterIP, port: 8181 }
container:    { port: null }

ingressRoute:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  entryPoints: [web]
  host: tautulli.kub.local
  pathPrefix: /
  middlewares: []
  tls: { enabled: false, ... }

storage:
  enabled: true
  existingClaim: ""          # defaults to <fullname>-config = tautulli-config
  className: local-path
  accessModes: [ReadWriteOnce]
  size: 128Mi
  mountPath: /config

env:
  TZ: "Europe/Paris"
  PUID: "1100"
  PGID: "1100"

probes:
  startup:   { enabled: true, path: /status, ... }
  liveness:  { enabled: true, path: /status, ... }
  readiness: { enabled: false }

resources:
  requests: { cpu: 10m, memory: 256Mi }
  limits:   { memory: 1Gi }

nodeSelector:
  kubernetes.io/hostname: streaming
```

## Decisions

### Image: `ghcr.io/tautulli/tautulli`, tag `v2.19.1`

The upstream docs name `tautulli/tautulli` on Docker Hub, but the project's release workflow pushes
the same build to `ghcr.io/tautulli/tautulli`, under its own organisation. Provenance is satisfied
either way; the registry rule in the stabilisation design prefers GHCR over Docker Hub, so GHCR
wins. `v2.19.1` is the current release and the tag exists on GHCR (verified against the registry tag
list).

### Local-only routing

`entryPoints: [web]`, host `tautulli.kub.local`, no TLS, no middlewares — the same shape as `sonarr`
and `prowlarr`. `kub.local` resolves through the Pi-hole wildcard record; it is not in a public zone
and Traefik's `web` entry point is HTTP.

The two public routes (`tautulli-public`, `tautulli-public-redirect`) stop being rendered. The
Application has no automated sync and no prune policy, so removing the templates does **not** delete
the live objects: the operator deletes them after the sync (see Verification).

### Config volume

The chart creates `tautulli-config` (128Mi, `local-path`) instead of referencing the vanished
`tautulli-config2`. `local-path` is hostPath-backed, so the claim is bound to one node; that is why
the pod is pinned to `streaming`, which is also where Plex and the rest of the media stack run.

The image chowns `/config` to `PUID`/`PGID` at every start, so a fresh claim needs no manual `chown`
— unlike the non-root pattern in `autobrr`, where the UID change happens outside the image.

### Security context

The image's `start.sh` must run as root: it renumbers the user, chowns `/config`, then drops
privileges with `gosu`. So `runAsNonRoot`/`runAsUser` are not options, and the capability set is the
narrow one that sequence needs: `drop: [ALL]` plus `CHOWN`, `SETUID`, `SETGID`. This is the same
documented exception `plex` carries. `allowPrivilegeEscalation: false` and
`seccompProfile: RuntimeDefault` apply and do not break the drop.

### Probes

- Startup and liveness: `GET /status`. It is unauthenticated, returns 200 as soon as CherryPy is
  serving, and is the endpoint the image's own `HEALTHCHECK` uses. Startup gates liveness through a
  first start after an upgrade, when the database may be migrated.
- Readiness: **disabled**. Tautulli's dependency state (`/server_status` for Plex,
  `/status?check=database` for SQLite) is reported in the JSON body, and always with HTTP 200, so
  the kubelet cannot see it. Pointing readiness at `/status` would duplicate liveness and gate
  nothing. This is the exception the contract allows, recorded in the README.

### Logs

No environment variable sets Tautulli's log level; it lives in `config.ini` on the claim, which the
setup wizard writes. Mounting a ConfigMap over it would replace the wizard's file and lose every
setting in it. The chart therefore sets nothing, and the README says to set **Log Level: Warning**
in Settings → Logging after first run — the contract's exception for apps whose level is not
env-driven.

### Resources

No measurements exist: the app has not run since the disk failure. Keep the previous request
(`10m`/`256Mi`) and the previous `1Gi` memory limit, and note in the README that the numbers are
unmeasured and should be replaced from Prometheus after a week — including after the first Plex
history import, which is the one operation that can spike memory.

## Documentation changes

- New `cluster/helm/media-stack/tautulli/README.md`: upstream links, what the values control, why
  the image is the official GHCR build, why readiness is off, the log-level exception, the local-only
  route and the manual deletion of the old public objects.
- `docs/charts/README.md`: add `tautulli` to the probes/hardening table and to "what good looks
  like", with its local-only routing and the disabled readiness recorded as a documented exception.

## Verification

Local:

```bash
helm lint cluster/helm/media-stack/tautulli --namespace media-stack
helm template tautulli cluster/helm/media-stack/tautulli --namespace media-stack >/dev/null
grep -rnP '\t' cluster/helm/media-stack/tautulli/templates || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/tautulli/values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io\|tautulli-config2\|node-type\|3[0-9]\{4\}' \
  cluster/helm/media-stack/tautulli/templates \
  cluster/helm/media-stack/tautulli/values.yaml || echo "no leftovers"
```

After the merge, sync the Application by hand (auto-sync is off) and check:

- Application `Synced`; pod `Running` as uid 1100; `tautulli-config` `Bound`.
- `curl -s http://tautulli.kub.local/status` → `{"result":"success","message":"Ok"}`.
- `kubectl -n media-stack delete ingressroute tautulli-public tautulli-public-redirect` and
  `kubectl -n media-stack delete middleware tautulli-redirect-https tautulli-security`, then
  confirm `tautulli.kub.home.ashudev.com` no longer answers.
- First run: point Tautulli at `http://plex.media-stack.svc.cluster.local:32400`, import history from
  Plex, set Log Level to Warning.

## Risks

- **Capability set.** If the init fails on a permission step, the missing capability shows in
  `kubectl describe pod` as a chown/usermod error. The fix is a specific capability, not a blanket
  `privileged: true`.
- **Old public objects linger.** They survive until deleted by hand; the DNS name is public, so this
  is the one step that must not be forgotten.
- **Unmeasured resources.** A first import of a large Plex history is the memory peak; the 1Gi limit
  is headroom, not a measurement.

## Left to the builder

- Exact template wording and README prose.
- Whether to split the work into chart / docs commits.
