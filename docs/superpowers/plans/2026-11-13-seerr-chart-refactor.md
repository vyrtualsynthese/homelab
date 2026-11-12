# seerr chart refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `cluster/helm/media-stack/seerr` on the repo chart contract and the Plex public-route shape, preserving every live object name.

**Architecture:** A single local chart with `app.*` naming helpers, one values block per concern, and a values-driven Traefik stack (secure IngressRoute, plain-HTTP redirect, security-headers Middleware, rate-limit Middleware, hardened TLSOption). The Deployment runs as UID 1000 with capabilities dropped and probes on Seerr's own endpoints.

**Tech Stack:** Helm 4, Kubernetes manifests, Traefik 2.11 CRDs (`traefik.containo.us/v1alpha1`), ArgoCD GitOps.

**Spec:** `docs/superpowers/specs/2026-11-13-seerr-chart-refactor-design.md`

## Global Constraints

- Chart directory: `cluster/helm/media-stack/seerr`.
- Release/Application name: `seerr`; namespace: `media-stack`.
- `image.repository: ghcr.io/seerr-team/seerr`, `image.tag: v3.4.1`, `Chart.yaml.appVersion: "v3.4.1"`.
- Chart version: `1.0.0`.
- Service and container port: `5055`; Service/container port name: `http`.
- Ingress host: `seerr.kub.home.ashudev.com`; TLS resolver: `myresolver`.
- Live names preserved: Deployment/Service/ServiceAccount `seerr`; PVC `seerr-config`; IngressRoutes `seerr-public` and `seerr-public-redirect`; Middlewares `seerr-security`, `seerr-redirect-https`, `seerr-ratelimit`; TLSOption `seerr-tlsoption`.
- Every object gets labels from `app.labels`; every name goes through `app.fullname`/`app.selectorLabels` or `app.configClaimName`.
- No tabs, no `networking.k8s.io`, no `latest`, no `PUID`/`PGID`, no `chart-example.local`, no `setGateway`.
- `helm lint . --namespace media-stack` and `helm template seerr . --namespace media-stack` must both pass.

## Review Focus

- A values change to `service.port`, `ingress.host` or `ingress.tls.*` must change the rendered objects and nothing may stay hardcoded.
- The secure route must reference both the security Middleware and the rate-limit Middleware; a route without them looks healthy and silently drops the hardening.
- The TLSOption must contain only suites Traefik accepts: no CBC, no TLS 1.3 suites, no `TLS_FALLBACK_SCSV`.
- The config claim must resolve to `seerr-config`; a different name would create a new empty claim and lose the request database.
- The pod must run as UID 1000, matching the image's `node` user and the existing volume ownership; a different UID makes `/app/config` unreadable.
- Readiness shares `/api/v1/status` because Seerr has no dependency-aware endpoint; a reviewer should confirm that no endpoint in the app separates process health from database health.

---

### Task 1: Chart code — metadata, values, workload and ingress stack

**Files:**
- Modify: `cluster/helm/media-stack/seerr/Chart.yaml`
- Modify: `cluster/helm/media-stack/seerr/values.yaml`
- Modify: `cluster/helm/media-stack/seerr/templates/_helpers.tpl`
- Modify: `cluster/helm/media-stack/seerr/templates/deployment.yaml`
- Modify: `cluster/helm/media-stack/seerr/templates/service.yaml`
- Replace: `cluster/helm/media-stack/seerr/templates/pvc-config.yaml` → `persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/seerr/templates/serviceaccount.yaml`
- Replace: `ingress-public.yaml`, `ingress-public-redirect.yaml`, `middleware-security.yaml`, `middleware.yaml`, `tls-option.yaml` → `ingressroute.yaml`, `middleware-headers.yaml`, `middleware-ratelimit.yaml`, `middleware-redirect.yaml`, `tlsoption.yaml`
- Create: `cluster/helm/media-stack/seerr/templates/NOTES.txt`

**Interfaces:**
- Produces for Task 2: the object list and names above; `helm template seerr . --namespace media-stack` renders exactly one Deployment, one Service, one ServiceAccount, one PVC, two IngressRoutes, three Middlewares, one TLSOption.

- [ ] **Step 1: Rewrite `Chart.yaml`**

Use `apiVersion: v2`, `name: seerr`, description "Seerr request portal on the official image, published through Traefik.", `type: application`, `version: 1.0.0`, `appVersion: "v3.4.1"`, `home: https://docs.seerr.dev/`, `sources: [https://github.com/seerr-team/seerr]`, keywords `seerr`, `media`, `requests`.

- [ ] **Step 2: Rewrite `values.yaml`**

Use the Plex/autobrr key layout. Exact values that matter:

```yaml
replicaCount: 1
strategy: Recreate
image:
  repository: ghcr.io/seerr-team/seerr
  tag: "v3.4.1"
  pullPolicy: IfNotPresent
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
  port: 5055
container:
  port: null
storage:
  enabled: true
  existingClaim: ""
  className: local-path
  accessModes: [ReadWriteOnce]
  size: 128Mi
  mountPath: /app/config
env:
  TZ: "Europe/Paris"
  LOG_LEVEL: "warn"
extraEnv: []
envFrom: []
volumes: []
volumeMounts: []
ingress:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  host: seerr.kub.home.ashudev.com
  pathPrefix: /
  labels: {}
  annotations: {}
  middlewares: []
  redirect:
    enabled: true
    entryPoint: web
  securityHeaders:
    enabled: true
    allowedHosts: []
    referrerPolicy: "no-referrer"
    permissionsPolicy: "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    contentSecurityPolicy: ""
  rateLimit:
    enabled: true
    average: 100
    burst: 200
    period: 1
  tls:
    enabled: true
    secretName: ""
    certResolver: myresolver
    options:
      enabled: true
      sniStrict: true
      minVersion: VersionTLS12
      cipherSuites:
        - TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256
        - TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256
        - TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384
        - TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384
        - TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305
        - TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305
      curvePreferences:
        - X25519
        - CurveP256
        - CurveP384
nodeSelector:
  kubernetes.io/hostname: streaming
tolerations: []
affinity: {}
resources:
  requests:
    cpu: 10m
    memory: 320Mi
  limits:
    memory: 512Mi
probes:
  startup:
    enabled: true
    path: /api/v1/status
    periodSeconds: 5
    timeoutSeconds: 5
    failureThreshold: 60
  liveness:
    enabled: true
    path: /api/v1/status
    initialDelaySeconds: 20
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    enabled: true
    path: /api/v1/status
    initialDelaySeconds: 10
    periodSeconds: 15
    timeoutSeconds: 5
    failureThreshold: 3
```

Add comments that record: the measured resource numbers; why readiness shares the status endpoint; why `LOG_LEVEL` is `warn`; why `PUID`/`PGID` are absent; why the claim stays 128Mi; why the rate limit can be switched off.

- [ ] **Step 3: Rewrite `_helpers.tpl` with `app.*` helpers**

Definitions: `app.name`, `app.fullname`, `app.chart`, `app.labels`, `app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName` (`default (printf "%s-config" (include "app.fullname" .)) .Values.storage.existingClaim`), `app.containerPort` (`default .Values.service.port .Values.container.port`).

- [ ] **Step 4: Write `deployment.yaml`**

Follow the Plex Deployment shape: `replicas`, `strategy`, selector, pod labels from `app.labels`, optional `imagePullSecrets`, ServiceAccount, pod security context, one container named `seerr` with `runAsNonRoot`/UID/GID and dropped capabilities, port `http`, a `PORT` entry rendered from `service.port` followed by env from `.Values.env` and `extraEnv`, optional `envFrom`, optional startup/liveness/readiness probes with a named `http` port, resources, the config claim mount, extra mounts, then volumes (config claim + extras), and scheduling fields. No hardcoded claim name, port, mount path or environment.

- [ ] **Step 5: Write `service.yaml`, `serviceaccount.yaml`, `persistentvolumeclaim.yaml`**

Service: ClusterIP from values, port `http` = `service.port`, `targetPort: http`, selector from `app.selectorLabels`. ServiceAccount: only when `serviceAccount.create`, with `automountServiceAccountToken` from values. PVC: only when `storage.enabled` and no `existingClaim`, named `app.configClaimName`, with `app.labels`, access modes, class and size from values.

- [ ] **Step 6: Write the ingress stack**

`ingressroute.yaml` renders the secure route and, when `ingress.redirect.enabled`, the redirect route separated by `---`. Both use `ingress.apiVersion`. The secure route:

```yaml
spec:
  entryPoints:
    - websecure
  routes:
    - kind: Rule
      match: Host(`<ingress.host>`) [&& PathPrefix(`<pathPrefix>`)]
      middlewares:
        - name: <fullname>-security          # when securityHeaders.enabled
        - name: <fullname>-ratelimit         # when rateLimit.enabled
        - name: <namespace>-<name>@kubernetescrd  # each ingress.middlewares entry
      services:
        - name: <fullname>
          port: <service.port>
          scheme: http
          passHostHeader: true
  tls:
    secretName: ...        # only when set
    certResolver: ...      # only when set
    options:
      name: <fullname>-tlsoption
      namespace: <release namespace>
```

