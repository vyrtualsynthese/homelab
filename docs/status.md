# What is broken right now

> **Kind: snapshot.** Read on **Tuesday 17 November 2026, 17:45–17:55 CET** through the read-only
> ServiceAccount `kube-system/opencode-mcp`. This is **not a live view**: it is true for that
> evening and for nothing after it.

The page answers two questions at once — *is the app running?* and *has its chart been rebuilt to the
contract?* — and it answers them only for the date above. Every other page in `docs/` describes how
things should be; this one says how they were on that day.

What it replaces: before this page existed, the answer was split across three files, none of them a
status board. `docs/charts/README.md` knew which charts had probes,
`docs/audit/argocd-helm.md` knew which references were broken on 2026-11-10, and
`docs/observability-conventions.md` knew a few pod counts. The audit has already drifted — it still
lists defects that were fixed on 2026-11-12.

**Refresh this page when a workstream lands.** The date at the top is the only claim it makes: if it
is older than the last merge on `develop`, distrust it rather than argue with it.

## Retired after this snapshot

`firefly`, `ghostfolio`, `guacamole`, `linkstack`, `monitorss`, `postiz` and `wiki` were removed from
the cluster and from git on 2026-11-18 — charts, `Application` files and namespaces. Four of them
appear below as broken and three as running; **nothing on this page's lists applies to those seven any
more**, and their rows are the 17 November state, not a queue to pick up. The reasoning is
[`decisions/2026-11-18-retire-seven-applications.md`](decisions/2026-11-18-retire-seven-applications.md);
[`how-it-works/applications.md`](how-it-works/applications.md) and
[`reference/routes.md`](reference/routes.md) carry the lists as they stand today.

## How to check right now

Operator, with the normal kubeconfig:

```bash
kubectl get applications -n argocd \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'

kubectl get pods -A --no-headers | awk '$4!="Running" && $4!="Completed"'

kubectl get deploy,statefulset,daemonset -A
kubectl get pvc -A
```

Every `Terminating` row the last command prints is expected: the driver that would clear it is gone.
Clearing a finalizer by hand destroys the volume and the application still running on it — the ones
the page names under **Running on a borrowed volume**.

Four dashboards answer most of it without a shell, all in Grafana's **Homelab** folder:

| Dashboard | Use it for |
|---|---|
| **Homelab · Health** | First look: pods and targets needing attention, crash loops, restarts, firing alerts |
| **Homelab · Applications** | One namespace in detail: replicas, CPU, memory, restarts, claims, dead exporters |
| **Homelab · Logs** | Errors and warnings by container, and the live stream |
| **Homelab · Disks** | Drive life and temperature, including the `nas` SSD below |

An agent reads the same data through the `kubernetes` MCP server: `resources_list` for
`argoproj.io/v1alpha1` `Application`, `apps/v1` `Deployment`/`StatefulSet`/`DaemonSet`, `v1`
`PersistentVolumeClaim`, and `pods_list` for the pod table. The server is read-only and cannot print
Secrets.

## Summary

What the snapshot saw: **63 ArgoCD `Application` objects**, which are **62 applications plus
`argocd-root`** (the root app is not an application workload). Behind them, 149 pods, 80
Deployments, 5 StatefulSets, 17 DaemonSets, 50 PersistentVolumeClaims, 5 CronJobs, 4 nodes, all on
`v1.30.1+k3s1`.

| State | Count | Meaning |
|---|---|---|
| Repaired to the contract | 28 | Rebuilt against the chart contract, every replica ready |
| Running, not yet rebuilt | 23 | Running, but on a chart that predates the contract |
| Broken | 8 | No working replica; cause recorded below |
| ArgoCD cannot compare | 2 | The Application points at a path that does not exist |
| Middlewares only | 1 | Renders no pod; defines shared Traefik objects |
| **Total** | **62** | |

53 of the 62 applications have at least one working replica: the 28 repaired, the 23 not yet
rebuilt, and the two provisioners ArgoCD cannot compare but still run. `traefik-gate` renders no pod
at all and is counted apart. Four of the working ones sit on a volume whose claim is stuck
`Terminating` — they work today and will not survive the Longhorn cleanup (see **Running on a
borrowed volume**).

## Broken now (8)

