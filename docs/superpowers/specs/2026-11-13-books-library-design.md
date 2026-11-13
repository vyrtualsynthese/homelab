# books library: Calibre-Web Automated replaces Calibre desktop — design

- Date: 2026-11-13
- Status: awaiting approval
- Scope: a new chart `cluster/helm/media-stack/calibre-web-automated`, a rebuild of
  `cluster/helm/media-stack/kavita`, their two Applications, and the retirement of
  `cluster/helm/media-stack/calibre` and `cluster/argocd/projects/media-stack/calibre.yaml`.

## Intent

The operator keeps a book library on the NAS share `/mnt/storage/nfs/books` and reads it on a
tablet and a phone. Today two applications sit on that share:

- **Calibre** (`ghcr.io/linuxserver/calibre`), which is the whole Calibre desktop application run
  in a container and driven through a remote screen (KasmVNC) — heavy, awkward on a phone, and a
  second interface to visit.
- **Kavita**, the reading server, which is currently not running at all.

The workflow is: drop a book into the Calibre web UI, then open the desktop to repair whatever
title, author, series or cover the import got wrong. Two interfaces over one library, for one
result.

The goal is one interface that imports *and* repairs metadata, with the reading side left to the
tool that already does it well. The reading requirements are unchanged: read in a browser, download
to the phone or tablet for offline reading, and resume at the last position.

## What the applications need

Facts from the upstream source and the registry, not guessed:

| Fact | Source |
|---|---|
| CWA listens on **8083**; `CWA_PORT_OVERRIDE` moves it | `Dockerfile` `EXPOSE 8083` |
| State lives in `/config`, ingested files arrive in `/cwa-book-ingest`, the library is `/calibre-library` | `Dockerfile` `VOLUME` lines |
| The image is built on `ghcr.io/linuxserver/baseimage-ubuntu:noble`: root first, then `PUID`/`PGID` | `Dockerfile` stage 2 |
| `curl -fsL http://localhost:8083/` is the image's own healthcheck; `/` redirects to `/login` and answers 200 | `Dockerfile` `HEALTHCHECK` |
| The web UI's Upload button writes the file atomically and feeds the **same** ingest pipeline as the watched folder | CWA wiki, *Enhanced Ingest System* |
| Ingest converts to a target format, repairs EPUBs, fetches metadata, and keeps the original in `/config/processed_books` | CWA README, *features* |
| Cover and metadata edits made in the web UI are written **into the book file** | CWA README, *automatic enforcement* |
| `NETWORK_SHARE_MODE=true` turns off SQLite WAL, skips the recursive `chown`, and switches the watcher to polling | CWA README, *network shares* |
| The project wiki still calls NFS unsupported and refuses support for it | CWA wiki home (page older than the README feature) |
| Latest release **v4.0.8**; published to Docker Hub **and** GHCR, tag `v4.0.8` exists on `ghcr.io/crocodilestick/calibre-web-automated` | GitHub release, `docker-image-build-release.yml`, registry manifest |
| Kavita listens on **5000**, state in `/config`, library in `/books` | current chart, `templates/deployment.yaml` |
| `GET /api/health` is `[AllowAnonymous]` and returns `200 Ok` | `Kavita.Server/Controllers/HealthController.cs` |
| Kavita **0.9.1** exists on `ghcr.io/linuxserver/kavita`, full tag `v0.9.1.4-ls132` | registry tag list |
| Kavita syncs reading position two ways with KOReader since 0.8.7, and serves an OPDS catalogue | Kavita wiki |

## Verified on the cluster, 2026-11-13

Read through the read-only MCP service account, not assumed:

| Fact | Value |
|---|---|
| `calibre` Deployment | running, revision 3y, pod on **`nuc0`** through the retired `node-type: general` affinity, image `8.16.2`, `1Gi` limit, requests `2m`/`256Mi` |
| `calibre` mounts | `/config` → `calibre-config`; `/books` → `pvc-books-media-nfs-subdir-external-provisioner` |
| `calibre` pod security | `seccompProfile: Unconfined`; plain env delivered through the `calibre-env` ConfigMap (contract item 6); the pod mounts a ServiceAccount token it never uses |
| `calibre-config` | PVC **`Terminating`** since 2026-08-28, StorageClass `longhorn-static`, 5Gi; the PV carries finalizers `kubernetes.io/pv-protection` and `external-attacher/driver-longhorn-io`; the kubelet is already reporting `FailedMount … PVC is being deleted` |
| `kavita` Deployment | `Synced`, pod **`Pending`**, no `kavita-config2` claim in the namespace at all — the chart names a claim nothing ever created |
| `kavita` security | `securityContext: {}`, `serviceAccountName: default`, no probes, pod annotation `setGateway: "true"`, `node-type: general` affinity |
| `kavita` routing | two IngressRoutes (`kavita-public`, `kavita-public-redirect`) plus a `TLSOption`, all on `kavita.kub.home.ashudev.com` |
| Books volume | `192.168.1.41:/mnt/storage/nfs/books`, RWX, reclaim `Retain`, currently `Bound` |
| **The library** | **is `/books`, the NFS share** — confirmed by the operator on 2026-11-13; `metadata.db` sits there, not on the config volume |
| Both Applications | **no `syncPolicy.automated`** — manual sync, which matches what the operator said |

