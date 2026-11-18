# Chart notes: the per-app record

> **In one line.** One chart per application, built from `autobrr` rather than from a shared library:
> this is how each chart meets the contract, where it had to diverge, and which ones have not caught
> up.
>
> **Use it when** you are building or reviewing a chart and want the worked example closest to it ·
> **Verified** 2026-11-17 · **Kind:** living page

## What good looks like

### autobrr

As of 2026-11-11: five objects render, ingress fully driven by values, env inline in the
Deployment, claim name derived from the release.

### cross-seed

As of 2026-11-12: four objects render, no ingress because the UI is not exposed, named
`configFile` and `torrents` values instead of raw pod spec, liveness only, claim name derived from
the release. It shares the naming helpers and the generic Service, ServiceAccount and claim
templates with `autobrr`; the Deployment and the values are built around cross-seed's read-only
config file, its NFS torrent directory and its liveness-only probe.

### flaresolverr

As of 2026-11-12: the app-shaped version of the same chart. Three objects because the
app keeps no state and is never exposed, `RollingUpdate` because there is no claim to fight over,
probes on the app's own `/health`, a `metrics` block that turns its exporter on in one place —
environment, port and scrape annotations together — and `LOG_LEVEL: warning`, with the counters
carrying the outcomes the log no longer prints.

### prowlarr

As of 2026-11-12: the same shape for a LinuxServer image. Five objects, including the
config claim, probes on `/ping`, and `PROWLARR__LOG__LEVEL` / `PROWLARR__LOG__CONSOLELEVEL` at
`warn`. The hardening stops where the LinuxServer init starts: the image must run as root to
prepare `/config` and then drop to `PUID`/`PGID`, so `runAsNonRoot`, `runAsUser` and dropping all
capabilities are not options. Only `allowPrivilegeEscalation: false` and the runtime seccomp profile
are safe. Its exportarr sidecar is wired from one `exportarr` block that carries the listener, the
scrape annotations and the API-key Secret reference together. The key was rotated to the app's
current value on 2026-11-12, and the Prometheus target is verified `up`; `APIKEY` is exportarr v1's
spelling, still accepted by v2.3.0, the newest published release. Since 2026-11-15 its `/api` is
exempt from the authentik gate (a `priority: 100` rule with `lan-only` alone): `/api/v1` requires
`X-Api-Key`, so the key check replaces the gate rather than disappearing with it.

### sonarr and sonarr-anime

As of 2026-11-12: the LinuxServer-plus-exporter pair, two charts because a
shared chart is not allowed. Five objects each, two named NFS media mounts next to the config claim,
probes on `/ping`, and both Sonarr log-level variables at `warn`. The exporter target is built from
the release name and `service.port` instead of being typed in twice; the previous `sonarr` chart had
`7878` in the target and `8989` in the Service, and the target never came up. The scrape annotations
render from the same `exportarr` block, so the announced port cannot disagree with the listener.

Since 2026-11-15 `sonarr`'s `/api` is exempt from the authentik gate: a second rule, `PathPrefix(/api)`
at `priority: 100`, carries `lan-only` alone, because every `/api/v3` request must carry `X-Api-Key`
and an API client cannot complete a sign-in redirect. `sonarr-anime` follows the same shape since
2026-11-18: it carries the gate plus the `/api` exemption, and its own login is off.

### recyclarr

As of 2026-11-13: the batch-job case. Three objects, no listener, no probes, no metrics.
The CronJob's schedule, timezone, concurrency policy and deadlines are values; the Secret holding
`recyclarr.yml` is mounted read-only at `/config/recyclarr.yml` instead of being copied into the claim
by an init container; the claim is derived from the release and stays on `nuc0`, where its PV lives.
It runs as UID 1000, the image's own default and the claim's existing owner, so enabling non-root cost
no node-side `chown` and needs no runbook.

### tautulli

