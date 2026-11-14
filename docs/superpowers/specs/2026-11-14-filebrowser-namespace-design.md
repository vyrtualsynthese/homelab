# FileBrowser in its own namespace — design

- **Date:** 2026-11-14
- **Status:** awaiting operator approval
- **Owner:** operator. Decisions marked **(operator)** were taken in conversation; decisions marked
  **(design)** are this document's call and can be overruled.
- **Relation to other work:** this is the FileBrowser rebuild scheduled as W7.1 in
  [`2026-11-10-homelab-programme-index.md`](../plans/2026-11-10-homelab-programme-index.md). It
  supersedes the FileBrowser rows in [`docs/status.md`](../../status.md) once the cutover is done, and
  it removes FileBrowser from the pattern-B example list in `AGENTS.md` §3.

## 1. Intent

FileBrowser serves media over NFS and has nothing to do with the *arr stack beyond sharing the
volumes. Keep it working, give it its own namespace and lifecycle, and rebuild it on storage that
actually exists. The old configuration and database are broken and are not rescued **(operator)**.

FileBrowser is **not** put behind authentik in this change **(operator)**. It keeps its own login,
with a fresh admin account bootstrapped from a Secret. The authentik blueprint, provider and
`filebrowser-editors` group stay where they are, unused, for a later change.

Success is: `https://filebrowser.kub.home.ashudev.com` reaches FileBrowser's own login page, the pod
is 1/1, its config and database sit on `local-path`, its three media mounts still show the same NAS
content, and nothing in the deployment uses a core `networking.k8s.io/v1` Ingress.

## 2. What exists today

Evidence read from the cluster through the read-only `kubernetes` MCP on 2026-11-14.

| Item | Today |
|---|---|
| Application | `cluster/argocd/projects/filebrowser.yaml`, namespace `argocd` |
| Destination namespace | `media-stack` |
| Shape | Pattern B: upstream chart `filebrowser` `0.1.2` from `selfhosted-helmcharts` plus `cluster/helm/media-stack/filebrowser/values.yaml` |
| Auth in git | `strip-authentik-header` → `authentik-forwardauth` → `security`, all in `kube-system` |
| Auth live | `kube-system-authentik@kubernetescrd` (CORS only) + `kube-system-security@kubernetescrd`; no enforcement |
| Live image | `filebrowser/filebrowser:v2.63.2` (git pins `v2.63.23`) |
| Live resources | 512Mi request / 1Gi limit (git says 64Mi / 256Mi) |
| Live ingress object | `networking.k8s.io/v1` Ingress with Traefik annotations |
| Node | `nuc0` |

The Application is `OutOfSync`. Its live Ingress is 2y205d old and still carries the pre-SSO
annotations. The four middleware objects exist in `kube-system`, but **no router in the cluster uses
`authentik-forwardauth`**: `curl -sI https://filebrowser.kub.home.ashudev.com/` returns `200` with
FileBrowser's own login page. The forward-auth chain was committed in the SSO work but never
applied.

That is no longer the target state: FileBrowser is not managed by authentik for now **(operator)**.
The new router keeps only the security-header middleware; FileBrowser authenticates its own users.

### 2.1 Why the storage forces a rebuild

The config and database claims are `filebrowser-config` and `filebrowser-db-2` in `media-stack`.
Both are `longhorn-static` volumes and both are stuck `Terminating`. The cluster has **no CSIDriver
objects left at all**: Longhorn was removed after the 2026 disk failure. The pod has been running
since 2026-08-13 with restart count 0, so its mounts are still attached; any restart would fail to
attach them.

`docs/superpowers/plans/2026-11-10-w0-cleanup.md` already records both claims as abandoned and
FileBrowser as "rebuilt in W7.1". There is no data to migrate **(operator: clean rebuild)**. That
also means there is no surviving admin password; §7 bootstraps a new one.

### 2.2 The media mounts cannot move as claims

FileBrowser mounts three claims today:

