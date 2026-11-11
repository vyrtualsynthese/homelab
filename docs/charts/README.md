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
| 6 | Env in a ConfigMap via `env`, secrets via `extraEnv` with `valueFrom` | The two paths must not diverge |
| 7 | `checksum/env` pod annotation so an env change rolls the pod | Otherwise the ConfigMap changes and nothing restarts |
| 8 | Ingress is a Traefik `IngressRoute` with the API group in a value | This repo never uses `networking.k8s.io/v1`, and v3 needs a rename |
| 9 | Every hardcoded name goes through `app.fullname` / `app.selectorLabels` | A copied chart that still says `autobrr-config` is a bug waiting for a restart |
| 10 | Labels from `app.labels` on every object | Selectors and dashboards depend on them |
| 11 | No tabs, and no tab indentation in any template | Invisible in review, fatal in YAML |
| 12 | `helm lint` and `helm template <release> . --namespace <ns>` both pass | The only local test these charts have |

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

`autobrr` as of 2026-11-11: six objects render, no tabs, no hardcoded app name outside `Chart.yaml`
and the values, ingress fully driven by values, and the claim name derived from the release so a copy
cannot collide with the original.

## Charts still to align

Only non-public apps with hand-maintained charts are in scope for now. Public-facing apps wait,
because their ingress, TLS and auth wiring is what changes first in W5 and W7.
