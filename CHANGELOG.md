# Changelog

All notable changes to this repository. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases are cut with
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `ashudev.com` is served by this cluster: a third static-site chart on the `ashuvidz-website`
  pattern, publishing the site's `gh-pages` branch — the same bytes GitHub Pages served, with no
  build in the cluster. `www.ashudev.com` redirects to the apex and carries its own certificate, and
  the sidecar fetches and resets rather than pulling fast-forward-only, because Pages rewrites that
  branch.

### Changed

- Traefik's certificate store holds sixteen certificates instead of thirty-four. The eighteen
  removed entries were for hosts no router has served since they were retired: `atlantis`,
  `cross-seed`, `dashy.kub.home`, `discourse`, `firefly`, `firefly-importer`, `forum.ashuvidz.com`,
  `ghostfolio`, `guacamole`, `linkstack`, `matrix`, `monitorss`, `overseerr`, `postiz`, `synapse`,
  `tautulli.kub.home`, `wiki.ashuvidz.com` and `wiki.kub.home`. Traefik renews every entry it holds,
  so each one kept consuming the weekly Let's Encrypt issuance limit. The file was backed up on its
  own volume as `acme.json.pre-orphan-cleanup-2026-11-22` (md5 `23a5546e…`) and the trimmed file has
  md5 `fe822754…`; all sixteen kept names still complete a TLS handshake with their own name and
  answer over HTTPS. The procedure and its traps are in
  [`docs/runbooks/acme-store.md`](docs/runbooks/acme-store.md). The renewal window of the earliest
  kept certificate opens around 2026-12-07; the entry count staying at sixteen after that date is
  what proves the orphans cannot come back.
- Traefik runs as one `Deployment` replica on `streaming` instead of a DaemonSet on every node. Two
  routers sharing `/data/acme.json`, which has no lock, corrupt it and re-request every name, and
  Let's Encrypt allows five duplicates a week: a morning of that takes every public hostname down.
  `updateStrategy: Recreate` keeps the old and new pod from overlapping for the same reason. The house
  router forwards 80, 443, 25565, 22000 TCP/UDP and 21027 UDP to `192.168.1.46` only, so a second pod
  would see no external traffic in any case; the busiest hour of the seven days to 2026-11-21 was
  792 Mbit/s in and 248 Mbit/s out on that node's 10 GbE link. `acme.json` was byte-identical before
  and after (`f074495f…`), and every public name kept serving its Let's Encrypt certificate.
- The Traefik release moves to chart `25.0.3` / Traefik `2.10.7` (from `20.3.1` / `2.9.4`, June
  2023), with the chart's CRDs applied first: the schema was 2.9-era and chart `25.0.3` refuses to
  render without `traefik.io/v1alpha1/ServersTransportTCP`. The dashboard route is now the
  `traefik.io/v1alpha1` object, and the stale `traefik.containo.us` copy is gone. The procedure, its
  verification and its rollback are in [`docs/runbooks/traefik-release.md`](docs/runbooks/traefik-release.md);
  it also records why the release is moved with `helm` by hand — K3s's helm-controller runs no job for
  any `HelmChart` in this cluster (task 530, `#398`).
- Ingress runs on Traefik v3: chart `42.4.1`, Traefik `3.7.19`, reached through 2.11 the same day
  because the CRD API group had to move first. Every route, middleware, TLS option and TLS store is
  on `traefik.io/v1alpha1`, the gate writes `ipAllowList` and the exposure checker rejects the old
  `ipWhiteList` name, and the nine `traefik.containo.us` CRDs that had served since 2023 are deleted
  — the last of them held no objects. `/data/acme.json` was byte-identical through both moves, every
  route count and every `nodePort` held, and every host answered as before. Rollback is
  `helm -n kube-system rollback traefik 10`, which is 2.11 on the v3 schemas.
- K3s moves to `v1.31.14+k3s1` on all four nodes, containerd `1.7.15` → `2.1.5` with it. One node at a
  time, each plan pinned to a version **and** a single hostname so the controller could not walk on
  unattended: `nuc0` 40 s, `rbp2` 160 s (the ARM Pi), `streaming` 20 s, `nas` 20 s. A probe every two
  seconds measured the only user-visible cost — **15 seconds** without ingress while Traefik restarted
  with `streaming`, and Plex answered `503` for about three minutes afterwards while its containers
  started. etcd quorum never dropped, an etcd snapshot went into the OVH bucket before each node,
  PostgreSQL shut down cleanly, the NFS exports kept serving from `nas`, and every node's backup job
  was re-run as the containerd check. No node is drained: workloads are pinned to a named node and
  would only sit `Pending`. The procedure, its timings, traps and an honest rollback section are in
  [`docs/runbooks/k3s-upgrade.md`](docs/runbooks/k3s-upgrade.md).