| Mount path | Claim in `media-stack` | NFS export | Access |
|---|---|---|---|
| `/srv/various` | `pvc-stash-nfs-nfs-stash` | `192.168.1.41:/mnt/storage/nfs/stash` | RWO |
| `/srv/audio` | `pvc-nfs-audio-nfs-subdir-external-provisioner` | `192.168.1.41:/mnt/storage/nfs/audio` | RWX |
| `/srv/torrent` | `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` | `192.168.1.41:/mnt/ultrafaststorage/nfs` | RWO |

A PersistentVolumeClaim belongs to exactly one namespace, so the new namespace needs its own claims.
The bytes are on the NAS; new static volumes pointed at the same exports keep them.

## 3. Goals, non-goals, anti-goals

**Goals**

- FileBrowser runs in namespace `filebrowser`.
- Config and database are fresh `local-path` claims.
- The three media mounts show the same NAS content.
- Host and TLS are unchanged; FileBrowser uses its own login.
- Storage and routing are declared in git and rendered by ArgoCD.
- Only Traefik CRDs are used for routing, per `AGENTS.md` §4.
- The old Helm release can be removed cleanly; no orphan and no second router for the same host.

**Non-goals**

- Rescuing the old config/database.
- Wiring authentik forward auth, the embedded outpost, or the `filebrowser-editors` group into
  FileBrowser. Deferred **(operator)**.
- Moving the NFS provisioners out of `media-stack`; their StorageClasses stay as they are.
- Changing the authentik blueprint, provider, group or hostname.
- Moving the workload to `nas`. The live pod is on `nuc0` and stays there.
- Retiring the abandoned Longhorn claims; they are inert.

**Anti-goals**

- Two Ingress/IngressRoute objects matching the same host at the same time.
- Hand-created PVs/PVCs that git does not describe.
- A fresh database reachable with FileBrowser's default `admin`/`admin` credentials.
- An orphaned `filebrowser` Helm release left in `media-stack`.
- Touching the NFS exports' contents.

## 4. Constraints

- `AGENTS.md` §3.D: new work wraps an external chart, it does not copy it. `charts/` and
  `Chart.lock` are built by ArgoCD at sync time.
- `AGENTS.md` §4: Traefik `traefik.containo.us/v1alpha1` objects only; no
  `networking.k8s.io/v1` Ingress.
- `AGENTS.md` §4: workloads are pinned to a named node with `kubernetes.io/hostname`; the
  `node-type` label is retired from charts.
- `AGENTS.md` §4: stateful apps use `strategy: Recreate`, one claim per concern, `local-path` for
  config, and the dedicated NFS classes for shared media.
- `AGENTS.md` §5: Secrets are created by hand, never committed.
- `AGENTS.md` §7.1: no Application records `syncPolicy.automated`; sync state stays in the UI.
- `cluster/argocd/**` files use CRLF. Preserve it.
- The architect has no cluster write access. Cluster-side steps are run by the operator.

## 5. Approaches considered

**A — Wrapper chart, everything in GitOps (chosen, operator).** A local chart at
`cluster/helm/filebrowser` depends on upstream `filebrowser` `0.1.2`, turns the subchart Ingress off,
renders its own Traefik `IngressRoute` and `TLSOption`, and declares three static PV/PVC pairs for the
NFS exports. The two config/database claims are left to the subchart on the default `local-path`
class. Trade-off: more files and a dependency build at sync time, but storage and routing are
managed, and the core-Ingress defect goes away with the move.

**B — Keep pattern B, create storage by hand.** Fewer files, but the five claims would live outside
git and drift. Rejected: contradicts "changes go through git" and the pattern-D rule.

**C — Keep pattern B, mount NFS directly in the pod.** No PV/PVC at all. Smallest diff, but bypasses
the NFS provisioner convention the rest of the repo uses and loses claim lifecycle. Rejected.

## 6. Design

### 6.1 Namespace and placement

