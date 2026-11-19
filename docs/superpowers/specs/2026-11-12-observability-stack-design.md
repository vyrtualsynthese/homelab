# Observability stack — design

2026-11-12. Status: awaiting operator approval. Supersedes W6 of
`2026-11-10-homelab-stabilisation-design.md` and corrects two statements elsewhere in that
document (§8.1 placement, §8.3 pattern B). §10 lists the documentation this change rewrites.

## 1. What this is, and why

The `monitoring` namespace holds two hand-edited copies of 2024-era Helm charts and one directory
that was never a release:

| Today | State |
|---|---|
| `cluster/helm/monitoring/prometheus` (vendored `prometheus` 14.8.0, appVersion 2.26.0) | runs on a `longhorn-static` claim that is `Terminating`; TSDB lost on next restart |
| `cluster/helm/monitoring/grafana` (vendored `grafana` 6.52.5, appVersion 9.4.3) | pod `Pending` for 70 days: `existingClaim: grafana-2` does not exist |
| `cluster/helm/monitoring/loki` | a 32-line `values.yaml` with no `Chart.yaml` and no Application: never deployed |

Both vendored charts also hardcode their Traefik `IngressRoute` inside the chart templates
(`grafana.kub.local`, `prom.kub.local`), so the only way to change a hostname is to edit vendored
upstream code.

This design replaces all three with one owned wrapper chart per component, each taking the
project's current official chart as a pinned dependency, plus its own `IngressRoute`. Metrics get
90 days of history, logs get 30, both on the server `nas`. Nothing else in the namespace changes:
`mktxp`, `pihole-exporter` and `prometheus-smartctl` stay exactly as they are and keep being
scraped.

## 2. Goals, non-goals, anti-goals

**Goals**

1. A working monitoring stack after a fresh ArgoCD sync, with no pre-existing volumes.
2. Metrics (Prometheus), dashboards (Grafana) and logs (Loki) reachable from the local network.
3. Four independently upgradable, independently removable charts.
4. Every pinned version in git: chart versions in `Chart.yaml`, image repository and tag in values.
5. 90-day metric retention, 30-day log retention, both bounded by disk size as well as time.

**Non-goals**

- Alerting. The operator will manage alerts inside Grafana later; Discord delivery is a separate
  feature. No Alertmanager, no rule files, no contact points in this change.
- Dashboards as code. They are created through Grafana's HTTP API (operator-supplied token) or by
  hand, and live in Grafana's own database. Datasources stay provisioned from values.
- Real dashboards at all in this change: a fresh install comes up with two datasources and an
  empty UI, which is the accepted end state.
- Tempo/traces, Pyroscope, Mimir, remote-write to anywhere.
- Backup of the TSDB or of Grafana's database.
- Converting the ten existing pattern-B apps (see §3.3).

**Anti-goals** (a plan that does any of these has misread the design)

- A `networking.k8s.io/v1` Ingress anywhere.
- Installing the Prometheus Operator, its CRDs, `ServiceMonitor`/`PodMonitor`/`PrometheusRule`.
- Alertmanager, MinIO, Loki canary, Loki gateway (nginx), Loki memcached caches, Grafana sidecars.
- Any `latest` image tag, any dashboards or alert rules committed to git.

## 3. Constraints

### 3.1 From the operator

- External charts are **always** a dependency of a local chart, chart version pinned.
- Values always pin the container **repository and tag**.
- `IngressRoute` is always used.
- Internal UI hostnames are `*.kub.local`, `web` entry point, no TLS.
- Everything observability-related sits on `nas`, and its volumes stay off
  `/mnt/ultrafaststorage` — that NVMe holds the qbittorrent download export.
- Modularity wins: one chart and one ArgoCD Application per component (approach B, §4).
- Fluent Bit, not Alloy, as the log shipper: CNCF tooling, smaller footprint.
- 90 days of metrics, 30 days of logs.

### 3.2 From upstream, as of 2026-11-12

| Component | Chart | Version | appVersion | Repository |
|---|---|---|---|---|
| Prometheus | `prometheus` | 29.47.2 | v3.16.0 | `prometheus-community` (Helm repo or `oci://ghcr.io/prometheus-community/charts`) |
| Grafana | `grafana` | 14.4.1 | 13.3.1 | `grafana-community` |
| Loki | `loki` | 21.2.1 | 3.7.9 | `grafana-community` |
| Fluent Bit | `fluent-bit` | 0.58.3 | 5.1.3 | `fluent` |

