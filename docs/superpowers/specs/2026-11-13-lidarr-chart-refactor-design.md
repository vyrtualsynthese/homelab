# lidarr chart refactor — design

- Date: 2026-11-13
- Status: awaiting approval
- Scope: `cluster/helm/media-stack/lidarr`, a new chart README, one row in
  `docs/charts/README.md`, and one block in `~/.config/secrets/homelab.env` (outside git).

## Intent

Lidarr is the music manager of the media stack: it watches indexers, grabs releases through
qBittorrent and files them into the audio library Plex reads. Its chart is the September scaffold and
violates the chart contract in most of the ways that matter: unprefixed helpers, a hardcoded claim
name, no probes, no log level, a retired node label, scrape annotations typed by hand, and an
`appVersion` (`1.0.0`) that has never described the app.

**It has not run since the 2026 disk failure.** The pod has been `Pending` for 71 days with one
event: `FailedScheduling … persistentvolumeclaim "lidarr-config" not found`. The claim was Longhorn,
its contents are abandoned by decision, and there is nothing to migrate. This rebuild is a fresh
install: a new config volume, a new database, and a new API key minted by the app at first start.

## What the application needs

Facts read from upstream and the live cluster on 2026-11-13, not guessed:

| Fact | Source |
|---|---|
| Web UI listens on **8686** | live Service and container port; LinuxServer docs |
| State lives in `/config` (database, `config.xml`, logs) | image layout; live Deployment |
| The image starts as **root** and drops to `PUID`/`PGID` through s6 | LinuxServer docs, same init as sonarr/radarr |
| `GET /ping` is anonymous, returns `200 {"status":"OK"}`, and **reads the config repository**, so it fails when the database cannot be opened | `src/Lidarr.Http/Ping/PingController.cs` on `develop` |
| Log level comes from `LogOptions`, bound to `LIDARR__LOG__LEVEL` and `LIDARR__LOG__CONSOLELEVEL`; the default level is **`debug`** | `src/NzbDrone.Core/Configuration/ConfigFileProvider.cs` line 245 |
| `exportarr` supports `lidarr` (collector `internal/arr/collector/lidarr.go`, argument `lidarr`) | exportarr repository |
| Image on the cluster is `lscr.io/linuxserver/lidarr:3.1.0.4875-ls15`; current stable is **`3.1.6.5079-ls48`** (published 2026-11-11; GitHub release `v3.1.6.5079`, 2026-10-26) | live Deployment, Docker Hub tag list, GitHub releases |
| `/downloads` is `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner`; `/audio` is `pvc-nfs-audio-nfs-subdir-external-provisioner`; both read-write | live Deployment |
| The config claim `lidarr-config` **does not exist**, and no `lidarr` series exists in Prometheus over 90 days — the app has no measurement window | live PVC list, Prometheus `/api/v1/query` |
| Live route is `lidarr.kub.local` on the `web` entry point, no TLS, no middleware; the Service port is named `lidarr-webui` | live IngressRoute and Service |
| API-key Secret is `lidarr`, key `secret` | live Deployment, exporter env. Its existence and currency cannot be read with the read-only ServiceAccount |
| The chart carries `version: 0.1.0` and `appVersion: "1.0.0"`, a TLS/autoscaling block nothing renders, and `nodeAffinity` on the retired `node-type: general` label | chart files |

## Goals

1. Satisfy the chart contract: `app.*` helpers, every name derived, no tabs, ingress fully
   value-driven, `helm lint` and `helm template` clean.
2. Create the config claim from the chart (`lidarr-config`, 128Mi, `local-path`) so the app can
   start at all, and pin the pod to the node that claim lands on.
3. Pin the image to `3.1.6.5079-ls48` and set `appVersion` to the same string; chart `version: 1.0.0`.
4. Probes on `/ping`, log level at `warn` on both variables, the two free hardening settings.
5. Keep the exporter: one `exportarr` block renders the sidecar, its port, its target and its scrape
   annotations together, so the announced port cannot drift from the listener.
6. A chart README stating what differs from the reference and the known debt, and one row in
   `docs/charts/README.md`.
7. Add the `LIDARR_API_KEY` slot to `~/.config/secrets/homelab.env` in the file's existing style.

## Non-goals

- No change to `cluster/argocd/projects/media-stack/lidarr.yaml`.
- No data migration (the old claim does not exist) and no attempt to recover the abandoned volume.
- No public exposure, no TLS, no auth middleware: the app stays on `lidarr.kub.local`.
- No shared chart, no library chart.
- No change to the `nfs-audio` provisioner, whose Deployment carries a `podAffinity` to a `lidarr`
  pod. When Lidarr lands on `streaming`, that provisioner follows it; the NFS export is reachable
  from every node, so this costs nothing.

## Anti-goals

