# Maintainerr chart refactor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Maintainerr Helm chart on the repo's contract so the app starts on a claim the chart creates, is reachable on the LAN only, and reports its health honestly.

**Architecture:** One self-contained chart in `cluster/helm/media-stack/maintainerr` shaped like `tautulli`/`autobrr`: unprefixed `app.*` helpers, named values for routing, storage, env, probes and resources, five objects (Deployment, Service, ServiceAccount, PVC, IngressRoute), and a claim the chart owns. The image runs as its own non-root UID 1000 and writes only `/opt/data`.

**Tech Stack:** Helm 4 (`apiVersion: v2` chart), Traefik v2 CRDs (`traefik.containo.us/v1alpha1`), Kubernetes Deployment/Service/ServiceAccount/PVC, image `ghcr.io/maintainerr/maintainerr:3.32.0`.

**Spec:** `docs/superpowers/specs/2026-11-13-maintainerr-chart-refactor-design.md`

## Global Constraints

- Chart contract: `docs/charts/README.md`. `appVersion` must equal `image.tag`; every object name comes
  from `app.*` helpers; no tabs anywhere; `helm lint` and `helm template` clean.
- Ingress is a Traefik `IngressRoute`; the API group is a value. Never `networking.k8s.io/v1`.
- Local-only: `entryPoints: [web]`, host `maintainerr.kub.local`, `tls.enabled: false`, no middlewares.
- Image: `ghcr.io/maintainerr/maintainerr:3.32.0`. Tag has no `v` prefix; `appVersion: "3.32.0"`.
- `strategy: Recreate`, claim `<fullname>-config` = `maintainerr-config`, `local-path`, **256Mi**.
- `nodeSelector: kubernetes.io/hostname: streaming`.
- Container security: `runAsNonRoot: true`, `runAsUser: 1000`, `runAsGroup: 1000`,
  `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`; pod `seccompProfile: RuntimeDefault`.
  No `fsGroup`, no capability adds, no root init: the image ships `USER node` (1000).
- Probes: startup and liveness on `/api/health/live`; readiness on `/api/health/ready`.
- `env`: `TZ: Europe/Paris`, `LOG_LEVEL: warn`, `TELEMETRY: off`.
- No metrics, no scrape annotations: no Maintainerr exporter exists.
- Bumps are manual: `image.tag`, `appVersion` and the chart `version` move in one commit.

## Review Focus

- **An unauthenticated app reachable from outside the LAN.** Maintainerr has no login and
  `/api/settings/database/download` hands out every stored credential. Any render that puts it on
  `websecure`, gives it a public host, or attaches a cert resolver is a security regression. Task 1
  checks the rendered `IngressRoute` is `web`-only at `maintainerr.kub.local`.
- **A process UID that cannot write `/opt/data`.** The image exits at start with `Failed to stage the
  UI` if the data directory is not writable. Task 1 checks the rendered `runAsUser` is 1000 and the
  mount is `/opt/data`; the claim-mode half is verified on the cluster after the sync.
- **A probe that answers 200 while the database is broken.** `/` serves the UI with 200 regardless,
  and `/api/health/live` deliberately never touches the database. A readiness probe on either would
  hide exactly the failure it exists to catch. Task 1 greps both probe paths.
- **A claim that is named but never created.** The reason the app has not run for 76 days. Task 1
  checks the PVC renders with the derived name and that the Deployment references the same helper.
- **A version that disagrees with itself.** `appVersion: "3.32.0"` and `image.tag: "3.32.0"`; a
  mismatch makes Helm report a version nobody is running. Task 1 greps both.

---

### Task 1: Rebuild the chart

**Files:**
- Rewrite: `cluster/helm/media-stack/maintainerr/Chart.yaml`
- Rewrite: `cluster/helm/media-stack/maintainerr/values.yaml`
- Rewrite: `cluster/helm/media-stack/maintainerr/templates/_helpers.tpl`
- Rewrite: `cluster/helm/media-stack/maintainerr/templates/deployment.yaml`
- Rewrite: `cluster/helm/media-stack/maintainerr/templates/service.yaml`
- Create: `cluster/helm/media-stack/maintainerr/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/maintainerr/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/maintainerr/templates/ingressroute.yaml`
- Create: `cluster/helm/media-stack/maintainerr/templates/NOTES.txt`
- Delete: `cluster/helm/media-stack/maintainerr/templates/ingress.yaml`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the values schema below; helpers `app.name`, `app.fullname`, `app.labels`,
  `app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName`, `app.containerPort`; runtime
  names `maintainerr` (Deployment/Service/SA/IngressRoute) and `maintainerr-config` (claim).

