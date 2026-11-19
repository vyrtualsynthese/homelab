# FreshRSS behind authentik — design

Date: 2026-11-19

## Intent

FreshRSS at `https://freshrss.kub.home.ashudev.com` keeps its own password form today and its route
carries only the LAN allowlist. The goal is one sign-in: an authentik session is the only credential,
and the app asks authentik directly using OIDC (the OpenID Connect sign-in standard the lab already
uses for Grafana, Vikunja, autobrr and Kavita) instead of a proxy gate in front of it.

## What decides the shape

- **Native OIDC exists only in the official image.** FreshRSS implements OIDC through
  `mod_auth_openidc`, an Apache module. The upstream docs say it ships in the official
  `freshrss/freshrss` image (Debian, x86_64) and not in Alpine or in third-party images. This chart
  runs `lscr.io/linuxserver/freshrss`, which is nginx and has no such module, so native OIDC means
  replacing the image.
- **The account names differ.** authentik knows `vyrtualsynthese`; the running FreshRSS instance
  knows `vronteix`, and all of its data (one feed, no categories, the API password hash) sits under
  `users/vronteix`. An OIDC login is whichever name the provider claims, so the existing user
  directory cannot simply keep working.
- **The feed list is not data.** `hack/freshrss/feeds.opml` is the source of truth and the database
  is documented as its cache (`docs/news-feeds.md`). Replaying it onto a rebuilt instance gives the
  same feeds back.
- **The API has its own login.** The reference Apache configuration protects `/i/` with OIDC and
  leaves `/p/api/` alone, because API clients (the phone app, `hack/freshrss_sync.py`) send an API
  password, not a browser session. So no route split is needed and the API keeps working.

## Approaches

1. **Outpost header auth.** Keep the LinuxServer image; an authentik proxy provider with a property
   mapping injects `X-WebAuth-User`, and FreshRSS switches to `http_auth` with the Traefik pod as a
   trusted source. One login, no image swap. **Rejected by the operator**: it is the proxy tier, and
   native OIDC is preferred where the app supports it.
2. **Native OIDC with the official image.** Replace the image, configure the app's own OIDC
   environment, create the provider in authentik. **Chosen.** It trades a chart rewrite and a fresh
   data directory for the app speaking the protocol itself, with no proxy in front of it.

## Decisions

- **Image.** `freshrss/freshrss:1.30.4` (Debian, x86_64). Same upstream version as the running
  instance (`1.30.4-ls332`), so the data format does not change. `Chart.yaml` appVersion follows the
  tag. `nuc0`, where the pod is pinned today, is x86_64.
- **Data directory: fresh, at `/config/data`, with `storage.mountPath` left at `/config`.** The
  claim root is owned by uid 1100 because the LinuxServer init chowns `/config`, and its group-write
  bit is not known. The image's permission fix-up (`access-permissions.sh`) can change the *group*
  but not the mode of files it does not own — the chart adds `CHOWN`, not `FOWNER` — so mounting the
  claim root as the data root could leave a data root that `www-data` cannot write. Instead the
  official image's supported `DATA_PATH` variable points at a subdirectory the entrypoint creates as
  root, which the same fix-up can chmod. The LinuxServer tree then sits at `/config/www`, outside
  the data root, as unused bytes; the feeds come back from the OPML file. Deleting the old tree is a
  destructive hand operation and is the operator's to make, not the chart's.
- **Chart changes.** Image repository and tag, the env block (`DATA_PATH`, `CRON_MIN`, the `OIDC_*`
  variables), the `securityContext` comment (the capabilities still exist because the entrypoint
  runs as root and chowns the data into the `www-data` group, so `CHOWN`/`SETUID`/`SETGID`/
  `DAC_OVERRIDE` stay), the probes comment and the README. `storage.mountPath` stays `/config`;
  `strategy: Recreate`, the claim and the node pin are unchanged.
