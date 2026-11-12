# sonarr and sonarr-anime chart refactor — design

- Date: 2026-11-12
- Status: approved 2026-11-12
- Scope: `cluster/helm/media-stack/sonarr`, `cluster/helm/media-stack/sonarr-anime`,
  `~/.config/secrets/homelab.env` (API keys, outside git)

## Intent

`sonarr` and `sonarr-anime` are two copies of the same September scaffold: unprefixed helpers,
hardcoded object names, a Service port copied from Plex, no probes, no log level, and an exporter
sidecar whose target has been wrong since the day it was written. This refactor rebuilds both charts
on the same shape as `prowlarr` (aligned 2026-11-12), which is the closest precedent: a LinuxServer
image that starts as root and drops to `PUID`/`PGID`, plus an `exportarr` sidecar. The exporter is
part of this change: its target, its scrape annotations and its image pin are all corrected here.

Two apps, two charts. The repo forbids a shared chart, so the duplication stays.

## What the applications need

Facts read from the live cluster and from upstream on 2026-11-12, not guessed:

| Fact | Source |
|---|---|
| Image is `ghcr.io/linuxserver/sonarr`, running tag `4.0.19`; latest stable is `4.0.20.3014`, published as `4.0.20.3014-ls4` (tag verified on ghcr) | live Deployment, GitHub releases, Docker Hub tags |
| The web UI listens on 8989 | live Service and container port |
| `GET /ping` answers `200 {"status":"OK"}` without credentials on both instances | live pods |
| Config lives in `/config`, claim `sonarr-config` / `sonarr-anime-config`, `local-path`, 128Mi, Bound | live PVCs |
| The claim is root-owned; the LinuxServer s6 init starts as root, prepares `/config`, then drops to `PUID`/`PGID` 1100 | image layout, contract §"Changing the UID" |
| `/downloads` is `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner`; `/movies` is `pvc-plex-media`; both read-write | live Deployment |
| API key Secret is `sonarr` / `sonarr-anime`, key `secret` | live Deployment, exporter env |
| Working set, 30 days: sonarr avg 216Mi, peak 299Mi; sonarr-anime avg 302Mi, peak 380Mi; CPU p95 31m / 8m | Prometheus |
| Live IngressRoutes are `sonarr` and `sonarr-anime`, hosts `*.kub.local`, `web` entry point, no TLS | cluster, chart |
| The current `service.port` value is 32400 while the Service and container use 8989 — a Plex leftover | chart values |
| Sonarr's exporter targets `http://sonarr:7878`; Sonarr listens on 8989, so the target has never been up. Sonarr-anime targets 8989 and works | live Deployment |
| `exportarr` `v2.3.0` is the newest release (12 Aug 2025) and is already pinned in both charts | GitHub releases, ghcr tag check |

## Goals

1. Satisfy the chart contract: `app.*` helpers, every name derived, no tab indentation, ingress fully
   value-driven, `helm lint` and `helm template` clean.
2. Keep every live object name unchanged, because the config claims hold real data: Deployment,
   Service, IngressRoute and config claim must render as `sonarr` / `sonarr-config` and
   `sonarr-anime` / `sonarr-anime-config`.
3. Pin the image to `4.0.20.3014-ls4` and set `appVersion` to the same string, chart `version: 1.0.0`.
4. Size memory from measurement instead of guesswork.
5. Probes on the app's own `/ping`, a log level at `warn`, and the two free hardening settings.
6. A chart README per app, stating what differs from the reference and the known debt.
7. Fix the exporter: one values block renders the sidecar, its port, its target and its scrape
   annotations, so the announced port and the listening port cannot drift apart again.

## Non-goals

- No change to hosts, routes, TLS or auth; no Sonarr 5 (the `develop` channel) and no image registry
  move.
- No merge of the two charts, no shared library chart.

## Approaches

