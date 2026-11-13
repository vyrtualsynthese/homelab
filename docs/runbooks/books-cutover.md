# Books cutover: Calibre desktop out, Calibre-Web Automated in, Kavita back up

> **Destructive.** This deletes the running Calibre Deployment and every other object its
> Application created, and it is the moment the library's single writer changes hands. The books
> themselves are not touched, and one copy of `metadata.db` is taken before anything is deleted.

One sitting of about an hour, plus two steps a week later. CWA (Calibre-Web Automated) becomes the
only writer of the library on the NAS share; Kavita reads the same share through a read-only mount
and is the only authority on reading position. Nothing is synced to the cluster yet when you start:
both Applications are manual-sync, so every apply below is a button you press.

- Design: [`docs/superpowers/specs/2026-11-13-books-library-design.md`](../superpowers/specs/2026-11-13-books-library-design.md)
- Charts: [`calibre-web-automated`](../../cluster/helm/media-stack/calibre-web-automated/README.md),
  [`kavita`](../../cluster/helm/media-stack/kavita/README.md)
- One-writer rule and the `lidarr` trap: [`docs/charts/README.md`](../charts/README.md)

## What "done" looks like

- `calibre-web-automated` and `kavita` are `Synced`/`Healthy` in ArgoCD, one pod each, `Running` on
  `streaming`.
- `http://calibre.kub.local/` answers the CWA login; `https://calibre.kub.local/` does not reach the
  app (the HTTPS router is gone by decision).
- `calibre-web-automated-allowlist` exists in the cluster and the IngressRoute names it.
- One real book went in through CWA and came out converted, with metadata, visible under the
  library.
- Kavita has a library over `/books`, a scan completed, a book opens in the browser reader, and a
  reading position survives a refresh.
- No `calibre` Deployment, Service, Ingress, ConfigMap or ServiceAccount is left in `media-stack`.
- `metadata.db.pre-cwa-2026-11-13` sits next to `metadata.db` on the share.

Three runtime checks are decided here, not before: whether CWA survives a read-only root filesystem
(step 8), whether Kavita survives the same setting (step 9), and whether Kavita honours
`Logging__LogLevel__*` (step 9). Both charts ship the read-only setting on trial and both READMEs say
so.

## Before you start

**Access.** Cluster-admin `kubectl` for the whole procedure, and a browser on the LAN.

```bash
kubectl config current-context
kubectl auth can-i delete deployments -n media-stack   # expect: yes
```

**The charts are on `develop`.** ArgoCD reads `develop`, not this branch. The `argocd-root`
Application syncs `cluster/argocd` on its own, so once the merge lands both Applications appear with
no hand action.

```bash
git fetch origin
git cat-file -e origin/develop:cluster/helm/media-stack/calibre-web-automated/Chart.yaml \
  && echo "CWA chart on develop"
git cat-file -e origin/develop:cluster/helm/media-stack/kavita/Chart.yaml && echo "kavita chart on develop"
kubectl -n argocd get applications.argoproj.io calibre-web-automated kavita
```

Expected: both echo lines, then two rows — `calibre-web-automated` and `kavita`, either `OutOfSync`
or `Synced`. If either Application is `NotFound`, the merge has not reached the cluster yet: wait for
the root app, or nudge it, then re-check.

```bash
kubectl -n argocd annotate application argocd-root argocd.argoproj.io/refresh=normal --overwrite
```

**Nothing else writes the library.** The only two workloads that may mount
`pvc-books-media-nfs-subdir-external-provisioner` after this run are CWA (read-write) and Kavita
(read-only). Today it is Calibre alone.

**Links that will break.** Anything pointing at `https://calibre.kub.local` stops working, and the
old UI is gone at the same time. Dashy's configuration lives in a Secret, not in git:

```bash
kubectl -n dashy get secret dashy-conf \
  -o go-template='{{range $k, $v := .data}}{{$k}}={{$v | base64decode}}{{"\n"}}{{end}}' \
  | grep -n calibre
```