| Application | Namespace | Replicas | Cause |
|---|---|---|---|
| `discourse` | discourse | 0/1 | Pending: claim `discourse2` not found |
| `koillection` | koillection | 0/1 | Pending: claim `koillection-conf` not found |
| `minecraft-server` | minecraft | 0/1 | Pending: claim `minecraft-lh-2` not found |
| `news-freshrss` | news | 0/1 | Pending: claim `freshrss-volume` not found |
| `postiz` | postiz | 0/1 | Pending: claim `postiz-uploads` not found. Its Redis is up, but that claim (`redis-data-postiz-redis-master-0`) is now stuck `Terminating` too, so the pair would not survive a restart |
| `monitorss` | monitorss | 0/2 | The Mongo pod is Pending on claim `monitorss-mongo`; the bot then crash-loops with no database, more than ten thousand restarts. Web and Redis are up |
| `ghostfolio` | ghostfolio | 0/1 | Crash-loop, more than ten thousand restarts: resolves `postgres-postgresql.databases.svc.cluster.local`, the **retired Bitnami** Service name. The live one is `postgres.databases.svc.cluster.local` |
| `wiki` | wiki | 0/1 | Crash-loop, more than ten thousand restarts: same retired Bitnami hostname, `getaddrinfo ENOTFOUND` |

Two distinct causes, not eight: six apps lost a claim that nobody recreated, and two still point at
the PostgreSQL Service that W2, the PostgreSQL workstream, retired. W2's "point the consuming apps at
the new instance" step is unfinished for `ghostfolio` and `wiki`.

## Repaired to the contract (28)

Rebuilt against [`docs/charts/README.md`](charts/README.md) — pinned image tag, node pin, `Recreate`
where a config claim exists, probes, memory limits, hardening. All of these were ready at the
snapshot.

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
| `tautulli` | media-stack | 2026-11-13 | LAN-only route, TLS since 2026-11-15; no gate, its own login is the gate; readiness disabled for a documented reason |
| `seerr` | media-stack | 2026-11-13 | Rebuilt and hardened on its public route |
| `unpackerr` | media-stack | 2026-11-13 | Metrics deliberately off: v1 needs an API key for `/metrics` |
| `maintainerr` | media-stack | 2026-11-13 | First real dependency check: readiness runs `SELECT 1` and answers 503 |
| `calibre-web-automated` | media-stack | 2026-11-13 | New chart; replaced the retired `calibre` |
| `kavita` | media-stack | 2026-11-13 | Rebuilt from scratch; library claim mounted read-only |
| `postgres` | databases | 2026-11-13 | Own chart on the official image; Bitnami gone |
| `pgadmin` | databases | 2026-11-13 | Wrapper with a login; was one of the eleven Longhorn casualties |
| `authentik` | authentik | 2026-11-14 | Repaved on `2026.8.5` with one hostname, blueprints in `files/` and a PostSync worker reload; database and secrets rebuilt the same day. Sign-in sources: Discord (members of the homelab guild only — verified refused for a non-member) and Plex (accounts with access to AshuFlex), both landing on the same account. Access model: `authentik Admins` / `homelab-users` / `homelab-guests`, bound per application, with `filebrowser-editors` as a capability group; MFA optional (TOTP, WebAuthn) and applied to source logins. Applications actually wired to it: Grafana and pgAdmin, plus Vikunja natively since 2026-11-16 — its chart sets its own `VIKUNJA_AUTH_OPENID_PROVIDERS_AUTHENTIK_*` values. Argo CD, Kavita and FileBrowser have authentik objects in git but no application-side configuration yet |
| `filebrowser` | filebrowser | 2026-11-14 | Wrapper chart on the official upstream image; local login on `v2.63.23`; own namespace; authentik deferred; upstream archived 2026-09-01 |
| `prometheus` | monitoring | 2026-11-12 | Wrapper on the official chart, its time-series database on `local-path` |
| `grafana` | monitoring | 2026-11-12 | Wrapper; rules and dashboards still live only in its database |
| `loki` | monitoring | 2026-11-12 | Wrapper, 90-day retention |
| `vikunja` | vikunja | 2026-11-16 | Wrapper on `vikunja/vikunja` 2.8.0, database on the rebuilt `postgres`, no local password: the only way in is authentik single sign-on (OIDC) |
| `docs-site` | docs | 2026-11-16 | Wrapper: an nginx pod serving the rendered docs and a builder CronJob every minute on `mkdocs-material` 9.8.1, its own claim |
| `alert-relay` | monitoring | 2026-11-16 | One small Python process relaying Grafana alerts into board comments, code in a ConfigMap so there is no image to build; on `nas` |

Two media-stack charts are documented but **not** rebuilt: `plex` (README plus recorded contract
exceptions) and `qbittorrent` (a DNS fix only). Both run.

## Running, not yet rebuilt (23)

Working, on the old chart shape. This is the queue for the chart work, not a defect list.