The redirect route uses `ingress.redirect.entryPoint`, the same match, the redirect Middleware and the same service. `middleware-headers.yaml` renders `allowedHosts` (defaults to the host and `host:443`), frame/XSS/nosniff/HSTS, `referrerPolicy`, `permissionsPolicy` and an optional CSP. `middleware-ratelimit.yaml` renders average/burst/period. `middleware-redirect.yaml` renders `redirectScheme: https, permanent: true`. `tlsoption.yaml` renders minVersion, sniStrict, cipherSuites and curvePreferences. All are gated on their values flags and carry `app.labels`.

- [ ] **Step 7: Write `NOTES.txt`**

Report the release name and namespace, the public URL when ingress is enabled (or the port-forward form when not), and the config claim name.

- [ ] **Step 8: Delete the superseded templates**

Remove `ingress-public.yaml`, `ingress-public-redirect.yaml`, `middleware-security.yaml`, `middleware.yaml`, `tls-option.yaml`, `pvc-config.yaml`.

- [ ] **Step 9: Run the render assertions**

```bash
cd cluster/helm/media-stack/seerr
helm lint . --namespace media-stack
helm template seerr . --namespace media-stack > /tmp/seerr-after.yaml

grep -c '^kind:' /tmp/seerr-after.yaml                          # 10 (Deployment, Service, SA, PVC, 2 routes, 3 middlewares, TLSOption)
grep -q 'image: "ghcr.io/seerr-team/seerr:v3.4.1"' /tmp/seerr-after.yaml
grep -q 'containerPort: 5055' /tmp/seerr-after.yaml
grep -q 'path: /api/v1/status' /tmp/seerr-after.yaml
grep -q 'runAsUser: 1000' /tmp/seerr-after.yaml
grep -q 'name: seerr-security' /tmp/seerr-after.yaml
grep -q 'name: seerr-ratelimit' /tmp/seerr-after.yaml
grep -q 'name: seerr-config' /tmp/seerr-after.yaml
grep -q 'name: seerr-tlsoption' /tmp/seerr-after.yaml
grep -q 'namespace: media-stack' /tmp/seerr-after.yaml
! grep -q 'PUID\|PGID\|setGateway\|chart-example.local\|TLS_FALLBACK_SCSV\|CBC' /tmp/seerr-after.yaml
grep -rnP '\t' templates/ || echo "no tabs"
! grep -rn 'networking.k8s.io' templates/
! grep -rn 'latest' values.yaml
```

While a chart has no unit tests, every command above is a regression check: a wrong host, a missing Middleware, a lost claim, an ignored probe path or a rejected cipher value fails one of them.

- [ ] **Step 10: Commit**

```bash
git add cluster/helm/media-stack/seerr
git commit -m "🚀 feat(seerr): rebuild the chart on the contract and harden the public route"
```

---

### Task 2: Chart README and handover verification

**Files:**
- Create: `cluster/helm/media-stack/seerr/README.md`

**Interfaces:**
- Consumes: the object list, values keys and names from Task 1.

- [ ] **Step 1: Write the README**

Cover: upstream image and tag; how ArgoCD deploys it; the object list; the differences from the `autobrr` reference and why; the UID decision and the `PUID`/`PGID` removal; probes and what each endpoint proves; the log level and what it hides; the measured resources; the claim size and why it cannot grow; the edge policy (TLS, headers, allowed hosts, rate limit, no auth, no CSP); and the verification commands. Mention that changing the UID or replacing the claim breaks existing state, and that `LOG_LEVEL` can be raised temporarily while debugging.

- [ ] **Step 2: Run the full verification gate**

```bash
cd cluster/helm/media-stack/seerr
helm lint . --namespace media-stack
helm template seerr . --namespace media-stack > /tmp/seerr-final.yaml
grep -c '^kind:' /tmp/seerr-final.yaml
grep -n 'kind:\|  name:' /tmp/seerr-final.yaml
grep -rnP '\t' templates/ || echo "no tabs"
grep -rnE 'tag:[[:space:]]*"?latest' values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io' templates/ || echo "no core Ingress"
grep -nE 'PUID|PGID|setGateway|seerr-webui' /tmp/seerr-final.yaml || echo "no leftovers"
```

Expected: lint passes, 10 objects, every name in the Global Constraints list, and all four greps report clean.

- [ ] **Step 3: Commit**

```bash
git add cluster/helm/media-stack/seerr/README.md
git commit -m "📝 docs(seerr): chart README and verification notes"
```

- [ ] **Step 4: Handover**

```bash
git fetch origin
git rebase origin/develop
helm lint cluster/helm/media-stack/seerr --namespace media-stack
git push origin feature/seerr-chart-refactor
```

Open a PR against `develop` with the object-name table and the render-assertion output in the body. Do not merge; the operator merges.
