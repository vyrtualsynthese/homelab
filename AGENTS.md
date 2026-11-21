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
- 57 ArgoCD `Application` objects in `cluster/argocd/projects/**/*.yaml`. The old `calibre` Application
  and its chart were retired once Calibre-Web Automated had the collection.
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
  the Traefik middlewares and TLSStore, and the Traefik release itself: it is installed from
  `cluster/infrastructure/traefik/values.yaml` and moved with `helm` (see
  [`docs/runbooks/traefik-release.md`](docs/runbooks/traefik-release.md)). K3s's Traefik addon is
  disabled in the node config.

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
    - repoURL: "https://github.com/rancher/local-path-provisioner.git"
      targetRevision: master
      path: deploy/chart/local-path-provisioner
      helm:
        valueFiles:
          - $values/cluster/helm/local-path/values.yaml
    - repoURL: "https://github.com/vyrtualsynthese/homelab.git"
      targetRevision: develop
      ref: values
```

The repo directory then holds only `values.yaml` (no `Chart.yaml`). Example: local-path.

Not the way to add an external chart: new work uses pattern D, and this one is converted separately.

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
    `traefik-gate` chart, and a `web` router that only redirects. The Traefik dashboard
    (`traefik.kub.local`) is the reference case: `values.yaml` carries the route and
    `dashboard/redirect.yaml` its `web` twin. See `docs/runbooks/internal-ca.md`.
  - TLS on a resolvable name: `entryPoints: [websecure]`, host `app.kub.home.ashudev.com`,
    `tls.certResolver: myresolver` plus a per-app `TLSOption` copied from an existing chart.
  - Public: `ashuvidz.com`.
  - Cross-namespace middleware refs use `kube-system-<name>@kubernetescrd`.
- **SSO: native first.** When an application supports OIDC (or SAML, or any session-based SSO) itself,
  use that, not `authentik-forwardauth`. The middleware cannot carry an identity into the app — every
  browser that passes the gate is the same user — so it is the fallback for applications that only
  know a local login. The order is: native OIDC, then proxy auth where the app is built for it,
  then `authentik-forwardauth`, then `lan-only` alone with the reason written down.
  - `kube-system-authentik` is **headers**, not authentication. Naming an app's route with it gates
    nothing; the middleware that authenticates is `kube-system-authentik-forwardauth`.
  - A route that keeps `lan-only` and no gate must say why in the chart, next to the middleware list,
    the way `qbittorrent` and `autobrr` do.
- **Every non-public route carries `kube-system-lan-only@kubernetescrd`.** The router forwards 80/443 to
  one cluster address, `192.168.1.46` (`streaming`), and Traefik routes on the `Host` header alone, so
  an ungated route answers the internet whether or not its hostname resolves publicly. The public list
  is `plex`, `seerr`, `ashuvidz.com`, `ashudev.com` with `www.ashudev.com`, `dashy.ashudev.com`,
  `filebrowser.kub.home.ashudev.com` and `authentik.kub.home.ashudev.com` — the last one because it
  is the sign-in page of the page before it, and a gate whose login lives on the LAN cannot be used
  from outside. FileBrowser is public because the editor signs in from outside and its share links
  are opened by people with no account. Nothing else. `hack/check-exposure.sh` enforces it and must
  pass before a PR.
- Stateful apps: `strategy: Recreate`; one PVC per concern; `storageClassName: local-path` for config.
  Shared media/data volumes use one of the eight NFS classes, each a release of
  `cluster/helm/nfs/provisioner` with its own values file. There is no
  replicated volume class: Longhorn was abandoned after the 2026 disk failure and is not coming back.
- **Backups: `cluster/helm/backup/values.yaml` is the coverage matrix.** One OVH bucket in `GRA`, four
  restic repositories (`nodes-nas`, `nodes-nuc0`, `nodes-streaming`, `nfs`), six CronJobs. Adding or
  removing a stateful application means adding or removing its row in the same change, with the
  measured size beside it; a row that resolves to nothing fails that node's job on purpose. No job
  runs `restic init` — the four repositories are created once by hand. `backup-verify` runs nightly
  and is what proves a stream is still advancing; `BackupStreamStale` alerts when one stops. The page
  is `docs/how-it-works/backups.md`, the procedure `docs/runbooks/restore.md`.
- LinuxServer images: `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`.
- Placement: `nodeSelector: kubernetes.io/hostname: <node>` — every workload is pinned to a named node,
  and its `local-path` volume lands on that same node. `streaming` for GPU and media work
  (`calibre-web-automated` and the rest), `nas` for storage-bound apps, `nuc0` for the general
  catalogue, `rbp2` for nothing but the control plane.
  Observability (Prometheus, Grafana, Loki) is a `nas` workload: ~50 GiB of `local-path` claims, no
  NFS. The NAS's `/mnt/ultrafaststorage` is reserved for the qbittorrent download export; nothing
  else goes there. DaemonSets (`fluent-bit`, `prometheus-node-exporter`) are not pinned: they must
  run on every node, including `rbp2`. The old `node-type` label is retired; do not reintroduce it.
  Traefik is the exception in the other direction: one `Deployment` replica pinned to `streaming`,
  never a DaemonSet. The certificate store is one file with no lock, so a second router alongside the
  first re-requests every name and burns the weekly quota (task 482).
- Never pin a workload to `rbp2`. It is an ARM64 Raspberry Pi and several images have no ARM build.
- Resources: every container sets memory requests/limits; CPU requests are tiny (1m–100m).
- Monitoring: exporters expose 9707 (exportarr) with `prometheus.io/scrape` annotations; Prometheus
  scrapes via annotations.
- **Providers are European, EU member states only.** Every service holding the operator's data or the
  cluster's identity runs on a company headquartered in an EU member state with the data inside the
  EU: object storage, DNS, mail, registries, identity. Switzerland, the UK and Norway are out —
  European is not the test, EU is. OVHcloud and Scaleway comply; GitHub, the container registries and
  Let's Encrypt are open items. The record is
  [`docs/decisions/2026-11-21-sovereign-eu-providers-only.md`](docs/decisions/2026-11-21-sovereign-eu-providers-only.md).
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

`hack/check-charts.sh` is that loop in bulk plus `helm lint`: requirement 11 of
[`docs/charts/README.md`](docs/charts/README.md) is both commands, and `helm template` masks a broken
document separator with the `# Source:` line it injects while lint does not. The script builds each
dependency chart in a temporary copy, so it lints all 50.

