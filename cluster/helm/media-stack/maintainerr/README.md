# maintainerr

Maintainerr watches the media library, builds collections from rules ("nobody has watched this in two
years", "no request is open for it") and removes the media when a collection's rule says to. This
chart follows the chart contract in [`docs/charts/README.md`](../../../../docs/charts/README.md); the
shape is copied from `media-stack/tautulli` and `media-stack/autobrr`, with the differences this app
needs.

## Upstream

- Website: https://maintainerr.info/
- Docs: https://docs.maintainerr.info/
- API reference: https://docs.maintainerr.info/api/
- Security model: https://docs.maintainerr.info/security/
- Source: https://github.com/Maintainerr/Maintainerr
- Image: https://github.com/Maintainerr/Maintainerr/pkgs/container/maintainerr

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/maintainerr.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `maintainerr`, so every object
lands on the `maintainerr` / `maintainerr-config` names.

```bash
# what the cluster will render, exactly
helm template maintainerr cluster/helm/media-stack/maintainerr --namespace media-stack
```

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. Tag is always pinned, and has no `v` prefix. | `ghcr.io/maintainerr/maintainerr`, `3.32.0` |
| `strategy` | Deployment strategy. Must stay `Recreate` with a data volume. | `Recreate` |
| `service.port` | Port the Service exposes, and what the IngressRoute points at. | `6246` |
| `container.port` | Only set when the app listens on a different port than the Service. | `null` (falls back to `service.port`) |
| `ingressRoute.*` | Traefik CRD routing: host, entry points, middlewares, TLS. | `maintainerr.kub.local`, `web`, no TLS |
| `storage.*` | The data claim: class, size, mount path, or reuse an existing claim. | `local-path`, `256Mi`, `/opt/data` |
| `env` | Non-secret variables, rendered **inline** into the container. Changing one rolls the pod. | `TZ`, `LOG_LEVEL`, `TELEMETRY` |
| `extraEnv` | Variables sourced from a Secret, as `{ name, valueFrom }`. | `[]` |
| `volumes`, `volumeMounts` | Escape hatch for extra mounts. Empty: the media library is **not** mounted. | `[]` |
| `nodeSelector` | Node pin. Every workload is pinned. | `kubernetes.io/hostname: streaming` |
| `probes.{startup,liveness,readiness}` | HTTP probes on `/api/health/live` and `/api/health/ready`. | enabled |
| `securityContext` | Container hardening for an image that already runs non-root. | UID/GID 1000, `ALL` dropped, no privilege escalation |
| `resources` | Scheduler reservation and memory limit. No CPU limit. | `25m`/`256Mi` request, `1Gi` memory limit |

## Image

`ghcr.io/maintainerr/maintainerr:3.32.0`.

The project publishes the same build to Docker Hub (`maintainerr/maintainerr`) and to GHCR, under its
own organisation. The lab's image policy prefers GHCR, so this chart uses GHCR. The tag carries no
`v`, unlike the GitHub release tags.

**Bumps are manual, and three numbers move together:** `image.tag` in `values.yaml`, `appVersion` in
`Chart.yaml` (which must equal it), and the chart `version`. Renovate's Docker manager only edits
`values.yaml`, so it cannot keep `appVersion` honest — that is why every hand-maintained chart here is
bumped by hand in one commit. A bump also changes the pod template, so the Deployment rolls by itself;
no `checksum/config` annotation is involved.

The jump from the previous `3.21.1` was safe because there was no database to migrate: the old claim
was a Longhorn volume that disappeared in the 2026 disk failure.

## Local-only, and no login

Maintainerr has **no authentication, by design**. Upstream treats it as an appliance on a network you
control: anyone who can reach the port can read every stored credential, change rules, and download
the whole SQLite database (`GET /api/settings/database/download`). The "API key" on its Settings page
is not checked on inbound requests; it exists for the app's own internal calls.

So the boundary is the network, not the app. The one route is on the `web` entry point at
`maintainerr.kub.local`, with no TLS and no middlewares. `kub.local` resolves through the Pi-hole
wildcard record; it is not in a public zone. Do not attach a public host, a TLS block or a cert
resolver to this chart. If it ever needs to leave the LAN, put an authenticating reverse proxy in
front first — upstream documents the options at https://docs.maintainerr.info/security/.

## Storage and the UID

Everything the app keeps lives in `/opt/data`: the SQLite database, its log files, and a copy of the
web UI that `start.sh` stages there on every boot. The chart creates the claim `maintainerr-config`
(`local-path`, `256Mi`). `local-path` is hostPath-backed, so the claim is bound to one node; that is
why the pod is pinned to `streaming`, where Plex and the rest of the media stack run.

256Mi rather than the house default of 128Mi: the data directory holds a second copy of the web UI
while a boot replaces it, and the database and logs sit alongside. A settings-only volume needs 128Mi;
this one is not settings-only.

The image runs as **UID 1000** (`USER node`) and never as root, so the chart sets `runAsNonRoot`,
`runAsUser`/`runAsGroup` 1000, drops `ALL` capabilities and sets the runtime seccomp profile. There is
no root phase and no capability to add back.

No `chown` on the node is needed, and no `fsGroup` is set. The `local-path` chart's setup script
creates each new volume directory world-writable (`0777`) and chowns it to `1100:1100`, so UID 1000 is
not the owner but can write. Two consequences worth knowing:

- This is the one chart where the process UID and the claim owner differ. It works because of the
  directory mode, not because of ownership.
- If the `local-path` chart ever sets `permPattern` (for example `0750`), this is the **first** chart
  to break, with `Failed to stage the UI` in its logs and the container exiting. Fix it by widening
  the mode or by moving the chart to UID 1100, not by adding capabilities.

`fsGroup` is deliberately absent: the kubelet does not apply it to hostPath-backed volumes, so it
would look meaningful and do nothing.

The old chart mounted `maintainerr-config`, which it never created — that missing object, together
with a retired `node-type` affinity, kept the pod `Pending` for 76 days. The new claim starts empty and
the app initialises itself on first boot.

## Probes

| Probe | Path | Behaviour |
|---|---|---|
| startup | `/api/health/live` | Runs before the other two; 5s × 36 = 180s budget through a first boot (UI staging, bootstrap, migrations) |
| liveness | `/api/health/live` | Answers 200 when the HTTP server is serving, and never touches the database |
| readiness | `/api/health/ready` | Runs `SELECT 1`; answers 503 when the SQLite file is missing, locked or corrupt |

This is the first media-stack chart whose readiness probe is a real dependency check on its own path,
so the contract needs no exception here. It is deliberately shallow: an unreachable Plex, Radarr or
Seerr does not take the pod out of rotation, which is what upstream intends — only the database
decides whether the app can serve.

## Logs

`LOG_LEVEL: warn`, inline in the Deployment so a change rolls the pod. Fluent Bit ships every
container line on every node to Loki with no collection-side filter, and this app evaluates rules on a
schedule: at the default `info` it writes a line per evaluation. At `warn` the failures that matter
stay — a deletion that failed, an integration that did not answer.

What the level hides is still countable elsewhere, which is what makes it safe: every run and its
result is recorded in the app's own **Collections → logs** view, and the collection page shows the
last run and the items it matched. Raise the level to `debug` temporarily while diagnosing, then put
it back; a chart that merges at `info` is a regression.

## Telemetry

`TELEMETRY: off`. The anonymous weekly usage report to `telemetry.maintainerr.info` is **on by
default** for new installs: it sends the version, the platform, and which integrations and features
are in use, with counts as ranges, and no identifier or media title. This appliance has nothing to
contribute, so the flag is set. Nothing else in the app changes; the report is the only outbound call
that is not to a service configured in the UI.

## Leftover-folder cleanup: not enabled

The app can remove folders that a Radarr/Sonarr deletion leaves empty. It needs the media library
mounted at the same container path the `*arr` apps report as their root folder, and it deletes
directories, so it is off until someone asks for it. To enable it, mount the shared media claim
read-write:

```yaml
volumes:
  - name: media
    persistentVolumeClaim:
      claimName: pvc-plex-media

volumeMounts:
  - name: media
    mountPath: /movies
```

`pvc-plex-media`, mounted at `/movies`, is exactly what `sonarr`, `radarr` and `plex` use. Leaving it
unmounted costs nothing: cleanup of empty folders is the only feature that needs it.

## Metrics

None. Maintainerr ships no Prometheus exporter, and no maintained sidecar exists (a GitHub search for
one returns nothing). Per the contract, an app with neither gets nothing: silence is better than a
metrics endpoint nobody maintains.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment, the inline env and the three probes |
| `templates/service.yaml` | the ClusterIP Service |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no mounted token |
| `templates/persistentvolumeclaim.yaml` | the data claim, only when `storage.enabled` and no `existingClaim` |
| `templates/ingressroute.yaml` | the Traefik IngressRoute |
| `templates/NOTES.txt` | the install notes, including the LAN-only warning |

There is deliberately no ConfigMap: every setting lives in the SQLite database, and the app owns it.

## Uninstalling

The data claim is created by this chart, so deleting the release deletes the claim — and with it the
database, which holds every integration credential in clear text. Take a copy of `/opt/data` first if
that matters, and treat the copy as a secret.
