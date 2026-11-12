# Observability stack — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken vendored Prometheus and Grafana charts and the never-deployed Loki directory with four owned wrapper charts, each taking the project's current official chart as a pinned dependency, exposing Grafana, Prometheus and Loki on `*.kub.local` and shipping container logs with Fluent Bit.

**Architecture:** One ArgoCD Application per component, each pointing at its own directory under `cluster/helm/monitoring/`. Each directory is a local chart whose `Chart.yaml` pins one upstream chart as a dependency; all upstream settings live under that dependency's key in `values.yaml`; the only hand-written template is the `IngressRoute`. ArgoCD runs `helm dependency build` at sync time — no `charts/` and no `Chart.lock` in git. Everything runs on `nas` with `local-path` volumes on its dedicated SSD (`/mnt/ssd0`), never `/mnt/ultrafaststorage`.

**Tech Stack:** Helm 4 (repo is validated with `helm` v4.2.4), ArgoCD, K3s v1.30.1, Traefik 2.x CRDs (`traefik.containo.us/v1alpha1`), Prometheus 3.16, Grafana 13.3.1, Loki 3.7.9 in monolithic mode, Fluent Bit 5.1.3.

**Spec:** `docs/superpowers/specs/2026-11-12-observability-stack-design.md`

## Global Constraints

- Chart versions, exact: `prometheus-community/prometheus` **29.47.2**, `grafana-community/grafana` **14.4.1**, `grafana-community/loki` **21.2.1**, `fluent/fluent-bit` **0.58.3**. No ranges, no `*`.
- Image pins, exact: `quay.io/prometheus/prometheus:v3.16.0`, `quay.io/prometheus-operator/prometheus-config-reloader:v0.95.2`, `quay.io/prometheus/node-exporter:v1.12.1`, `registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.20.0`, `docker.io/grafana/grafana:13.3.1-distroless`, `docker.io/library/busybox:1.38.0`, `docker.io/curlimages/curl:8.23.0`, `docker.io/grafana/loki:3.7.9`, `cr.fluentbit.io/fluent/fluent-bit:5.1.3`. Never `latest`.
- Sub-chart settings are nested under the dependency's name (`prometheus:`, `grafana:`, `loki:`, `fluent-bit:`). Top-level keys in a wrapper are the wrapper's own (`traefik:`, `ingress:`) and are not passed down.
- **Every wrapper sets `fullnameOverride`** on its dependency to the bare component name. ArgoCD names the Helm release after the Application, so a chart called `prometheus` in a release called `prometheus` renders `prometheus-prometheus-server` without it — and every service name in this plan (`prometheus-server`, `grafana`, `loki`) plus the datasource URLs would be wrong. Prometheus is the exception that needs two: its server objects come from a separate `server.fullnameOverride`, so that one is set to `prometheus-server` as well, and its two sub-charts are pinned to `prometheus-kube-state-metrics` and `prometheus-node-exporter`.
- **Render every chart with the release name ArgoCD will use** (`prometheus`, `grafana`, `loki`, `fluent-bit`), never an invented one: the Prometheus server name falls back to `<release>-prometheus-server` when the release name does not contain `prometheus`, which is how a rename would silently break the datasources.
- One namespace, `monitoring`. Every pod `nodeSelector: {kubernetes.io/hostname: nas}` except the two DaemonSets (`prometheus-node-exporter`, `fluent-bit`), which run on all four nodes.
- Volumes: `prometheus-server` 25Gi, `loki` 20Gi, `grafana` 5Gi, default `local-path` class. Nothing on `/mnt/ultrafaststorage`.
- Retention: metrics `90d` with `retentionSize: 20GB`; logs `720h` enforced by the Loki compactor.
- Ingress is `traefik.containo.us/v1alpha1` taken from `.Values.traefik.apiVersion`, `entryPoints: [web]`, no TLS, no middleware. Never `networking.k8s.io/v1`.
- No `charts/`, no `Chart.lock`, no CRDs, no Alertmanager, no dashboards or alert rules in git.
- `cluster/argocd/**` is CRLF — every file touched there stays CRLF. `cluster/helm/**` is LF.
- Commit messages: emoji prefix, conventional type (`✨ feat:`, `♻️ refactor:`, `🔥 chore:`, `📝 docs:`).

## Review Focus

