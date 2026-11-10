# Audit — observability and storage

Read-only audit of the homelab repo plus live cluster state, 2026-11-10.
Repo root: `/home/vsynthese/.local/share/opencode/worktree/117be0/agent1`.
Cluster reads via the read-only `opencode-mcp` ServiceAccount.

## 1. Observability

### 1.1 Inventory and pattern

| Chart | Pattern | Upstream version | appVersion | Notes |
|---|---|---|---|---|
| `cluster/helm/monitoring/prometheus` | C (vendored) | `prometheus` 14.8.0 | 2.26.0 | `Chart.yaml` |
| `cluster/helm/monitoring/grafana` | C (vendored) | `grafana` 6.52.5 | 9.4.3 | `Chart.yaml` |
| `cluster/helm/monitoring/mktxp` | A (local) | 0.1.0 | "1.16.0" (meaningless) | scaffold chart |
| `cluster/helm/monitoring/pihole-exporter` | A (local) | 0.1.0 | "1.16.0" (meaningless) | scaffold chart |
| `cluster/helm/monitoring/prometheus-smartctl` | A (local) | 0.1.0 | "1.16.0" (meaningless) | scaffold chart |
| `cluster/helm/monitoring/loki` | — | — | — | **only `values.yaml`; no `Chart.yaml`, no Application** |

All five real charts render cleanly with `helm template`, zero errors.

### 1.2 Prometheus

- Dependency drift: `Chart.yaml` asks for `kube-state-metrics 7.*`; `Chart.lock` pins 7.0.0
  (generated 2025-12-09) but the vendored `charts/kube-state-metrics/Chart.yaml` is **3.5.2**.
  The lock and the vendored chart disagree; `helm dependency build` would swap it.
- Images pinned: `quay.io/prometheus/prometheus:v3.11.3` (values.yaml:653), alertmanager
  `v0.29.0` (:51), node-exporter `v1.11.1` (:478), pushgateway `v1.11.2` (:1101),
  configmap-reload `v0.15.0` (:379, :417).
- **Alerting: effectively none.** `alertmanager.enabled: false` (:33), `pushgateway.enabled: false`
  (:1086), `server.alertmanagers: []` (:945), `alerting_rules.yml: {}` (:1342), `alerts: {}` (:1355).
  The alertmanager default receiver has every integration commented out (:1324-1328).
- **Storage:** `server.persistentVolume.enabled: true` with `existingClaim: prometheus-data`
  (:898) and `storageClass` commented out (:915) → the chart creates no PVC and expects a
  pre-existing claim. That claim is `monitoring/prometheus-data`, 150Gi, `longhorn-static`,
  now `Terminating`, while `prometheus-server` still runs on it.
- Retention `365d` (:1081) plus `storage.tsdb.retention.size=30GB` (:695) — the size cap
  truncates at 30 GB regardless of the day setting.
- `remoteWrite: []` / `remoteRead: []` (:725, :728) → **no remote write, no metrics backup path**.
- Scraping is annotation-based only: `kubernetes-pods` honours `prometheus.io/scrape=true`
  (:1685-1706), plus `kubernetes-service-endpoints` (:1541). No ServiceMonitor or PodMonitor
  is used (`grafana.serviceMonitor.enabled: false`, :187).
- Static jobs still reference deleted infrastructure:
  - `longhorn` → `longhorn-backend.longhorn-system.svc.cluster.local:9500` (:1377-1380).
    Longhorn has zero pods; the target is dead.
  - `rbp0` / `rbp1` → `192.168.1.22` / `192.168.1.38`, ports 9100/9902/9323 (:1382-1400).
    Correct, but hardcoded rather than discovered.

### 1.3 Grafana

- `grafana/grafana-oss:13.0.1` (values.yaml:88), sidecar `1.30.11` (:776), image renderer
  `v1.4.1` (:103).
