# Applications, by namespace

> **In one line.** Every ArgoCD Application this repository declares: its namespace, where its pod last
> ran, how its chart is consumed, and one plain line on what it is.
>
> **Use it when** you are looking for the app behind a hostname, adding one, or wondering which
> machine an application belongs to · **Verified** 2026-11-22 · **Kind:** reference
>
> **Sources** `cluster/argocd/projects/**/*.yaml`, the live cluster read on 2026-11-22, and each
> chart's `values.yaml`

This is a snapshot of the repository and of the cluster on 2026-11-22. Pod health and claim state move
daily, and no page keeps up with them: `kubectl get pods -A` and ArgoCD's application list are the
live answer, and [Every day](../running-it/every-day.md) is the sweep that reads them.
Two rows were refreshed on 2026-11-20: `koillection` is scheduled again, and `minecraft-server` now
renders its workload at `0` replicas by decision rather than waiting on a claim. One more on
2026-11-25: `qbittorrent` runs on `nas`, where its downloads are a local path.

## How to read the tables

The Application column is the ArgoCD name, which is also the file name under
`cluster/argocd/projects/`. **Node** is where the pod last ran, read from the live cluster: it is
placement, not the chart's intent, so the two can disagree — see the `node-type` section below.
`Pending` means nothing has been scheduled. **Chart** is one letter:

- **A** — our own chart, templates and all, under `cluster/helm/`.
- **B** — an upstream chart, unmodified, plus a `values.yaml` of ours.
- **C** — an upstream chart committed whole and edited in place.
- **D** — a small chart of ours that depends on the upstream chart and pins its image.

A chart can be both A and D: `ashuvidz` depends on the upstream `nginx` chart and adds its own route
templates, so it is marked **D**. A `—` in Node means no pod is running: either the Application
renders no workload of its own (`traefik-gate`) or it is held at `0` replicas on purpose
(`minecraft-server`). Every Application here now declares a chart.

What those four patterns cost you when you change something is [gitops.md](gitops.md), one page.

Addresses, access gates and TLS are deliberately absent: they are [reference/routes.md](../reference/routes.md)
and [reference/sso-applications.md](../reference/sso-applications.md), so nothing is written twice.

Two media words, once: the ***arr apps*** are Sonarr, Radarr and Lidarr, the tools that fetch and file
media, and an *indexer* is a site that lists releases and their download links. The pipeline they
belong to is [media.md](media.md).

## The applications

| Application | Namespace | Node | Chart | What it is |
|---|---|---|---|---|
| `argocd` | `argocd` | nas | D | the GitOps controller: it reads this repository and makes the cluster match it |
| `ashudev` | `ashudev` | nuc0 | A | the `ashudev.com` site: the nginx that serves it, its `www` redirect and its hostnames |
| `ashuvidz` | `ashuvidz` | nuc0 | A | the `ashuvidz.com` site: the nginx that serves it, the git sidecar that refreshes the content, its hostnames and its redirect |
| `authentik` | `authentik` | nuc0 | D | the identity provider: one sign-in for the gated apps |
| `backup` | `backup` | nas | A | the six restic CronJobs: one stream per node and one for the NFS export, plus verify and prune |
| `dashy` | `dashy` | nuc0 | D | the start page: links to every application |
| `pgadmin` | `databases` | nuc0 | D | the web console for the shared PostgreSQL |
| `postgres` | `databases` | nas | A | the shared PostgreSQL that holds the other apps' databases |
| `docs-site` | `docs` | nuc0 | A | every repository's `docs/` folder, built on a schedule and served read-only |
| `filebrowser` | `filebrowser` | nuc0 | A | web file browser over the media shares, FileBrowser Quantum, sign-in through authentik |
| `gpu-operator` | `gpu-operator` | streaming | D | the NVIDIA stack: driver, device plugin and GPU metrics |
| `koillection` | `koillection` | nuc0 | A | catalogue for a collection of things |
| `autobrr` | `media-stack` | streaming | A | grabs releases from IRC announcements, without a search |
| `bazarr` | `media-stack` | streaming | A | subtitles for the television and film libraries |
| `bazarr-anime` | `media-stack` | streaming | A | the same for the anime library |
| `calibre-web-automated` | `media-stack` | streaming | A | ingests, converts and serves the books library |
| `cross-seed` | `media-stack` | streaming | A | offers finished torrents on a second tracker |
| `flaresolverr` | `media-stack` | streaming | A | passes the browser challenges the indexers put in the way |
| `kavita` | `media-stack` | streaming | A | reads the books library Calibre-Web Automated writes |
| `lidarr` | `media-stack` | streaming | A | music library manager |
| `maintainerr` | `media-stack` | streaming | A | removes library items when a rule says so |
| `plex` | `media-stack` | streaming | A | playback; video conversion on the GPU |
| `prowlarr` | `media-stack` | streaming | A | the one indexer list the *arr apps share |
| `qbittorrent` | `media-stack` | nas | A | the download client, with its VPN sidecar |
| `qbittorrent-exporter` | `media-stack` | streaming | A | Prometheus metrics for qBittorrent |
| `radarr` | `media-stack` | streaming | A | film library manager |
| `radarr-anime` | `media-stack` | streaming | A | Radarr for the anime library |
| `recyclarr` | `media-stack` | nuc0 | A | daily: quality profiles and naming into the *arr apps |
| `seerr` | `media-stack` | streaming | A | the request page for family and friends |
| `sonarr` | `media-stack` | streaming | A | television library manager |
| `sonarr-anime` | `media-stack` | streaming | A | Sonarr for the anime library |
| `tautulli` | `media-stack` | streaming | A | Plex viewing statistics |
| `unpackerr` | `media-stack` | streaming | A | extracts packed downloads so the *arr apps can import them |
| `minecraft-server` | `minecraft` | — | A | a Minecraft Java server, held at `0` replicas until its anti-cheat, anti-grief and backup work lands |
| `alert-relay` | `monitoring` | nas | A | turns Grafana alerts into cards on the alerts board |
| `blackbox-exporter` | `monitoring` | nas | D | probes a URL from outside and reports whether it answered, and with which status |
| `grafana` | `monitoring` | nas | D | the dashboards |
| `loki` | `monitoring` | nas | D | the log store Fluent Bit writes into |
| `mktxp` | `monitoring` | nuc0 | A | metrics from the MikroTik router |
| `pihole-exporter` | `monitoring` | nuc0 | A | metrics from Pi-hole |
| `prometheus` | `monitoring` | nas | D | the metrics database and its scrape configuration |
| `snapraid-exporter` | `monitoring` | nas | A | the SnapRAID runner's last-success timestamp, served as a metric |
| `n8n` | `n8n` | nuc0 | A | workflow automation |
| `news-freshrss` | `news` | nuc0 | A | RSS reader, on a claim the chart creates |
| `renovate` | `renovate` | nas | D | nightly pull requests that bump dependencies |
| `crowdsec` | `security` | nas | D | attack detection: reads the router's access log on `streaming`, keeps the decisions on `nas`; the banning is a host service, see the CrowdSec runbook |
| `crowdsec-web-ui` | `security` | Pending | A | second view of those decisions and alerts, with the buttons to undo a ban; it reads the LAPI directly, so it works while Grafana is the thing that is broken |
| `security-exporter` | `security` | nas | A | posture numbers for the security dashboard: live route count, un-gated routes, public hosts, privileged workloads |
| `syncthing` | `syncthing` | nuc0 | A | file sync between devices |
| `thelounge` | `thelounge` | nuc0 | A | web IRC client |
| `vikunja` | `vikunja` | nuc0 | D | the task board, where the state of every ticket lives |

