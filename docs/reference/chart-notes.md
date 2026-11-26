# Chart notes: the per-app record

> **In one line.** One chart per application, built from `autobrr` rather than from a shared library:
> this is how each chart meets the contract, where it had to diverge, and which ones have not caught
> up.
>
> **Use it when** you are building or reviewing a chart and want the worked example closest to it ·
> **Verified** 2026-11-17 · **Kind:** reference
>
> **Sources** `cluster/helm/**/values.yaml`, `docs/charts/README.md`, and each chart's templates

## What good looks like

### autobrr

As of 2026-11-11: five objects render, ingress fully driven by values, env inline in the
Deployment, claim name derived from the release.

### cross-seed

As of 2026-11-12: four objects render, no ingress because the UI is not exposed, named
`configFile` and `torrents` values instead of raw pod spec, liveness only, claim name derived from
the release. It shares the naming helpers and the generic Service, ServiceAccount and claim
templates with `autobrr`; the Deployment and the values are built around cross-seed's read-only
config file, its NFS torrent directory and its liveness-only probe. Its logger has two ordinary
levels and one switch: `info` is the default and `--verbose` only raises, so there is no level for
the chart to set. The chart says so next to `env` and the README says what `info` costs.

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
no node-side `chown` and needs no runbook. Since 2026-11-19 `args` carries `--log warn`: a redirected
stdout switches recyclarr to log mode at `info` by itself, and a run's progress lines are not worth
the volume. The Job status and the arr settings the sync changes carry the outcome.

### renovate

As of 2026-11-19: the second batch-job case, and the first wrapper chart over a chart whose values
name their own root. Two objects — ConfigMap `renovate-config` and CronJob `renovate` — no listener,
no probes, no Service, no ingress; the exit status and the run's log are the health signal. The
wrapper pins upstream `renovate` 46.443.0 and the image at 44.251.0, which is also its `appVersion`.
The chart version now lives in `Chart.yaml`'s dependency and nowhere else; the Application points at
the directory, so Renovate's `helmv3` manager owns the chart and its `docker` manager owns the tag.

The bug that kept it `OutOfSync` was not the version: the values carried an `affinity` block one
level too high, without `nodeAffinity:` above it, and the API server pruned it to `affinity: {}` on
every sync. That block also asked for the retired `node-type: general` label. Both are gone; the pod
is pinned to `nas` with `nodeSelector`.

It runs at 03:00 Europe/Paris — after the *arrs' midnight housekeeping, before the 04:30 database
dump — with `Forbid`, `backoffLimit: 1`, a 90-minute deadline and three days of finished Jobs. It
takes the batch exception on probes and readiness, and its image already runs as UID 12021, so
non-root, `allowPrivilegeEscalation: false`, `drop: [ALL]` and the runtime seccomp profile cost
nothing. **Log level is a deliberate divergence:** the contract asks for `warning`, but the nightly
log is 300-350 lines and about 16 KB, it is the only per-repository record of what was scanned, and
`recyclarr` — the other batch job — keeps `info` too. Resources are a protection ceiling (256Mi
requested, 1Gi limited), not a measurement: a run is shorter than the scrape interval. It still runs
under the namespace's `default` ServiceAccount, which mounts a token Renovate never uses; the
upstream chart cannot turn that off without an `extraObjects` manifest.

### tautulli

As of 2026-11-13: the first chart in the media stack with **no public route at all**. One
IngressRoute at `tautulli.kub.local`, LAN-only — and since 2026-11-15 that means two of them, a
`websecure` route and a `web` redirect, like every other `kub.local` route in the namespace. It also carries the second deliberate readiness exception: Tautulli reports Plex
reachability and SQLite integrity only in a JSON body with HTTP 200, which the kubelet cannot read,
so readiness is disabled rather than pointed at liveness. Its image starts as root and drops to
`PUID`/`PGID`, so the capability set is `ALL` dropped plus the three calls that init makes. Since
2026-11-18 the route carries `lan-only` and no authentik gate, by decision: the gate cannot carry an
identity into the app, so every browser would share one Tautulli account and per-user watch history
would collapse. The console handler has no level either — INFO on stdout, WARNING on stderr, with
`--quiet` as the only other switch and it hides the errors too — so the chart sets nothing.
Tautulli's own login is the gate, and it keeps the login it had; the Plex-only toggle
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