- Seeding `config.xml` with a chosen API key to make the exporter green on the first sync. It needs
  an extra object and a file the app rewrites; the operator pastes one key instead.
- Keeping the `node-type` affinity "because it is there". The label is retired; the pod would never
  schedule.
- Raising the log level to `info` "so the first scan is visible". The UI is the record for a scan;
  Loki is not.

## Approaches considered

**A. Minimal patch.** Create the missing claim, fix the node label, bump the image, add probes. Cheapest,
but leaves the hardcoded claim name, the dead TLS/autoscaling values, the hand-typed scrape
annotations and no README.

**B. Rebuild on the sonarr shape.** Sonarr is the closest precedent: a LinuxServer image that starts
as root and drops to `PUID`/`PGID`, plus an `exportarr` sidecar, a derived claim name and probes on
`/ping`. Lidarr gets the same shape with its own values; the two charts stay separate, as the repo
requires.

**Chosen: B.** The app's requirements are sonarr's requirements plus a second media mount and a
different app name. A patch would leave the chart as the copy it has always been.

## Values (shape)

```yaml
image:
  repository: lscr.io/linuxserver/lidarr
  tag: "3.1.6.5079-ls48"

strategy: Recreate

service:   { type: ClusterIP, port: 8686 }
container: { port: null }

ingressRoute:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  entryPoints: [web]
  host: lidarr.kub.local
  pathPrefix: /
  middlewares: []
  tls: { enabled: false }

storage  { enabled: true, existingClaim: "", className: local-path, size: 128Mi, mountPath: /config }
downloads { claim: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner, mountPath: /downloads, readOnly: false }
audio     { claim: pvc-nfs-audio-nfs-subdir-external-provisioner, mountPath: /audio, readOnly: false }

env:
  TZ: "Europe/Paris"
  PUID: "1100"
  PGID: "1100"
  LIDARR__LOG__LEVEL: "warn"
  LIDARR__LOG__CONSOLELEVEL: "warn"

exportarr:
  enabled: true
  image: { repository: ghcr.io/onedr0p/exportarr, tag: "v2.3.0" }
  port: 9707
  apiKey: { secretName: "", secretKey: secret }

probes:
  startup:   { enabled: true, path: /ping, periodSeconds: 5, failureThreshold: 30 }
  liveness:  { enabled: true, path: /ping, initialDelaySeconds: 20, periodSeconds: 30, failureThreshold: 3 }
  readiness: { enabled: true, path: /ping, initialDelaySeconds: 10, periodSeconds: 15, failureThreshold: 3 }

resources:
  requests: { cpu: 10m, memory: 256Mi }
  limits:   { memory: 1Gi }

nodeSelector:
  kubernetes.io/hostname: streaming
```

## Decisions

### Image: `lscr.io/linuxserver/lidarr`, tag `3.1.6.5079-ls48`

Same registry the cluster already pulls from, and `lscr.io` is named in the stabilisation design's
registry rule. Sonarr and Radarr sit on the GHCR mirror; this chart does not move registries for
consistency alone, because the tag was verified on `lscr.io` and could not be verified on GHCR within
the rate limit. The tag is the current stable build of the current stable release, `v3.1.6.5079`.

### Names

Release `lidarr` with the chart name inside it renders `lidarr` for the Deployment, Service,
ServiceAccount and IngressRoute, and `lidarr-config` for the claim. Nothing is renamed: the live
Deployment, Service and IngressRoute are updated in place, and the claim name is the one the old
chart referenced, so the new claim lands where the old one was expected.

### Fresh install, not a migration

The old claim is gone and its data is abandoned by decision. The first start creates an empty
`/config`, Lidarr runs its setup wizard, and the root folder is `/audio`. Indexers come from Prowlarr,
which needs Lidarr added as an application there once the key exists.

### The API key

Lidarr mints its key at first start, so there is no value to put in `homelab.env` today. The file gets
the block with an **empty value**, in the same shape as `SONARR_API_KEY`:

```
# Lidarr 3.1.6.5079 - Settings -> General -> API Key. Sent as the X-Api-Key header.
# API base: http://lidarr.kub.local/api/v1
# Used to restore the `lidarr` Secret so the exportarr sidecar can scrape it.
LIDARR_API_KEY=
```

After the first start the operator pastes the key into that line and patches the Secret:

```bash
source ~/.config/secrets/homelab.env
kubectl -n media-stack patch secret lidarr --type merge \
  -p "{\"data\":{\"secret\":\"$(printf %s "$LIDARR_API_KEY" | base64 -w0)\"}}"
```

The cost is stated plainly: until the paste, the exporter target is down. If the `lidarr` Secret does
not exist at all, the sidecar container fails to start and the pod reports 1/2 — the app container is
unaffected. The plan creates the Secret if it is absent.

### Probes

