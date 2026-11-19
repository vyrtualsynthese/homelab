# filebrowser chart

FileBrowser Quantum over the NAS exports, signed in through authentik, with no local password form.

## Why the fork

Upstream `filebrowser/filebrowser` was archived on 2026-09-01: no fixes, no OIDC.
[FileBrowser Quantum](https://github.com/gtsteffaniak/filebrowser) is the maintained fork and adds
native OIDC plus per-user, per-source permissions.

The chart here is a thin wrapper (repo pattern D): `Chart.yaml` pins the community chart
[`Softwaredam/filebrowser-chart`](https://github.com/Softwaredam/filebrowser-chart) as an OCI
dependency at `oci://ghcr.io/softwaredam/helm-charts`, and this file's values carry everything that
matters. `charts/` and `Chart.lock` are gitignored — ArgoCD runs `helm dependency build` at sync time.

| Object | Name |
|---|---|
| Deployment, Service, ConfigMap, Secret | `filebrowser-filebrowser-quantum` |
| Claims on `local-path` | `filebrowser-filebrowser-quantum-config` (holds `database.db`), `-data` |
| NFS claims | `filebrowser-stash`, `filebrowser-audio`, `filebrowser-download` |

The service port is **8080** (the chart's default), not 80. `templates/ingressroute.yaml` reads
`traefik.service` and `traefik.port` for that reason.

## Sources and the export they need

| Source | Mount | NAS path | Claim |
|---|---|---|---|
| `various` | `/srv/various` | `/mnt/storage/nfs/stash` | `filebrowser-stash` |
| `audio` | `/srv/audio` | `/mnt/storage/nfs/audio` | `filebrowser-audio` |
| `torrent` | `/srv/torrent` | `/mnt/ultrafaststorage/nfs` | `filebrowser-download` |

Those are the three exports the NAS already serves; there is no dedicated export for one folder. The
editor's folder is a subdirectory of one of them, and that is expressed as a **scope**, not as a
fourth volume: a user's scope is a source plus a path inside it, so `various` with `/creator` shows
only that folder and nothing else from the assets export.

`templates/nfs-volumes.yaml` renders one PV and one pre-bound claim per entry in `nfsVolumes`. A
missing export is not tolerated: the pod sits in `ContainerCreating` on a failed mount rather than
serving an empty folder.

Every source sets `defaultEnabled: false`. A brand-new OIDC user therefore starts with an empty
sidebar, **administrators included**: grant the sources in **Settings → User management** after the
first sign-in. Without it the editor would inherit every export on the NAS.

Uploads run as uid/gid 1100 (`securityContext`). Reads succeed under almost any identity; the first
upload is the real test of the export's ownership.

`fsGroup` is **1000**, not 1100: the assets export carries directories owned by 1000 with mode
`0750`, which uid 1100 cannot open — the video-project folders and four of the ROM sets. NFS volumes
are not chowned, so `fsGroup` only puts 1000 in the process's group list, which makes those readable
without touching the NAS. Writes stay with 1100, the owner of the download export (`0755`).

That uid is also why `server.cacheDir` is set to `/data/cache`: the chart's default is the relative
string `tmp`, resolved against the image's working directory, which 1100 cannot write, and the app
dies at startup with `mkdir tmp: permission denied`. `/data` is the claim the chart already mounts for
this, and `cacheDirMaxSizeMB: 700` keeps the preview cache inside the 1 Gi claim.

## Login

Namespace `filebrowser` has one OAuth2 client, id `filebrowser`, secret in Secret `filebrowser-oidc`
key `CLIENTSECRET`. The provider lives in the authentik blueprint
`cluster/helm/authentik/files/app-filebrowser.yaml`; the same secret is `FILEBROWSER_CLIENT_SECRET` in
the `authentik-sso` Secret, because Secrets do not cross namespaces. Callback:
`https://filebrowser.kub.home.ashudev.com/api/auth/oidc/callback`.

`auth.methods.password.enabled` is `false`, and Quantum only auto-redirects when OIDC is the *only*
method, which is what the operator asked for. Consequences:

- No local login exists. Recovery is a commit — see `docs/runbooks/authentik-apps.md`.
- `adminPassword.passwordOverride` is pinned to an inert string because the chart otherwise generates
  a password with `rand` on every render and emits it unquoted; a value starting with `#` produced
  invalid YAML. Turning password login back on means setting a real value there first.
- Share links are unaffected by an authentik outage: the app serves `/share/...` itself, with no gate
  in front of it.

## The route is public on purpose

`middlewares` carries `security` only. `filebrowser.kub.home.ashudev.com` is on
`hack/check_exposure.py`'s allowlist: the editor signs in from outside, and share links go to people
with no account at all. Re-adding `lan-only` makes both unreachable while `helm template` still looks
fine.

## Known gap

The community chart renders no `nodeSelector`, `affinity` or `tolerations`, so the pod is not pinned to
a named node (repo rule). It can land on any node, including the ARM64 `rbp2`. Tracked as a follow-up
task; the fixes are an upstream chart PR, a fork, or a local chart.
