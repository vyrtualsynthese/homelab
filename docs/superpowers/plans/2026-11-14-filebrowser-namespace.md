# FileBrowser Namespace Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move FileBrowser from `media-stack` to its own `filebrowser` namespace, rebuilt as a local wrapper chart with fresh `local-path` config/database, the same three NFS media exports, and its own login.

**Architecture:** A pattern-D wrapper chart at `cluster/helm/filebrowser` depends on the upstream `filebrowser` `0.1.2` chart, disables its core Ingress, renders a Traefik `IngressRoute` and `TLSOption`, and declares three static NFS PV/PVC pairs. The two config/database claims are created by the subchart on `local-path`. The Application becomes single-source, pointed at the local chart, in namespace `filebrowser`.

**Tech Stack:** Helm 3, Kubernetes, ArgoCD, Traefik v2 CRDs, kubectl, git.

**Spec:** `docs/superpowers/specs/2026-11-14-filebrowser-namespace-design.md`

## Global Constraints

- Namespace is `filebrowser`. Pod pinned to `nuc0` with `nodeSelector: kubernetes.io/hostname: nuc0`; never `node-type`.
- Image `filebrowser/filebrowser:v2.63.23`; strategy `Recreate`; requests `1m` CPU / `64Mi`; limit `256Mi`.
- Config and database are fresh `local-path` claims: `filebrowser-config`, `filebrowser-database`, 1Gi each.
- Host `filebrowser.kub.home.ashudev.com`; `websecure`; `certResolver: myresolver`; `TLSOption` `filebrowser-tlsoption`.
- Middleware list is exactly `kube-system-security@kubernetescrd`. No `authentik`, no `authentik-forwardauth`.
- NFS server `192.168.1.41`; exports `/mnt/storage/nfs/stash` (RWO), `/mnt/storage/nfs/audio` (RWX), `/mnt/ultrafaststorage/nfs` (RWO); mount options `nfsvers=4.2`, `rsize=1048576`, `wsize=1048576`, `nolock`, `async`.
- NFS PVs/PVCs use `storageClassName: ""`, `volumeName` pre-binding, reclaim `Retain`, capacity `10Mi` placeholder.
- Secret `filebrowser` keys: `FB_CONFIG`, `FB_DATABASE`, `FB_USERNAME`, `FB_PASSWORD` (bcrypt). Never commit the Secret or the plaintext password.
- No `networking.k8s.io/v1` Ingress anywhere in the new chart.
- No `syncPolicy.automated` on the child Application.
- Every file in `cluster/argocd/**` is CRLF.
- `cluster/helm/filebrowser/charts/` and `Chart.lock` are gitignored; ArgoCD runs `helm dependency build`.
- Chart count stays 58 Applications; FileBrowser keeps its name.
- Commit messages use a Gitmoji prefix.

## Review Focus

- **Missing Secret.** A fresh database with no Secret starts on `admin`/`admin`, or the pod sits in `CreateContainerConfigError`. Task 2 proves the Deployment reads `envFrom.secretRef` and renders no ConfigMap; Task 8 creates the Secret before the first sync.
- **Two routers for one host.** The old release must be gone before the new Application syncs, or Traefik has two rules for `filebrowser.kub.home.ashudev.com`. Task 5 removes the old Application and values; Task 8 orders the uninstall before the new sync.
- **NFS path typo.** A wrong export silently mounts an empty directory. Task 3 asserts every rendered path against the `cluster/helm/nfs/*/values.yaml` values.
- **Wrong volume binding.** A new claim must not attach to the old Longhorn PVs. Task 3 asserts `volumeName: pv-filebrowser-*` and `storageClassName: ""`.
- **Authentik left in the path.** The app has no outpost; a leftover middleware locks out or redirects. Task 4 asserts the string `authentik` does not appear in the rendered chart.

---

### Task 1: Record the approved design and this plan

**Files:**
- Create: `docs/superpowers/specs/2026-11-14-filebrowser-namespace-design.md` (already written)
- Create: `docs/superpowers/plans/2026-11-14-filebrowser-namespace.md` (this file)