1. **Loki retention is inert when the compactor block is missing** — a fresh install with `retention_period: 720h` but no `compactor.retention_enabled` keeps every byte forever and fills the SSD after months. Task 3 asserts both keys in the rendered config.
2. **A claim that does not land on `nas`** — the pod is pinned, but a forgotten `storageClassName: nfs-*` would still schedule and then write to the wrong disk. Task 1–3 assert the rendered PVC has no `storageClassName` (default class) and the pod carries the `nas` selector.
3. **Grafana starting without its admin Secret** — `existingSecret: grafana` with the wrong key names gives `CreateContainerConfigError` and looks like a chart bug. Task 2 asserts both key names in the rendered Deployment and carries the operator step that keeps the Secret alive across the prune.
4. **Fluent Bit pushing to the wrong endpoint or labelling by pod** — one pod label per stream is what makes a small Loki collapse. Task 4 asserts the rendered output block hosts `loki.monitoring.svc.cluster.local:3100` and that the pod name appears only in `Structured_Metadata`.
5. **An IngressRoute naming a Service that does not exist** — the route renders fine and returns 404. Tasks 1–3 assert the `IngressRoute` service name matches the name rendered by the same release.

---

## File Structure

| Path | Responsibility |
|---|---|
| `cluster/helm/monitoring/prometheus/{Chart.yaml,values.yaml,templates/ingressroute.yaml}` | replaces the vendored Prometheus chart in place |
| `cluster/helm/monitoring/grafana/{Chart.yaml,values.yaml,templates/ingressroute.yaml}` | replaces the vendored Grafana chart in place |
| `cluster/helm/monitoring/loki/{Chart.yaml,values.yaml,templates/ingressroute.yaml}` | replaces the values-only directory |
| `cluster/helm/monitoring/fluent-bit/{Chart.yaml,values.yaml}` | new chart, no template of its own |
| `cluster/argocd/projects/monitoring/{prometheus,grafana,loki,fluent-bit}.yaml` | one Application each, CRLF |
| `.gitignore` | ignore built `charts/` and `Chart.lock` for these four |
| `docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md` | W6 rewritten, §8.3 pattern B retired |
| `AGENTS.md` | pattern list, placement note, known issues 14/16 |

---

### Task 0: Branch and commit the design

**Files:**
- Create (commit): `docs/superpowers/specs/2026-11-12-observability-stack-design.md`

**Interfaces:**
- Consumes: nothing.
- Produces: branch `feature/observability-stack`, based on `origin/develop`, containing the approved design. Every later task commits here.

- [ ] **Step 1: Create the branch from develop**

```bash
git fetch origin
git switch --create feature/observability-stack origin/develop
git status --short
```

Expected: `?? docs/superpowers/specs/2026-11-12-observability-stack-design.md` only — the untracked design carries over. If any other file is listed, stop and report it.

- [ ] **Step 2: Commit the design**

```bash
git add docs/superpowers/specs/2026-11-12-observability-stack-design.md
git commit -m "📝 docs(monitoring): design the observability stack rebuild"
```

---

### Task 1: Prometheus wrapper chart

**Files:**
- Delete: `cluster/helm/monitoring/prometheus/` (vendored chart: `Chart.yaml`, `Chart.lock`, `README.md`, `values.yaml`, `values.schema.json`, `templates/`, `charts/`)
- Create: `cluster/helm/monitoring/prometheus/Chart.yaml`
- Create: `cluster/helm/monitoring/prometheus/values.yaml`
- Create: `cluster/helm/monitoring/prometheus/templates/ingressroute.yaml`
- Modify: `cluster/argocd/projects/monitoring/prometheus.yaml` (CRLF)

**Interfaces:**
- Consumes: nothing.
- Produces: Service `prometheus-server` on port 80; claim `prometheus-server`; TSDB retention `90d` / `20GB`; every `prometheus.io/scrape` annotation in the cluster is scraped by the chart's default jobs. Task 2's Grafana datasource points at `http://prometheus-server.monitoring.svc.cluster.local`.

- [ ] **Step 1: Delete the vendored chart**

```bash
git rm -r cluster/helm/monitoring/prometheus
```

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: prometheus
description: Prometheus - observability
type: application
version: 0.1.0
appVersion: v3.16.0
dependencies:
  - name: prometheus
    version: 29.47.2
    repository: https://prometheus-community.github.io/helm-charts
```

- [ ] **Step 3: Write `values.yaml`**

```yaml
traefik:
  apiVersion: traefik.containo.us/v1alpha1

ingress:
  host: prometheus.kub.local
  service: prometheus-server
  port: 80