2026-11-19: it was the loudest container in the namespace, ~22,000 lines and 19 MB a day. Two thirds
of that was the per-item `still waiting`/`Status` pair, one per tracked item per minute, and every
one of those items was an *arr queue entry Sonarr or Radarr refused to import. Four Sonarr
`Not an upgrade` records were removed from the queue (`removeFromClient=false`, nothing deleted); one
Radarr download stays because the film has no file and needs a manual import. The remaining floor is
the liveness probe's access line, one every 30 seconds. The chart README carries the table and the
rule: an item that keeps appearing in the log is a stuck queue entry, not a level to lower.

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
operational record. 2026-11-19 added the part that is not a level: `NETWORK_SHARE_MODE=true` is one
switch over three mounts, and upstream skips the recursive `chown` of `/config` and
`/cwa-book-ingest` because of it. Only `/calibre-library` is NFS, so the app was starting as 1100 on
two `local-path` claims it did not own — `install: cannot change owner`, `attempt to write a readonly
database`, an AutoZipper that never started. The `claimOwnership` init container now does that chown,
as root with `CHOWN` and `DAC_OVERRIDE`; turning the mode off instead would put `metadata.db` in WAL
on NFS. The same rollout showed the capability set was a second defect of its own: `root` without
`DAC_OVERRIDE`/`FOWNER` cannot read, write or `chmod` what 1100 owns, so the image's own init failed on
`/app` and on `app.db` before any chown could help, and `sqlite3` reports that as `attempt to write a
readonly database`. Both containers carry them now; nothing else was added and `privileged` stays
false. Its access control is the LAN allow list plus a sign-in: the UI can delete books and
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
healthy. The log level is debt of `bazarr`'s kind with a specific cause: `Program.cs` clears its
configuration sources and loads only `appsettings.json`, so no environment variable reaches Serilog
and the level stays at `Information`.

### freshrss

As of 2026-11-19: the same class of failure as `kavita` — a pod `Pending` for 77 days on a claim its
chart mounted and never created — fixed the other way round, because there was nothing left to
preserve. The old volume was on `longhorn-static`, its contents went with the storage in 2026-08, and
its PV has since been cleared from the cluster, so this is a fresh start with no data to rescue. The
chart creates `news-freshrss-config` on `local-path`, 1Gi: FreshRSS keeps its SQLite database *and*
the article bodies it has fetched under the data root (`/config/data`), so the 128Mi settings-sized
default would be full
within days. The pod stays on `nuc0`, which is what the retired `node-type: general` label used to
select.

On 2026-11-19 the chart also moved to the **official `freshrss/freshrss` image**, because native OIDC
needs the Apache module `mod_auth_openidc` and the LinuxServer image (nginx) has none. Two
consequences. The data root is `DATA_PATH=/config/data`, not the claim root: the claim root was
chowned to uid 1100 by the old image, and the container can change a file's group but not its mode
without `FOWNER`, so the Apache worker could not write there. And the capability set keeps
`DAC_OVERRIDE` for a new reason: the entrypoint runs as root and must create that data directory
inside the 1100-owned claim. The old LinuxServer tree stays at `/config/www`; its contents were
copied into `/config/data`, so the subscriptions and the read state survived the image swap. Two
requirements came out of that copy, and the chart README spells them out: the user directory has to
carry the authentik username, because `http_auth` looks the account up by name and nothing is
provisioned on first sign-in, and `auth_type` has to be `http_auth` rather than the wizard's `form`,
or the app asks for a second, local password after authentik.

Two things were unusual already and stay. **No health endpoint exists**, so the probes split the
question: startup and liveness are a TCP connect, while readiness is `GET /i/`. With OIDC on that
path answers `302` to authentik, which a probe counts as ready; it still boots Apache and PHP, so a
dead worker fails there and passes the TCP check. `/` is never used: it is a 301 to `p/` and answers
with a broken backend. And **there is no log-level variable**: FreshRSS logs per user under the data
root and leaves PHP errors on stderr at the `php.ini` default, so the chart sets nothing and the
README says what that costs.