Charts that need `helm dependency build`: `monitoring/prometheus`, `monitoring/grafana`,
`monitoring/loki`, `monitoring/fluent-bit`, `authentik`, `dashy`, `filebrowser`,
`databases/pgadmin`, `renovate`, `nfs/provisioner`.

`hack/check-app-version.sh` checks requirement 1 of [`docs/charts/README.md`](docs/charts/README.md):
every `appVersion` names the version inside the chart's `image.tag`, with the packaging suffix
dropped. It is the check to run after merging a Renovate image bump, which moves the tag alone.

`hack/check-config-keys.sh` renders each chart listed in `hack/config-key-checks.yaml` and fails
any key in its config file that the pinned dependency's `values.yaml` does not declare — the class
of failure that cost the FileBrowser cutover three syncs. A chart whose app rejects unknown config
keys at startup belongs in that manifest; the check names the key and its line in the rendered file.

Check that ArgoCD references resolve (CRLF-safe):

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")

while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u)
```

Run the `hack/` checker suite (its dependencies are declared in `hack/tests/requirements.txt`):

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```

## 7. Known issues

Verified against the cluster on 2026-11-10. Evidence in `docs/audit/`; the plans that fix each item
are in `docs/superpowers/plans/`.

**Structural**

1. **Sync policy lives outside git, by design.** Auto-sync is enabled per Application from the ArgoCD
   UI — faster than a commit when debugging a pod. **No Application file records
   `syncPolicy.automated`**, and a PR that adds it is a regression: a root-app sync rewrites the
   Application to match git, which silently switches automation off until the operator re-enables it
   in the UI. None of the 55 carries it (§8.4 of the design).
