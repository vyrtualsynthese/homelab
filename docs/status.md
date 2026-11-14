# Application status — repaired, working, broken

**Snapshot: 2026-11-14.** 58 ArgoCD `Application` objects, 135 pods, read from the cluster through the
read-only ServiceAccount `kube-system/opencode-mcp`.

This is **not** a live view. It answers two questions at once — *is the app running?* and *has its
chart been rebuilt to the contract?* — and it is only true for the date above. Every other page in
`docs/` describes how things should be; this one says how they were on that day.

Before this page existed, the answer was split across three files and none of them was a status
board: `docs/charts/README.md` knew which charts had probes, `docs/audit/argocd-helm.md` knew which
references were broken on 2026-11-10, and `docs/observability-conventions.md` knew a few pod counts.
The audit in particular has already drifted — it still lists defects that were fixed on 2026-11-12.

## How to refresh it

Operator, with the normal kubeconfig:

```bash
kubectl get applications -n argocd \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'

kubectl get pods -A --no-headers | awk '$4!="Running" && $4!="Completed"'

kubectl get deploy,statefulset,daemonset -A
kubectl get pvc -A
```

An agent uses the same data through the `kubernetes` MCP server: `resources_list` for
`argoproj.io/v1alpha1` `Application`, `apps/v1` `Deployment`/`StatefulSet`/`DaemonSet`, `v1`
`PersistentVolumeClaim`, and `pods_list` for the pod table. The server is read-only and cannot print
Secrets.

**Refresh this page when a workstream lands.** The date at the top is the only claim it makes: if it
is older than the last merge on `develop`, distrust it rather than argue with it.

## Summary

| State | Count | Meaning |
|---|---|---|
| Repaired and working | 24 | Rebuilt against the chart contract, every replica ready |
| Working, not yet rebuilt | 24 | Running, but on a chart that predates the contract |
| Broken | 8 | No working replica; cause recorded below |
| ArgoCD cannot compare | 2 | The Application points at a path that does not exist |

50 of the 58 Applications have at least one working replica: the 24 repaired and 24 not yet rebuilt,
plus the two `ArgoCD cannot compare` provisioners, which still run. Six of the working ones are
sitting on a volume whose claim is stuck `Terminating` — they work today and will not survive the
Longhorn cleanup (see **Running on a borrowed volume**).

## Broken now (8)

| Application | Namespace | Replicas | Cause |
|---|---|---|---|
| `discourse` | discourse | 0/1 | Pending: claim `discourse2` not found |
| `koillection` | koillection | 0/1 | Pending: claim `koillection-conf` not found |
| `minecraft-server` | minecraft | 0/1 | Pending: claim `minecraft-lh-2` not found |
| `news-freshrss` | news | 0/1 | Pending: claim `freshrss-volume` not found |
| `postiz` | postiz | 0/1 | Pending: claim `postiz-uploads` not found (its Redis is up) |
| `monitorss` | monitorss | 0/2 | Mongo Pending on claim `monitorss-mongo`; the bot then crash-loops with no database (13 200 restarts). Web and Redis are up |
| `ghostfolio` | ghostfolio | 0/1 | Crash-loop, 19 569 restarts: resolves `postgres-postgresql.databases.svc.cluster.local`, the **retired Bitnami** Service name. The live one is `postgres.databases.svc.cluster.local` |
| `wiki` | wiki | 0/1 | Crash-loop, 18 510 restarts: same retired Bitnami hostname, `getaddrinfo ENOTFOUND` |

Two distinct causes, not eight: six apps lost a claim that nobody recreated, and two still point at
the PostgreSQL Service that W2 retired. W2's "point the consuming apps at the new instance" step is
unfinished for `ghostfolio` and `wiki`.

## Repaired to the contract (24)

Rebuilt against [`docs/charts/README.md`](charts/README.md) — pinned image tag, node pin,
`Recreate` where a config claim exists, probes, memory limits, hardening. All of these were 1/1 at
the snapshot.

