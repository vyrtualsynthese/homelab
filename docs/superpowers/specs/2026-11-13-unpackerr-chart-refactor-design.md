# unpackerr chart refactor — design

- Date: 2026-11-13
- Status: awaiting approval
- Scope: `cluster/helm/media-stack/unpackerr`, `docs/charts/README.md`, and a new chart README.

## Intent

The unpackerr chart is still the `helm create` scaffold from the September migration. It holds four
live *arr API keys in plaintext, advertises a Prometheus port nothing listens on, runs as root inside
a media stack where every other app is UID 1100, runs `BestEffort` with no requests or limits, and
carries a `service`, `ingress` and `autoscaling` block that no template renders.

This refactor rebuilds it around what unpackerr actually needs, using the repo chart contract and the
autobrr/cross-seed charts as references for principles, not as files to copy. It also carries the
image from `0.16.1` to `v1.0.1`, because the chart's shape depends on v1: 0.16.x has no page at `/`
and no web UI, so a liveness probe and a `noauth` UI mode only make sense once the image is v1.

## What the application needs

Facts taken from the image, the upstream source and the live pod, not guessed:

| Fact | Source |
|---|---|
| The live pod runs `golift/unpackerr:0.16.1`; the bump to `v1.0.1` exists only on the unmerged branch `renovate/golift-unpackerr-1.x` | live pod spec; `git branch --contains 1a21f2fb` |
| 0.16.x's web server "currently only supports metrics": no UI, no login, no page at `/` | upstream docs at v0.16.1 |
| v1.0.0 (Oct 2026) is a rewrite: config sections became map keys (`[sonarr.0]`, not `[[sonarr]]`), a web UI with local login was added, and `/metrics` moved behind API keys | release notes, `examples/unpackerr.conf.example` |
| Environment variables still configure everything: `UN_SONARR_0_URL`, `UN_SONARR_0_API_KEY`, `UN_SONARR_0_PATHS_0` | `examples/MANUAL.md`, `pkg/unpackerr/cnfgfile_test.go` |
| The default `protocols` value covers both the legacy `torrent` string and the newer `TorrentDownloadProtocol` | `examples/unpackerr.conf.example` |
| Radarr items are currently retrieved with `protocols=torrent`, so the explicit list is not filtering anything today | live logs, 2026-11-13 |
| In v1 the HTTP server listens on `0.0.0.0:5656` by default and serves the UI at `/`, outside the auth wrappers | `pkg/unpackerr/webserver.go`, `serveUI` |
| An empty `ui_password` makes the app **generate a temporary password at every start** and print it through the log | `pkg/unpackerr/password.go`, `setupUIPassword` |
| `ui_password = "noauth"` is a supported mode and suppresses password generation | same file, `CryptPass.Noauth` / `Webauth` |
| `/metrics` requires an API key with `system:metrics:read`; the annotation-based Prometheus job cannot send one | `pkg/unpackerr/webserver.go`, `requirePermHTTP(PermReadSystemMetrics, ...)` |
| There is no unauthenticated health endpoint; every `/api/*` route is behind a permission check | `pkg/unpackerr/api.go` |
| The official image declares no `USER`, so it runs as root | `init/docker/Dockerfile.goreleaser` |
| No log file is written when no `log_file` is set and the binary has no GUI | `pkg/unpackerr/cnfgfile.go:207` (`ui.HasGUI()` guard) |
| `log_files` / `log_file_mb` only size a log file, so they are inert without one | same file |
| `activity = true` silences queue summary lines that report only zeros | `examples/unpackerr.conf.example` |
| Latest release is `v1.0.1` (29 Oct 2026) | GitHub releases |
| The live pod has **no** resources, so its QoS class is `BestEffort`, and it has 13 restarts in 60 days | live pod spec |
| Live idle usage is **15 MiB** memory and **~0.02m** CPU | `kubectl top pod`, 2026-11-13 |
| Live logs are dominated by one `Status:` line per tracked item per minute — about 13k lines a day, mostly "Imported, elapsed: 27m" | `kubectl logs --tail`, 2026-11-13 |
| The download export is qBittorrent's NFS claim, already mounted at `/downloads` by the *arr apps | `cluster/helm/media-stack/qbittorrent/templates/deployment.yaml` |

The chart currently announces `prometheus.io/scrape: "true"` on port 5656 while the image runs
0.16.1 with `UN_WEBSERVER_METRICS=false`. `docs/observability-conventions.md` lists `unpackerr` among
the seven dead media-stack targets; that annotation is one of them.

## Goals

1. Stop storing credentials in the chart. Every API key comes from the existing Secret that the
   matching app already owns (`sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`, key `secret`).
2. Remove the observability lie: no scrape annotation, no declared metrics port.
3. Run as UID/GID 1100 like the rest of media-stack, so extracted files are owned by the same user
   as the download client and the importing app.