- The Traefik release is this repository's, not the K3s addon's. K3s's `traefik` and `traefik-crd`
  `HelmChart` objects, its `HelmChartConfig` and the node drop-in are gone, and the addon is disabled
  in `/etc/rancher/k3s/config.yaml` on the three servers. The release was adopted at the version it
  already ran — chart `25.0.3` / Traefik `2.10.7`, revision 8 — from
  [`cluster/infrastructure/traefik/values.yaml`](cluster/infrastructure/traefik/values.yaml), which
  renders what served with one argument removed: `manualRouting` is off, so `/metrics` answers
  `traefik_*` again (task 531). `traefik-config.yaml` is deleted, and the pre-cutover capture is kept
  in `cluster/infrastructure/traefik/snapshot/2026-11-21/`.
- The restore runbook's drill log has its first three entries. On 2026-11-21 a scratch restic pod
  restored the PostgreSQL dump directory from `nodes-nas` (26 dumps plus `globals.sql`), Sonarr's
  database from `nodes-streaming` (which passed `pragma integrity_check`) and one Syncthing photo
  from `nfs` (sha256 identical to the live file). The destructive put-back onto a live claim and the
  etcd rehearsal are still unproved, and the pages say so.
- FileBrowser moves to FileBrowser Quantum, the maintained fork of the archived upstream, as a wrapper
  around the community chart pinned at `2.0.2-stable`.
- FileBrowser signs in through authentik only: the password form is off, so an unauthenticated browser
  is redirected straight to the provider, and the app carries its own OIDC session.
- FileBrowser answers the internet on `filebrowser.kub.home.ashudev.com` so the editor can sign in from
  outside and share links can be opened by people without an account. `lan-only` is removed from the
  route; `security` headers remain.
- FileBrowser's four NAS sources are `defaultEnabled: false`, so a new OIDC account starts with no
  access and folders are granted per person.
- FreshRSS signs in through authentik with its own OIDC session rather than the forward-auth
  middleware, and runs the official `freshrss/freshrss` image with its data at `/config/data`.
- The Longhorn cleanup is closed: no volume, claim, StorageClass, namespace or custom resource
  remains. The disposition page is kept as the record of what was removed, and the chart comments
  that named a Terminating claim to explain a claim name now describe that as history.
- The seven `Released` `local-path` volumes from earlier rebuilds, and the directories they left on
  `streaming` and `nuc0`, are gone. `local-path` keeps a deleted volume's directory (retain policy),
  so removing the volume and removing the bytes are two steps; three older `nuc0` directories with
  no volume or claim behind them are left as housekeeping.
- The four NFS volumes that had carried a deletion timestamp since 2023-12-06 are rebuilt, not
  tolerated: each claim was deleted, the volume's pending deletion completed, and self-heal
  recreated the pair from the provisioner chart. `traefik-nfs`, `plex-media`, `stash-nfs` and
  `nfs-qbittorrent-download` are `Synced`/`Healthy` instead of permanently `Progressing`. The data
  stayed on `nas` (every volume is `Retain`), no pod restarted, and `acme.json` was byte-identical
  afterwards. `docs/how-it-works/storage.md` now carries the procedure and the check.
- The feed set is `hack/freshrss/feeds.opml`, and `hack/freshrss_sync.py` keeps the instance matching
  it. A feed the instance holds twice — same address with and without `#force_feed` — is now reported
  as an extra instead of hiding behind the first row.
- `hack/freshrss_sync.py state` reports what the instance holds per feed — the date it last stored an
  item and that item's title, or `never` — so a feed whose publisher rate-limits can be watched
  without spending the same address's request budget.
- The `local-path` helper pod no longer runs `busybox:latest`. It is pinned to `1.38.0` with a
  `# renovate: datasource=docker` annotation, which closes the one image in the storage path that
  could change without a commit: the helper runs the setup and teardown scripts as root on whichever
  node provisions next.

### Added

- `hack/check-config-keys.sh`, which renders each chart listed in `hack/config-key-checks.yaml` and
  fails any key in its config file that the pinned dependency's own `values.yaml` does not declare,
  naming the key and its line — the class of failure that cost the FileBrowser cutover three syncs.
  Requirement 20 of the chart contract names the manifest, and AGENTS.md §6 runs the check. The
  manifest ships empty: FileBrowser's chart became hand-written on 2026-11-20 and no wrapper chart
  currently validates its config file.
- `hack/freshrss_sync.py drop <url>…`, which unsubscribes a feed so an OPML import can re-create it
  with the settings the file carries. A poll interval, a CSS selector and `#force_feed` are stored on
  the feed and applied only when it is created, so they cannot be edited in place.
- `docs/news-feeds.md` records the feed set, the script's four commands and the re-apply procedure.

- Per-user FileBrowser scopes carrying a path, so the editor's folder is granted as a subdirectory of
  an existing export instead of a dedicated one.