As of 2026-11-13: the first chart in the media stack with **no public route at all**. One
IngressRoute at `tautulli.kub.local`, LAN-only — and since 2026-11-15 that means two of them, a
`websecure` route and a `web` redirect, like every other `kub.local` route in the namespace. It also carries the second deliberate readiness exception: Tautulli reports Plex
reachability and SQLite integrity only in a JSON body with HTTP 200, which the kubelet cannot read,
so readiness is disabled rather than pointed at liveness. Its image starts as root and drops to
`PUID`/`PGID`, so the capability set is `ALL` dropped plus the three calls that init makes. Since
2026-11-18 the route carries `lan-only` and no authentik gate, by decision: the gate cannot carry an
identity into the app, so every browser would share one Tautulli account and per-user watch history
would collapse. Tautulli's own login is the gate, and it keeps the login it had; the Plex-only toggle
from v2.19.0 is not in the official docs, so the chart does not set it. With no gate there is no
`/api` exemption to render either; `lan-only` covers the whole host. The chart
creates the config claim: the previous one was a Longhorn volume that vanished in 2026, which is why
the app had not run for 71 days.

### radarr and radarr-anime

As of 2026-11-13: the same LinuxServer-plus-exporter pair as `sonarr` and
`sonarr-anime`, five objects each. The two NFS media claims are exposed as named `downloads` and
`media` values rather than hardcoded pod volumes; probes are on `/ping` with a 300s startup budget
for the database migration; `RADARR__LOG__LEVEL` and `RADARR__LOG__CONSOLELEVEL` are both at `warn`.
Claim names already matched the derived `<fullname>-config`, so no data moved. The `radarr-anime`
instance also moved off `PUID 0`, which is the chown runbook the contract's UID section describes.
Both exporter sidecars answered 401 until the operator patched the keys in Secrets `radarr` and
`radarr-anime`; both targets read `up` in Prometheus on 2026-11-18. Since 2026-11-15 `radarr`'s
`/api` is exempt from the sign-in (a `priority: 100` rule carrying `lan-only` alone), because
`/api/v3` requires `X-Api-Key` on every
request; `radarr-anime` has carried the same exemption since 2026-11-18.

Both instances moved from `6.4.4.10685-ls326` to `7.0.3.10918-ls328` on 2026-11-18, and v7 is what
made the rest possible: the `/api/v3` prefix is unchanged (so `exportarr` keeps working), and Radarr 7
signs users in with OIDC itself. Both now do: `authenticationMethod: oidc`, authority
`https://authentik.kub.home.ashudev.com/application/o/radarr/` (and `…/radarr-anime/`), callback
`/signin-oidc`, `authenticationRequired: enabled`, and the password login is off. The forward-auth gate
and the boilerplate that came with it — `authenticationMethod: none`, `disabledForLocalAddresses`,
trusted ranges — are gone: with `Enabled`, the local bypass would let a forwarded request in as the
admin user without passing authentik at all.

`Allowed Hosts` is set on both, `radarr.kub.local, radarr, radarr.media-stack.svc.cluster.local` and
the anime equivalent, which is what `AllowedHostsCheck` was warning about while `0.0.0.0` stood in for
"any host". ASP.NET enforces that list through host filtering, so the probes send a `Host` header of
their own (`probes.*.httpHeaders`), the way `prowlarr` already did: the kubelet would otherwise send
the pod IP, which no list can name.

### lidarr

As of 2026-11-13: the sonarr pair's shape with a second media claim. Five objects, the
config claim created by the chart because the previous one was Longhorn and vanished with the 2026
disk failure, `/downloads` and `/audio` as named values, and probes on `/ping` — which is not a plain
process ping: `PingController` reads the config repository, so it answers 500 when the database
cannot be opened, and readiness shares it rather than inventing a second endpoint. Its default log
level is `debug`, the noisiest in the stack, so both `LIDARR__LOG__LEVEL` and
`LIDARR__LOG__CONSOLELEVEL` are at `warn`. The config claim is new, so the first start mints a new API
key: the exporter stays down until the operator pastes it into `homelab.env` and the `lidarr` Secret,
and the chart README says so instead of leaving it to be discovered. Since 2026-11-15 its `/api` is
exempt from the authentik gate (a `priority: 100` rule with `lan-only` alone): `/api/v1` requires
`X-Api-Key`, so the key check replaces the gate rather than disappearing with it.

### bazarr and bazarr-anime