**Interfaces:**
- Consumes: nothing.
- Produces: the spec and plan paths every later task references.

- [ ] **Step 1: Stage both documents**

```bash
git add docs/superpowers/specs/2026-11-14-filebrowser-namespace-design.md \
        docs/superpowers/plans/2026-11-14-filebrowser-namespace.md
```

- [ ] **Step 2: Commit**

```bash
git commit -m "📝 docs: design and plan the FileBrowser namespace move"
```

- [ ] **Step 3: Verify the tree is clean apart from the work to come**

Run: `git status --short`
Expected: no output.

---

### Task 2: Wrapper chart skeleton and core values

**Files:**
- Create: `cluster/helm/filebrowser/Chart.yaml`
- Create: `cluster/helm/filebrowser/values.yaml`
- Modify: `.gitignore` (append the wrapper build outputs)

**Interfaces:**
- Consumes: the approved decisions in the spec §6.2.
- Produces: Service `filebrowser` port `80`; claims `filebrowser-config` and `filebrowser-database`; Deployment `filebrowser`; `envFrom.secretRef` name `filebrowser`.

- [ ] **Step 1: Add the wrapper build outputs to `.gitignore`**

Append after the existing `cluster/helm/authentik/Chart.lock` block:

```gitignore
# Built by ArgoCD at sync time
cluster/helm/filebrowser/charts/
cluster/helm/filebrowser/Chart.lock
```

- [ ] **Step 2: Write `cluster/helm/filebrowser/Chart.yaml`**

```yaml
apiVersion: v2
name: filebrowser
description: FileBrowser - media file browser, own namespace
type: application
version: 0.1.0
appVersion: "2.63.23"
dependencies:
  - name: filebrowser
    version: 0.1.2
    repository: https://vyrtualsynthese.github.io/selfhosted-helmcharts/
```

- [ ] **Step 3: Write the core `cluster/helm/filebrowser/values.yaml`**

The subchart's values stay nested under `filebrowser:`. These are the values that decide the move;
everything else comes from the subchart defaults.

```yaml
filebrowser:
  fullnameOverride: filebrowser
  replicaCount: 1

  image:
    repository: filebrowser/filebrowser
    tag: v2.63.23
    pullPolicy: IfNotPresent

  strategyType: Recreate

  podAnnotations:
    setGateway: "true"

  # The repo uses Traefik CRDs only; templates/ingressroute.yaml renders the route.
  ingress:
    enabled: false

  service:
    type: ClusterIP
    port: 80

  resources:
    requests:
      cpu: 1m
      memory: 64Mi
    limits:
      memory: 256Mi

  storage:
    config:
      class: ~
      accessModes: ReadWriteOnce
      dataSize: 1Gi
      existingClaim: ""
    database:
      class: ~
      accessModes: ReadWriteOnce
      dataSize: 1Gi
      subPath: ""
      existingClaim: ""

  # The Secret carries FB_CONFIG and FB_DATABASE too; the subchart renders no ConfigMap.
  env:
    existingSecret: filebrowser

  nodeSelector:
    kubernetes.io/hostname: nuc0

  tolerations: []
  affinity: {}
```

- [ ] **Step 4: Build the dependency and render**

```bash
cd cluster/helm/filebrowser
helm dependency build .
helm template test . -f values.yaml > /tmp/opencode/filebrowser-render.yaml
```

Expected: `charts/filebrowser-0.1.2.tgz` and `Chart.lock` exist, both ignored by git.

- [ ] **Step 5: Verify the core objects**

