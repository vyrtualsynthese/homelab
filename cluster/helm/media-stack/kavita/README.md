# kavita

Kavita is the reading server for the book library: a web UI that renders EPUB and PDF files in the
browser, keeps a reading position per user, and publishes the collection as an OPDS catalogue (a
standard feed that third-party reader apps can browse) so a phone or tablet can download a book and
resume where it left off.

It is **not** the library manager. Calibre-Web Automated owns the collection: it imports new books,
repairs metadata and writes the files. Kavita mounts that same library **read-only** and is the only
authority on reading position. See `media-stack/calibre-web-automated`.

The chart follows the contract in [`docs/charts/README.md`](../../../../docs/charts/README.md); the
shape is copied from `media-stack/sonarr`, with the differences this app needs listed below.

## Upstream

- Website: https://www.kavitareader.com/
- Docs and wiki: https://wiki.kavitareader.com/
- Source: https://github.com/Kareadita/Kavita
- Image: https://github.com/linuxserver/docker-kavita

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/kavita.yaml`, using the
chart in this directory and `values.yaml` next to it. The release name is the Application name,
`kavita`, and it contains the chart name, so every object is named `kavita` — no `kavita-kavita`
suffix.

```bash
# what the cluster will render, exactly
helm template kavita cluster/helm/media-stack/kavita --namespace media-stack
```

It renders eight objects: the Deployment, the Service (ClusterIP, 5000/TCP), the ServiceAccount, the
PersistentVolumeClaim `kavita-config`, two IngressRoutes (`kavita-public`, `kavita-public-redirect`),
the redirect Middleware (`kavita-redirect-https`) and the TLSOption (`kavita-tlsoption`).

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. Tag is always pinned. | `ghcr.io/linuxserver/kavita`, `v0.9.1.4-ls132` |
| `strategy` | Deployment strategy. Must stay `Recreate` with a config volume. | `Recreate` |
| `service.port` | Port the Service exposes, and what both IngressRoutes point at. | `5000` |
| `container.port` | Only set when the app listens on a different port than the Service. | `null` (falls back to `service.port`) |
| `ingressRoute.*` | Traefik CRD routing: host, TLS, and whether the HTTP redirect router renders. | `kavita.kub.home.ashudev.com`, `websecure`, `myresolver`, `kavita-tlsoption` |
| `storage.*` | The config claim: class, size, mount path, or reuse an existing claim. | `local-path`, `2Gi`, `/config` |
| `storage.library.*` | The shared book library claim and where it is mounted. | `pvc-books-media-nfs-subdir-external-provisioner` at `/books`, read-only |
| `env` | Non-secret variables, rendered **inline** into the container. Changing one rolls the pod. | `TZ`, `PUID`, `PGID`, the two `Logging__*`, `DOTNET_HOSTBUILDER__RELOADCONFIGONCHANGE` |
| `extraEnv` | Variables sourced from a Secret, as `{ name, valueFrom }`. | `[]` |
| `nodeSelector` | Node pin. Every workload is pinned. | `kubernetes.io/hostname: streaming` |
| `probes.{startup,liveness,readiness}` | HTTP probes on `/api/health`. Startup gates liveness through a first boot on a fresh `/config`. | enabled; startup 10s × 60, liveness 20s delay, readiness **disabled** |
| `securityContext` | Container hardening, including the capability set the LinuxServer init needs and the read-only root filesystem attempt. | no privilege escalation, not privileged, `ALL` dropped except `CHOWN`/`SETUID`/`SETGID`, read-only root filesystem with `emptyDir` at `/run` and `/tmp` |
| `resources` | Scheduler reservation and memory limit. No CPU limit. | `15m`/`256Mi` request, `1Gi` memory limit |

## Image

`ghcr.io/linuxserver/kavita:v0.9.1.4-ls132`.

The old chart pulled `0.8.8`, a series tag that moves. This one is pinned to the full release tag,
which does not: that is the rule for LinuxServer images across this repo. `appVersion` in `Chart.yaml`
must move with it, and Renovate proposes the next `-ls` build the same way it does for the other
media charts.

Kavita migrates its database forward on first start and does not migrate it back. Take a copy of
`/config` before an image bump, and roll back by restoring that copy and pinning the old tag.

## Routing

One host, `kavita.kub.home.ashudev.com`, and two routers:

| Object | Entry point | What it does |
|---|---|---|
| `kavita-public` | `websecure` | Serves the UI over TLS, with `certResolver: myresolver` and the `kavita-tlsoption` TLSOption |
| `kavita-public-redirect` | `web` | Same host on plain HTTP; carries the `kavita-redirect-https` Middleware, which answers with a permanent redirect to HTTPS |
| `kavita-tlsoption` | — | TLS 1.2 minimum, the cipher and curve list the other public charts use |
| `kavita-redirect-https` | — | `redirectScheme: https`, permanent |

All four are rendered under the names they already have in the cluster, so a sync updates the live
objects instead of orphaning them.

The chart used to render a fifth: a `Middleware` named `kavita-security` with response headers. No
route ever referenced it — the IngressRoutes carried no middlewares — so it is gone from the
templates. The Application has no automated sync and no prune policy, so the live object is **not**
deleted by the sync. Remove it by hand after the first sync:

```bash
kubectl -n media-stack delete middleware kavita-security
```

## Storage

| Mount | Claim | Holds |
|---|---|---|
| `/config` | `kavita-config`, 2Gi, `local-path`, created by this chart | Kavita's SQLite database, its settings, cover thumbnails and the page cache it generates for every chapter it has served |
| `/books` | `pvc-books-media-nfs-subdir-external-provisioner` (existing), **read-only** | The Calibre library: `metadata.db` and every book file, on the NAS |

### The config claim, and why 2Gi

The old chart mounted `kavita-config2` — a Longhorn claim that went away with the 2026 disk failure.
It does not exist in the namespace at all, which is why the pod sat `Pending` for months rather than
failing loudly. The rebuild creates `kavita-config` on `local-path` and derives the name from the
release.

2Gi is above the 128Mi house standard for one reason: Kavita caches a cover and page renders under
`/config`, and that cache grows with the library rather than with the settings. **The number is a
guess**, not a measurement; it is cheap to raise and impossible to shrink, because `local-path`
cannot expand a claim.

**Nothing is migrated, because there is nothing to migrate.** The reading position lives in the
database on that claim, so a fresh claim means the first start indexes the library from scratch and
progress restarts at zero. Readers rebuild it as they read, and KOReader clients re-sync from their
own side.

Because a `local-path` claim binds to whichever node the first pod schedules on, the old `Pending`
pod is deleted before the first sync of this chart (the `lidarr` trap in the contract). If the claim
does bind to the wrong node, delete the claim first and then the pod — deleting the claim alone
leaves the pod queued against its old UID.

### The library, read-only

`pvc-books-media-nfs-subdir-external-provisioner` is shared with Calibre-Web Automated, which writes
it. Kavita's mount is declared `readOnly: true` in the pod spec, so the single-writer rule is
enforced by the manifest rather than by Kavita's good behaviour. No workload other than CWA mounts
that claim.

The library files are owned by UID 1100, which is what Kavita's `PUID`/`PGID` run as. Nothing on the
node needs a `chown`, and Kavita never writes there.

## Hardening and the UID (LinuxServer root init)

The image is a LinuxServer build: `s6` starts as **root**, prepares `/config`, then drops privileges
to `PUID`/`PGID` 1100. The security context follows from that, and matches
`media-stack/calibre-web-automated`:

| Setting | Value | Why |
|---|---|---|
| `allowPrivilegeEscalation` | `false` | The init does not need to gain privileges, only to drop them |
| `privileged` | `false` | Stated explicitly so the default is never left to memory |
| `capabilities.drop` | `[ALL]` | Start from nothing |
| `capabilities.add` | `[CHOWN, SETUID, SETGID]` | The three calls the init makes: `chown` to fix `/config` ownership, `setuid`/`setgid` to become `PUID`/`PGID`. Nothing else is granted |
| `readOnlyRootFilesystem` | `true` | An attempt — see below |
| pod `seccompProfile` | `RuntimeDefault` | Free, and the image needs nothing it blocks |
| `runAsNonRoot`, `runAsUser` | absent | The container would not start: the init is root before the drop |
| `fsGroup` | absent | The kubelet does not apply it to hostPath-backed volumes, so it would look meaningful and do nothing |

**The read-only root filesystem is an attempt, not a settled fact.** It stays only if the container
starts, indexes a library and serves a page with it on. The Deployment mounts two `emptyDir` volumes
at `/run` and `/tmp` so the .NET runtime and the LinuxServer init have somewhere to write. If the
image turns out to write anywhere else, set `securityContext.readOnlyRootFilesystem` to `false`: the
setting and both `emptyDir` mounts are gated on that one value, so the revert is one line and the
render drops them together. A setting that breaks the app is worse than its absence.

A fresh claim is chowned by the image at every start, so no manual node-side step is needed.

## Probes

| Probe | Path | Behaviour |
|---|---|---|
| startup | `/api/health` | Runs before the other two; 10s × 60 = 600s budget. A restart after an upgrade only needs the migration, but a first boot on a fresh `/config` creates the database, runs the migrations and seeds the default user, so the ceiling follows `calibre-web-automated`'s first-start shape. Liveness does not run until startup succeeds |
| liveness | `/api/health` | Answers 200 as soon as the web stack is serving |
| readiness | — | **disabled** (see below) |

`/api/health` is `[AllowAnonymous]` in `Kavita.Server/Controllers/HealthController.cs`; `/` would
answer too but redirects into the UI, so it is not used.

**Readiness is off on purpose**, which is the exception the contract allows. Kavita's only
dependency is its SQLite database on the config claim, and a database it cannot open stops the
process from starting at all — there is no dependency that can fail while the process stays healthy.
Pointing readiness at `/api/health` as well would duplicate liveness and turn one slow response into
a restart loop. `tautulli` and `cross-seed` record the same exception.

## Logs

Kavita is an ASP.NET Core app, so the chart sets the framework's own configuration keys:

| Variable | Value | What it controls |
|---|---|---|
| `Logging__LogLevel__Default` | `Warning` | The .NET logger's default level |
| `Logging__LogLevel__Microsoft` | `Warning` | The noisy Microsoft namespace, which logs a line per request |

**This has not been confirmed in a running pod**, because the app has not run since the claim that
kept it `Pending` disappeared. Verify after the first start:

```bash
kubectl -n media-stack logs deploy/kavita --tail=100
```

If request lines still appear at `INFO`, Kavita's own Serilog configuration is overriding the
framework keys. In that case remove both variables and record the debt here, the way `bazarr` does:
a knob that does nothing is worse than none, because the next person believes the level is set.
Fluent Bit ships every container line on every node to Loki unfiltered, so the level is the only
place the volume can be cut.

## Metrics

None. Kavita and its LinuxServer image ship no Prometheus exporter, and no maintained sidecar exists.
Per the contract, an app with neither gets nothing: silence is better than a metrics endpoint nobody
maintains. The `kubernetes-pods` scrape job therefore has no kavita target, and that is intended.

## Reading, offline copies and progress

Kavita is the only authority on reading position. Calibre-Web Automated's Kobo and KOReader sync stay
**off**: two services writing the same position is how progress gets rolled back.

- **Browser**: Kavita's own EPUB and PDF readers.
- **Phone and tablet, offline**: browse the OPDS catalogue from a reader app, download a book, read
  it without a connection. Kavita has synced position two ways with KOReader since 0.8.7. Dedicated
  third-party clients do the download-and-resume job natively: Kover (Android, desktop, web),
  Turnleaf (Android, EPUB only) and Inkita (Android). The upstream wiki keeps that list current; this
  README does not repeat it.

The catalogue is reachable on the same host as the UI, `/api/opds` with the API key from
**Settings → Account**.

## Renames cost a series its position

Kavita identifies a series by its **folder path**. When a metadata edit changes a book's author or
title, Calibre renames that book's folder, and Kavita then sees a different series with no reading
history: the position for the old path is orphaned, and the app offers no merge.

So an edit that renames a folder costs that series its position. Do the library repair first, before
Kavita indexes the collection, and treat folder-changing metadata edits as a decision rather than a
cleanup step. Titles that change only inside the metadata, without moving the folder, are free.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment, the inline env, the probes, both claim mounts and the two `emptyDir`s the read-only root filesystem needs |
| `templates/service.yaml` | the ClusterIP Service |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no mounted token |
| `templates/persistentvolumeclaim.yaml` | the config claim, only when `storage.enabled` and no `existingClaim` |
| `templates/ingressroute.yaml` | both IngressRoutes: `websecure` with TLS, and the `web` redirect |
| `templates/middleware.yaml` | the `redirectScheme` Middleware the HTTP router uses |
| `templates/tls-option.yaml` | the `TLSOption` the HTTPS router names |

There is deliberately no ConfigMap: every non-secret setting is an environment variable, and the
rest lives in Kavita's own database under `/config`.

## Uninstalling

The config claim is created by this chart, so deleting the release deletes the claim — and with it
the reading position of every user. Take a copy of `/config` first if that matters. The library is
untouched either way: it is not this chart's claim.
