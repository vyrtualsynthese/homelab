# The homelab backup solution — design

2026-11-20 · branch `feature/backups` · approach A, chosen by the operator

## Why

The cluster holds every application's configuration, every database and every Secret, and copies
none of it anywhere. The position today, read from the live cluster and the repo on 2026-11-20:

| Piece | What exists | What it is worth |
|---|---|---|
| PostgreSQL | `postgres-dump`, 03:30 daily, verified dumps into a 20 GiB claim on `nas` | a guard against a dropped table, not against a dead disk: one machine, one copy |
| syncthing | a restic CronJob at 05:00 | **never worked**: the AWS bucket does not exist (#432) |
| ~40 `local-path` config claims | nothing | the *arr stack, n8n, Kavita, Calibre, FreshRSS, Thelounge, FileBrowser, Grafana, Vikunja, the board's attachments |
| etcd | K3s writes snapshots to each server's own disk | every Secret, every ArgoCD Application, every object; a destroyed control plane loses the cluster's definition |
| NFS exports | nothing (syncthing's job points at the dead bucket) | 16 TB total, of which the media, music, stash library and downloads are explicitly out of scope |
| edge Pis, host configuration | restic → AWS S3, credentials in plaintext `/etc/environment` | out of scope here: the operator manages the Pis at a later stage |

The failure mode to remove is not "no backup job" — syncthing has one — but **a job that reports
success while writing nothing**. Every part of this design has to prove it produced a snapshot.

## What success looks like

- A dead `nas` disk, a dropped database, or a rebuilt control plane each have a documented, rehearsed
  restore, and the documentation names the date it was last performed.
- Every application that holds state has an explicit decision in the coverage matrix below: backed up
  and how, or excluded and why. Nothing is backed up by accident, and nothing is missed by accident.
- One Grafana page answers "is every stream healthy, how big is the repository, what does it cost".
- An alert fires within 26 hours of any stream going quiet, and it names the stream.
- The offsite copy costs cents per month at the current data volume, and the design says what makes
  it grow.

## Goals

- One offsite repository family on OVHcloud Object Storage, in the EU, reached over the S3 API.
- etcd through K3s's own S3 support — the documented way — rather than a script of ours.
- PostgreSQL through dumps, because a running database's files are not a backup.
- Every SQLite database copied with SQLite's own `.backup` command, so a live application cannot hand
  us a half-written file.
- A per-application decision table, written down, reviewed when an application is added or removed.
- Dashboard and alerts in Grafana, following the conventions in `docs/reference/alerts.md`.
- A restore runbook with a monthly drill, recorded by date.

## Non-goals

- **Any media**: the Plex library, the Stash library, the music share, the qBittorrent downloads, the
  ingest directories' video. Excluded by the operator; they are large, mostly re-obtainable, and they
  are what would turn a €0.20 bill into a €100 bill.
- **Logs and metrics**: Loki's 20 GiB and Prometheus' 25 GiB. They are re-collected, they churn every
  day, and restic would upload gigabytes nightly for data nobody restores. The dashboards and alert
  rules that *are* data of record live in Grafana's own database, which is in scope.
- **The edge Pis** (`rbp0`, `rbp1`): the operator manages them later. Their current restic-to-AWS
  setup is left alone except that its dead code in `ansible/` is deleted.
- **Rebuilding a host from git**: the Ansible inventory, K3s flags, NFS exports and GPU driver are W8.
  This design captures the files a host needs (see the host-configuration row), not a rebuild.
- **A second provider.** One OVH account holds the DNS and the backups, which is a concentration risk
  stated in the stabilisation design §W4. It is accepted here to keep the cost and the moving parts
  down; a third copy on removable media is the operator's call, later.
- **Velero** and any Kubernetes-object backup tool: the etcd snapshot already holds every object, and
  those tools do not make a live SQLite file consistent.

## Decisions the operator made

1. **Approach A**: restic jobs inside the cluster, plus K3s's own etcd upload. Rejected: restic on the
   hosts (outside GitOps, needs the Ansible layer that cannot run) and Velero (heavy, and redundant
   with the etcd snapshot).
2. **K3s's official etcd backup**, not a home-made snapshot script.
3. **No logs.**
4. **Case by case per application**: an explicit decision per claim, not a blanket copy of every
   volume.
5. **Dashboard and alerts**, because a backup nobody watches is a hope.
6. **Reduce OVH cost**, which here means excluding the large streams, one bucket, one storage class,
   and alerting on repository growth.
7. **etcd uploads wait for W5.** The three servers stay on `v1.30.1`; the S3 configuration and its
   rolling restart land with the 1.31 upgrade, and this work writes the runbook and the card instead.
   The consequence is stated below: the cluster's Secrets are not off-box until W5.

## How it works

### One bucket, several repositories

One bucket, `ashudev-homelab-backup`, region `GRA`, 1-AZ (the cheapest tier; the data is a backup, not
a serving workload). One bucket keeps cost reporting and credentials simple. Inside it:

| Stream | Written by | Shape | Why separate |
|---|---|---|---|
| `etcd/` | K3s on the three servers, **after W5**; until then the `etcd-local` copy inside the node jobs | raw snapshot files, K3s's own naming and retention | not restic; K3s must own its pruning |
| `nodes-nas/`, `nodes-nuc0/`, `nodes-streaming/` | one restic CronJob per node | one repository each | a corrupt repository takes one node's configs with it, not all three, and one node's overrun cannot hold another node's lock |
| `nfs/` | one restic CronJob pinned to `nas` | one repository, several tags | the large and slow streams; must not delay the config backups |

Separate repositories mean a prune on a 200 GB stream never holds a lock that blocks the nightly
config backup. Deduplication is inside a repository, and these streams share no data, so nothing is
lost by splitting them.

### Where the jobs run, and why not in the applications' namespaces

A PersistentVolumeClaim belongs to a namespace, and these claims live in eleven of them
(`media-stack`, `databases`, `monitoring`, `n8n`, `news`, `filebrowser`, `koillection`, `syncthing`,
`thelounge`, `vikunja`, `kube-system`). A job in namespace `backup` cannot mount any of them, and a
Secret in `backup` cannot be read from `media-stack`. The honest ways out:

- **One job and one Secret per namespace per node**, eleven of each. Correct, but eleven hand-created
  copies of the same credential, eleven CronJobs to stagger, and eleven values blocks that drift.
- **One job per node in `backup`, reading the node's storage directly** — the local-path roots with a
  read-only `hostPath`, and the NAS exports with a read-only NFS volume. One Secret, four jobs, no
  namespace coupling. What it gives up is the claim object as the unit: paths are resolved from
  `<namespace>/<claim>` at start, and a claim recreated with a new identity would break its row.

**Choice: the second**, with the failure made loud. Every row in the values file names its claim or
export; the job resolves each row before copying anything and **fails if a row resolves to nothing**,
so a recreated claim turns the job red instead of silently backing up an empty directory. That is the
same guarantee the claim mount would give, without eleven credentials.

Two local-path roots exist because the provisioner's `nodePathMap` names `nuc`, not `nuc0`
(card #471): `nuc0`'s claims sit under `/opt/local-path-provisioner/`, the other three nodes' under
`/mnt/ssd0/`. Both are mounted read-only on every node job, so the day #471 is fixed and new claims
land on `/mnt/ssd0`, nothing has to change here. Directories are matched as
`<root>/*_<namespace>_<claim>`, which is the provisioner's naming and survives a claim's UID changing.

### The three kinds of job

**1. etcd — K3s does it.** On each of `streaming`, `nuc0`, `rbp2`, K3s is started with:

```yaml
# /etc/rancher/k3s/config.yaml
etcd-s3: true
etcd-s3-config-secret: k3s-etcd-snapshot-s3-config
etcd-s3-bucket: ashudev-homelab-backup
etcd-s3-folder: etcd
etcd-s3-region: gra
etcd-s3-endpoint: s3.gra.io.cloud.ovh.net
etcd-snapshot-compress: true
etcd-s3-retention: 180
```

- `etcd-s3-config-secret` keeps the access key out of the host filesystem: the credentials live in a
  Kubernetes Secret in `kube-system`, read on every snapshot.
- The schedule stays the default, `0 */12 * * *`, two snapshots a day from each server.
- Retention: 3 servers × 2 cycles = 6 objects a day; 180 objects is about 30 days. At ~20 MB a
  snapshot that is ~3.6 GB, about €0.03 a month.
- **This cluster cannot use the Secret yet.** `--etcd-s3-config-secret` arrived in **K3s 1.30.3**
  (backports of July 2024), and the cluster runs `v1.30.1+k3s1`. **Decision: wait for W5.** The three
  servers keep their current version; the S3 configuration, the Secret and the rolling restart land
  with the 1.31 upgrade, as a step inside W5 rather than a second upgrade window before it. This work
  delivers the runbook, the Secret name and the card, and W5 executes them.
- **The consequence, stated plainly: unless the bridge below is taken, the cluster's Secrets are not
  off-box until W5.** The snapshot K3s already writes every 12 hours is on the same disk as the etcd
  it protects, so a dead control plane currently takes every Secret with it.

  **Interim bridge, proposed:** the two control-plane nodes that also run a config job (`nuc0`,
  `streaming`) add `/var/lib/rancher/k3s/server/db/snapshots` as one more read-only `hostPath`, tagged
  `etcd-local`. K3s keeps owning the snapshot; only the off-box copy is ours, so nothing in the host
  configuration changes, no restart is needed, and the file is encrypted twice — by the join token for
  the Secrets, by restic for the bytes. Measured on 2026-11-20: each snapshot file is 110–125 MB and
  K3s keeps several, so `nuc0`'s directory holds 602 MB and `streaming`'s 506 MB. A night adds one file
  per server, and restic's dedupe trims most of it against the previous snapshot. It is removed by the
  same W5 step that turns on the official upload.
- Whatever is chosen, the config file is edited on the host by the operator: agents have read-only
  cluster access and no way to reach `streaming`, `nuc0` or `rbp2`. The runbook is written for them,
  and "done" means a snapshot exists in the bucket, not that the file was edited.
- **The join token is part of the backup.** A snapshot is encrypted with a key derived from
  `/var/lib/rancher/k3s/server/token`; restoring onto a rebuilt host without the same token produces
  an unreadable snapshot. Its value goes into the password manager, and the runbook says so.

**2. Local-path claims — one restic run per node, over an explicit allow list.** This is the
case-by-case part, and it is a declarative list, not a directory walk:

```yaml
# values.yaml of cluster/helm/backup, one block per node
nodes:
  streaming:
    roots: [/mnt/ssd0]
    claims:
      - { claim: sonarr-config, namespace: media-stack, kind: sqlite,
          exclude: ["MediaCover/", "logs/", "logs.db*", "Backups/"] }
      - { claim: plex-config,   namespace: media-stack, kind: files,
          exclude: ["Cache/", "Metadata/", "Media/", "Scanners/", "Codecs/"] }
      - { claim: kavita-config, namespace: media-stack, kind: sqlite, db: ["*.db"] }
  nuc0:
    roots: [/mnt/ssd0, /opt/local-path-provisioner]
    claims:
      - { claim: n8n-config, namespace: n8n, kind: sqlite }
      - { claim: pgadmin-config, namespace: databases, kind: sqlite, pv: pvc-80b62a0a-954e-4d9f-ad56-e10de4351e38 }
```

- Only the claims in the list are copied. A new claim is invisible until someone writes its row —
  which is the review step the operator asked for — and a row that resolves to nothing fails the job.
- `exclude` is a list of restic `--exclude` patterns, applied to the row as restic reads it. It is
  reserved for what is large **and** rebuilt from something else (poster art, logs, caches, the
  application's own rotating db copies), never for making a row look small.
- `pv` pins a row to one PV. It exists because a deleted claim can leave a directory behind with the
  same `namespace_claim` suffix: `pgadmin-config` and `calibre-web-automated-config` each have one
  today. Without the pin the row would match two directories, and the job would refuse to guess.
- `kind: sqlite` puts an initContainer in front of restic. It copies every matching database with
  `sqlite3 <file> ".backup /staging/<namespace>/<claim>/<file>"`, which is consistent even while the
  application is writing, then restic backs up `/staging` **and** the claim directory with the
  database files excluded. Restore guidance names the staged copy as the one to use.
- `kind: files` hands the directory to restic as it is: configuration, certificates, attachments.
- The job is pinned to its node by its own `nodeSelector`: the directories it reads exist only there.
  No claim is mounted, so the scheduler cannot infer it.
- The four paths a rebuild needs (`/etc/rancher/k3s/`, `/etc/exports`, `/etc/netplan/`, any systemd
  unit added by hand) are mounted read-only by the same job and tagged `host`, so a node's snapshot
  carries both its claims and its host configuration.

**3. NFS streams — one restic run pinned to `nas`, read-only.** syncthing, koillection uploads, the
Traefik ACME store and the qBittorrent config export. The job mounts the
exports directly rather than through a claim, so it needs no namespace of its own. It is pinned to
`nas` to keep the reads off the cluster network.

**Ruling, 2026-11-20: the books library is out of scope.** It was 22 GB of the 28.9 GB this stream
carried — the single largest thing in the backup and the number that set the bill. Re-acquiring a
book is possible, the operator decided that is the cheaper answer, and the export is now absent from
`values.yaml` rather than excluded, so the job cannot reach it. Scope drops from 34.1 GB to 12.2 GB,
and the first snapshot from €0.24 to €0.09.

### Names, secrets, credentials

| Object | Where | Created by |
|---|---|---|
| bucket + S3 user + policy | OVH Public Cloud project `a93a624a…`, declared in `infra-main/ovh/` | [card #485](https://vikunja.kub.local/projects/12/71?task=485) on the `infra-main` board, filed 2026-11-20 and **blocking all verification**: `ovh_cloud_project_storage`, `ovh_cloud_project_user`, `ovh_cloud_project_user_s3_policy` |
| `backup-restic` Secret (`RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) | namespace `backup`, read by all four jobs | the operator, by hand, from the password manager — the repo's model for every Secret |
| `k3s-etcd-snapshot-s3-config` Secret | namespace `kube-system`, type `etcd.k3s.cattle.io/s3-config-secret` | the operator, by hand, and only once the K3s version carries the flag |
| the restic repository password | the password manager, plus one sealed paper copy | the operator |
| the server join token | the password manager | the operator |

The restic password is the single point of failure for every restic stream: lose it and the
repository is noise. It is written down twice, and the drill starts by fetching it from the password
manager rather than from a shell history.

### Schedule

All times are Europe/Paris on the cluster, and the order matters: the PostgreSQL dump must finish
before the node job that copies the dumps.

| Time | Job | Stream |
|---|---|---|
| 03:30 | `postgres-dump` (exists) | writes the dumps claim on `nas` |
| 00:00 / 12:00 | K3s etcd snapshot, on each server | `etcd/` |
| 04:30 | `backup-node-nas` | `nodes-nas/` |
| 05:00 | `backup-node-nuc0` | `nodes-nuc0/` |
| 05:30 | `backup-node-streaming` | `nodes-streaming/` |
| 06:00 | `backup-nfs` | `nfs/` (syncthing, koillection, ACME, qBittorrent config) |
| Sun 12:00 | `backup-prune` | one `forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune` per repository, sequentially |
| 08:30 | `backup-verify` | checks every stream, exits non-zero when one is stale or over budget |

Every restic invocation carries `--retry-lock 2h` (the prune `--retry-lock 6h`) and every job
`concurrencyPolicy: Forbid`. The prune sits at noon on Sunday, outside every nightly window, so an
NFS run that started at 06:00 and is still going at noon waits for the lock rather than racing it —
and if it holds the lock past six hours, the prune fails loudly instead of silently skipping a week.

Until W5, the `nuc0` and `streaming` node jobs also copy `/var/lib/rancher/k3s/server/db/snapshots`
(the interim bridge described above); that read happens at 05:00 and 05:30, hours after K3s writes the
00:00 snapshot, so no half-written file is ever copied.

Repositories are created once, by hand, in the setup runbook (`restic init`, four times). No job ever
runs `init`, so a job cannot accidentally create an empty repository where a lost one used to be.

The 06:00 slot for the NFS job was a guess; the measurement settles it. The job walks 6.9 GB a night,
and restic reads only what changed, so the window is normally minutes. If a run still reaches Sunday
noon, the pruning moves rather than the backup: the backup is the thing that must not be cut short.

The existing syncthing CronJob in `cluster/helm/syncthing` is **retired**, not repointed: the new NFS
job does the same work with the retention policy the rest of the design uses, and two mechanisms for
one stream is how a stale one survives unnoticed. The retirement happens in the same change that
proves `backup-nfs` writes a snapshot, so the stream is never without a job.

## The coverage matrix

Every claim in the cluster on 2026-11-20, with its decision and its measured size. `Size` is MB of
bytes on disk, measured on 2026-11-20 with `du -sb` on each node's own filesystem; `a → b` means the
row is `a` on disk and `b` after the exclusions in its decision, which is what restic reads. A claim's
advertised size is never used: several advertise 128 MiB and hold a gigabyte.

### `local-path` claims, node `streaming`

| Claim | App | Kind | Size (MB) | Decision | Why |
|---|---|---|---|---|---|
| `sonarr-config`, `sonarr-anime-config`, `radarr-config`, `radarr-anime-config`, `lidarr-config`, `prowlarr-config` | *arr stack | sqlite | 2 310.9 → 473.5 | **back up**, excluding `MediaCover/`, `logs/`, `logs.db*`, `Backups/` | library definitions, indexers, quality profiles, API keys; weeks of tuning. The excluded four are poster art, rolling logs and the app's own rotating db copies: 80 % of the row, all of it rebuilt from the metadata providers |
| `bazarr-config`, `bazarr-anime-config` | Bazarr | sqlite | 43.6 → 25.0 | **back up**, the database only (`db/*.db`), excluding `cache/`, `backup/`, `log/` | subtitle language profiles and history; the 18.6 MB excluded is cache and the app's own copies |
| `autobrr-config` | autobrr | sqlite | 2 544.4 → 1 373.0 | **back up**, excluding `*_sv*_*.backup` | filters, IRC announce state and the announce history in `autobrr.db`. The excluded file is autobrr's own 1.17 GB db copy |
| `cross-seed-config` | cross-seed | sqlite | 135.2 → 10.6 | **back up**, excluding `torrent_cache/`, `logs/` | match database; the cache refills from the indexers |
| `seerr-config` | seerr | sqlite | 4.7 | **back up** | requests and users |
| `tautulli-config` | tautulli | sqlite | 14.0 → 1.8 | **back up**, excluding `cache/`, `logs/`, `backups/` | watch history, the only copy |
| `maintainerr-config` | maintainerr | sqlite | 25.7 → 1.0 | **back up**, excluding `ui/`, `logs/` | collection rules |
| `qbittorrent-vpn-config` | qBittorrent | files | 0.02 | **back up** | the VPN client's keys and config, not a database. The seed state is `BT_backup/` on the NFS export below |
| `plex-config` | Plex | files + sqlite | 7 663.7 → 1 851.5 | **back up**, excluding `Cache/`, `Metadata/`, `Media/`, `Scanners/`, `Codecs/` | the case review found the library database at `Library/Application Support/Plex Media Server/Plug-in Support/Databases` (1.85 GB, both `library.db` and `library.blobs.db`): collections, playlists, watch state and manual matches, none of it reconstructible. The 5.8 GB excluded is poster art, the transcode cache, thumbnails and downloaded scanners — all regenerated |
| `kavita-config` | Kavita | sqlite | 67.3 → 5.4 | **back up**, excluding `covers/`, `logs/`, `cache*`, `backups/` | library index, users, reading progress; covers are regenerated from the files |
| `calibre-web-automated-config` | CWA | sqlite + files | 152.1 → 1.0 | **back up**, excluding `log/`, `log_archive/`, `thumbnails/` | `app.db`, settings; the library itself left scope on 2026-11-20 and the 151 MB of thumbnails is rebuilt from it |
| `calibre-web-automated-ingest` | CWA | files | 0.004 | **back up** | books waiting to be filed are not yet on the share |

### `local-path` claims, node `nuc0`

| Claim | App | Kind | Size (MB) | Decision | Why |
|---|---|---|---|---|---|
| `pgadmin-config` | pgAdmin | sqlite + files | 0.4 | **back up** | `pgadmin4.db` holds the server list and the encrypted database passwords. Pinned to `pvc-80b62a0a…`: a released 10 GiB claim with the same name left a second directory on the disk, and the row must not guess between them |
| `n8n-config` | n8n | sqlite + files | 6.6 | **back up** | workflows and stored credentials, encrypted with the key in a Secret (which is in etcd) |
| `news-freshrss-config` | FreshRSS | sqlite + files | 442.3 → 222.0 | **back up**, excluding `www/`, `log/`, `data.fresh-*` | feeds, read state, users. `www/` is the application unpacked from the image, `data.fresh-*` is an upgrade's copy of the data |
| `filebrowser-filebrowser-quantum-config` | FileBrowser | sqlite + files | 0.45 | **back up** | users and rules |
| `filebrowser-filebrowser-quantum-data` | FileBrowser | files | 20.8 | **back up** | whatever it stores locally; small enough that the case review is not worth its own card |
| `koillection-conf` | Koillection | files | 0.004 | **back up** | configuration, currently empty; the cards are in PostgreSQL |
| `syncthing-config` | Syncthing | files | 0.1 | **back up**, excluding `index-v*.db` | device identity, certificates and folder list matter; the index rebuilds itself by rescan |
| `thelounge-config` | The Lounge | sqlite + files | 0.09 | **back up** | accounts and settings |
| `vikunja-data` | Vikunja | files | 0.12 | **back up** | attachments and uploads; the board's cards are in PostgreSQL |
| `recyclarr-config` | recyclarr | files | 157.7 → 0.07 | **back up**, excluding `resources/`, `logs/` | synced configuration; git holds only its templates. `resources/` is the Trash Guides JSON cache the app re-downloads. It sits on `nuc0`, not `streaming` as an earlier draft of this matrix had it |
| `docs-site-site` | docs site | files | 70.9 | **exclude** | rebuilt from git every minute by its own CronJob; restoring it costs one sync |
| *(no claim)* | authentik | — | — | **note the gap** | its uploaded icons and flow backgrounds sit on an `emptyDir` and die with the pod. Not a backup problem; a separate card |

### `local-path` claims, node `nas`

| Claim | App | Kind | Size (MB) | Decision | Why |
|---|---|---|---|---|---|
| `postgres-dumps` | PostgreSQL | files | 38.7 | **back up**, after 03:30, excluding `*.partial` | the dumps are the database backup; seven days of them are 38.7 MB and restic dedupes the repeats. A dump still being written is not a dump |
| `postgres-data` | PostgreSQL | — | 171.2 | **exclude the files** | a running database's directory is not a consistent copy; the dumps are. Recovering the directory is not a restore path this design promises |
| `grafana` | Grafana | sqlite | 58.4 | **back up** | every dashboard and alert rule in the cluster lives only here (58.4 MB of it is installed plugins, which are static and dedupe away) |
| `prometheus-server` | Prometheus | — | 2 482.0 | **exclude** | metrics, rewritten daily; re-collected |
| `storage-loki-0` | Loki | — | 447.0 | **exclude** | logs, excluded by the operator |

### NFS claims

| Claim | Export | Size (MB) | Decision | Why |
|---|---|---|---|---|
| `pvc-nfs-syncthing` | `/mnt/storage/nfs/syncthing` | 6 773.3 | **back up** (the `nfs` stream) | the operator's own critical data. The case review checked for `.stversions` and found none: Syncthing's file versioning is off, so the exclusion below is future-proofing, not a saving |
| `pvc-books-media-…` | `/mnt/storage/nfs/books` | 21 952.1 | **not backed up** (ruling 2026-11-20) | the Calibre library and its `metadata.db`; re-acquiring books is possible, and it was the largest thing in scope |
| `koillection-uploads` | `/mnt/storage/nfs/stash/koillection-koillection-uploads-pvc-9c9ffcb6-90e9-4d6a-94b3-d6b2f6c04d15` | 0.008 | **back up** | images the operator uploaded; currently one empty subdirectory. The directory name carries the claim's PV id, so the row fails loudly rather than silently if the claim is recreated |
| `pvc-nfs-audio-…` | `/mnt/storage/nfs/audio` | excluded | **exclude** | music, large, re-obtainable |
| `pvc-plex-media` | `/mnt/storage/nfs/plexmedia` | excluded | **exclude** | media |
| `pvc-stash-nfs-nfs-stash` | `/mnt/storage/nfs/stash` | excluded (692 100) | **exclude** | media; only koillection's subdirectory above is in scope |
| `pvc-nfs-qbittorrent-download-…` | `/mnt/ultrafaststorage/nfs` | excluded (2 365 667) | **exclude** | downloads; the seed state is on the qBittorrent config export below |
| `pvc-nfs-qbittorrent-config-…` | `/mnt/storage/nfs/qbittorrent-config` | 428.7 → 181.2 | **back up**, excluding `logs/`, `log/`, `geoip/`, `ui.prev/`, `.cache/`, `.npm/` | the case review confirmed the live qBittorrent container mounts this export at `/config` and writes `qBittorrent/BT_backup` there: the seed list and resume data, which cannot be reconstructed. The excluded 247 MB is container logs, a GeoIP database and an old web UI copy |
| `pvc-traefik-nfs` | `/mnt/storage/traefik` | 0.9 | **back up** | the ACME certificate store (`acme.json` plus one old copy); losing it means re-issuing and risking Let's Encrypt's weekly duplicate limit |

### Nothing to back up

ArgoCD (all of its state is objects in etcd), Dashy (its configuration is a ConfigMap in git), the two
nginx sites, the provisioners, the exporters, `gpu-operator`, node-feature-discovery,
`system-upgrade-controller`, `renovate` and the cluster addons. Their definitions are in git or in the
etcd snapshot; they hold no bytes of their own.

Two more cases, both found while measuring:

- `minecraft-server-data` is `Pending` with no volume behind it. There is nothing to copy, and no row.
- Seven PVs are `Released` and their directories are still on the disks: `atlantis-data`,
  `filebrowser-config`, `filebrowser-database`, `calibre-web-automated-config`, `pgadmin-config`,
  `cross-seed2` and `plex-transcode`. They are not claims, so they get no row. Two of them —
  `calibre-web-automated-config` and `pgadmin-config` — have the same `namespace_claim` suffix as a
  live claim, which is why those two rows name their PV instead of matching by name.

### The measurement, and what it decided

Taken on 2026-11-20 from each node's own filesystem through a temporary read-only `hostPath` pod, and
for NFS on `nas`, which is where the exports physically live. Prometheus cannot answer this: its
`kubelet_volume_stats_used_bytes` reports the filesystem behind the export, so the 0.9 MB ACME store
and the 692 GB stash library both read as the same 13.8 TB.

| Stream | Rows on disk (GB) | After exclusions (GB) |
|---|---|---|
| `nodes-streaming` | 12.9 | 3.7 |
| `nodes-nuc0` | 0.94 | 0.25 |
| `nodes-nas` | 3.10 | 0.10 |
| `nfs` | ≈ 3 087 | 6.9 |
| etcd copies (bridge) | 1.11 | 1.11 |
| **Total** | | **12.2** |

`nfs` on disk is almost all excluded: 2.37 TB of downloads, 692 GB of stash library and, since
2026-11-20, the 22 GB books library. The two music and media exports
(`/mnt/storage/nfs/audio`, `/mnt/storage/nfs/plexmedia`) were not walked at all, since they are
excluded and their size changes nothing.

The decision: **1-AZ Standard in `GRA`, €7.10 per TB per month.** 12.2 GB is €0.09 for the first
snapshot; with the 7/4/12 retention tail the repositories are expected to settle between 15 and
25 GB, i.e. €0.11–0.18 a month — the etcd copies are the only data that grows night over night. It is
nowhere near the 200 GB where the class question reopens, and Infrequent Access stays rejected: it
charges for retrieval and bills a 30-day minimum per object, which is wrong for data restored during
a drill.

### Host configuration

Not claims, but data that no backup currently holds and that a rebuild needs: `/etc/rancher/k3s/`,
`/etc/exports`, `/etc/netplan/`, the local-path provisioner's ConfigMap, and any systemd unit added by
hand. The first four are copied into the `nodes-*` stream by the same per-node job, from a short
explicit path list. A full rebuild still needs W8; this only stops the files from vanishing.

## What it costs

1-AZ Standard class in `GRA` is **€7.10 per TB per month** (€0.00000972 per GB-hour). Egress is free,
so a restore costs nothing but time.

| Data in the repository | Per month |
|---|---|
| 10 GB | €0.07 |
| 100 GB | €0.71 |
| 1 TB | €7.10 |
| 3 TB | €21.30 |

The cluster side of the repository — configs, PostgreSQL dumps, Grafana, Plex's database, etcd copies
— measures 5.1 GB, so the earlier guess of "under 10 GB" holds. The bill is decided by `nfs`, 6.9 GB
after the books library left scope, plus 1.11 GB of etcd copies. Measured total is 12.2 GB, and the
200 GB line where the class
decision would reopen is nowhere in sight.

Growth is bounded by three things, and each is a card: retention is 7 daily, 4 weekly, 12 monthly, so
the tail grows by one monthly increment, not by 30 more dailies; `.stversions` and application log
directories are excluded; and the verifier fails when the repository passes its budget, which fires
the same alert as a stale stream. `budgetGiB` is that ceiling, measured on the whole repository
(`restic stats --mode raw-data`), so it is the number the bill is computed from; the four budgets sum
to 25 GiB, the top of the expected range.

## Monitoring

### Dashboard: `Homelab · Backups`

In Grafana's `Homelab` folder, beside Health, Applications, Logs and Disks.

| Panel | Source | Answers |
|---|---|---|
| Stream status table: stream, last success, age, state | `kube_cronjob_status_last_successful_time`, `kube_cronjob_status_active`, `kube_job_status_failed` | which stream is late, red or running now |
| Repository size, one line per stream | the verifier's log line, read through Loki | is something growing that should not |
| Snapshot count and newest snapshot id per stream | same | is a stream adding snapshots or only succeeding at nothing |
| etcd: newest `etcd-local` snapshot, age, size, which server it came from | the verifier's restic query, logged | is the control plane covered off-box |
| Estimated monthly cost | total size × the class price | the operator's actual question |
| Failed backup jobs in 7 days | `kube_job_status_failed` scoped to `backup` | a flapping stream |

The verifier writes one structured line per stream per night
(`stream=… snapshots=… bytes=… newest=… age_h=…`), which Loki already indexes; no new component, no
push gateway, no scrape config.

### Alerts

One new rule, plus one already existing:

- **`BackupStreamStale`** — fires when a CronJob in the `backup` namespace has not succeeded in
  **26 hours** (one daily slot plus two hours of slack):

  ```
  (time() - kube_cronjob_status_last_successful_time{namespace="backup"}) > 93600
  ```

  A namespace selector rather than one rule per stream, because every job there is daily and
  kube-state-metrics 2.20 no longer publishes the cron schedule — the same constraint that made
  `RecyclarrNotSucceeding` a single hard-coded rule. The namespace is the right key and the chart's
  `homelab.io/backup-stream` label is not: **kube-state-metrics does not publish CronJob labels on
  `kube_cronjob_status_last_successful_time`** (checked live on 2026-11-20), so a label selector would
  match nothing. The summary therefore names `{{ $labels.cronjob }}` — `backup-node-nas` is the job and
  `nodes-nas` the stream. Severity warning, `for: 10m`; resolves on the next success. A stale
  *repository* is the verifier's job; this rule is about the job not running.
- **`JobFailed`** already covers the other half: a run that fails and does not retry into a success.
- **The verifier is the third alert without a rule of its own.** It fails when the newest etcd
  snapshot is older than 26 h, when any restic repository reports no snapshot, or when the repository
  is over budget. A failed verifier means the verifier's own CronJob is stale, so `BackupStreamStale`
  fires for it, and the verifier's log in Loki says which stream and why.

The rule lives in the `homelab-core` group at 300 s with `for: 10m`; nothing here needs a 60 s
cadence, and a `for` of 5 minutes would put it in the fast group by the rule `alerts.md` states. They are created
through the Grafana API and recorded in `docs/reference/alerts.md`, like every other rule.

## Restore, and the proof

`docs/runbooks/restore.md` is written with four restores, each performed once during the build and
recorded with its date, duration and result:

1. **PostgreSQL** — a dump into a scratch database on the same instance, then a whole database into a
   fresh instance; extends the existing `postgres-restore.md`.
2. **A config claim** — one *arr claim from restic into a scratch claim, the application booted on it,
   its UI and database check out. The staged SQLite copy is what is used.
3. **A file from syncthing** — one file restored and read back, proving the stream that has never
   worked.
4. **etcd** — a snapshot restored into the W5 test cluster (card #8 owns the rehearsal); this design
   adds "restore from the OVH copy" to that card and a check that a Secret survives.

The monthly drill repeats one of them, rotating, and the runbook keeps the log. A backup without a
logged restore is a hope with a timestamp, which is what `docs/running-it/disaster-recovery.md` says
today; that page is rewritten to describe what this builds instead.

## Docs this changes

| Page | Change |
|---|---|
| `docs/runbooks/restore.md` | new: the four restores, the drill log, the recovery order |
| `docs/running-it/disaster-recovery.md` | rewritten: from "nothing is backed up" to what is, where, and how it is proven |
| `docs/secrets.md` | two new Secrets, the join token, the restic password and its sealed copy |
| `docs/reference/alerts.md` | `BackupStreamStale`, what it protects, and why the verifier has no rule |
| `docs/observability-conventions.md` | the dashboard row |
| `AGENTS.md` | one section: where backups live, the case-by-case table, "no job creates a repository" |
| `cluster/helm/backup/README.md` | the chart contract, as every chart here has |
| `docs/how-it-works/backups.md` | new living page: what is backed up, the measured sizes, the restore order |
| `mkdocs.yml` | the two new pages in the nav |

## Risks, and what is done about them

| Risk | Mitigation |
|---|---|
| The restic password is lost | two copies, one sealed; the drill fetches it from the password manager |
| One bucket, one credential: a mistake or a compromise can delete history | separate repositories per stream; the S3 user is scoped to this bucket; the credential is not used by anything else. There is no immutable copy — restic prune needs deletes. A removable-media third copy is left to the operator |
| The etcd snapshot holds every Secret in the clear | bucket private, encryption at rest, EU region; the join token stays out of the bucket |
| A job reports success and writes nothing | the verifier checks snapshot counts and ages independently; a stream that stops adding snapshots fails within a day |
| SQLite `.backup` cannot open a database read-only under WAL | fallback is a read-write mount of that one claim, recorded in its row; the copy is still made by `.backup`, never by `cp` |
| A claim is recreated with a new identity, so its directory name no longer matches | rows resolve `<root>/*_<namespace>_<claim>` and the job fails when a row resolves to nothing; a red job is the alert |
| A claim is added and nobody adds its row | the matrix is reviewed whenever an application is added; the chart's values are the matrix, so the review is a diff |
| The etcd Secret flag is missing on 1.30.1 | decided: the official upload waits for W5, with the `etcd-local` restic bridge in the meantime |
| W5 slips further, so the official upload is late | the bridge keeps a copy off-box; the card stays on W5's list and the runbook is already written |
| A long NFS run holds the lock through the prune window | `--retry-lock` on every restic call, prune at Sunday noon, prune failure alerts instead of silently skipping |

## Decisions taken after the build review

A reviewer read this design as a builder and raised seven gaps. Six are settled here, as my calls; the
seventh was the operator's (the etcd route: wait for W5), and it settles the etcd restore drill with
it, since that drill needs the W5 test cluster.

| Gap | Call |
|---|---|
| Claims live in eleven namespaces, so a job in `backup` cannot mount them | read the storage directly, one job per node plus one NFS job; one Secret, four jobs. Eleven per-namespace jobs and eleven Secrets were rejected as more drift than safety |
| `nodes/` as one repository contradicted its own rationale | one repository per node (`nodes-nas`, `nodes-nuc0`, `nodes-streaming`) |
| Nothing owned the Traefik ACME store or the qBittorrent config export | both go to the NFS job; the qBittorrent export is still a case review, and if it is a leftover its row is deleted with the reason |
| The bucket and its S3 user exist on no board | filed on the `infra-main` board by this work, and it blocks every verification step, so it is the plan's first task |
| The pre-flight measurement and the five case reviews exist as no cards | they are tasks in this plan, not prerequisites: each is a measurement with a written result |
| Books and koillection at a fixed 06:30, prune at 07:30 | one NFS job, `--retry-lock` everywhere, prune at Sunday noon, and the pre-flight re-times the slot |
| Who restarts the control plane for etcd | **the operator's call**: wait for W5, and write the runbook now. The interim bridge is proposed above |
| The etcd stream would then ship unbuilt | it ships as a runbook plus an `etcd-local` restic copy, and the official upload becomes a W5 step; "done" for this work is a fresh off-box snapshot, whichever mechanism produced it |

## Left to the builder

- The exact `restic` and `sqlite3` image pins (`restic/restic:0.19.1` is what the syncthing chart runs
  today; the staging image needs a shell and `sqlite3`, which the restic image does not have).
- The verifier's implementation: a shell script on an image that already has `restic` and `kubectl`,
  or the `alert-relay` pattern of a small Python in a ConfigMap.
- Byte counts per claim; the pre-flight measures them and replaces the estimates in this design.
- The storage class, once the total is known.
- Whether the sqlite staging needs a per-app pattern list or a default (`*.db`, `*.sqlite*`) is enough —
  the case reviews answer it per application.
- Whether the pod's own NFS volume negotiates the same version as the claims (`nfsvers=4.2`,
  `nolock`); the pod volume source carries no mount options, so if v3 does not work the fallback is an
  NFS-backed PV declared by the chart, mounted like a claim.
