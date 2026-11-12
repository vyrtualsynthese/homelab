# tautulli

Tautulli is a Plex statistics dashboard: it reads the Plex server's watch history and shows who
watched what, when, and from where. It follows the chart contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md); the shape is copied from
`media-stack/autobrr`, with the differences this app needs.

## Upstream

- Website: https://tautulli.com/
- Docs: https://docs.tautulli.com/
- Source: https://github.com/Tautulli/Tautulli
- Image: https://github.com/Tautulli/Tautulli/pkgs/container/tautulli

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/tautulli.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `tautulli`, so every object lands
on the `tautulli` / `tautulli-config` names.

```bash
# what the cluster will render, exactly
helm template tautulli cluster/helm/media-stack/tautulli --namespace media-stack
```

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. Tag is always pinned. | `ghcr.io/tautulli/tautulli`, `v2.19.1` |
| `strategy` | Deployment strategy. Must stay `Recreate` with a config volume. | `Recreate` |
| `service.port` | Port the Service exposes, and what the IngressRoute points at. | `8181` |
| `container.port` | Only set when the app listens on a different port than the Service. | `null` (falls back to `service.port`) |
| `ingressRoute.*` | Traefik CRD routing: host, entry points, middlewares, TLS. | `tautulli.kub.local`, `web`, no TLS |
| `storage.*` | The config claim: class, size, mount path, or reuse an existing claim. | `local-path`, `128Mi`, `/config` |
| `env` | Non-secret variables, rendered **inline** into the container. Changing one rolls the pod. | `TZ`, `PUID`, `PGID` |
| `extraEnv` | Variables sourced from a Secret, as `{ name, valueFrom }`. | `[]` |
| `nodeSelector` | Node pin. Every workload is pinned. | `kubernetes.io/hostname: streaming` |
| `probes.{startup,liveness,readiness}` | HTTP probes on `/status`. Startup gates liveness through a first start after an upgrade. | enabled; startup 5s × 30, liveness 20s delay, readiness **disabled** |
| `securityContext` | Container hardening that does not break the image's root init. | no privilege escalation, `ALL` dropped except `CHOWN`/`SETUID`/`SETGID` |
| `resources` | Scheduler reservation and memory limit. No CPU limit. | `10m`/`256Mi` request, `1Gi` memory limit |

## Image

`ghcr.io/tautulli/tautulli:v2.19.1`.

The upstream install docs name only `tautulli/tautulli` on Docker Hub, but the project's release
workflow (`.github/workflows/publish-docker.yml`) pushes the same build to both Docker Hub and GHCR,
under its own organisation. Provenance is therefore identical; the lab's image policy prefers GHCR
over Docker Hub, so this chart uses GHCR. Renovate manages the tag, and `appVersion` in `Chart.yaml`
must move with it.

## Local-only

Tautulli is on the LAN, not the internet. The one route is on the `web` entry point at
`tautulli.kub.local`, with no TLS and no middlewares — the same shape as `sonarr` and `prowlarr`.
`kub.local` resolves through the Pi-hole wildcard record; it is not in a public zone.

The chart used to publish `tautulli.kub.home.ashudev.com` over TLS with a redirect from HTTP. Those
templates are gone, but the live objects are **not** deleted by the sync: the Application has no
automated sync and no prune policy. Delete them by hand after the first sync:

```bash
kubectl -n media-stack delete ingressroute tautulli-public tautulli-public-redirect
kubectl -n media-stack delete middleware tautulli-redirect-https tautulli-security
```

## Storage and the UID

Everything Tautulli keeps lives in `/config`: `tautulli.db`, `config.ini`, and its logs. The chart
creates the claim `tautulli-config` (`local-path`, `128Mi`). `local-path` is hostPath-backed, so the
claim is bound to one node; that is why the pod is pinned to `streaming`, where Plex and the rest of
the media stack already run.

The old chart mounted `tautulli-config2`, a Longhorn claim that disappeared with the 2026 disk
failure. That is what kept the pod `Pending` for 71 days. Nothing is migrated: the new claim starts
empty, and Tautulli can import the watched history back from Plex on first run.

The image starts as **root**: `start.sh` renumbers its `tautulli` user to `PUID`/`PGID` (1100 here),
chowns `/config`, then drops privileges with `gosu`. Consequences:

- `runAsNonRoot` and `runAsUser` are not set: the container would not start.
- The capability set is `drop: [ALL]` plus `CHOWN`, `SETUID`, `SETGID`, the three calls that
  sequence makes. Dropping more breaks the init; adding more widens the pod for nothing.
- `allowPrivilegeEscalation: false` and `seccompProfile: RuntimeDefault` still apply.
- A fresh claim is chowned by the image at every start, so this chart needs **no** manual `chown` on
  the node — unlike the non-root pattern in `autobrr`.

`fsGroup` is deliberately absent: the kubelet does not apply it to hostPath-backed volumes, so it
would look meaningful and do nothing.

## Probes

| Probe | Path | Behaviour |
|---|---|---|
| startup | `/status` | Runs before the other two; 5s × 30 = 150s budget through a first start after an upgrade |
| liveness | `/status` | `{"result":"success","message":"Ok"}` once the web stack is serving |
| readiness | — | **disabled** (see below) |

`/status` is Tautulli's own unauthenticated health endpoint; the image's `HEALTHCHECK` curls the
same path. `/` would answer too but redirects into the UI, so it is not used.

Readiness is off on purpose. Tautulli reports its dependencies — Plex reachability at
`/server_status`, SQLite integrity at `/status?check=database` — **in the JSON body**, always with
HTTP 200. The kubelet cannot read a body, so a readiness probe here would either duplicate liveness
or need a sidecar that parses JSON. This is the documented exception the contract allows.

## Logs

There is no environment variable for Tautulli's log level. It lives in `config.ini` on the claim,
which the setup wizard writes; mounting a ConfigMap over that file would replace the wizard's file
and lose every setting in it.

So the chart sets nothing, and **after the first run, set Settings → Logging → Log Level to
Warning**. Fluent Bit ships every container line on every node to Loki unfiltered, and Tautulli's
default is `info`: a line per HTTP request and per playback event. The History and Users views in the
UI are the operational record, not the log stream.

This is a recorded exception to the contract's log rule, for an app whose level is not env-driven.

## Metrics

None. Tautulli ships no Prometheus exporter, and no maintained sidecar exists — the GitHub search
returns projects with one or two stars. Per the contract, an app with neither gets nothing: silence
is better than a metrics endpoint nobody maintains.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment, the inline env and the probes |
| `templates/service.yaml` | the ClusterIP Service |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no mounted token |
| `templates/persistentvolumeclaim.yaml` | the config claim, only when `storage.enabled` and no `existingClaim` |
| `templates/ingressroute.yaml` | the Traefik IngressRoute |
| `templates/NOTES.txt` | the install notes, including the first-run steps |

There is deliberately no ConfigMap: the only configuration that is not an environment variable lives
in `config.ini`, which the app owns.

## Uninstalling

The config claim is created by this chart, so deleting the release deletes the claim — and with it
the watch history Tautulli has accumulated. Take a copy of `/config` first if that matters.
