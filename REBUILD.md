# Homelab rebuild plan

Written 2026-10-24. Status: agreed with the operator, not started.
Nothing in this plan has been executed.

## Decisions already made

- **Source of truth:** git + ArgoCD. The cluster is authoritative only for the items listed under "Deliberate exceptions".
- **Storage:** `local-path` for config volumes, NFS provisioner classes for media/data. Longhorn is abandoned; no recovery attempt.
- **Data:** all backups were corrupt and restic is not running. Loss is accepted. No irreplaceable data. Backups are designed after the platform upgrades (see Resolved questions).
- **Observability:** `kube-prometheus-stack` + Loki + Alloy, 90-day retention, alerts to Discord.
- **PostgreSQL:** plain StatefulSet on the official `postgres` image, replacing Bitnami.
- **Traefik:** migrate to the official chart, installed manually (not ArgoCD), following the K3s migration docs. Values live in git. Stays manual; not adopted into ArgoCD.
- **K3s:** upgrade to the latest stable through the System Upgrade Controller, one minor at a time.
- **Chart policy:** official charts first, mature community charts second. Exit Bitnami (licensing) and Docker Hub (move to ghcr where the project publishes there). The Bitnami exit happens in this rebuild, not later: `ashuvidz-website` → official `nginx`, `postiz` → plain postgres StatefulSet + official `redis`.
- **Delete rather than rebuild:** `readarr` (abandoned upstream) and `maintainerr-anime` (duplicate of `maintainerr`).
- **Backups:** designed after the platform upgrades.

## Verified ground truth (2026-10-23/24)

Cluster: K3s `v1.30.1+k3s1`, nodes `nas` (agent), `nuc0`, `rbp2`, `streaming` (servers), all Ready.
Stable channel: `v1.36.5+k3s1` (`v1.37.1` published but not promoted). No `lts` channel exists in the update API.
SUC plans `server-plan` and `agent-plan` exist in `system-upgrade`, both pinned to `v1.30.1+k3s1` with `channel: stable`.

Failures:

- 17 pods Pending, 1 ContainerCreating. All Pending pods are blocked by deleted PVCs, not by capacity (nodes at 22–54% memory).
- 16 apps blocked on missing config PVCs: bazarr, bazarr-anime, lidarr, readarr, maintainerr, maintainerr-anime, tautulli, kavita, grafana, koillection, discourse, news-freshrss, postiz (uploads), minecraft, monitorss (mongo), postgres (database).
- 2 pods CrashLoopBackOff: `ghostfolio` (~13.9k restarts) and `monitorss-bot` (~7.8k), both DB-related.
- Longhorn leftovers: no pods in `longhorn-system`; StorageClasses `longhorn` and `longhorn-static` gone; ~25 PVs `Released`; 11 PVCs still `Bound` to `driver.longhorn.io` PVs backing *running* pods (calibre, filebrowser ×2, firefly-config, linkstack, n8n, prometheus-data, syncthing-conf, thelounge, pgadmin, postiz-redis). These survive only until the next pod restart or node reboot.

Traefik:

- Running: `rancher/mirrored-library-traefik:2.9.4`, Helm release `traefik` rev 5, chart 20.3.1 (June 2023).
- K3s `HelmChart` already targets chart 25.0.3 (Traefik 2.11.10) but was never applied.
- Live `HelmChartConfig` says `persistence.existingClaim: traefik`; the DaemonSet actually mounts `pvc-traefik-nfs` (10Mi, NFS). `cluster/infrastructure/traefik/traefik-config.yaml` says `existingClaim: "traefik-pvc"` and is misindented.
- 58 IngressRoutes live; 106 repo files use `traefik.containo.us/v1alpha1`. Both API groups have CRDs installed since Feb 2024.
- K3s 1.32 ships Traefik 2.11.10; K3s 1.33 ships Traefik 3.x. Target chart today: 42.1.3 / Traefik v3.7.16.

GitOps:

- 57 Applications including `argocd-root`. 31 apps have `selfHeal=true` + `prune=true`, so manual `helm upgrade` on them is reverted.
- Broken references: `plex-media` (path `nfs/plex-media` vs dir `plexmedia`), `nfs-audio` (values path `nfs/audio`), `nfs-qbittorrent-config` (no Chart.yaml, syncs nothing).
- Helm release history is stale: 55 releases, most last touched in 2023–2024, ArgoCD owns the workloads.
- Empty namespaces with no workloads: nextcloud, synapse, inventree, inventree-lego, minio, orbital-sync, postfix, test, longhorn-system.
- Ghost Helm releases with no workloads: qbit-manage, syncrr, radarr-hd, sonarr-hd.
- Repo orphans: `cluster/helm/nfs/minio-nfs` (no Application), empty `cluster/helm/localpath/`.
- Already present and reusable: `cluster/helm/monitoring/loki/values.yaml` (single-binary, filesystem, 10Gi, Jan 2025), `cluster/helm/argocd/values.yaml` (Aug 2026).

## Deliberate exceptions (outside ArgoCD)

1. ArgoCD itself: Helm release `argocd` 10.4.1 (v3.5.2), values in `cluster/helm/argocd/values.yaml`.
2. K3s node configuration and the SUC plans (node-level, not Kubernetes objects).
3. Traefik: official chart, manual install; values in git.
4. NFS exports and host prep: Ansible.
5. Secrets: never in git; created out of band.

Anything else that disagrees with git is drift to fix.

## Phase 1 — finish the media stack

1. Fix the three Applications:
   - `cluster/argocd/projects/nfs/plex-media.yaml`: path → `cluster/helm/nfs/plexmedia`. Do **not** delete this app; it is the NFS provisioner for the media share.
   - `cluster/argocd/projects/nfs/nfs-audio.yaml`: values path → `cluster/helm/nfs/nfs-audio/values.yaml`.
   - `cluster/argocd/projects/nfs/nfs-qbittorrent-config.yaml`: convert to the multi-source pattern used by the sibling NFS apps.
2. Fix `cluster/helm/media-stack/qbittorrent/templates/pvc.yaml`: `storageClassName: nfs-ultrafaststorage` does not exist. Either create that class (its HelmChart exists in `default` but produced no StorageClass — investigate the failed install) or point the PVC at an existing class. Do not do both.
3. Recreate the missing config PVCs on `local-path` for: bazarr, bazarr-anime, lidarr, maintainerr, tautulli, kavita. Fresh config; `recyclarr` repopulates quality profiles.
4. Fix the Sonarr exportarr URL in `cluster/helm/media-stack/sonarr/templates/deployment.yaml`: port 7878 → 8989.
5. Investigate `maintainerr` Degraded.

Acceptance: zero Pending pods in `media-stack`, `maintainerr` Healthy, every media Application Synced.

## Phase 2 — observability

1. Remove the `prometheus` and `grafana` Applications and their vendored 2024 charts (pattern C). Keep the exporters: mktxp, pihole-exporter, prometheus-smartctl.
2. Add kube-prometheus-stack 92.11.0 as pattern B (remote chart + values in repo), namespace `monitoring`.
   - Prometheus TSDB on `local-path`, retention 90d.
   - ServiceMonitors for existing exporters; drop the longhorn scrape job.
   - Alertmanager receiver: Discord webhook from a Secret.
3. Add Loki 7.5.0 (single binary) reusing `cluster/helm/monitoring/loki/values.yaml`; filesystem on `local-path`, 90d retention.
4. Add Alloy 1.14.3 as a DaemonSet for pod logs.
5. Grafana dashboards as ConfigMaps in git; Grafana/Prometheus/Alertmanager behind the existing Traefik TLS pattern (still `traefik.containo.us` until Phase 3; those files are part of the rename list).
6. Pre-check free disk on `streaming`: 90d metrics + logs + migrated config volumes all land there.

Acceptance: all scrape targets up, Traefik metrics present, one test alert in Discord, dashboards survive a Grafana pod restart.

## Phase 3 — Traefik, four steps

Pre-work: snapshot all live routes, middlewares, TLSOptions and TLSStores (58 routes) because the cluster copy is the only complete one; confirm SSH to all nodes; confirm `pvc-traefik-nfs` holds `acme.json`.

1. **Externalize while still v2.** Add `disable: [traefik]` to `/etc/rancher/k3s/config.yaml` on all four nodes, restart k3s, install the official chart pinned to 2.11 with values mirroring the live config and `existingClaim: pvc-traefik-nfs`. 2.11 serves both API groups, so routing is never blind.
2. **Rename to `traefik.io/v1alpha1`:** 106 repo files (preserve CRLF under `cluster/argocd/**`) and the live objects. Verify every router.
3. **Upgrade to chart 42.1.3 / v3.7.16.** v3 no longer serves `traefik.containo.us`; nothing should use it by now.
4. Delete the `containo.us` CRDs and the stale repo references.

