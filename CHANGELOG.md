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
- The four NFS volumes that have carried a deletion timestamp since 2023-12-06 are recorded as
  deliberately kept, one reason each, in `docs/how-it-works/storage.md`; the provisioner chart still
  declares all four, so a sync recreates them if one ever has to be replaced.
- The feed set is `hack/freshrss/feeds.opml`, and `hack/freshrss_sync.py` keeps the instance matching
  it. A feed the instance holds twice — same address with and without `#force_feed` — is now reported
  as an extra instead of hiding behind the first row.
- `hack/freshrss_sync.py state` reports what the instance holds per feed — the date it last stored an
  item and that item's title, or `never` — so a feed whose publisher rate-limits can be watched
  without spending the same address's request budget.

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