- Datasources are provisioned from values into a ConfigMap, pointing at
  `http://prometheus-server.monitoring.svc.cluster.local` (:536-544). The service exists.
- **Dashboards: none.** `dashboardProviders: {}` (:642), all `dashboards` entries commented
  (:661-680), `dashboardsConfigMaps: {}` (:690), `sidecar.dashboards.enabled: false` (:833).
  A fresh install comes up with a datasource and an empty UI.
- `existingClaim: grafana-2` (:331) → chart creates no PVC. `grafana-2` is gone, so the pod
  has been `Pending` for 68 days.

### 1.4 Exporters

- `mktxp` image `tag: latest` (values.yaml:11) — floating. Init container `busybox:1.35`
  (deployment.yaml:38). Reads Secret `mktxp-conf` (deployment.yaml:33).
- `pihole-exporter` `v1.2.0`, but `containerPort: 9902` in `templates/deployment.yaml:52`
  disagrees with `PORT=9617` (:48-49) and the scrape annotation (values.yaml:29). Scraping
  works only via the pod annotation. `PIHOLE_HOSTNAME=192.168.1.22` is hardcoded (:40).
  Reads Secret `pihole-exporter` key `key` (:44-45).
- Neither `mktxp` nor `pihole-exporter` ships a **Service**: both template directories hold
  only `_helpers.tpl` and `deployment.yaml`.
- `prometheus-smartctl` `v2.5.0` as a DaemonSet on all four nodes (live: 4/4 ready, 3y101d).

### 1.5 Loki and logs

`cluster/helm/monitoring/loki/` holds a single 32-line `values.yaml` (single binary, filesystem,
10Gi). There is **no `Chart.yaml`** and **no ArgoCD Application anywhere references `loki`**.
No log shipper exists in the repo at all — no promtail, alloy, fluent-bit or Grafana Agent,
in `cluster/helm` or `cluster/argocd`. There is therefore no log pipeline: Loki is a dead
directory, and every `pods_log` call is the only way to read logs today.

## 2. Storage

### 2.1 Longhorn: removed, not cleaned up

- `longhorn-system` namespace exists with **zero pods and zero services**.
- No `longhorn`, `longhorn-static` or `lh-performance` StorageClass exists in the cluster.
- Live StorageClasses: `local-path` (default) plus ten `nfs-*` classes.
- `cluster/infrastructure/storageclass/lh-performance.yaml` and `lh-performance-no-backup.yaml`
  **both declare `metadata.name: lh-performance`** — a duplicate name, applied by hand because
  `cluster/infrastructure/**` is outside GitOps. Both say `driver.longhorn.io`, `Retain`,
  `Immediate`, 3 replicas, `diskSelector: ssd`.
- No `RecurringJob` CR, no Longhorn backup target, no Longhorn `HelmChartConfig` exists anywhere
  in the repo. The only Longhorn remnant with content is
  `kubectl/ingressRoute/longhorn.yaml` (UI at `longhorn.kub.local` → a service that no longer
  exists).

### 2.2 What the data loss took

11 PVCs and 36 PVs are stuck `Terminating` with `driver.longhorn.io` finalizers and no CSI
driver left to clear them. The 150Gi `prometheus-data` and 20Gi claims
(`databases-postgres-backup`, `discourse2`, `koillection-uploads`, `monitorss-mongo`,
`overseerr-config`) are the largest.

Three classes of damage:

1. **Gone and already broken** — the PVC object itself is deleted, so the pod cannot schedule:
   bazarr, bazarr-anime, kavita, lidarr, maintainerr, maintainerr-anime, readarr, tautulli,
   grafana, koillection, discourse, news-freshrss, postiz-uploads, minecraft, monitorss-mongo,
   postgres, databases-postgres-backup.