- A `filebrowser-editors` capability group in the authentik blueprint, plus an OAuth2 provider
  (`filebrowser-oidc`) that replaces the forward-auth provider.

### Fixed

- `backup-verify` fails a `sqlite` row whose stream snapshot holds no staged database, naming the
  row. Until now nothing checked what a row had actually staged: a `db:` pattern that upstream moved
  or renamed matched nothing every night while the node job stayed green, and the consistent copy the
  row exists for quietly stopped being written. The verifier reads the file list of the stream's own
  snapshot — not `latest`, which an ad-hoc etcd copy taken between two nights would answer with — and
  counts the files under `claims/<namespace>/<claim>/`. Every `sqlite` row of `nodes-nas`,
  `nodes-nuc0` and `nodes-streaming` carries its database in the newest stream snapshot, checked
  against the live repositories on 2026-11-22.
- `cluster/infrastructure/traefik/traefik-config.yaml` parses again, so the Traefik release can be
  upgraded for the first time since 2023. The over-indented `existingClaim` was one of five defects
  that every apply had hidden behind: the values used `certificatesResolvers` where the chart reads
  `certResolvers` (so no ACME resolver, and `acme.json` never loaded), named `traefik-pvc` instead of
  the live `pvc-traefik-nfs` that holds the certificate store, carried neither the Syncthing entry
  points nor `allowCrossNamespace` — both present only on the hand-edited live objects — and pointed
  the dashboard at `traefik.app.local` while the cluster serves `traefik.kub.local`. Applying it
  moves the chart `20.3.1` → `25.0.3` and Traefik `2.9.4` → `2.10.7`; that apply is a manual step for
  `cluster/infrastructure/**`, and nothing changes on merge.

- The `postgres` bootstrap Job is named after the digest of its whole pod template, not of the SQL
  ConfigMap and the image tag alone. The narrower digest left the env, the resources, the security
  context and the node selector out of the name, so editing any of them was refused by the API
  server on an immutable pod template and the sync could not converge. Every field of the template
  now lives in one `define`, which is rendered into the Job and hashed into its name; a change
  therefore creates a new Job and prunes the completed one, and the idempotent SQL re-runs harmlessly.

- `www.ashuvidz.com` answers again. The name resolved to the cluster with no router of its own, so
  Traefik never asked for a certificate and every request ended in a TLS name mismatch. It now has
  the same `www` pair as `ashudev.com` — a redirect to the apex that carries its own `tls` — and it
  is on the public allowlist in `hack/check_exposure.py`.

- `local-path` and `postgres` report `Synced` again, and for a reason rather than a forced sync.
  `local-path`'s upstream chart renders `spec.template.spec.hostUsers: true`, which this API server
  (1.30, user namespaces off) drops from the stored object whether it is true or false, so the
  Deployment could never match; the Application ignores that one field. `postgres`'s bootstrap Job
  gained the image tag in its content-addressed name, because a Job's pod template cannot be updated
  in place and the image had drifted since the Job ran; the four labels the Job controller adds to
  that template are now ignored, since they are not in git and cannot be removed.

- `ashudev.com` serves `/name` as `name.html`, the way GitHub Pages did: the site links to
  extensionless paths from outside, and they would have answered 404 on the first day of the move.
- The `ashudev-website` sidecar reports which git command failed. A failed fetch leaves the previous
  content in place; a failed `reset --hard` leaves a half-written tree. Both used to print the
  fetch message, so the log claimed the old content was still being served when it was not.
- `freshrss_sync.py verify` reads the Elite Dangerous item expression: it now honours a container
  class (`//*[contains(@class,'…')]//`) and an exclusion (`and not(contains(@href,'…'))`), so the
  feed is checked automatically instead of printing `sel main unchecked`.
- `local-path` provisions new claims on `nuc0` under `/mnt/ssd0`. The node map named `nuc`, a node
  that does not exist, so every claim on `nuc0` fell through to `/opt/local-path-provisioner` on the
  root disk. The thirteen claims already there are left where they are.

### Removed

- The syncthing chart carries no backup any more. Its restic CronJob wrote to
  `s3:s3.amazonaws.com/ashudev-homelab-syncthing-backup`, a bucket that does not exist, so the job
  had never written a snapshot and reported `Failed` every night. The folder it read is the `nfs`
  stream of `cluster/helm/backup`.
- KBDfans *new arrivals* leaves the feed set: the shop refuses the pod's client at every interval —
  `429 local_rate_limited` on eight consecutive scheduled polls while every other feed answered — and
  no address, user agent, protocol or cURL option changes that. `state` never saw an item. The
  measurement is in `docs/news-feeds.md`, and a fetcher outside FreshRSS is left as its own decision
  (Vikunja #463) rather than pending work.