One consequence that was not in the first draft: **the Calibre config volume is already dying.** It
is a Longhorn claim in deletion whose driver no longer exists, and the kubelet has started failing to
mount it. That no longer threatens the library — the books and their database are on the share — but
it does mean the old Calibre instance cannot be restarted, and anything still living in `/config`
(Calibre's own settings, the KasmVNC configuration) is best-effort to copy.

So the library side of this change is a clean adoption: CWA opens a library that already exists, on
storage that already works, and the Calibre desktop's own state is not worth preserving.

## Goals

1. One web UI where a book is imported and its metadata repaired: CWA.
2. The existing library is used in place. No re-import of the collection, no renaming sweep.
3. Exactly one writer to `metadata.db` at any time.
4. Kavita back to `Running` on a fresh config claim, reading the same share, on 0.9.1, with probes
   and the contract's naming.
5. One authority for reading position: Kavita. CWA's own Kobo and KOReader sync stay off.
6. Both charts satisfy `docs/charts/README.md`, and the security pass below is done against the
   live objects, not only against the templates.
7. CWA is reachable from the local network only, enforced at the proxy as well as by DNS.
8. Everything ships through git; the only hand steps are the ones ArgoCD cannot express, and they
   are listed in the order they must run.

## Non-goals

- No download automation. The operator does not want it.
- No new exposure and no TLS change for Kavita. It keeps exactly one hostname, the public
  `kavita.kub.home.ashudev.com`, with the two IngressRoutes it has today (the `websecure` route and
  the `web` redirect) and its `TLSOption`. No `kub.local` route is added, by the operator's decision
  of 2026-11-13: the reader is meant to be reachable from outside the LAN.
- No move of the library off NFS. That is what disqualifies BookOrbit and BookLore and it is not
  being revisited.
- No second database service, no PostgreSQL, no MariaDB.
- No change to `cluster/helm/nfs/books-media` or its StorageClass.
- No repair of the other workload that sits on a vanished claim.
- No scheduled backup of `metadata.db` in this chart. The cutover takes one copy next to the
  original, and the recurring copy belongs to the backup workstream, which is broken cluster-wide
  (`AGENTS.md` §7.3) and is not a chart-shaped fix. The books themselves stay on the share and carry
  their own metadata, so a lost database is a re-catalogue, not a lost collection.
- No edit of Grafana. The `MediaDeploymentDown` rule currently excludes `kavita`; removing that
  exclusion happens in Grafana, by the operator, after Kavita is green.

## Anti-goals

- Running CWA and Calibre desktop against the same `metadata.db`. Two SQLite writers on NFS is the
  failure mode this design exists to avoid.
- An ingest folder nested inside the library mount: CWA's own documentation forbids binds made
  within other binds, and a library that contains its own import queue confuses the scanner.
- Turning on CWA's KOReader sync because it exists. Two progress stores is worse than one.
- Re-importing the collection to normalise it. It would rename every folder and orphan every series
  Kavita knows.
- Pointing readiness at the liveness path to fill the field.

## Approaches considered

**A. Keep Calibre and add Calibre-Web next to it.** The light web UI on the same library, the
desktop kept for repairs. Rejected: it does not remove the second interface, and it introduces a
second writer to the same database — the exact hazard the two-tool setup already carries.

**B. CWA replaces the Calibre desktop container.** One container, one UI, ingest and repair in the
same place, edits written into the files so the reader sees them. Chosen.

**C. Kavita only, plus a scheduled metadata script.** Lightest by far, but it turns metadata repair
into hand-editing EPUBs, which is a regression against the workflow the operator actually uses.

**D. BookOrbit, the successor to BookLore.** Best feature set of anything reviewed — twelve
metadata sources, drop-folder import, three-way progress sync between Kobo, KOReader and its web
reader, native iOS app. Rejected: its documentation states that network storage is unsupported and
such issues are closed without investigation, its Android app is "coming soon", and it requires
PostgreSQL.

## Values (shape)

`calibre-web-automated`:

```yaml
image:
  repository: ghcr.io/crocodilestick/calibre-web-automated
  tag: "v4.0.8"

strategy: Recreate

service:   { type: ClusterIP, port: 8083 }
container: { port: null }

ingressRoute:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  entryPoints: [web]
  host: calibre.kub.local
  pathPrefix: /
  # LAN only, enforced at the proxy: DNS alone is not access control.
  sourceRanges: ["192.168.1.0/24"]
  middlewares: []
  tls: { enabled: false, secretName: "", certResolver: "", options: "" }

storage:
  config:  { enabled: true, existingClaim: "", className: local-path, size: 10Gi, mountPath: /config }
  ingest:  { enabled: true, existingClaim: "", className: local-path, size: 1Gi,  mountPath: /cwa-book-ingest }
  library:
    claim: pvc-books-media-nfs-subdir-external-provisioner
    mountPath: /calibre-library
    readOnly: false

env:
  TZ: "Europe/Paris"
  PUID: "1100"
  PGID: "1100"
  NETWORK_SHARE_MODE: "true"

probes:
  startup:   { enabled: true, path: /, ... }
  liveness:  { enabled: true, path: /, ... }
  readiness: { enabled: false }

resources:
  requests: { cpu: 25m, memory: 512Mi }
  limits:   { memory: 2Gi }

nodeSelector:
  kubernetes.io/hostname: streaming
```

`kavita` keeps the reference shape already used by `sonarr` and `tautulli`, with `image.tag`
`v0.9.1.4-ls132`, `service.port: 5000`, a created `kavita-config` claim and probes on `/api/health`.
Its routing is the one place it diverges from the LAN-only reference: one host,
`kavita.kub.home.ashudev.com`, on `websecure` with `certResolver: myresolver` and the existing
`kavita-tlsoption`, plus the `web` route that redirects to it. The values carry both routes; no
`kub.local` host is rendered.

## Decisions

### Image and registry

`ghcr.io/crocodilestick/calibre-web-automated:v4.0.8`, verified present (manifest
`sha256:5e0037…88679`). Upstream builds and pushes the same build to Docker Hub and GHCR, so the
GHCR preference in the stabilisation design is satisfied without an exception.

The tag is pinned to the release, not `latest`, and `appVersion` is `v4.0.8` to match. Renovate will
propose the next `v4.x` tag the same way it does for the other media charts.

Kavita moves from the moving series tag `0.8.8` to the full release tag `v0.9.1.4-ls132`, which is
the local rule for LinuxServer images: the series tag moves, the full tag does not.

### Storage

Three mounts, each with a reason:

| Mount | Claim | Class | Size | Why |
|---|---|---|---|---|
| `/calibre-library` | `pvc-books-media-nfs-subdir-external-provisioner` (existing) | `nfs-books` | — | The library is on the NAS and stays there. Shared with Kavita, which mounts the same claim at `/books` |
| `/config` | `calibre-web-automated-config` (created) | `local-path` | 10Gi | Holds `app.db`, the Calibre configuration, thumbnails, and **a copy of every original it processes** under `/config/processed_books`. That backup is the safety net for a bad conversion, so it is kept. 128Mi would fill on the first import; 10Gi is a guess with headroom, to be re-sized from use |
| `/cwa-book-ingest` | `calibre-web-automated-ingest` (created) | `local-path` | 1Gi | The drop folder the ingest service watches. Small on purpose: files leave it as soon as they are processed, and the web UI is the documented path |

The ingest folder is deliberately **not** on the NAS. A second NFS provisioner for a sibling
directory would mean another StorageClass, another Application and another export to maintain, for
a path the operator will rarely use — dragging files into the web UI does the same job and is what
they do today. If bulk drops over NFS are wanted later, that is a separate change.

`/config` and `/cwa-book-ingest` are `local-path`, so they are welded to one node. The pod is
pinned to `streaming`, which is where the rest of the media stack and the GPU live, and both claims
are created by this chart on a fresh name, so no pre-existing volume is involved.

### Kavita's config claim

The chart currently mounts `kavita-config2` by hardcoded name. That claim **does not exist in the
namespace at all**, which is the whole reason the pod is Pending: the chart references a volume
nothing ever created. The rebuild creates `kavita-config` (2Gi, `local-path`, `Recreate`) and derives
the name from the release. 2Gi is above the 128Mi house standard for one reason: Kavita caches every
cover and page it renders under `/config`, and that cache grows with the library. The number is a
guess and is recorded as such.

Because a `local-path` claim is bound to whichever node the first pod schedules on, the old Pending
pod must not be a candidate. It is deleted before the first sync of the new chart, per the trap in
`docs/charts/README.md` (the `lidarr` case). The same applies to CWA, whose two claims are new and
whose pod moves from `nuc0` to `streaming`.

Because a `local-path` claim is bound to whichever node the first pod schedules on, the old Pending
pod must not be a candidate. It is deleted before the first sync of the new chart, per the trap in
`docs/charts/README.md` (the `lidarr` case).

### Single writer

CWA is the only writer. Kavita's mount of the same claim is declared `readOnly: true`, which proves
it in the pod spec instead of relying on Kavita's good behaviour. No other workload mounts that
claim.

The Calibre desktop Deployment is deleted **before** CWA starts, and its claim is kept for a week
as a fallback. It is not scaled to zero: a Deployment that still exists is a Deployment someone can
sync back.

### Library registration

CWA's library auto-detect searches the mounted directory recursively for `metadata.db` and mounts
the largest one it finds. If the preflight finds more than one, the design stops and the extra ones
are dealt with first; silently picking the wrong library would be discovered days later.

### Probes

- **CWA**: startup and liveness on `/`, which is the endpoint the image's own healthcheck uses. It
  redirects to `/login` and answers 200, which curl follows (`-L`), so it reports "the web stack is
  serving" rather than "the process exists". Startup gets a long budget: the first start creates a
  library database and Calibre's configuration from scratch.
- **CWA readiness: disabled.** There is no unauthenticated endpoint that reports dependency health.
  The real dependency — the NFS library — is not exposed by any route, so a readiness probe could
  only duplicate liveness. This is the documented exception already used by `tautulli` and
  `cross-seed`.
- **Kavita**: startup and liveness on `/api/health`, readyness disabled for the same reason. Its
  database lives on its own claim and a database that cannot be opened stops the process from
  starting, so there is no dependency that fails while the process stays healthy.

### Security context

CWA is a LinuxServer base image: `s6` starts as root, prepares `/config`, then drops to
`PUID`/`PGID`. So `runAsNonRoot` and `runAsUser` are not options, and the narrow capability set the
init needs is kept, exactly as `prowlarr` and `tautulli` do. `allowPrivilegeEscalation: false` and
`seccompProfile: RuntimeDefault` apply and break nothing. Release v4.0.8 moved the metadata enforcer
to the `abc` user, so files it creates inside the library belong to 1100 rather than root — which is
what Kavita needs to read them.

Kavita already runs as root-then-1100 and the chart keeps that; no change to the UID. That matters
because the library files are owned by 1100 today.

### Logs

**CWA**: no documented log-level variable exists. The contract requires `warning` and above, so the
implementation looks for a knob first (`CWA_*`, Flask `LOG_LEVEL`); if there is none, the chart sets
nothing and the README records the INFO volume as accepted debt, the same way `bazarr` does. The
library's own activity view is the operational record.

**Kavita**: ASP.NET Core reads `Logging__LogLevel__Default`, so the chart sets
`Logging__LogLevel__Default: Warning` and `Logging__LogLevel__Microsoft: Warning`, then verifies in
the pod logs that the level actually dropped. If Kavita's Serilog configuration overrides it, the
variables come out again and the README records why: a knob that does nothing is worse than none.

### Resources

Both are unmeasured, because neither app has run in its final shape.

- CWA: request `25m`/`512Mi`, limit `2Gi`. The limit is generous on purpose: a PDF-to-EPUB
  conversion runs Calibre and Ghostscript at once, and that is the peak. There is no CPU limit, per
  the house rule.
- Kavita: keep `15m`/`256Mi` request and the `1Gi` limit from the current chart, and replace the
  numbers from Prometheus after a week.

### Network share mode and its caveat

`NETWORK_SHARE_MODE=true` is required, not optional: it disables SQLite's write-ahead log (the
journal mode that assumes reliable file locking), skips the recursive `chown` that fails on NFS, and
switches the ingest watcher from filesystem events to polling.

