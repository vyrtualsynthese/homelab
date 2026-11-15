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
| Middleware | none of its own; the IngressRoute uses `kube-system-lan-only` from the `traefik-gate` chart |
| Middleware | `calibre-web-automated-redirect-https`, `redirectScheme` to `https`, used only by the redirect router |
| IngressRoute | `calibre-web-automated`, `websecure` entry point, `Host(calibre.kub.local) && PathPrefix(/)`, TLS from the cluster default certificate |
| IngressRoute | `calibre-web-automated-redirect`, `web` entry point, same host, sends the browser to HTTPS |
| TLSOption | `calibre-web-automated-tlsoption`, TLS 1.2 minimum |

The two claims are created by this chart only while `storage.<name>.enabled` is true and
`existingClaim` is empty. There is no ConfigMap and no Secret: everything the app needs from the
chart is plain configuration, and it is rendered inline in the Deployment's `env`.

## What the container mounts

| Path | Source | Why |
|---|---|---|
| `/config` | `<fullname>-config`, 10Gi, `local-path` | `app.db`, Calibre's own configuration, thumbnails, logs, and the backup of every processed file (see [Backups](#backups-of-originals)) |
| `/cwa-book-ingest` | `<fullname>-ingest`, 1Gi, `local-path` | The drop folder the import pipeline watches. Files leave it as soon as they are processed |
| `/calibre-library` | `pvc-books-media-nfs-subdir-external-provisioner` (existing) | The library itself, read-write. It stays on the NAS and is shared with Kavita, which mounts it read-only |
| `/run`, `/tmp` | `emptyDir` | Only rendered while `securityContext.readOnlyRootFilesystem` is true — see [Read-only root filesystem](#read-only-root-filesystem-off) |

The library is in place already: CWA adopts the existing `metadata.db` instead of importing the
collection again. Auto-mount is **off** (see [Library auto-mount](#library-auto-mount-off)), so the
database path is set once in the UI and CWA never invents a library of its own.

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

The chart renders two `IngressRoute` objects for **`https://calibre.kub.local`**:

- one on the `websecure` entry point, which is the real route, carrying one `Middleware`:
  `kube-system-lan-only@kubernetescrd`;
- one on `web`, which redirects to it. It carries the same middleware first, so a request from outside the
  LAN is refused with `403` before anything is redirected.

`lan-only` is shared by every non-public route in the cluster and allows the LAN (`192.168.1.0/24`),
WireGuard (`10.8.0.0/24`) and the pod network (`10.42.0.0/16`); everything else gets `403 Forbidden`. It is
defined in the `traefik-gate` chart, not in this one.

**The name is not a boundary; the allow list is.** `calibre.kub.local` is only a DNS entry: anyone who can
resolve it reaches Traefik, and the proxy answers only if the client address sits inside a listed range.
Reading a `.kub.local` name as a boundary is how an internal app ends up reachable from outside the LAN.

### Why this one is HTTPS

Every other media tool is plain HTTP on `kub.local`. Calibre-Web is the exception because its sign-in
demands it: the OIDC button only appears when *Login type* is `OAuth`, and that setting forces
`SESSION_COOKIE_SECURE=True` (`cps/__init__.py`). A browser given a `Secure` cookie over plain HTTP throws
it away, so the callback would arrive without the session it started with, and even the password login
would stop sticking. Its own settings page says it in the dropdown: *Use OAuth (requires HTTPS)*.

The certificate is **the cluster's default one**, `kube-system/local-cert` via the default `TLSStore` — a
private certificate whose subject alternative names are `*.kub.local`, `*.app.local`, `localhost` and
`127.0.0.1`. The chart sets neither `secretName` nor `certResolver`: ACME cannot issue for a `.local` name,
because public CAs have been barred from internal names since 2015. A client without the root installed
shows a certificate warning, which is accepted here; installing the root on the phone or laptop removes it.

**The certificate that was there expired on 2026-05-06** and went unnoticed for ten months, because nothing
in the repo could reissue it. `hack/internal-cert.sh` now does: it keeps a private CA (so renewals need no
client action) and signs a leaf from it.

```bash
hack/internal-cert.sh                       # reuse the CA, sign a fresh leaf
kubectl -n kube-system create secret tls local-cert \
  --cert=$HOME/.config/secrets/kub-local/kub.local.pem \
  --key=$HOME/.config/secrets/kub-local/kub.local-key.pem \
  --dry-run=client -o yaml | kubectl apply -f -
```

Traefik serves the new certificate as soon as that Secret changes, without a restart. This route is
currently the only one that falls back to the default store, so it is the only one affected.

The root is `~/.config/secrets/kub-local/local-ca.pem`, installed once per client. Backup rules, the
restore drill and the per-client trust install are in
[`docs/runbooks/internal-ca.md`](../../../docs/runbooks/internal-ca.md) — read it before touching that
directory, because losing `local-ca-key.pem` is what forced a new root the last time.

### Sign-in

There is **no forward-auth gate** in front of this route. CWA signs users in over **OIDC** itself — the
same protocol authentik uses natively — configured in its own admin interface under Settings → Basic
Configuration → OAuth, provider `generic`, with *Login type* set to `OAuth` in Login Settings:

| Setting | Value |
|---|---|
| OAuth Metadata URL | `https://authentik.kub.home.ashudev.com/application/o/calibre/.well-known/openid-configuration` |
| OAuth Redirect Host | `https://calibre.kub.local` |
| OAuth Client Id | `calibre` |
| OAuth Client Secret | the value of `CALIBRE_CLIENT_SECRET` in the `authentik-sso` Secret |
| OAuth Scopes | `openid profile email` |
| Username Field | `preferred_username` |
| OAuth group for Admin | `authentik Admins` |

The callback authentik has to allow is `https://calibre.kub.local/login/generic/authorized`; it is
declared in the `authentik` chart's `files/app-calibre.yaml`.

Why not a proxy gate, when most of the media stack has one: a gate would put a login page in front of
**`/opds`** and the Kobo sync endpoints, which authenticate with basic auth (the Calibre-Web username and
password) and cannot follow a redirect to authentik. Native OIDC leaves both untouched, and the app's own
session is the only thing the browser needs.

Two consequences of the group mapping:

- an account in `authentik Admins` becomes a Calibre-Web administrator on login, and an account that is
  not in it loses the role — that is the point of "Enable OAuth Group-Based Admin Role Management";
- anyone the authentik application's policy refuses cannot sign in through OIDC at all.

### If the sign-in locks you out

The Service does not care about the route, so a port-forward bypasses Traefik and the OIDC client both:

```bash
kubectl -n media-stack port-forward svc/calibre-web-automated 8083:8083
# then open http://localhost:8083 and use the local password
```

`http://localhost` is the one plain-HTTP origin browsers treat as trustworthy: they accept a `Secure`
cookie from it, so the local login still works there while `OAuth` is the login type. Opening the same
app through a port-forward on another host name over HTTP would not.

That is the way back in when authentik is down or the client secret is wrong. The local account keeps its
password on purpose; it is never removed.

## Credentials

Two ways in, one account.

**The local account is named after the authentik username**, `vyrtualsynthese` since 2026-11-15 (it was
`admin`). Renaming it is what makes OIDC land in it: Calibre-Web matches the name from the identity
provider against its own users and creates a second account when it finds none, which would leave the
administrator role and every shelf behind on the old one.

That account keeps its password. Typing it at `http://localhost:8083` after a port-forward is the
break-glass route when authentik is unreachable or the client secret is wrong. Nothing else in the app
uses that password: the `/opds` catalog and Kobo sync accept it as basic auth, which is the reason the
route carries no proxy gate ([why](#sign-in)).

The upstream default is `admin` / `admin123`; it was changed on the first login, before anything was
imported. The app is an administration interface: it can delete books and rewrite files in the library.

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

## Read-only root filesystem (off)

`securityContext.readOnlyRootFilesystem` is **`false`**. It was tried on the first start of the
cutover and the image does not survive it: the update-notification and theme-migration services write
notice files into `/app`, and the container logged

```
[Errno 30] Read-only file system: '/app/cwa_update_notice'
[Errno 30] Read-only file system: '/app/theme_migration_notice'
```

The web UI stayed up, so this was not a crash loop — it was a setting that quietly broke background
work, which is why it came out. The Deployment renders the two `emptyDir` volumes for `/run` and
`/tmp` only while the setting is on, so they disappear with it.

The rest of the hardening stands: `allowPrivilegeEscalation: false`, `privileged: false`,
`seccompProfile: RuntimeDefault`, and `ALL` capabilities dropped except the three the `s6` init needs.

## Library auto-mount (off)

`DISABLE_LIBRARY_AUTOMOUNT` is **`true`**. On the first start the auto-mount service did not find the
library at `calibre-lib/metadata.db`, created an empty `metadata.db` at the mount root, and pointed
CWA at it — hence the `/admin/dbconfig` redirect and `no such table: custom_columns` in the logs. It
also owns the database-path field in the UI while it runs, so the real library could not be selected.

With it off, the path is set once at `/admin/dbconfig` and stored in `app.db` on the config claim.
Set it to the **directory** that holds `metadata.db`, for example `/calibre-library/calibre-lib`.

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