2. **Running on borrowed time** — the PVC is `Terminating` but the pod still holds the mount:
   filebrowser (`filebrowser-config`, `filebrowser-db-2`), calibre (`calibre-config`),
   firefly (`firefly-config-2`), linkstack, n8n (`n8n-2`), syncthing (`syncthing-conf`),
   thelounge (`thelounge-volume`), pgadmin (`pgadmin-pgadmin4`), prometheus (`prometheus-data`),
   postiz redis. Any restart ends them.
3. **Survived intact** — every `local-path` claim (19 of them, 128Mi config volumes) and every
   NFS claim (the 10Mi `pvc-*` bootstrap claims plus `discourse` redis, 8Gi, 68d).

### 2.3 NFS provisioners

Nine NFS provisioner charts plus `plex-media`, `stash-nfs`, `traefik-nfs`. Each creates its own
StorageClass. Two live breakages:

- `default/nfs-ssd-nfs-subdir-external-provisioner` has been `ContainerCreating` for 68 days:
  its own bootstrap volume fails `MountVolume.SetUp` (exit status 32). The `nfs-ssd` class is
  therefore unable to provision.
- `media-stack/qbittorrent-download-nfs` is `Pending` for 2y144d on class
  `nfs-ultrafaststorage`, which does not exist. The provisioner chart
  `nfs-qbittorrent-download` creates a class under a different name.
- `media-stack/plex-media-nfs` failed provisioning with
  `storageclass.storage.k8s.io "nfs-hdd" not found`.
- `media-stack` NFS/chart config also exposes `nfs-plex` and `plex-media` as separate classes
  from separate charts pointing at the same NAS export `/mnt/storage/nfs/plexmedia` — two
  names for one thing.

## 3. Backups

| Path | Where | Scope | State |
|---|---|---|---|
| Host restic | `ansible/playbooks/backup-setup.yaml` → `s3:s3.amazonaws.com/ashudev-homelab-<host>-backup` | runners rbp0/rbp1 | **AWS S3**, credentials in `restic_env.yaml` (gitignored). Template only defines `rbp0` and `rbp1` — the k3s hosts have no entry. |
| K3s datastore | `scripts/backup-scripts/k3s/backup.sh` | `sqlite3 .../server/db/state.db` dump, then restic | **Wrong for this cluster.** A 3-server K3s runs on etcd; there is no `state.db` to dump. The only control-plane backup script cannot back up the control plane. |
| NAS | `scripts/backup-scripts/nas/backup.sh` | unclear | see Ansible audit |
| PostgreSQL | `cluster/helm/databases/postgres/values.yaml:39-41` | `pg_dumpall` CronJob, 30 dumps, into `databases-postgres-backup` | The destination claim is one of the dead Longhorn volumes. |
| Syncthing config | `cluster/helm/syncthing/templates/deployment-backup.yaml` | restic CronJob `restic/restic:0.18.1`, Secret `syncthing-backup` | Runs. Only covers syncthing's own config. |
| Metrics | — | nothing | No remote write; TSDB lives on a `Terminating` claim. |
| Longhorn volumes | — | nothing defined in git | Gone with Longhorn. |

Net: no working backup of the cluster control plane, no backup of PVC data, and the only
offsite storage is an AWS S3 bucket. This matches `REBUILD.md` line 10 — "all backups were
corrupt and restic is not running".

## 4. Drift summary for this area

1. `prometheus` and `grafana` are vendored 2024 charts pinned to 2022-era appVersions
   (Prometheus chart 14.8.0 / appVersion 2.26.0, Grafana chart 6.52.5 / appVersion 9.4.3),
   both running much newer images out of band — chart and workload disagree.
2. Both rely on `existingClaim` names that no longer exist, so a fresh sync leaves them Pending.
3. `loki/` is a values file with no chart and no Application.
4. Two StorageClass manifests share the name `lh-performance`, and neither is applied.
5. `mktxp` floats on `:latest`; `pihole-exporter` declares the wrong container port.
6. Prometheus still scrapes `longhorn-backend`, which has no pods.
7. No alerting exists anywhere, so nothing has ever paged on any of the above.