- [ ] **Step 1: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: maintainerr
description: Maintainerr builds collections from rules and removes the matching media once a collection's rule says to.
type: application
# Chart version. Bumps are manual: image.tag, appVersion and this version move together.
version: 1.0.0
# Must match image.tag in values.yaml.
appVersion: "3.32.0"
icon: https://raw.githubusercontent.com/Maintainerr/Maintainerr/v3.32.0/apps/ui/public/logo.png
home: https://maintainerr.info/
sources:
  - https://github.com/Maintainerr/Maintainerr
keywords:
  - plex
  - jellyfin
  - cleanup
  - media
```

- [ ] **Step 2: Copy `_helpers.tpl` from the reference chart**

```bash
cp cluster/helm/media-stack/tautulli/templates/_helpers.tpl \
   cluster/helm/media-stack/maintainerr/templates/_helpers.tpl
```

Copy verbatim. It defines `app.name`, `app.fullname`, `app.chart`, `app.labels`,
`app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName`, `app.containerPort`.

- [ ] **Step 3: Write `values.yaml`**

Use these keys and defaults exactly. Add a doc comment above each block explaining the non-obvious
default: the 256Mi size, the UID-1000 choice, the two health endpoints, `LOG_LEVEL`, `TELEMETRY`, and
the absent media mount. Every comment in this file is operator-facing prose.

```yaml
nameOverride: ""
fullnameOverride: ""
replicaCount: 1
strategy: Recreate

image:
  repository: ghcr.io/maintainerr/maintainerr
  tag: "3.32.0"
  pullPolicy: IfNotPresent

imagePullSecrets: []

serviceAccount:
  create: true
  automount: false
  name: ""
  annotations: {}

podAnnotations: {}
podLabels: {}

podSecurityContext:
  seccompProfile:
    type: RuntimeDefault

securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL

service:
  type: ClusterIP
  port: 6246

container:
  port: null

ingressRoute:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  entryPoints:
    - web
  host: maintainerr.kub.local
  pathPrefix: /
  middlewares: []
  labels: {}
  annotations: {}
  tls:
    enabled: false
    secretName: ""
    certResolver: ""
    options: ""

storage:
  enabled: true
  existingClaim: ""
  className: local-path
  accessModes:
    - ReadWriteOnce
  size: 256Mi
  mountPath: /opt/data

env:
  TZ: "Europe/Paris"
  LOG_LEVEL: "warn"
  TELEMETRY: "off"

extraEnv: []
envFrom: []
volumes: []
volumeMounts: []

nodeSelector:
  kubernetes.io/hostname: streaming

tolerations: []
affinity: {}

resources:
  requests:
    cpu: 25m
    memory: 256Mi
  limits:
    memory: 1Gi

probes:
  startup:
    enabled: true
    path: /api/health/live
    periodSeconds: 5
    timeoutSeconds: 5
    failureThreshold: 36
  liveness:
    enabled: true
    path: /api/health/live
    initialDelaySeconds: 20
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    enabled: true
    path: /api/health/ready
    initialDelaySeconds: 5
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 3
```

- [ ] **Step 4: Copy the four generic templates from `tautulli`**

```bash
cd cluster/helm/media-stack
cp tautulli/templates/deployment.yaml    maintainerr/templates/deployment.yaml
cp tautulli/templates/service.yaml       maintainerr/templates/service.yaml
cp tautulli/templates/serviceaccount.yaml maintainerr/templates/serviceaccount.yaml
cp tautulli/templates/persistentvolumeclaim.yaml maintainerr/templates/persistentvolumeclaim.yaml
cp tautulli/templates/ingressroute.yaml  maintainerr/templates/ingressroute.yaml
```

Copy verbatim. They are values-driven and carry no app name; the reference chart is built that way on
purpose. No edits needed.

- [ ] **Step 5: Write `templates/NOTES.txt`**

Four blocks, in this order: fullname and namespace; the URL `http://maintainerr.kub.local/` with a
note that `kub.local` resolves only inside the LAN and the router is plain HTTP by design; the claim
name from `app.configClaimName` with a note that deleting the release deletes it; and one line saying
the app has no login, so anyone on the LAN can read its settings.

- [ ] **Step 6: Delete the old ingress template**

```bash
git rm cluster/helm/media-stack/maintainerr/templates/ingress.yaml
```

- [ ] **Step 7: Verify the render**

Run:
```bash
helm lint cluster/helm/media-stack/maintainerr --namespace media-stack
helm template maintainerr cluster/helm/media-stack/maintainerr --namespace media-stack >/tmp/maintainerr.yaml
grep -c '^kind:' /tmp/maintainerr.yaml
```
Expected: `1 chart(s) linted, 0 chart(s) failed`; exit 0; `kind:` count **5** (Deployment, Service,
ServiceAccount, PVC, IngressRoute).

- [ ] **Step 8: Verify the contract greps**

