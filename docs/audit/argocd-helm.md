# Audit — ArgoCD and Helm drift

Read-only audit, 2026-11-10. Repo root `.../worktree/117be0/agent1`.
Scripts and raw outputs: `/tmp/opencode/homelab-audit/{analyze.py,analyze.out,ingress2.out,render-summary.txt}`.

## 1. What ArgoCD actually manages

56 Applications under `cluster/argocd/projects/**`, plus `argocd-root`.

| Sync policy | Count | Consequence |
|---|---|---|
| `automated` with `prune` + `selfHeal` + `CreateNamespace=true` | ~20 | genuinely reconciled |
| `automated` without selfHeal/prune | small remainder | partially reconciled |
| **no `syncPolicy.automated` at all** | ~34 | **applied by hand once, then never reconciled** |

Apps with no automation include `ashuvidz`, `authentik`, `dashy`, `discourse`, `firefly`,
`ghostfolio`, `guacamole`, `koillection`, `linkstack`, most media-stack `*arr` apps, all eight
`nfs/*` apps, `postiz`, `wiki`, `n8n`, `news-freshrss`.

Additional gap: `syncOptions` is empty for `ashuvidz`, `authentik`, `dashy` and all `nfs/*`
apps, so their destination namespaces are not created by the Application. They exist on the
cluster only because someone made them by hand.

This is the single largest structural drift in the repo. The README and AGENTS.md describe a
GitOps cluster; in practice roughly 60 % of the catalogue is a set of one-shot applies, and
manual `helm` or `kubectl` changes on those apps are not reverted. `REBUILD.md` line 42 says
"31 apps have selfHeal=true + prune=true" — the real figure is near 20.

## 2. Reference integrity

Confirmed broken:

1. `projects/nfs/plex-media.yaml` → `cluster/helm/nfs/plex-media`; the directory is `plexmedia`.
   Because of this, the `plexmedia` chart is unreachable — it is the NFS provisioner for the
   media share, so the media StorageClass has no owner in git.
2. `projects/nfs/nfs-audio.yaml` → `$values/cluster/helm/nfs/audio/values.yaml`; the file is at
   `nfs/nfs-audio/values.yaml`.
3. `projects/nfs/nfs-qbittorrent-config.yaml` uses a single `path:` on a directory holding only
   `values.yaml` and `README.md`. ArgoCD renders an empty manifest set and reports Synced while
   deploying nothing.

Orphans and dead weight:

- `cluster/helm/nfs/minio-nfs` — full chart, no Application. MinIO is decommissioned.
- `cluster/helm/nfs/plexmedia` — reachable only through defect 1.
- `cluster/helm/argocd` — `values.yaml` only, no Application. Correct if ArgoCD stays a manual
  Helm release (REBUILD.md lists that as a deliberate exception), but nothing in the repo says so
  except that document.
- `cluster/helm/monitoring/loki` — `values.yaml` only, no Application, no chart.
- `cluster/helm/databases/pgadmin` — local chart with a `Chart.yaml`, but the Application uses the
  remote `runix` chart 1.50.0. The local chart is dead weight.
- `cluster/helm/local-path` — holds only `values.yaml` and is used by the Application. The empty
  directory named in AGENTS.md §7.8 is `cluster/helm/localpath/`, which does not exist on disk.

## 3. Patterns and versions

| App | Pattern | Source | Notes |
|---|---|---|---|
| `prometheus`, `grafana` | C | vendored, chart 14.8.0 / 6.52.5 | appVersions 2.26.0 / 9.4.3, years behind their images |
| `postgres` | B | Bitnami `postgresql` 15.5.23 | **Bitnami is being exited** — this is the app that must become a plain StatefulSet |
| `pgadmin` | B | `runix/pgadmin4` 1.50.0 | local chart unused |
| `filebrowser`, `bazarr`, `bazarr-anime`, `calibre` | B | `vyrtualsynthese/selfhosted-helmcharts` 0.1.2–0.1.3 | own charts, tiny version numbers |
| `nfs/books-media`, `nfs/nfs-audio` | B | `nfs-subdir-external-provisioner` 4.0.18 | image pinned `v4.0.2` |
| `renovate` | B | 46.141.4 in the Application, `43.160.6` in values | mismatch |
| `local-path` | B | rancher `local-path-provisioner` `master` | **floating branch**, image `v0.0.37` |
| `postiz`, `discourse`, `authentik`, `dashy`, `ashuvidz-website` | C | vendored | need `helm dependency build` |

