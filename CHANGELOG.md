# Changelog

All notable changes to this repository. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases are cut with
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `GpuNotAllocatable`: a warning in `homelab-core` when a node that has registered the NVIDIA card
  advertises zero allocatable `nvidia.com/gpu`. A k3s restart on `streaming` clears the GPU
  operator's validator markers in `/run/nvidia/validations` and does not re-run the init containers
  that write them, so the scheduler resource silently goes to 0 while every GPU pod stays `Running`
  and Plex keeps transcoding. The workaround — delete the validator pod, about 90 seconds — is now
  recorded in `docs/runbooks/gpu-operator.md`, and the K3s upgrade's `streaming` check reads the
  allocatable value instead of trusting `nvidia-smi`.
- [`docs/runbooks/last-resort.md`](docs/runbooks/last-resort.md): the procedure for the morning the
  cluster is gone — a laptop, the Bitwarden item `homelab-backup-last-resort` and the OVH bucket,
  with no `kubectl`, no `backup-restic` Secret and possibly no house. Proved 2026-11-22 from outside
  the cluster: all four restic repositories read, and a tree from `nfs` decrypted, with the on-disk
  copies of the password moved aside so only the vault could supply the values. The same change makes
  `docs/secrets.md` name the failure domain of each key, and corrects a claim that had been false
  since the bucket was built: the vault held neither the repository password nor a K3s token, so the
  documented "password manager" copy did not exist.
- `ashudev.com` is served by this cluster: a third static-site chart on the `ashuvidz-website`
  pattern, publishing the site's `gh-pages` branch — the same bytes GitHub Pages served, with no
  build in the cluster. `www.ashudev.com` redirects to the apex and carries its own certificate, and
  the sidecar fetches and resets rather than pulling fast-forward-only, because Pages rewrites that
  branch.

### Changed

- Traefik asks Let's Encrypt for two certificates instead of fifteen. One DNS-01 wildcard over the
  OVH API carries `ashudev.com`, `*.ashudev.com` and `*.kub.home.ashudev.com`; one TLS-ALPN
  certificate carries `ashuvidz.com` and `www.ashuvidz.com` while that zone is still on AWS
  nameservers. Only the apex route of each domain requests a certificate, and every other name is
  served from the store by SNI, so a new application under `ashudev.com` orders nothing. The OVH
  credential is a consumer key scoped to that zone's record and refresh endpoints, in
  `kube-system/traefik-ovh`.
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
- The backups design settles what protects the bucket from the cluster itself: versioning stays on
  with a 30-day lifecycle rule on previous versions, so a `restic prune` run by whoever holds the
  cluster's S3 pair costs thirty days of undo instead of the history. Object Lock was rejected — OVH
  offers it only at bucket creation, and Compliance mode makes `restic prune` fail. A copy that
  outlives the OVH account waits on a second EU provider (card #535, frozen 2026-11-22). In force:
  versioning on since 01:29Z and two lifecycle rules applied at 02:43Z.
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
- Traefik's v3 configuration is audited against 3.7: the two startup warnings are settled, not
  tolerated. `safeNaming` stays `false` because turning it on renames every generated object and the
  cluster's 85 template references are written in the legacy `namespace-name@kubernetescrd` form —
  they would all stop resolving; `global.aliasHeadersStrategy: delete` closes the header-alias
  spoofing; `global.sendAnonymousUsage` is off. The release is revision 12 and
  `helm -n kube-system rollback traefik 11` is the way back. Every decision, with the option it
  replaces, is in [`docs/runbooks/traefik-release.md`](docs/runbooks/traefik-release.md).

### Fixed

- A Traefik upload no longer dies at 60 seconds. v3 raised the entry point's `readTimeout` default
  from `0` to `60s`, which caps the whole request including its body: the public FileBrowser needs
  about 800 s for a 2 GB file on a 20 Mbit/s line. `websecure` now sets `1h`. `writeTimeout` stays
  `0`, so a Plex stream is never cut.

### Security

- Traefik no longer trusts a `PROXY` line or an `X-Forwarded-*` header from the client. Both
  `proxyProtocol.insecure` flags were the chart's stock values and were measured as a `lan-only`
  bypass on 2026-11-22: a request to an internal host carrying `PROXY TCP4 192.168.1.99 …` was
  answered like a LAN client while the same header claiming `8.8.8.8` was refused, and the house
  router forwards 80 and 443 to the same address, so the gate could be walked through from the
  internet. The same request now answers `400`. `X-Forwarded-Host` and `X-Forwarded-Proto` can no
  longer be rewritten by a caller either.
- Traefik deletes a request header whose name aliases another one (`X_Auth_User`, `X.Auth.User` for
  `X-Auth-User`) before routing. Backends that derive variable names from header names — authentik is
  Python WSGI — would otherwise read a header Traefik did not write, which is how the headers the
  gate depends on can be spoofed. `delete` is chosen over `reject` so an innocuous private header is
  dropped instead of answering `400`.

### Added

- The Traefik dashboard gains "Requests per second by entry point", which needs
  `metrics.prometheus.addEntryPointsLabels`. The metric was off, so `traefik_entrypoint_requests_total`
  did not exist and no per-entry-point traffic panel was possible; it costs 66 series at startup on
  five entry points. The export under `cluster/helm/monitoring/grafana/export/traefik/` is re-exported
  at dashboard version 7.

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

- `backup-verify` reports a stream's age and its `snapshots=` count from the nightly job's own
  snapshots only. A copy taken by hand between two nights — the etcd copy from the 1.31 upgrade was
  one — sits in the same repository with a later timestamp, so it made a stopped stream look fresh
  for up to the 26-hour window and inflated the count the dashboard reads. Both now count only
  snapshots tagged `stream=<name>`; a repository holding none reports an empty stream.
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
