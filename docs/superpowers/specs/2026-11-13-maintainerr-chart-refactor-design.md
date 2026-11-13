# maintainerr chart refactor — design

- Date: 2026-11-13
- Status: draft — awaiting the operator's approval
- Scope: `cluster/helm/media-stack/maintainerr`, a new chart README, one row and one paragraph in
  `docs/charts/README.md`. The Application file does not change.

## Intent

Maintainerr is the media-cleanup app: it builds collections from rules (unwatched, no requests, low
quality) and removes the media when a collection's rule says to. Its chart is the September-migration
scaffold and breaks the repo's chart contract in the ways that stop it running at all.

**The app has not run for 76 days.** The pod is `Pending` for two independent reasons, either of which
is enough on its own:

1. The Deployment mounts claim `maintainerr-config`, and the chart never creates it. The claim does
   not exist in `media-stack`.
2. The pod carries a `node-type: general` node affinity, a label the repo retired. No node matches
   it any more.

The rest is the usual scaffold debt: no probes, no resource requests or limits, no ServiceAccount,
`replicas: 1` typed into the template, a hardcoded claim name, and an `appVersion` of `1.0.0` that
never matched the image. The image is pinned at `3.21.1`; upstream published **`3.32.0`** on
2026-11-05.

There is nothing to migrate: the old claim was a Longhorn volume that vanished in the 2026 disk
failure, so the rebuild starts from an empty config volume and the app initialises itself on first
boot.

## What the application needs

Facts from the upstream source, image and docs at `v3.32.0`, not guessed:

| Fact | Source |
|---|---|
| Web UI and API share one listener on **6246** | `Dockerfile` `EXPOSE 6246`, `ENV UI_PORT=6246`; installation docs |
| State lives in `/opt/data` (SQLite database, logs, staged UI copy) | `Dockerfile` `ENV DATA_DIR=/opt/data`, `VOLUME ["/opt/data"]` |
| The image runs as **UID 1000** (`node`), never as root | `Dockerfile` `USER node` |
| `start.sh` copies the web UI into `$DATA_DIR/ui` on **every boot** and rewrites its base-path placeholder there | `docker/start.sh` at `v3.32.0` |
| `/opt/data` must be writable by the container's UID, or the app exits at start | same file, the `Failed to stage the UI` branch |
| `GET /api/health/live` answers 200 whenever the process serves; it never touches the database | app-and-health API docs |
| `GET /api/health/ready` runs `SELECT 1` and returns **503** when the database is unreachable | same page; the image's own `HEALTHCHECK` calls it |
| `LOG_LEVEL` is a supported runtime variable: `debug`, `verbose`, `info`, `warn`, `error`, `fatal`; default `info` | installation docs, environment-variable table |
| `TELEMETRY=off` disables the anonymous weekly usage report | installation and security docs |
| There is **no authentication anywhere**. The key on the Settings page is never checked on inbound requests; it exists for the app's own internal calls | security docs, API docs |
| `GET /api/settings/database/download` hands over the database, with every stored credential in clear text | API docs, Secrets section |
| Latest release `v3.32.0` (2026-11-05); GHCR publishes `3.32.0` | GitHub releases, GHCR tag list |
| The purpose-built optional mount is the media library, for cleaning up leftover folders after deletions | installation docs, "leftover-folder cleanup" |

The health endpoints matter more than usual here: this is the first media-stack app whose readiness
endpoint fails with a real `503` when its dependency (the SQLite file) is broken, so no readiness
exception is needed.

## Goals

1. Follow the chart contract: `app.*` helpers for every name, no hardcoded object names, no tabs,
   `helm lint` and `helm template` clean, `appVersion` equal to `image.tag`.
2. Create the config claim from the chart (`maintainerr-config`, `local-path`) so the app can start,
   and pin the pod to the node that claim lands on.
3. Honest probes: startup and liveness on `/api/health/live`, readiness on `/api/health/ready`.
4. Local-only exposure: one Traefik `IngressRoute` on the `web` entry point at
   `maintainerr.kub.local`, no TLS, no middlewares.