| Application | Namespace | Rebuilt | Note |
|---|---|---|---|
| `autobrr` | media-stack | 2026-11-11 | The reference chart every other one was measured against |
| `flaresolverr` | media-stack | 2026-11-12 | Metrics listener and annotations from one values block |
| `prowlarr` | media-stack | 2026-11-12 | LinuxServer root-then-`PUID` phase kept; exportarr wired |
| `cross-seed` | media-stack | 2026-11-12 | Liveness only, deliberately: no endpoint reports its dependencies |
| `sonarr` | media-stack | 2026-11-12 | Exportarr port fixed; the old chart aimed it at 7878 instead of 8989 |
| `sonarr-anime` | media-stack | 2026-11-12 | Same shape, second library |
| `radarr` | media-stack | 2026-11-13 | Named `downloads`/`media` claims; 300 s startup budget for the migration |
| `radarr-anime` | media-stack | 2026-11-13 | Also moved off `PUID 0` |
| `lidarr` | media-stack | 2026-11-13 | New claim; exporter waits for the operator to paste the minted API key |
| `bazarr` | media-stack | 2026-11-13 | exportarr present and **off** — its collector walks the whole library per scrape |
| `bazarr-anime` | media-stack | 2026-11-13 | Same chart, second library |
| `recyclarr` | media-stack | 2026-11-13 | CronJob: no listener, no probes; exit status is the health signal |
| `tautulli` | media-stack | 2026-11-13 | LAN-only route, no TLS; readiness disabled for a documented reason |
| `seerr` | media-stack | 2026-11-13 | Rebuilt and hardened on its public route |
| `unpackerr` | media-stack | 2026-11-13 | Metrics deliberately off: v1 needs an API key for `/metrics` |
| `maintainerr` | media-stack | 2026-11-13 | First real dependency check: readiness runs `SELECT 1` and answers 503 |
| `calibre-web-automated` | media-stack | 2026-11-13 | New chart; replaced the retired `calibre` |
| `kavita` | media-stack | 2026-11-13 | Rebuilt from scratch; library claim mounted read-only |
| `postgres` | databases | 2026-11-13 | Own chart on the official image; Bitnami gone |
| `pgadmin` | databases | 2026-11-13 | Wrapper with a login; was one of the eleven Longhorn casualties |
| `authentik` | authentik | 2026-11-14 | Repaved on `2026.8.5` with one hostname, blueprints in `files/` and a PostSync worker reload; database and secrets rebuilt the same day |
| `prometheus` | monitoring | 2026-11-12 | Wrapper on the official chart, TSDB on `local-path` |
| `grafana` | monitoring | 2026-11-12 | Wrapper; rules and dashboards still live only in its database |
| `loki` | monitoring | 2026-11-12 | Wrapper, 90-day retention |

Two media-stack charts are documented but **not** rebuilt: `plex` (README plus recorded contract
exceptions) and `qbittorrent` (a DNS fix only). Both run.

## Running, not yet rebuilt (24)

Working, on the old chart shape. This is the queue for the chart work, not a defect list.

