# AGENTS.md

Operating notes for AI agents working in this repo. Read this before changing anything.
Everything below was derived from the repo; items marked **[verify]** need cluster access.

> **Read this first.** The cluster is mid-rebuild, and the authoritative documents are in
> [`docs/`](docs/README.md) — audit, design, programme index and per-workstream plans. If this file
> disagrees with them, they win. `TODO.MD` and `REBUILD.md` were deleted on 2026-11-11 because they
> described a plan that no longer matched the cluster.

## 1. What this repo is

A single K3s homelab managed by ArgoCD (GitOps) plus Ansible for host prep.

- GitOps branch: `develop` (every Application pins `targetRevision: develop`).
- 58 ArgoCD `Application` objects in `cluster/argocd/projects/**/*.yaml`.
- 49 Helm charts under `cluster/helm/`.
- No CI. Renovate updates images/chart versions; nothing runs on PR.
- Secrets are never committed. See §5.

Nodes: `nas` (worker; NFS exports + `local-path` on a dedicated SSD), `streaming` (control-plane; NVIDIA GPU; runs most apps),
`nuc0` (control-plane), `rbp2` (control-plane Pi), `rbp0`/`rbp1` (edge Pis: Pi-hole + WireGuard).

## 2. How a change reaches the cluster

Commit to `develop` → ArgoCD root app (`cluster/argocd/argocd-root.yaml`) syncs the
`cluster/argocd` directory recursively → each `projects/**/*.yaml` Application syncs its own source.

Consequences:

- `cluster/argocd/projects/` contains **Applications, not AppProjects**. The folder name is misleading.
- Editing `cluster/helm/**` only takes effect after the commit lands on `develop`.
- Never `kubectl edit/apply` a GitOps-managed resource; automated `selfHeal` reverts it.
- `cluster/infrastructure/**` is **not referenced by any Application** → applied by hand. That includes
  the Traefik `HelmChartConfig`, middlewares and TLSStore. **[verify]** intent.

## 3. App patterns

### A. Local chart (majority)

`cluster/argocd/projects/<area>/<app>.yaml`:

```yaml
spec:
  destination:
    server: "https://kubernetes.default.svc"
    namespace: media-stack
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
  source:
    repoURL: "https://github.com/vyrtualsynthese/homelab.git"
    targetRevision: develop
    path: cluster/helm/media-stack/sonarr
```

The chart lives at `cluster/helm/<path>`; its version is `image.tag` in that chart's `values.yaml`.

### B. Remote chart + values (legacy for third-party apps)

```yaml
spec:
  sources:
    - repoURL: "https://vyrtualsynthese.github.io/selfhosted-helmcharts/"
      chart: bazarr
      targetRevision: 0.1.3          # chart version lives here, not in values.yaml
      helm:
        valueFiles:
          - $values/cluster/helm/media-stack/bazarr/values.yaml
    - repoURL: "https://github.com/vyrtualsynthese/homelab.git"
      targetRevision: develop
      ref: values
```

The repo directory then holds only `values.yaml` (no `Chart.yaml`). Examples: bazarr, bazarr-anime,
calibre, filebrowser, postgres, pgadmin, renovate, local-path, books-media, nfs-audio.

Not the way to add an external chart: new work uses pattern D, and these ten are converted
separately.

### C. Vendored upstream chart

`postiz` commits the full upstream chart and edits it in place. Treat upstream files as vendored:
change `values.yaml` unless you intend to fork. There is no clean upgrade path; prefer migrating to
pattern D when touching it. The vendored `prometheus` and `grafana` charts are gone — they are
wrappers now (pattern D).

### D. Wrapper chart with the official chart as a dependency

The way external charts are consumed: the repo owns a small chart in `cluster/helm/` whose
`Chart.yaml` pins the upstream chart to an exact `version:`, and whose `values.yaml` pins the image
repository and tag of every container it renders. `charts/` and `Chart.lock` are gitignored for the
monitoring wrappers (`.gitignore` scopes the rule there) — ArgoCD runs `helm dependency build` at
sync time. Examples: `monitoring/prometheus`,
`monitoring/grafana`, `monitoring/loki`, `monitoring/fluent-bit`, `authentik`, `dashy`.

## 4. Conventions

- YAML: 2 spaces, lowercase keys.
- **CRLF**: every file in `cluster/argocd/**` uses CRLF. Strip `\r` when scripting
  (`... | tr -d '\r'`) and preserve CRLF when editing those files.
- Ingress is Traefik v2 CRDs only (`traefik.containo.us/v1alpha1`), never `networking.k8s.io/v1`.
  - Internal, no TLS: `entryPoints: [web]`, host `app.kub.local`.
  - TLS: `entryPoints: [websecure]`, host `app.kub.home.ashudev.com`,
    `tls.certResolver: myresolver` plus a per-app `TLSOption` copied from an existing chart.
  - Public: `ashuvidz.com`, `forum.ashuvidz.com`, `wiki.ashuvidz.com`.
  - Cross-namespace middleware refs use `kube-system-<name>@kubernetescrd`.
