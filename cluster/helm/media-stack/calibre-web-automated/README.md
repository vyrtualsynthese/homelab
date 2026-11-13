# calibre-web-automated

Calibre-Web Automated (CWA) is the library manager for the book collection. It imports a book —
converts it to the target format, repairs the EPUB, fetches title, author and cover — and writes
metadata and cover edits **into the book file itself**, so what the catalogue shows is what every
other device sees. It is the single writer to the Calibre library on the NAS.

It is **not** the reader. Reading, offline copies and reading position belong to Kavita, which
mounts the same library read-only. CWA's own Kobo sync and KOReader sync stay off: two services
writing the same reading position is how progress gets rolled back.

## Upstream

- Source and release notes: <https://github.com/crocodilestick/Calibre-Web-Automated>
- Wiki: <https://github.com/crocodilestick/Calibre-Web-Automated/wiki>
- Image: `ghcr.io/crocodilestick/calibre-web-automated`, tag `v4.0.8`. The project publishes the
  same build to Docker Hub and GHCR; this chart pulls from GHCR only.

The wiki still says network storage (NFS/SMB) is unsupported and support will not be given. The
release README contradicts it and documents `NETWORK_SHARE_MODE`. Both statements are about the same
release, so the honest position is below, under [Network shares](#network-shares).

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/calibre-web-automated.yaml`, using the chart in this directory
and `values.yaml` next to it. The release name is the Application name, `calibre-web-automated`,
which contains the chart name, so `app.fullname` stays `calibre-web-automated` and no object gets a
doubled suffix.

```bash
# what the cluster will render, exactly
helm template calibre-web-automated cluster/helm/media-stack/calibre-web-automated --namespace media-stack
```

| Object | Name |
|---|---|
| Deployment | `calibre-web-automated`, strategy `Recreate` |
| Service | `calibre-web-automated` (ClusterIP, 8083/TCP) |
| ServiceAccount | `calibre-web-automated`, with no API token mounted |
| PersistentVolumeClaim | `calibre-web-automated-config`, 10Gi, `local-path` |
| PersistentVolumeClaim | `calibre-web-automated-ingest`, 1Gi, `local-path` |
| Middleware | `calibre-web-automated-allowlist`, `ipWhiteList` over `192.168.1.0/24` |
| IngressRoute | `calibre-web-automated`, `web` entry point, `Host(calibre.kub.local) && PathPrefix(/)` |

The two claims are created by this chart only while `storage.<name>.enabled` is true and
`existingClaim` is empty. There is no ConfigMap and no Secret: everything the app needs from the
chart is plain configuration, and it is rendered inline in the Deployment's `env`.

## What the container mounts

| Path | Source | Why |
|---|---|---|
| `/config` | `<fullname>-config`, 10Gi, `local-path` | `app.db`, Calibre's own configuration, thumbnails, logs, and the backup of every processed file (see [Backups](#backups-of-originals)) |
| `/cwa-book-ingest` | `<fullname>-ingest`, 1Gi, `local-path` | The drop folder the import pipeline watches. Files leave it as soon as they are processed |
| `/calibre-library` | `pvc-books-media-nfs-subdir-external-provisioner` (existing) | The library itself, read-write. It stays on the NAS and is shared with Kavita, which mounts it read-only |
| `/run`, `/tmp` | `emptyDir` | Only rendered while `securityContext.readOnlyRootFilesystem` is true — see [Read-only root filesystem](#read-only-root-filesystem-on-trial) |

The library is in place already: CWA adopts `metadata.db` where it sits instead of importing the
collection again. Its auto-detect walks the mounted directory and takes the **largest** database it
finds, so more than one `metadata.db` on the share is a decision to make before the first start, not
after.

Both claims land on the node the pod is pinned to (`streaming`) because `local-path` is
hostPath-backed and `WaitForFirstConsumer`. Two new claims therefore bind to whatever node the first
pod schedules on: delete the old Calibre pod before the first sync so `streaming` is the only
candidate. The trap is written up in [`docs/charts/README.md`](../../../../docs/charts/README.md).

### Claim sizes

`128Mi` is the house standard for a config volume. This chart asks for **10Gi** on purpose:
`/config/processed_books` keeps a copy of every original CWA processes, and that copy is the safety
net for a bad conversion or a bad metadata edit. `128Mi` fills on the first import. 10Gi is a guess
with headroom, not a measurement, and `local-path` does not support volume expansion — re-sizing
means a new claim.

The ingest claim is `1Gi` for the same reason in reverse: files are removed after processing, and the
web UI's Upload button feeds the same pipeline, so a large drop folder is not needed.

## Ingest

Two entry points, one pipeline:

- **The Upload button in the web UI.** It writes the file atomically and feeds the same importer.
  This is the documented path and the one used from a browser.
- **A file copied into `/cwa-book-ingest`.** CWA converts it if the format is not the target one,
  repairs the EPUB, fetches metadata, imports it into the library and deletes it from the ingest
  folder.

Two rules that matter, both from upstream:

- **Do not download directly into the ingest folder.** Write the file somewhere else first and move
  the completed file in. A partially written file in the ingest folder can be imported twice and,
  upstream warns, can corrupt the database.
- **The file must be owned by 1100, not root.** The importer runs as `PUID`/`PGID` 1100 and fails on
  permission errors, leaving the book unimported.

If a book does not appear, use the **Library Refresh** button in the navbar: it re-runs the ingest
over anything still sitting in the folder, which is the recovery path after an abrupt shutdown.

The ingest folder is deliberately **not** on the NAS and deliberately not inside the library mount:
CWA's documentation forbids binds made within other binds, and a library that contains its own import
queue confuses the scanner. Bulk drops over NFS would need a second provisioner, StorageClass and
Application for a folder that is rarely used; dragging files into the web UI does the same job.

## Backups of originals

By default CWA copies every file it converts or repairs to **`/config/processed_books`** before
replacing it. The setting can be switched off in the CWA settings page; it must stay on here, because
it is the only per-book recovery point. A zipper service compresses each day's processed files just
before midnight.

That backup lives on the config claim, on `streaming`, not on the NAS. It survives a bad import; it
does not survive the node's disk, and it is not a backup of the library. The books themselves stay on
the NAS and, after the first metadata pass, carry their own titles, authors and covers.

## Network shares

`NETWORK_SHARE_MODE=true` is required, not optional. On a network share it does three things:

1. disables SQLite's write-ahead log (the journal mode that assumes reliable file locking) for both
   `metadata.db` and `app.db`;
2. skips the recursive ownership change (`chown`) that fails on NFS;
3. switches the ingest and metadata watchers from filesystem events (`inotify`) to polling.

`CWA_WATCH_MODE=poll` forces polling on its own, but the mode above is the switch that matters here.

**The caveat, plainly:** upstream's wiki calls NFS unsupported and refuses to support it; upstream's
release README documents this mode and says network shares are fully supported with it enabled. So
this deployment is *expected* to work and is *not contractually supported*. What bounds the risk:

- the originals of processed files are kept under `/config/processed_books`;
- one copy of `metadata.db` is taken next to the original before the first start;
- losing the database costs a re-catalogue, not the collection, because the books and their embedded
  metadata live on the share.

CWA is the only writer to the library, and Kavita's mount of the same claim is declared
`readOnly: true` in its pod spec, so the rule is enforced by the manifests rather than by good
behaviour.

## Route and access control

The chart renders one `IngressRoute` on the `web` entry point for **`calibre.kub.local`**, and one
`Middleware` — a small Traefik object that runs before a request reaches the app — named
`calibre-web-automated-allowlist`. That Middleware holds a list of allowed source networks
(`ingressRoute.sourceRanges`, `192.168.1.0/24` by default) and rejects every request that did not come
from one of them.

**The allow list is the access control, not the name.** `calibre.kub.local` is only a DNS entry: anyone
who can resolve that name reaches Traefik, and the proxy then answers only if the client address sits
inside a listed range — anything else gets `403 Forbidden`. Reading a `.kub.local` name as a boundary is
how an internal app ends up reachable from outside the LAN.

Two consequences worth stating plainly:

- **An empty `sourceRanges` list is not a safe default.** It renders no Middleware at all, leaving the
  route with no restriction for whoever can reach the proxy. It is a deliberate setting, never a
  fallback.
- **The allow list runs first in the middleware chain**, before anything listed under
  `ingressRoute.middlewares`. A request from outside the ranges is dropped at the proxy instead of being
  handed to an authentication middleware that would offer it a login page.

To read from outside the LAN, add the VPN's address range next to the existing one and commit. If
something between the client and the proxy rewrites addresses before Traefik sees the request, the range
to add is the one the **proxy** sees, not the one the client has — turn on Traefik's access log for a
minute if the range is not obvious; it records the address the request arrived from.

The field is `ipWhiteList` on the Traefik **2.9.4** this cluster runs; Traefik v3 renames it to
`ipAllowList`, together with the CRD group in `ingressRoute.apiVersion`. The field name comes from the
Traefik 2.9 documentation, because the CRD schema is not readable through the read-only account used for
automation — **confirm the live object once after the first sync**:

```bash
kubectl -n media-stack get middleware calibre-web-automated-allowlist -o yaml
# expect spec.ipWhiteList.sourceRange: [192.168.1.0/24]
```

### If the list locks you out

A wrong range denies the browser you are using, and there is no way back in through the name. The
Service does not care about the route, so a port-forward bypasses Traefik entirely:

```bash
kubectl -n media-stack port-forward svc/calibre-web-automated 8083:8083
# then open http://localhost:8083
```

Fix `ingressRoute.sourceRanges`, commit, and let ArgoCD sync. That escape hatch is why a bad range is an
inconvenience rather than an outage — and why the list is worth one thought before the first sync, not
after.

## Credentials

The image starts with the upstream default login, **`admin` / `admin123`**.

Change that password on the first login, before anything else is imported. The app is an
administration interface: it can delete books and rewrite files in the library. The route in front of
it is LAN-only ([why](#route-and-access-control)), which is why the default is tolerable for the few
minutes between the first start and the first login — not beyond that.

## Probes and readiness

| Probe | Path | Behaviour |
|---|---|---|
| startup | `/` | 60 attempts, 10s apart: a first start creates the library database and Calibre's configuration from scratch, and a large library takes minutes to register |
| liveness | `/` | after a 30s delay, every 30s: three failures restart the container |
| readiness | — | deliberately disabled |

`/` is the endpoint the image's own `HEALTHCHECK` uses. It answers `302` towards `/login`; an HTTP
probe treats any `2xx`–`3xx` as success and does not follow the redirect, so the probe passes on the
redirect itself. That is the point: it answers only when the web stack is serving, not merely when
the process exists.

**Readiness is disabled on purpose** (documented exception to contract requirement 13). CWA has no
unauthenticated endpoint that reports dependency health, and its real dependency — the NFS library —
is not exposed by any route. Pointing readiness at `/` would duplicate liveness and turn a share blip
into a restart loop. `tautulli` and `cross-seed` carry the same exception.

## Read-only root filesystem (on trial)

`securityContext.readOnlyRootFilesystem` is `true`, with `emptyDir` volumes at `/run` and `/tmp` so
the image has somewhere to write. **This is an attempt, not a proven setting.** It is kept only if
the container comes up and one ingest completes with it on.

The runtime dependency is exactly this: a writable `/run` and `/tmp`, plus the three mounted claims.
If the image writes outside those paths, the container fails at start (or the ingest fails while the
web UI stays up). The fix in that case is to set
`securityContext.readOnlyRootFilesystem: false` in `values.yaml`: the Deployment gates the two
`emptyDir` volumes on the same value, so they disappear with it. Record the reason here when that
happens — a setting that breaks the app is worse than the absence of it.

## Root-then-1100 init

The image is a LinuxServer base: `s6` (the init supervisor inside the image) starts as root, prepares
`/config` and the ingest folder, then drops to `PUID`/`PGID` 1100. Consequences, all encoded in
`values.yaml`:

- `runAsNonRoot` and `runAsUser` are **absent**: setting either stops the container from starting.
  This is the documented LinuxServer exception, shared with `prowlarr`, `sonarr` and `tautulli`.
- Capabilities are `ALL` dropped plus `CHOWN`, `SETUID`, `SETGID` — the three calls that init makes
  and nothing else.
- `allowPrivilegeEscalation: false` and `seccompProfile: RuntimeDefault` still apply and break
  nothing.
- `fsGroup` is **not** set: `local-path` is hostPath-backed and the kubelet does not apply group
  ownership to it, so it would look meaningful and do nothing. A fresh claim is created `0777` and
  owned by 1100 by the provisioner; the image fixes it again at start.
- No ServiceAccount token is mounted, because the app never calls the Kubernetes API.

Release `v4.0.8` runs the metadata enforcer as `abc`, which is 1100: files it creates in the library
belong to 1100, which is what Kavita needs to read them.

## Log-level debt

Contract requirement 18 asks for `warning` and above. **No documented log-level variable exists** in
this image — the documented environment is `PUID`, `PGID`, `TZ`, `NETWORK_SHARE_MODE`,
`CWA_PORT_OVERRIDE`, `CWA_WATCH_MODE`, `TRUSTED_PROXY_COUNT` and the metadata-provider tokens — and
nothing in the release notes mentions a knob for the application logger. The chart therefore sets
nothing and the app logs at its default level, which is `INFO`.

That is accepted debt, recorded the way `bazarr` records its own: the outcome to watch is not in the
log. The library's activity view, the task list and the duplicate-management screen are the
operational record, and the app's own database holds the import and conversion history. If upstream
adds a knob, set it inline in `env` at `warning` — inline, so the Deployment rolls when it changes —
and delete this section.

## Chart contract exceptions

Every deliberate exception to [`docs/charts/README.md`](../../../../docs/charts/README.md), in one
place:

1. **Readiness disabled** (requirement 13) — no unauthenticated dependency-health endpoint; see
   [Probes and readiness](#probes-and-readiness).
2. **Config claim is 10Gi, not 128Mi** (requirement 5) — the processed-originals backup lives there;
   see [Claim sizes](#claim-sizes).
3. **No `runAsNonRoot`, no `runAsUser`, capabilities not fully dropped** (requirement 15) — the
   LinuxServer root-then-`PUID`/`PGID` init; see [Root-then-1100 init](#root-then-1100-init).
4. **No scrape annotation** (requirement 17) — the image ships no Prometheus exporter. Silence beats
   a target nothing serves.
5. **No log level above `INFO`** (requirement 18) — no knob exists; see
   [Log-level debt](#log-level-debt).
6. **Liveness on `/`, not on an app-specific health endpoint** (requirement 12) — the image ships no
   unauthenticated endpoint that reports the state of a dependency, so `/` is the only thing that
   answers without a session; see [Probes and readiness](#probes-and-readiness). `unpackerr` carries
   the same exception for the same reason.

## Upgrading

`image.tag` in `values.yaml` and `appVersion` in `Chart.yaml` move **together**, because `appVersion`
feeds the `app.kubernetes.io/version` label on every object. Renovate bumps the tag only: update
`appVersion` in the same commit. The chart `version` moves on its own.

CWA does not migrate a Calibre database backwards. Taking a copy of `metadata.db` before an upgrade
that changes the version is cheap; the `/config` claim holds the processed-originals copies but not a
snapshot of the database.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment: inline `env`, three mounts plus the two `emptyDir`s, startup and liveness probes |
| `templates/service.yaml` | the ClusterIP Service, 8083 → 8083 |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no token mounted |
| `templates/persistentvolumeclaim.yaml` | the `-config` and `-ingest` claims, each gated on its own `enabled` and `existingClaim` |
| `templates/middleware-allowlist.yaml` | the `ipWhiteList` Middleware, rendered only while the route and its `sourceRanges` both exist |
| `templates/ingressroute.yaml` | the IngressRoute: one rule, the allow list first in the chain, then any middleware from values |
| `templates/_helpers.tpl` | the naming helpers, copied verbatim from `media-stack/sonarr` |

`app.configClaimName` is present and unused: the helper file is deliberately identical to the
reference chart's, and it can only name one claim, so both names are built with `printf` in the claim
template and the Deployment instead.

There is no ConfigMap, no Secret and no Prometheus exporter in this chart.