Expect one tile, on `https://calibre.kub.local`. Change it to `http://calibre.kub.local` now or in
Dashy's UI after the switch; also check phone home-screen shortcuts. The `calibre-gui.kub.local`
host disappears with the old Ingress and has no replacement: the KasmVNC desktop is the thing being
retired.

## Step 1 — Preflight: find the library and its database

```bash
kubectl -n media-stack exec deploy/calibre -- sh -c 'find /books -maxdepth 3 -name metadata.db; ls -ln /books | head -5'
```

Expected: exactly one path, `/books/metadata.db`, then five lines of the library root, owned by
`1100`.

- **One path** — note it. The rest of this runbook assumes `/books/metadata.db`; if yours sits
  deeper (say `/books/Calibre Library/metadata.db`), substitute it in step 2 and use the same
  relative path under `/calibre-library` in steps 8 and 10. CWA finds it either way.
- **More than one** — stop. CWA's auto-detect mounts the *largest* database it finds, so more than
  one is a decision, and the wrong one is discovered days later. Move the extra files off the share,
  re-run this command, then continue.
- **Nothing** — stop. The library is not where the design assumes and the cutover has nothing to
  adopt.

The library is on the share, not in Calibre's `/config`; `/config` holds Calibre's own settings and
is a claim that is already being deleted, which is why step 2 treats its backup as best-effort.

## Step 2 — Take the recovery point

The database is the only thing whose loss costs work rather than downtime: the books stay on the
share with their own metadata, but a broken `metadata.db` is a re-catalogue.

**Nothing may be importing when the copy is taken.** Check Calibre's activity view in its UI and let
it go quiet first: SQLite writes in place, so a file copied mid-write is torn, not merely stale — and
this copy is what rollback case A restores. A torn copy is worse than none, because it looks valid.

**The copy is not optional, and it needs the old pod running.** If the `exec` below fails, stop. The
pod may be failing on its Longhorn mount, which is already in deletion; find out with
`kubectl -n media-stack get pods -l app.kubernetes.io/name=calibre` and
`kubectl -n media-stack describe pod -l app.kubernetes.io/name=calibre`. Take the copy from a
throwaway pod instead — the same pattern as rollback case A, with the `cp` arguments swapped for a
copy out of `/books` — and only continue once you have it.

```bash
DB=/books/metadata.db        # the path step 1 printed
kubectl -n media-stack exec deploy/calibre -- cp -v "$DB" "$DB.pre-cwa-2026-11-13"
kubectl -n media-stack exec deploy/calibre -- ls -l "$DB" "$DB.pre-cwa-2026-11-13"
```

Expected: `... -> ...` from `cp -v`, then two lines, same size, the copy's timestamp now. **This copy
stays on the share** as the recovery point until the cluster-wide backup workstream covers this
share; do not clean it up later.

Then the best-effort copy of Calibre's own state. The `|| true` is deliberate: that volume is a
Longhorn claim in deletion, the kubelet has already reported failed mounts, and Calibre's settings
are not read by CWA anyway, so a failure here costs nothing:

```bash
kubectl -n media-stack exec deploy/calibre -- sh -c 'tar czf "/books/calibre-config-$(date +%F).tgz" -C /config .' || true
```

Expected: either silence (wrote the tarball) or an error such as `tar: .: file changed as we read it`
or `Invalid argument`. Both are acceptable. If it worked, `ls -lh /books/*.tgz` inside the pod shows
the file; it is deleted in step 11, once nothing needs it.

## Step 3 — Stop the old writer and remove everything it left behind

Two reasons, in order of importance: from the delete onward there is exactly one writer to the
library, and the old Ingress answers `Host(calibre.kub.local)` on `web,websecure` with a redirect to
HTTPS. ArgoCD does not prune, so that Ingress survives its Deployment and would shadow the new
`web`-only router and answer 502 through a Service with no endpoints.

```bash
kubectl -n media-stack delete deploy calibre
kubectl -n media-stack delete deploy,svc,ingress,configmap,serviceaccount -l app.kubernetes.io/instance=calibre
```

