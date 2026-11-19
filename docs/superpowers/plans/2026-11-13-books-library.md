# Books library (CWA + Kavita) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Calibre desktop container with Calibre-Web Automated (CWA) as the single library UI, and rebuild the Kavita chart so the reader runs again on 0.9.1, both over the existing NFS library.

**Architecture:** Two hand-maintained Helm charts in `cluster/helm/media-stack/`, each one ArgoCD Application, no new infrastructure. CWA writes the Calibre library on the NFS share; Kavita mounts the same share read-only and owns reading progress. The Calibre Application and values are retired in a final commit, after the cutover is verified on the cluster.

**Tech Stack:** Helm 4.2.4 (no kubeconform installed; `yq` is), Traefik 2.9.4 CRDs (`traefik.containo.us/v1alpha1`), ArgoCD with manual sync, LinuxServer-style images (`PUID`/`PGID` 1100).

**Spec:** `docs/superpowers/specs/2026-11-13-books-library-design.md`

## Global Constraints

- Helm is **4.2.4**; `kubeconform` is **not** installed. Verification is `helm lint`, `helm template`, `yq`, `grep`.
- Images, pinned exactly: `ghcr.io/crocodilestick/calibre-web-automated:v4.0.8` and `ghcr.io/linuxserver/kavita:v0.9.1.4-ls132`. Never `latest`.
- `Chart.yaml` `appVersion` equals `image.tag` in both charts.
- Pods pin `kubernetes.io/hostname: streaming`. No `node-type` label anywhere.
- `TZ: Europe/Paris`, `PUID: "1100"`, `PGID: "1100"` on both apps.
- Ingress/route API group comes from values as `traefik.containo.us/v1alpha1`.
- Traefik is **2.9.4**: the allow-list middleware field is `ipWhiteList.sourceRange`. `ipAllowList` does not exist on this cluster.
- Claims: `calibre-web-automated-config` 10Gi, `calibre-web-automated-ingest` 1Gi, `kavita-config` 2Gi — all `local-path`, names derived from the release, `strategy: Recreate`.
- Both apps mount the existing claim `pvc-books-media-nfs-subdir-external-provisioner`: CWA read-write at `/calibre-library`, Kavita **readOnly** at `/books`.
- Every container: `resources.requests.memory` and `resources.limits.memory` set, no CPU limit.
- Plain configuration goes inline in `env`, never in a ConfigMap. No secret values in `values.yaml`.
- Helpers are prefixed `app.` (`app.fullname`, `app.labels`, `app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName`). No tabs in any template.
- Files under `cluster/argocd/**` are **CRLF**. Everything else in this plan is **LF**.
- No Application file gains `syncPolicy.automated`.
- Chart README is mandatory for both charts, and every deliberate exception (disabled readiness, GiB-sized config claim, root-then-`abc` init) is recorded there.

## Review Focus

- **The CWA route is reachable from outside the LAN.** DNS suggests otherwise; Traefik routes on `Host`. Expected: a request to the public address with `Host: calibre.kub.local` is rejected by the proxy. Pinned in Task 2.
- **A pod is pinned to a node where its fresh `local-path` claim cannot bind.** Expected: claims bind on `streaming`, the only node the pod can run on. Pinned in Tasks 1 and 4 (assertions) and Task 6 (pre-sync pod deletion).
- **Ingest silently does nothing because the app is in local-disk mode.** Expected: `NETWORK_SHARE_MODE=true` on CWA. Pinned in Task 1.
- **A port disagrees between Service and container.** Expected: CWA 8083/8083, Kavita 5000/5000. Pinned in Tasks 1 and 4.
- **The admin UI is published with `admin`/`admin123` behind nothing.** Expected: `web` entry point only, allow list attached, and a README step to change the password. Pinned in Task 2 and Task 1's README.

---

### Task 1: CWA chart — claims, service, deployment, README