4. Give the pod real requests and limits so it stops being `BestEffort` — the eviction-first class.
5. Follow the chart contract: `app.*` helpers, values-driven environment, claim name in values,
   liveness probe, memory requests and limits, no tabs.
6. Carry the image from `0.16.1` to `v1.0.1` and make `appVersion` match, because the chart's UI and
   probe decisions require v1.
7. Give the chart a README that records the UI mode, the probe decision, the metrics decision, the
   log-volume exception and the key-rotation debt.

## Non-goals

- No Service, no IngressRoute, no new DNS name. The UI stays reachable by port-forward only.
- No Prometheus scrape config change; metrics stay off here (see Decisions).
- No key rotation in this branch. The keys are in git history and must be rotated in the four *arr
  apps; that is recorded as a required handover step, not performed here.
- No Lidarr instance. The old template had a commented-out Lidarr block with a key in
  it; it is removed and the README says how to add one.
- No change to `cluster/argocd/projects/media-stack/unpackerr.yaml`.
- No edit to the dated health snapshot in `docs/observability-conventions.md`.

## Anti-goals

- A scrape annotation the app cannot satisfy.
- A generated password per restart landing in Loki.
- `noauth` plus a Service: that would publish an unauthenticated management API at a stable
  cluster address.
- Dropping the API keys into a ConfigMap or into `envFrom` over a shared Secret, which would hide
  which key belongs to which instance.
- Copying autobrr byte for byte and compensating with raw pod spec in values.

## Approaches considered

**A. Per-instance flat keys.** `instances: { sonarr0: {...}, sonarr1: {...}, radarr0: {...}, ... }`.
The key names the app and the index that appears in the environment variable. The Deployment makes
one explicit include call per instance. Adding an instance is a values block plus one line in the
Deployment.

**B. One list per app type** (`sonarr: [ ... ]`, `radarr: [ ... ]`), rendered by a single helper into
`UN_SONARR_<i>_*`. Less repetition, but the index becomes an implementation detail of the range.

**C. One `instances:` list with an `app:` field.** Cleanest values block, but the template has to
carry a separate counter per app type to produce the right index.

**Chosen: A.** The operator picked explicit per-instance keys. The index is visible in the values
and in the render, which makes a mis-numbered instance obvious in review, and the instance blocks
stay readable next to each other. The cost — one include line per instance in the Deployment — is
paid four times and documented in the chart README.

## Values (shape)

```yaml
image:
  repository: golift/unpackerr
  tag: "v1.0.1"
  pullPolicy: IfNotPresent

strategy: Recreate

serviceAccount:
  create: true
  automount: false

env:
  TZ: "Europe/Paris"
  UN_ACTIVITY: "true"
  UN_INTERVAL: "1m"
  UN_START_DELAY: "1m"
  UN_RETRY_DELAY: "5m"
  UN_MAX_RETRIES: "10"
  UN_PARALLEL: "1"
  UN_FILE_MODE: "0644"
  UN_DIR_MODE: "0755"
  UN_WEBSERVER_UI_PASSWORD: "noauth"

instances:
  sonarr0:
    url: "http://sonarr:8989"
    apiKeySecret: { name: sonarr, key: secret }
    paths: [/downloads]
    protocols: "torrent,TorrentDownloadProtocol"
    timeout: 1m
    deleteOrig: false
    deleteDelay: 30m
  # sonarr1, radarr0, radarr1 likewise

downloads:
  existingClaim: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner
  mountPath: /downloads
  readOnly: false

extraEnv: []
envFrom: []
volumes: []
volumeMounts: []

nodeSelector:
  kubernetes.io/hostname: streaming

resources:
  requests:
    cpu: 10m
    memory: 64Mi
  limits:
    memory: 512Mi

probes:
  liveness:
    enabled: true
    path: /
    port: http
    initialDelaySeconds: 20
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    enabled: false
```

## Decisions

### Image upgrade is part of this change

`tag: v1.0.1`, `appVersion: "v1.0.1"`. The chart rebuilt here assumes v1: a page at `/` to probe,
and a `ui_password` mode to set. Building it against 0.16.1 would mean shipping a probe that cannot
pass, then reworking the chart again when the Renovate branch merged.

The env-var surface is unchanged between the two versions, so the upgrade does not need a config
migration; the config-file format change in v1 does not apply because this chart uses env only.
`renovate/golift-unpackerr-1.x` becomes redundant and can be closed once this merges.

Rollback is a one-line revert of `image.tag` if v1 misbehaves: the env contract is the same, so the
old image starts from the same values.

### Instances and protocols

