# What is broken right now

> **Kind: snapshot.** Read on **Friday 20 November 2026, 01:10–01:40 CET** through the read-only
> ServiceAccount `kube-system/opencode-mcp`, plus the Grafana API for the one firing alert and the
> drive number below. Refreshed on **Saturday 21 November 2026, 00:25–01:00 CET** for the storage
> provisioners, the two drifted Applications, and the eight empty namespaces with the seven
> `Released` volumes, all cleared that night. On **Sunday 22 November** the K3s version claim below
> was refreshed after the move to `v1.31.14+k3s1`, and the last three orphan `local-path`
> directories on `nuc0` were removed; the counts in this paragraph are still the 20–21 November
> ones. This is
> **not a live view**: it is true for the moment named beside each section.

The short answer: **nothing is broken.** Every application that is meant to run has a ready replica,
both batch applications ran successfully, and every Application ArgoCD manages is `Synced`. One
Application is `Progressing`: a Minecraft server switched off in its own values file.

**Refresh this page when a workstream lands.** The date at the top is the only claim it makes: if it
is older than the last merge on `develop`, distrust it rather than argue with it.

## What this page answers, and what it stopped answering

It answers *is the app running?* for the morning above, from the cluster itself.

It no longer answers *has its chart been rebuilt to the contract?* That question is a fact about
git, not about the cluster, and copying it into a dated snapshot is how the previous version came to
list apps as broken months after they were fixed. The answer now lives in two living pages:
[`charts/README.md`](charts/README.md) is the contract and its per-chart table, and
[`reference/chart-notes.md`](reference/chart-notes.md) is the per-app record. This page carries only
what a read of the cluster can prove.

## How to check right now

Operator, with the normal kubeconfig:

```bash
kubectl get applications -n argocd \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'

kubectl get pods -A --no-headers | awk '$4!="Running" && $4!="Completed"'

kubectl get deploy,statefulset,daemonset -A
kubectl get pvc -A
kubectl get pv | grep Terminating
```

`kubectl get pvc -A` today prints one `Pending` claim, `minecraft/minecraft-server-data`, which binds
only when a pod asks for it. `kubectl get pv | grep Terminating` prints nothing: the four volumes that
carried a deletion mark since 2023 were rebuilt on 2026-11-21 (see **Storage**).

Grafana's **Homelab** folder holds nine dashboards. These four answer most of the questions above:

| Dashboard | Use it for |
|---|---|
| **Homelab · Health** | First look: pods and targets needing attention, crash loops, restarts, firing alerts |
| **Homelab · Applications** | One namespace in detail: replicas, CPU, memory, restarts, claims, dead exporters |
| **Homelab · Logs** | Errors and warnings by container, and the live stream |
| **Homelab · Disks** | Drive life and temperature, including the `nas` SSD below |

An agent reads the same data through the `kubernetes` MCP server: `resources_list` for
`argoproj.io/v1alpha1` `Application`, `apps/v1` `Deployment`/`StatefulSet`/`DaemonSet`, `v1`
`PersistentVolumeClaim`/`PersistentVolume`, and `pods_list` for the pod table. The server is
read-only and cannot print Secrets.

## Summary

What the snapshot saw: **56 ArgoCD `Application` objects**, 55 of which are application workloads
(one of them, `traefik-gate`, renders no pod) plus `argocd-root`, the root application that syncs the
rest. `argocd` itself is one of the 55: since 2026-11-19 the install is a wrapper chart in
`cluster/helm/argocd`, synced like every other app. Behind them, 63 Deployments, 3 StatefulSets,
17 DaemonSets, 47 claims, 5 CronJobs and 120 pods (105 running, 15 finished Job pods), on four nodes
all at `v1.31.14+k3s1` (moved from `v1.30.1+k3s1` on 21–22 November).

Twenty of those pods sit in three namespaces no Application manages: `gpu-operator`,
`node-feature-discovery` and `system-upgrade`. They run and they are not part of the GitOps flow —
[`audit/ansible-hosts.md`](audit/ansible-hosts.md) records the history.

