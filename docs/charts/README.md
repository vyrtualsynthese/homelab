# Hand-maintained charts — the contract

Most charts in `cluster/helm/` are written here rather than vendored. This page is the checklist
every one of them is expected to satisfy, and
[`cluster/helm/media-stack/autobrr`](../../cluster/helm/media-stack/autobrr) is the reference
implementation to copy.

**Copy `autobrr`, change the four values that identify the app, and do not touch `templates/`.**

## Why a reference chart and not a shared library

A shared library chart would remove the duplication, but it would also mean every app's shape is
decided in one file, and any per-app peculiarity becomes a values key nobody remembers. The operator
maintains these charts directly and wants each app to keep its own peculiarities. A reference chart
gives a consistent starting point without coupling the apps.

The consequence is real and accepted: a fix to the shared shape must be applied 20-odd times. The
checklist below is what makes that mechanical instead of archaeological.

## The contract

### Required

| # | Requirement | Why |
|---|---|---|
| 1 | `Chart.yaml` has `name`, `description`, `version`, `appVersion`, and `appVersion` matches `image.tag` | Three numbers that disagree are worse than one number that is wrong |
| 2 | `image.tag` is pinned to an explicit version. Never `latest`, never empty | A rolling tag makes every deploy a different app |
| 3 | `strategy: Recreate` whenever the app has a config volume | A `ReadWriteOnce` claim cannot attach to two pods |
| 4 | `nodeSelector` pins the pod to one node, and the `local-path` claim lands there | There is no failover; pretending otherwise hides it |
| 5 | Config claim named `<fullname>-config`, `storageClassName: local-path`, `128Mi` unless there is a reason | Config is small; sizes in the GiB range are a guess |
| 6 | Configuration reaches the app by the rule in the next section | Indirection has to earn its keep |
| 7 | Every name goes through `app.fullname` / `app.selectorLabels` | A copied chart still saying `autobrr-config` is a bug waiting for a restart |
| 8 | Ingress is a Traefik `IngressRoute` with the API group in a value | This repo never uses `networking.k8s.io/v1`, and v3 needs a rename |
| 9 | Labels from `app.labels` on every object | Selectors and dashboards depend on them |
| 10 | No tabs, and no tab indentation in any template | Invisible in review, fatal in YAML |
| 11 | `helm lint` and `helm template <release> . --namespace <ns>` both pass | The only local test these charts have |

### How configuration reaches the app

Three destinations, and the choice is not a matter of taste:

| Configuration | Where it goes | Why |
|---|---|---|
| A plain value | **Inline `env:` in the Deployment** | Changing it changes the pod template, so the Deployment rolls by itself. No second object, no `checksum/config` annotation to fake the rollout |
| A secret | **`extraEnv` with `valueFrom.secretKeyRef`**, or `envFrom.secretRef` for a bulk dump | Never in the plain map, so it cannot be pasted in by accident |
| A file the app reads, such as `config.yaml` | **A ConfigMap mounted as a volume** | Only here does the ConfigMap earn its place: the app needs a file on disk, not a variable |

A ConfigMap used to inject plain variables is a defect, not a style choice. It adds an object that
can be missing, it hides the values from `kubectl describe pod`, and `envFrom` silently skips keys
that are not valid environment-variable names. If a rollout is only correct because a
`checksum/config` annotation was added, the ConfigMap was the wrong tool.

### Required once the app needs it

| # | Requirement | Why |
|---|---|---|
| 12 | A liveness probe on the app's own health endpoint, not on `/` | `/` often returns 200 with a broken backend |
| 13 | A readiness probe that fails when a dependency is down, and **not** the same endpoint as liveness | Sharing them turns a database blip into a restart loop |
| 14 | `resources.requests.memory` and `limits.memory` on every container | Without a limit one app can evict a node |
| 15 | `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault` | Free, and breaks nothing that does not need privileges |
| 16 | Extra volumes with an explicit `mountPath` under `/srv/` or the app's own path | Keeps the NFS-media convention readable |
| 17 | If the image ships a Prometheus exporter, it is enabled and scraped (see **Metrics**) | An exporter that is off is an observability hole nobody notices until the incident |
| 18 | The app logs at `warning` or above, not at `info` (see **Logs**) | Fluent Bit ships every line to Loki with no filter; `info` is thousands of lines a day nobody reads |

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

`flaresolverr` is the first chart to follow this: `LOG_LEVEL: warning`, with the counters carrying
the challenge outcomes the log no longer prints.

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

**New volumes are root-owned again.** The provisioner runs its setup script as root, so every freshly
provisioned claim starts at root. Either re-chown per volume, or set `userPattern`/`groupPattern` on
the local-path provisioner and stop doing this by hand.

| Chart | Probes | Hardening | Non-root UID |
|---|---|---|---|
| `autobrr` | yes | capabilities dropped, seccomp, no privilege escalation | **yes — 1100**, claim chowned first |
| `flaresolverr` | yes, on `/health` | no — deferred on purpose, see the chart README | no — deferred on purpose |
| everything else | no | no | no |

## Checking a chart against this list

```bash
cd cluster/helm/<area>/<app>
helm lint . --namespace <ns>
helm template <release> . --namespace <ns> | kubeconform -strict -summary

# the checks that grep can still do
grep -rnP '\t' templates/ || echo "no tabs"
grep -rn 'latest' values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io' templates/ || echo "no core Ingress"
grep -rn 'autobrr\|<previous app name>' templates/ values.yaml || echo "no copy-paste leftovers"
grep -rn 'prometheus.io/scrape' templates/ || echo "no scrape annotation — does the image ship an exporter?"
grep -rniE 'log_?level' values.yaml || echo "no log level — what does the image default to, and is it info?"
```

That last one is the important one. Every chart in this repo was produced by copying another, and the
first defect found in `autobrr` was a README that described Dashy. Grep for the app you copied from
before you commit.

## What good looks like

`autobrr` as of 2026-11-11: five objects render, no tabs, no hardcoded app name outside `Chart.yaml`
and the values, ingress fully driven by values, env inline in the Deployment, and the claim name derived from the release so a copy
cannot collide with the original.

`flaresolverr` as of 2026-11-12: the app-shaped version of the same chart. Three objects because the
app keeps no state and is never exposed, `RollingUpdate` because there is no claim to fight over,
probes on the app's own `/health`, a `metrics` block that turns its exporter on in one place —
environment, port and scrape annotations together — and `LOG_LEVEL: warning`, with the counters
carrying the outcomes the log no longer prints.

## Charts still to align

Only non-public apps with hand-maintained charts are in scope for now. Public-facing apps wait,
because their ingress, TLS and auth wiring is what changes first in W5 and W7.

Metrics count as debt of the same kind. The rule above is recorded now and applied one chart at a
time; `flaresolverr` is the first, and the rest are not aligned yet.