prometheus:
  fullnameOverride: prometheus
  alertmanager:
    enabled: false
  prometheus-pushgateway:
    enabled: false
  server:
    fullnameOverride: prometheus-server
    retention: 90d
    retentionSize: 20GB
    strategy:
      type: Recreate
    persistentVolume:
      enabled: true
      size: 25Gi
    nodeSelector:
      kubernetes.io/hostname: nas
    resources:
      requests:
        cpu: 100m
        memory: 512Mi
      limits:
        memory: 1Gi
    image:
      repository: quay.io/prometheus/prometheus
      tag: v3.16.0
  configmapReload:
    prometheus:
      image:
        repository: quay.io/prometheus-operator/prometheus-config-reloader
        tag: v0.95.2
  kube-state-metrics:
    fullnameOverride: prometheus-kube-state-metrics
    nodeSelector:
      kubernetes.io/hostname: nas
    image:
      registry: registry.k8s.io
      repository: kube-state-metrics/kube-state-metrics
      tag: v2.20.0
    resources:
      requests:
        cpu: 10m
        memory: 64Mi
      limits:
        memory: 128Mi
  prometheus-node-exporter:
    fullnameOverride: prometheus-node-exporter
    image:
      registry: quay.io
      repository: prometheus/node-exporter
      tag: v1.12.1
    resources:
      requests:
        cpu: 10m
        memory: 32Mi
      limits:
        memory: 64Mi
  # The two edge Pis run their exporters outside the cluster, so annotation
  # discovery can never find them. Ports: 9100 node-exporter, 9902 smartctl,
  # 9323 Docker.
  scrapeConfigs:
    rbp0:
      enabled: true
      static_configs:
        - targets:
            - 192.168.1.22:9100
            - 192.168.1.22:9902
            - 192.168.1.22:9323
    rbp1:
      enabled: true
      static_configs:
        - targets:
            - 192.168.1.38:9100
            - 192.168.1.38:9902
            - 192.168.1.38:9323
```

- [ ] **Step 4: Write `templates/ingressroute.yaml`**

This exact template is reused by Tasks 2 and 3 with different values.

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
    - web
  routes:
    - match: Host(`{{ .Values.ingress.host }}`)
      kind: Rule
      services:
        - name: {{ .Values.ingress.service }}
          port: {{ .Values.ingress.port }}
```

- [ ] **Step 5: Build dependencies and lint**

```bash
helm dependency build cluster/helm/monitoring/prometheus
helm lint cluster/helm/monitoring/prometheus -n monitoring
```

Expected: `Saving 1 charts` / `Downloading prometheus from repo https://prometheus-community.github.io/helm-charts`, then `1 chart(s) linted, 0 chart(s) failed`. Node-exporter, kube-state-metrics, alertmanager and pushgateway arrive inside that one tarball.

- [ ] **Step 6: Render and assert**

```bash
helm template prometheus cluster/helm/monitoring/prometheus -n monitoring > /tmp/prom.yaml
grep -q 'image: "quay.io/prometheus/prometheus:v3.16.0"' /tmp/prom.yaml
grep -q -- '--storage.tsdb.retention.time=90d' /tmp/prom.yaml
grep -q -- '--storage.tsdb.retention.size=20GB' /tmp/prom.yaml
grep -q 'quay.io/prometheus/node-exporter:v1.12.1' /tmp/prom.yaml
grep -q 'registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.20.0' /tmp/prom.yaml
grep -q 'kubernetes.io/hostname: nas' /tmp/prom.yaml
grep -q 'traefik.containo.us/v1alpha1' /tmp/prom.yaml
grep -q 'name: prometheus-server' /tmp/prom.yaml
helm template prometheus cluster/helm/monitoring/prometheus -n monitoring \
  --show-only templates/ingressroute.yaml > /tmp/prom-route.yaml
grep -q 'Host(`prometheus.kub.local`)' /tmp/prom-route.yaml
grep -q 'name: prometheus-server' /tmp/prom-route.yaml
! grep -q 'storageClassName' /tmp/prom.yaml
! grep -qE '^  name: .*(alertmanager|pushgateway)' /tmp/prom.yaml
grep -q -- '- job_name: rbp0' /tmp/prom.yaml
grep -q '192.168.1.22:9323' /tmp/prom.yaml
grep -q '192.168.1.38:9902' /tmp/prom.yaml
! grep -q 'latest' /tmp/prom.yaml
! grep -q 'apiVersion: networking.k8s.io' /tmp/prom.yaml
echo OK
```

Expected: `OK`. Every `grep -q` exits 0; the negated ones find nothing.

The chart still renders a `prometheus-pushgateway` **scrape job** inside the ConfigMap even with the pushgateway sub-chart disabled. That is a no-op job with no targets and is not a defect — do not try to remove it.

If `alertmanager` appears as an object name, the values are not nested under `prometheus:`.

- [ ] **Step 7: Rewrite the ArgoCD Application (CRLF)**

`cluster/argocd/projects/monitoring/prometheus.yaml` becomes:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: prometheus
  namespace: argocd
