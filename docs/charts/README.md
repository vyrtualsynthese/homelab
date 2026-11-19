# Hand-maintained charts — the contract

> **In one line.** Every chart under `cluster/helm/` is hand-written against one contract, and
> `autobrr` is the worked example of what meeting it looks like.
>
> **Use it when** you are writing a chart, reviewing one against the list it has to satisfy, or
> looking for the reason a rule exists · **Verified** 2026-11-17 · **Kind:** living page

Most charts in `cluster/helm/` are written here rather than vendored. This page is the checklist
every one of them is expected to satisfy. [`cluster/helm/media-stack/autobrr`](../../cluster/helm/media-stack/autobrr)
is the reference implementation: a worked example of the principles and the contract below, not a
directory to copy byte for byte.

**Use `autobrr` as a reference for naming, labels, claim shape and probe honesty, then build the
chart the image actually needs.**

## Why a reference chart and not a shared library

A shared library chart would remove the duplication, but it would also mean every app's shape is
decided in one file, and any per-app peculiarity becomes a values key nobody remembers. The operator
maintains these charts directly and wants each app to keep its own peculiarities. A reference chart
gives a consistent starting point without coupling the apps.

The consequence is real and accepted: a fix to a shared principle must be applied 20-odd times. The
contract below is what makes that mechanical. Each chart is allowed to diverge in its values and its
templates where the image demands it; it is not allowed to diverge from the contract.

## The contract

### Required

| # | Requirement | Why |
|---|---|---|
| 1 | `Chart.yaml` has `name`, `description`, `version`, `appVersion`, and `appVersion` matches `image.tag` | Three numbers that disagree are worse than one number that is wrong |
| 2 | `image.tag` is pinned to an explicit version. Never `latest`, never empty | A rolling tag makes every deploy a different app |
| 3 | `strategy: Recreate` whenever the app has a config volume | A `ReadWriteOnce` claim cannot attach to two pods. Deployments only: a `CronJob` has no strategy field |
| 4 | `nodeSelector` pins the pod to one node, and the `local-path` claim lands there | There is no failover; pretending otherwise hides it |
| 5 | Config claim named `<fullname>-config`, `storageClassName: local-path`, `128Mi` unless there is a reason | Config is small; sizes in the GiB range are a guess |
| 6 | Configuration reaches the app by the rule in the next section | Indirection has to earn its keep |
| 7 | Every name goes through `app.fullname` / `app.selectorLabels` | A copied chart still saying `autobrr-config` is a bug waiting for a restart |
| 8 | Ingress is a Traefik `IngressRoute` with the API group in a value | This repo never uses `networking.k8s.io/v1`, and v3 needs a rename |
| 9 | Labels from `app.labels` on every object | Selectors and dashboards depend on them |
| 10 | No tabs, and no tab indentation in any template | Invisible in review, fatal in YAML |
| 11 | `helm lint` and `helm template <release> . --namespace <ns>` both pass | The only local test these charts have |
| 12 | A chart whose route carries the authentik gate sets `apiBypassMiddlewares` when the app authenticates `/api` itself | An API client sends a key, not a browser session, and cannot complete a sign-in redirect. `lan-only` is a boundary, not a credential |

### How configuration reaches the app

Three destinations, and the choice is not a matter of taste:

| Configuration | Where it goes | Why |
|---|---|---|
| A plain value | **Inline `env:` in the Deployment** | Changing it changes the pod template, so the Deployment rolls by itself. No second object, no `checksum/config` annotation to fake the rollout |
| A secret | **`extraEnv` with `valueFrom.secretKeyRef`**, or `envFrom.secretRef` for a bulk dump | Never in the plain map, so it cannot be pasted in by accident |
| A file the app reads, such as `config.yaml` | **A ConfigMap mounted as a volume**, or a **Secret** when the file contains credentials | Only here does the extra object earn its place: the app needs a file on disk, not a variable. A `subPath` mount does not update on change, so the chart README must say a restart is required |

A ConfigMap used to inject plain variables is a defect, not a style choice. It adds an object that
can be missing, it hides the values from `kubectl describe pod`, and `envFrom` silently skips keys
that are not valid environment-variable names. If a rollout is only correct because a
`checksum/config` annotation was added, the ConfigMap was the wrong tool.

### Required once the app needs it

