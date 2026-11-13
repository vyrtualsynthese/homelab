# seerr

Seerr is the request portal for the lab's Plex library: household members ask for a film or a show,
and Seerr passes the request to Radarr or Sonarr. It is published at
<https://seerr.kub.home.ashudev.com> through Traefik.

The chart is built from the reference chart `../autobrr` and the contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md). The differences are listed below;
each one follows from what Seerr is.

## Upstream

- Image: `ghcr.io/seerr-team/seerr`, pinned to `v3.5.2`.
- Docs: <https://docs.seerr.dev/>
- Source: <https://github.com/seerr-team/seerr>

The image runs as the `node` user baked into the official Node.js base image (UID/GID 1000). It
does **not** support `PUID`/`PGID`: it never starts as root, so there is no privilege drop to
configure. See <https://docs.seerr.dev/blog/why-seerr-doesnt-support-puid-pgid/>.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/seerr.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `seerr`, and it contains the
chart name, so every object is named `seerr` — no `seerr-seerr` suffix.

```bash
# what the cluster will render, exactly
helm template seerr cluster/helm/media-stack/seerr --namespace media-stack
```

It renders ten objects:

| Kind | Name |
|---|---|
| Deployment | `seerr` |
| Service | `seerr` |
| ServiceAccount | `seerr` |
| PersistentVolumeClaim | `seerr-config` |
| IngressRoute | `seerr-public` |
| IngressRoute | `seerr-public-redirect` |
| Middleware | `seerr-security` |
| Middleware | `seerr-ratelimit` |
| Middleware | `seerr-redirect-https` |
| TLSOption | `seerr-tlsoption` |

Every one of those names already exists in the cluster except the ServiceAccount and the rate-limit
Middleware. ArgoCD adopts the rest in place; the config claim is never deleted or recreated.

## Why this chart differs from the reference

| Reference (autobrr) | Here | Why |
|---|---|---|
| `web` entry point, no TLS | **Secure route on `websecure` with ACME and a TLSOption, plus a `web` → HTTPS redirect** | Seerr is reached by name over TLS, like the other published media apps |
| No auth middleware | **No auth middleware here either, deliberately** | Seerr's own account model is the gate; its API clients talk to the same host and cannot complete a forward-auth flow |
| `runAsUser: 1100` | **`runAsUser: 1000`, caps dropped** | The image's `node` user is 1000; the explicit pin matches the UID it already uses |
| `PUID`/`PGID` in the LinuxServer style | **Removed** | Seerr does not read them; the image runs unprivileged by design |
| Exporter annotations | **None** | Seerr ships no Prometheus exporter, and there is no maintained sidecar for it |
| No probe excuse | **Three probes on `/api/v1/status/appdata`** | Seerr has one unauthenticated endpoint that answers without an update check; see below |
| `LOG_LEVEL` at the image default | **`warn`** | The default is `debug`, which fills Loki with a line per scheduled job every minute |

## Probes

| Probe | Path | Budget |
|---|---|---|
| startup | `/api/v1/status/appdata` | 5s × 60 = five minutes for migrations after an upgrade |
| liveness | `/api/v1/status/appdata` | 20s delay, 3 failures every 30s |
| readiness | `/api/v1/status/appdata` | 10s delay, 3 failures every 15s |

The probes deliberately use `/api/v1/status/appdata`, not `/api/v1/status`. The status route
performs the GitHub update check when the `versionCheck` setting is on; a slow or unreachable GitHub
can hold it past the probe's 5-second timeout, turning an update-service outage into a restart loop.
The appdata route answers 200 while the process serves and reports whether `/app/config` is
accessible. It also does no outbound call, so it is safe to hit on every probe.

Readiness shares the liveness endpoint. Seerr exposes no endpoint that separates "the process is up"
from "the database is usable" — the public settings endpoint reads an in-memory `Settings` object,
not the database — so a second guessed endpoint would turn a slow database into a restart loop. This
is a documented exception to the contract's "readiness must not share the liveness endpoint" rule.

## Config volume and the UID trap

The config claim is `seerr-config`: `local-path`, `ReadWriteOnce`, 128Mi, mounted at `/app/config`.
It holds the SQLite request database, `settings.json`, the VAPID keys and the rotating logs.

Verified live on 2026-11-13: the claim is `Bound` with that class, mode and size, and the running pod
mounts it at `/app/config`. No `overseerr-config` claim exists in any namespace — that name in the
storage audit is a stale remnant of the Overseerr → Seerr rename, not a second live claim.

- **Do not replace the claim.** It is the only copy of every request and user setting. `local-path`
  cannot expand an existing claim, so changing `storage.size` makes ArgoCD fail on an immutable
  field; 128Mi is a label rather than a quota, not a limit the app can hit.