## The plumbing

Provisioners, per-node agents and the route middleware. They are Applications like the rest, and
they are what everything above stands on.

| Application | Namespace | Node | Chart | What it is |
|---|---|---|---|---|
| `local-path` | `default` | nas | B | the default storage class: one directory per claim, on the node's own disk |
| `books-media` | `media-stack` | nuc0 | D | the `nfs-books` class: the books library |
| `nfs-audio` | `media-stack` | nuc0 | D | the `nfs-audio` class: music |
| `nfs-qbittorrent-download` | `media-stack` | streaming | D | the `qbittorrent-download` class: what is still downloading, on the fast SSD |
| `plex-media` | `media-stack` | streaming | D | the `plex-media` class: film and television |
| `stash-nfs` | `media-stack` | nuc0 | D | the `stash-nfs` class: the assets export — koillection's uploads, and FileBrowser's read of the same directory |
| `nfs-syncthing` | `syncthing` | nuc0 | D | the `nfs-syncthing` class: what Syncthing syncs |
| `traefik-nfs` | `kube-system` | nuc0 | D | the `traefik-nfs` class: where Traefik keeps its certificates |
| `fluent-bit` | `monitoring` | every node | D | DaemonSet: ships container logs to Loki |
| `prometheus-smartctl` | `monitoring` | every node | A | DaemonSet: drive health (SMART) metrics |
| `traefik` | `kube-system` | streaming | D | the ingress router: the one door the house router forwards to |
| `traefik-gate` | `kube-system` | — | A | middlewares, TLS options and the `lan-only` gate: no workload of its own |
| `network-policy-databases` | `databases` | — | A | the ingress floor of `databases`, and the allows PostgreSQL and pgAdmin need |
| `network-policy-filebrowser` | `filebrowser` | — | A | the ingress floor of `filebrowser`, and the one allow FileBrowser needs |
| `network-policy-vikunja` | `vikunja` | — | A | the ingress floor of `vikunja`, and the two allows the board needs |
| `network-policy-media-stack` | `media-stack` | — | A | the ingress floor of `media-stack`, and one allow per caller measured from the running apps |

All seven provisioners are one release each of the wrapper chart at
`cluster/helm/nfs/provisioner/`, one values file per export, and every release asks for `streaming`.
The Node column records where each pod ran at verification; each row moves to `streaming` as its
Application syncs. None of the seven selects the retired `node-type` label.

## No chart selects `node-type`

Every chart pins its pod with `nodeSelector: kubernetes.io/hostname`. `monitoring/pihole-exporter`
was the last one that rendered a required node affinity on `node-type: general`; it moved to a
hostname pin on 2026-11-22, the day the label was removed from the four nodes. `syncthing` and
`thelounge` were the two before it, rebuilt onto hostname pins on 2026-11-19; `koillection`, `mktxp`
and `news-freshrss` keep the old text in comments only and pin by hostname. `AGENTS.md` §4 keeps the
rule: a new chart asks for a hostname, never a label.

The Node column is where each pod was observed, not what the chart asks for. None of the rows above
depends on the removed label.

## Where to look next

- [reference/routes.md](../reference/routes.md) — every hostname, its gate, its TLS and whether it is
  reachable from the internet.
- [reference/sso-applications.md](../reference/sso-applications.md) — which application uses OIDC,
  which sits behind the forward-auth gate, and which is LAN only.
- [storage.md](storage.md) — the classes above, the claim naming and the missing backups.
- [observability.md](observability.md) — what watches these workloads, and the known holes.
- [Every day](../running-it/every-day.md) — the commands that show what is running, what is
  `Pending` and what is broken.