Floating `latest` tags: `dashy`, `monitorss` (two images), `mktxp`, `linkstack`, `grafana`,
`minecraft`, `local-path`, plus `alpine:latest` inline at
`media-stack/qbittorrent/templates/deployment.yaml:50`.

Aged inline images: `busybox:1.35` in five files, `alpine/git:2.40.1`.

## 4. Ingress

38 charts render cleanly; 6 need `helm dependency build`.

- 106 repo files and 58 live objects use `traefik.containo.us/v1alpha1`. Zero use
  `traefik.io/v1alpha1`, although both CRD groups are installed.
- **Two violations of the project's own rule** that ingress is Traefik CRDs only:
  - `cluster/helm/firefly/templates/ingress-public.yaml:10` — `networking.k8s.io/v1`
  - `cluster/helm/postiz/templates/postiz-ingress.yaml:2` — `networking.k8s.io/v1`
- Cross-namespace middleware references in 11 values files use
  `kube-system-{redirect-https,authentik,security,discord}@kubernetescrd`.
- `wiki` also references a local `wiki/wiki-app-redirect-old` middleware.

Domain groups in use: `*.kub.local` (web, no TLS), `*.kub.home.ashudev.com` and
`ashuvidz.com` / `forum.ashuvidz.com` / `wiki.ashuvidz.com` / `dashy.ashudev.com`
(websecure, `certResolver: myresolver`).

Caution for future renders: `helm template` without `--namespace` inherits the ambient kubeconfig
namespace, which produced false "wrong namespace" findings during this audit. Always pass
`--namespace`.

## 5. Secrets referenced by charts

From grep across all charts: `dashy-conf`, `pgadmin`, `postgres-secrets`, `discourse`,
`cross-seed`, `recyclarr` (hardcoded name), `renovate`, `mktxp-conf`, `importer-auth` (firefly
middleware), plus the exportarr and `*arr` `secretKeyRef`s documented in AGENTS.md §5.

Not yet reconciled against AGENTS.md §5 in both directions (undocumented secret vs documented but
unreferenced). Deferred to the documentation workstream.

## 6. Decommissioned software

- MinIO: only `cluster/helm/nfs/minio-nfs` (orphan chart) and
  `ansible/playbooks/scripts/backup-scripts/nas/backup.sh:4`, which still includes
  `/mnt/storage/minio/.minio.sys` in the restic backup set.
- AWS: real usage only in `ansible/playbooks/backup-setup.yaml:30-45` — restic to
  `s3:s3.amazonaws.com/ashudev-homelab-<host>-backup` with `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY`. Everything else is commented-out boilerplate in vendored chart values.
- No `route53`, `external-dns`, `cloudflare`, `rclone` or cloud SDK references anywhere.

## 7. Known defects, verdicts

| Defect | Verdict | Evidence |
|---|---|---|
| `plex-media` path vs directory | CONFIRMED | `projects/nfs/plex-media.yaml` |
| `nfs-audio` values path | CONFIRMED | `projects/nfs/nfs-audio.yaml` |
| `nfs-qbittorrent-config` deploys nothing | CONFIRMED | single `path:`, no `Chart.yaml` |
| exportarr port 7878 vs 8989 | CONFIRMED | `media-stack/sonarr/templates/deployment.yaml:77` vs `:48`, `service.yaml:10`. `sonarr-anime` is correct. |
| Traefik `existingClaim` over-indented | CONFIRMED | `infrastructure/traefik/traefik-config.yaml:145`, nested under `name: data` (144). YAML still parses, which is why it fails silently. |
| orphan `nfs/minio-nfs` | CONFIRMED | directory has a full chart, no Application |
| empty `localpath/` | **REFUTED** | the directory named `cluster/helm/local-path/` exists and holds `values.yaml`; `localpath/` does not exist |