The project's own wiki still says NFS is unsupported and support will not be given. The README of
the current release contradicts it and documents the mode. The operator is told the honest version:
this is expected to work, it is not contractually supported upstream, and the mitigations are the
original-file backups in `/config/processed_books` plus a manual copy of `metadata.db` taken before
the first start.

### Routing and local-network-only access for CWA

`calibre.kub.local` keeps the hostname, on the `web` entry point only. Today's route is a Traefik
`Ingress` from the remote chart with `web,websecure` and a redirect to HTTPS, so
`https://calibre.kub.local` answers today and will not afterwards. That is deliberate: `kub.local`
is the LAN-only naming, and every other LAN-only app in this stack uses `web`.

DNS is not access control. The host resolves only through the Pi-hole wildcard, but Traefik routes
by the `Host` header, so a request from the internet that sets `Host: calibre.kub.local` against the
public address would match the router. An admin UI that ships `admin`/`admin123` therefore gets a
real restriction, not a naming convention:

- the chart renders a `Middleware` in its own namespace with an IP allow list, attached to the
  route from values;
- the default list is the LAN subnet, `192.168.1.0/24` (the NAS is `192.168.1.41`);
- the cluster runs Traefik **2.9.4**, where the field is `ipWhiteList.sourceRange`. `ipAllowList` is
  the 2.11 rename; the builder confirms with `kubectl explain middleware.spec` before writing it and
  uses the field the running CRD has.