spec:
  project: default
  destination:
    server: 'https://kubernetes.default.svc'
    namespace: monitoring
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      prune: true
      selfHeal: true
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/monitoring/prometheus
```

Write it with CRLF endings:

```bash
python3 - <<'PY'
p = "cluster/argocd/projects/monitoring/prometheus.yaml"
body = open(p).read().replace("\r\n", "\n").replace("\n", "\r\n")
open(p, "w", newline="").write(body)
PY
file cluster/argocd/projects/monitoring/prometheus.yaml
grep -c $'\r' cluster/argocd/projects/monitoring/prometheus.yaml
```

Expected: `file` reports `with CRLF line terminators`; the `grep -c` count equals the number of lines.

- [ ] **Step 8: Commit**

```bash
git add -A cluster/helm/monitoring/prometheus cluster/argocd/projects/monitoring/prometheus.yaml
git commit -m "♻️ refactor(monitoring): wrap the official Prometheus chart and keep annotation scraping"
```

---

### Task 2: Grafana wrapper chart and the admin Secret cutover

**Files:**
- Delete: `cluster/helm/monitoring/grafana/` (vendored chart: `Chart.yaml`, `README.md`, `values.yaml`, `.helmignore`, `ci/`, `dashboards/`, `templates/`)
- Create: `cluster/helm/monitoring/grafana/Chart.yaml`
- Create: `cluster/helm/monitoring/grafana/values.yaml`
- Create: `cluster/helm/monitoring/grafana/templates/ingressroute.yaml`
- Modify: `cluster/argocd/projects/monitoring/grafana.yaml` (CRLF)

**Interfaces:**
- Consumes: Service `prometheus-server` port 80 (Task 1); Service `loki` port 3100 (Task 3 — the Loki datasource is declared now and stays red until Task 3 syncs, which is expected).
- Produces: Service `grafana` port 80; claim `grafana`; a Grafana reachable at `grafana.kub.local` with both datasources provisioned.

- [ ] **Step 1: Delete the vendored chart**

```bash
git rm -r cluster/helm/monitoring/grafana
```

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: grafana
description: Grafana - observability
type: application
version: 0.1.0
appVersion: 13.3.1
dependencies:
  - name: grafana
    version: 14.4.1
    repository: https://grafana-community.github.io/helm-charts
```

- [ ] **Step 3: Write `values.yaml`**

```yaml
traefik:
  apiVersion: traefik.containo.us/v1alpha1

ingress:
  host: grafana.kub.local
  service: grafana
  port: 80

grafana:
  fullnameOverride: grafana
  deploymentStrategy:
    type: Recreate
  nodeSelector:
    kubernetes.io/hostname: nas
  resources:
    requests:
      cpu: 50m
      memory: 192Mi
    limits:
      memory: 512Mi
  image:
    registry: docker.io
    repository: grafana/grafana
    tag: 13.3.1-distroless
  initChownData:
    image:
      registry: docker.io
      repository: library/busybox
      tag: 1.38.0
  downloadDashboardsImage:
    registry: docker.io
    repository: curlimages/curl
    tag: 8.23.0
  testFramework:
    enabled: false
  admin:
    existingSecret: grafana
    userKey: admin-user
    passwordKey: admin-password
  persistence:
    enabled: true
    size: 5Gi
  ingress:
    enabled: false
  datasources:
    datasources.yaml:
      apiVersion: 1
      datasources:
        - name: Prometheus
          type: prometheus
          url: http://prometheus-server.monitoring.svc.cluster.local
          access: proxy
          isDefault: true
        - name: Loki
          type: loki
          url: http://loki.monitoring.svc.cluster.local:3100
          access: proxy
```

- [ ] **Step 4: Write `templates/ingressroute.yaml`**

Byte-identical to Task 1 Step 4. Do not retype it: copy the file.

```bash
cp cluster/helm/monitoring/prometheus/templates/ingressroute.yaml \
   cluster/helm/monitoring/grafana/templates/ingressroute.yaml
```

- [ ] **Step 5: Build dependencies, lint and render**

```bash
helm dependency build cluster/helm/monitoring/grafana
helm lint cluster/helm/monitoring/grafana -n monitoring
helm template grafana cluster/helm/monitoring/grafana -n monitoring > /tmp/graf.yaml
grep -q 'docker.io/grafana/grafana:13.3.1-distroless' /tmp/graf.yaml
grep -q 'docker.io/library/busybox:1.38.0' /tmp/graf.yaml
grep -q 'secretKeyRef' /tmp/graf.yaml
grep -A3 'secretKeyRef' /tmp/graf.yaml | grep -q 'name: grafana'
grep -q 'admin-user' /tmp/graf.yaml
grep -q 'admin-password' /tmp/graf.yaml
! grep -q 'kind: Secret' /tmp/graf.yaml
grep -q 'http://prometheus-server.monitoring.svc.cluster.local' /tmp/graf.yaml
grep -q 'http://loki.monitoring.svc.cluster.local:3100' /tmp/graf.yaml
grep -q 'kubernetes.io/hostname: nas' /tmp/graf.yaml
grep -q 'type: Recreate' /tmp/graf.yaml
helm template grafana cluster/helm/monitoring/grafana -n monitoring \
  --show-only templates/ingressroute.yaml > /tmp/graf-route.yaml
grep -q 'Host(`grafana.kub.local`)' /tmp/graf-route.yaml
grep -q 'name: grafana' /tmp/graf-route.yaml
! grep -q 'storageClassName' /tmp/graf.yaml
! grep -q 'latest' /tmp/graf.yaml
! grep -q 'apiVersion: networking.k8s.io' /tmp/graf.yaml
echo OK
```