Expected: `deployment.apps "calibre" deleted`, then one line each for `service "calibre"`,
`ingress.networking.k8s.io "calibre"`, `configmap "calibre-env"` and `serviceaccount "calibre"`.
Every one of those objects carries the `app.kubernetes.io/instance=calibre` label (checked against
the live cluster on 2026-11-13), and nothing else in the namespace does. The claim
`calibre-config` is **not** labelled and is **not** deleted: it is the only surviving copy of
Calibre's settings. It cannot serve a new pod — the claim is already `Terminating` — so treat it as a
data artefact, not a rollback path.

Verify, and expect both to say `No resources found`:

```bash
kubectl -n media-stack get deploy,svc,ingress,configmap,serviceaccount -l app.kubernetes.io/instance=calibre
kubectl -n media-stack get pods -l app.kubernetes.io/name=calibre
```

If the label sweep deletes nothing (an object rebuilt outside Helm, labels lost), delete by name:

```bash
kubectl -n media-stack delete svc/calibre ingress/calibre configmap/calibre-env serviceaccount/calibre
```

The old Ingress served two hosts, `calibre.kub.local` **and** `calibre-gui.kub.local`; both go with
it.

> **Do not sync the `calibre` Application from here on.** It stays in ArgoCD, shows `OutOfSync`, and
> is removed by the follow-up commit in step 11. A sync would recreate the Deployment, the Service
> and the shadowing Ingress.

## Step 4 — Make sure no old Kavita pod can claim the new volume

A `local-path` claim binds to whichever node the first pod that uses it schedules on. The old Kavita
Deployment still carries a `node-type: general` nodeAffinity, and its one pod was scheduled on
`nuc0` before the missing claim left it `Pending`. Whether any node still carries that label is not
something this runbook asserts; what matters is that the old pod spec is not the rebuilt one and must
not be a scheduling candidate while `kavita-config` is being bound. If the claim binds on `nuc0`, it
is welded there and the new pod never runs — the `lidarr` trap.

```bash
kubectl -n media-stack delete pod -l app.kubernetes.io/name=kavita
```

Expected: `pod "kavita-…" deleted`. The Deployment is still managing it and recreates a Pending pod
within seconds, so delete the pod's ability to come back as well — the sync in step 9 restores
`replicas: 1` from the chart:

```bash
kubectl -n media-stack scale deploy kavita --replicas=0
kubectl -n media-stack get pods -l app.kubernetes.io/name=kavita
```

Expected: `deployment.apps/kavita scaled`, then `No resources found`. The Application will read
`OutOfSync` until step 9; that is the expected state, not a fault.

## Step 5 — Sync Calibre-Web Automated

ArgoCD → Applications → `calibre-web-automated` → **Sync** → **Synchronize**. The CLI equivalent is
`argocd app sync calibre-web-automated`. There is no prune flag: this sync creates and updates, it
deletes nothing it does not render.

```bash
kubectl -n argocd get application calibre-web-automated
kubectl -n media-stack get deploy,pods -l app.kubernetes.io/instance=calibre-web-automated
kubectl -n media-stack get pvc calibre-web-automated-config calibre-web-automated-ingest
```

Expected within a couple of minutes: `Synced`/`Healthy`; one pod `1/1 Running` on `streaming`; both
claims `Bound` on `local-path`, `10Gi` and `1Gi`. A first start builds `/config` from scratch, so the
startup probe is allowed ten minutes before liveness is even considered.

```bash
kubectl -n media-stack logs deploy/calibre-web-automated -f
```

Expected: the `s6` init lines, then the web server on 8083. There must be no line containing
`read-only file system` — step 8 is where that becomes decisive, but a hard failure shows up here.

If the pod stays `Pending`, the reason is in
`kubectl -n media-stack describe pod -l app.kubernetes.io/instance=calibre-web-automated`; if a claim
stays `Pending`, in `kubectl -n media-stack describe pvc calibre-web-automated-config`. The common
cause is a claim bound to the wrong node — rollback case C.