Each instance renders seven variables: `URL`, `API_KEY` (from `secretKeyRef`), `PATHS_<n>`,
`PROTOCOLS`, `TIMEOUT`, `DELETE_ORIG`, `DELETE_DELAY`. The values keep the current non-secret
behaviour (`/downloads`, `1m` timeout, `delete_orig: false`, `30m` delay).

`protocols` becomes `torrent,TorrentDownloadProtocol`, upstream's default. This is
forward-compatibility, not a bug fix: the live logs show Radarr items being retrieved with
`protocols=torrent` today, so nothing is being filtered right now. The union costs nothing and keeps
working when an *arr release starts reporting the newer class name.

### Secrets

`apiKeySecret` is a `name`/`key` pair, rendered as `valueFrom.secretKeyRef`. The four Secrets already
exist and already hold these keys for the exportarr sidecars, so nothing new is created. Keeping the
key explicit per instance means a wrong or missing Secret fails visibly on that instance instead of
silently blanking all of them, which is what `envFrom` over one Secret would do.

The four keys are in git history. They are still valid, so this chart change does not rotate them;
the README carries the rotation as required handover work.

### Metrics off, annotation gone

`/metrics` returns 403 without an API key holding `system:metrics:read`, and the cluster's
`kubernetes-pods` job scrapes by annotation with no credentials. Enabling the exporter would
therefore need a chart-created key plus a Prometheus scrape config that sends it — two changes
outside this chart's remit today, and the observability work is deliberately deferred. The honest
state is: no listener, no port, no annotation, and the README records what enabling it would take.

The dated snapshot in `docs/observability-conventions.md` is left alone. It is a health record for
2026-11-12, and rewriting history in it would hide the fact that seven targets were down then. What
changes is the live state: with the annotation gone, there is no target to be down.

### Web UI: on, unauthenticated, not published

The HTTP server is the app's only probe target, so it stays enabled. `ui_password` is set to
`noauth`:

- leaving it empty makes the app generate a random password at every start and print it, which puts
  a credential in Loki on every restart;
- a literal password in `values.yaml` is a secret in git, which is the defect being removed;
- `noauth` matches how the rest of media-stack runs (Sonarr, Radarr and qBittorrent all expose
  unauthenticated UIs on ClusterIP Services).

The chart renders no Service, so the API is reachable only by pod IP and by `kubectl port-forward`.
That is stricter than the apps it sits next to. Putting a Service or a route in front of it is
explicitly out of scope, because that would publish an unauthenticated management API at a stable
address.

### Probes

Liveness is `GET /` — in v1 the SPA root, served outside the auth wrappers, returns 200 while the
process serves. The contract prefers an app health endpoint over `/`; unpackerr has none, since every
`/api/*` route needs a permission. The README records that, and records that `/` is a process check
rather than a dependency check.

Readiness is disabled. There is no unauthenticated endpoint that reports dependency health, and with
no Service there is no traffic to gate. Duplicating liveness into readiness would add a restart
trigger with no benefit.

### Logs

unpackerr has no log level. v1 exposes `debug` and `quiet`, nothing in between, so the contract's
"log at warning" rule cannot be met by configuration. `quiet` is not an option: it silences stdout
entirely, including the failures that must survive.

What the chart does set is `UN_ACTIVITY: "true"`, which drops the queue summary lines that report
only zeros. What remains is one `Status:` line per tracked item per minute, measured at roughly 13k
lines a day, and that cannot be turned off without silencing everything.

The contract's condition for that kind of exception applies here: whatever the level hides must be
countable elsewhere. In v1 the queue API and the UI carry per-item state, and the history file
carries completed and failed items. The README records the volume, the reason, and that raising the
noise floor is a v2 wish, not a chart setting.

### Security and the UID

`runAsNonRoot`, `runAsUser`/`runAsGroup` 1100, `allowPrivilegeEscalation: false`, all capabilities
dropped, `seccompProfile: RuntimeDefault`, ServiceAccount token not mounted. The image is a static
Go binary on Alpine and writes only to `/downloads`, which the NFS export already lets UID 1100
write — qBittorrent writes there as 1100 today.

No `chown` step is needed for this chart: it creates no claim. Existing extracted files written by
the root-running pod are root-owned; unpackerr can read them (0644) and the parent directories are
writable by 1100, so leftovers are removable. `delete_orig` is false, so nothing depends on
rewriting those directories. The README notes that any directory it cannot clean is a leftover to
remove by hand.

### Resources and strategy

`strategy: Recreate` with one replica. Not for a claim — the download export is NFS and supports
multiple mounts — but because two pods scanning the same queue would extract the same archive twice
into the same directory.

Measured idle is 15 MiB and about 0.02m CPU, so the defaults are request `10m` CPU and `64Mi` memory,
limit `512Mi` memory, no CPU limit. The request covers idle plus margin; the limit is headroom for
the extraction children, which are the real peak and were not running when the measurement was
taken. The README says to confirm with `kubectl top pod` during a burst and adjust.