Expected: `OK`. If `admin-user`/`admin-password` are missing, `admin.existingSecret` is not nested under `grafana:`. If `kind: Secret` appears, the chart is generating its own admin Secret — meaning `admin.existingSecret` did not reach the sub-chart, and the operator's preserved Secret would be overwritten on every sync. Chart 14.4.1 renders the reference as `secretKeyRef: {key: ..., name: grafana}`, so the literals `existingSecret:`/`secretName:` never appear in its output; the `secretKeyRef` checks are the real ones.

- [ ] **Step 6: Operator step — keep the admin Secret across the prune**

**Run by the operator, before the Grafana Application is deleted or switched over.** The current chart creates the Secret; the new one only reads it.

```bash
kubectl -n monitoring annotate secret grafana argocd.argoproj.io/tracking-id- --overwrite
kubectl -n monitoring label secret grafana app.kubernetes.io/instance- --overwrite
kubectl -n monitoring get secret grafana -o jsonpath='{.data.admin-user}' >/dev/null && echo present
```

Expected: `present`. If the Secret does not exist, create it instead:

```bash
kubectl -n monitoring create secret generic grafana \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$(openssl rand -base64 24)"
```

- [ ] **Step 7: Rewrite the ArgoCD Application (CRLF)**

Same content as Task 1 Step 7 with `name: grafana` and `path: cluster/helm/monitoring/grafana`, then re-apply the CRLF conversion and the `file` check from that step.

This is the file where `syncPolicy.automated` is new — the old Application had no automated block.

- [ ] **Step 8: Commit**

```bash
git add -A cluster/helm/monitoring/grafana cluster/argocd/projects/monitoring/grafana.yaml
git commit -m "♻️ refactor(monitoring): wrap the community Grafana chart and drop the vendored copy"
```

---

### Task 3: Loki wrapper chart

**Files:**
- Delete: `cluster/helm/monitoring/loki/values.yaml`
- Create: `cluster/helm/monitoring/loki/Chart.yaml`
- Create: `cluster/helm/monitoring/loki/values.yaml`
- Create: `cluster/helm/monitoring/loki/templates/ingressroute.yaml`
- Create: `cluster/argocd/projects/monitoring/loki.yaml` (CRLF, new file)

**Interfaces:**
- Consumes: nothing.
- Produces: Service `loki` port 3100, UI at `/ui`, claim `loki`; 30-day retention enforced by the compactor. Task 2's Loki datasource and Task 4's output block both point at `loki.monitoring.svc.cluster.local:3100`.

- [ ] **Step 1: Remove the values-only directory**

```bash
git rm cluster/helm/monitoring/loki/values.yaml
```

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: loki
description: Loki - observability
type: application
version: 0.1.0
appVersion: 3.7.9
dependencies:
  - name: loki
    version: 21.2.1
    repository: https://grafana-community.github.io/helm-charts
```

- [ ] **Step 3: Write `values.yaml`**

```yaml
traefik:
  apiVersion: traefik.containo.us/v1alpha1

ingress:
  host: loki.kub.local
  service: loki
  port: 3100

loki:
  fullnameOverride: loki
  deploymentMode: Monolithic
  singleBinary:
    replicas: 1
    kind: StatefulSet
    nodeSelector:
      kubernetes.io/hostname: nas
    resources:
      requests:
        cpu: 100m
        memory: 512Mi
      limits:
        memory: 2Gi
    persistence:
      enabled: true
      size: 20Gi
      enableStatefulSetAutoDeletePVC: false
  backend:
    replicas: 0
  read:
    replicas: 0
  write:
    replicas: 0
  ingester:
    replicas: 0
  querier:
    replicas: 0
  queryFrontend:
    replicas: 0
  queryScheduler:
    replicas: 0
  distributor:
    replicas: 0
  compactor:
    replicas: 0
  indexGateway:
    replicas: 0
  bloomGateway:
    replicas: 0
  gateway:
    enabled: false
  lokiCanary:
    enabled: false
  chunksCache:
    enabled: false
  resultsCache:
    enabled: false
  test:
    enabled: false
  monitoring:
    selfMonitoring:
      enabled: false
      grafanaAgent:
        installOperator: false
    lokiCanary:
      enabled: false
    dashboards:
      enabled: false
    serviceMonitor:
      enabled: false
  loki:
    image:
      registry: docker.io
      repository: grafana/loki
      tag: 3.7.9
    auth_enabled: false
    ui:
      enabled: true
    commonConfig:
      replication_factor: 1
    schemaConfig:
      configs:
        - from: "2024-04-01"
          store: tsdb
          object_store: filesystem
          schema: v13
          index:
            prefix: index_
            period: 24h
    storage:
      type: filesystem
    compactor:
      working_directory: /var/loki/compactor
      retention_enabled: true
      delete_request_store: filesystem
    limits_config:
      retention_period: 720h