2. `cluster/infrastructure/**` is outside GitOps (§2).
3. **Backups run, and a copy comes back; a rebuild from one is not yet drilled.** One OVH bucket in
   `GRA`, four restic repositories (`nodes-nas`, `nodes-nuc0`, `nodes-streaming`, `nfs`), six CronJobs
   in `cluster/helm/backup`, nightly. `docs/how-it-works/backups.md` is the page. The drill log in
   `docs/runbooks/restore.md` holds three read-only restores from 2026-11-21 (the PostgreSQL dumps,
   Sonarr's database, a Syncthing file); the destructive put-back onto a live claim and the etcd
   rehearsal are untried.
   The old paths remain broken and are not used: the k3s script dumps a SQLite file on an etcd cluster,
   the NAS script backs up a MinIO path deleted in July 2024. See `docs/audit/ansible-hosts.md`.
4. **Longhorn is gone, and its dead are buried (resolved 2026-11-19).** The driver, its
   StorageClasses, its custom resources, the `longhorn-system` namespace, all twenty-six volumes and
   all six claims are gone, and every application that was on it runs on `local-path` or one of the
   NFS classes. Twenty-three volumes and three claims were cleared on 2026-11-18; `n8n`, `thelounge`
   and `syncthing` were rebuilt on `local-path` on 2026-11-19 and the last three claims followed that
   day, `syncthing-conf` last. Their contents were abandoned by decision.

   The inventory, the dispositions and the patch loop that was used are kept as the record in
   [`docs/cleanup/longhorn-disposition.md`](docs/cleanup/longhorn-disposition.md).

   **The last trap is cleared (2026-11-21).** Four externally-provisioned NFS volumes had carried a
   deletion timestamp since 2023-12-06 — `pv-traefik-nfs` (the ACME certificate store), `pv-plex-media`
   (the film and television library), `pv-stash-nfs-nfs-stash` (the assets export koillection writes
   to and FileBrowser reads) and `pv-nfs-qbittorrent-download-nfs-subdir-external-provisioner` (the
   downloads queue). They were kept by decision on 2026-11-20, then rebuilt: the claim is deleted,
   the volume's pending deletion completes, self-heal recreates the pair from the provisioner chart,
   and the data stays on `nas` because every volume is `Retain`. No pod restarts and
   `kubectl get pv` must now print **no** `Terminating` volume; a name in that list is a rebuild, not
   a mark to tolerate. A finalizer is never cleared on its own — the claim has to go with it. The
   order and the evidence are in [`docs/how-it-works/storage.md`](docs/how-it-works/storage.md).

**Broken references**

5. **Resolved 2026-11-19:** `projects/nfs/plex-media.yaml` pointed at `cluster/helm/nfs/plex-media`
   while the directory was `plexmedia`. It now reads `cluster/helm/nfs/provisioner` with
   `values/plex-media.yaml`.
6. **Resolved 2026-11-19:** `projects/nfs/nfs-audio.yaml` read
   `$values/cluster/helm/nfs/audio/values.yaml`, which never existed. It now reads
   `cluster/helm/nfs/provisioner` with `values/nfs-audio.yaml`.
7. **Resolved 2026-11-19:** `projects/nfs/nfs-qbittorrent-config.yaml` used `path:` on a directory
   holding no `Chart.yaml`, so ArgoCD reported **Synced/Healthy while deploying nothing**. It now
   reads `cluster/helm/nfs/provisioner` with `values/nfs-qbittorrent-config.yaml`.

**Chart defects**

8. `media-stack/sonarr/templates/deployment.yaml:77`: exportarr `URL` uses port `7878`; Sonarr listens
   on `8989` (`sonarr-anime` has it right).
9. **Resolved 2026-11-21, applied:** `infrastructure/traefik/traefik-config.yaml` parses again and
   the release is on chart `25.0.3` / Traefik `2.10.7`. The over-indented `existingClaim` was one of
   five defects that had kept every apply failing since 2023: the file also used
   `certificatesResolvers` where the chart reads `certResolvers` (which configures no ACME resolver
   at all, so `acme.json` is never loaded), named `traefik-pvc` instead of the live
   `pvc-traefik-nfs` (the certificate store), omitted the Syncthing entry points that only the
   hand-edited live Service had, and omitted `providers.kubernetesCRD.allowCrossNamespace` (every
   gate and redirect is referenced as `kube-system-<name>@kubernetescrd`).
   Applying it hit two further blockers, both now recorded in
   [`docs/runbooks/traefik-release.md`](docs/runbooks/traefik-release.md): chart `25.0.3` refuses to
   render until the `traefik.io/v1alpha1/ServersTransportTCP` CRD exists (the CRDs were 2.9-era and
   applied by hand), and the K3s helm-controller runs no job for any HelmChart or HelmChartConfig, so
   the two releases were moved with `helm` directly. That controller is the subject of homelab task
   530 (`#398`). **Ownership followed the same day**: the addon is disabled on the three servers, the
   packaged `HelmChart`/`HelmChartConfig` objects are gone, and the release is installed from
   `cluster/infrastructure/traefik/values.yaml` — `traefik-config.yaml` is deleted.
10. **Resolved 2026-11-18:** the two duplicate `lh-performance` StorageClass files under
    `cluster/infrastructure/storageclass/` are deleted with the rest of the Longhorn leftovers.
11. **Resolved 2026-11-18:** `firefly` and `postiz` were the two charts that rendered a
    `networking.k8s.io/v1` Ingress, against the Traefik-CRD-only rule in §4. Both were retired, so no
    chart in the repo breaks that rule.
12. **Resolved 2026-11-21:** the `local-path` **helper** image was `busybox:latest`; it is pinned to
    `1.38.0` with a `# renovate: datasource=docker` annotation in
    `cluster/helm/local-path/values.yaml`. One floating `latest` remains, `mktxp` (upstream publishes
    no usable semver tags; staying on `latest` was decided on 2026-11-19). `dashy` (`4.9.19`) and the
    minecraft `mc-monitor` sidecar (`1.21.0`) are pinned now. (`alpine:latest` in qBittorrent's
    VueTorrent installer was pinned on 2026-11-18: `vueTorrent.initTag` plus a
    `# renovate: datasource=docker` annotation.)

**Ingress**

13. All 65 live IngressRoutes and 82 repo files use `traefik.containo.us/v1alpha1`. Both CRD groups
    are installed; the v3 upgrade requires moving to `traefik.io/v1alpha1` while still on 2.11.
14. **Resolved for the monitoring charts**: `cluster/helm/monitoring/loki` is now a wrapper chart with
    an Application. **Resolved 2026-11-19**: the `cluster/helm/nfs/minio-nfs` orphan chart is deleted
    with the rest of the old NFS packaging.

**Version drift**

15. **Resolved 2026-11-21:** the Traefik Helm release was stuck at chart `20.3.1` / Traefik `2.9.4`
    (June 2023) because `traefik-config.yaml` never parsed. It now runs chart `25.0.3` / Traefik
    `2.10.7`, with CRDs from the same chart applied first, and the release is this repository's
    rather than the K3s addon's; the procedure, the ownership steps and the two blockers that shaped
    it are in [`docs/runbooks/traefik-release.md`](docs/runbooks/traefik-release.md). K3s `1.30.1`
    against a `1.36.x` stable is unchanged.
16. **Resolved for the monitoring charts:** the vendored `prometheus` 14.8.0 and `grafana` 6.52.5 are
    replaced by wrapper charts on current upstream versions. No vendored charts are to be added;
    migrate the remaining ones on their next touch.
17. `.github/renovate.json` is read from `main`, the default branch, so an edit merged to `develop`
    only takes effect after the next release lands on `main`. Its pull requests target `develop`.
    `ansible/` is covered (the edge Pis' compose stacks); `kubectl/**` and
    `cluster/infrastructure/**` are scanned by the Kubernetes manager but hold no version pin worth
    updating, the only image being a `latest` scratch pod. `docs/runbooks/renovate.md` has the
    manager-by-manager coverage.

## 8. Rules

- Never commit secrets, kubeconfigs or `*.pub` keys (see `.gitignore`).
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
  dashboards, the alert set and its known holes. Dashboards and alert rules live in Grafana's
  database, never in git — except the one snapshot under
  `cluster/helm/monitoring/grafana/export/traefik/`, which is a copy for review and restore and is
  applied by nothing.

### Configure through the API, not the operator's mouse

**Before asking a person to click something, look for a token.** App configuration, provider
cleanup, a blueprint re-apply and a sync trigger are all reachable over an HTTP API; handing them to
the operator wastes their turn and leaves no record of what changed.

| Target | Endpoint | Credential |
|---|---|---|
| Radarr, Sonarr, Lidarr, Prowlarr, Bazarr, Tautulli, Seerr, qBittorrent, autobrr, Kavita | each app's own REST API (`/api/v3/config/host` for the *arr settings, `/api/v1` on Prowlarr) | `<APP>_API_KEY` in `~/.config/secrets/homelab.env` |
| authentik | `https://authentik.kub.home.ashudev.com/api/v3/` — providers, applications, api tokens, blueprint apply | `AUTHENTIK_TOKEN` |
| n8n | `https://n8n.kub.home.ashudev.com/api/v1` — workflows, executions, credentials, tags; the key goes in the `X-N8N-API-KEY` header, not a bearer token | `N8N_API_KEY` |
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