Rollback: keep the pre-rename commit. Reverting v3 → 2.11 is safe (2.11 reads `traefik.io`). Re-enabling the bundled 2.9.4 requires renaming back to `containo.us` first.

Acceptance: all routers healthy, the same ACME certificate served, dashboard and metrics reachable, no `containo.us` references left.

## Phase 4 — K3s 1.30 → 1.36.5

- Confirm `disable: traefik` is in the node config file on every node, not a CLI flag.
- Drive `server-plan` then `agent-plan` one minor at a time: 1.31 → 1.32 → 1.33 → 1.34 → 1.35 → 1.36.5. Set an explicit version per step; never let `channel: stable` jump the whole way.
- Never upgrade two servers at once (etcd quorum is 2 of 3). Order: nuc0, rbp2, streaming; `nas` last.
- Verify after each step: nodes Ready, no crash loops, ArgoCD Synced, ingress serving, etcd healthy.

Acceptance: all four nodes on the target version, no workload churn, ingress unaffected.

## Phase 5 — deletions and longhorn cleanup

Delete after confirming nothing references them:

- Namespaces: nextcloud, synapse, inventree, inventree-lego, minio, orbital-sync, postfix, test, longhorn-system.
- Ghost Helm releases: qbit-manage, syncrr, radarr-hd, sonarr-hd.
- Repo: `cluster/helm/nfs/minio-nfs`, `cluster/helm/localpath/`, the empty `TODO.MD`.
- Longhorn artifacts: ~25 Released PVs, `kubectl/ingressRoute/longhorn.yaml`, `cluster/infrastructure/storageclass/lh-*.yaml`, the longhorn scrape job (goes with Phase 2).
- Never delete: `plex-media` (NFS provisioner), `gpu-operator`, `node-feature-discovery`, `system-upgrade`.

Notes: deleting an Application file with root `prune` removes the Application object but leaves its resources unless the Application has the resources finalizer — clean namespaces explicitly. The 11 remaining longhorn-backed PVCs must be replaced with `local-path` volumes when each app is next restarted; until then, a restart fails on mount.

Delete outright rather than rebuild: `readarr` (abandoned upstream) and `maintainerr-anime` (duplicate of `maintainerr`). Remove their Applications and charts with the rest of this phase.

## Phase 6 — chart structure migration

- PostgreSQL: plain StatefulSet on `docker.io/library/postgres`, config in git; migrate / recreate the database, since the old data is gone.
- Bitnami references (6 files): pgadmin chart stays (Helm chart, not Bitnami), ashuvidz-website nginx, postiz bundled postgresql/redis. Replace with official `nginx`, the new postgres StatefulSet, and official `redis` in this rebuild.
- Docker Hub → ghcr: 14 files reference LinuxServer images; verify per image that a ghcr tag exists (LinuxServer publishes lscr.io and ghcr), else keep Docker Hub. Update Renovate custom managers accordingly.
- Stop vendoring upstream charts; migrate `postiz` (and anything else still vendored) to pattern B at the next touch.
- Keep chart versions in Application `targetRevision`, not in values.

## Risks

- **Traefik cutover:** brief ingress downtime when the bundled addon is disabled. Reusing the ACME PVC avoids re-issuing certificates (Let's Encrypt duplicate-cert limit is 5/week). Do it from the LAN with SSH available.
- **K3s:** six reboot rounds; one server at a time or lose etcd quorum.
- **selfHeal:** 31 apps revert manual Helm changes. Rebuilds go through git.
- **local-path:** node-bound, no redundancy; with backups not yet designed, disk loss on `streaming` is data loss by design.
- **Stuck longhorn PVCs:** any pod restart before migration fails with a mount error, not a clean crash.
- **Root prune:** deleting Application files does not guarantee resource cleanup.

## Resolved questions

The operator answered all five on 2026-10-24. None are open.

1. Delete `readarr` and `maintainerr-anime`.
2. qbittorrent storage class: decide during Phase 1, after checking the failed `nfs-ultrafaststorage` HelmChart.
3. Bitnami exit immediately for `ashuvidz-website` and `postiz`.
4. Traefik stays manual, not adopted into ArgoCD.
5. Backups revisited after the platform upgrades.