- Namespace `filebrowser` (same shape as `dashy`, `syncthing`, `thelounge`).
- Pod on `nuc0` via `nodeSelector: kubernetes.io/hostname: nuc0` **(design)**. This matches the live
  placement and avoids moving local-path writes to `nas`, whose local-path SSD is at 0 % of rated
  life (`docs/status.md`). The stabilisation design's placement table says `nas`; that drift is
  recorded here, not changed.

### 6.2 Chart

`cluster/helm/filebrowser`:

```text
Chart.yaml              dependency: filebrowser 0.1.2, repository selfhosted-helmcharts
values.yaml             values nested under `filebrowser:`
templates/nfs-volumes.yaml   3 PVs + 3 PVCs
templates/ingressroute.yaml  websecure route for the host
templates/tls-option.yaml    TLSOption named filebrowser-tlsoption
```

`values.yaml` keeps `fullnameOverride: filebrowser` so the subchart renders stable names:
`filebrowser` Service and the `filebrowser-config` / `filebrowser-database` claims. The subchart's
Ingress is disabled.

Login is bootstrapped from a hand-created Secret, so the subchart renders **no ConfigMap**
(`env.existingSecret` switches its Deployment to `envFrom.secretRef`):

| Setting | Value |
|---|---|
| image | `filebrowser/filebrowser:v2.63.23` |
| strategy | `Recreate` |
| resources | request 1m CPU / 64Mi; limit 256Mi |
| config claim | `filebrowser-config`, 1Gi, default class (`local-path`) |
| database claim | `filebrowser-database`, 1Gi, default class (`local-path`) |
| `env.existingSecret` | `filebrowser` |
| `podAnnotations` | `setGateway: "true"` kept for parity; it has no consumer in this repo **(design)** |

The Secret `filebrowser` in namespace `filebrowser` carries four keys, because the chart's
`envFrom` replaces the ConfigMap entirely:

| Key | Value |
|---|---|
| `FB_CONFIG` | `/config/settings.json` |
| `FB_DATABASE` | `/database/filebrowser.db` |
| `FB_USERNAME` | `admin` |
| `FB_PASSWORD` | bcrypt hash of the chosen admin password |

`FB_PASSWORD` is read only at first start, when the database is empty. It creates the first admin;
it does not rotate the password afterwards (use `filebrowser users update` for that). The Secret is
listed in `AGENTS.md` §5 as a pre-existing Secret.

### 6.3 NFS volumes

The wrapper declares both ends, because the subchart only knows how to consume an existing claim:

| PV | PVC | Export | Access | Mount |
|---|---|---|---|---|
| `pv-filebrowser-stash` | `filebrowser-stash` | `/mnt/storage/nfs/stash` | RWO | `/srv/various` |
| `pv-filebrowser-audio` | `filebrowser-audio` | `/mnt/storage/nfs/audio` | RWX | `/srv/audio` |
| `pv-filebrowser-download` | `filebrowser-download` | `/mnt/ultrafaststorage/nfs` | RWO | `/srv/torrent` |

All three use `storageClassName: ""`, `volumeName` pre-binding, reclaim policy `Retain`, and the same
mount options as the NFS provisioner charts (`nfsvers=4.2`, `rsize=1048576`, `wsize=1048576`,
`nolock`, `async`). Capacity is the 10Mi placeholder those charts use; the claim is pre-bound and NFS
enforces no quota, so the number gates nothing.

### 6.4 Routing

One `IngressRoute` in the `filebrowser` namespace:

- `entryPoints: [websecure]`, `match: Host(`filebrowser.kub.home.ashudev.com`)`;
- one middleware: `kube-system-security@kubernetescrd`;
- service `filebrowser` port 80;
- TLS `certResolver: myresolver`, `options: filebrowser-tlsoption`.

No authentik middleware. The live router's `authentik` CORS middleware is dropped along with the
never-applied forward-auth chain, because authentik is not in the path. The
`strip-authentik-header` and `authentik-forwardauth` objects stay in `kube-system`, unused, for the
later change.