**Files:**
- Create: `cluster/helm/media-stack/calibre-web-automated/Chart.yaml`
- Create: `cluster/helm/media-stack/calibre-web-automated/.helmignore`
- Create: `cluster/helm/media-stack/calibre-web-automated/values.yaml`
- Create: `cluster/helm/media-stack/calibre-web-automated/templates/_helpers.tpl`
- Create: `cluster/helm/media-stack/calibre-web-automated/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/calibre-web-automated/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/calibre-web-automated/templates/service.yaml`
- Create: `cluster/helm/media-stack/calibre-web-automated/templates/deployment.yaml`
- Create: `cluster/helm/media-stack/calibre-web-automated/README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: chart named `calibre-web-automated`; values keys `image.{repository,tag,pullPolicy}`, `strategy`, `service.{type,port}`, `container.port`, `storage.{config,ingest,library}`, `env`, `probes.{startup,liveness,readiness}`, `resources`, `nodeSelector`, `serviceAccount.{create,automount}`, `podSecurityContext`, `securityContext`, `podAnnotations`. Task 2 adds `ingressRoute.*`; Task 3 points an Application at this directory.

- [ ] **Step 1: Scaffold the chart metadata**

`Chart.yaml`: `apiVersion: v2`, `name: calibre-web-automated`, `description: Calibre-Web Automated`, `type: application`, `version: 0.1.0`, `appVersion: "v4.0.8"`. `.helmignore`: copy from `cluster/helm/media-stack/sonarr/.helmignore`.

- [ ] **Step 2: Copy the helpers unchanged**

Copy `cluster/helm/media-stack/sonarr/templates/_helpers.tpl` verbatim (`app.*` helpers, `app.configClaimName`). This chart has **two** claims, so the two claim names are built in `persistentvolumeclaim.yaml` and `deployment.yaml` with `printf "%s-config" (include "app.fullname" .)` and `printf "%s-ingest" (include "app.fullname" .)` rather than through `app.configClaimName`; leave that define in place, unused, so the helper file stays identical to the reference.

- [ ] **Step 3: Write `values.yaml`**

```yaml
nameOverride: ""
fullnameOverride: ""
replicaCount: 1
strategy: Recreate

image:
  repository: ghcr.io/crocodilestick/calibre-web-automated
  tag: "v4.0.8"
  pullPolicy: IfNotPresent

imagePullSecrets: []

serviceAccount:
  create: true
  automount: false
  name: ""
  annotations: {}

podAnnotations: {}

podSecurityContext:
  seccompProfile:
    type: RuntimeDefault

securityContext:
  allowPrivilegeEscalation: false
  privileged: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: [ALL]
    add: [CHOWN, SETUID, SETGID]

service:
  type: ClusterIP
  port: 8083

container:
  port: null

storage:
  config:
    enabled: true
    existingClaim: ""
    className: local-path
    accessModes: [ReadWriteOnce]
    size: 10Gi
    mountPath: /config
  ingest:
    enabled: true
    existingClaim: ""
    className: local-path
    accessModes: [ReadWriteOnce]
    size: 1Gi
    mountPath: /cwa-book-ingest
  library:
    claim: pvc-books-media-nfs-subdir-external-provisioner
    mountPath: /calibre-library
    readOnly: false

env:
  TZ: "Europe/Paris"
  PUID: "1100"
  PGID: "1100"
  NETWORK_SHARE_MODE: "true"

extraEnv: []
envFrom: []

probes:
  startup:
    enabled: true
    path: /
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 60
  liveness:
    enabled: true
    path: /
    initialDelaySeconds: 30
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    enabled: false
    path: /
    initialDelaySeconds: 0
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 3

resources:
  requests:
    cpu: 25m
    memory: 512Mi
  limits:
    memory: 2Gi

nodeSelector:
  kubernetes.io/hostname: streaming