```bash
grep -c '^kind: Deployment$' /tmp/opencode/filebrowser-render.yaml                       # 1
grep -c 'image: "filebrowser/filebrowser:v2.63.23"' /tmp/opencode/filebrowser-render.yaml # 1
grep -c '^kind: PersistentVolumeClaim$' /tmp/opencode/filebrowser-render.yaml            # 2
grep -c '^kind: ConfigMap$' /tmp/opencode/filebrowser-render.yaml                        # 0
grep -c 'secretRef' /tmp/opencode/filebrowser-render.yaml                                # 1
grep -c 'configMapRef' /tmp/opencode/filebrowser-render.yaml                             # 0
! grep -q '^kind: Ingress$' /tmp/opencode/filebrowser-render.yaml
grep -A2 'nodeSelector:' /tmp/opencode/filebrowser-render.yaml | grep 'kubernetes.io/hostname: nuc0'
grep 'name: filebrowser-config$' /tmp/opencode/filebrowser-render.yaml
grep 'name: filebrowser-database$' /tmp/opencode/filebrowser-render.yaml
```

- [ ] **Step 6: Confirm the build outputs are ignored**

Run: `git status --short cluster/helm/filebrowser .gitignore`
Expected: only `?? cluster/helm/filebrowser/Chart.yaml` and `?? cluster/helm/filebrowser/values.yaml`; no `charts/` and no `Chart.lock`.

- [ ] **Step 7: Commit**

```bash
git add .gitignore cluster/helm/filebrowser/Chart.yaml cluster/helm/filebrowser/values.yaml
git commit -m "🚀 feat(filebrowser): add the wrapper chart core"
```

---

### Task 3: NFS media volumes

**Files:**
- Create: `cluster/helm/filebrowser/templates/nfs-volumes.yaml`
- Modify: `cluster/helm/filebrowser/values.yaml` (add `nfsServer`, `nfsMountOptions`, `nfsVolumes`, and the subchart `volumes`/`volumeMounts`)

**Interfaces:**
- Consumes: Task 2's chart and values.
- Produces: PVs `pv-filebrowser-stash`, `pv-filebrowser-audio`, `pv-filebrowser-download`; claims `filebrowser-stash`, `filebrowser-audio`, `filebrowser-download`; Deployment mounts `/srv/various`, `/srv/audio`, `/srv/torrent`.

- [ ] **Step 1: Add the NFS values**

Append to `values.yaml` above the `filebrowser:` block:

```yaml
nfsServer: 192.168.1.41
nfsMountOptions:
  - nfsvers=4.2
  - rsize=1048576
  - wsize=1048576
  - nolock
  - async

nfsVolumes:
  - claim: filebrowser-stash
    pv: pv-filebrowser-stash
    path: /mnt/storage/nfs/stash
    accessMode: ReadWriteOnce
  - claim: filebrowser-audio
    pv: pv-filebrowser-audio
    path: /mnt/storage/nfs/audio
    accessMode: ReadWriteMany
  - claim: filebrowser-download
    pv: pv-filebrowser-download
    path: /mnt/ultrafaststorage/nfs
    accessMode: ReadWriteOnce
```

Then add under `filebrowser:`:

```yaml
  volumes:
    - name: stash-volume
      persistentVolumeClaim:
        claimName: filebrowser-stash
    - name: audio-volume
      persistentVolumeClaim:
        claimName: filebrowser-audio
    - name: qbittorrent-download-volume
      persistentVolumeClaim:
        claimName: filebrowser-download

  volumeMounts:
    - name: stash-volume
      mountPath: /srv/various
    - name: audio-volume
      mountPath: /srv/audio
    - name: qbittorrent-download-volume
      mountPath: /srv/torrent
```

- [ ] **Step 2: Write `cluster/helm/filebrowser/templates/nfs-volumes.yaml`**

One loop renders both ends. `volumeName` is what prevents the binder from choosing another PV.

```yaml
{{- range .Values.nfsVolumes }}
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {{ .pv }}
  labels:
    app.kubernetes.io/name: {{ $.Chart.Name }}
    app.kubernetes.io/instance: {{ $.Release.Name }}
spec:
  capacity:
    storage: 10Mi
  accessModes:
    - {{ .accessMode }}
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  mountOptions:
    {{- toYaml $.Values.nfsMountOptions | nindent 4 }}
  nfs:
    server: {{ $.Values.nfsServer }}
    path: {{ .path }}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ .claim }}
  namespace: {{ $.Release.Namespace }}
  labels:
    app.kubernetes.io/name: {{ $.Chart.Name }}
    app.kubernetes.io/instance: {{ $.Release.Name }}
spec:
  accessModes:
    - {{ .accessMode }}
  storageClassName: ""
  volumeName: {{ .pv }}
  resources:
    requests:
      storage: 10Mi
{{- end }}
```