- Stateful apps: `strategy: Recreate`; one PVC per concern; `storageClassName: local-path` for config.
  Shared media/data volumes use the dedicated NFS provisioner classes (`nfs-*`). There is no
  replicated volume class: Longhorn was abandoned after the 2026 disk failure and is not coming back.
- LinuxServer images: `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`.
- Placement: `nodeSelector: kubernetes.io/hostname: <node>` — every workload is pinned to a named node,
  and its `local-path` volume lands on that same node. `streaming` for GPU and media work, `nas` for
  storage-bound apps, `nuc0` for the general catalogue, `rbp2` for nothing but the control plane.
  Observability (Prometheus, Grafana, Loki) is a `nas` workload: ~50 GiB of `local-path` claims, no
  NFS. The NAS's `/mnt/ultrafaststorage` is reserved for the qbittorrent download export; nothing
  else goes there. DaemonSets (`fluent-bit`, `prometheus-node-exporter`) are not pinned: they must
  run on every node, including `rbp2`. The old `node-type` label is retired; do not reintroduce it.
- Never pin a workload to `rbp2`. It is an ARM64 Raspberry Pi and several images have no ARM build.
- Resources: every container sets memory requests/limits; CPU requests are tiny (1m–100m).
- Monitoring: exporters expose 9707 (exportarr) with `prometheus.io/scrape` annotations; Prometheus
  scrapes via annotations.
- Commit messages use an emoji prefix: `⬆️` upgrade, `🐛` fix, `🚀` feature, `♻️` refactor.

## 5. Secrets (never commit)

Templates expect these Secrets to already exist; a missing one shows up as
`CreateContainerConfigError`. **[verify]** the full list against the cluster.

| Namespace | Secret | Keys / used by |
|---|---|---|
| media-stack | `sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`, `lidarr`, `prowlarr` | `secret` → exportarr APIKEY |
| media-stack | `plex` | `plex_claim` |
| media-stack | `qbittorrent-exporter` | `username`, `password` |
| media-stack | `cross-seed` | mounted as a file (`conf.secretName`) |
| media-stack | `recyclarr` | mounted as a file |
| firefly | `firefly-app` | `APP_KEY`, `STATIC_CRON_TOKEN` |
| firefly | `firefly-db` | `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` |
| guacamole | `guacamole-db` | `POSTGRES_USER`, `POSTGRES_PASSWORD` |
| koillection | `koillection` | `DB_NAME`, `DB_HOST`, `DB_USER`, `DB_PASSWORD` |
| monitorss | `monitorss` | bot `clientid`, `clientsecret`, `token` |
| wiki | `wikijs` | `password` |
| ghostfolio | `ghostfolio` | `envFrom` |
| syncthing | `syncthing-backup` | `envFrom` (restic credentials) |
| authentik | `authentik-env` | `envFrom` |
| dashy | `dashy-conf` | external chart `existingSecret` |
| discourse | `discourse` | `discourse-password` |
| databases | `postgres-secrets` | Bitnami PostgreSQL `existingSecret` |
| databases | `pgadmin` | external chart `existingSecret` |
| renovate | `renovate` | external chart `existingSecret` |
| monitoring | `pihole-exporter` | `key` |
| monitoring | `mktxp-conf` | mounted as a file |
| monitoring | `grafana` | `admin-user`, `admin-password` — required; the chart reads it via `admin.existingSecret` |
| kube-system | `local-cert` | Traefik default TLS cert (`TLSStore`) |
| kube-system | `traefik-dashboard-auth` | basicAuth for the dashboard |

`n8n` references no pre-existing Secret. `postiz` templates `<fullname>-secrets` itself.

## 6. Validate before committing (no cluster needed)

Render every self-contained chart:

```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```

Charts that need `helm dependency build`: `monitoring/prometheus`, `monitoring/grafana`,
`monitoring/loki`, `monitoring/fluent-bit`, `authentik`, `dashy`, `ashuvidz-website`,
`databases/pgadmin`, `discourse`, `postiz`.

Check that ArgoCD references resolve (CRLF-safe):

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")