The route has no authentik middleware. OIDC in the app is the only prompt; only `/i/` is behind the
module, so `/p/api/` keeps its own login and the phone app and the feed sync keep working. `lan-only`
stays, because a sign-in is not a network boundary.

Debt: the refresh cron enabled by `CRON_MIN` writes `actualize_script.php` output to
`/tmp/FreshRSS.log` inside the container, which Fluent Bit does not read, so refresh failures are only
visible with `kubectl exec`; the `security` middleware renders but no route lists it; and the sizing
is unmeasured. The `FreshRSS-AutoTTL` init container is gone — it cloned from `https://server/repo`, a
placeholder that could only fail.

### koillection

As of 2026-11-19: the third card of the same defect, and the same shape as `freshrss` — a pod
`Pending` on claims its chart mounted and never created (`koillection-conf`, `koillection-uploads`),
both PVs already cleared from the cluster, contents abandoned by decision. The chart now creates
both: `koillection-conf`, 1Gi on `local-path` at `/conf`, and `koillection-uploads`, 20Gi on
`stash-nfs` at `/uploads`. Nothing is lost that was not lost before — and nothing is restorable
either: the shared instance was rebuilt empty on 2026-11-13, and its database log has no koillection
row, so there is no database and no dump to restore. First start is empty, and the database has to be
created by hand from `runbooks/postgres-add-database.md` before the app can answer a query.

What the image actually needs is narrower than the old chart assumed. It serves on :80 (the values
file claimed 5432, a PostgreSQL copy-paste), persists only `/uploads`, and declares its health in the
Dockerfile as `curl --fail http://localhost:80/` — so the startup and liveness probes use `/`, and
readiness is off, because nothing unauthenticated reports whether PostgreSQL is reachable. `/conf` is
dead weight the image never writes to; it stays because the card asked for both claims and the
deployment mounted that path, and removing it is follow-up work rather than part of getting the pod
running. The pod is pinned to `nuc0` by `nodeSelector`, replacing an `affinity` on the
`node-type: general` label. That label was still on `nuc0` at the time, so it was never what kept the
pod `Pending` — the two claims the chart mounted and never created were. The label itself was
removed from the nodes on 2026-11-22.

The syncs after the merge bound both claims and scheduled the pod on `nuc0`, and then found the one
thing the chart still had wrong. The image has no `USER` instruction and its entrypoint must be root:
it writes `php.ini` under `/usr/local/etc`, `/app/public/.env.local` and the `/uploads` symlink
**before** it rewrites its user and `setpriv`s away. Two attempts settled the shape. Root with every
capability dropped cannot write `/app/public`, which belongs to uid 1001
(`touch: cannot touch '/app/public/.env.local': Permission denied`). Running as 1001 passes that write
and dies on `php.ini` (`Permission denied`), which it cannot skip. So the chart keeps the default
capability set and sets only `allowPrivilegeEscalation: false` — `prowlarr`'s shape for a root init.
Hardening that cannot start the app is not hardening, and leaving the level at `info` for a week
while the pod crash-loops would have been worse than the two failed syncs were. The last failure was
the operator's: the `koillection` Secret still named the retired Bitnami Service, and the database did
not exist. Both were fixed by hand on 2026-11-19 and the app runs 1/1 on its own claims, with the
account created and the API answering.

### thelounge

As of 2026-11-19: the last application still mounting a claim that could never clear, and the one
whose route was ungated — `routes.md` recorded `none` while the checked-in chart already asked for
`lan-only`, which is half of why the Application had been `OutOfSync` since the Longhorn failure.

The old chart mounted `thelounge-volume`, a 20 GiB `longhorn-static` claim `Terminating` since
2026-08-28 and held only by the running pod. Twenty gigabytes was a guess for what is a
`users.json`, a log directory and per-network settings. The rebuild creates `thelounge-config`,
`128Mi` on `local-path`, mounted at `/var/opt/thelounge` — the image's own `THELOUNGE_HOME`. The
contents of the old claim were abandoned by decision on 2026-11-13 and are not migrated: first start
is empty, and the accounts and networks are re-created by hand. The old claim and its volume are
cleared with the disposition loop once the pod that held the mount is gone.