| Application | Namespace | Replicas | Note |
|---|---|---|---|
| `ashuvidz` | ashuvidz | 1/1 | |
| `dashy` | dashy | 1/1 | `latest` tag; OutOfSync |
| `filebrowser` | media-stack | 1/1 | **Borrowed volume**; OutOfSync |
| `firefly` | firefly | 3/3 | **Borrowed volume**; OutOfSync |
| `guacamole` | guacamole | 2/2 | OutOfSync |
| `linkstack` | linkstack | 1/1 | **Borrowed volume**; `latest` tag |
| `n8n` | n8n | 1/1 | **Borrowed volume**; OutOfSync |
| `syncthing` | syncthing | 1/1 | **Borrowed volume**; OutOfSync |
| `thelounge` | thelounge | 1/1 | **Borrowed volume** |
| `local-path` | default | 1/1 | OutOfSync; provisioner settings matter to every claim |
| `renovate` | renovate | CronJob | OutOfSync; Application and values disagreed on the chart version |
| `plex` | media-stack | 1/1 | Contract exceptions recorded in its README |
| `qbittorrent` | media-stack | 1/1 | DNS fix applied; `alpine:latest` in the completion hook |
| `qbittorrent-exporter` | media-stack | 1/1 | Now on `v2.0.4`; the previous image could not log in at all |
| `fluent-bit` | monitoring | 4/4 | DaemonSet, not pinned — it must run on every node |
| `mktxp` | monitoring | 1/1 | `latest` tag; OutOfSync |
| `pihole-exporter` | monitoring | 1/1 | |
| `prometheus-smartctl` | monitoring | 4/4 | DaemonSet |
| `books-media` | media-stack | 1/1 | NFS provisioner, wrapper chart |
| `nfs-qbittorrent-config` | media-stack | 1/1 | **Manages nothing** — see below; the running provisioner is a leftover |
| `nfs-qbittorrent-download` | media-stack | 1/1 | ArgoCD still reports `Progressing` |
| `nfs-syncthing` | syncthing | 1/1 | |
| `stash-nfs` | media-stack | 1/1 | ArgoCD still reports `Progressing` |
| `traefik-nfs` | kube-system | 1/1 | ArgoCD still reports `Progressing` |

## Running on a borrowed volume (6)

`firefly`, `linkstack`, `n8n`, `syncthing`, `thelounge` and `filebrowser` mount claims that are
already stuck `Terminating` — the Longhorn volumes the 2026 disk failure orphaned. Their pods keep
running because the volume is still attached and the deletion is blocked by a finalizer no driver can
remove any more.

The consequence matters for W0: clearing those finalizers **stops these six apps and destroys their
config**. Nine claims were stuck at the snapshot, not the eleven the plan recorded, because
`prometheus-data` and the old `pgadmin` claim have since been replaced. Do not treat "1/1 ready" as
"safe" for these six.

## ArgoCD cannot compare (2)

Both report `Healthy`, because the objects from their last good sync are still running. ArgoCD simply
cannot render them any more, so a sync would fail:

| Application | Error |
|---|---|
| `plex-media` | `cluster/helm/nfs/plex-media: app path does not exist` — the directory is `plexmedia` |
| `nfs-audio` | `helm template` fails on `$values/cluster/helm/nfs/audio/values.yaml` — the directory is `nfs-audio` |

Their provisioners still run, so the storage they serve is in use. The charts are effectively
unmanaged: fixing one defect at a time in the repo is the only way back.

## Drift, not breakage (10)

`dashy`, `discourse`, `filebrowser`, `firefly`, `guacamole`, `local-path`, `mktxp`, `n8n`,
`renovate`, `syncthing` are `OutOfSync` while `Healthy`. That means git and the cluster
disagree about something — often an image Renovate bumped, sometimes a UI edit. Not urgent; worth one
look each when its chart is touched.

## Faults this page does not fix

- `default/nfs-ssd-...-provisioner` has been `ContainerCreating` for 71 days, 0/1. The `nfs-ssd`
  StorageClass is therefore dead. W1's job.
- `nfs-qbittorrent-config` renders nothing: the Application uses `path:` on a directory that holds
  only `values.yaml` and `README.md`, so ArgoCD reports `Synced`/`Healthy` while managing no object.
  The provisioner pod that serves that class is a leftover from before the split.
- `nas /dev/sdf`, the SSD that holds every `local-path` claim, is at 0 % of its rated life. Tracked in
  `docs/observability-conventions.md`; it is the single largest risk to this page's "working" column.
- Backups still do not run. W4.

## Related

- [`charts/README.md`](charts/README.md) — what "rebuilt to the contract" means, one requirement at a time.
- [`audit/argocd-helm.md`](audit/argocd-helm.md) — the dated audit this page replaces as a status board.
- [`observability-conventions.md`](observability-conventions.md) — the alerts that watch these apps.