Any non-zero request also moves the pod from `BestEffort` to `Burstable`. That is a real change: the
current pod has 13 restarts in 60 days and, as `BestEffort`, is the first thing evicted when
`streaming` comes under memory pressure.

### Removals

From `values.yaml`: `replicaCount` (hardcoded 1 in the template), `service`, `ingress`, `autoscaling`,
`podSecurityContext`/`securityContext` placeholders, and the `podAnnotations` that only carried the
fake scrape pair and `setGateway` (a marker nothing in this repo reads, on a pod that has neither a
Service nor a route to consume it).

From `templates/deployment.yaml`: the four API keys, the commented-out Lidarr block, the hardcoded
claim name, the hardcoded `replicas: 1`, and the inert `UN_LOG_FILES` / `UN_LOG_FILE_MB`.

## Documentation changes

- New `cluster/helm/media-stack/unpackerr/README.md`: upstream links, values table, why the keys come
  from Secrets, why metrics are off, why the UI is `noauth` and unpublished, why readiness is off,
  the log-volume exception, how to add a Lidarr instance, the resource-sizing note, the v1 upgrade
  and its rollback, and the key-rotation debt.
- `docs/charts/README.md`: add `unpackerr` to the hardening table and to "What good looks like", as
  the worked example of per-instance values and of metrics deliberately left off.

## Verification

```bash
helm lint cluster/helm/media-stack/unpackerr --namespace media-stack
helm template unpackerr cluster/helm/media-stack/unpackerr --namespace media-stack > /tmp/unpackerr-after.yaml

grep -q 'image: "golift/unpackerr:v1.0.1"' /tmp/unpackerr-after.yaml
[ "$(grep -c 'secretKeyRef' /tmp/unpackerr-after.yaml)" = 4 ]
! grep -qE 'UN_(SONARR|RADARR)_[0-9]+_API_KEY"?' /tmp/unpackerr-after.yaml | head -1
grep -q 'TorrentDownloadProtocol' /tmp/unpackerr-after.yaml
! grep -q 'prometheus.io/scrape' /tmp/unpackerr-after.yaml
grep -q 'runAsUser: 1100' /tmp/unpackerr-after.yaml
grep -q 'claimName: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner' /tmp/unpackerr-after.yaml
grep -q 'memory: 64Mi' /tmp/unpackerr-after.yaml
grep -q 'memory: 512Mi' /tmp/unpackerr-after.yaml
! grep -q 'readinessProbe' /tmp/unpackerr-after.yaml
! grep -q 'UN_WEBSERVER_METRICS: "true"' /tmp/unpackerr-after.yaml
! grep -rnP '\t' cluster/helm/media-stack/unpackerr/templates
! grep -rn 'latest' cluster/helm/media-stack/unpackerr/values.yaml
```

The "no literal keys" check has to look at the rendered env values, not at names: every API key must
appear only as a `secretKeyRef` name, never as a `value`. The plan spells out a concrete grep for it.

Plus the repo-wide render loop and the ArgoCD path checks from `AGENTS.md` §6, whose only expected
output on this branch is the two pre-existing `nfs` breakages.

After the merge, sync the Application manually (auto-sync is off) and check:

- the pod starts on `v1.0.1` as UID 1100 and stays `Running`;
- the liveness probe passes and no generated-password line appears in the logs;
- the QoS class is `Burstable` and the queue is still being polled (four `Updated (...)` lines a
  cycle);
- extractions still complete end to end on Sonarr, and the v1 UI answers on a port-forward;
- `kubectl top pod` during an extraction burst, to check the 512Mi limit.

## Risks

- **v1 is an app rewrite.** Extraction behaviour, defaults (`remnant_action`, `keep_history`) and the
  log format change, and the UI is new. The chart does not depend on any of it beyond `/` and
  `ui_password`, and rollback is a one-line tag revert, but the first rollout deserves watching.
- **`noauth` on the pod IP.** Any pod in the namespace can reconfigure unpackerr. Accepted because
  there is no Service and the alternative is a generated credential in Loki; it is recorded in the
  README so a future Service addition has to re-open the question.
- **UID change.** Existing root-created files stay root-owned. The chart only reads them and the
  directories stay writable by 1100, but a directory unpackerr cannot clean is a manual fix.
- **Unmeasured memory peak.** `512Mi` is headroom, not a measurement: the idle figure was taken while
  nothing was extracting. The README says to check `kubectl top` during a burst.
- **Keys still valid.** Removing them from the chart does not remove them from git history. Rotation
  is required, tracked in the README and the PR body.

## Left to the builder

- Exact template and helper contents.
- README wording.
- Commit split (image bump, chart, docs) once the plan exists.