```

Note the nesting: `deploymentMode`, `singleBinary`, `gateway`, `lokiCanary`, `chunksCache`, `resultsCache`, `test` and `monitoring` are keys of the **Loki chart**, so they sit under `loki:` in this wrapper; `image`, `ui`, `schemaConfig`, `compactor` and `limits_config` are keys **within** the chart's `loki:` value, so they sit under `loki.loki:`.

- [ ] **Step 4: Write `templates/ingressroute.yaml`**

Copy from Task 1 Step 4, unchanged.

- [ ] **Step 5: Build, lint, render and assert**

```bash
helm dependency build cluster/helm/monitoring/loki
helm lint cluster/helm/monitoring/loki -n monitoring
helm template loki cluster/helm/monitoring/loki -n monitoring > /tmp/loki.yaml
grep -q 'docker.io/grafana/loki:3.7.9' /tmp/loki.yaml
grep -q -- '-target=all,ui' /tmp/loki.yaml
grep -q 'retention_enabled: true' /tmp/loki.yaml
grep -q 'delete_request_store: filesystem' /tmp/loki.yaml
grep -q 'retention_period: 720h' /tmp/loki.yaml
grep -q 'object_store: filesystem' /tmp/loki.yaml
grep -q 'kubernetes.io/hostname: nas' /tmp/loki.yaml
grep -c 'kind: StatefulSet' /tmp/loki.yaml
helm template loki cluster/helm/monitoring/loki -n monitoring \
  --show-only templates/ingressroute.yaml > /tmp/loki-route.yaml
grep -q 'Host(`loki.kub.local`)' /tmp/loki-route.yaml
grep -q 'name: loki' /tmp/loki-route.yaml
! grep -q 'storageClassName' /tmp/loki.yaml
! grep -q 'loki-chunks-cache\|loki-results-cache' /tmp/loki.yaml
! grep -q 'index-gateway' /tmp/loki.yaml
! grep -q 'latest' /tmp/loki.yaml
echo OK
```

Expected: `OK`, and the `kind: StatefulSet` count is `1`. If `retention_enabled` is absent, `compactor` is at the wrong nesting level and the 30-day retention silently does nothing.

One harmless artifact: with both caches disabled the chart still renders an orphan `loki-memcached` ServiceAccount. Leave it — it holds no workload and no permissions that matter.

- [ ] **Step 6: Create the ArgoCD Application (CRLF)**

`cluster/argocd/projects/monitoring/loki.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: loki
  namespace: argocd
spec:
  project: default
  destination:
    server: 'https://kubernetes.default.svc'
    namespace: monitoring
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    automated:
      prune: true
      selfHeal: true
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/monitoring/loki
```

Convert with the same `python3` snippet as Task 1 Step 7 (substituting the path) and check with `file`.

- [ ] **Step 7: Commit**

```bash
git add -A cluster/helm/monitoring/loki cluster/argocd/projects/monitoring/loki.yaml
git commit -m "✨ feat(monitoring): add Loki in monolithic mode with 30-day retention"
```

---

### Task 4: Fluent Bit wrapper chart

**Files:**
- Create: `cluster/helm/monitoring/fluent-bit/Chart.yaml`
- Create: `cluster/helm/monitoring/fluent-bit/values.yaml`
- Create: `cluster/argocd/projects/monitoring/fluent-bit.yaml` (CRLF, new file)

**Interfaces:**
- Consumes: Service `loki` port 3100 (Task 3).
- Produces: a DaemonSet on all four nodes; no Service is consumed by anything else.

- [ ] **Step 1: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: fluent-bit
description: Fluent Bit - log shipping for observability
type: application
version: 0.1.0
appVersion: 5.1.3
dependencies:
  - name: fluent-bit
    version: 0.58.3
    repository: https://fluent.github.io/helm-charts
```

- [ ] **Step 2: Write `values.yaml`**

