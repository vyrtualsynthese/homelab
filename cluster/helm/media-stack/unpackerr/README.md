# unpackerr

[unpackerr](https://unpackerr.zip) watches the download queues of Lidarr, Radarr, Readarr and Sonarr
and extracts completed archives in place, so the *arr app can import them. This is the
[Unpackerr/unpackerr](https://github.com/Unpackerr/unpackerr) project; the image comes from Docker Hub
as `golift/unpackerr`.

The chart is hand-maintained. It follows the contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md); `autobrr` is the reference for
principles, not a file to copy.

## Deployment

| | |
|---|---|
| ArgoCD Application | `cluster/argocd/projects/media-stack/unpackerr.yaml` |
| Release name | `unpackerr` |
| Namespace | `media-stack` |
| Objects | Deployment `unpackerr`, ServiceAccount `unpackerr` |
| Node | `streaming` (`nodeSelector`, `kubernetes.io/hostname`) |
| Image | `golift/unpackerr:v1.0.1` |

There is no Service, no IngressRoute and no ConfigMap. The chart renders nothing that carries
traffic, and all configuration is inline `env` in the Deployment, so a change rolls the pod by
itself.

Render locally:

```bash
helm template unpackerr cluster/helm/media-stack/unpackerr --namespace media-stack
helm lint cluster/helm/media-stack/unpackerr --namespace media-stack
```

## Values

| Key | Default | What it does |
|---|---|---|
| `image.repository` | `golift/unpackerr` | Image repository. |
| `image.tag` | `v1.0.1` | Pinned image tag. Keep `appVersion` in `Chart.yaml` equal to it. |
| `image.pullPolicy` | `IfNotPresent` | The tag is pinned, so pulling every start buys nothing. |
| `strategy` | `Recreate` | One pod only: a second would extract the same archive twice. |
| `container.port` | `5656` | HTTP listener, named `http`. Renders `UN_WEBSERVER_LISTEN_ADDR` and the liveness probe, so listener and probe cannot drift. |
| `env` | see file | Plain environment variables, rendered inline. |
| `instances.*` | four blocks | Starr instances, one block per app and index. |
| `downloads.existingClaim` | qBittorrent's NFS claim | Where completed downloads live. The chart does not create it. |
| `downloads.mountPath` | `/downloads` | Must match the paths the *arr apps report. |
| `resources.*` | `10m`/`64Mi` request, `512Mi` limit | See [Resources](#resources). |
| `probes.liveness` | enabled, `GET /` | Process check, see [Probes](#probes). |
| `probes.readiness` | disabled | See [Probes](#probes). |
| `securityContext` | UID/GID 1100, no escalation, all caps dropped | See [Security](#security). |
| `nodeSelector` | `streaming` | Every workload is pinned to a named node. |

`extraEnv`, `envFrom`, `volumes`, `volumeMounts`, `podAnnotations` and `podLabels` are escape hatches
rendered verbatim. Prefer a named value when one exists.

## Instances

The key names the app and the index that appears in the environment variable:

| Key | Renders |
|---|---|
| `sonarr0` | `UN_SONARR_0_*` |
| `sonarr1` | `UN_SONARR_1_*` |
| `radarr0` | `UN_RADARR_0_*` |
| `radarr1` | `UN_RADARR_1_*` |

Each block carries `url`, `apiKeySecret` (`name` and `key`), `paths`, `protocols`, `timeout`,
`deleteOrig` and `deleteDelay`. The API key is rendered as a `secretKeyRef` and never as a value.

The four Secrets already exist in the namespace and hold the same keys the exportarr sidecars read:

| Instance | Secret | Key |
|---|---|---|
| `sonarr0` | `sonarr` | `secret` |
| `sonarr1` | `sonarr-anime` | `secret` |
| `radarr0` | `radarr` | `secret` |
| `radarr1` | `radarr-anime` | `secret` |

To add an instance, add the values block and one include line in
`templates/deployment.yaml`:

```yaml
{{- include "app.starrEnv" (dict "prefix" "LIDARR" "index" 0 "instance" .Values.instances.lidarr0) | nindent 12 }}
```

The index in the key, the `index` argument and the rendered variable must all agree. A copy-paste
that leaves two instances on the same index silently overwrites one of them.

## Security debt: rotate the API keys

The four API keys were committed in plaintext before 2026-11-13, in
`templates/deployment.yaml`. Removing them from the chart does not remove them from git history.

**Rotating them is required and is not done by this chart.** For each app, change the key in the app's
own settings, then replace the Secret:

```bash
kubectl -n media-stack create secret generic sonarr \
  --from-literal=secret=<new-key> --dry-run=client -o yaml | kubectl apply -f -
```

The pod reads its keys at start, so restart the Deployment afterwards. Do the same for
`sonarr-anime`, `radarr` and `radarr-anime`.

## Security

`runAsNonRoot` as UID/GID 1100, `allowPrivilegeEscalation: false`, every capability dropped,
`seccompProfile: RuntimeDefault`, and no ServiceAccount token. The image declares no `USER` and would
otherwise run as root.

UID 1100 is what qBittorrent, Sonarr and the rest of media-stack use, and the NFS download export
already accepts it. Files extracted while the pod ran as root are root-owned: unpackerr can read them
and the parent directories are writable by 1100, but a directory it cannot clean has to be removed or
`chown`ed by hand on the NFS export.

## The web UI

v1 ships a web UI. This chart does not publish it: there is no Service and no route, so it is
reachable only by pod IP and `kubectl port-forward`:

```bash
kubectl -n media-stack port-forward deploy/unpackerr 5656:5656
```

`UN_WEBSERVER_UI_PASSWORD` is `noauth`. The alternatives are worse:

- empty makes v1 generate a temporary password at every start and print it into the logs, which goes
  to Loki;
- a literal password in `values.yaml` is a secret in git, which is the defect this chart removed.

Adding a Service or an IngressRoute in front of this would publish an unauthenticated management API
at a stable cluster address. That is a deliberate decision to re-open, not a default.

## Probes

Liveness is `GET /` — the SPA root, served outside the auth wrappers, which answers 200 while the
process is serving. unpackerr has no health endpoint: every `/api/*` route needs a permission the
kubelet does not have. So this proves the process is alive; it does not prove Sonarr is reachable.

Readiness is disabled. There is no unauthenticated endpoint that reports dependency health, and there
is no Service, so there is no traffic to gate. Pointing readiness at the same path as liveness would
turn an *arr outage into a pod restart. The `probes.readiness` block exists only so the toggle renders
a valid probe if it is ever switched on.

## Metrics

Prometheus scraping is off, deliberately.

v1 serves `/metrics` behind an API key holding `system:metrics:read`. Prometheus in this cluster
discovers pods by annotation (`kubernetes-pods` job), and that job sends no credentials, so an
annotation would produce a target that is permanently down. That is what the old chart did: it
announced port 5656 while the exporter was disabled.

Turning it on properly takes two changes outside this chart: create an API key with
`system:metrics:read` and store it in a Secret, then add an authenticated scrape config to the
monitoring stack. Until both exist, this chart announces nothing.

## Logs

unpackerr has no log level. v1 exposes `debug` and `quiet`, nothing in between, and `quiet` silences
errors too, so it is not an option.

`UN_ACTIVITY=true` removes the queue summary lines that report only zeros. What remains is one
`Status:` line per tracked item per minute — measured at roughly 13k lines a day on 0.16.1, mostly
`Imported, elapsed: N` — and that cannot be turned off without silencing everything.

The v1 queue UI and the history file are the record for what the log cannot be quietened about. If an
extraction matters, check the queue rather than grepping for it.

## Resources

Measured idle on 2026-11-13: 15 MiB memory and about 0.02m CPU. The request (`64Mi`, `10m`) covers idle
plus margin; the `512Mi` limit is headroom for the extraction children, which were not running when the
measurement was taken.

Check `kubectl top pod` during a real extraction burst and adjust if it climbs. Any non-zero request
also keeps the pod out of the `BestEffort` class, where it spent its first 60 days — `BestEffort` pods
are the first evicted when the node runs short of memory, and this one had 13 restarts in that period.

## Upgrades

`image.tag` and `Chart.yaml`'s `appVersion` move together. Renovate updates the tag only, so check both
on an upgrade.

This chart carries a major app change: `0.16.1` → `v1.0.1`. v1 rewrote the config-file format, added
the web UI and moved metrics behind API keys. The environment-variable contract did not change, and
this chart uses environment variables only, so the rollback is a one-line revert of `image.tag`:
the old image starts from the same values.

## Uninstall

The chart creates no PersistentVolumeClaim. `helm uninstall` removes the Deployment and the
ServiceAccount; the download export belongs to qBittorrent and is left untouched.