tolerations: []
affinity: {}
```

Comment every block the way `sonarr/values.yaml` does: why `Recreate`, why the config claim is 10Gi (processed-originals backups), why the library is not `local-path`, why the security context keeps three capabilities.

- [ ] **Step 4: Write the object templates**

- `persistentvolumeclaim.yaml`: one claim per `storage.config` and `storage.ingest`, both `<app.fullname>-config` / `<app.fullname>-ingest` unless `existingClaim` is set, `app.labels` on both.
- `serviceaccount.yaml`: `app.serviceAccountName`, `automountServiceAccountToken: {{ .Values.serviceAccount.automount }}`.
- `service.yaml`: `ClusterIP`, port `{{ .Values.service.port }}`, targetPort `{{ .Values.container.port | default .Values.service.port }}`, name `http`.
- `deployment.yaml`: `Recreate`; container port from `container.port | default service.port`; volumes from the three storage blocks (`library.claim` mounted with `readOnly: {{ .Values.storage.library.readOnly }}`), plus two `emptyDir` volumes mounted at `/run` and `/tmp` so the read-only root filesystem can work; `env` from `toYaml .Values.env`; `extraEnv`/`envFrom` appended; three probes rendered from values with `enabled` gates; arguments put a `GET` on the app root so the redirect to `/login` is followed — use `httpGet` on the path in values and accept the 302 (do **not** set `httpHeaders`).

The read-only root filesystem is an attempt, not a certainty. Task 6 checks at first start that the container comes up and one ingest completes; if the image writes somewhere else and fails, remove `readOnlyRootFilesystem` and the two `emptyDir`s in a follow-up commit and record why in the README. A setting that breaks the app is worse than the absence of it.

- [ ] **Step 5: Write the README**

Sections: upstream links; what this app is and what it is not (library manager; reading is Kavita); the ingest workflow (UI upload or `/cwa-book-ingest`); where the backups of originals live (`/config/processed_books`); `NETWORK_SHARE_MODE` and the upstream "NFS unsupported" caveat; the default `admin`/`admin123` credentials and that they must be changed on first login; why readiness is disabled; the CWA log-level debt.

- [ ] **Step 6: Verify the render**

```bash
helm lint cluster/helm/media-stack/calibre-web-automated -n media-stack
helm template calibre-web-automated cluster/helm/media-stack/calibre-web-automated -n media-stack >/tmp/cwa.yaml
yq 'select(.kind=="Deployment") | .spec.template.spec.containers[0] | {"env": .env, "ports": .ports, "probes": [.startupProbe, .livenessProbe, .readinessProbe], "resources": .resources}' /tmp/cwa.yaml
yq 'select(.kind=="PersistentVolumeClaim") | [.metadata.name, .spec.storageClassName, .spec.resources.requests.storage]' /tmp/cwa.yaml
yq 'select(.kind=="Service") | [.spec.ports[0].port, .spec.ports[0].targetPort]' /tmp/cwa.yaml
```

Expected: lint clean; `env` contains `NETWORK_SHARE_MODE: "true"` and `PUID: "1100"`; container port `8083`; no `readinessProbe`; two claims named `calibre-web-automated-config` (10Gi) and `calibre-web-automated-ingest` (1Gi), both `local-path`; service `8083` → `8083`; the deployment mounts the library claim at `/calibre-library`.

- [ ] **Step 7: Verify the absences**

```bash
grep -rnP '\t' cluster/helm/media-stack/calibre-web-automated/templates && echo "TABS" || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/calibre-web-automated/values.yaml || echo "no floating tags"
grep -rni 'kind: ConfigMap' cluster/helm/media-stack/calibre-web-automated/templates || echo "no configmap"
grep -rn 'prometheus.io/scrape' cluster/helm/media-stack/calibre-web-automated/templates || echo "no scrape annotation (no exporter in the image)"
grep -rn 'node-type\|32400\|kavita' cluster/helm/media-stack/calibre-web-automated || echo "no leftovers"
```

- [ ] **Step 8: Commit**

```bash
git add cluster/helm/media-stack/calibre-web-automated
git commit -m "🚀 feat(calibre-web-automated): chart for the library manager"
```

---

### Task 2: CWA chart — local-network-only route

**Files:**
- Create: `cluster/helm/media-stack/calibre-web-automated/templates/middleware-allowlist.yaml`
- Create: `cluster/helm/media-stack/calibre-web-automated/templates/ingressroute.yaml`
- Modify: `cluster/helm/media-stack/calibre-web-automated/values.yaml`
- Modify: `cluster/helm/media-stack/calibre-web-automated/templates/_helpers.tpl` (only if a name helper is missing)
- Modify: `cluster/helm/media-stack/calibre-web-automated/README.md`

**Interfaces:**
- Consumes: Task 1's values shape and `app.labels`.
- Produces: values key `ingressRoute` with `enabled`, `apiVersion`, `entryPoints`, `host`, `pathPrefix`, `sourceRanges`, `middlewares`, `tls`; the rendered `Middleware` is named `<fullname>-allowlist` and referenced by plain name from the same namespace.

- [ ] **Step 1: Confirm the allow-list field name on the running CRD**

```bash
kubectl explain middleware.spec --recursive 2>/dev/null | grep -iE 'ipWhiteList|ipAllowList'
```

Expected: `ipWhiteList` (Traefik 2.9.4). If the output shows only `ipAllowList`, use that field and note the substitution in the README; do not guess.

- [ ] **Step 2: Add the `ingressRoute` block to `values.yaml`**

```yaml
ingressRoute:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  entryPoints:
    - web
  host: calibre.kub.local
  pathPrefix: /
  sourceRanges:
    - 192.168.1.0/24
  middlewares: []
  labels: {}
  annotations: {}
  tls:
    enabled: false
    secretName: ""
    certResolver: ""
    options: ""