> **Never delete `pvc-books-media-nfs-subdir-external-provisioner`** while chasing that, or anything
> else. It holds the books and its provisioner deletes the export's contents with the claim. The only
> claims this runbook may ever delete are `calibre-web-automated-config`,
> `calibre-web-automated-ingest` and `kavita-config`.

## Step 6 — Prove the allow list is attached

The route is LAN-only, and DNS is not the control: Traefik routes on the `Host` header, so the proxy
must reject requests from outside `192.168.1.0/24`. The chart renders a Middleware for that, and an
empty or misspelled field would render no Middleware at all — leaving the admin UI open, silently.
This check is the only proof:

```bash
kubectl -n media-stack get middleware calibre-web-automated-allowlist -o yaml
kubectl -n media-stack get ingressroute calibre-web-automated \
  -o jsonpath='{.spec.routes[0].middlewares[*].name}{"\n"}'
```

Expected: `spec.ipWhiteList.sourceRange: [192.168.1.0/24]` (the field name on Traefik 2.9.4; v3
renames it `ipAllowList`), and the router printing `calibre-web-automated-allowlist`.

Then the routes themselves:

```bash
curl -s  -o /dev/null -w '%{http_code}\n' http://calibre.kub.local/
curl -sk -o /dev/null -w '%{http_code}\n' https://calibre.kub.local/
```

Expected: `302` on HTTP (the redirect to `/login`), and on HTTPS anything **but** the app — `404` from
Traefik or a TLS/connection error. A `302`, `200` or login page there means an HTTPS router survived
step 3: `kubectl -n media-stack get ingress,ingressroute | grep calibre` should show only the new
IngressRoute.