As of 2026-11-13: five objects each, one config claim and the shared
`pvc-plex-media` at `/media`, probes on `/` with a 300 s startup budget for a first start on a fresh
`/config`. The `exportarr` block is present and **off**: exportarr v2.3.0's Bazarr collector walks
every episode on each scrape and takes tens of seconds, the 60 s interval would overlap, and the flag
that removes the walk (`DISABLE_EPISODE_METRICS`) is unreleased. No scrape annotation renders while
the block is off, so Bazarr is absent from the target list rather than present and down. Bazarr's
logger has only INFO and DEBUG and no variable selects either, so the chart sets no log level and
records the INFO volume as accepted debt instead of inventing a warning level that does not exist.
The API does reach one switch — `general.debug` in `/api/system/settings` — but it is two-state and
only makes the log louder, so it cannot meet the warning rule either; a pod writing it on every
rollout would also overwrite whatever the operator chose by hand.
Both charts keep the `pvc-plex-media` mount read-write because subtitle files are written next to the
videos, and both keep the LinuxServer root-then-`PUID`/`PGID` init.

### unpackerr

As of 2026-11-13: two objects because it keeps no state of its own and is never exposed,
per-instance values (`sonarr0`, `radarr1`, …) that render `UN_<APP>_<i>_*` through one helper, API
keys from the per-app Secrets, and metrics deliberately off because v1 requires an API key for
`/metrics`. It is the worked example of the metrics rule's other half: when the exporter cannot be
scraped by the annotation job, announce nothing rather than leave a target permanently down.

### maintainerr

As of 2026-11-13: the first chart whose readiness probe is a real dependency check.
`/api/health/live` answers while the process serves and never touches the database; `/api/health/ready`
runs `SELECT 1` and returns `503` when the SQLite file is missing, locked or corrupt — its own path,
so no exception is needed. The image already runs as UID 1000 and never as root, and the chart keeps
it there: `ALL` capabilities dropped, no root phase, and a fresh claim the `local-path` provisioner
creates `0777` and chowns to 1100. It is the one chart where the process UID and the claim owner
differ, which works only because of the directory mode. Upstream gives it **no
authentication at all**: its model is an appliance on a network you control. The LAN boundary is no
longer the only control — since 2026-11-15 the route carries the authentik forward-auth gate, which
is also why its API must stay gated: with no login and no API key, the gate is all it has. The app
had been `Pending` for 76 days on a claim the old chart named and never created.

### calibre-web-automated