**A. Rebuild both charts from the prowlarr shape (chosen).** Two independent charts, each rendering
its own Deployment, Service, ServiceAccount, config claim, IngressRoute and NOTES, with `app.*`
helpers and values for everything the app needs. Cost: the same content exists twice. Benefit: an
app's peculiarity never becomes a values key nobody remembers, and a chart can be read top to bottom.

**B. One chart, two releases with values overrides.** Halves the files. Rejected: the repo's
`AGENTS.md` forbids introducing a shared chart, and the two releases would diverge in image tag,
secret name, host and memory size — exactly the per-release values that make one chart unreadable.

**C. Patch the existing charts in place.** Only rename helpers and fix the port. Rejected: leaves the
route hardcoded, the claim name hardcoded, the exporter annotations in two places and no README, so
the next chart copies the same defects again.

## Decisions

- **Names.** `app.fullname` with release `sonarr` renders `sonarr`; with `sonarr-anime`, `sonarr-anime`.
  The config claim becomes `app.configClaimName`, which renders the same two names that exist today.
  ArgoCD updates the existing objects instead of creating new ones, so no data is lost.
- **Image.** `ghcr.io/linuxserver/sonarr:4.0.20.3014-ls4` for both charts. Same registry as today;
  only the tag moves.
- **Ports.** Service 8989, port named `http`, route references the numeric port. The bogus 32400
  value disappears.
- **Memory.** sonarr `256Mi` request / `768Mi` limit; sonarr-anime `384Mi` / `1Gi`. Requests sit at
  or above the 30-day average; limits are about 2.5× the peak. CPU request 10m, no CPU limit — house
  rule.
- **Logs.** `SONARR__LOG__LEVEL: warn` and `SONARR__LOG__CONSOLELEVEL: warn` (variable names confirmed
  in the Servarr wiki). Both are set because one writes the file and the other writes stdout, and
  Fluent Bit only sees stdout.
- **Probes.** Startup, liveness and readiness on `/ping`. Readiness shares the liveness endpoint on
  purpose: Sonarr's only dependency is the SQLite database on `/config`, and a database it cannot
  open stops the process rather than degrading it. The README records the reason. Startup allows
  30 × 5s for the first start after the version bump, when the database is migrated.
- **Hardening.** `seccompProfile: RuntimeDefault` and `allowPrivilegeEscalation: false`. No
  `runAsNonRoot`, no dropped capabilities: the s6 init needs root before it drops to 1100, exactly as
  in `prowlarr`.
- **ServiceAccount.** Dedicated, created by the chart, token not mounted. Neither app talks to the
  Kubernetes API.
- **Media mounts.** Two named value blocks, `downloads` and `media`, each with claim, mount path and
  `readOnly` flag (both false). Values stay recognisable as the NFS claims they point at.
- **Ingress.** One `ingressRoute` block with API group in a value, `web` entry point, host from
  values. No TLS, no middleware.
- **Exporter.** One `exportarr` values block per chart: `enabled`, image `v2.3.0` (already the newest
  release), port 9707, API-key Secret and key, and resources. The sidecar's `URL` is built from
  `app.fullname` and the service port, so it renders `http://sonarr:8989` and
  `http://sonarr-anime:8989`; the bug disappears because the port is no longer typed twice. The
  scrape annotations (`scrape`, `scheme`, `port`, `path`) are rendered from the same block when it is
  enabled, never listed a second time in `podAnnotations`. The metrics port stays off the Service.
  Exporter resources stay at the measured 56Mi request / 112Mi limit (observed peak 13Mi over 30
  days).

## Risks

- The image bump is a one-way database migration (4.0.19 → 4.0.20, both v4). A failed start after the
  migration is recovered by pinning the tag back, not by a schema downgrade; the README says so.
- The exporter has never answered for sonarr because its target was 7878. After the fix, confirm the
  target is up in Prometheus; a failing target now points at the Secret's API key, not the port.
- Changing the Service port name from `sonarr-webui` to `http` and the service account from `default`
  to a dedicated one rolls both pods once. Nothing else in the cluster refers to either by name.
- `local-path` cannot grow a claim; 128Mi stays, as with every other config claim in the stack.