- **Do not change `runAsUser`.** The image and the existing volume are already owned by UID 1000.
  `local-path` is hostPath-backed, and the kubelet does not apply `fsGroup` ownership to hostPath
  volumes, so a different UID makes `/app/config` unreadable. If a fresh claim is ever created
  root-owned, `chown -R 1000:1000` its hostPath on the node before starting the pod.

## Logs

`LOG_LEVEL` is `warn`. Seerr defaults to `debug`, which ships a line per scheduled job and per
download-tracker poll to stdout every minute. Fluent Bit forwards every line to Loki without a
filter, so the app's own level is the only place the volume can be cut.

Failures are still logged at `warn` and `error`. Start-up messages are logged at `info`, so they
disappear from Loki; the probes and the app's own file logs cover that gap. The full record stays in
`/app/config/logs` (rotated, 7 days). Raise the level to `info` or `debug` temporarily while
debugging, then put it back.

## Resources

Sized from Prometheus, 30 days on `streaming`:

| Measure | Value |
|---|---|
| memory working set | p50 322MiB, p95 325MiB, max 336MiB |
| CPU (5m rate) | p95 1m, max 5m |

Requests are 10m CPU and 320Mi memory, following the measured steady state. The memory limit is
512Mi — about 1.5× the observed peak, with the file cache as the first thing the kernel reclaims.
There is no CPU limit: a limit would throttle a poster burst or a library scan without protecting
anything. The app has never been OOM-killed.

## Security posture of the public route

Seerr is on the internal `kub.home.ashudev.com` name, but it gets the same edge treatment as Plex so
the repo has one public-route dialect:

- **TLS:** 1.2 floor, AEAD cipher suites only (no CBC), X25519 first with P-256 and P-384 as
  fallbacks, `sniStrict: true`. TLS 1.3 is used automatically; its suites are not configurable in
  Go.
- **HSTS:** one year, include subdomains, preload.
- **Headers:** `frameDeny`, `contentTypeNosniff`, `browserXssFilter`, `referrerPolicy:
  no-referrer`, `permissionsPolicy` denying camera, microphone, geolocation, payment and USB.
- **Host allowlist:** only `seerr.kub.home.ashudev.com` and the explicit `:443` form. A different
  Host header gets a 421.
- **Rate limit:** 100 requests/second per source IP, burst 200. Seerr's UI loads a few dozen assets
  at once and the rest is API traffic; this is far above normal use. Set
  `ingress.rateLimit.enabled: false` if a client ever trips it.
- **No auth Middleware:** Seerr's login is the gate, and the same host serves its API clients.
- **No Content Security Policy:** the Next.js UI loads inline scripts. The value exists and is
  empty; populate it only after watching the browser console against a staging host.

## Secrets

None. Seerr creates its own session secret, VAPID keys and SQLite database under `/app/config`, so
this chart expects no pre-existing Secret and no `envFrom` source.

## Upgrading

The chart pins the image tag; changing `image.tag` and `Chart.yaml.appVersion` together is the whole
upgrade. Renovate does this automatically for patch and minor releases.

**v3.5.2 (2026-11-12)** is a security release: it fixes three vulnerabilities whose details are
published on 2026-11-15. It also carries the v3.5.0 change to the Plex and Jellyfin library API:
`GET /api/v1/settings/{plex,jellyfin}/library` no longer accepts `sync` or `enable`, and reading
libraries no longer changes their enabled state. The web UI already uses the new endpoints. Nothing
in this chart calls those routes, so no values change is needed; an external script that syncs or
toggles libraries must move to `POST .../library/sync` and `PUT .../library/{libraryId}`.

Seerr runs its database migrations during startup, gated by the five-minute startup probe. The
config claim is not touched by the upgrade. If the new image fails to start, set `image.tag` and
`appVersion` back to `v3.4.1`; a migration may be one-way, so check the release notes before rolling
back.

The first refactor rollout also changed the pod shape (dedicated ServiceAccount, explicit UID/GID,
dropped capabilities, restart probes, `LOG_LEVEL=warn`, `PORT` derived from the container port
and `CONFIG_DIRECTORY` from the mount path). That
change has already been applied; only the image tag moves now.

## Checking the chart against the contract

```bash
cd cluster/helm/media-stack/seerr
helm lint . --namespace media-stack
helm template seerr . --namespace media-stack | tee /tmp/seerr.yaml

grep -c '^kind:' /tmp/seerr.yaml          # 10
grep -rnP '\t' templates/ || echo "no tabs"
grep -rnE 'tag:[[:space:]]*"?latest' values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io' templates/ || echo "no core Ingress"
grep -nE 'PUID|PGID|setGateway|seerr-webui' /tmp/seerr.yaml || echo "no leftovers"
```

Changing `values.yaml` and re-running the second command is the whole test loop: there is no CI, and
the render is the contract.
