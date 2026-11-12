# recyclarr chart refactor — design

Date: 2026-11-13
Status: approved by the operator (claim kept, proceed)

## Why this exists

`recyclarr` is the last chart in `media-stack` that is still the untouched `helm create` scaffold. It
runs, so nobody looked at it, and most of its values are decoration: `replicaCount`, `autoscaling`,
`serviceAccount`, `securityContext`, `tolerations`, `affinity` and even `nodeSelector` are never read
by the only template the chart has. The chart is also outside the contract in
[`docs/charts/README.md`](../../charts/README.md): the image tag and `appVersion` disagree, names are
hardcoded, no object carries the house labels, and configuration reaches the app through an init
container that copies a Secret into a claim.

## What exists today

- One `CronJob`, created 2023-04-03, schedule `0 5 * * *` interpreted by a UTC controller, so it
  actually runs at 06:00 Paris. Last run completed in 7 seconds.
- Image `ghcr.io/recyclarr/recyclarr:8.7.2`; `appVersion` says `1.0.0`, and every live object carries
  `app.kubernetes.io/version=1.0.0`.
- `cron.yaml` mounts the claim at `/config` and a `busybox:1.35` init container (named after the
  nextcloud chart) copies the Secret's `recyclarr.yml` into it and `chown -R 1000:1000`. The app then
  runs `recyclarr sync` through `/bin/sh -c`.
- Claim `recyclarr-config`, 128Mi, `local-path`, bound on **nuc0**. The pod runs on nuc0 because of
  the claim, although `values.yaml` says `nodeSelector: streaming` — that key is never rendered.
- `PUID`/`PGID` are set to 1100. The official image documents that it supports neither: it is
  rootless and defaults to `1000:1000`, which is also the UID the init container chowns the claim to.
- `resources.limits.memory: 5128Mi` (5 GiB) on a job that finishes in seconds, requests `100m`/`64Mi`.
- No pod labels, no ServiceAccount, no `startingDeadlineSeconds`, `backoffLimit` or
  `activeDeadlineSeconds`.
- Last run: four instances synced (`movie`, `movie-anime`, `series`, `anime`), four deprecation
  warnings for `replace_existing_custom_formats`, and one configuration error: profile `Original` on
  `movie-anime` has no `qualities`.

## Goals

1. Bring the chart inside the contract, using `autobrr` for naming, labels, claim shape and the
   Secret-file rule.
2. Make the values real: every key either drives the render or is gone.
3. Drop the init container, `busybox` and the shell. The Secret is mounted where the app reads it.
4. Correct the image/`appVersion` pair and move to 8.8.1, whose fixes are about sync failure
   reporting.
5. Write the chart README and update the contract document with what this chart changes about it.

## Non-goals

- Fixing the Secret's content. The `replace_existing_custom_formats` deprecation and the
  `movie-anime` profile error are operator data, recorded as debt in the README.
- Moving the claim to `streaming`. A bound `local-path` claim cannot move; the operator chose to keep
  the claim, so the pod pins to `nuc0` and the placement table is corrected instead.
- A Service, an IngressRoute or probes. The app is a batch command with no listener.
- Notifications, the content of `settings.yml`, or anything from the unreleased v9.

## Approaches

**1. Stateless job.** `emptyDir` for `/config` and `/data`, no claim. Fewer objects, no ownership
question, pins back to `streaming`. Its cost: the downloaded guide resources and the state directory
are rebuilt every morning.

**2. Stateful claim (chosen).** Keep `<fullname>-config` (128Mi, `local-path`) at `/config`, derive
its name from the release, and mount the Secret as a read-only file inside it. Resources, logs and
state persist between runs, and the claim that already exists keeps its data. Its cost: the pod must
stay on the node that holds the PV (`nuc0`).

**3. Keep the init container.** Rejected. It exists only to copy the Secret into a writable directory;
a `subPath` Secret mount does the same without `busybox`, without a shell, and without rewriting the
file on every run.