Reading from outside the LAN, if it is ever wanted, means adding the VPN range to the same list. The
fallback if the list is wrong is `kubectl port-forward`, which needs no route at all — which is why
a mistake here locks out the UI, not the data.

No TLS: `kub.local` is plain HTTP today and the allow list only passes local traffic. The default
credentials are changed on first login regardless.

### Reading, offline copies, and progress

Kavita is the only authority.

- **Browser**: Kavita's EPUB and PDF readers.
- **Phone and tablet, offline**: Kavita serves an OPDS catalogue (a standard feed reader apps can
  browse) and supports two-way KOReader progress sync since 0.8.7. Dedicated third-party clients do
  the download-and-resume job natively: Kover (Android, desktop, web), Turnleaf (Android, EPUB
  only), Inkita (Android). The chart README points at the Kavita wiki page rather than repeating it.
- **CWA's Kobo sync and KOReader (KOSync) sync stay off.** Two services writing the same reading
  position is how progress gets rolled back.

### Renames and Kavita's series identity

Kavita identifies a series by its folder path. When a metadata edit changes a book's author or
title, Calibre renames that book's folder, and Kavita then sees a different series with no reading
history.

Nothing is exposed to this today: Kavita's config volume is gone, so it holds no progress to lose.
The sequence uses that: the library is repaired and left alone **first**, and Kavita indexes it
once, afterwards. The README states the rule for the future — a folder-changing edit costs that
book's position — so it is a known trade-off rather than a surprise.

## Security and contract pass

Both charts are written against `docs/charts/README.md` and then reviewed as a rendered manifest, not
just as templates. The pass is a deliverable, not an outcome: the PR is not ready until every line
below is checked and the exceptions are written into the chart README.

### Pod hardening, both charts

