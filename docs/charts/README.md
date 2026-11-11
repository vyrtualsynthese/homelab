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
| 13 | Probes: `tcpSocket` on the service port is enough for most apps | A wedged pod otherwise looks healthy to the scheduler |
| 14 | `resources.requests.memory` and `limits.memory` on every container | Without a limit one app can evict a node |
| 15 | `securityContext` with `runAsUser`/`fsGroup` 1100 for LinuxServer images | They expect those IDs |
| 16 | Extra volumes with an explicit `mountPath` under `/srv/` or the app's own path | Keeps the NFS-media convention readable |

Items 13–16 are deferred for `autobrr` itself by decision. Do not copy the omission to a new chart
without deciding it is also deferred for that app.

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
```

That last one is the important one. Every chart in this repo was produced by copying another, and the
first defect found in `autobrr` was a README that described Dashy. Grep for the app you copied from
before you commit.

## What good looks like

`autobrr` as of 2026-11-11: five objects render, no tabs, no hardcoded app name outside `Chart.yaml`
and the values, ingress fully driven by values, env inline in the Deployment, and the claim name derived from the release so a copy
cannot collide with the original.

## Charts still to align

Only non-public apps with hand-maintained charts are in scope for now. Public-facing apps wait,
because their ingress, TLS and auth wiring is what changes first in W5 and W7.