The negative test — a request from outside the LAN — needs a client on another network. From a phone
on mobile data:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: calibre.kub.local' http://<public-address>/
```

Expected: `403`. Worth doing once; the object above is what makes it true.

## Step 7 — First login and the settings that matter

Open <http://calibre.kub.local/> and log in as `admin` / `admin123`, then change the password
immediately: this UI can delete books and rewrite files in the library.

The existing collection should already be listed. CWA adopts the `metadata.db` that step 1 found; it
does not import the collection again. **If the library is empty, stop before importing anything** —
that means the wrong database or none, and step 1's path is what to re-check.

Then set, in CWA's settings (the admin page, plus its own CWA tab; labels move between releases, the
outcomes below are the requirement):

| Setting | Target | Why |
|---|---|---|
| Auto-convert on import | **on**, target format EPUB | one target format for everything imported |
| EPUB fixer | **on** | repairs the EPUBs a conversion or a download got wrong |
| Auto-metadata fetch | **on**, provider order Google Books then Internet Archive | title, author and cover written into the file, so every reader sees them |
| Kobo sync | **off** | Kavita is the only authority on reading position |
| KOReader (KOSync) sync | **off** | same reason: two progress stores roll each other back |
| Processed-originals backup | **leave on** | `/config/processed_books` is the only per-book recovery point |

If a label has been renamed, match the meaning. Two progress stores is the failure this table exists
to prevent, so the sync toggles are the ones worth re-reading before you move on.

## Step 8 — Ingest one real book (runtime check 1: read-only root filesystem)

Upload a book you actually want through the UI's Upload button — not a test file, because the
metadata path is the half being verified. Watch the task/activity view, then check the filesystem:

```bash
kubectl -n media-stack exec deploy/calibre-web-automated -- sh -c 'ls -l /calibre-library | head; ls -l /config/processed_books | tail -5'
kubectl -n media-stack exec deploy/calibre-web-automated -- ls -l /calibre-library/metadata.db
```

Expected: a new author folder under `/calibre-library`, the original kept under
`/config/processed_books`, and `metadata.db` not zero and the same size or larger than the copy from
step 2. In the UI: the conversion finished, the title, author and cover are right, and the book
appears in the library.

**The read-only root filesystem is on trial.** `securityContext.readOnlyRootFilesystem: true` with
`emptyDir` at `/run` and `/tmp` is kept only if this ingest completed. If instead the container
restarts, or the ingest fails while the UI stays up:

```bash
kubectl -n media-stack get pods -l app.kubernetes.io/instance=calibre-web-automated
kubectl -n media-stack logs deploy/calibre-web-automated --tail=200 | grep -i 'read-only file system\|EROFS'
```

A match, a `CrashLoopBackOff`, or a failed task in the UI means the image writes outside the mounted
paths. Then, in a follow-up commit: set `securityContext.readOnlyRootFilesystem: false` in
`cluster/helm/media-stack/calibre-web-automated/values.yaml` (the two `emptyDir` volumes are gated on
the same value and drop with it), note the reason in that chart's README under *Read-only root
filesystem*, and resync. Do not leave the setting on if it breaks the app.

If the upload never appears, the CWA README's *Ingest* section has the two rules that cause it: the
file must be owned by 1100, and a partially written file must not be dropped into the ingest folder.
The navbar's **Library Refresh** re-runs the ingest over anything still waiting.

## Step 9 — Sync Kavita, first run, library scan (runtime checks 2 and 3: read-only root filesystem, log level)

Kavita comes after the repair so that its first index sees the library in its final shape: it
identifies a series by folder path, so a metadata edit that renames a folder afterwards costs that
series its reading position.

Sync `kavita` in ArgoCD (CLI: `argocd app sync kavita`), then:

```bash
kubectl -n argocd get application kavita
kubectl -n media-stack get pods -l app.kubernetes.io/instance=kavita
kubectl -n media-stack get pvc kavita-config
```

Expected: `Synced`/`Healthy`; one pod `1/1 Running` on `streaming`; `kavita-config` `Bound`, `2Gi`.
A first boot on a fresh `/config` creates the database and seeds the default user, which is what the
600-second startup probe covers.

The rebuilt chart no longer renders one Middleware the old one shipped. ArgoCD does not prune, so it
is still there and is removed by hand:

```bash
kubectl -n media-stack get middleware
kubectl -n media-stack delete middleware kavita-security
```

Expected: `kavita-security` in the list before (it is, as of 2026-11-13), then
`middleware.traefik.containo.us "kavita-security" deleted`. No route ever referenced it.

> **Do not delete `kavita-redirect-https` or `kavita-tlsoption`.** Both routes name them, and they
> keep their old names on purpose so this sync updated them in place instead of orphaning them.

Health, without going through the proxy:

```bash
kubectl -n media-stack port-forward svc/kavita 5000:5000
# second terminal
curl -s localhost:5000/api/health; echo
```

Expected: `Ok`. (`curl: (52) Empty reply` or a refused connection means the pod is not serving yet;
check `kubectl -n media-stack logs deploy/kavita`.)

**The read-only root filesystem is on trial here too.** `securityContext.readOnlyRootFilesystem: true`
with `emptyDir` at `/run` and `/tmp` stays only if the pod keeps running and the scan below completes
with it on. A restart, or a scan that fails while the UI stays up, is the signal:

```bash
kubectl -n media-stack get pods -l app.kubernetes.io/instance=kavita
kubectl -n media-stack logs deploy/kavita --tail=200 | grep -i 'read-only file system\|EROFS'
```

A match, a `CrashLoopBackOff`, or a failed scan means the image writes outside the mounted paths.
Then, in a follow-up commit: set `securityContext.readOnlyRootFilesystem: false` in
`cluster/helm/media-stack/kavita/values.yaml` (the two `emptyDir` volumes are gated on the same value
and drop with it), note the reason in that chart's README under *Hardening and the UID*, and resync.
Do not leave the setting on if it breaks the app.

Then the UI at <https://kavita.kub.home.ashudev.com/>: the first-run screen creates the admin account.
Nothing is migrated, so this is a first start. Then **Settings → Libraries → Add Library**: type
`Books`, folder `/books`, and let the scan finish.

Expected: the scan completes without errors in the UI, the series count looks like the library, a
book opens in the browser reader, and the position survives a page refresh.

**The log level is on trial.** ASP.NET Core reads `Logging__LogLevel__Default` and
`Logging__LogLevel__Microsoft`, which the chart sets to `Warning`; whether Kavita's own logger honours
them has never been seen in a running pod:

```bash
kubectl -n media-stack logs deploy/kavita --tail=200
```

Expected after a few page loads: warnings and errors only, no per-request lines. If it is still
printing `Request starting` / `Request finished` at `info`, Kavita's Serilog configuration overrides
the framework keys: remove both variables from `cluster/helm/media-stack/kavita/values.yaml`, commit,
and record why in that chart's README under *Logs*, then resync. A knob that does nothing is worse
than none, because the next person believes the level is set.

## Step 10 — Verification pass

One block, each expectation beside it. Run it with the browser closed and nothing uploading.

```bash
# Applications Synced/Healthy, two rows
kubectl -n argocd get applications.argoproj.io calibre-web-automated kavita