| State | Count | Meaning |
|---|---|---|
| Running | 51 | At least one ready replica |
| Batch | 2 | `renovate` and `recyclarr`: CronJobs with no long-running pod; the last run succeeded |
| Switched off by choice | 1 | `minecraft-server`, `replicaCount: 0` in its values |
| Middlewares only | 1 | `traefik-gate` renders no pod; it defines shared Traefik objects |
| Broken | 0 | |
| **Total** | **55** | plus `argocd-root` |

## Broken now (0)

Nothing. The five applications the previous snapshot called broken — `discourse`, `ghostfolio`,
`monitorss`, `postiz` and `wiki` — were retired, `minecraft-server` is off by choice, and
`guacamole` and `linkstack` were retired while still running.

## Working (51)

Replicas are the live `Ready` figure at the read. Every one of these is `Synced` except the two
rows that say otherwise.

| Application | Namespace | Replicas | Note |
|---|---|---|---|
| `argocd` | argocd | 7 workloads, 1/1 each | Argo CD itself: server, repo server, controller, applicationset, notifications, dex, redis |
| `ashuvidz` | ashuvidz | 1/1 | The `ashuvidz.com` site and its git sidecar |
| `authentik` | authentik | 1/1 + 1/1 | Server and worker |
| `dashy` | dashy | 1/1 | |
| `pgadmin` | databases | 1/1 | |
| `postgres` | databases | 1/1 | Bootstrap Job carries the image tag in its name, so a bump replaces it |
| `local-path` | default | 1/1 | Ignores the one field this API server drops; its claim settings matter to every `local-path` claim |
| `docs-site` | docs | 1/1 | Serves the docs; the builder CronJob ran a minute before the read |
| `filebrowser` | filebrowser | 1/1 | |
| `koillection` | koillection | 1/1 | Rebuilt from the broken shape on 2026-11-19 |
| `traefik-nfs` | kube-system | 1/1 | The `traefik-nfs` class; volume rebuilt on 2026-11-21 |
| `autobrr` | media-stack | 1/1 | |
| `bazarr` | media-stack | 1/1 | |
| `bazarr-anime` | media-stack | 1/1 | |
| `books-media` | media-stack | 1/1 | The `nfs-books` class |
| `calibre-web-automated` | media-stack | 1/1 | |
| `cross-seed` | media-stack | 1/1 | |
| `flaresolverr` | media-stack | 1/1 | |
| `kavita` | media-stack | 1/1 | |
| `lidarr` | media-stack | 1/1 | |
| `maintainerr` | media-stack | 1/1 | |
| `nfs-audio` | media-stack | 1/1 | The `nfs-audio` class |
| `nfs-qbittorrent-config` | media-stack | 1/1 | Renders its provisioner from the wrapper chart — the previous snapshot's "manages nothing" no longer applies |
| `nfs-qbittorrent-download` | media-stack | 1/1 | The downloads queue class; volume rebuilt on 2026-11-21 |
| `plex` | media-stack | 1/1 | |
| `plex-media` | media-stack | 1/1 | The film and television class; volume rebuilt on 2026-11-21 |
| `prowlarr` | media-stack | 1/1 | |
| `qbittorrent` | media-stack | 1/1 | With its WireGuard sidecar |
| `qbittorrent-exporter` | media-stack | 1/1 | |
| `radarr` | media-stack | 1/1 | |
| `radarr-anime` | media-stack | 1/1 | |
| `seerr` | media-stack | 1/1 | |
| `sonarr` | media-stack | 1/1 | |
| `sonarr-anime` | media-stack | 1/1 | |
| `stash-nfs` | media-stack | 1/1 | The `stash-nfs` class; volume rebuilt on 2026-11-21 |
| `tautulli` | media-stack | 1/1 | |
| `unpackerr` | media-stack | 1/1 | |
| `alert-relay` | monitoring | 1/1 | |
| `fluent-bit` | monitoring | 4/4 | DaemonSet, not pinned — it must run on every node |
| `grafana` | monitoring | 1/1 | |
| `loki` | monitoring | 1/1 | |
| `mktxp` | monitoring | 1/1 | `latest` tag, kept by decision on 2026-11-19: upstream publishes no usable semver tag |
| `pihole-exporter` | monitoring | 1/1 | |
| `prometheus` | monitoring | 1/1 | Plus kube-state-metrics and node-exporter |
| `prometheus-smartctl` | monitoring | 4/4 | DaemonSet: drive health on every node |
| `n8n` | n8n | 1/1 | Rebuilt on `local-path` on 2026-11-19 |
| `news-freshrss` | news | 1/1 | Rebuilt against the contract on 2026-11-19, with native OIDC |
| `nfs-syncthing` | syncthing | 1/1 | The `nfs-syncthing` class |
| `syncthing` | syncthing | 1/1 | Rebuilt on `local-path` on 2026-11-19; carries the backup CronJob |
| `thelounge` | thelounge | 1/1 | Rebuilt on `local-path` on 2026-11-19 |
| `vikunja` | vikunja | 1/1 | |