| Control | Setting | Why |
|---|---|---|
| `seccompProfile` | `RuntimeDefault` | Free; breaks only things that need raw syscalls |
| `allowPrivilegeEscalation` | `false` | Prevents setuid escalation after the init drops |
| `privileged` | `false`, stated | The default, written down so a later "fix" has to argue with it |
| `capabilities` | `drop: [ALL]` plus `CHOWN`, `SETUID`, `SETGID` | Both images are LinuxServer builds whose `s6` init must `chown` the config volume and drop to `PUID`/`PGID`. The three calls are the whole reason root is needed; nothing else is granted |
| `runAsNonRoot` / `runAsUser` | not set | The init is root-then-`abc`; setting them stops the container from starting. This is the documented LinuxServer exception |
| `automountServiceAccountToken` | `false`, dedicated ServiceAccount | Neither app talks to the Kubernetes API, so neither carries a token |
| `hostNetwork` / `hostPID` / `hostIPC` | absent | Nothing here needs the node's namespaces |
| `readOnlyRootFilesystem` | attempt with `emptyDir` at `/run` and `/tmp` | Try it first: if the app starts and one ingest completes, keep it. If the image writes elsewhere, revert it and record why in the README rather than leaving a setting that does not work |

### What the pass replaces

Found in the live objects on 2026-11-13, each one something the new charts must not reproduce:

- `seccompProfile: Unconfined` on the Calibre pod. Nothing in that image needs it.
- Plain configuration (`TZ`, `PUID`, `PGID`) delivered through the `calibre-env` ConfigMap — contract
  item 6, and the reason those values cannot be seen in `kubectl describe pod`.
- A mounted ServiceAccount token on a Calibre pod that never calls the API.
- Kavita with `securityContext: {}`, `serviceAccountName: default`, and no probes at all.
- `node-type: general` affinity in both charts. The label is retired, and here it meant Calibre on
  `nuc0` rather than `streaming`.
- A pod annotation, `setGateway: "true"`, that nothing reads.
- A claim name typed into a template (`kavita-config2`) instead of derived from the release — the
  direct cause of a Pending pod.
- Port `32400` in Kavita's values against a container listening on `5000`.
- A public HTTPS route on an internal admin UI protected by `admin`/`admin123`.

### Contract compliance

| # | Requirement | Where it lands |
|---|---|---|
| 1–2 | `appVersion` equals a pinned `image.tag`; never `latest` | `v4.0.8` and `v0.9.1.4-ls132` |
| 3 | `strategy: Recreate` | Both have a config claim |
| 4 | Pod pinned to one node, claims land on it | `kubernetes.io/hostname: streaming` |
| 5 | Config claim `<fullname>-config`, `local-path` | CWA 10Gi and Kavita 2Gi, both with the stated reason in the README; the sizes are guesses and say so |
| 6 | Plain configuration inline in `env`, never a ConfigMap | `TZ`, `PUID`, `PGID`, `NETWORK_SHARE_MODE`, log levels |
| 7 | Every object name derived from the release | `app.fullname`, `app.selectorLabels`; no `kavita-config2`, no `calibre-config` |
| 8 | Traefik CRD group in a value | `traefik.containo.us/v1alpha1`, one-line change for v3 |
| 9 | `app.labels` on every object | PVC, Service, ServiceAccount, Deployment, IngressRoute, Middleware |
| 10–11 | No tabs; `helm lint` and `helm template` clean | plus `kubeconform -strict` on the rendered output |
| 12–13 | Liveness on the app's own endpoint; readiness disabled with a reason | `/` and `/api/health`; both READMEs record the exception |
| 14 | Memory request **and** limit on every container | CWA `512Mi`/`2Gi`, Kavita `256Mi`/`1Gi` |
| 15 | Hardening as above | Same table |
| 16 | Extra volumes as named values with explicit `mountPath` | `storage.library`, `storage.ingest` |
| 17 | Exporter enabled if the image ships one | Neither image ships one, so neither gets a scrape annotation. Silence beats a permanently-down target |
| 18 | Logs at `warning` and above | Kavita via `Logging__LogLevel__*`, verified in the pod; CWA's knob is the open item, recorded as debt if it does not exist |

Also in the pass, because they are the failures this repo has actually shipped:

- no secret value in `values.yaml`; CWA binds no Secret at all, and nothing is added for symmetry;
- Service `ClusterIP`, one port, no metrics port bolted on;
- no `node-type` label, no copy-pasted claim name, no inherited port number;
- `.helmignore` present, `Chart.yaml` with `name`, `description`, `version`, `appVersion`;
- `cluster/argocd/**` stays CRLF; the chart files stay LF.

The check that catches what grep cannot: `helm template` both charts, read the rendered Deployment
top to bottom once as a reviewer would, and compare the `securityContext`, the probes and the volumes
against the two tables above.