5. Pin the image to `3.32.0`, run it non-root as the image's own UID 1000, and drop every capability.
6. Quiet logs by day (`LOG_LEVEL: warn`) and no outbound usage report (`TELEMETRY: off`).
7. Record the security model in the chart README: the API has no login, so the LAN boundary is the
   only access control.

## Non-goals

- No public route and no TLS. The app is LAN-only; its current IngressRoute already is.
- No change to `cluster/argocd/projects/media-stack/maintainerr.yaml`.
- No data migration (the old claim does not exist).
- No media-library mount. Leftover-folder cleanup can delete directories on the NAS; it stays off
  until the operator asks for it, and the README documents the values to add.
- `maintainerr-anime` is not this change's business. Its Application and chart left git some time
  ago and ArgoCD pruned the Application; only four orphaned objects remain in `media-stack` (the
  Deployment, its `Pending` pod, the Service and the IngressRoute), still carrying
  `argocd.argoproj.io/tracking-id: maintainerr-anime` annotations. They do not interfere with this
  rebuild — separate Service, selector and host — and removing them is a cluster cleanup for the
  operator to sanction, not a chart change. The programme index's Wave B entry is stale for the same
  reason and is corrected separately.
- No Prometheus exporter. No maintained Maintainerr exporter exists (a GitHub search returns
  nothing), and the contract says an app with neither an exporter nor a maintained sidecar gets
  nothing.

## Anti-goals

- Creating the claim by hand and leaving the chart as it is. The next sync would not fix the next
  incident, and the repo does not accept hand-applied manifests.
- Pointing readiness at the liveness path to fill the field. `/api/health/ready` exists and is the
  honest target.
- Running the container as `1100` because the rest of the media stack uses that UID. The image's
  supported and documented UID is 1000, and this UID has never been exercised; a fresh claim is
  world-writable, so there is nothing to gain.
- Mounting a ConfigMap over anything: the app has no config file, all configuration is in the
  database.
- Reintroducing `fsGroup`. `local-path` is hostPath-backed and the kubelet does not apply it, so it
  would look like a permission fix and change nothing.

## Approaches considered

**A. Minimal patch.** Create the claim, replace the retired affinity with `kubernetes.io/hostname`,
add probes and resources, bump the tag. Four defects fixed and the chart left as the copy it is:
boilerplate values nothing reads, non-derived object names, helpers that differ from every other
chart, `replicas: 1` in the template.

**B. Rebuild on the reference shape.** Same contract as `autobrr` and `tautulli`: `app.*` helpers,
named values for routing, storage, env, probes and resources, five objects, a README, a contract row.

**Chosen: B.** The templates already exist and are copied, not designed, so a rebuild costs the same
as a patch. Every media-stack chart repaired this week is on that shape, and a patched chart would be
the only one that is not.

## Values (shape)

```yaml
image:
  repository: ghcr.io/maintainerr/maintainerr
  tag: "3.32.0"          # appVersion must match

strategy: Recreate

service:      { type: ClusterIP, port: 6246 }
container:    { port: null }

ingressRoute:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  entryPoints: [web]
  host: maintainerr.kub.local
  pathPrefix: /
  middlewares: []
  tls: { enabled: false, ... }

storage:
  enabled: true
  existingClaim: ""      # defaults to <fullname>-config = maintainerr-config
  className: local-path
  accessModes: [ReadWriteOnce]
  size: 256Mi
  mountPath: /opt/data

env:
  TZ: "Europe/Paris"
  LOG_LEVEL: "warn"
  TELEMETRY: "off"

securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  allowPrivilegeEscalation: false
  capabilities: { drop: [ALL] }

probes:
  startup:   { enabled: true, path: /api/health/live, ... }
  liveness:  { enabled: true, path: /api/health/live, ... }
  readiness: { enabled: true, path: /api/health/ready, ... }

resources:
  requests: { cpu: 25m, memory: 256Mi }
  limits:   { memory: 1Gi }

nodeSelector:
  kubernetes.io/hostname: streaming
```