Startup, liveness and readiness on `/ping`. Readiness shares the liveness path on purpose, and here
the reason is stronger than in sonarr: `PingController` reads the configuration repository on every
call, so `/ping` answers 500 when the database is unreadable. That is a dependency-aware check, not a
duplicate of the process check. The API's `/health` endpoint is stronger still but needs the key,
which a probe cannot read.

### Logs

`LIDARR__LOG__LEVEL` and `LIDARR__LOG__CONSOLELEVEL` at `warn`. Both are set because one writes the
file and the other writes stdout, and Fluent Bit only sees stdout. Lidarr's default is `debug`, which
is the worst case in the stack: one line per indexer search and per track scan, unfiltered into Loki.

### Security context

`seccompProfile: RuntimeDefault` and `allowPrivilegeEscalation: false`. No `runAsNonRoot`, no
`runAsUser`, no `fsGroup`, and the capabilities are kept: the s6 init must start as root to prepare
`/config` and then drop to 1100, exactly as in sonarr, radarr and prowlarr. The claim is root-owned
on a hostPath-backed class, so a non-root UID would start and then fail on its first write.

### Resources

No measurement exists for Lidarr. The request follows sonarr's measured profile (`10m` CPU, `256Mi`);
the memory limit is `1Gi` rather than sonarr's `768Mi` because the first full scan of the music
library is the one operation this app can be expected to exceed a series library's working set, and
an OOM during a scan means starting it again. The README says the numbers are unmeasured and must be
replaced from Prometheus after a week. No CPU limit, as everywhere in this cluster.

### Exporter

One `exportarr` block: `enabled`, image `v2.3.0` (newest release), port 9707, API-key Secret `lidarr`
key `secret`, resources `56Mi`/`112Mi`. The sidecar's `URL` is built from `app.fullname` and
`service.port`, so it renders `http://lidarr:8686` from one place. The four scrape annotations are
rendered from the same block and appear nowhere else. The metrics port stays off the Service.

### Routing

One `ingressRoute` block with the API group in a value, `web` entry point, host `lidarr.kub.local`,
no TLS, no middleware. The route keeps its name, so ArgoCD updates the live object instead of
creating a second one.

## Documentation changes

- New `cluster/helm/media-stack/lidarr/README.md`: upstream links, what the values control, the fresh
  claim and why there is nothing to migrate, the `/ping` endpoint and why readiness shares it, the
  log-level choice, the exporter and its API-key dependency, and the setup steps after first start.
- `docs/charts/README.md`: add `lidarr` to the probes/hardening/UID table and one paragraph to "what
  good looks like".
- `~/.config/secrets/homelab.env`: the `LIDARR_API_KEY` block above, value empty.

## Verification

Local:

```bash
helm lint cluster/helm/media-stack/lidarr --namespace media-stack
helm template lidarr cluster/helm/media-stack/lidarr --namespace media-stack >/dev/null
grep -rnP '\t' cluster/helm/media-stack/lidarr/templates || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/lidarr/values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io\|node-type\|lidarr-webui\|0\.1\.0' \
  cluster/helm/media-stack/lidarr/templates cluster/helm/media-stack/lidarr/values.yaml \
  || echo "no leftovers"
grep -cE '^LIDARR_API_KEY=' ~/.config/secrets/homelab.env   # expect 1
```

After the merge, sync the Application by hand (auto-sync is off):

- Application `Synced`; `lidarr-config` `Bound`; pod on `streaming`.
- `curl -s http://lidarr.kub.local/ping` → `{"status":"OK"}`; pod `2/2` once the Secret holds a key,
  `1/2` until then.
- Prometheus target `container="lidarr-exporter"` is `up`, or `down` with the HTTP 500 that exportarr
  returns when Lidarr answers 401 — which points at the Secret, not the chart.
- Setup wizard: root folder `/audio`, quality profile, then Prowlarr → Settings → Apps → add Lidarr
  with `http://lidarr:8686` and the key.

## Risks

- **Fresh install.** The library must be rebuilt: root folder, quality profile, indexer sync from
  Prowlarr, and the qBittorrent category. Nothing in the chart can do that, and the README lists it.
- **The Secret may not exist or may be stale.** The read-only ServiceAccount cannot see it. Either
  way the exporter is the only thing affected; the plan creates the Secret when it is missing.
- **`1Gi` is a guess.** An OOM during the first scan shows as a restarted container, not as a slow
  scan; the README says to check Prometheus after a week.
- **One-way database migration** if a later version is rolled back. Rollback means a fresh claim, not
  a schema downgrade; the README says so.

## Left to the builder

- Exact template wording and README prose.
- Whether to split the work into chart / docs commits.
- Whether the `lidarr-config` claim needs an explicit `chown` — it should not, because the LinuxServer
  init prepares `/config` as root on every start.