- [ ] **Step 3: Re-render**

```bash
cd cluster/helm/filebrowser
helm template test . -f values.yaml > /tmp/opencode/filebrowser-render.yaml
```

- [ ] **Step 4: Verify every volume and mount**

```bash
grep -c '^kind: PersistentVolume$' /tmp/opencode/filebrowser-render.yaml          # 3
grep -c '^kind: PersistentVolumeClaim$' /tmp/opencode/filebrowser-render.yaml     # 5
grep -c 'path: /mnt/storage/nfs/stash' /tmp/opencode/filebrowser-render.yaml      # 1
grep -c 'path: /mnt/storage/nfs/audio' /tmp/opencode/filebrowser-render.yaml      # 1
grep -c 'path: /mnt/ultrafaststorage/nfs' /tmp/opencode/filebrowser-render.yaml   # 1
grep -c 'volumeName: pv-filebrowser-' /tmp/opencode/filebrowser-render.yaml       # 3
grep -c 'storageClassName: ""' /tmp/opencode/filebrowser-render.yaml              # 6 (3 PVs + 3 PVCs)
grep -c 'claimName: filebrowser-stash' /tmp/opencode/filebrowser-render.yaml      # 1
grep -c 'claimName: filebrowser-audio' /tmp/opencode/filebrowser-render.yaml      # 1
grep -c 'claimName: filebrowser-download' /tmp/opencode/filebrowser-render.yaml   # 1
grep -c 'mountPath: /srv/various' /tmp/opencode/filebrowser-render.yaml           # 1
grep -c 'mountPath: /srv/audio' /tmp/opencode/filebrowser-render.yaml             # 1
grep -c 'mountPath: /srv/torrent' /tmp/opencode/filebrowser-render.yaml           # 1
```

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/filebrowser/templates/nfs-volumes.yaml cluster/helm/filebrowser/values.yaml
git commit -m "🚀 feat(filebrowser): declare the NFS media volumes"
```

---

### Task 4: Traefik route and TLS option

**Files:**
- Create: `cluster/helm/filebrowser/templates/ingressroute.yaml`
- Create: `cluster/helm/filebrowser/templates/tls-option.yaml`
- Modify: `cluster/helm/filebrowser/values.yaml` (add the `traefik:` block)

**Interfaces:**
- Consumes: Task 2's Service `filebrowser` port `80`.
- Produces: `IngressRoute` `filebrowser`, `TLSOption` `filebrowser-tlsoption`.
- Later tasks and the cutover refer to the route by name `filebrowser` and the middleware list by value.

- [ ] **Step 1: Add the Traefik values**

Append at the end of `values.yaml`:

```yaml
traefik:
  apiVersion: traefik.containo.us/v1alpha1
  host: filebrowser.kub.home.ashudev.com
  certResolver: myresolver
  tlsOption: filebrowser-tlsoption
  service: filebrowser
  port: 80
  middlewares:
    - namespace: kube-system
      name: security
```

- [ ] **Step 2: Write `cluster/helm/filebrowser/templates/ingressroute.yaml`**

```yaml
apiVersion: {{ .Values.traefik.apiVersion }}
kind: IngressRoute
metadata:
  name: {{ .Chart.Name }}
  namespace: {{ .Release.Namespace }}
  labels:
    app.kubernetes.io/name: {{ .Chart.Name }}
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  entryPoints:
    - websecure
  routes:
    - kind: Rule
      match: Host(`{{ .Values.traefik.host }}`)
      middlewares:
        {{- range .Values.traefik.middlewares }}
        - name: {{ .namespace }}-{{ .name }}@kubernetescrd
        {{- end }}
      services:
        - name: {{ .Values.traefik.service }}
          port: {{ .Values.traefik.port }}
  tls:
    certResolver: {{ .Values.traefik.certResolver }}
    options:
      name: {{ .Values.traefik.tlsOption }}
