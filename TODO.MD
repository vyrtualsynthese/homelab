# TODO — rebuild tracker

One section per session (3–4 h). Tick the boxes, then log planned vs actual in the drift table.
Rationale, evidence and rollback notes live in `REBUILD.md`; this file is only the checklist.

Rules for every session:

- Start by reading the last line of the drift log. 10 minutes of state beats an hour of re-investigation.
- Last 60 minutes: no destructive step (namespace deletion, `disable: [traefik]`, CRD removal).
- Additive commits only while `selfHeal` + `prune` covers 31 apps: new path, sync, verify, then delete the old path in a separate commit.
- Never `kubectl apply` a GitOps-managed resource. Drift is fixed in git.
- Close the session with: `kubectl get apps -n argocd` all Synced, `git status` clean enough to describe, one line in the drift log.

**Next session: S1** · Done: 0/15 · Planned total: ~42 h

---

## S1 — Layout decision + pilot (3 h)

- [ ] Inspect the chart behind `cluster/helm/media-stack/bazarr` (remote `vyrtualsynthese.github.io/selfhosted-helmcharts`) — is it generic enough for media-stack apps?
- [ ] Decide: reuse that chart, or build one library chart. Write the decision as 3 lines in `REBUILD.md`.
- [ ] Choose pilot: `sonarr` (exercises config PVC, exportarr sidecar, ingress, nodeSelector).
- [ ] Rewrite sonarr as pattern B: values-only dir + multi-source Application.
- [ ] Fix exportarr URL while rewriting: port `7878` → `8989` (`cluster/helm/media-stack/sonarr/templates/deployment.yaml`).
- [ ] Commit 1: new Application/values. Sync and verify.
- [ ] Commit 2: delete the old chart directory.

**Done when:** sonarr Synced/Healthy, UI reachable, exportarr scraped, no Pending PVC, old chart gone.
**If it slips:** only sonarr is affected; do not start wave 1 next session until the pilot is green.

## S2 — Media wave 1 (3 h)

- [ ] Migrate 4–5 local-chart media apps using the S1 template (suggested: radarr, radarr-anime, lidarr, prowlarr).
- [ ] Keep PUID/PGID 1100, `TZ=Europe/Paris`, `nodeSelector: streaming`, `strategy: Recreate`.
- [ ] Additive commits per app.

**Done when:** apps Synced/Healthy, config PVCs Bound on `local-path`, no new Pending pods.

## S3 — Media cleanup (3 h)

- [ ] `cluster/argocd/projects/nfs/plex-media.yaml`: path → `cluster/helm/nfs/plexmedia` (do not delete this app; it is the NFS provisioner).
- [ ] `cluster/argocd/projects/nfs/nfs-audio.yaml`: values path → `cluster/helm/nfs/nfs-audio/values.yaml`.
- [ ] `nfs-qbittorrent-config.yaml`: convert to multi-source (currently Synced but deploys nothing).
- [ ] qbittorrent download PVC: investigate the failed `nfs-ultrafaststorage` HelmChart in `default`; then either fix the class or repoint the PVC — not both.
- [ ] Investigate `maintainerr` Degraded.
- [ ] Delete `readarr` and `maintainerr-anime` (Application + chart) instead of rebuilding.

**Done when:** every media Application Synced, zero Pending pods in `media-stack`.

## S4 — Longhorn PVC migration (3 h) — do not postpone

Still mounted to running pods and will break on any restart or k3s reboot.

- [ ] calibre-config
- [ ] filebrowser-config and filebrowser-db-2
- [ ] firefly-config-2
- [ ] linkstack
- [ ] n8n-2
- [ ] syncthing-conf
- [ ] thelounge-volume
- [ ] pgadmin-pgadmin4
- [ ] postiz redis
- [ ] Skip `prometheus-data` (150Gi): it disappears with the old Prometheus in S6.

Per app: set `local-path` in the chart, delete the old PVC (data loss accepted), sync, verify the new PVC is Bound and the pod is Running.

**Done when:** `kubectl get pvc -A | grep longhorn` returns nothing except `prometheus-data`.

## S5 — Deletions and cleanup (2.5 h)

- [ ] Namespaces: nextcloud, synapse, inventree, inventree-lego, minio, orbital-sync, postfix, test, longhorn-system.
- [ ] Ghost releases: `helm uninstall qbit-manage syncrr radarr-hd sonarr-hd`.
- [ ] Repo: `cluster/helm/nfs/minio-nfs`, `cluster/helm/localpath/`.
- [ ] ~25 `Released` PVs from `driver.longhorn.io`.
- [ ] `kubectl/ingressRoute/longhorn.yaml`, `cluster/infrastructure/storageclass/lh-*.yaml`.
- [ ] Verify `plex-media`, `gpu-operator`, `node-feature-discovery`, `system-upgrade` are untouched.

**Done when:** no `longhorn` string anywhere except `REBUILD.md`/history, and ArgoCD is Synced.

## S6 — Observability 1: metrics (3.5 h)

- [ ] Remove the `prometheus` and `grafana` Applications and their vendored charts. Keep the exporters.
- [ ] Add kube-prometheus-stack 92.11.0 as pattern B, namespace `monitoring`, TSDB on `local-path`, 90d retention.
- [ ] ServiceMonitors for mktxp, pihole-exporter, smartctl.
- [ ] Drop the longhorn scrape job.