# two pods, both 1/1 Running on streaming, no calibre pod
kubectl -n media-stack get pods -o wide | grep -Ei 'calibre|kavita|NAME'

# three claims Bound: -config 10Gi, -ingest 1Gi, kavita-config 2Gi
kubectl -n media-stack get pvc | grep -Ei 'calibre-web-automated|kavita-config'

# exactly two writers-candidates: one line per Deployment, 2 in total
kubectl -n media-stack get deploy -o yaml \
  | grep -c 'claimName: pvc-books-media-nfs-subdir-external-provisioner'

# Kavita's mount of the library is read-only: true
kubectl -n media-stack get deploy kavita \
  -o jsonpath='{.spec.template.spec.containers[0].volumeMounts[?(@.name=="library")].readOnly}{"\n"}'

# 302
curl -s -o /dev/null -w '%{http_code}\n' http://calibre.kub.local/

# the allow list exists and is referenced
kubectl -n media-stack get middleware,ingressroute -l app.kubernetes.io/instance=calibre-web-automated

# nothing of the old stack is left
kubectl -n media-stack get deploy,svc,ingress,configmap,serviceaccount -l app.kubernetes.io/instance=calibre
```

Then the checks that need eyes:

- [ ] The library is visible to CWA: `kubectl -n media-stack exec deploy/calibre-web-automated -- ls /calibre-library | head`
      lists author folders.
- [ ] The files in the library belong to 1100, which is what Kavita reads as:
      `kubectl -n media-stack exec deploy/calibre-web-automated -- ls -ln /calibre-library | head -3`.
- [ ] `metadata.db` did not shrink across the first start (step 8's size, against step 2's copy).
- [ ] A book opened in Kavita's reader, and the position survived a refresh.
- [ ] One book downloaded through OPDS to the phone, read a page, position reported back. The
      catalogue is `/api/opds` on the same host, with the API key from **Settings → Account**.
- [ ] No pod logs errors once settled: Kavita carries `warning` and above only, after step 9's check;
      CWA logs at `INFO` by design (the recorded debt in its README), so read its errors, not its
      level.
- [ ] `https://kavita.kub.home.ashudev.com/` answers over TLS from the LAN, and plain HTTP there
      redirects to HTTPS.

## Step 11 — Cleanup: alerting as soon as Kavita is green, the rest a week later

The fallback the cutover leaves behind is a claim and a tarball; both are cleared here, once the new
stack has run.

**Remove Kavita's exclusion from the alerting as soon as Kavita is green** — that one does not wait
the week: Grafana → Alerting → Alert rules → `MediaDeploymentDown` → drop `kavita` from the exclusion,
save. Then update the two mentions in
[`docs/observability-conventions.md`](../observability-conventions.md) in a small PR — dashboards and
alert rules live in Grafana's database, never in git, so the document is the only record.

Everything below waits a week.

**Retire the old Application and its chart** in a follow-up commit: `cluster/argocd/projects/media-stack/calibre.yaml`
and `cluster/helm/media-stack/calibre/`. Do this before clearing the claim, so no root-app sync can
re-apply anything.

**Clear the dead claim**, PV first and then its claim — their finalizers name a CSI driver that no
longer exists, so they will never clear on their own:

```bash
kubectl patch pv calibre-config -p '{"metadata":{"finalizers":null}}' --type=merge
kubectl -n media-stack patch pvc calibre-config -p '{"metadata":{"finalizers":null}}' --type=merge
kubectl get pv calibre-config   # expect: NotFound
```

**Delete the tarball** if step 2 wrote one. The glob has to expand inside the container, so it runs
under `sh -c`:

```bash
kubectl -n media-stack exec deploy/calibre-web-automated -- sh -c 'rm -v /calibre-library/calibre-config-*.tgz'
```

The `metadata.db.pre-cwa-2026-11-13` copy stays: it is the recovery point until the backup
workstream covers this share.

## Rollback

The library was never copied, moved, re-imported or bulk-converted, so there is nothing to restore;
only the workflow changed hands. Four cases.

> **The books claim is never part of any recovery.** `pvc-books-media-nfs-subdir-external-provisioner`
> holds the collection, and its provisioner deletes the export's contents together with the claim.
> Delete only the three disposable claims: `calibre-web-automated-config`,
> `calibre-web-automated-ingest`, `kavita-config`.

**A. The database looks damaged after the first start.** Stop CWA and put the copy back. The copy is
one file next to the original, on the share:

```bash
kubectl -n media-stack scale deploy calibre-web-automated --replicas=0
kubectl -n media-stack run restore-db --rm -it --restart=Never --image=busybox:1.36.1 \
  --overrides='{"spec":{"securityContext":{"runAsUser":1100,"runAsGroup":1100},"containers":[{"name":"restore-db","image":"busybox:1.36.1","command":["sh","-c","cp -v /books/metadata.db.pre-cwa-2026-11-13 /books/metadata.db"],"volumeMounts":[{"name":"books","mountPath":"/books"}]}],"volumes":[{"name":"books","persistentVolumeClaim":{"claimName":"pvc-books-media-nfs-subdir-external-provisioner"}}]}}'
kubectl -n media-stack scale deploy calibre-web-automated --replicas=1
```

Then check the library in CWA before importing anything else. Books keep their own metadata, so a
lost database is a re-catalogue, not a lost collection.

**B. CWA starts but is unusable.** Its `/config` is disposable — fresh, and holding only what this
session created. Take the pod out first, so the claim's `pvc-protection` finalizer can clear, then
delete the claim, then resync to rebuild both:

```bash
kubectl -n media-stack scale deploy calibre-web-automated --replicas=0
kubectl -n media-stack delete pvc calibre-web-automated-config
# then sync calibre-web-automated in ArgoCD: the claim and the Deployment come back at replicas 1
```

**C. A claim bound to the wrong node** (`Pending`, `node(s) didn't find available persistent volumes to
bind`). The same order, for the same reason: scale the app to zero, delete *its* disposable claim,
resync. Deleting a claim while a pod still references it leaves the pod queued on the old claim's UID
and the claim stuck in `Terminating` under `pvc-protection`.

```bash
# <app> is calibre-web-automated or kavita; <claim> is one of the three disposable claims above
kubectl -n media-stack scale deploy <app> --replicas=0
kubectl -n media-stack delete pvc <claim>
# then sync <app> in ArgoCD
```

If the app is `calibre-web-automated`, its claim names are `calibre-web-automated-config` and
`calibre-web-automated-ingest`; if it is `kavita`, the claim is `kavita-config`. There is no case in
which `pvc-books-media-nfs-subdir-external-provisioner` is the claim to delete.

**D. Back to the Calibre desktop** is not a rollback path. Syncing the `calibre` Application would
recreate the container, but `calibre-config` is a Longhorn claim in deletion and cannot serve a new
pod, so the desktop needs a fresh config claim first; its settings are already gone. Treat that as
its own task, and note that the library itself is untouched either way.

## The one thing that cannot be undone

Step 11's finalizer patch and the deletion of the old chart discard Calibre's `/config`. Everything
before it is reversible from the share, the git history and the database copy.
