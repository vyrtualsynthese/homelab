# AGENTS.md

Operating notes for AI agents working in this repo. Read this before changing anything.
Everything below was derived from the repo; items marked **[verify]** need cluster access.

## 1. What this repo is

A single K3s homelab managed by ArgoCD (GitOps) plus Ansible for host prep.

- GitOps branch: `develop` (every Application pins `targetRevision: develop`).
- 56 ArgoCD `Application` objects in `cluster/argocd/projects/**/*.yaml`.
- 48 Helm charts under `cluster/helm/`.
- No CI. Renovate updates images/chart versions; nothing runs on PR.
- Secrets are never committed. See §5.

Nodes: `nas` (worker; NFS + Longhorn storage), `streaming` (control-plane; NVIDIA GPU; runs most apps),
`nuc0` (control-plane), `rbp2` (control-plane Pi), `rbp0`/`rbp1` (edge Pis: Pi-hole + WireGuard).

## 2. How a change reaches the cluster

Commit to `develop` → ArgoCD root app (`cluster/argocd/argocd-root.yaml`) syncs the
`cluster/argocd` directory recursively → each `projects/**/*.yaml` Application syncs its own source.

Consequences:

- `cluster/argocd/projects/` contains **Applications, not AppProjects**. The folder name is misleading.
- Editing `cluster/helm/**` only takes effect after the commit lands on `develop`.
- Never `kubectl edit/apply` a GitOps-managed resource; automated `selfHeal` reverts it.
- `cluster/infrastructure/**` is **not referenced by any Application** → applied by hand. That includes
  the Traefik `HelmChartConfig`, middlewares, TLSStore and the Longhorn StorageClasses. **[verify]** intent.

## 3. Three app patterns

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

### B. Remote chart + values (preferred for third-party apps)

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

### C. Vendored upstream chart

`prometheus`, `grafana`, `postiz` commit the full upstream chart and edit it in place. Treat upstream
files as vendored: change `values.yaml` unless you intend to fork. There is no clean upgrade path;
prefer migrating to pattern B when touching these.

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
  Shared media/data volumes use the dedicated NFS provisioner classes (`nfs-*`), not Longhorn.
- LinuxServer images: `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`.
- Placement: `nodeSelector: kubernetes.io/hostname: streaming` for most apps; `nas` for storage-bound ones.
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
| monitoring | `grafana` | chart-created admin creds unless `admin.existingSecret` is set |
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

Charts that need `helm dependency build`: `authentik`, `dashy`, `ashuvidz-website`,
`databases/pgadmin`, `discourse`, `postiz`.

Check that ArgoCD references resolve (CRLF-safe):

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")

while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u)
```

## 7. Known issues (repo evidence only, not yet confirmed on-cluster)

1. `projects/nfs/plex-media.yaml` → `path: cluster/helm/nfs/plex-media`; the directory is `plexmedia`.
2. `projects/nfs/nfs-audio.yaml` → `$values/cluster/helm/nfs/audio/values.yaml`; the directory is `nfs-audio/`.
3. `projects/nfs/nfs-qbittorrent-config.yaml` uses `path:` on a directory containing only `values.yaml`
   and `README.md` (no `Chart.yaml`) → should be multi-source like the other NFS apps. ArgoCD renders it
   as a plain directory, finds no manifests, and reports **Synced/Healthy while deploying nothing**.
4. `cluster/infrastructure/**` is outside GitOps (§2).
5. `infrastructure/traefik/traefik-config.yaml:145`: `existingClaim` is over-indented, so YAML folds it into
   `persistence.name`. Check which PVC Traefik actually got for ACME storage.
6. `media-stack/sonarr/templates/deployment.yaml`: exportarr `URL` uses port `7878`; Sonarr listens on
   `8989` (`sonarr-anime` has it right).
7. All IngressRoutes use `traefik.containo.us/v1alpha1`. Correct for Traefik v2 (K3s 1.30); a Traefik v3
   upgrade requires switching to `traefik.io/v1alpha1` across 57 files.
8. Orphans: `cluster/helm/nfs/minio-nfs` (no Application), empty `cluster/helm/localpath/`, empty `TODO.MD`.
9. `renovate.json` ignores `cluster/infrastructure/**`, `kubectl/**`, `ansible/**` — those are bumped by hand.

## 8. Rules

- Never commit secrets, kubeconfigs, `*.pub` keys, or `restic_env.yaml` (see `.gitignore`).
- Do not introduce a shared/library chart or restructure the per-app charts unless asked.
- Do not merge the three app patterns into one; all three are in active use.
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