The image ends its Dockerfile with `USER node:node`, so the chart runs as UID 1000 rather than the
house 1100. A new `local-path` volume arrives world-writable, so the claim needed no `chown`; the
chart must not set `fsGroup`, which the kubelet never applies on hostPath-backed volumes. The old
`affinity` on the retired `node-type: general` label is replaced by `nodeSelector` on `nuc0`.

The probes are the narrow pair. The image has no health endpoint: `/` returns the login page with a
200 whether or not an IRC network is reachable, so liveness is a TCP connect on 9000 and readiness
is off, with the reason next to the values. `config.js` is a file, so its source is a ConfigMap; an
init container copies it into the claim as UID 1000 on every start. The rebuild had removed that
copy, and it came back on 2026-11-19: The Lounge's `verifyFileOwner()` warns on every start when
`<home>/config.js` is not owned by the UID it runs as, and every file a ConfigMap volume provides is
owned by root, so a `subPath` mount of it is a warning on every start by construction. The copy uses
the app's own image rather than another `busybox`, and the Deployment still hashes the ConfigMap into
`checksum/config`, because the copy happens at pod start and an edit otherwise waits for a restart
nobody planned.

The sign-in is LDAP since 2026-11-20, not a gate. The Lounge has no OIDC, but its own LDAP plugin is
native, so authentik runs an LDAP outpost (`authentik-ldap`, port 3389, rendered by the authentik
chart, authenticated by Secret `authentik-ldap` — a missing Secret is a `CreateContainerConfigError`,
not a failed sync) and `config.js` binds directly as the person — `baseDN` with `primaryKey: "cn"` and no
`searchDN`, so no service account and no bind password exist to store. The route is `lan-only` then
`security`; the forward-auth gate is gone because a gate in front of an app that authenticates is a
second prompt that carries no identity. The app's local passwords stop working and the authentik
password replaces them. The two chart-local Middlewares (`thelounge-security`,
`thelounge-redirect-https`) are replaced by the shared `kube-system-*` copies; the `TLSOption` stays
per-app, because it belongs to the `*.kub.home.ashudev.com` certificate, not to the internal
`*.kub.local` policy.

Sizing came from the pod that had run 98 days: p95 76Mi, peak 81Mi working set, CPU peak 2m. Request
64Mi → 80Mi, limit 128Mi unchanged. There is no log-level knob — console output follows the `debug`
object in `config.js`, both keys off — so the chart sets nothing and says so next to `env`.