```yaml
fluent-bit:
  fullnameOverride: fluent-bit
  kind: DaemonSet
  image:
    repository: cr.fluentbit.io/fluent/fluent-bit
    tag: 5.1.3
  testFramework:
    enabled: false
  rbac:
    create: true
    nodeAccess: false
    eventsAccess: false
  resources:
    requests:
      cpu: 10m
      memory: 64Mi
    limits:
      memory: 128Mi
  livenessProbe:
    httpGet:
      path: /api/v2/health
      port: http
  config:
    service: |
      [SERVICE]
          Daemon Off
          Flush 5
          Log_Level info
          Parsers_File /fluent-bit/etc/parsers.conf
          Parsers_File /fluent-bit/etc/conf/custom_parsers.conf
          HTTP_Server On
          HTTP_Listen 0.0.0.0
          HTTP_Port 2020
          Health_Check On
    inputs: |
      [INPUT]
          Name tail
          Path /var/log/containers/*.log
          multiline.parser docker, cri
          Tag kube.*
          Mem_Buf_Limit 5MB
          Skip_Long_Lines On
    filters: |
      [FILTER]
          Name kubernetes
          Match kube.*
          Merge_Log On
          Keep_Log Off
          K8S-Logging.Parser On
          K8S-Logging.Exclude On
    outputs: |
      [OUTPUT]
          Name loki
          Match kube.*
          Host loki.monitoring.svc.cluster.local
          Port 3100
          Line_Format json
          Compress gzip
          Retry_Limit no_limits
          Labels job=fluent-bit, namespace=$kubernetes['namespace_name'], container=$kubernetes['container_name'], node=$kubernetes['host']
          Structured_Metadata pod=$kubernetes['pod_name']
```

The chart's default second input (`systemd`, `kubelet.service`) is deliberately omitted: K3s runs the kubelet inside its own process and that unit does not exist here.

- [ ] **Step 3: Build, lint, render and assert**

```bash
helm dependency build cluster/helm/monitoring/fluent-bit
helm lint cluster/helm/monitoring/fluent-bit -n monitoring
helm template fluent-bit cluster/helm/monitoring/fluent-bit -n monitoring > /tmp/fb.yaml
grep -q 'kind: DaemonSet' /tmp/fb.yaml
grep -q 'cr.fluentbit.io/fluent/fluent-bit:5.1.3' /tmp/fb.yaml
grep -q 'Host loki.monitoring.svc.cluster.local' /tmp/fb.yaml
grep -q 'Port 3100' /tmp/fb.yaml
grep -q 'Structured_Metadata pod=' /tmp/fb.yaml
grep -q '/api/v2/health' /tmp/fb.yaml
! grep -q 'kubelet.service' /tmp/fb.yaml
! grep -qE '^ +Labels .*pod=' /tmp/fb.yaml
! grep -q 'nodeSelector' /tmp/fb.yaml
! grep -q 'latest' /tmp/fb.yaml
echo OK
```

Expected: `OK`. The DaemonSet has no `nodeSelector` (it must schedule on every node), and `pod=` appears only in the `Structured_Metadata` line.

- [ ] **Step 4: Create the ArgoCD Application (CRLF)**

Same content as Task 3 Step 6 with `name: fluent-bit` and `path: cluster/helm/monitoring/fluent-bit`, CRLF converted the same way.

- [ ] **Step 5: Commit**

```bash
git add -A cluster/helm/monitoring/fluent-bit cluster/argocd/projects/monitoring/fluent-bit.yaml
git commit -m "✨ feat(monitoring): ship container logs to Loki with Fluent Bit"
```

---

### Task 5: Gitignore the built charts and reconcile the documents

