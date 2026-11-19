# koillection

Koillection is a catalogue for a personal collection: books, films, games and music, with the photos
and documents that belong to each item. It keeps its records in PostgreSQL and the uploaded images on
a volume.

The chart follows the contract in [`docs/charts/README.md`](../../../docs/charts/README.md). It
was rebuilt on 2026-11-19 from the shape of `media-stack/kavita` and `media-stack/autobrr`; the
differences this app needed are below.

## Upstream

- Website: https://koillection.github.io/
- Source: https://github.com/benjaminjonard/koillection
- Image: https://hub.docker.com/r/koillection/koillection

## What changed in the rebuild

| Before | Now | Why |
|---|---|---|
| No claim template at all; the Deployment mounted `koillection-conf` and `koillection-uploads` | Both claims are rendered here: `koillection-conf` on `local-path` (1 GiB) at `/conf`, `koillection-uploads` on `stash-nfs` (20 GiB) at `/uploads` | The chart mounted volumes nothing created. A Pending pod is not a placeholder |
| `affinity.nodeAffinity` on the retired `node-type: general` label | `nodeSelector: kubernetes.io/hostname: nuc0` | `node-type` no longer exists anywhere, so the old affinity matched no node |
| `service.port: 5432` (a PostgreSQL copy-paste) | `80`, one value for container port, Service and both probes | The image serves on :80 (`FRANKENPHP_SERVER_NAME=":80"`) |
| No probes | A startup probe and a liveness probe on `/`; no readiness probe | The image's own `HEALTHCHECK` is `curl --fail http://localhost:80/`. Koillection publishes no health route, and nothing unauthenticated reports whether the database is up, so readiness would only duplicate liveness |
| No `securityContext` | seccomp `RuntimeDefault`, no privilege escalation, capabilities kept, root then dropping to `PUID`/`PGID` 1100 | The entrypoint must be root: it writes `php.ini` under `/usr/local/etc`, `/app/public/.env.local` and the `/uploads` symlink, rewrites its user, chowns `/uploads`, then `setpriv`s to 1100. Two live attempts pin the shape down — see "The init needs root" below |
| `limits.memory: 256Mi`, `requests.cpu: 1m` | request 128Mi / 10m, limit 512Mi | See "Sizing" below |
| Boilerplate `Chart.yaml` (`appVersion` 1.0.0, a description nobody read) | `version` 0.2.2, `appVersion` 1.8.6 matching `image.tag` | Three numbers that disagree are worse than one number that is wrong |

The route is untouched: `koillection.kub.home.ashudev.com`, `websecure` with the `myresolver`
certificate resolver, the chart's own `TLSOption`, `lan-only` only. This is a public chart, and
rebuilding it is not permission to redesign the certificate or the gate. The only edit inside those
templates is the Service port name (`koillection` → `http`), to match the renamed Service port.

## The init needs root

Two syncs settled this, and both failures are worth keeping.

The image has no `USER` instruction, so it starts as root, and its entrypoint writes
`/usr/local/etc/php/conf.d/php.ini`, `/app/public/.env.local` and the `/uploads` symlink before it does
anything else. It then rewrites the `koillection` user to `PUID`/`PGID`, chowns `/uploads` and
`setpriv`s to that user for migrations and the server.

1. **Root with every capability dropped** fails at its first write: `/app/public` belongs to uid 1001,
   and root without `CAP_DAC_OVERRIDE` cannot write another uid's directory —
   `touch: cannot touch '/app/public/.env.local': Permission denied`.
2. **Running as uid 1001** gets past that and dies on `php.ini`:
   `/usr/local/etc/php/conf.d/php.ini: Permission denied`, because that file is root-owned and the
   entrypoint cannot skip it.

So the chart keeps the default capability set and sets only `allowPrivilegeEscalation: false`, the
same shape `prowlarr` uses for its LinuxServer init. Hardening that cannot start the app is not
hardening.

## Where the volume class came from

Uploads are 20 GiB of item photos and documents, so they do not belong on a node disk. Of the NFS
classes the cluster has ([`docs/how-it-works/storage.md`](../../../docs/how-it-works/storage.md)),
`stash-nfs` is the one recorded as "assets and documents"; the others are film, music and the book
library itself. Change `app.storage.uploads.className` if that is the wrong corner of the NAS export.

## Sizing

Unmeasured. The pod has not run since the Longhorn failure, so there is no `container_memory_working_set_bytes`
history to size from, and the numbers above are a floor plus a headroom, not evidence.
`PHP_MEMORY_LIMIT` is 128M for the app's single PHP process, and the 512Mi limit leaves room for
FrankenPHP's Caddy side and the spike from resizing an uploaded image. Run it through the method in
[`docs/reference/chart-sizing.md`](../../../docs/reference/chart-sizing.md) once it has history.

## What the first start looks like

**The old data is gone and this starts empty.** The previous claims went with Longhorn, and the
shared PostgreSQL instance was rebuilt empty on 2026-11-13 — its database log
([`docs/runbooks/postgres-add-database.md`](../../../docs/runbooks/postgres-add-database.md#step-5--record-it))
lists only `acceptance` and `authentik`, so no koillection database and no koillection dump exists.
Contents were abandoned by decision.

After the sync:

1. **The database has to be created by hand first.** Follow
   [`docs/runbooks/postgres-add-database.md`](../../../docs/runbooks/postgres-add-database.md) with
   `APP=koillection`, `APP_NS=koillection`, `APP_SECRET=koillection`, `APP_SECRET_KEY=DB_PASSWORD`.
   `koillection` is already in `networkPolicy.consumerNamespaces`. The pod will start without it and
   fail on first query, so do this before judging the sync.
2. Sign in (Koillection creates its own account on first visit) and upload one item with a photo.
3. Restart the pod and confirm the photo is still there: that is the proof the uploads claim
   survives a restart.

`DB_VERSION` stays at `16.3.0` on purpose: it is Doctrine's declared feature level, not the server's
version. The shared instance runs 18.7, and declaring a level lower than the server only narrows the
SQL Doctrine emits.

## Known debt

- **The `koillection-conf` claim holds nothing.** The 1.8.x image writes only to `/uploads` and the
  database; `/conf` appears in the old chart and nowhere in the image. The claim and the mount are
  kept because the card asked for both and the old Deployment mounted that name. Removing them is
  follow-up work.
- **`HTTPS_ENABLED` is `0`.** TLS ends at Traefik and the pod is reached over plain HTTP, so the
  session cookie is not marked secure. Unchanged from the previous chart; flipping it needs a login
  test through the public route first.
- **No log-level knob.** The image has no variable for the application's log level; Symfony defaults
  to `warning` in `prod`, so the chart sets nothing there rather than inventing a variable that does
  nothing.
- **No exporter.** The image ships no Prometheus endpoint, and there is no maintained sidecar for
  this app, so the chart renders no scrape annotations.
