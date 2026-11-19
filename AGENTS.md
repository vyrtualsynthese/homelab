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
- 56 ArgoCD `Application` objects in `cluster/argocd/projects/**/*.yaml`. The old `calibre` Application
  and its chart were retired once Calibre-Web Automated had the collection.
- 53 Helm charts under `cluster/helm/`.
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
    - repoURL: "https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/"
      chart: nfs-subdir-external-provisioner
      targetRevision: 4.0.18          # chart version lives here, not in values.yaml
      helm:
        valueFiles:
          - $values/cluster/helm/nfs/books-media/values.yaml
    - repoURL: "https://github.com/vyrtualsynthese/homelab.git"
      targetRevision: develop
      ref: values
```

The repo directory then holds only `values.yaml` (no `Chart.yaml`). Examples: local-path,
books-media, nfs-audio.

Not the way to add an external chart: new work uses pattern D, and these three are converted
separately.

### C. Vendored upstream chart

No chart in the repo is built this way any more: `postiz` was the last one, and it was retired on
2026-11-18. The pattern meant committing the full upstream chart and editing it in place, which has
no clean upgrade path. Do not start another one; use pattern D.

### D. Wrapper chart with the official chart as a dependency

The way external charts are consumed: the repo owns a small chart in `cluster/helm/` whose
`Chart.yaml` pins the upstream chart to an exact `version:`, and whose `values.yaml` pins the image
repository and tag of every container it renders. `charts/` and `Chart.lock` are gitignored for the
wrapper charts that need them (see `.gitignore`) — ArgoCD runs `helm dependency build` at sync
time. Examples:
`monitoring/prometheus`,
`monitoring/grafana`, `monitoring/loki`, `monitoring/fluent-bit`, `authentik`, `dashy`,
`filebrowser`.

## 4. Conventions

- YAML: 2 spaces, lowercase keys.
- **CRLF**: every file in `cluster/argocd/**` uses CRLF. Strip `\r` when scripting
  (`... | tr -d '\r'`) and preserve CRLF when editing those files.
- Ingress is Traefik v2 CRDs only (`traefik.containo.us/v1alpha1`), never `networking.k8s.io/v1`.
  - Internal, no TLS: `entryPoints: [web]`, host `app.kub.local`.
  - Internal TLS: `entryPoints: [websecure]` on the same `app.kub.local` host, with neither
    `secretName` nor `certResolver` (Traefik serves the default `kube-system/local-cert`, the
    `*.kub.local` leaf of the ashulab internal CA), `tls.options: internal-tls` from the
    `traefik-gate` chart, and a `web` router that only redirects. See
    `docs/runbooks/internal-ca.md`.
  - TLS on a resolvable name: `entryPoints: [websecure]`, host `app.kub.home.ashudev.com`,
    `tls.certResolver: myresolver` plus a per-app `TLSOption` copied from an existing chart.
  - Public: `ashuvidz.com`, `forum.ashuvidz.com`.
  - Cross-namespace middleware refs use `kube-system-<name>@kubernetescrd`.
- **SSO: native first.** When an application supports OIDC (or SAML, or any session-based SSO) itself,
  use that, not `authentik-forwardauth`. The middleware cannot carry an identity into the app — every
  browser that passes the gate is the same user — so it is the fallback for applications that only
  know a local login. The order is: native OIDC, then proxy auth where the app is built for it
  (FileBrowser), then `authentik-forwardauth`, then `lan-only` alone with the reason written down.
  - `kube-system-authentik` is **headers**, not authentication. Naming an app's route with it gates
    nothing; the middleware that authenticates is `kube-system-authentik-forwardauth`.
  - A route that keeps `lan-only` and no gate must say why in the chart, next to the middleware list,
    the way `qbittorrent` and `autobrr` do.
- **Every non-public route carries `kube-system-lan-only@kubernetescrd`.** The router forwards 80/443 to
  the cluster and Traefik routes on the `Host` header alone, so an ungated route answers the internet
  whether or not its hostname resolves publicly. The public list is `plex`, `seerr`, `ashuvidz.com` and
  `forum.ashuvidz.com`; nothing else. `hack/check-exposure.sh` enforces it and
  must pass before a PR.
- Stateful apps: `strategy: Recreate`; one PVC per concern; `storageClassName: local-path` for config.
  Shared media/data volumes use the dedicated NFS provisioner classes (`nfs-*`). There is no
  replicated volume class: Longhorn was abandoned after the 2026 disk failure and is not coming back.
- LinuxServer images: `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`.
- Placement: `nodeSelector: kubernetes.io/hostname: <node>` — every workload is pinned to a named node,
  and its `local-path` volume lands on that same node. `streaming` for GPU and media work
  (`calibre-web-automated` and the rest), `nas` for storage-bound apps, `nuc0` for the general
  catalogue, `rbp2` for nothing but the control plane.
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

The inventory is [`docs/secrets.md`](docs/secrets.md). The chart that mounts or references a Secret
is the authority on its keys. A missing Secret shows as `CreateContainerConfigError`, not as a
failed sync.

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
`monitoring/loki`, `monitoring/fluent-bit`, `authentik`, `dashy`, `filebrowser`,
`databases/pgadmin`, `discourse`, `renovate`.

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

1. **Sync policy lives outside git, by design.** Auto-sync is enabled per Application from the ArgoCD
   UI — faster than a commit when debugging a pod. **No Application file records
   `syncPolicy.automated`**, and a PR that adds it is a regression: a root-app sync rewrites the
   Application to match git, which silently switches automation off until the operator re-enables it
   in the UI. None of the 56 carries it (§8.4 of the design).
2. `cluster/infrastructure/**` is outside GitOps (§2).
3. **Backups do not work.** The only offsite target is an AWS S3 bucket. The k3s script dumps a
   SQLite file on an etcd cluster; the NAS script backs up a MinIO path deleted in July 2024; and the
   k3s and NAS hosts have had a daily cron since 2024 pointing at a script that was never deployed
   to them. See `docs/audit/ansible-hosts.md`.
4. **Longhorn is gone, but it cannot bury its dead.** The driver, its StorageClasses, its custom
   resources and the `longhorn-system` namespace are gone, and every application that was on it runs on
   `local-path` or one of the NFS classes — except three: **`n8n`, `syncthing` and `thelounge` still
   mount their Longhorn claims** from before the failure. What survives is those three PVs and their
   three PVCs, stuck `Terminating` since 2026-08-28. Twenty-three of the twenty-six volumes and three
   of the six claims were cleared on 2026-11-18, and the namespace is gone. Their contents were
   abandoned by decision, and **they will never clear on their own**: the finalizers name the
   Longhorn CSI driver
   (`external-attacher/driver-longhorn-io`), which no longer exists to remove itself, and the bound
   ones also carry `kubernetes.io/pv-protection` until the matching claim goes.

   The inventory, the three claims that must wait for a rebuild, and the exact patch loop are in
   [`docs/cleanup/longhorn-disposition.md`](docs/cleanup/longhorn-disposition.md). Clearing one is a
   hand operation — patch the finalizers away, PV first and then the PVC:

   ```bash
   kubectl patch pv <name> -p '{"metadata":{"finalizers":null}}' --type=merge
   kubectl -n <namespace> patch pvc <name> -p '{"metadata":{"finalizers":null}}' --type=merge
   ```

   Never run that loop over the four externally-provisioned NFS volumes that also show a deletion
   timestamp (`pv-traefik-nfs`, `pv-plex-media`, `pv-stash-nfs-nfs-stash`,
   `pv-nfs-qbittorrent-download-nfs-subdir-external-provisioner`): they are live, and `pv-traefik-nfs`
   holds the ACME certificate store. The rest are inert: they hold no data, block nothing, and show up
   only in `kubectl get pv` and in namespace health counts.

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
10. **Resolved 2026-11-18:** the two duplicate `lh-performance` StorageClass files under
    `cluster/infrastructure/storageclass/` are deleted with the rest of the Longhorn leftovers.
11. **Resolved 2026-11-18:** `firefly` and `postiz` were the two charts that rendered a
    `networking.k8s.io/v1` Ingress, against the Traefik-CRD-only rule in §4. Both were retired, so no
    chart in the repo breaks that rule; the live `discourse` Ingress comes from its vendored upstream
    chart, not from a template here.
12. Floating `latest` tags in `dashy`, `mktxp`, `minecraft` and `local-path`. (`alpine:latest` in
    qBittorrent's VueTorrent installer was pinned on 2026-11-18: `vueTorrent.initTag` plus a
    `# renovate: datasource=docker` annotation.)

**Ingress**

13. All 69 live IngressRoutes and 84 repo files use `traefik.containo.us/v1alpha1`. Both CRD groups
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
- Do not merge the app patterns into one. A and D are in active use, B is legacy and converted one app at a time, and C has no chart left in the repo: do not bring it back for a new app.
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
- Observability has its own document: [`docs/observability-conventions.md`](docs/observability-conventions.md)
  covers the live stack, how to query Prometheus/Loki/Grafana without cluster access, the
  dashboards, the alert set and its known holes. Dashboards and alert rules are in Grafana's
  database, never in git.

### Configure through the API, not the operator's mouse

**Before asking a person to click something, look for a token.** App configuration, provider
cleanup, a blueprint re-apply and a sync trigger are all reachable over an HTTP API; handing them to
the operator wastes their turn and leaves no record of what changed.

| Target | Endpoint | Credential |
|---|---|---|
| Radarr, Sonarr, Lidarr, Prowlarr, Bazarr, Tautulli, Seerr, qBittorrent, autobrr, Kavita | each app's own REST API (`/api/v3/config/host` for the *arr settings, `/api/v1` on Prowlarr) | `<APP>_API_KEY` in `~/.config/secrets/homelab.env` |
| authentik | `https://authentik.kub.home.ashudev.com/api/v3/` — providers, applications, api tokens, blueprint apply | `AUTHENTIK_TOKEN` |
| Traefik, Grafana, anything the MCP exposes | the Kubernetes API or the app's API, read-only | the MCP ServiceAccount |
| Vikunja | `https://vikunja.kub.local/api/v2` | `VIKUNJA_API_TOKEN` |

- **State the call in the report**, with the endpoint and the result code, so the next agent can
  repeat it instead of re-deriving it.
- **A secret value is the exception**: `view` RBAC excludes Secrets, so a value the agent has never
  seen has to come from the operator. Say which key and which Secret, and stop there.
- **A browser sign-in is the other exception.** An OIDC flow needs the human once; verify everything
  up to the redirect and ask for the click.
- **If no token exists, say which endpoint would have done it** and file that gap as a card. Argo CD
  is the current example: the API would sync an Application, but no token has been issued, so a sync
  waits on the merge plus the poll.

Long form, with the specimens: [`docs/agent-protocol.md`](docs/agent-protocol.md).