```

- [ ] **Step 3: Write the two templates**

`middleware-allowlist.yaml`: a `Middleware` of `.Values.ingressRoute.apiVersion`, named `<fullname>-allowlist`, `ipWhiteList.sourceRange: toYaml .Values.ingressRoute.sourceRanges`. Render nothing when `sourceRanges` is empty.
`ingressroute.yaml`: `IngressRoute` on `entryPoints`, one route matching ``Host(`<host>`) && PathPrefix(`<pathPrefix>`)``, service `<fullname>` on the Service port, `middlewares` from values **plus** the allow-list middleware when `sourceRanges` is non-empty, `tls` block only when `tls.enabled`.

- [ ] **Step 4: Verify the render**

```bash
helm template calibre-web-automated cluster/helm/media-stack/calibre-web-automated -n media-stack >/tmp/cwa.yaml
yq 'select(.kind=="IngressRoute") | [.spec.entryPoints, .spec.routes[0].match, .spec.routes[0].middlewares, .spec.tls]' /tmp/cwa.yaml
yq 'select(.kind=="Middleware") | [.metadata.name, .spec]' /tmp/cwa.yaml
```

Expected: entry point `["web"]`; match `Host(`+"`calibre.kub.local`"+`) && PathPrefix(`+"`/`"+`)`; middlewares list contains `calibre-web-automated-allowlist`; no `tls` key; the Middleware carries `ipWhiteList.sourceRange: ["192.168.1.0/24"]`.

- [ ] **Step 5: Verify nothing public is rendered**

```bash
grep -n 'websecure\|certResolver\|kub.home.ashudev.com' /tmp/cwa.yaml || echo "lan only, no TLS"
```

- [ ] **Step 6: Document the lockout fallback**

In the README, add: the route is restricted to `192.168.1.0/24` at the proxy, DNS alone is not access control, add the VPN range to `ingressRoute.sourceRanges` to read from outside, and `kubectl -n media-stack port-forward svc/calibre-web-automated 8083:8083` is the way back in if the list is wrong.

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/media-stack/calibre-web-automated
git commit -m "🚀 feat(calibre-web-automated): LAN-only route with a proxy allow list"
```

---

### Task 3: CWA ArgoCD Application

**Files:**
- Create: `cluster/argocd/projects/media-stack/calibre-web-automated.yaml`