## ArgoCD reports Progressing (1)

| Application | Why |
|---|---|
| `minecraft-server` | `replicaCount: 0`: nothing runs, so nothing ever becomes healthy. Its claim is `Pending` for the same reason — `local-path` binds a claim only when a pod asks for it |

## Switched off by choice (1)

`minecraft-server` is at 0 replicas in `cluster/helm/minecraft/minecraft-server/values.yaml`. The
Application still syncs; the pod is simply not wanted. Turning it back on is a values change plus a
sync, not a repair.

## Drift, not breakage (0)

Nothing sits `Healthy` and `OutOfSync`. The two that did were settled on 2026-11-21 rather than
accepted: `local-path` ignores `/spec/template/spec/hostUsers`, which this API server drops from the
stored Deployment, and the `postgres` bootstrap Job is named after the digest of its whole pod
template, so an image bump or any other template edit replaces it instead of failing on an immutable
pod template.

## Storage

All 47 claims are `Bound` except `minecraft/minecraft-server-data` (`Pending`, see above). **No
claim is `Terminating`**: the Longhorn cleanup finished on 2026-11-19, and `syncthing`, `thelounge`
and `n8n` were rebuilt on `local-path` before their old claims were cleared.

`kubectl get pv` shows no `Terminating` volume: the four that carried a deletion mark since
2023-12-06 — `pv-traefik-nfs`, `pv-plex-media`, `pv-stash-nfs-nfs-stash` and
`pv-nfs-qbittorrent-download-nfs-subdir-external-provisioner` — were rebuilt on **2026-11-21**. Each
claim was deleted, the volume's pending deletion completed, and self-heal recreated the pair from the
provisioner chart. The data stayed on `nas` throughout: every volume is `Retain`, no pod restarted,
and `acme.json` was byte-identical afterwards. The order, the check and why the mark could not
simply be cleared are in [`how-it-works/storage.md`](how-it-works/storage.md).

No `Released` volume remains. The six from the previous read (`plex-transcode`, `cross-seed2`, the
old `calibre-web-automated-config`, the old `pgadmin-config`, `filebrowser-config` and
`filebrowser-database`) and `atlantis-data`, which appeared on 2026-11-20 after its namespace was
already gone, were deleted on 2026-11-21, oldest first. `local-path` uses retain policy, so deleting
a volume leaves its directory behind instead of freeing the space: `/mnt/ssd0` on `streaming` for the
three media-stack volumes, `/opt/local-path-provisioner` on `nuc0` for the rest. Those seven
directories were removed by hand the same day, 0.7 MiB between them, which is all they held.
Three older directories on `nuc0` that no volume or claim had ever explained — 1.6 MiB between
them — followed on 2026-11-22, after a `du -sh` and a read of every PV path confirmed nothing used
them.

Each of the eight classes is a directory on a `nas` export, served by one release of
`cluster/helm/nfs/provisioner/`. `nfs-ssd` and `nfs-plex` no longer exist as classes or as
provisioners, and the `nfs-ssd` `ContainerCreating` pod the previous snapshot recorded is gone with
them.

## Faults that are not an application

Checked against the cluster, or explicitly marked as not visible to it:

- **`nas /dev/sdf` is at 0 % of its rated life.** It is a `CT1000BX500SSD1`, it holds the
  `local-path` claims of the apps pinned to `nas`, and Grafana's `DriveNeedsReplacement` rule is
  **firing** for it: `smartprom_percent_lifetime_remain` reads 0, where the only other two drives
  that report the number read 92 and 85. This is the single largest risk to the "working" column
  above.
- **Backups.** The dumps and the offsite copies are one system now: `postgres-dump` writes the
  dump, and the `backup` namespace's six CronJobs ship it, the claims, the NFS exports and the etcd
  copies to one OVH bucket nightly. The old host-side paths in
  [`audit/ansible-hosts.md`](audit/ansible-hosts.md) are retired. What is not proved is a restore.
- **Kubernetes version.** All four nodes are on `v1.31.14+k3s1` with containerd `2.1.5-k3s1.32`,
  moved from `1.30.1` on 21–22 November with the licence-free path in
  [`runbooks/k3s-upgrade.md`](runbooks/k3s-upgrade.md). 1.32 and 1.33 are the next steps (cards 520
  and 521), and nothing about them has started.
- **Alert rules.** 132 rules exist in the Homelab folder: 1 firing (the drive above), 104 `Normal`,
  27 `Normal (NoData)`.

## Retired since the previous snapshot

`firefly`, `ghostfolio`, `guacamole`, `linkstack`, `monitorss`, `postiz` and `wiki` were removed from
the cluster and from git on 2026-11-18 — charts, `Application` files and namespaces — and
`discourse` followed on 2026-11-19. None of them appears in any list above. The reasoning is
[`decisions/2026-11-18-retire-seven-applications.md`](decisions/2026-11-18-retire-seven-applications.md)
and [`decisions/2026-11-19-retire-discourse.md`](decisions/2026-11-19-retire-discourse.md);
[`how-it-works/applications.md`](how-it-works/applications.md) carries the inventory as it stands.

## What changed since 17 November

- The page said 63 Applications; the cluster holds 56. Eight were retired, `argocd` became one more
  managed workload, and `freshrss` was rebuilt into a `news` namespace.
- The six "broken" applications from the previous page are gone or fixed, and the three
  "ArgoCD cannot compare" entries are gone: `plex-media` and `nfs-audio` are `Synced`, and the
  paths they pointed at are the wrapper chart.
- `nfs-ssd` and its 75-day `ContainerCreating` pod are gone; the NFS classes are one chart with
  eight releases.
- The Longhorn claims are gone with the pods rebuilt, and so is every remaining deletion mark: on
  2026-11-21 the four NFS volumes that had carried one since 2023 were rebuilt.
- The eight namespaces left behind by retired applications (`inventree`, `inventree-lego`, `minio`,
  `nextcloud`, `orbital-sync`, `postfix`, `synapse`, `test`) and the seven `Released` volumes were
  deleted on 2026-11-21. Their `local-path` directories are still on the nodes.
- Pods fell from 149 to 120 at the read, and claims from 50 to 47.
- The docs builder, which failed three runs on 2026-11-17, has run cleanly since: its runs complete
  and the CronJob fires every minute.
- Backups became a cluster workload: six CronJobs in the `backup` namespace, four restic repositories
  in one OVH bucket, a nightly verifier and a `BackupStreamStale` rule. The first restores followed
  on 21–22 November — three read-only ones from the cluster and all four repositories read from a
  laptop with no cluster at all — and the drill log in
  [`runbooks/restore.md`](runbooks/restore.md) is where those rows live.

## Related

- [`how-it-works/applications.md`](how-it-works/applications.md) — every Application, its namespace,
  its node and how its chart is consumed.
- [`charts/README.md`](charts/README.md) — what "rebuilt to the contract" means, and the per-chart
  record of who meets it.
- [`audit/argocd-helm.md`](audit/argocd-helm.md) — the dated audit this page replaces as a status
  board.
- [`observability-conventions.md`](observability-conventions.md) — the dashboards, the alert set and
  its known holes.
- [`how-it-works/storage.md`](how-it-works/storage.md) — the classes, the claims, and the rebuild
  procedure for a volume that carries a deletion mark.