| # | Requirement | Why |
|---|---|---|
| 12 | A liveness probe on the app's own health endpoint, not on `/` | `/` often returns 200 with a broken backend |
| 13 | A readiness probe that fails when a dependency is down, and **not** the same endpoint as liveness | Sharing them turns a database blip into a restart loop. If the app has no dependency that can fail while the process stays up, sharing the endpoint is fine — record why in the chart README |
| 14 | `resources.requests.memory` and `limits.memory` on every container | Without a limit one app can evict a node |
| 15 | `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault` | Free, and breaks nothing that does not need privileges |
| 16 | Extra volumes with an explicit `mountPath` under `/srv/` or the app's own path | Keeps the NFS-media convention readable |
| 17 | If the image ships a Prometheus exporter, it is enabled and scraped (see **Metrics**) | An exporter that is off is an observability hole nobody notices until the incident |
| 18 | The app logs at `warning` or above, not at `info` (see **Logs**) | Fluent Bit ships every line to Loki with no filter; `info` is thousands of lines a day nobody reads |
| 19 | A non-public route references `kube-system-lan-only@kubernetescrd`; a public one does not | The internet reaches every route by hostname, so the allowlist is the only thing that keeps internal apps internal |

Two things live outside this page because they change for reasons the contract does not:
[chart sizing](../reference/chart-sizing.md) records which charts have been measured against Prometheus
and what each number became, and [chart notes](../reference/chart-notes.md) is the per-app record of how
each chart meets the list above and where it had to diverge.

### Metrics

**An app that ships a Prometheus exporter uses it.** Prometheus in this cluster discovers pods by
annotation (`kubernetes-pods` job, no ServiceMonitor, no Operator), so the chart has to render all
four:

| Annotation | Value |
|---|---|
| `prometheus.io/scrape` | `"true"` |
| `prometheus.io/scheme` | `http` |
| `prometheus.io/port` | the exporter's own port |
| `prometheus.io/path` | `/metrics`, unless the app serves metrics elsewhere |
| `prometheus.io/scrape_slow` | `"true"` as well, when nothing reads the numbers inside a minute |

**`scrape_slow` moves the target to the 5-minute job.** The fast job refuses any pod carrying it, so
both annotations are set: the port and path still live in one block, and the cadence is the only
thing that changes. Use it when every reader is a table or a one-hour increase — queue depth, library
counts, per-app totals — and not when a panel shows "now". The six exportarr sidecars
(`sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`, `lidarr`, `prowlarr`), `autobrr` and
`flaresolverr` do this since 2026-11-18.

Rules that came out of `flaresolverr`:

- **Turn the listener and the annotations on together.** Where the chart owns its templates, render
  both from one values block (`flaresolverr`'s `metrics`). Where it does not — a wrapper around a
  third-party chart — `podAnnotations` is the only knob, and the exporter may still need enabling in
  the chart's own values. Two independent edits are how the announced port stops matching the
  listener, and the failure is silent: the pod is scraped on a port nothing listens to and the target
  is simply down.
- **The metrics port stays off the Service.** Prometheus scrapes the pod IP; a second Service port
  only widens the app's surface for nothing. Exception: when a chart ships a dedicated metrics
  Service of its own, the `kubernetes-service-endpoints` job scrapes that instead — still one
  annotation pair, just on the Service.
- **If the chart renders the annotations itself, do not also list them in `podAnnotations`.** A
  second copy is how the port and the path end up disagreeing. The reference chart predates its
  `metrics` block and does it in two places (`podAnnotations` plus the exporter's variables in
  `env`); that works because the port appears twice in one file and gets reviewed together.
- **No exporter in the image?** Use a maintained sidecar if one exists (`exportarr` for the *arr
  apps, `qbittorrent-exporter`, `mktxp` for MikroTik); never write one. An app with neither gets
  nothing — silence is better than a metrics endpoint nobody maintains.

### Logs

**Log at warnings and errors, not at `info`.** Fluent Bit tails every container on every node and
ships every line to Loki unfiltered: there is no collection-side filter to fix it afterwards. So the
app's own level is the only place the volume can be cut, and a chart that leaves it at the default is
a chart that fills Loki with request lines nobody reads and buries the two lines that matter when
something breaks.

- Set the app's level to `warning` (or `error` where `warning` is pure noise) in `env`, never in a
  ConfigMap. Inline, so the rollout follows the change.
- **Two exceptions, both temporary:** while debugging a live problem, and while a new app is being
  set up on the cluster. Raise the level for those, then put it back to `warning` once the app
  behaves. A chart that merges at `info` is a regression, whatever the reason it was raised for.
- **Keep the failures.** Startup failures and the errors that stop a request from being served must
  still be visible at `warning`. If an app only reports them at `info`, say so in its README and pick
  `error` deliberately — never leave the level at `info` just to preserve them.
- **Whatever the level hides must be countable elsewhere:** a Prometheus counter, the app's own
  database, its UI. If there is no other record of an outcome, log that outcome at `warning` rather
  than dropping it silently.
- **When the app offers no knob, say so where the knob would go.** Write the answer next to `env` in
  the chart's values and explain it in the chart's README: what the default level is, what it costs in
  Loki, and which other record carries the outcome. `bazarr`, `kavita`, `plex`, `qbittorrent`,
  `tautulli`, `calibre-web-automated` and `cross-seed` are the seven that carry it today. Silence in
  both places reads as an oversight, and the next reader adds a variable that does nothing.

`flaresolverr` is the first chart to follow this: `LOG_LEVEL: warning`, with the counters carrying
the challenge outcomes the log no longer prints.

Readiness applies when the app serves traffic or exposes a dependency-aware endpoint. If the image
has no unauthenticated endpoint that reports dependency health, do not point readiness at the
liveness path just to fill the field: disable readiness and say why in the chart README. `cross-seed`
is the worked example.

This narrows requirement 13 rather than waiving it: 13 still applies whenever the app serves traffic
or exposes an endpoint that reports dependency health, and steps aside only for a chart like
`cross-seed` that does neither.

A batch workload is the other exception, and it is a stronger one: a `CronJob` has no long-running
process and no listener, so a probe has nothing to check. The Job's exit status and `backoffLimit` are
the health signal, and the run's logs are the record. `recyclarr` is the worked example.

### Changing the UID breaks the config volume

**This is the trap in this repo.** `local-path` is hostPath-backed, and the kubelet does **not**
apply `fsGroup` ownership to hostPath volumes. So:

- Setting `runAsUser` on a pod whose config volume was written as root makes `/config` unreadable and
  the app fails to start.
- Setting `fsGroup` looks correct in review and does nothing at all.

Before a chart moves to a non-root UID: read the current UID and the ownership of the volume first,
then either `chown` the directory on the node or start from a fresh claim. Never flip the UID on a
chart whose claim already holds data.

The procedure that worked for `autobrr`, in order: scale the deployment to zero, resolve the claim to
its PV and the PV to its hostPath, `chown -R 1100:1100` that directory over SSH on the node, then set
`runAsUser`, `runAsGroup` and `runAsNonRoot`. Scale back up and confirm the app can still write its
own state — a pod that starts and then fails on a write is the failure this order avoids.

**New volumes now arrive writable by any UID.** The `local-path` chart sets `userPattern` and
`groupPattern` to `1100` and leaves `permPattern` empty, so the provisioner's setup script creates
each volume directory `0777` and chowns it to `1100:1100`. A chart running as 1100 owns its claim; a
chart running as another UID can still write, but the files it creates belong to that UID. Setting
`permPattern` — `0750`, say — would break the non-owner charts first, and `maintainerr` (UID 1000) is
the worked example. Claims provisioned before that change are still root-owned, which is why the
table below records a manual `chown` for `autobrr` and `cross-seed`.

**A fresh claim binds to the node the *old* pod made eligible.** `local-path` is
`WaitForFirstConsumer`, so a claim binds to whichever node the scheduler picks for a pod that uses it —
and during a chart rebuild the old pod is still a candidate. Lidarr, 2026-11-13: the sync created
`lidarr-config` while the old pod still carried the retired `node-type: general` affinity, which made
`nuc0` eligible; the claim bound there, the sync then replaced the pod with one pinned to `streaming`,
and the claim was welded to a node the pod could never use. The pod stayed `Pending` with
`1 node(s) didn't find available persistent volumes to bind`.

Two habits follow:

- Before the first sync of a chart that creates a new claim and moves the pod to a different node,
  scale the old Deployment to zero or delete its pod, so the only candidate is the new pod.
- Recovery, if the claim already bound to the wrong node: delete the PVC (it holds nothing on a fresh
  install), let the Application recreate it, then **delete the pod**. Deleting the claim alone is not
  enough — the pod sits in the scheduler's unschedulable queue keyed on the old claim's UID and never
  gets a fresh pass. A PV with `Retain` policy stays behind as `Released`; delete it and its directory.

| Chart | Probes | Hardening | Non-root UID |
|---|---|---|---|
| `autobrr` | yes | capabilities dropped, seccomp, no privilege escalation | **yes — 1100**, claim chowned first |
| `flaresolverr` | yes, on `/health` | capabilities dropped, seccomp, no privilege escalation | **yes — 1000**, read from the image, no claim to chown |
| `cross-seed` | liveness only | capabilities dropped, seccomp, no privilege escalation, no API token | **yes — 1100**, claim chowned first |
| `prowlarr` | yes, on `/ping` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100** |
| `sonarr` | yes, on `/ping` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100** |
| `sonarr-anime` | yes, on `/ping` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100** |
| `recyclarr` | n/a (batch job) | capabilities dropped, seccomp, no privilege escalation, no API token | **yes — 1000**, the image's default and the claim's existing owner |
| `tautulli` | yes, on `/status` | capabilities dropped except `CHOWN`/`SETUID`/`SETGID`, which the image's root init needs; seccomp; no privilege escalation | **no — the image starts as root, then drops to 1100** |
| `radarr` | yes, on `/ping` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100** |
| `radarr-anime` | yes, on `/ping` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100**; `PUID` moved from 0 |
| `lidarr` | yes, on `/ping` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100** |
| `bazarr` | yes, on `/` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100** |
| `bazarr-anime` | yes, on `/` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100** |
| `unpackerr` | liveness only, on `/` | capabilities dropped, seccomp, no privilege escalation, no API token | **yes — 1100**, no claim to chown |
| `maintainerr` | yes, `/api/health/live` and `/api/health/ready` | capabilities dropped, seccomp, no privilege escalation, no API token | **yes — 1000**, the image's own UID; the claim is `0777` and owned by 1100 |
| `calibre-web-automated` | startup and liveness only, both on `/` | capabilities dropped except `CHOWN`/`SETUID`/`SETGID`, which the LinuxServer init needs; seccomp; no privilege escalation; no API token; **no** read-only root filesystem — the image writes notices into `/app` | **no — the image starts as root, then drops to 1100**; config claim `10Gi`, not `128Mi` |
| `kavita` | startup and liveness only, both on `/api/health` | capabilities dropped except `CHOWN`/`SETUID`/`SETGID`, which the LinuxServer init needs; seccomp; no privilege escalation; no API token; read-only root filesystem with `emptyDir` at `/run` and `/tmp`; public TLS route | **no — the image starts as root, then drops to 1100** |
| `plex` | yes, on `/identity` | capabilities dropped except `CHOWN`/`SETUID`/`SETGID`, which the image's root init needs; seccomp; no privilege escalation | **no — the image starts as root, then drops to 1100** |
| `koillection` | startup and liveness only, both on `/` — the image's own `HEALTHCHECK` target; readiness off, no endpoint reports database health | capabilities dropped except `CHOWN`/`SETUID`/`SETGID`, which the image's root init needs; seccomp; no privilege escalation | **no — the image starts as root, then drops to 1100**; uploads claim `20Gi` on `stash-nfs`, config claim `1Gi` on `local-path` |
| `seerr` | yes, on `/api/v1/status/appdata` | capabilities dropped, seccomp, no privilege escalation | **yes — 1000**, the image's own UID |
| `qbittorrent-exporter` | n/a (no HTTP listener of its own) | capabilities dropped, seccomp, no privilege escalation | **no — no `runAsUser` set** |
| `ashuvidz-website` | liveness on `/healthz`, a `return 200` in the chart's own `nginx.conf`; readiness on `/`, which is the site's `index.html` | capabilities dropped, seccomp, no privilege escalation, read-only root filesystem; `/site` mounted read-only into nginx and dotfiles denied | **yes — 101**, the `nginx-unprivileged` image's own UID; no claim (the content is an `emptyDir` clone) |
| `freshrss` | startup and liveness on a **TCP connect** to the WebUI port; readiness on `/i/`, which boots PHP and reads the user table | capabilities dropped except `CHOWN`/`SETUID`/`SETGID`, which the s6 init needs; seccomp; no privilege escalation | **no — the image starts as root, then drops to 1100**; config claim `1Gi`, not `128Mi` |
| `qbittorrent` | liveness only, a TCP connect to the WebUI port — there is no unauthenticated HTTP endpoint to ask; readiness off | pod seccomp `RuntimeDefault` and `allowPrivilegeEscalation: false` on the app; the WireGuard sidecar is `privileged`, adds `NET_ADMIN`/`SYS_MODULE` and sets seccomp `Unconfined` for `/dev/net/tun` | **no — the image starts as root, then drops to 1100** |

## Checking a chart against this list

```bash
cd cluster/helm/<area>/<app>
helm lint . --namespace <ns>
helm template <release> . --namespace <ns> | kubeconform -strict -summary
hack/check-exposure.sh --only cluster/helm/<area>/<app>   # non-public routes carry the LAN gate

# the checks that grep can still do
grep -rnP '\t' templates/ || echo "no tabs"
grep -rn 'latest' values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io' templates/ || echo "no core Ingress"
grep -rn 'autobrr\|<previous app name>' templates/ values.yaml || echo "no copy-paste leftovers"
grep -rn 'prometheus.io/scrape' templates/ || echo "no scrape annotation — does the image ship an exporter?"
grep -rniE 'log_?level' values.yaml || echo "no log level — what does the image default to, and is it info?"
```

Copy-paste leftovers are still the most common defect: every chart in this repo started from another
one. Grep for the original app's name and for its storage names before you commit, and check that the
claim name is derived rather than hardcoded.

The helpers are unprefixed (`app.fullname`, `app.labels`, `app.selectorLabels`) so a chart can be
rebuilt from the reference without a rename sweep. The trade-off still stands: do not use two of
these charts as subcharts of the same parent, or the helper names collide.