## Decisions

- **Claim kept**, rendered only when `storage.enabled` and no `storage.existingClaim`. The rendered
  name for release `recyclarr` is `recyclarr-config`, which is the name the live claim already has,
  so nothing is orphaned or recreated. Access `ReadWriteOnce`, class `local-path`, size `128Mi` —
  128Mi has held the cache, logs and state for two years.
- **Node pin `nuc0`**, because the `recyclarr-config` PV is there. `recyclarr` touches no media and
  needs no GPU, so `nuc0`, the general node, is a correct home; the placement table in the
  stabilisation design is corrected in this branch.
- **UID/GID 1000**, not 1100. 1000 is the official image's documented default and the claim is
  already owned by it, so `runAsNonRoot` costs no manual `chown` and no runbook. Capabilities are
  dropped, privilege escalation is off, and the runtime seccomp profile applies.
- **Configuration by file mount.** Secret `recyclarr`, key `recyclarr.yml`, mounted read-only at
  `/config/recyclarr.yml` with `subPath`, mode `0444`. Each CronJob run starts a new pod, so the
  `subPath` staleness that matters for a Deployment does not apply here. The chart never templates or
  validates the YAML.
- **Schedule `0 6 * * *` with `timeZone: Europe/Paris`**: a fixed 06:00 Paris run. The old
  `0 5 * * *` was 05:00 UTC — 06:00 Paris in winter and 07:00 in summer — so the summer run moves one
  hour earlier; the schedule no longer drifts with the offset.
- **`concurrencyPolicy: Forbid`**, not `Replace`: a sync takes seconds, and killing one halfway
  leaves an arr instance partly updated. A skipped day is cheaper than a torn sync.
- **`startingDeadlineSeconds: 300`**, `backoffLimit: 1` (sync is idempotent), `activeDeadlineSeconds:
  1800` (no run may hold the claim for a day), history 3/3.
- **Image `8.8.1`**, `appVersion: "8.8.1"`, chart `1.0.0` — the first contract-compliant version of
  this chart.
- **Resources:** requests `50m` / `128Mi`, memory limit `1Gi`, no CPU limit. The number is a
  protection ceiling, not a measurement: the pod lives for seconds, shorter than the 15s scrape, so
  cAdvisor holds no sample for it. The previous `5128Mi` was scaffold text.
- **ServiceAccount** created, `automountServiceAccountToken: false`: the pod never calls the API.
- **No probes, no Service, no ingress, no metrics.** A CronJob has no long-running process to probe
  and recyclarr ships no exporter, so silence is correct rather than an omission.
- **Log level debt.** v8 exposes no level control; a run prints about twenty lines once a day. The
  contract's `warning` rule applies when the app offers the knob. v8.8 reads a `settings.yml` but its
  schema has no logging section; v9 adds `server.logging` there. Recorded in the README so it is not
  mistaken for an oversight.

## Files

| File | Change |
|---|---|
| `cluster/helm/media-stack/recyclarr/Chart.yaml` | Real description, `version: 1.0.0`, `appVersion: 8.8.1`, links |
| `cluster/helm/media-stack/recyclarr/values.yaml` | Rewritten around the CronJob; scaffold keys removed |
| `templates/_helpers.tpl` | `app.*` names copied from `autobrr`, with `app.configClaimName` |
| `templates/cronjob.yaml` | New; replaces `cron.yaml` |
| `templates/persistentvolumeclaim.yaml` | New; replaces `pvc-config.yaml` |
| `templates/serviceaccount.yaml` | New |
| `templates/NOTES.txt` | New |
| `cluster/helm/media-stack/recyclarr/README.md` | New |
| `docs/charts/README.md` | Hardening table row, batch-job note, worked example |
| `docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md` | Move `recyclarr` to `nuc0` in the placement table |

## What is left to the builder

Nothing about the shape. The render checks, the README content and the commit split are in the plan.