No HTTP router is added: the current deployment has none either **(design)**. The `TLSOption` is the
same copy every other chart carries.

### 6.5 Application and removals

`cluster/argocd/projects/filebrowser.yaml` becomes a single-source pattern-D Application:

- destination namespace `filebrowser`;
- source `homelab.git` at `develop`, path `cluster/helm/filebrowser`;
- `CreateNamespace=true` stays; no automated sync is added.

Removed in the same change:

- `cluster/helm/media-stack/filebrowser/` (the old values-only directory);
- the `filebrowser` entry in the pattern-B examples in `AGENTS.md` §3.B, replaced in §3.D.

`.gitignore` gains `cluster/helm/filebrowser/charts/` and `cluster/helm/filebrowser/Chart.lock`,
following the monitoring wrappers.

### 6.6 Documentation

- `docs/runbooks/authentik-apps.md`: replace the FileBrowser forward-auth section with a short
  deferral note — local accounts for now, the proxy procedure stays in the SSO plan for the later
  change — and point at this design.
- `docs/status.md`: move FileBrowser from "Working, not yet rebuilt" to the rebuilt table, drop it
  from "Running on a borrowed volume" and "Drift, not breakage", adjust the counts, and replace
  "Forward auth through authentik" with the local-login state. Refresh the snapshot date with the
  observed cutover result.
- `docs/charts/README.md`: sizing row path `media-stack/filebrowser` → `filebrowser`.
- `docs/audit/argocd-helm.md`: pattern-B table row for FileBrowser updated to pattern D.
- `AGENTS.md`: pattern examples (§3.B and §3.D) and the §5 Secret table (`filebrowser` →
  `FB_CONFIG`, `FB_DATABASE`, `FB_USERNAME`, `FB_PASSWORD`). The chart count does not change.

## 7. Cutover

Run by the operator after the PR merges. Order matters: the old router must go before the new one
exists for the same host.

### 7.1 Preconditions

Create the namespace and the login Secret **before** the first sync, so the app never starts with
the default `admin`/`admin` account:

```bash
kubectl create namespace filebrowser --dry-run=client -o yaml | kubectl apply -f -

# The image's entrypoint passes arguments through to `filebrowser`.
HASH=$(kubectl -n filebrowser run fb-hash --rm -i --restart=Never --quiet \
  --image=filebrowser/filebrowser:v2.63.23 -- hash '<the admin password>' 2>/dev/null)

kubectl -n filebrowser create secret generic filebrowser \
  --from-literal=FB_CONFIG=/config/settings.json \
  --from-literal=FB_DATABASE=/database/filebrowser.db \
  --from-literal=FB_USERNAME=admin \
  --from-literal=FB_PASSWORD="$HASH"
```

Store the plain password in the password manager; the cluster only holds the hash.

`helm list -n media-stack` settles whether the old release has a Helm record. The first step handles
both outcomes.

### 7.2 Steps

```bash
# 1. Stop and remove the old release, whichever way it was installed.
#    The abandoned Longhorn config/db are lost here, by decision.
if helm status filebrowser -n media-stack >/dev/null 2>&1; then
  helm uninstall filebrowser -n media-stack
fi
kubectl -n media-stack delete deployment,service,ingress,configmap,serviceaccount \
  -l app.kubernetes.io/instance=filebrowser --ignore-not-found

# 2. Sync the root Application so it prunes the old Application object
#    and creates the new one.
argocd app sync argocd-root

# 3. Sync the new Application.
argocd app sync filebrowser

# 4. Confirm no core Ingress and no leftover old objects.
kubectl -n media-stack get deploy,svc,ingress,configmap,serviceaccount \
  -l app.kubernetes.io/instance=filebrowser
kubectl -n filebrowser get ingress
```

Step 4 must report no resources in both commands.