```bash
cd cluster/helm/media-stack/maintainerr
grep -rnP '\t' templates/ || echo "no tabs"
grep -rn 'latest' values.yaml || echo "no floating tags"
grep -rn 'node-type\|networking.k8s.io\|maintainerr-config-volume\|ingress\.hosts\|autoscaling' \
  templates/ values.yaml || echo "no leftovers"
grep -rn 'prometheus.io' templates/ || echo "no metrics"
grep -n 'image.tag\|appVersion' Chart.yaml values.yaml
```
Expected: `no tabs`, `no floating tags`, `no leftovers`, `no metrics`; `appVersion: "3.32.0"` and
`tag: "3.32.0"`.

- [ ] **Step 9: Verify the five focus behaviours in the rendered output**

```bash
grep -A4 'kind: IngressRoute' /tmp/maintainerr.yaml | head
grep -n 'kub.local\|entryPoints\|websecure\|tls:' /tmp/maintainerr.yaml
grep -n 'claimName\|mountPath: /opt/data\|kind: PersistentVolumeClaim' /tmp/maintainerr.yaml
grep -n 'path: /api/health/live\|path: /api/health/ready' /tmp/maintainerr.yaml
grep -n -A6 'securityContext' /tmp/maintainerr.yaml | head -20
grep -n 'containerPort\|port: 6246' /tmp/maintainerr.yaml
```
Expected: one IngressRoute on `web` at `maintainerr.kub.local`, no `websecure`, no `tls:` block;
`kind: PersistentVolumeClaim` present with `claimName: maintainerr-config` and
`mountPath: /opt/data`; two `/api/health/live` probes and one `/api/health/ready`; `runAsUser: 1000`,
`runAsNonRoot: true`, no `fsGroup`; `containerPort: 6246` and the Service port `6246`.

- [ ] **Step 10: Commit**

```bash
git add cluster/helm/media-stack/maintainerr
git commit -m "🔧 chore(maintainerr): rebuild the chart on the reference shape, local-only"
```

### Task 2: Chart README

**Files:**
- Create: `cluster/helm/media-stack/maintainerr/README.md`

**Interfaces:**
- Consumes: the values schema from Task 1.
- Produces: documentation only.

- [ ] **Step 1: Write the README** with the sections the other charts use: upstream links (docs,
  API, security page); how it is deployed here (`helm template` command); a values table; the image
  choice and the manual bump rule (`image.tag`, `appVersion`, `version` in one commit); the claim and
  the UID-1000-on-a-`0777`-claim story, including that this chart is the first to break if
  `permPattern` is set; the probes table (`live` vs `ready`, and why readiness is real here); the
  `LOG_LEVEL` and `TELEMETRY` values; the no-authentication model and the database-download caveat;
  the optional media mount for leftover-folder cleanup, as a values snippet that is **not** enabled;
  and uninstall behaviour.

- [ ] **Step 2: Check the claims against the chart**

```bash
cd cluster/helm/media-stack/maintainerr
grep -n 'maintainerr.kub.local\|3.32.0\|maintainerr-config\|/api/health/ready' README.md
grep -n 'image.tag' README.md
```
Expected: the README's host, tag and claim name match `values.yaml`, and the bump rule is stated.

- [ ] **Step 3: Commit**

```bash
git add cluster/helm/media-stack/maintainerr/README.md
git commit -m "📝 docs(maintainerr): document the chart, its probes and its missing login"
```

### Task 3: Record Maintainerr in the chart contract

**Files:**
- Modify: `docs/charts/README.md` (the probes/hardening table, and "What good looks like")

**Interfaces:**
- Consumes: the chart and README from Tasks 1–2.
- Produces: documentation only.

- [ ] **Step 1: Add the table row** to the probes/hardening table:

```
| `maintainerr` | yes, `/api/health/live` and `/api/health/ready` | capabilities dropped, seccomp, no privilege escalation | **yes — 1000**, the image's own UID; the claim is `0777` and owned by 1100 |
```

- [ ] **Step 2: Add the "what good looks like" paragraph**, naming Maintainerr's two deliberate
  differences: readiness is a real dependency check (`SELECT 1`, `503` when the SQLite file is
  broken), and the app has **no authentication at all**, so the LAN boundary is the only access
  control and the route stays on `web`.

- [ ] **Step 3: Commit**

```bash
git add docs/charts/README.md
git commit -m "📝 docs(charts): record maintainerr in the chart contract"
```

---

## Post-merge verification

Auto-sync is off, so the Application is synced by the operator. Then:

- Pod `Running` and `Ready`; `maintainerr-config` `Bound` on `streaming`.
- `curl -s http://maintainerr.kub.local/api/health/live` → `{"status":"ok",...}`.
- `curl -s http://maintainerr.kub.local/api/health/ready` → `{"status":"ok","database":"ok",...}`.
- `curl -s http://maintainerr.kub.local/api/app/status` → version `3.32.0`.
- The app staged its UI: `/opt/data/ui` exists on the node and is writable by UID 1000.
- After a week, replace the unmeasured resources from Prometheus.