**Done when:** Prometheus shows all targets up, no Pending PVC, old Grafana data gone.

## S7 — Observability 2: logs and alerts (3 h)

- [ ] Loki 7.5.0 reusing `cluster/helm/monitoring/loki/values.yaml` (single binary, filesystem, `local-path`).
- [ ] Alloy 1.14.3 DaemonSet for pod logs.
- [ ] Alertmanager → Discord webhook from a Secret (never in git).
- [ ] Dashboards as ConfigMaps in git; ingress for Grafana/Prometheus/Alertmanager on the existing TLS pattern.
- [ ] Check free disk on `streaming` before enabling 90d retention.

**Done when:** logs searchable in Grafana, one test alert lands in Discord, dashboards survive a Grafana restart.

## S8 — Traefik step 1: externalize (3 h, single shot)

- [ ] Snapshot all live routes, middlewares, TLSOptions, TLSStores into `cluster/infrastructure/traefik/` (58 routes; the cluster copy is the only complete one).
- [ ] `disable: [traefik]` in `/etc/rancher/k3s/config.yaml` on all four nodes.
- [ ] Install the official chart pinned to 2.11, `existingClaim: pvc-traefik-nfs`, values mirroring live config.
- [ ] Verify all routers; check the ACME cert is the existing one.

**Done when:** ingress serving from the official chart, no cert re-issue, dashboard and metrics reachable.
**Rollback:** re-enable the bundled addon and restore the snapshot.

## S9 — Traefik step 2: rename (3 h)

- [ ] Batch 1: one namespace → `traefik.io/v1alpha1`, sync, verify.
- [ ] Batches 2–N: remaining files (106 total), preserving CRLF under `cluster/argocd/**`.
- [ ] Update `AGENTS.md` if the API-group rule changes.

**Done when:** zero `traefik.containo.us` references in git or live objects.

## S10 — Traefik step 3+4: v3 (3 h, single shot)

- [ ] Upgrade chart to 42.1.3 / Traefik v3.7.16.
- [ ] Fix routing regressions (headers, redirects, middleware behaviour).
- [ ] Delete the `containo.us` CRDs.
- [ ] Update `AGENTS.md` and `REBUILD.md` with the new version.

**Done when:** all routers healthy, certificate unchanged, no `containo.us` CRD left.
**Rollback:** previous chart version; 2.11 still reads `traefik.io`.

## S11 — K3s 1.31 + 1.32 (2 h)

- [ ] Confirm `disable: traefik` is in the config file, not a CLI flag, on every node.
- [ ] Pin `server-plan` to the latest 1.31 patch, upgrade servers one at a time (nuc0, rbp2, streaming), verify.
- [ ] Pin to the latest 1.32 patch, same order. `agent-plan` for `nas` last.
- [ ] Never let `channel: stable` run: it would jump straight to 1.36.

**Done when:** all four nodes Ready on 1.32, ArgoCD Synced, ingress serving.

## S12 — K3s 1.33 + 1.34 (2 h)

- [ ] Same procedure, one server at a time. 1.33 is also the Traefik v3 boundary — confirm the addon stays disabled.

**Done when:** all nodes on 1.34, no crash loops.

## S13 — K3s 1.35 + 1.36.5 (2 h)

- [ ] Same procedure, ending on the stable channel's current patch.
- [ ] Re-check `kubectl get nodes -o wide` and the SUC plans.

**Done when:** every node on the target version, cluster healthy, `REBUILD.md` phase 4 marked done.

## S14 — Remaining apps (3 h)

- [ ] minecraft-server, news-freshrss, discourse, postiz, monitorss, ghostfolio.
- [ ] Fresh config PVCs on `local-path`; `recyclarr`-style config rebuilt where applicable.
- [ ] Delete anything you no longer use rather than rebuilding it.

**Done when:** no Pending pods cluster-wide, every Application Synced.

## S15 — Postgres, Bitnami, registry (3 h)

- [ ] PostgreSQL: plain StatefulSet on `docker.io/library/postgres`, config in git, recreate the database.
- [ ] Bitnami exit: `ashuvidz-website` → official `nginx`; `postiz` → the new StatefulSet + official `redis`.
- [ ] ghcr sweep: 14 LinuxServer references, verify a ghcr tag exists before switching; update Renovate regexes.
- [ ] Stop vendoring; move `postiz` to pattern B.
- [ ] Mark `REBUILD.md` complete.

**Done when:** zero Bitnami references, zero vendored charts, Renovate still opens sensible PRs.

---

## Drift log

One line per finished session: planned vs actual, and what slipped.

| Session | Plan | Actual | Slip | Note |
|---|---|---|---|---|
| S1 | 3 h | | | |
| S2 | 3 h | | | |
| S3 | 3 h | | | |
| S4 | 3 h | | | |
| S5 | 2.5 h | | | |
| S6 | 3.5 h | | | |
| S7 | 3 h | | | |
| S8 | 3 h | | | |
| S9 | 3 h | | | |
| S10 | 3 h | | | |
| S11 | 2 h | | | |
| S12 | 2 h | | | |
| S13 | 2 h | | | |
| S14 | 3 h | | | |
| S15 | 3 h | | | |

If two sessions in a row slip by more than an hour, stop and re-plan instead of pushing through — the estimates assume green endings, not heroics.