## Decisions

### Image: `ghcr.io/maintainerr/maintainerr`, tag `3.32.0`

GHCR is the project's own registry and already the chart's source; the tag list carries `3.32.0`, and
the repo's image policy prefers GHCR to Docker Hub. Upstream tags carry no `v` prefix, unlike the
release tags, so `appVersion` is `"3.32.0"`.

The jump from `3.21.1` is safe precisely because the config volume does not exist: there is no
database to migrate. The chart version is `1.0.0` because the values schema changes with the rebuild.

**Bumps are manual from here, and both numbers move together.** Renovate's Docker manager edits
`values.yaml` only; nothing in `.github/renovate.json` touches a hand-maintained chart's `Chart.yaml`,
and no Renovate commit in the history has. Every hand-maintained chart's version bump is a single
commit carrying `image.tag`, `appVersion` and the chart `version` (see `autobrr` and `tautulli`). The
README states that rule instead of repeating `tautulli`'s comment claiming Renovate does it.

### Config volume: `maintainerr-config`, 256Mi, `local-path`

The chart creates the claim it mounts, so the `Pending` pod has something to bind. `local-path` is
hostPath-backed and `storageClassName` is `local-path`, which is why the pod is pinned to
`streaming`, where the rest of the media stack and Plex run.

256Mi rather than the house default of 128Mi: `/opt/data` holds a copy of the web UI that `start.sh`
rebuilds **on every boot** (so peak usage is roughly two copies while it is being replaced), the
SQLite database with its collection logs, and the app's log files. 128Mi is the size for a volume
that holds settings; this one holds an application bundle as well.

### Non-root UID 1000

`securityContext` is `runAsNonRoot: true`, `runAsUser: 1000`, `runAsGroup: 1000`,
`allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, with
`seccompProfile: RuntimeDefault` at pod level. The image ships `USER node` (1000), so this is the
configuration upstream documents, and the container starts able to write with no root phase.

No `fsGroup`, and no node-side `chown`: the `local-path` chart's setup script now creates each volume
directory world-writable (`0777`) and chowns it to `1100:1100`. UID 1000 is not the owner but can
write, so the first boot succeeds. This is recorded in the README: it is the one place where the
claim owner and the process UID differ, and it is why a future `permPattern` of `0750` would break
this chart before any other.

### Probes

- **Startup and liveness**: `GET /api/health/live`. It answers as soon as the HTTP server listens and
  deliberately does not touch the database, which is what separates a wedged process from a database
  blip. Startup period 5s with a 180s budget covers the first boot: UI staging, Nest bootstrap and
  the initial migrations.
- **Readiness**: `GET /api/health/ready`. It runs `SELECT 1` and returns `503` when the SQLite file is
  missing, locked or corrupt. This is a real dependency check on a distinct path, so the contract's
  readiness requirement is met without an exception. It is deliberately shallow: an unreachable
  Plex or Radarr does not take the pod out of rotation, which is what upstream intends.

### Logs and telemetry

`LOG_LEVEL: warn` in the inline `env` map, per the contract: Fluent Bit ships every line to Loki with
no filter, and `info` from a scheduler app is thousands of request and rule-evaluation lines a day.
Failures that matter (a failed deletion, an unreachable integration) are logged at `warn` and above,
so nothing is lost.

`TELEMETRY: off` disables the anonymous weekly report to `telemetry.maintainerr.info`. The report is
on by default for new installs; this is a self-hosted appliance on a private network, and the
operator has no reason to send it usage data. Upstream exposes the flag precisely for this.

### No authentication, by design

Maintainerr has no login and the API key on its Settings page is not checked on inbound requests. On
the LAN that is the upstream model; the chart's job is to make sure the app is not reachable from
outside it. The README says this plainly, including that `/api/settings/database/download` hands out
the whole database, and points at the upstream security page for the reverse-proxy options if the
app is ever exposed.

### Resources: unmeasured, but plausible

The app has not run since the 2026 disk failure, so there is no Prometheus history. The request is
`25m`/`256Mi` and the limit `1Gi`. A Node/Nest process with a SQLite handle and on-demand poster
rendering (`sharp`, `node-canvas`) is the shape being budgeted for: a few hundred MiB resident, with
a spike when a large collection renders posters. No CPU limit, per the house rule. The README marks
the numbers as unmeasured and says to replace them from Prometheus a week after the first start.

### No metrics

No exporter exists upstream and none is maintained elsewhere; a hand-written one would be code
nobody maintains, which the contract forbids. The chart renders no scrape annotations.

## Documentation changes

- New `cluster/helm/media-stack/maintainerr/README.md`: upstream links, what each value controls, the
  claim and the `0777`/UID-1000 story, the probes, the log level and telemetry flags, the
  no-authentication model, the optional media mount for leftover-folder cleanup, the manual bump rule
  (`image.tag`, `appVersion` and `version` in one commit, because Renovate will not do it), and
  uninstall behaviour (the claim goes with the release).
- `docs/charts/README.md`: a row in the probes/hardening table, and a "what good looks like"
  paragraph naming Maintainerr's two deliberate differences — a readiness probe that is real, and an
  app with no authentication at all, kept LAN-only.

## Verification

Local:

```bash
helm lint cluster/helm/media-stack/maintainerr --namespace media-stack
helm template maintainerr cluster/helm/media-stack/maintainerr --namespace media-stack >/tmp/maintainerr.yaml
grep -c '^kind:' /tmp/maintainerr.yaml                     # expect 5
grep -rnP '\t' cluster/helm/media-stack/maintainerr/templates || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/maintainerr/values.yaml || echo "no floating tags"
grep -rn 'node-type\|networking.k8s.io\|maintainerr-config-volume\|ingress.hosts' \
  cluster/helm/media-stack/maintainerr/templates \
  cluster/helm/media-stack/maintainerr/values.yaml || echo "no leftovers"