The Grafana chart was deprecated in `grafana/helm-charts` on 2026-01-30 and the Loki chart on
2026-03-16; both moved to `grafana-community/helm-charts`, which is what the Loki documentation now
points at. The old repositories still exist and the Loki one is now maintained for Grafana
Enterprise Logs only. **Anyone reading a stale reference to `grafana/helm-charts` and bumping
`targetRevision` there will silently install nothing.** This is the correction to W6.

Prometheus is not replaced by Mimir. Mimir is a long-term store that Prometheus remote-writes
into; its own documentation describes it as horizontally scalable and not designed for
single-binary use. `mimir-distributed` 6.x additionally requires Kubernetes ≥ 1.32 (this cluster
is 1.30.1) and enables Kafka and MinIO by default. Not used here.

### 3.3 From the repo

- YAML 2 spaces, lowercase keys. `cluster/argocd/**` is CRLF; `cluster/helm/**` is LF.
- Ingress is Traefik CRDs only (`traefik.containo.us/v1alpha1` today — the `traefik.io` rename is
  W5's job; the wrappers take the group from a value so either order works).
- Every workload pinned with `nodeSelector: kubernetes.io/hostname: <node>`.
- Every container sets a memory request and limit; CPU requests stay tiny.
- Stateful charts use `strategy: Recreate`.
- Sync policy written into git (spec §8.4), not toggled in the UI.
- Secrets are never committed. Templates expect them to exist.

The operator's rule "external charts are always a dependency of a local chart" retires pattern B
(values-only directory, chart resolved by ArgoCD). Ten apps use it today — `bazarr`,
`bazarr-anime`, `calibre`, `filebrowser`, `postgres`, `pgadmin`, `renovate`, `local-path`,
`books-media`, `nfs-audio`. They are **out of scope**; converting them is a separate sweep and
this change leaves them untouched.

### 3.4 From the cluster (measured 2026-11-12)

- `nas`: 12 vCPU, 64 GiB RAM, ~456 GB ephemeral disk, K3s `v1.30.1+k3s1`, Ubuntu 22.04, arch
  `amd64`, worker node. ~7 GiB of RAM in use — the least loaded of the three x86 machines.
- `nuc0`: 16 vCPU, 64 GiB RAM, ~890 GB, ~28 GiB of RAM in use.
- `local-path` on `nas` provisions under **`/mnt/ssd0`**, read from the live `local-path-config`
  ConfigMap in the `default` namespace. That is the NAS's dedicated SSD. Fourteen `local-path`
  claims exist cluster-wide and together hold ~10 GiB, one of them 8 GiB (`discourse` redis).
- `/mnt/ultrafaststorage` is a different NAS export, and it is what `nfs-qbittorrent-download`
  exposes (chart values, `path: /mnt/ultrafaststorage/nfs`). **Not used by this design.**
- Default StorageClass `local-path`; ten `nfs-*` classes exist and the rest are irrelevant here.
- `monitoring` namespace exists. Running: `mktxp`, `pihole-exporter`, `prometheus-smartctl`
  (DaemonSet, 4/4), `prometheus-server` on `nuc0`, `prometheus-node-exporter` (DaemonSet, 4/4),
  `prometheus-kube-state-metrics` on `streaming`, `grafana` (Pending).
- Exporters publish `prometheus.io/scrape`, `prometheus.io/port` annotations; nothing in the
  cluster uses `ServiceMonitor` CRDs, and the CRDs are not installed.

## 4. Approaches considered

**A — one wrapper, one Application.** Four dependencies in one `observability` chart, one
`values.yaml`, three `IngressRoute` templates, one ArgoCD Application. One sync and one
rollback. Rejected: a render error in any subchart blocks the entire stack, and the log pipeline
(least proven piece) shares a release with metrics.

**B — one wrapper per component, four Applications.** *(chosen)* Four charts in
`cluster/helm/monitoring/`, four Applications, shared `monitoring` namespace. Each chart owns its
values and its own `IngressRoute`, so nothing is duplicated and each component can be upgraded,
rolled back or removed alone. Cost: four ArgoCD tiles and four `values.yaml` files to read.

**C — two wrappers: metrics (Prometheus + Grafana) and logs (Loki + Fluent Bit).** A middle
ground that isolates the log pipeline without splitting Grafana from Prometheus. Rejected because
the operator asked for maximum modularity.

The operator chose B.

## 5. The design

### 5.1 File layout

```
cluster/helm/monitoring/
├── prometheus/                  # owned wrapper
│   ├── Chart.yaml               # dependency: prometheus-community/prometheus 29.47.2
│   ├── values.yaml
│   └── templates/ingressroute.yaml
├── grafana/                     # owned wrapper
│   ├── Chart.yaml               # dependency: grafana-community/grafana 14.4.1
│   ├── values.yaml
│   └── templates/ingressroute.yaml
├── loki/                        # owned wrapper
│   ├── Chart.yaml               # dependency: grafana-community/loki 21.2.1
│   ├── values.yaml
│   └── templates/ingressroute.yaml
├── fluent-bit/                  # owned wrapper
│   ├── Chart.yaml               # dependency: fluent/fluent-bit 0.58.3
│   ├── values.yaml
│   └── templates/  (empty: no extra objects)
├── mktxp/                       # unchanged
├── pihole-exporter/             # unchanged
└── prometheus-smartctl/          # unchanged
```

`charts/` and `Chart.lock` are **not** committed; ArgoCD runs `helm dependency build` at sync time,
as it already does for `authentik` and `dashy`. `Chart.lock` is deliberately left out: Renovate
rewrites `Chart.yaml` on every bump but does not regenerate a lock, and `helm dependency build`
fails hard on a stale one — so the first Renovate PR would break all four syncs. Every dependency
constraint is an exact version, which is what makes the lock dispensable.

`cluster/argocd/projects/monitoring/` gains `loki.yaml` and `fluent-bit.yaml`; `grafana.yaml` and
`prometheus.yaml` keep their names and paths and lose the `syncPolicy.automated` block they
carried.

### 5.2 Charts, repositories and pins

| Wrapper | Dependency | Version | Values file pins |
|---|---|---|---|
| `prometheus` | `prometheus` | 29.47.2 | `quay.io/prometheus/prometheus`, `quay.io/prometheus-operator/prometheus-config-reloader`, `quay.io/prometheus/node-exporter`, `registry.k8s.io/kube-state-metrics/kube-state-metrics` |
| `grafana` | `grafana` | 14.4.1 | `docker.io/grafana/grafana`, `docker.io/library/busybox` (initChownData), `docker.io/curlimages/curl` (downloadDashboards) |
| `loki` | `loki` | 21.2.1 | `docker.io/grafana/loki` |
| `fluent-bit` | `fluent-bit` | 0.58.3 | `cr.fluentbit.io/fluent/fluent-bit` |

Exact tags are read from each chart's `appVersion` at implementation time and written explicitly
into the values file, so a chart bump does not move an image by accident.

Every wrapper also sets `fullnameOverride` on its dependency to the bare component name
(`prometheus`, `grafana`, `loki`, `fluent-bit`), plus `prometheus-kube-state-metrics` and
`prometheus-node-exporter` on the two Prometheus sub-charts. Without it ArgoCD's release name —
which equals the Application name — prepends itself and the Services become
`prometheus-prometheus-server` and `grafana-grafana`, breaking every datasource URL, the three
IngressRoutes and the Fluent Bit output host.

Prometheus needs one more: its server objects are named from `server.fullnameOverride`, not from
the chart-level key, and that template falls back to `<release>-prometheus-server` whenever the
release name does not contain the string `prometheus`. It is therefore pinned to
`prometheus-server` explicitly, which makes the name independent of the Application's name.
Verified by rendering every wrapper under two different release names. Sub-charts that are
switched off (`alertmanager`, `pushgateway`, Loki gateway/canary/caches, Grafana sidecars and test
framework, Fluent Bit test framework) contribute no image and no pin.

`helm dependency build` fetches exactly one tarball per wrapper, and those tarballs already vendor
their sub-charts — verified by extracting `prometheus-29.47.2` (alertmanager, kube-state-metrics,
node-exporter, pushgateway) and `loki-21.2.1` (rollout-operator). So no OCI registry is involved and
only the three Helm repositories need to be reachable from ArgoCD's repo-server. **Call I made:**
keep the upstream repositories rather than mirroring them locally.

A local rebuild is not idempotent: `helm dependency build` writes a `Chart.lock`, and a second run
with that file present fails unless the repository is registered in the local Helm config. ArgoCD
never sees the lock (it is gitignored and its checkout is fresh), so this only bites a person
working locally — `rm cluster/helm/monitoring/<component>/Chart.lock` then rebuild, or register the
repository with `helm repo add`.

### 5.3 Namespace, placement, resources

All four in `monitoring`. Every pod gets `nodeSelector: {kubernetes.io/hostname: nas}` **except**
the two DaemonSets (`prometheus-node-exporter`, `fluent-bit`), which must run on all four nodes —
`nas`, `nuc0`, `streaming`, `rbp2`.

`nas` rather than `nuc0` because this is the storage-bound workload: 50 GiB of claims belongs on the
machine that exists to hold disks, and `nas` also has four times the free RAM (~7 GiB used against
`nuc0`'s ~28 GiB). The claims land on `/mnt/ssd0`, the NAS's dedicated SSD, because `local-path`
maps that node to that path in its live config.

| Workload | Requests | Limits |
|---|---|---|
| `prometheus-server` | 100m / 512Mi | 1Gi |
| `prometheus-config-reloader` | 10m / 32Mi | 64Mi |
| `prometheus-node-exporter` (DS) | 10m / 32Mi | 64Mi |
| `prometheus-kube-state-metrics` | 10m / 64Mi | 128Mi |
| `grafana` | 50m / 192Mi | 512Mi |
| `loki` | 100m / 512Mi | 2Gi |
| `fluent-bit` (DS) | 10m / 64Mi | 128Mi |

Total requested memory ≈ 1.4 GiB against 64 GiB with ~7 GiB in use. `GOMEMLIMIT` is left at the
chart default for Loki; the limit above is three times the sample in the chart's own
`monolithic-values.yaml`, which assumes a 2 GiB floor. **Call I made:** these numbers are
deliberately conservative for a 4-node homelab — they are sized to survive a spike, not to
maximise density.

### 5.4 Storage and retention

| Volume | Claim | Size | Retention |
|---|---|---|---|
| Prometheus TSDB | `prometheus-server` | 25Gi | `90d`, hard cap `20GB` (`retentionSize`) |
| Loki chunks + index | `storage-loki-0` (StatefulSet template) | 20Gi | `720h` (30 days) via the compactor |
| Grafana database | `grafana` | 5Gi | n/a |

All three use the default `local-path` class, so they bind on `/mnt/ssd0` on `nas` because the pods
are pinned there. 50 GiB total; the ~14 existing `local-path` claims hold ~10 GiB, so the SSD needs
60 GiB free and nothing is placed on `/mnt/ultrafaststorage`.

**Unverified:** the exact size and free space of `/mnt/ssd0`. The stabilisation design (§8.1) states
that the NAS root filesystem is a dedicated SSD used only for `local-path`, that the 30 TB array
holds media, and that the NVMe is not part of that design — which is consistent with ~10 GiB of
existing claims and makes 50 GiB a comfortable fit. The read-only cluster account still cannot read
node statistics, so this is the one number in the design that rests on a document rather than a
measurement; `df -h /mnt/ssd0` on `nas` settles it. If free space is under ~60 GiB, the sizes drop
in the order Loki → Prometheus → Grafana, since two thirds of the metrics volume is headroom for the
`retentionSize` cap.

`retentionSize` is set so that a cardinality accident truncates history instead of filling the
disk — the failure mode that hurts least.

Loki's 30 days only happens if the compactor is enabled and configured; the chart's default leaves
`loki.compactor` empty, which means **nothing is ever deleted**. The values set
`retention_enabled: true` and `delete_request_store: filesystem` explicitly. This is the single
most likely thing to be wrong on a first install.

### 5.5 Ingress

Three `IngressRoute`s, one per wrapper chart, `entryPoints: [web]`, no TLS, no middleware. Per the
repo's chart contract (`docs/charts/README.md`, item 8) the **API group comes from a value** —
`traefik.apiVersion: traefik.containo.us/v1alpha1` — so W5's rename to `traefik.io/v1alpha1` is one
value change per chart and not an edit to a template. That also means this work does not have to be
scheduled around W5: whichever lands second flips three values.

| Host | Target service | Port | Notes |
|---|---|---|---|
| `grafana.kub.local` | `grafana` | 80 | |
| `prometheus.kub.local` | `prometheus-server` | 80 | |
| `loki.kub.local` | `loki` | 3100 | whole host, not only `/ui` |

Loki's UI is a real thing: Loki 3.x serves it on its HTTP port at `/ui`, and the chart gates it
behind `loki.ui.enabled`, which the values set to `true` (it adds a `ui` module to the `-target`
flag). The route cannot be limited to `/ui`: the UI is a single-page app that queries
`/loki/api/v1/*` and `/api/*` from the browser, so a `/ui`-only match loads a page that cannot
fetch anything. Routing the whole host also exposes Loki's push endpoint to the LAN, which is
consistent with Prometheus and Grafana being unauthenticated there.

Grafana's existing `grafana.kub.local` hostname and Prometheus' `prom.kub.local` are replaced —
`prometheus.kub.local` is the new name, so any bookmark to `prom.kub.local` breaks.

### 5.6 Secrets and prerequisites

One Secret, created by hand before the first sync:

```
monitoring/grafana   keys: admin-user, admin-password
```

Consumed via `admin.existingSecret: grafana` (`userKey: admin-user`,
`passwordKey: admin-password`). No other component needs one; Discord comes later.

The current Grafana chart *creates* that Secret, so deleting its Application would have ArgoCD prune
it and leave the new chart pointing at nothing (`CreateContainerConfigError`). The cutover is
therefore one operator command, run **before** the old Application is deleted: strip ArgoCD's
ownership markers from the existing Secret so it survives the prune and stays as-is.

```
kubectl -n monitoring annotate secret grafana argocd.argoproj.io/tracking-id- 
kubectl -n monitoring label   secret grafana app.kubernetes.io/instance-
```

Two removals because ArgoCD prunes by tracking annotation on newer installs and by instance label on
older ones; removing both is safe either way and idempotent. The credential never leaves the
cluster, so there is nothing to copy and no clipboard to lose. If the Secret turns out not to exist
(it was chart-created with an unmanaged name, or was deleted by hand), any password works instead —
nothing depends on the old one, and `kubectl -n monitoring create secret generic grafana
--from-literal=admin-user=admin --from-literal=admin-password=...` is the fallback.

Loki uses no object storage and no MinIO: filesystem storage on its own claim.

### 5.7 Prometheus specifics

- `alertmanager.enabled: false`, `prometheus-pushgateway.enabled: false`.
- `kube-state-metrics.enabled: true` and `prometheus-node-exporter.enabled: true` (they are
  sub-charts of the Prometheus chart and both are wanted).
- `server.retention: 90d`, `server.retentionSize: 20GB`,
  `server.persistentVolume.size: 25Gi`, `server.strategy.type: Recreate`.
- `server.nodeSelector`, `kube-state-metrics.nodeSelector` → `nas`. Node-exporter stays a
  DaemonSet on all nodes.
- **Scraping contract unchanged for everything inside the cluster:** chart version 29 ships its
  annotation-based jobs (`kubernetes-service-endpoints`, `kubernetes-pods`, `kubernetes-nodes`,
  `kubernetes-nodes-cadvisor`, `kubernetes-api-servers`) as structured defaults under
  `scrapeConfigs`. They are left at their defaults, so every `prometheus.io/scrape` annotation in
  the repo keeps working, and no ServiceMonitor layer is introduced. The dead `longhorn` static
  job disappears by construction, because the new chart has no such job.
- **Two external targets are carried over explicitly.** The edge Pis `rbp0` (192.168.1.22) and
  `rbp1` (192.168.1.38) run node-exporter, a smartctl exporter and the Docker metrics endpoint
  outside this cluster, so no annotation or endpoint discovery can ever find them; the old chart
  scraped them with static jobs. They are re-declared under `scrapeConfigs.rbp0` and
  `scrapeConfigs.rbp1` with ports 9100, 9902 and 9323. This is the only target set that is not
  discovered from Kubernetes objects (the chart also keeps its own static self-scrape, and a no-op
  pushgateway job with no targets), and it is deliberate: dropping them would silently remove host,
  disk and container metrics for the two machines that run Pi-hole and WireGuard.
- RBAC: the chart creates its own ClusterRole/ClusterRoleBinding for pod, service, endpoint,
  node and configmap discovery. Nothing extra to grant.

### 5.8 Grafana specifics

- `deploymentStrategy.type: Recreate` (own volume), `persistence.enabled: true`, `size: 5Gi`.
- `admin.existingSecret: grafana`.
- `datasources` provisioned from values: Prometheus (`http://prometheus-server.monitoring.svc.cluster.local`,
  default) and Loki (`http://loki.monitoring.svc.cluster.local:3100`).
- No `dashboardProviders`, no `dashboards`, no `dashboardsConfigMaps`, no sidecar, no
  `testFramework`. `ingress.enabled: false` — the wrapper owns the IngressRoute.

### 5.9 Loki specifics

- `deploymentMode: Monolithic`, `singleBinary.replicas: 1`, `singleBinary.kind: StatefulSet`.
- Zero replicas for `backend`, `read`, `write`, `ingester`, `querier`, `queryFrontend`,
  `queryScheduler`, `distributor`, `compactor`, `indexGateway`, `bloomGateway` (the chart defaults
  three of them to 3).
- `loki.commonConfig.replication_factor: 1`.
- `loki.schemaConfig`: single `v13` TSDB schema from `2024-04-01`, `object_store: filesystem`.
- `loki.storage.type: filesystem`; `loki.compactor` as in §5.4;
  `loki.limits_config.retention_period: 720h`.
- `loki.ui.enabled: true`.
- Off: `gateway.enabled`, `lokiCanary.enabled`, `chunksCache.enabled`, `resultsCache.enabled`,
  `test.enabled`, `monitoring.*`. This removes nginx, the canary DaemonSet and two memcached
  StatefulSets — five pods and roughly 1 GiB of defaults that a single-node install does not need.
- `singleBinary.persistence.size: 20Gi`, `enableStatefulSetAutoDeletePVC: false` (keep the data if
  the StatefulSet is ever deleted).

### 5.10 Fluent Bit specifics

DaemonSet on all four nodes, tails `/var/log/containers/*.log` through the host path the chart
already mounts, enriches with the `kubernetes` filter, and pushes to Loki with the **built-in**
`loki` output (no external Go plugin to ship).

- Input: tail only. The chart's default second input reads the `kubelet.service` systemd unit —
  removed, because K3s runs the kubelet inside the `k3s` process and that unit does not exist here.
- Filter: `kubernetes` with `Merge_Log On`, `Keep_Log Off`, `K8S-Logging.Parser On`,
  `K8S-Logging.Exclude On`.
- Output: `Name loki`, `Host loki.monitoring.svc.cluster.local`, `Port 3100`,
  `Line_Format json`, `Compress gzip`, `Retry_Limit no_limits`.
- Labels: `job=fluent-bit`, `namespace=$kubernetes['namespace_name']`,
  `container=$kubernetes['container_name']`, `node=$kubernetes['host']`. **Call I made:** the pod
  name goes into `Structured_Metadata`, not into a label. Loki's index cost is per distinct label
  combination, and one label value per pod is exactly how a small Loki falls over; structured
  metadata keeps the field queryable without indexing it.
- `testFramework.enabled: false`, `rbac.create: true` with `nodeAccess` and `eventsAccess` off.

### 5.11 What gets deleted

- `cluster/helm/monitoring/prometheus/` in full — vendored chart, `templates/`, `charts/`,
  `Chart.lock`, `README.md`. Replaced by the wrapper of the same name.
- `cluster/helm/monitoring/grafana/` in full — vendored chart, `templates/`, `dashboards/`, `ci/`,
  `README.md`, `.helmignore`. Replaced by the wrapper of the same name.
- `cluster/helm/monitoring/loki/values.yaml` — replaced by the wrapper's `values.yaml` + `Chart.yaml`.
- `.gitignore`: the negation `!cluster/helm/monitoring/grafana/templates/secret.yaml` was removed
  with this change (it pointed at a template that never held a secret), and two globs were added so
  the built `charts/` and `Chart.lock` are never committed:
  `cluster/helm/monitoring/*/charts/` and `cluster/helm/monitoring/*/Chart.lock`.

Not deleted: the stuck `prometheus-data` PVC and the `longhorn-system` namespace. They belong to
W0 and removing an Application does not touch a claim the chart never owned.

### 5.12 ArgoCD Applications

Four files, same shape:

```yaml
spec:
  destination: {server: 'https://kubernetes.default.svc', namespace: monitoring}
  syncPolicy:
    syncOptions: [CreateNamespace=true]
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/monitoring/<component>
```

No `automated` block, in any of the four. Auto-sync is the operator's switch in the ArgoCD UI, and a
PR that adds it to these files is a regression against the repo rule in AGENTS.md §7 item 1.

### 5.13 Deviation from `docs/charts/README.md`

That contract is written for charts copied from `autobrr`, where this repo owns the templates. A
wrapper owns only its `IngressRoute`; its pods are rendered by upstream, so several items cannot
apply. What does apply, and what cannot:

| Contract item | Here |
|---|---|
| 1 `appVersion` matches `image.tag` | **cannot** — one chart, four images. `appVersion` names the dependency's application; each image is pinned in values |
| 2 no floating tags | applies |
| 3 `strategy: Recreate` | applies where there is a volume: Prometheus and Grafana |
| 4 `nodeSelector` pins the pod | applies, except the two DaemonSets by design |
| 5 claim named `<fullname>-config`, 128Mi | **cannot** — claim names and sizes come from upstream (`prometheus-server`, `grafana`, `loki`) and these are data volumes in the GiB range, not config |
| 7 names through `app.fullname` | applies to the `IngressRoute` only |
| 8 IngressRoute with the API group in a value | applies |
| 9 labels on every object | applies to the `IngressRoute` only |
| 10 no tabs | applies |
| 11 `helm lint` and `helm template` pass | applies |
| 12–13 probes on distinct endpoints | **inherited, with one exception**: Prometheus, Grafana and Loki ship distinct liveness/readiness endpoints. Fluent Bit's default liveness is `/`, which is exactly what this item warns against, so the values move it to `/api/v2/health` — the same endpoint its readiness uses, because Fluent Bit has no separate one. Items 12–13 are therefore only partly satisfiable here |
| 14 memory request and limit on every container | applies, set through values (§5.3) |
| 15 hardening | **partly inherited**: upstream `securityContext`s are kept; where a chart allows dropping capabilities and the pod can still read host logs (Fluent Bit) it is set |
| 16 extra volumes under `/srv/` | does not apply — the only host paths are Fluent Bit's log directories and node-exporter's, both from upstream |

Reviewers should not flag items 1, 5, 12–13 or 16 on these four charts; the reason is here.

## 6. Risks

1. **Loki retention is inert unless the compactor is configured.** Covered in §5.4; verify by
   watching the volume size, not by trusting the value.
2. **The Grafana Secret is pruned with the old Application**, and the new chart then fails to
   start. Mitigated by §5.6: the operator strips the ownership markers before the switch. If that
   step is skipped, the failure is loud (`CreateContainerConfigError`) and the fix is the same
   command.
3. **`helm dependency build` needs network at sync time** to fetch one tarball per wrapper from
   `prometheus-community` / `grafana-community` / `fluent` over HTTPS. It does **not** need
   `ghcr.io`: the Prometheus and Loki chart tarballs vendor their own sub-charts
   (`alertmanager`, `kube-state-metrics`, `prometheus-node-exporter`, `prometheus-pushgateway`,
   `rollout-operator`), verified by extracting 29.47.2 and 21.2.1. If egress is blocked the four
   syncs fail at render time, which is loud but total.
4. **`prom.kub.local` disappears.** Deliberate rename to `prometheus.kub.local`.
5. **Label cardinality in Loki.** Mitigated by the structured-metadata call in §5.10, but a noisy
   namespace can still grow the index; the 20 GiB claim is the backstop.
6. **Chart versions are the current ones on 2026-11-12.** Renovate will bump them, and the values
   are written against these majors — a Loki major bump has already renamed keys once
   (`SingleBinary` → `Monolithic`). Because no lock file is committed, a Renovate bump is a
   `Chart.yaml`-only change and the next sync re-resolves; the risk is a values key disappearing,
   not a failed render.

## 7. Validation

No cluster access needed for the first three:

1. `helm dependency build` then `helm template` for each of the four wrappers, with its own
   `values.yaml`, and `helm lint` on each. (The repo's bulk loop skips charts that need a
   dependency build, so these are run explicitly.)
2. The repo's CRLF-safe reference checks still pass: every `path:` in
   `cluster/argocd/projects` resolves to a directory, and every `$values/` reference to a file.
3. `helm template` output contains exactly three `IngressRoute` objects, no `Ingress`, no
   `latest` tag, and no `Chart.lock` anywhere in `cluster/helm/monitoring`.

Operator pre-flight, before the merge that switches the Applications over:

4. Nothing outside the repo still points at the old hostname:
   `kubectl get ingressroute,cm,secret -A -o yaml | grep -n 'prom\.kub'`. The read-only MCP account
   cannot read Secrets, so this one command is the operator's. The repo itself is clean — only the
   two vendored IngressRoutes use it — but `dashy-conf` and other cluster-side objects are not
   greppable from git.

On the cluster, after the first sync:

5. Four Applications `Synced`/`Healthy`; `kubectl -n monitoring get pods` shows `prometheus-server`,
   `prometheus-kube-state-metrics`, `grafana`, `loki` on `nas`, and two DaemonSets at 4/4.
6. Three PVCs `Bound`, all on `nas`, resolving to `/mnt/ssd0`, and none on `/mnt/ultrafaststorage`.
7. `grafana.kub.local` loads, both datasources green, a Prometheus query and a Loki query both
   return data, and a log line from a `monitoring` pod appears in Grafana Explore.
8. `prometheus.kub.local` shows every present target `UP` and the flags carry
   `storage.tsdb.retention.time=90d`.
9. `loki.kub.local/ui` responds.
10. Running image tags equal the pins in the values files.
11. Prometheus' `/targets` page lists the two `rbp0` and `rbp1` static jobs as `UP` on ports 9100,
    9902 and 9323. This is the one part of the scrape configuration that no render can prove.
12. A **plain-text** log line (not a JSON one) appears in Grafana Explore with its message body
    intact. `Merge_Log On` with `Keep_Log Off` keeps the raw `log` field when the merge fails in most
    builds, but not provably all, and the general "a log line appears" check above can be satisfied
    by JSON-only traffic.

## 8. Acceptance

The stack is done when §7 passes and this holds after deleting and re-syncing all four
Applications from scratch: no manual step beyond the Grafana Secret of §5.6, no `Pending` pod, and
no component reaching for a volume, image or hostname that git does not declare.

## 9. Left to the builder

- Exact image tags, read from each chart's `appVersion`.
- Column widths and grouping of the values files; whether each wrapper sorts its keys or mirrors
  the upstream chart's order.
- Whether `prometheus-smartctl`, `mktxp` and `pihole-exporter` are ever migrated to wrapper charts.
  Out of scope here.
- The `traefik.io/v1alpha1` rename, W5's job.

## 10. Documentation this change rewrites

Approved by the operator. Four edits, all in the same branch as the charts:

**`docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md`**

1. Replace W6 with a pointer to this document, plus the two items that survive: remove the vendored
   Prometheus and Grafana charts, and the fact that the exporters keep working unchanged. Delete
   the rest of W6's list (Alertmanager, ServiceMonitors, dashboards as files, the alert set) and its
   acceptance, so nobody measures this stack against a superseded bar.
2. §8.1 placement map: **no change needed.** It already lists the Prometheus TSDB and Loki under
   `nas`, already says the NAS root filesystem is a dedicated SSD used only for `local-path`, and
   already excludes the NVMe. My earlier `nuc0` placement in this design was the deviation; the
   operator's correction brings the two documents back into agreement.
3. §8.3 rule 1: pattern B is retired. External charts are a dependency of a local chart with an
   exact `version:`, no lock file, images pinned in values. Rule 3 ("no vendored charts") shrinks
   accordingly: `postiz`, `discourse`, `authentik`, `dashy` and `ashuvidz-website` still migrate,
   `prometheus` and `grafana` no longer need to.

**`AGENTS.md`**

4. §2/§4: the pattern list gains the wrapper-with-dependencies shape as the way external charts are
   consumed, and the placement map records observability on `nas`. §7 known issues 14 (`minio-nfs`,
   `loki` values-only directory) and 16 (vendored Prometheus/Grafana drifting from their images) are
   resolved for these charts and get marked as such; 15 and the Traefik items are untouched.
