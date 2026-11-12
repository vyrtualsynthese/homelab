# recyclarr

recyclarr copies the TRaSH Guides' recommended settings into Radarr and Sonarr: quality profiles (the
ranking of resolutions and sources), custom formats (rules that describe a release's properties, such
as "prefer x265" or "avoid cam rips"), quality size limits and file naming. It is a command-line tool:
it reads a YAML file, calls each arr instance's API, and exits.

## Upstream

- Website and docs: https://recyclarr.dev/
- Source: https://github.com/recyclarr/recyclarr

The image comes from GHCR, `ghcr.io/recyclarr/recyclarr`. Docker Hub carries the same build; upstream
publishes to both.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/recyclarr.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `recyclarr`.

Three objects render:

| Object | Name |
|---|---|
| CronJob | `recyclarr` |
| ServiceAccount | `recyclarr`, with no API token mounted |
| PersistentVolumeClaim | `recyclarr-config`, only when `storage.enabled` and no `storage.existingClaim` |

There is no Service and no IngressRoute: recyclarr has no listener, no UI and no daemon. The image does
support a long-running "cron mode" of its own; this chart does not use it, because a Kubernetes
CronJob already gives one pod per run, per-run logs and a clean exit status. The image's
`CRON_SCHEDULE` variable is therefore irrelevant here.

```bash
# what the cluster will render, exactly
helm template recyclarr cluster/helm/media-stack/recyclarr --namespace media-stack
```

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. Tag is always pinned, never `latest`. | `ghcr.io/recyclarr/recyclarr`, `8.8.1` |
| `args` | Arguments after the image entrypoint (`recyclarr`). | `[sync]` |
| `schedule`, `timeZone` | When the CronJob runs. The timezone is explicit because the controller runs in UTC. | `0 6 * * *`, `Europe/Paris` |
| `concurrencyPolicy` | What happens when a run is still going at the next tick. `Forbid` skips; `Replace` would kill a half-done sync. | `Forbid` |
| `startingDeadlineSeconds` | How late a missed run may still start. | `300` |
| `backoffLimit` | Retries after a failed run. Sync is idempotent, so one retry. | `1` |
| `activeDeadlineSeconds` | Kills a hung run instead of letting it hold the claim. | `1800` |
| `successfulJobsHistoryLimit`, `failedJobsHistoryLimit` | How many finished Jobs are kept for inspection. | `3`, `3` |
| `storage.*` | The config claim: class, size, mount path, or reuse an existing claim. | `local-path`, `128Mi`, `/config` |
| `configFile.*` | The Secret holding `recyclarr.yml`, its key, and where it mounts. | Secret `recyclarr`, key `recyclarr.yml`, `/config/recyclarr.yml` |
| `env` | Variables rendered inline, so a change alters the pod template. | `TZ`, `NO_COLOR`, `RECYCLARR_CONFIG_DIR`, `RECYCLARR_DATA_DIR` |
| `extraEnv`, `envFrom` | Secret-sourced variables. Neither is used: the configuration is a file. | `[]` |
| `securityContext` | UID/GID 1000, drops all capabilities, no privilege escalation. | non-root |
| `podSecurityContext` | `seccompProfile: RuntimeDefault`. No `fsGroup`: it does nothing on a hostPath-backed claim. | seccomp default |
| `resources.*` | Requests and limits. No CPU limit, so the sync burst is never throttled. | `50m`/`128Mi` request, `1Gi` limit |
| `nodeSelector` | Node pin. Must match the node that holds the claim. | `kubernetes.io/hostname: nuc0` |

`PUID` and `PGID` are not chart values on purpose. The official image is rootless, documents that it
supports neither variable, and defaults to `1000:1000`; a UID belongs in `securityContext`, not in the
environment.

## Configuration

`recyclarr.yml` is not written by the chart. It comes from the Secret `recyclarr`, key
`recyclarr.yml`, and is mounted **read-only** at `/config/recyclarr.yml` with `subPath`. The chart does
not template or validate the YAML.

The Secret **must already exist in the namespace before the first run**. A missing Secret leaves the
job's pod in `CreateContainerConfigError`; a malformed one makes the run exit non-zero, which shows up
as a failed Job.

Every run is a new pod, so a Secret change applies to the next run with no restart and no rollout.
That is the one place this chart behaves better than a Deployment: a long-lived pod would keep the
`subPath` copy it started with.

The old chart copied the Secret onto the claim with an init container. The claim may still hold that
copy, but the read-only mount now covers the same path, so the copy is unreachable while the job runs.
Editing `/config/recyclarr.yml` is therefore impossible from inside the pod; change the Secret.