**Files:**
- Modify: `.gitignore`
- Modify: `docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the decisions in the design doc §5.1 (no lock) and §10 (documentation).
- Produces: nothing other tasks consume.

- [ ] **Step 1: Ignore built dependencies for these four charts**

Append to `.gitignore`:

```
cluster/helm/monitoring/*/charts/
cluster/helm/monitoring/*/Chart.lock
```

Remove the now-dangling line `!cluster/helm/monitoring/grafana/templates/secret.yaml`.

- [ ] **Step 2: Verify nothing built is staged**

```bash
git status --short
```

Expected: `.gitignore`, the two docs, and nothing under `cluster/helm/monitoring/*/charts/`. If a `charts/` or `Chart.lock` appears, the ignore patterns are wrong.

- [ ] **Step 3: Retire W6 in the stabilisation spec**

Replace the whole `### W6 — Observability` section with:

```markdown
### W6 — Observability

Superseded by `2026-11-12-observability-stack-design.md`, which replaces the vendored Prometheus
and Grafana charts and the values-only `loki/` directory with one owned wrapper chart per
component.

What survives from this workstream:

1. The vendored `prometheus` and `grafana` charts and their Applications are removed.
2. `mktxp`, `pihole-exporter` and `prometheus-smartctl` keep working unchanged: Prometheus still
   scrapes them through their `prometheus.io/scrape` annotations, so no ServiceMonitor layer and
   no operator CRDs are introduced.

Deferred to their own features: alerting (Grafana unified alerting, Discord delivery), dashboards
as code, and the `mktxp` floating `latest` tag.
```

- [ ] **Step 4: Retire pattern B in §8.3**

Replace rule 1 with:

```markdown
1. **Official chart as a dependency.** Where the upstream project publishes a Helm chart, the
   repo owns a small chart in `cluster/helm/` whose `Chart.yaml` pins that chart to an exact
   `version:`, and whose `values.yaml` pins the image repository and tag of every container it
   renders. The chart version never lives in an Application's `targetRevision`. `charts/` and
   `Chart.lock` are not committed — ArgoCD runs `helm dependency build` at sync time, and a
   committed lock goes stale on the first Renovate bump.
```

- [ ] **Step 5: Update AGENTS.md**

Three edits:

1. §2 bullet list: replace "Three app patterns" heading usage so the wrapper-with-dependencies shape is named as the way external charts are consumed, and note that `prometheus` and `grafana` are no longer pattern C.
2. §4 placement: add `nas` — observability stack (Prometheus, Grafana, Loki), on `local-path`, with a note that `/mnt/ultrafaststorage` is reserved for qbittorrent.
3. §7 known issues: mark 14 (`cluster/helm/monitoring/loki` values-only directory) and 16 (vendored `prometheus`/`grafana`) as resolved for the monitoring charts. Leave the `minio-nfs` half of 14 and all of 15 and 13 alone.

- [ ] **Step 6: Commit**

```bash
git add .gitignore AGENTS.md docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md
git commit -m "📝 docs: record the wrapper-chart pattern and retire the observability workstream"
```

---

### Task 6: Whole-branch validation and handover

**Files:**
- Read only.

**Interfaces:**
- Consumes: everything above.
- Produces: a branch ready for a pull request to `develop`.

- [ ] **Step 1: Render every self-contained chart in the repo**

```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```

Expected: no `FAIL` lines. `SKIP` lines are expected for the new wrappers (their `charts/` are built locally and gitignored) and for authentik, dashy, ashuvidz-website, pgadmin, discourse, postiz.

- [ ] **Step 2: Render the four wrappers explicitly**

```bash
for c in prometheus grafana loki fluent-bit; do
  helm template monitoring "cluster/helm/monitoring/$c" -n monitoring >/dev/null || echo "FAIL $c"
done
echo done
```

Expected: `done` with no `FAIL`.

- [ ] **Step 3: Reference checks (CRLF-safe)**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")

while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u)
```

Expected: no output.

- [ ] **Step 4: Confirm the four Applications and the three routes exist exactly once**

```bash
grep -rl 'path: cluster/helm/monitoring' cluster/argocd/projects/monitoring | sort
grep -rn 'ingressroute.yaml' cluster/helm/monitoring | wc -l
grep -rn 'traefik.containo.us\|networking.k8s.io' cluster/helm/monitoring | grep -v 'fluent-bit' | sort
grep -q -- '- job_name: rbp0' <(helm template prometheus cluster/helm/monitoring/prometheus -n monitoring)
```

Expected: four Application files; three template files; the third command lists the three `values.yaml` `apiVersion` lines and three template `apiVersion` lines, and no `networking.k8s.io`.

- [ ] **Step 5: Rebase on develop**

```bash
git fetch origin
git rebase origin/develop
```

Expected: clean rebase. If it conflicts, stop and report rather than resolving blindly.

- [ ] **Step 6: Push and open the pull request**

```bash
git push -u origin feature/observability-stack
```

Then open the PR with `github_create_pull_request`, base `develop`, title
`♻️ refactor(monitoring): rebuild the observability stack on the community charts`.

The body must state: four wrapper charts; the three new hostnames; the deleted vendored charts;
the Grafana Secret needed before the Grafana Application switches over; and that retention is 90 days of metrics and 30 days of logs on `nas`.

- [ ] **Step 7: Post-merge cluster verification (operator, after the PR merges)**

```bash
kubectl -n monitoring get pods -o wide
kubectl -n monitoring get pvc
kubectl -n monitoring get ingressroute
```

Expected: `prometheus-server`, `prometheus-kube-state-metrics`, `grafana`, `loki` on `nas`; `prometheus-node-exporter` and `fluent-bit` on all four nodes; three PVCs `Bound`; three IngressRoutes. Then `grafana.kub.local`, `prometheus.kub.local` and `loki.kub.local/ui` load.