```

- [ ] **Step 3: Write `cluster/helm/filebrowser/templates/tls-option.yaml`**

Copy the TLSOption body every other chart carries (route it through the same values key):

```yaml
apiVersion: {{ .Values.traefik.apiVersion }}
kind: TLSOption
metadata:
  name: {{ .Values.traefik.tlsOption }}
  namespace: {{ .Release.Namespace }}
  labels:
    app.kubernetes.io/name: {{ .Chart.Name }}
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  minVersion: VersionTLS12
  cipherSuites:
    - TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA256
    - TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256
    - TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384
    - TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305
    - TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384
    - TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305
    - TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305
    - TLS_AES_256_GCM_SHA384
    - TLS_AES_128_GCM_SHA256
    - TLS_CHACHA20_POLY1305_SHA256
    - TLS_FALLBACK_SCSV
  curvePreferences:
    - CurveP521
    - CurveP384
  sniStrict: false
```

- [ ] **Step 4: Re-render and verify routing**

```bash
cd cluster/helm/filebrowser
helm template test . -f values.yaml > /tmp/opencode/filebrowser-render.yaml

grep -c '^kind: IngressRoute$' /tmp/opencode/filebrowser-render.yaml             # 1
grep -c '^kind: TLSOption$' /tmp/opencode/filebrowser-render.yaml                # 1
grep -c '^kind: Ingress$' /tmp/opencode/filebrowser-render.yaml                  # 0
grep -c 'authentik' /tmp/opencode/filebrowser-render.yaml                        # 0
grep -c 'kube-system-security@kubernetescrd' /tmp/opencode/filebrowser-render.yaml # 1
grep -c 'filebrowser.kub.home.ashudev.com' /tmp/opencode/filebrowser-render.yaml # 1
grep -c 'certResolver: myresolver' /tmp/opencode/filebrowser-render.yaml         # 1
grep -c 'name: filebrowser-tlsoption' /tmp/opencode/filebrowser-render.yaml      # 2 (metadata + options)
```

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/filebrowser/templates/ingressroute.yaml \
        cluster/helm/filebrowser/templates/tls-option.yaml \
        cluster/helm/filebrowser/values.yaml
git commit -m "🚀 feat(filebrowser): route the app through Traefik CRDs"
```

---

### Task 5: Application, old values removal, CRLF

**Files:**
- Modify: `cluster/argocd/projects/filebrowser.yaml` (all lines; preserve CRLF)
- Delete: `cluster/helm/media-stack/filebrowser/values.yaml` (and the now-empty directory)

**Interfaces:**
- Consumes: Task 4's chart path `cluster/helm/filebrowser`.
- Produces: namespace `filebrowser`; Application name `filebrowser`; no reference to the old path.

- [ ] **Step 1: Rewrite the Application to pattern D**

Content, with CRLF line endings:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: filebrowser
  namespace: argocd
spec:
  project: default
  destination:
    server: 'https://kubernetes.default.svc'
    namespace: filebrowser
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/filebrowser
```

If the editor writes LF, convert the file back:

```bash
perl -pi -e 's/\r?\n/\r\n/' cluster/argocd/projects/filebrowser.yaml
```

- [ ] **Step 2: Delete the old values directory**

```bash
git rm cluster/helm/media-stack/filebrowser/values.yaml
rmdir cluster/helm/media-stack/filebrowser 2>/dev/null || true
```

- [ ] **Step 3: Verify the Application and the removal**

```bash
file cluster/argocd/projects/filebrowser.yaml                       # CRLF line terminators
grep -c $'\r' cluster/argocd/projects/filebrowser.yaml              # equals the line count
grep -n 'namespace: filebrowser' cluster/argocd/projects/filebrowser.yaml
grep -n 'path: cluster/helm/filebrowser' cluster/argocd/projects/filebrowser.yaml
! grep -q 'media-stack' cluster/argocd/projects/filebrowser.yaml
! grep -q 'automated' cluster/argocd/projects/filebrowser.yaml
test ! -d cluster/helm/media-stack/filebrowser
! grep -rn 'cluster/helm/media-stack/filebrowser' --exclude-dir=.git --exclude-dir=superpowers .
```

- [ ] **Step 4: Run the repo's reference checks from `AGENTS.md` §6**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")

while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u)
```