**Interfaces:**
- Consumes: `cluster/helm/media-stack/calibre-web-automated` from Tasks 1–2.
- Produces: Application `calibre-web-automated` in namespace `media-stack`, target revision `develop`, no `syncPolicy.automated`.

- [ ] **Step 1: Write the Application**

Copy `cluster/argocd/projects/kavita.yaml` and change `metadata.name` and `spec.source.path` to `calibre-web-automated`. Keep `syncOptions: [CreateNamespace=true]`, `project: default`, the destination and the repo URL verbatim.

- [ ] **Step 2: Verify CRLF and shape**

```bash
file cluster/argocd/projects/media-stack/calibre-web-automated.yaml
grep -c $'\r' cluster/argocd/projects/media-stack/calibre-web-automated.yaml
yq '.spec.syncPolicy, .spec.source.path' cluster/argocd/projects/media-stack/calibre-web-automated.yaml
test -d cluster/helm/media-stack/calibre-web-automated && echo "path resolves"
```

Expected: CRLF present (non-zero count, matching the karma-sibling files), `syncPolicy` has only `syncOptions`, the path resolves.

- [ ] **Step 3: Commit**

```bash
git add cluster/argocd/projects/media-stack/calibre-web-automated.yaml
git commit -m "🚀 feat(argocd): Application for calibre-web-automated"
```

---

### Task 4: Kavita chart rebuild

**Files:**
- Modify: `cluster/helm/media-stack/kavita/Chart.yaml`
- Modify: `cluster/helm/media-stack/kavita/values.yaml` (rewrite)
- Modify: `cluster/helm/media-stack/kavita/templates/_helpers.tpl` (replace with `app.*`)
- Modify: `cluster/helm/media-stack/kavita/templates/deployment.yaml`
- Modify: `cluster/helm/media-stack/kavita/templates/service.yaml`
- Create: `cluster/helm/media-stack/kavita/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/kavita/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/kavita/templates/ingressroute.yaml`
- Delete: `cluster/helm/media-stack/kavita/templates/ingress-public.yaml`, `ingress-public-redirect.yaml`, `middleware-security.yaml`
- Create: `cluster/helm/media-stack/kavita/README.md`

**Interfaces:**
- Consumes: the reference shape of `cluster/helm/media-stack/sonarr` (helpers, claim, probes, service).
- Produces: values keys `image.*`, `strategy`, `service.{type,port}`, `container.port`, `ingressRoute.*` (two routes: `websecure` with TLS and `web` redirect), `storage.*`, `env`, `probes.*`, `resources`, `nodeSelector`, `serviceAccount.*`.

- [ ] **Step 1: Rebuild the helpers and metadata**

Copy `sonarr/templates/_helpers.tpl` verbatim (defines `app.*`, `app.configClaimName`). `Chart.yaml`: keep `name: kavita`, set `version: 1.0.0` and `appVersion: "v0.9.1.4-ls132"`.

- [ ] **Step 2: Rewrite `values.yaml`**

Start from `sonarr/values.yaml` and change: image `ghcr.io/linuxserver/kavita`, tag `v0.9.1.4-ls132`; `service.port: 5000`; `storage.size: 2Gi` (cover/page cache); remove `downloads`/`media`/`exportarr`; add `storage.library` with `claim: pvc-books-media-nfs-subdir-external-provisioner`, `mountPath: /books`, `readOnly: true`; env `TZ`, `PUID`, `PGID`, `DOTNET_HOSTBUILDER__RELOADCONFIGONCHANGE: "false"`, `Logging__LogLevel__Default: "Warning"`, `Logging__LogLevel__Microsoft: "Warning"`; probes `startup` and `liveness` on `/api/health`, `readiness.enabled: false`; `resources` `15m`/`256Mi` request and `1Gi` limit; `nodeSelector kubernetes.io/hostname: streaming`.

The routing block is the one place this chart diverges from the LAN-only reference, so write it out rather than adapting:

```yaml
ingressRoute:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  host: kavita.kub.home.ashudev.com
  pathPrefix: /
  redirectToHttps: true
  middlewares: []
  labels: {}
  annotations: {}
  tls:
    enabled: true
    secretName: ""
    certResolver: myresolver
    options: kavita-tlsoption
```

- [ ] **Step 3: Write the templates**

- `persistentvolumeclaim.yaml` from the reference; `serviceaccount.yaml` with `automount: false`.
- `service.yaml`: port 5000, targetPort from `container.port | default service.port`.
- `deployment.yaml`: `Recreate`; container port 5000; env inline; `/config` from the created claim, `/books` from `storage.library` with `readOnly: true`; probes from values; resources; nodeSelector.
- `ingressroute.yaml`: render **two** `IngressRoute`s, `-public` on `websecure` with `tls.certResolver`/`tls.options` from values, and `-public-redirect` on `web` whose route carries a `redirectScheme` `Middleware` named `<fullname>-redirect-https`, both on `Host(`kavita.kub.home.ashudev.com`)`. Keep the object names identical to today's so the live objects are updated, not orphaned.

- [ ] **Step 4: Verify the render**

```bash
helm lint cluster/helm/media-stack/kavita -n media-stack
helm template kavita cluster/helm/media-stack/kavita -n media-stack >/tmp/kavita.yaml
yq 'select(.kind=="Deployment") | .spec.template.spec.volumes' /tmp/kavita.yaml
yq 'select(.kind=="Deployment") | [.spec.template.spec.containers[0].ports[0].containerPort, .spec.template.spec.containers[0].env]' /tmp/kavita.yaml
yq 'select(.kind=="IngressRoute") | [.metadata.name, .spec.entryPoints, .spec.routes[0].match, .spec.tls]' /tmp/kavita.yaml
yq 'select(.kind=="PersistentVolumeClaim") | [.metadata.name, .spec.storageClassName]' /tmp/kavita.yaml
```

Expected: lint clean; volumes include the library claim with `readOnly: true` and `kavita-config`; container port 5000; env has the two `Logging__*` entries and `PUID` 1100; two IngressRoutes named `kavita-public` (websecure, certResolver `myresolver`, options `kavita-tlsoption`) and `kavita-public-redirect` (web); claim `kavita-config` on `local-path`.

- [ ] **Step 5: Verify the absences**

```bash
grep -rn 'kavita-config2\|32400\|node-type\|setGateway\|networking.k8s.io' cluster/helm/media-stack/kavita && echo "LEFTOVERS" || echo "clean"
grep -rnP '\t' cluster/helm/media-stack/kavita/templates && echo "TABS" || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/kavita/values.yaml || echo "no floating tags"
```

- [ ] **Step 6: Write the README**

Upstream links, the 0.9.1 upgrade, the config claim and why 2Gi, the read-only library mount and that CWA is the writer, probes on `/api/health` with readiness disabled and why, the log level and the fact it must be verified in the pod, progress sync + OPDS + Kover/Turnleaf as the offline path, and the rename caveat (a metadata edit that changes author/title costs that series its position).

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/media-stack/kavita
git commit -m "♻️ refactor(kavita): rebuild on the chart contract, run 0.9.1"
```

---

### Task 5: Chart documentation and AGENTS.md

**Files:**
- Modify: `docs/charts/README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the two charts from Tasks 1–4.
- Produces: nothing code-level.

- [ ] **Step 1: Add two rows to the probes/hardening table** in `docs/charts/README.md`, in the same columns as the existing rows: `calibre-web-automated` (startup+liveness on `/`, readiness disabled, capabilities dropped except the three the init needs, claim 10Gi) and `kavita` (startup+liveness on `/api/health`, readiness disabled, public TLS route).

- [ ] **Step 2: Add two paragraphs under "What good looks like"**, dated 2026-11-13, one per chart, naming the deliberate exceptions: CWA's GiB config claim with its reason, its disabled readiness, its LAN-only allow list, and its log-level debt; Kavita's rebuilt chart, its read-only library mount, and its single public hostname with no LAN route by decision.