## Migration sequence

Order matters at three points: the old writer must stop before the new one starts, the old Pending
pod must be gone before the new claim is created, and Kavita indexes only after the library is
clean.

**0. Preflight.** Done. The library is `/books` on the NFS share. One cheap confirmation before
anything changes, because a wrong library costs a re-catalogue:

```bash
kubectl -n media-stack exec deploy/calibre -- sh -c 'find /books -maxdepth 3 -name metadata.db; ls -ln /books | head -5'
```

If it prints a `metadata.db`, proceed. If it prints nothing, stop: the library is somewhere the
operator did not expect and the whole plan needs the rescue step instead.

The rest is answered: claims, nodes, ownership (`1100`), sync policy (off), and the state of both
Applications.

**1. Commit** the CWA chart and Application and the Kavita rebuild, with the Calibre Application and
values still in the tree. Nothing is synced yet.

**2. Stop the old writer, and take one copy first.** Use the `metadata.db` path that step 0 printed:

```bash
kubectl -n media-stack exec deploy/calibre -- cp <path>/metadata.db <path>/metadata.db.pre-cwa-2026-11-13
kubectl -n media-stack exec deploy/calibre -- sh -c 'tar czf /books/calibre-config-$(date +%F).tgz -C /config .' || true
kubectl -n media-stack delete deploy calibre
```

The first line is the recovery point for the database: it stays on the share next to the original, is
one file, and is the thing to restore if CWA's first scan damages anything. The `|| true` on the
second line is deliberate — the config volume may be unreadable already, and Calibre's own settings do
not survive into CWA anyway. The important line is the delete: from that moment there is exactly one
writer to the library.

**3. Delete the old Kavita Pending pod** so the new claim binds to `streaming`:

```bash
kubectl -n media-stack delete pod -l app.kubernetes.io/name=kavita
```

**4. Sync the two Applications by hand.** CWA starts, finds `metadata.db`, registers it. First login
changes the `admin` password away from the default `admin123`, and the CWA settings are set:
auto-convert on, EPUB fixer on, auto-metadata fetch on with Google Books then Internet Archive, and
KOReader/Kobo sync off.

**5. Prove the ingest** with one real book: upload it in the UI, watch conversion and metadata
fetch, confirm the file appeared under the library and that the cover and title are right. Then
trigger a Kavita library scan and confirm the book shows up.

**6. Index in Kavita.** Create the admin account, add a Books library over `/books` (EPUB/PDF),
scan. This is the first time Kavita has a config volume in months, so everything is configured from
scratch.

**7. Clean up, in this order:** delete `cluster/argocd/projects/media-stack/calibre.yaml` and
`cluster/helm/media-stack/calibre/` in a follow-up commit, after the new stack has run for a few
days. Then clear the two dead claims by hand — they will never clear on their own, because their
finalizers name a CSI driver that no longer exists (`AGENTS.md` §7.4):

```bash
kubectl patch pv calibre-config -p '{"metadata":{"finalizers":null}}' --type=merge
kubectl -n media-stack patch pvc calibre-config -p '{"metadata":{"finalizers":null}}' --type=merge
```

`kavita-config2` needs no equivalent: no such claim exists, which is why the pod is Pending.

## Rollback

CWA adds books to the library and rewrites only the files it is asked to edit; it does not re-organise
the collection. So the rollback is: delete the CWA Application and its two claims, and point a Calibre
container at the same share again. The library was never copied, moved or converted in bulk, so there
is nothing to restore — which is also why the cutover is low-risk for the data and only the workflow
changes hands.

## Documentation changes

- `cluster/helm/media-stack/calibre-web-automated/README.md`: upstream links, the network-share
  caveat, the ingest and repair workflow, the backup location, the default credentials to change,
  why readiness is off, and that progress lives in Kavita, not here.
- `cluster/helm/media-stack/kavita/README.md`: upstream links, the config claim, probes, the
  progress-sync story and the rename caveat.
- `docs/charts/README.md`: two rows in the probes/hardening table and two paragraphs under "what
  good looks like"; note that CWA is the first chart whose config claim is sized in GiB with a
  stated reason.
- `AGENTS.md` §3.B names `calibre` as the pattern-B sample; with the app gone the sample becomes
  `filebrowser`. §1's chart count and the node table are updated with it. §7's known-issues list
  gains nothing and loses nothing: neither app was listed as broken there.