Expected: no `BROKEN PATH .*filebrowser` and no `BROKEN VALUES .*filebrowser`. The pre-existing
`plex-media` and `nfs-audio` defects may still print; they are out of scope.

- [ ] **Step 5: Commit**

```bash
git add cluster/argocd/projects/filebrowser.yaml
git commit -m "🚀 feat(filebrowser): deploy the local chart into its own namespace"
```

---

### Task 6: Documentation

**Files:**
- Modify: `AGENTS.md` (§3.B example and list, §3.D examples, §5 Secret table, §6 dependency-build list)
- Modify: `docs/runbooks/authentik-apps.md` (FileBrowser section)
- Modify: `docs/charts/README.md` (sizing row path)
- Modify: `docs/audit/argocd-helm.md` (pattern table)

**Interfaces:**
- Consumes: the namespace and chart path from Task 5.
- Produces: no stale `media-stack` FileBrowser command or pattern-B listing.

- [ ] **Step 1: Update `AGENTS.md` §3.B**

Replace the `filebrowser` example block with a pattern-B app that still exists (`books-media` or
`nfs-audio`): change `chart: filebrowser` to `chart: nfs-subdir-external-provisioner`,
`targetRevision: 0.1.2` to `4.0.18`, and the value path to
`$values/cluster/helm/nfs/books-media/values.yaml`. Remove `filebrowser` from the examples list and
change "these seven" to "these six".

- [ ] **Step 2: Update `AGENTS.md` §3.D and §5**

Add `filebrowser` to the pattern-D example list. Add to the Secret table:

```markdown
| filebrowser | `filebrowser` | `FB_CONFIG`, `FB_DATABASE`, `FB_USERNAME`, `FB_PASSWORD` (bcrypt) — first admin |
```

- [ ] **Step 3: Add the chart to the dependency-build list in `AGENTS.md` §6**

Add `cluster/helm/filebrowser` to the list of charts that need `helm dependency build`
(`monitoring/prometheus`, …, `postiz`).

- [ ] **Step 4: Replace the FileBrowser section in `docs/runbooks/authentik-apps.md`**

Replace "## FileBrowser (forward auth)" through the line ending
"Upstream archived FileBrowser on 2026-09-01; never expose it except through this middleware chain."
with:

````markdown
## FileBrowser (local login, authentik deferred)

FileBrowser is **not** behind authentik for now. It runs in namespace `filebrowser` with its own
accounts; the forward-auth chain, the embedded-outpost attachment and `filebrowser-editors` are
deferred. The procedure to wire it later stays in
`docs/superpowers/plans/2026-11-14-sso-access-model.md`.

The first admin is created from the `filebrowser` Secret when the database is empty. Rotate it with:

```bash
kubectl -n filebrowser exec deploy/filebrowser -- filebrowser users update admin --password '<new>'
```

Non-admin accounts keep the same shape as before, now in the new namespace:

```bash
kubectl -n filebrowser exec deploy/filebrowser -- filebrowser users add \
  '<username>' "$(openssl rand -hex 24)" --scope '<scope-path>' \
  --lockPassword --perm.execute=false
```
````

Also adjust the capability-group sentence above it: `filebrowser-editors` exists but is unused
until FileBrowser is wired.

- [ ] **Step 5: Update the chart README and the audit**

- `docs/charts/README.md`: change the row path `media-stack/filebrowser` to `filebrowser`.
- `docs/audit/argocd-helm.md` §3: remove `filebrowser` from the pattern-B row; add a pattern-D row
  `| filebrowser | D | selfhosted-helmcharts 0.1.2 | wrapper, local login, own namespace |`.

- [ ] **Step 6: Verify no stale references**