| Application | Namespace | Replicas | Note |
|---|---|---|---|
| `ashuvidz` | ashuvidz | 1/1 | |
| `dashy` | dashy | 1/1 | `latest` tag; OutOfSync |
| `firefly` | firefly | 3/3 | OutOfSync; now on the `Bound` claim `firefly-config-2` |
| `guacamole` | guacamole | 2/2 | OutOfSync |
| `linkstack` | linkstack | 1/1 | **Borrowed volume**; `latest` tag |
| `n8n` | n8n | 1/1 | **Borrowed volume**; OutOfSync |
| `syncthing` | syncthing | 1/1 | **Borrowed volume**; OutOfSync |
| `thelounge` | thelounge | 1/1 | **Borrowed volume**; OutOfSync |
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

The three provisioners marked `Progressing` (`nfs-qbittorrent-download`, `stash-nfs`, `traefik-nfs`)
are reported that way while serving their claims normally. That state is ArgoCD's, not a fault.

## Running on a borrowed volume

`linkstack`, `n8n`, `syncthing` and `thelounge` mount claims that are already stuck `Terminating` —
the Longhorn volumes the 2026 disk failure orphaned. Their pods keep running because the volume is
still attached and the deletion is blocked by a finalizer no driver can remove any more.

The consequence matters for W0, the volume-cleanup workstream: clearing those finalizers **stops
these apps and destroys their config**. Do not treat "1/1 ready" as "safe" for them. Twelve
claims were stuck `Terminating` at the snapshot, not the nine the previous page recorded; `firefly`
has since moved to a `Bound` claim and left this list. `kubectl get pvc -A` shows the current set.

## ArgoCD cannot compare (2)

Both report `Healthy`, because the objects from their last good sync are still running. ArgoCD simply
cannot render them any more, so a sync would fail:

| Application | Error |
|---|---|
| `plex-media` | `cluster/helm/nfs/plex-media: app path does not exist` — the directory is `plexmedia` |
| `nfs-audio` | `helm template` fails on `$values/cluster/helm/nfs/audio/values.yaml` — the directory is `nfs-audio` |

Their provisioners still run, so the storage they serve is in use. The charts are effectively
unmanaged: fixing one defect at a time in the repo is the only way back.

## Middlewares only (1)

| Application | Namespace | Sync / health | Note |
|---|---|---|---|
| `traefik-gate` | kube-system | Synced / Healthy | No pod of its own: carries the shared Traefik objects, not an app |

`traefik-gate` creates nothing to run: it carries the shared Traefik objects every other chart's
route refers to by name — the `lan-only` IP gate, the `redirect-to-https` middleware and the
`internal-tls` TLS policy for `*.kub.local`. The authentik gate middlewares are not in it; they live
under `cluster/infrastructure/` and are applied by hand. It is counted apart from the running apps
because there is nothing to run.

## Drift, not breakage (10)

`dashy`, `firefly`, `guacamole`, `linkstack`, `local-path`, `mktxp`, `n8n`, `renovate`, `syncthing`
and `thelounge` are `OutOfSync` while `Healthy`. That means git and the cluster disagree about
something — often an image Renovate bumped, sometimes a UI edit. Not urgent; worth one look each
when its chart is touched.

## Faults this page does not fix

- `default/nfs-ssd-...-provisioner` has been `ContainerCreating` for 75 days, 0/1 (71 days on the
  previous page). The `nfs-ssd` StorageClass is therefore dead. W1, the storage workstream, owns it.
- `nfs-qbittorrent-config` renders nothing: the Application uses `path:` on a directory that holds
  only `values.yaml` and `README.md`, so ArgoCD reports `Synced`/`Healthy` while managing no object.
  The provisioner pod that serves that class is a leftover from before the split.
- `nas /dev/sdf`, the SSD that holds the `local-path` claims of the apps pinned to `nas`, is at 0 %
  of its rated life. Tracked in
  [`observability-conventions.md`](observability-conventions.md); it is the single largest risk to
  this page's "working" column.
- The docs-site builder failed three CronJob runs on 2026-11-17 — `Permission denied` while writing
  the staging landing page — after which the last three runs completed. Watched, not counted as a
  broken application.
- Backups still do not run. W4, the backups workstream.
- All four nodes are on `v1.30.1+k3s1`, behind the current stable Kubernetes. The upgrade is W5, the
  Kubernetes upgrade workstream, and it has not started.

## Related

- [`charts/README.md`](charts/README.md) — what "rebuilt to the contract" means, one requirement at a time.
- [`audit/argocd-helm.md`](audit/argocd-helm.md) — the dated audit this page replaces as a status board.
- [`observability-conventions.md`](observability-conventions.md) — the dashboards and alerts that watch these apps.
- [`runbooks/docs-site-first-run.md`](runbooks/docs-site-first-run.md) — the builder whose failed runs are recorded above.