- [ ] **Step 3: Update `AGENTS.md`**

- §1 chart count (51 → 52, and adjust if the Calibre values directory is still counted at this point).
- §3.B: the pattern-B sample `calibre` becomes `filebrowser`, and the eight-app list becomes seven.
- §4 placement: `streaming` line mentions `calibre` → `calibre-web-automated`.

- [ ] **Step 4: Verify no stale reference remains**

```bash
grep -rn 'calibre' AGENTS.md docs/charts/README.md | grep -v 'calibre-web-automated'
```

Expected: only lines that describe the removal, or none.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md docs/charts/README.md
git commit -m "📝 docs: record the CWA and Kavita charts in the contract"
```

---

### Task 6: Cutover runbook

**Files:**
- Create: `docs/runbooks/books-cutover.md`

**Interfaces:**
- Consumes: the design's migration sequence.
- Produces: the operator-facing procedure; no code.

- [ ] **Step 1: Write the runbook** with these steps, in this order, each one command block plus what "good" looks like: confirm `metadata.db` under `/books`; copy the database next to the original; best-effort tarball of `/config`; `kubectl -n media-stack delete deploy calibre`; delete the Pending Kavita pod; sync both Applications by hand; first login and CWA settings (auto-convert, EPUB fixer, auto-metadata Google Books → Internet Archive, KOReader/Kobo sync **off**); ingest one real book and confirm it lands; Kavita first-run and library scan; verification commands from the design; finalizer patch for `calibre-config`. Include the one runtime check the plan cannot do locally: the pod starts with `readOnlyRootFilesystem: true` and completes an ingest — if it does not, the runbook says to remove that setting and the two `emptyDir`s in a follow-up commit and note it in the chart README.

- [ ] **Step 2: Verify the runbook matches the design**

```bash
grep -c 'kubectl' docs/runbooks/books-cutover.md
```

Expected: every hand action in the design's sequence has a command; the order is identical.

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/books-cutover.md
git commit -m "📝 docs: runbook for the books cutover"
```

---

### Task 7: Retire the Calibre desktop

**Files:**
- Delete: `cluster/argocd/projects/media-stack/calibre.yaml`
- Delete: `cluster/helm/media-stack/calibre/values.yaml` (and the directory)
- Modify: `AGENTS.md` if any reference survived Task 5

**Interfaces:**
- Consumes: the cutover verified on the cluster (Task 6 executed by the operator).
- Produces: nothing.

- [ ] **Step 1: Confirm the new stack has run before deleting anything**

```bash
kubectl -n media-stack get deploy,pods | grep -Ei 'calibre|kavita'
```

Expected: `calibre-web-automated` and `kavita` Running, no `calibre` Deployment. If the old Deployment is still there, stop: Task 6 has not been run.

- [ ] **Step 2: Delete the two paths** with `git rm -r`.

- [ ] **Step 3: Verify no dangling reference**

```bash
grep -rn 'media-stack/calibre\b\|projects/media-stack/calibre' cluster docs AGENTS.md || echo "no references"
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "🔥 chore(calibre): remove the desktop container and its Application"
```

---

## Self-review notes

- Every design section maps to a task: image/registry → 1 and 4; storage → 1 and 4; Kavita claim → 4; single writer → 1 and 4 (readOnly mount); library registration → 1 (mount) and 6 (first start); probes → 1 and 4; security context → 1, 4, 5; logs → 4 and Task 1's README debt; resources → 1 and 4; network-share mode → 1; routing/allow list → 2; reading/progress → 4's README; renames → 4's README; migration → 6; documentation → 5; verification → spread across 1, 2, 4, 7.
- No task defines a type or function another task names differently: the values keys are fixed once in Task 1 and reused verbatim.
- The plan deliberately does not restate chart bodies; the reference chart plus the values blocks are the decision, the body is the implementer's.