grep -n 'appVersion' cluster/helm/media-stack/maintainerr/Chart.yaml   # 3.32.0, matches image.tag
```

After the merge (auto-sync is off, so the Application is synced by hand):

- Pod `Running` and `Ready`, `maintainerr-config` `Bound` on `streaming`.
- `curl -s http://maintainerr.kub.local/api/health/live` → `{"status":"ok",...}`.
- `curl -s http://maintainerr.kub.local/api/health/ready` → `{"status":"ok","database":"ok",...}`.
- `curl -s http://maintainerr.kub.local/api/app/status` → version `3.32.0`, `updateAvailable:false`.
- `/opt/data/ui` exists and is owned by UID 1000: the app staged its UI and can write.
- Prometheus/`kubectl top` after a week: replace the unmeasured resources.

## Risks

- **Claim permission.** A process UID that is not the claim's owner relies on the `0777` directory
  mode. If the local-path chart ever sets `permPattern`, maintainerr is the first chart to break, with
  `Failed to stage the UI` in its logs. Recorded in the README.
- **First boot is the slow path.** UI staging plus migrations on an empty database; the 180s startup
  budget is deliberate headroom, not a guess at steady state.
- **No authentication.** A pod that starts and is reachable is a pod whose settings and database can
  be read by anyone who can resolve `maintainerr.kub.local`. The route stays on `web` and the LAN.
- **Unmeasured resources.** 1Gi is headroom for poster rendering, not a measurement; a very large
  library may need more, and the metric to watch is the container's memory peak during a collection
  run.
- **Unguarded deletion.** The app's whole purpose is deleting media. No chart-side guard can prevent
  a bad rule; the README points at the app's own dry-run and collection-preview controls.

## Left to the builder

- Exact template wording, README prose and comment density.
- The order of the commits (chart, then README, then contract row) — the other refactors used three.