## Verification

Local, before the PR:

```bash
for c in calibre-web-automated kavita; do
  helm lint "cluster/helm/media-stack/$c" --namespace media-stack
  helm template "$c" "cluster/helm/media-stack/$c" --namespace media-stack >/dev/null
done
grep -rnP '\t' cluster/helm/media-stack/{calibre-web-automated,kavita}/templates || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/{calibre-web-automated,kavita}/values.yaml || echo "no floating tags"
grep -rn 'node-type\|kavita-config2\|32400\|networking.k8s.io' \
  cluster/helm/media-stack/kavita cluster/helm/media-stack/calibre-web-automated || echo "no leftovers"
```

Cluster, after each sync:

- CWA: Application `Synced`, pod `Running` as uid 1100, both claims `Bound`,
  `curl -s -o /dev/null -w '%{http_code}' http://calibre.kub.local/` answers `302`,
  `curl -s -k https://calibre.kub.local/` fails (the HTTPS route is gone by decision), `kubectl exec`
  shows the library visible at `/calibre-library` and `metadata.db` unchanged in size across the
  first start.
- Ingest: one book uploaded, converted if needed, metadata applied, visible in the library folder.
- Kavita: pod `Running`; `kubectl -n media-stack port-forward svc/kavita 5000:5000` then
  `curl -s localhost:5000/api/health` → `Ok`. The existing routes keep the public host, so there is
  no `kavita.kub.local` to check; browsing to `kavita.kub.home.ashudev.com` from the LAN is the
  end-to-end check. A library scan completes, a book opens in the browser reader, and the position
  survives a refresh.
- Offline: one book downloaded through OPDS to the phone, read a few pages, position reported back.
- Neither pod logs above `warning` once settled.

## Risks

- **SQLite on NFS.** Upstream calls it unsupported. Network-share mode removes the three known
  failure modes and the original-file backups cover a bad import, but a corrupted `metadata.db` is
  still possible. Severity is bounded: the books stay on the share and, after the first metadata
  pass, carry their own titles, authors and covers, so losing the database costs a re-catalogue, not
  the collection. The cutover copy is the recovery point until the cluster-wide backup workstream
  covers this share.
- **Two `metadata.db` files.** Auto-detect picks the largest. The preflight counts them first.
- **Claim bound to the wrong node.** The `lidarr` trap. The old Pending pod is deleted before the
  first sync; if the claim still binds wrongly, delete the claim, then delete the pod, then resync.
- **Conversion memory.** A large PDF-to-EPUB conversion can approach the 2Gi limit; the failure is
  an OOMKill of one ingest, visible in the CWA task view, not a crash loop.
- **Kavita's old config turns out to be readable.** Not applicable: no `kavita-config2` claim exists,
  so 0.9.1 is a first start. Anything restored from a backup would be a migration from a months-old
  version; don't.
- **The CWA host loses HTTPS.** Anything still pointing at `https://calibre.kub.local` — a bookmark,
  a dashboard tile, a phone home-screen shortcut — breaks on cutover, and the old Calibre UI is gone
  at the same time, so there is no fallback page to land on. Worth grepping `dashy`'s config before
  the switch.
- **Public hostname on the admin UI.** `calibre.kub.local` is LAN-only through Traefik's `web`
  entry point and the Pi-hole wildcard, but the app ships `admin`/`admin123`. If the host is ever
  moved to a public zone, it needs a middleware first. The README says so.
- **The reader is deliberately public.** `kavita.kub.home.ashudev.com` is the only hostname, it
  keeps its certificate and its redirect, and it serves a personal library to the internet. That is
  the operator's decision, not an oversight: reading away from home is the requirement. Any
  hardening — an auth middleware, a rate limit — is a separate change and must not be smuggled into
  a chart rebuild.

## Left to the builder

- Exact templates, helper reuse and comment prose; copy from `sonarr`/`tautulli` rather than
  inventing a shape.
- Whether the chart READMEs are one commit or two, and whether the Calibre removal is its own
  commit (recommended: yes, after both new charts are verified).
- Which CWA settings to enable by default, beyond the ones listed in step 4.
- Whether the `kavita` upgrade and the `kavita` chart rebuild land together or separately.