As of 2026-11-13: a config claim sized in GiB rather than `128Mi` — 10Gi,
because `/config/processed_books` keeps a copy of every file the import pipeline processes, the only
per-book recovery point for a bad conversion or a bad metadata edit. The ingest drop folder is a
second claim on the same node. The book library on the NAS is mounted read-only in Kavita's pod spec,
so the single-writer rule is enforced by the pod specs. Readiness is disabled, the exception
`tautulli` and `cross-seed` already record: its real dependency, the NFS library, has no endpoint
that reports its health, so readiness on `/` would duplicate liveness and turn a share blip into a
restart loop. The log level is debt of `bazarr`'s kind — no documented variable selects it, so the
chart sets none, accepts default `INFO`, and leaves the activity view and the app database as the
operational record. Its access control is the LAN allow list plus a sign-in: the UI can delete books and
rewrite files, and `calibre.kub.local` is only a DNS entry, so the shared `kube-system-lan-only`
Middleware is what stops an outside request with `403` before the app sees it, and Calibre-Web asks
authentik for a sign-in itself over OIDC (v4.0.8's generic provider, 2026-11-15). There is no proxy gate
in front of it: `/opds` and Kobo sync use basic auth and cannot follow a redirect. It was the first media
tool on HTTPS — `https://calibre.kub.local`, because its OAuth login forces a `Secure` session cookie and
cannot complete a sign-in over plain HTTP — and on 2026-11-15 the whole namespace followed it, one shared
`redirect-to-https` Middleware and one shared `internal-tls` TLSOption from the `traefik-gate` chart.

### kavita

As of 2026-11-13: a rebuild, not a touch-up. The old pod sat `Pending` for months on a
Longhorn claim that vanished in the 2026 disk failure, so the chart now creates `kavita-config` on
`local-path`, 2Gi because the page cache it keeps under `/config` grows with the library. The
library claim is mounted **read-only**, the manifest side of CWA's single-writer rule, and Kavita is
the only authority on reading position. It serves a single public hostname:
`kavita.kub.home.ashudev.com` over TLS on `websecure`, with a plain-HTTP router that redirects, and
no LAN route by decision. Readiness is disabled for the shared reason — a database it cannot open
stops the process from starting, so there is no dependency that can fail while the process stays
healthy.

### qbittorrent

As of 2026-11-18: the last media-stack chart to go through the contract pass, and the only one with a
privileged sidecar. Six objects render — Deployment, Service, ServiceAccount, the WireGuard claim and
the two IngressRoutes — with the API group in a value, every name derived from the release, and
`/config` and `/downloads` as named mounts of two NFS claims this chart does **not** own (both created
by their own `nfs-qbittorrent-*` Applications). The one claim it does create is
`qbittorrent-vpn-config`, defaulted to `<fullname>-vpn-config`, which is the name the live pod already
mounted, so nothing moved.

The tunnel decides the security context: `ghcr.io/linuxserver/wireguard` needs `privileged`, adds
`NET_ADMIN`/`SYS_MODULE` and mounts `/dev/net/tun`, with seccomp `Unconfined` stated explicitly so the
pod-level `RuntimeDefault` is not read as applying to it. qBittorrent itself takes
`allowPrivilegeEscalation: false` and the pod-level seccomp profile, and keeps the default capability
set: the LinuxServer image starts as root, prepares both NFS mounts through s6, then drops to
`PUID`/`PGID` 1100, the same position as `sonarr` and `prowlarr`.

Probes are the deliberate narrow case: qBittorrent publishes only `auth/login` without a session, so
liveness is a TCP connect to the WebUI port and readiness is off — an `httpGet` probe would either fail
forever or silently depend on the `10.42.0.0/16` auth bypass. The route keeps the authentik gate and
carries **no** `/api` exemption, because nothing in the cluster reaches the hostname: cross-seed, the
exporter and autobrr all use `http://qbittorrent:8080`. The log level is worse off than `bazarr`'s:
qBittorrent has no verbosity setting at all, only file-logging on/off, so the chart sets nothing, and
the VueTorrent installer's traps are recorded above its values block.

### ashuvidz-website

As of 2026-11-18: the public site, and the one public chart taken through the pass — its routes were
left byte for byte, which is the rule for a public-facing chart: ingress, TLS and auth belong to W5
and W7, and a contract pass is not permission to redesign them.

It is a wrapper (pattern D) around the Bitnami `nginx` chart, so seven objects render and only two
templates are written here, the pair of IngressRoutes. Everything else — Deployment, Service,
ServiceAccount, NetworkPolicy, PodDisruptionBudget — comes from upstream values. That buys the one
mechanism a static site needs and would otherwise be hand-written: an init container that clones the
content repository into an `emptyDir`, and a sidecar that `git pull`s it every 60 seconds. A content
push to `vyrtualsynthese/ashuvidz-website` is live in about a minute, with no build, no image and no
sync. The chart README carries that flow, because "someone built it once" was the state the card
found.

What the pass changed: `resourcesPreset: nano` became an explicit block measured against 5 days of
Prometheus — nginx 24Mi p95, 24Mi peak, and the git syncer 21Mi / 25Mi — with the nginx limit left at
128Mi because the same block also covers a `git clone` init container too short-lived to be scraped,
and `ephemeral-storage` kept because `/app` is a clone on disk and one bad clone should not fill the
node; the pod is pinned
to `nuc0`, where the other small front ends live; `appVersion` now matches the image tag; and the
leftover `metrics.serviceMonitor` / `metrics.prometheusRule` flags, true while the exporter was off
and no Prometheus Operator exists, are false.

Probes are the honest pair for files: readiness on `/`, which *is* the site, and liveness on a TCP
connect to the HTTP port. A bad content push leaves the pod `0/1 Ready` instead of restarting it.

Debt: the exporter stays off, since exporters are turned on one chart at a time; and nginx's access
log runs at its default level — no value in the wrapped chart exposes it — so per-request lines still
reach Loki. Both are recorded in the chart README rather than invented away.

## Charts still to align

Only non-public apps with hand-maintained charts are in scope for now. Public-facing apps wait,
because their ingress, TLS and auth wiring is what changes first in W5 and W7; `ashuvidz-website` is
the exception, aligned on 2026-11-18 with its routes untouched because its card asked for exactly
that.

Metrics count as debt of the same kind. The rule above is recorded now and applied one chart at a
time; `flaresolverr` is the first, and the rest are not aligned yet.