The job runs as UID 1000 because that is the image's default and the owner of the existing claim. It
needs no `chown` step. If you ever raise it to 1100, the claim must be chowned on `nuc0` first —
`local-path` is hostPath-backed and the kubelet does not fix ownership — and the run otherwise fails
on its first write after the pod reports Running.

## State

Everything recyclarr persists lives in the claim mounted at `/config`:

| Path | Contents |
|---|---|
| `resources/` | downloaded guide data and templates |
| `logs/` | one log file per run, pruned by recyclarr's own log janitor (20 files) |
| `state/` | recyclarr's own bookkeeping between runs |
| `settings.yml` | recyclarr's settings file: `git_path`, SSL validation, log janitor, notifications. Created with a schema comment on the first run; when absent, the defaults apply |
| `configs/` | extra configuration files. Every `.yml` or `.yaml` here is loaded **in addition to** the Secret, non-recursively |
| `includes/` | include templates referenced by `include:` in the configuration |

Only `recyclarr.yml` comes from the Secret. Everything else in the table is written by recyclarr on
the claim and is not managed by the chart — which also means a YAML file dropped into `configs/` is
loaded alongside the Secret, not instead of it. `settings.yml` is created by the image itself (v8.8
writes one with a schema comment); the chart neither renders nor validates it.

The claim is named `<fullname>-config` (`recyclarr-config`) and is created by the chart. Set
`storage.existingClaim` to bind an existing claim instead; the chart then renders no
PersistentVolumeClaim. `RECYCLARR_DATA_DIR` is set to `/config` explicitly so the data files stay in
the same claim rather than depending on an image default.

The claim is a `local-path` volume on **`nuc0`**, so the CronJob pins to that node. recyclarr reads no
media and needs no GPU, so the general node is its correct home; the old values said `streaming`, but
that key was never rendered and the claim has lived on `nuc0` since the 2026 rebuild.

Deleting the release deletes a chart-created claim. Keep that in mind before `helm uninstall`: the
state is regenerable, but the next run then re-downloads the guides.

## Schedule

`0 6 * * *` in `Europe/Paris`: a fixed 06:00 run, all year. The old `0 5 * * *` was 05:00 UTC, which
is 06:00 Paris in winter and 07:00 in summer, so the summer run moves one hour earlier. The value is
now stated so it cannot move again with the controller's timezone.

`Forbid` is deliberate. A sync takes seconds; `Replace` would kill one halfway and leave an arr
instance with some settings applied and some not. A skipped day is the cheaper failure and is visible
as a missing Job in the history.

## No probes, no metrics, no ingress

A CronJob has no long-running process and no listener, so liveness and readiness have nothing to say;
the Job's exit status and `backoffLimit` are the health signal. recyclarr ships no Prometheus
exporter, and the contract says an app with no maintained exporter gets none rather than a hand-written
one. The run's output is the record, and Fluent Bit already ships it to Loki.

## Logs

A run prints about twenty lines: provider initialisation, per-instance progress and any warning or
error. `NO_COLOR=1` keeps ANSI escapes out of Loki.

**Debt: the level is not controllable in v8.** recyclarr v8 has no log-level setting or flag; the
contract's "log at warning" rule applies when the app offers the knob. A once-a-day, seven-second run
is not the volume problem that rule exists for. recyclarr already creates a `settings.yml` in the
claim, but v8.8's settings schema has no logging section; the unreleased v9 adds `server.logging`
there, and that is where this gets fixed.

## Known data debt

These come from the Secret's content, not from the chart, and are recorded so the next reader does not
look for a template bug:

- `replace_existing_custom_formats` is printed as deprecated four times. v8 removed the option; delete
  it from the four instance blocks in `recyclarr.yml`.
- `movie-anime`: profile `Original` fails with `` `qualities` is required when creating profiles for
  the first time ``. The profile does not exist on that Radarr instance and the config does not define
  its qualities, so the guide cannot create it. Add the quality list or remove the profile from the
  config.

## Upgrading

`image.tag` and `Chart.yaml`'s `appVersion` move together; Renovate updates the tag only, so the pair
must be checked on every upgrade. The chart version in `Chart.yaml` moves independently.

## Files

| File | Purpose |
|---|---|
| `Chart.yaml` | Chart name, version, and the image version it must match |
| `values.yaml` | Every knob above, with the reason each non-obvious default exists |
| `templates/cronjob.yaml` | The schedule, the Job template and the pod spec |
| `templates/persistentvolumeclaim.yaml` | The claim, skipped when `storage.existingClaim` is set |
| `templates/serviceaccount.yaml` | The token-less ServiceAccount |
| `templates/_helpers.tpl` | `app.*` names shared by convention across these charts |
| `templates/NOTES.txt` | What renders, how to run one sync now |