The admin account exists from the Secret. Add any non-admin local accounts the operator wants, with
their folder scopes:

```bash
kubectl -n filebrowser exec deploy/filebrowser -- filebrowser users add \
  '<username>' "$(openssl rand -hex 24)" --scope '<scope-path>' \
  --lockPassword --perm.execute=false
```

Do **not** run `filebrowser config set --auth.method=proxy`: that is the authentik path, deferred.

## 8. Verification

Repository side, before the PR:

- `helm dependency build cluster/helm/filebrowser` then
  `helm template test cluster/helm/filebrowser -f cluster/helm/filebrowser/values.yaml`.
- Rendered output contains one `IngressRoute`, one `TLSOption`, three `PersistentVolume`, three NFS
  `PersistentVolumeClaim`, two `local-path` claims, one `Deployment`, and **no** object with
  `kind: Ingress`.
- The `IngressRoute` middleware list contains `security` only — no `authentik` and no
  `authentik-forwardauth`.
- `grep` confirms no reference to `cluster/helm/media-stack/filebrowser` remains.

Cluster side, after the cutover:

- `kubectl -n filebrowser get pods` → 1/1, restarts 0.
- `kubectl -n filebrowser get pvc` → all five `Bound`; the two config claims on `local-path`.
- `kubectl -n filebrowser get pv` → three `pv-filebrowser-*` `Bound` to the right namespace.
- `kubectl -n filebrowser exec deploy/filebrowser -- ls /srv/various /srv/audio /srv/torrent`
  shows the NAS content.
- `curl -sI https://filebrowser.kub.home.ashudev.com/ | head -1` → `200` with the login page. A
  `302` would mean an authentik middleware is still in the path; that is a stop.
- Signing in with the Secret's admin account succeeds; a non-admin account sees only its scope.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Fresh database reachable with `admin`/`admin` | Namespace and Secret are created before the first sync (§7.1); the Secret sets the first admin |
| Secret missing, pod stuck `CreateContainerConfigError` | Precondition creates it; recreate the same way if lost; the password lives in the password manager |
| Changing `FB_PASSWORD` later does not rotate the password | Documented; use `filebrowser users update` for rotation |
| Old release left installed, two routers on one host | Conditional `helm uninstall` plus label-based delete before syncing the new app; step 4 checks for leftovers |
| Old release has no Helm record, so `helm uninstall` fails | Preflight `helm list`; the label-based delete is the fallback and runs either way |
| New claims attach to the old Longhorn PVs | New PV names and `volumeName` pre-binding; old claims stay in `media-stack` |
| NFS export path typo silently mounts an empty directory | Paths copied from `cluster/helm/nfs/*/values.yaml`; verification lists all three mounts |
| `local-path` is not the default class | It is the live default; values also can name it explicitly if that changes |
| ArgoCD cannot fetch the dependency | The same repo and version already build for the current Application; `helm dependency build` is exercised locally first |
| Authentik wiring is assumed live and locks out the app | The design removes authentik from the route; the deferral is recorded in the runbook and status |

## 10. Relation to the SSO plan

This design **defers**, and does not deliver, the FileBrowser steps in
[`2026-11-14-sso-access-model.md`](../plans/2026-11-14-sso-access-model.md) — Task 6 Step 5 (the
302 gate), Task 7 Step 4 (outpost attachment) and Task 8 (FileBrowser verification). The blueprint,
provider and `filebrowser-editors` group stay in authentik but are unused. When FileBrowser is
wired later, that plan's forward-auth chain and outpost attachment apply on top of the namespace,
chart and storage this design puts in place. Until then, the runbook marks FileBrowser as
local-auth.

## 11. Left to the builder

- Exact template decomposition, label set and comments.
- Whether to name `local-path` explicitly or rely on the default class.
- Chart `version` and `appVersion` values.
- Doc wording and the final status counts, taken from the cutover evidence.
- Whether the later authentik wiring gets its own design or reuses the SSO plan as-is.