Debt: no metrics exporter in the image, and the outpost listens on plain LDAP inside the pod network
(a certificate on the provider plus the internal CA in the app's trust store is what LDAPS would need).

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

2026-11-19: the installer stopped being able to fail the pod. It ran `set -eu` and a bare `wget`, so
a first boot with no route to github.com — cold cluster, fresh claim, GitHub outage — kept the init
container from finishing and the pod from ever starting, over a UI asset. Every step is now guarded:
it logs the step that failed plus one line saying qBittorrent starts on its stock WebUI, and exits 0
with `/config/ui` untouched, staging still staging-before-swapping. `apk` runs under `timeout 60` and
`wget` under `-T 20`, so a blackholed address costs seconds rather than the kernel's retry budget.
Tested by running the rendered script with stub `apk`/`wget` and real `unzip`: no network, download
failure, staging failure with a working UI present, and the no-op restart.

2026-11-19: the sidecar's `AllowedIPs` is `0.0.0.0/0`, and wg-quick's local-access rule only spares
prefixes the pod's main table knows specifically — which is its own subnet and a default route. So
every ClusterIP went into the tunnel, and the post-download hook to `cross-seed:2468` died after the
kernel's 127 s SYN retry budget, 154 times in 24 h. A `vpn-cluster-routes` init container now adds
`10.42.0.0/16` and `10.43.0.0/16` to that table through the pod's gateway; the ranges are
`vpn.clusterRoutes`, so a second in-cluster dependency is one line. The hook should also carry
`--max-time 10`, which is an app preference, not a chart value.

2026-11-25: the chart now owns both claims it mounts, and it no longer references an NFS claim at all.
`/config` moved to `local-path` on 2026-11-25 (card 758) and to `nas` with the pod that evening; the
WireGuard claim went with it, because a `local-path` claim is bound to the node that first mounted it
and a pod elsewhere leaves it `Pending`. `/downloads` is now `storage.downloads.hostPath`, the export
root `/mnt/ultrafaststorage/nfs` mounted from the host, with `type: Directory` so a missing export
fails the pod rather than creating an empty tree — the pod is pinned to `nas`, which is the node that
exports it, and the *arrs keep mounting the same directory over NFS. The reason is the Web UI stall:
with the app writing through an NFS client, one `close()` stopped returning and took the API with it,
and the same load on a local filesystem did not. A `local-path` claim would have created a
subdirectory beside the export root that no NFS client reads, which is why this one mount is a host
path. [`../how-it-works/storage.md`](../how-it-works/storage.md) states the rule,
[`../qbittorrent-webui-stalls.md`](../qbittorrent-webui-stalls.md) carries the evidence, and
[`../runbooks/qbittorrent-to-nas.md`](../runbooks/qbittorrent-to-nas.md) is the move, including the
two claims' copy and its rollback.

### ashuvidz-website

As of 2026-11-18: the public site, the one public chart taken through the pass, and the one that went
from a wrapper to a hand-written chart inside the same day. Its routes were left byte for byte, which
is the rule for a public-facing chart: ingress, TLS and auth belong to W5 and W7, and a contract pass
is not permission to redesign them.

**Why it stopped wrapping.** The chart was pattern D around `bitnamicharts/nginx`, and that chart
pins `bitnami/nginx:1.28.0-debian-12-r0`. Bitnami then moved every versioned tag to the frozen
`bitnamilegacy/*` namespace and publishes only `latest` under `bitnami/*`, so the tag stopped
resolving. The failure stayed invisible while the only pod that needed the image was on `nas`, which
had it cached since before the move; pinning the pod to `nuc0` is what exposed it, as
`Init:ImagePullBackOff`. Repointing at `bitnamilegacy` restored service, but that archive takes no
security fixes, and this is a public web server. The chart now owns its Deployment: six objects
render from five templates, and `helm dependency build` is no longer part of rendering it.

**What it is now.** `nginxinc/nginx-unprivileged:1.31.7-alpine` — the nginx team's non-root build on
8080 — plus `alpine/git` for the two containers that move content: an init container that clones
`vyrtualsynthese/ashuvidz-website` into an `emptyDir` at `/site`, and a sidecar that pulls it every
60 s with `--ff-only`. A content push is live in about a minute, with no build, no image and no sync.
The chart's own `nginx.conf` is in a ConfigMap, mounted with `subPath` and hashed into a
`checksum/config` annotation so an edit rolls the pod.

Probes are the honest pair for files, and now both endpoints are the chart's to choose: liveness on
`/healthz`, a `return 200` in the config, so a half-finished clone can never restart nginx; readiness
on `/`, which *is* the site, so a failed clone leaves the pod `0/1 Ready`. The init container also
fails when `index.html` is missing, so a blank site never presents as healthy.

Hardening is total because the image allows it: UID 101 for all three containers, `fsGroup` on two
`emptyDir`s (where the kubelet does apply it, unlike `local-path`), dropped capabilities, no privilege
escalation, `RuntimeDefault` seccomp, read-only root filesystem with nginx's temp paths moved into an
`emptyDir`, `/site` mounted read-only into nginx, dotfiles denied so `/.git` is not downloadable, and
no ServiceAccount token.

Sizing carried over: nginx 24Mi p95 / 24Mi peak from the 5-day window, request 32Mi, limit 128Mi
because the clone init container has no sample to size from; the content containers take the
displaced syncer's 21Mi / 25Mi as their stand-in. The pod stays on `nuc0`.

Debt: the access log is on — on a public host the request record is the only evidence of what
arrived, and the error log is the part that runs at `warn` — and there is no metrics exporter, which
is the chart-at-a-time metrics work the repo has not reached here.

### ashudev-website

As of 2026-11-20: the second public site, and the first one that was never anything but a
hand-written chart. It exists to take `ashudev.com` off GitHub Pages without touching the site
itself — the content is still the repository's `gh-pages` branch, and the migration is hosting only.
It is `ashuvidz-website` with the same three containers (`nginx-unprivileged` on 8080, an
`alpine/git` init container that clones, a sidecar that refreshes), the same `emptyDir` at `/site`,
the same hardening (UID 101, dropped capabilities, read-only root, `/site` read-only in nginx,
dotfiles denied so `/.git` is not downloadable) and the same probe pair: liveness on the chart's
`/healthz`, readiness on `/`, which is the site. The pod is on `nuc0`.

**Two deliberate divergences from the chart it was copied from.** The sidecar fetches and resets
rather than pulling `--ff-only`, because Pages rewrites `gh-pages` on every publish and a
fast-forward-only pull against a rewritten branch fails forever, which serves yesterday's site with
nothing red anywhere. And the config resolves `/name` to `name.html` before giving up, because that
is what Pages did — `/services` answered 200 there and would have answered 404 here, and it is
linked from outside. `/index.html` is served rather than redirected to `/`; that costs a duplicate
URL, not a working one.

**The reset is not atomic.** `git reset --hard` rewrites `/site` file by file while nginx serves it,
so a request in that window can read a new `index.html` beside a bundle that is not written yet. The
window is one reset of a small tree; an atomic version needs a second directory and a symlink swap,
which this site does not earn yet. A failed fetch leaves the previous tree; a failed reset leaves a
half-written one, and the sidecar now says which of the two happened instead of reporting the
misleading one for both.

**`www.ashudev.com` has its own router**, which is the defect `ashuvidz-website` carried: Traefik
asks the ACME resolver for a name only when a router matches it, so a `www` that resolves without a
router answers with the default certificate and a name mismatch. The redirect is a `redirectRegex`
that keeps path and query, and the `www` router sets `tls: {}` on `websecure` — its name is in the
`ashudev.com` wildcard's SAN list, so it asks for nothing of its own. The host match is
case-sensitive where Traefik's `Host()` is not, so an uppercased `www` URL falls through to the
no-op service rather than redirecting; that is filed with the sibling's two defects rather than
fixed here.

Sizing is carried from `ashuvidz-website`, not measured: no pod had run when the chart was written.
Debt: the access log is on for the same reason as the sibling — on a public host the request record
is the only evidence of what arrived — and there is no metrics exporter.

### minecraft-server

As of 2026-11-19: repaired but deliberately not running. Five objects render — Deployment, Service,
ServiceAccount, the `-data` claim and the `IngressRouteTCP` — with `replicas: 0` until the
anti-cheat, anti-grief and backup work lands. The old chart mounted `minecraft-lh-2`, a Longhorn claim
that no longer exists, so the pod had been Pending since 2026-08-28; the replacement is
`<fullname>-data`, `local-path` on `streaming`, `20Gi`. The claim name is `-data` rather than the
contract's `-config` and the size is world-sized, both on purpose.

Two changes there were corrections rather than copies. The exporter inherited the server's `12Gi`
limit and `1000m` request for a sidecar that polls the server list ping; it now has `32Mi`/`64Mi`.
And the plugin list was passed as `MODS`; on a plugin-only type the image copies either `MODS` or
`PLUGINS` into `/data/plugins`, so the jars did install, but the chart now says `PLUGINS` and matches
the image's own Paper documentation. Whether the previous world ever had Multiverse and LuckPerms is
unverifiable: the server has not started since 2026-08-28.

The image has no `USER` instruction: the entrypoint starts as root, rewrites its `minecraft` account,
chowns `/data` and `gosu`s to uid 1000, so the server process itself is not root. The chart keeps
`CHOWN`/`SETUID`/`SETGID` for that path and sets `allowPrivilegeEscalation: false` plus the runtime
seccomp profile; it does not pin a UID, because running as 1000 directly depends on `HOME` being
writable, which the image does not set, and no pod has run to prove it. The claim is fresh — the
Longhorn claim was abandoned by decision, so a first start is an empty world. Probes are a TCP connect to the game
port, shared between readiness and liveness because there is no second endpoint to ask. The TCP route
is preserved verbatim (`mc` entryPoint, `HostSNI(*)`); an HTTP `lan-only` middleware cannot gate a TCP
router, so the router's port closure is the only thing keeping it off the internet. Paper's log level
lives in `/data/config/log4j2.xml`, which the image generates and the chart does not own, so `info`
reaches Loki until a later change takes that file. Heap, memory reservation and the Meowice JVM flags
are unmeasured.
### syncthing

Rebuilt on 2026-11-19 off the dead Longhorn claim. The chart now creates its own config claim,
`syncthing-config` on `local-path`, because `syncthing-conf` is held by a `Terminating` object whose
name cannot be reused. 1Gi rather than `128Mi`: the index database for the media tree does not fit
in 128Mi, and the old claim was the same size.

The pod is pinned to `nuc0` — where the app already ran, and where AGENTS.md places the general
catalogue — instead of carrying the `node-type: general` affinity. That label answered on `nuc0`, so
it was not what kept the replacement pod `Pending`: the `Terminating` claim was. The label was
removed from the nodes on 2026-11-22. The claim is `WaitForFirstConsumer`, so it binds to whichever
node schedules first: the stuck old pod has to be force-deleted before the first sync, or the claim
binds to a node the new pod cannot use.

Probes sit on `/rest/noauth/health`, the one unauthenticated endpoint. Readiness shares it: a
disconnected folder is a sync state, not a dependency failure, and the process is the only thing
that can fail. `PUID`/`PGID` moved from `0` to `1100` with `TZ` from `Etc/CEST` to `Europe/Paris`;
`runAsUser` stays absent because the LinuxServer init starts as root and drops, and a fresh
`local-path` claim is already owned by 1100.

Debt: no log level. Syncthing has no verbosity variable (`STTRACE` only raises), so it runs at
`info` and the GUI is the other record. The restic CronJob this chart carried until 2026-11-20 is
gone; its folder is the `nfs` stream of `cluster/helm/backup`.

### filebrowser

As of 2026-11-20: the chart owns its templates (pattern A) after the community chart it depended on
was found to render no scheduling field at all. Every object name and the Deployment selector are the
ones that chart rendered, because a Deployment selector is immutable and the config claim holds the
database with the OIDC users and scopes. `nodeSelector` pins `nuc0`; the claim was provisioned there
and `local-path` binds on first consumer, so any other node would leave it unschedulable. Liveness
and readiness share `/health` — the only dependency is the local SQLite database, which fails with
the process — and `server.logging` keeps `warning|error`. Uploads run as uid 1100 with primary gid
1000; the primary group is the mergerfs requirement, not a preference. Debt: the four startup lines
the app logs before it reads its config stay at `INFO`; the level applies after that. The 1 Gi
claims are the ones the previous chart created and a `local-path` claim cannot shrink.

### crowdsec-web-ui

As of 2026-11-25: the reference chart's templates, unchanged, with this image's values (pattern A,
six objects). It is the second view of the CrowdSec stack: it reads the LAPI over the cluster network
at `http://crowdsec-service:8080` and needs no Prometheus, Loki or Grafana, which is the point — the
Grafana dashboard is still the trend view, this one has the button that undoes a wrong ban. It
authenticates with its own OIDC (blueprint `app-crowdsec-web-ui`), the route is `websecure` +
`internal-tls` + `lan-only` on `crowdsec.kub.local`, and there is no gate in front, because native
OIDC comes first here. Two credentials it needs are not issued by authentik: the LAPI machine
(watcher) it logs in as, and the client secret; both are keys of the `crowdsec-web-ui` Secret, made
by hand. Liveness and readiness share `/api/health` — it reports the HTTP listener, not the LAPI,
because restarting the UI during a LAPI outage changes nothing and loses the sync. The server
documents no log-level variable, so container logs stay at its default and the audit log
(`[audit]` JSON lines on every decision change) is the record instead; that is written next to `env`
in the values. It runs as uid 1000, the image's own `node` user, on a fresh claim, so no `chown` on
the node was needed; the claim is 1Gi rather than 128Mi because it mirrors a 168h alert lookback.
Sizing is unmeasured until the first night: 20m/128Mi requested, 512Mi limit.

### backup

As of 2026-11-20: one chart, six CronJobs, no library, and the only chart in the repo whose values
file is also a document — `values.yaml` is the coverage matrix, so adding a stateful application means
adding a row in the same change. Three node jobs read their node's `local-path` root as a `hostPath`,
because the claims they cover live in eleven namespaces and a claim mount per namespace would be
eleven pods; nothing is ever mounted read-write except the staging `emptyDir`.

Two containers per node job, and the split is the point: a Python `initContainer` copies each SQLite
database through the module's backup API into staging and writes the two path lists restic reads,
then the restic container does one backup run. That is the only place a claim is read through a copy
rather than directly, and it exists because a database read as bytes while it is being written
restores to a file that opens and is missing its last minutes.

Both containers run as root with `DAC_OVERRIDE` and `DAC_READ_SEARCH` and nothing else. A `hostPath`
mount ignores `fsGroup`, so a configuration directory written by `PUID` 1100 is readable as 1100 or as
root; dropping all capabilities from root would leave it unreadable, and the mounts are read-only so
there is nothing for those two capabilities to write.

No job runs `restic init`. The four repositories are created once by hand: a job that could create one
could create an empty repository where a lost one used to be, and the next night would look healthy.
The prune job renders each repository's retention flags from `values.yaml` — the shared tail, plus
whatever the stream overrides on its own entry — so the schedule and the policy cannot drift apart,
and the verifier is a script rather than a rule so its line can be the dashboard's input as well as
the alert's.

### pihole-exporter

As of 2026-11-26: one object, the Deployment. The app is stateless and Prometheus
discovers the pod by annotation, so there is no Service, no claim and no route; the
three scrape annotations are rendered in the template from `metrics.port`, which is
also `PORT` and the `containerPort`, because the chart had announced `9902` for a
listener on `9617`.

One instance reads both edge Pis (`PIHOLE_HOSTNAME` is a list), and the two app
passwords are mounted as files and named to the exporter through
`PIHOLE_PASSWORD_FILE`, one path per host in the same order: the Pis mint their own
passwords, so a single `PIHOLE_PASSWORD` could not carry both, and a file keeps the
secret out of the process environment. `values.yaml` fails to render when the two
lists differ in length, because the pairing is positional.

The hop to each Pi is HTTPS on 443 — Pi-hole v6 terminates TLS itself — with
`SKIP_TLS_VERIFICATION=true`. The certificate is the one each Pi mints at first
start: CN `pi.hole`, self-signed, and the same CN on both hosts, so no client can
verify it against a name it reached. What that buys is encryption between `nuc0`
and the Pis, not an authenticated peer; the fix is a leaf per Pi from the internal
CA (`hack/internal-cert.sh --host`), which is a Pi change and a card of its own.

Liveness and readiness share the image's own `-healthcheck`, which asks `/liveness`
on `PORT`. The only failure that leaves the process up is Pi-hole refusing the
session, and restarting cannot fix a credential: it shows as a warning in the log
and as missing `pihole_*` series. The image is `scratch`, so the binary is the probe
— there is no shell to run one. `privileged: true`, copied from the boilerplate,
was dropped with `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`, a
read-only root filesystem and the image's own uid 65532.

## Charts still to align
Only non-public apps with hand-maintained charts are in scope for now. Public-facing apps wait,
because their ingress, TLS and auth wiring is what changes first in W5 and W7; `ashuvidz-website` is
the exception, aligned on 2026-11-18 with its routes untouched because its card asked for exactly
that.

Metrics count as debt of the same kind. The rule above is recorded now and applied one chart at a
time; `flaresolverr` is the first, and the rest are not aligned yet.