while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u)
```

## 7. Known issues

Verified against the cluster on 2026-11-10. Evidence in `docs/audit/`; the plans that fix each item
are in `docs/superpowers/plans/`.

**Structural**

1. **Sync policy lives outside git.** Auto-sync is enabled per Application from the ArgoCD UI — faster
   than a commit when debugging a pod — so ~34 of the 58 manifests record no `syncPolicy.automated`.
   The apps are reconciled, but git does not say so, and a root-app sync that rewrites an Application
   can silently switch automation off. Write the policy into git when you next touch each app (§8.4
   of the design).
2. `cluster/infrastructure/**` is outside GitOps (§2).
3. **Backups do not work.** The only offsite target is an AWS S3 bucket. The k3s script dumps a
   SQLite file on an etcd cluster; the NAS script backs up a MinIO path deleted in July 2024; and the
   k3s and NAS hosts have had a daily cron since 2024 pointing at a script that was never deployed
   to them. See `docs/audit/ansible-hosts.md`.
4. Longhorn is gone: no pods, no StorageClasses, and 36 PVs plus 11 PVCs were stuck `Terminating`.
   Their contents are abandoned by decision; every affected app is reconfigured from scratch.

**Broken references**

5. `projects/nfs/plex-media.yaml` → `path: cluster/helm/nfs/plex-media`; the directory is `plexmedia`.
   This orphans the media NFS provisioner chart.
6. `projects/nfs/nfs-audio.yaml` → `$values/cluster/helm/nfs/audio/values.yaml`; the directory is
   `nfs-audio/`.
7. `projects/nfs/nfs-qbittorrent-config.yaml` uses `path:` on a directory containing only
   `values.yaml` and `README.md` (no `Chart.yaml`) → ArgoCD renders it as an empty directory and
   reports **Synced/Healthy while deploying nothing**.

**Chart defects**

8. `media-stack/sonarr/templates/deployment.yaml:77`: exportarr `URL` uses port `7878`; Sonarr listens
   on `8989` (`sonarr-anime` has it right).
9. `infrastructure/traefik/traefik-config.yaml:145`: `existingClaim` is over-indented, so YAML folds it
   into `persistence.name`. The live DaemonSet mounts `pvc-traefik-nfs`, which is the ACME store —
   reuse that claim or the certificate is re-issued.
10. Two files under `cluster/infrastructure/storageclass/` both declare `metadata.name: lh-performance`.
    Applied by hand, the class does not exist, and nothing reads them.
11. `firefly/templates/ingress-public.yaml` and `postiz/templates/postiz-ingress.yaml` use
    `networking.k8s.io/v1` Ingress, against the Traefik-CRD-only rule in §4.
12. Floating `latest` tags in `dashy`, `monitorss` (two images), `mktxp`, `linkstack`,
    `minecraft`, `local-path`, and `alpine:latest` at
    `media-stack/qbittorrent/templates/deployment.yaml:50`.

**Ingress**

13. All 58 live IngressRoutes and 106 repo files use `traefik.containo.us/v1alpha1`. Both CRD groups
    are installed; the v3 upgrade requires moving to `traefik.io/v1alpha1` while still on 2.11.
14. Orphans: `cluster/helm/nfs/minio-nfs` (full chart, no Application, MinIO is gone). **Resolved for
    the monitoring charts**: `cluster/helm/monitoring/loki` is now a wrapper chart with an
    Application.

**Version drift**

15. Traefik `2.9.4` (June 2023) from the K3s bundled addon; K3s `1.30.1` against a `1.36.x` stable.
16. **Resolved for the monitoring charts:** the vendored `prometheus` 14.8.0 and `grafana` 6.52.5 are
    replaced by wrapper charts on current upstream versions. No vendored charts are to be added;
    migrate the remaining ones on their next touch.
17. `renovate.json` ignores `cluster/infrastructure/**`, `kubectl/**` and `ansible/**` — those are
    bumped by hand.

## 8. Rules

- Never commit secrets, kubeconfigs, `*.pub` keys, or `restic_env.yaml` (see `.gitignore`).
- Do not introduce a shared/library chart or restructure the per-app charts unless asked.
- Do not merge the app patterns into one. A, C and D are in active use; B is legacy and converted one app at a time.
- Changes go through git. Never apply manifests by hand to "fix" drift.
- Adding an app means adding the chart (or values-only dir) **and** the Application file; the root app
  finds it automatically via `directory.recurse`.

## 9. Cluster access

A `kubernetes` MCP server (containers/kubernetes-mcp-server) is configured globally in OpenCode and runs
as the **read-only** ServiceAccount `kube-system/opencode-mcp`, using `~/.kube/mcp-readonly.yaml`.

- RBAC lives in `kubectl/mcp-readonly-rbac.yaml` (applied by hand): built-in `view` plus read on
  nodes/PVs/namespaces/StorageClasses and the CRDs this repo uses. `view` excludes Secrets by design.
- The server config is `~/.config/opencode/kubernetes-mcp.toml` (`read_only = true`,
  `disabled_tools = ["configuration_view"]` so the token is never printed).
- The human kubeconfig stays cluster-admin (`system:admin`). Do not point the MCP server at it.
- To grant writes, create a scoped Role for the ServiceAccount and bind it — do not reuse admin.