- **Environment.** `DATA_PATH=/config/data`, `CRON_MIN=1,31`, `OIDC_ENABLED=1`,
  `OIDC_PROVIDER_METADATA_URL` pointing at
  `https://authentik.kub.home.ashudev.com/application/o/freshrss/.well-known/openid-configuration`,
  `OIDC_CLIENT_ID=freshrss`, `OIDC_REMOTE_USER_CLAIM=preferred_username`,
  `OIDC_SCOPES="openid profile email"`, and `OIDC_X_FORWARDED_HEADERS="X-Forwarded-Host
  X-Forwarded-Port X-Forwarded-Proto"` because Traefik terminates TLS. `CRON_MIN=1,31` enables the
  image's built-in feed refresh, which the LinuxServer image provided for itself. The client secret
  and the crypto key come from a Secret through `extraEnv`, never from values.
- **Secrets.** A new `news/freshrss-oidc` Secret with `client-secret` and `client-crypto-key`. The
  same client secret goes into the authentik Secret `authentik-sso` as `FRESHRSS_CLIENT_SECRET`,
  because the blueprint reads it with `!Env`, and `authentik-sso` is where every other app's client
  secret lives (`docs/reference/sso-applications.md`, the phase-3 plan). Two copies of one value,
  the existing pattern.
- **authentik blueprint** `ashulab-app-freshrss`: an OAuth2 provider named `freshrss`, confidential,
  grant types `authorization_code` and `refresh_token`, the default signing key, scopes
  `openid`/`email`/`profile`, and the exact redirect URI
  `https://freshrss.kub.home.ashudev.com:443/i/oidc/` (upstream says the port is required even for
  443). Application `freshrss`, launch URL the app root, policy binding `authentik Admins` — the
  operator is the only user, as with autobrr, sonarr and Grafana.
- **No outpost, no middleware.** OIDC providers are not attached to the embedded outpost, and the
  route does not gain `authentik-forwardauth`. The route keeps `kube-system-lan-only@kubernetescrd`:
  every non-public route carries it, and OIDC is not a substitute for the LAN boundary.
- **Readiness stays `/i/`.** With OIDC enabled that path answers `302` to authentik, and the probe
  treats 3xx as success. It still exercises Apache and PHP; it no longer reads the user table on
  every probe. The alternative, an unauthenticated health path, upstream does not provide in a way
  the image serves.

## Operator steps (secrets and the first sign-in)

1. Generate one client secret and one crypto key; put them in `news/freshrss-oidc`
   (`client-secret`, `client-crypto-key`) and the client secret in `authentik-sso` as
   `FRESHRSS_CLIENT_SECRET`.
2. Let ArgoCD sync the chart and the blueprint.
3. Open the app and sign in through authentik. In the setup wizard set the default user to
   `vyrtualsynthese` (the exact OIDC identity), choose HTTP authentication, and give the password
   field a random value it will not use.
4. Set an API password in Settings, then update `FRESHRSS_USER` and `FRESHRSS_API_PASSWORD` in
   `~/.config/secrets/homelab.env`.
5. Replay the feed list: `python3 hack/freshrss_sync.py apply`.

## Verification

- `helm template news-freshrss cluster/helm/news/freshrss --namespace news` renders, and the
  rendered Deployment mounts the claim at `/config`, sets `DATA_PATH=/config/data`, and carries the
  OIDC variables and the two Secret keys.
- `hack/check-exposure.sh` passes: the route still carries `lan-only`.
- After sync: the pod is Ready, `/i/` answers `302` to the authentik authorize endpoint, and
  `/api/greader.php` answers on its own credentials with no redirect.
- `grep` over the tree finds no secret value.

## Non-goals

- Changing the feed set or the OPML.
- Notifications, push, or the phone app's configuration.
- Wiping the old LinuxServer tree from the claim.
- Migrating other apps to OIDC.

## Left to the builder

The exact variable ordering and comments in `values.yaml`, the README rewrite, the blueprint's
comments, and the docs edits (`docs/reference/routes.md`, `docs/secrets.md`, `docs/charts/README.md`
and `docs/reference/chart-notes.md`).