```bash
! grep -rn 'media-stack/filebrowser' --exclude-dir=.git --exclude-dir=superpowers .
! grep -rn 'media-stack exec deploy/filebrowser' docs AGENTS.md --exclude-dir=superpowers
grep -n 'filebrowser' docs/charts/README.md
grep -n 'filebrowser' docs/audit/argocd-helm.md
```

Expected: the two negative greps print nothing; the README row says `filebrowser`; the audit row
says pattern D.

- [ ] **Step 7: Commit**

```bash
git add AGENTS.md docs/runbooks/authentik-apps.md docs/charts/README.md docs/audit/argocd-helm.md
git commit -m "📝 docs: record FileBrowser's own namespace and local login"
```

---

### Task 7: Full validation, rebase, push, pull request

**Files:** none.

**Interfaces:**
- Consumes: every earlier task.
- Produces: a branch ready for review; no cluster change.

- [ ] **Step 1: Run the full chart render check from `AGENTS.md` §6**

```bash
cd /home/vsynthese/.local/share/opencode/worktree/117be0/misty-otter
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```

Expected: no `FAIL cluster/helm/filebrowser`; known `SKIP`s are allowed. `cluster/helm/filebrowser`
has `charts/` from Task 2, so it renders.

- [ ] **Step 2: Re-run the targeted render and the review-focus assertions**

```bash
cd cluster/helm/filebrowser
helm template test . -f values.yaml > /tmp/opencode/filebrowser-render.yaml
grep -c 'authentik' /tmp/opencode/filebrowser-render.yaml        # 0
! grep -q '^kind: Ingress$' /tmp/opencode/filebrowser-render.yaml
grep -c 'volumeName: pv-filebrowser-' /tmp/opencode/filebrowser-render.yaml # 3
```

- [ ] **Step 3: Rebase on `develop` and re-render**

```bash
cd /home/vsynthese/.local/share/opencode/worktree/117be0/misty-otter
git fetch origin
git rebase origin/develop
cd cluster/helm/filebrowser && helm dependency build . \
  && helm template test . -f values.yaml > /dev/null
```

Expected: rebase clean, render clean.

- [ ] **Step 4: Push the branch**

```bash
git push origin feature/filebrowser-namespace
```

- [ ] **Step 5: Open the pull request**

Use `github_create_pull_request` with base `develop`, head `feature/filebrowser-namespace`:

- Title: `🚀 feat(filebrowser): move FileBrowser to its own namespace`
- Body: the goal, the chart/storage/route summary, the clean-rebuild decision, the local-login
  decision, and a line saying the operator cutover in Task 8 follows the merge.

Expected: a PR URL.

---

### Task 8: Operator cutover

**This task is run by the operator with a real kubeconfig. The implementer has no cluster write
access; the agent watches the pasted outputs.** Do not run these against the read-only MCP.

**Files:** none in the repo; cluster state only.

**Interfaces:**
- Consumes: the merged Application and chart, plus the Secret below.
- Produces: FileBrowser 1/1 in namespace `filebrowser`, five bound claims, a working login.

- [ ] **Step 1: Create the namespace and login Secret before the first sync**

```bash
kubectl create namespace filebrowser --dry-run=client -o yaml | kubectl apply -f -

HASH=$(kubectl -n filebrowser run fb-hash --rm -i --restart=Never --quiet \
  --image=filebrowser/filebrowser:v2.63.23 -- hash '<the admin password>' 2>/dev/null)

kubectl -n filebrowser create secret generic filebrowser \
  --from-literal=FB_CONFIG=/config/settings.json \
  --from-literal=FB_DATABASE=/database/filebrowser.db \
  --from-literal=FB_USERNAME=admin \
  --from-literal=FB_PASSWORD="$HASH"
```

Expected: namespace `filebrowser` and secret `filebrowser` with four keys. Store the plain password
in the password manager.

- [ ] **Step 2: Stop and remove the old release**

```bash
if helm status filebrowser -n media-stack >/dev/null 2>&1; then
  helm uninstall filebrowser -n media-stack
fi
kubectl -n media-stack delete deployment,service,ingress,configmap,serviceaccount \
  -l app.kubernetes.io/instance=filebrowser --ignore-not-found
```

Expected: the old pod is gone and the host is free. The abandoned Longhorn config/db are lost here,
by decision.

- [ ] **Step 3: Sync the root and the new Application**

```bash
argocd app sync argocd-root
argocd app sync filebrowser
```

(Or sync both from the ArgoCD UI.)

Expected: the root prunes the old Application and creates the new one; the new app syncs without
error.

- [ ] **Step 4: Verify the cluster**

```bash
kubectl -n media-stack get deploy,svc,ingress,configmap,serviceaccount \
  -l app.kubernetes.io/instance=filebrowser
kubectl -n filebrowser get ingress
kubectl -n filebrowser get pods
kubectl -n filebrowser get pvc
kubectl -n filebrowser get pv | grep pv-filebrowser
kubectl -n filebrowser get ingressroute filebrowser -o yaml
kubectl -n filebrowser exec deploy/filebrowser -- ls /srv/various /srv/audio /srv/torrent
curl -sI https://filebrowser.kub.home.ashudev.com/ | head -1
```

Expected:
- the first two commands report no resources;
- pod 1/1 with 0 restarts;
- all five PVCs `Bound`, the two config claims on `local-path`;
- three `pv-filebrowser-*` PVs bound to `filebrowser`;
- the IngressRoute's middleware list is only `kube-system-security@kubernetescrd`;
- all three media directories list the NAS content;
- `HTTP/2 200` with the FileBrowser login page (a `302` means an authentik middleware is still
  present — stop and remove it).

- [ ] **Step 5: Sign in and add any non-admin accounts**

Sign in with the Secret's admin account. Then:

```bash
PASS=$(openssl rand -hex 24)
printf 'password for <username>: %s\n' "$PASS"
kubectl -n filebrowser exec deploy/filebrowser -- filebrowser users add \
  '<username>' "$PASS" --scope '<scope-path>' \
  --lockPassword --perm.execute=false
```

Store that password before moving on. Do **not** run
`filebrowser config set --auth.method=proxy`.

- [ ] **Step 6: Hand the outputs back**

Paste the verification output to the agent. Any failed check stops the cutover; do not delete the
old PVCs or namespace.

---

### Task 9: Refresh the status snapshot (follow-up PR)

**Files:**
- Modify: `docs/status.md`

**Interfaces:**
- Consumes: Task 8's verified output.
- Produces: a status page that no longer calls FileBrowser borrowed or OutOfSync.

- [ ] **Step 1: Update the tables with the observed state**

- Move `filebrowser` from "Working, not yet rebuilt (24)" to "Repaired to the contract (24)"; the
  counts become 25 and 23.
- Remove `filebrowser` from "Running on a borrowed volume (6)"; the count becomes 5 and the prose
  lists five apps.
- Remove `filebrowser` from "Drift, not breakage (10)"; the count becomes 9.
- Replace the note "Forward auth through authentik on `v2.63.23`" with "Local login on `v2.63.23`;
  own namespace; authentik deferred".
- Set the snapshot date to the cutover date.

- [ ] **Step 2: Verify the counts add up**

Run: `grep -n 'Repaired and working\|Working, not yet rebuilt\|borrowed volume\|Drift, not breakage' docs/status.md`
Expected: the summary counts match the section counts.

- [ ] **Step 3: Commit and open a follow-up PR**

```bash
git switch -c docs/filebrowser-status
git add docs/status.md
git commit -m "📝 docs: refresh the status page after the FileBrowser move"
git push origin docs/filebrowser-status
```

Open the PR against `develop` with the cutover evidence in the body.

---

## Self-review notes

- Spec coverage: namespace, wrapper chart, NFS PV/PVCs, local-path config/database, Secret bootstrap,
  Traefik CRD only, removal of the old values, docs and cutover are each assigned above.
- The authentik deferral is recorded in the design, the runbook and the status task; no task wires
  the outpost.
- The chart's `helm template` assertions are the test cycle because there is no unit-test harness in
  this repo; every value the spec pins has a grep in Tasks 2–4.
