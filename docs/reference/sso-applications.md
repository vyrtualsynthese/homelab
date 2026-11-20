# SSO applications: which application is protected how

> **In one line.** Every application that has a single-sign-on decision: the mechanism it uses —
> native OIDC, proxy auth, the forward-auth gate, or LAN only — the state that mechanism is in, and
> the hostname it answers; then the media-tools detail, the two trap lists and the procedure for
> adding a tool.
>
> **Use it when** an application's sign-in surprises you, you are choosing how to protect a new one,
> or a hostname answers 404 from authentik · **Verified** 2026-11-19 · **Kind:** living page

What the mechanisms themselves prove — OIDC, proxy auth, the gate, `lan-only` — is
[how-it-works/identity.md](../how-it-works/identity.md). Hostnames, TLS and whether a name answers
the internet are [routes.md](routes.md). The procedure that produces one application's row is
[runbooks/authentik-apps.md](../runbooks/authentik-apps.md); this page is the state it leaves behind.

## The matrix

One row per application that has a decision, and the ones whose decision is *no gate at all* carry the
reason in **State**. **Mechanism** is the strongest of the four layers that applies, `none` where even
`lan-only` is absent. **Route** is what the browser asks for; whether that hostname is public, and
which certificate answers it, is [routes.md](routes.md)'s fact, not this table's. An application
absent from this table has no single sign-on at all — no provider, no gate — and its row in
`routes.md` is the fact.

| Application | Mechanism | State | Route |
|---|---|---|---|
| Argo CD | OIDC, through the Dex broker it bundles | configured; `admin.enabled: "false"` | `argocd.kub.local` |
| autobrr | OIDC | configured | `autobrr.kub.local` |
| Calibre-Web Automated | OIDC | configured, verified live 2026-11-15; its own password kept as break-glass | `calibre.kub.local` |
| FreshRSS | OIDC | configured 2026-11-19 — its own OIDC through the official image; no gate, `/api` keeps its own login | `freshrss.kub.home.ashudev.com` |
| Grafana | OIDC | configured; every sign-in lands as a **Viewer** | `grafana.kub.local` |
| Kavita | OIDC | configured 2026-11-15; password login disabled | `kavita.kub.home.ashudev.com` |
| pgAdmin | OIDC | configured; no local login left | `pgadmin.kub.local` |
| Radarr | OIDC | configured 2026-11-18 — Radarr 7's own OIDC; the forward-auth gate and the app's login are gone, `/api` exempt | `radarr.kub.local` |
| Radarr (anime) | OIDC | configured 2026-11-18 — same settings as Radarr, own client id; `/api` exempt | `radarr-anime.kub.local` |
| Vikunja | OIDC | done 2026-11-16 — gate removed, subject linked to the original account; the blueprint in git describes this provider since 2026-11-20 | `vikunja.kub.local` |
| FileBrowser | OIDC | configured 2026-11-19 — FileBrowser Quantum, password form off, so the browser redirects straight to authentik; `filebrowser-editors` admits the editor, whose folder scope is set inside the app | `filebrowser.kub.home.ashudev.com` |
| Bazarr | forward-auth gate | configured; its own login is off; `/api` exempt | `bazarr.kub.local` |
| Bazarr (anime) | forward-auth gate | configured; no login of its own; `/api` exempt | `bazarr-anime.kub.local` |
| Docs site (MkDocs) | forward-auth gate | live since 2026-11-17 — blueprint `app-docs`, Admins only | `docs.kub.local` |
| Lidarr | forward-auth gate | configured; its own login is off; `/api` exempt | `lidarr.kub.local` |
| Maintainerr | forward-auth gate | **to verify** before the next touch — it has no login of its own; `/api` exempt since 2026-11-15 | `maintainerr.kub.local` |
| Prowlarr | forward-auth gate | configured; its own login is off; `/api` exempt; its probes carry a `Host` header | `prowlarr.kub.local` |
| Sonarr | forward-auth gate | configured; its own login is off; `/api` exempt | `sonarr.kub.local` |
| Tautulli | none | by decision: no gate and no authentik object; `lan-only` stays, and Tautulli's own login is the gate because a gate would collapse per-user history | `tautulli.kub.local` |
| The Lounge | LDAP | configured 2026-11-20 — authentik's LDAP outpost `ashulab-ldap` and provider `thelounge-ldap`; the gate is gone and the app's own form is the credential, so the account is the person's own username | `thelounge.kub.home.ashudev.com` |
| Dashy | forward-auth gate | configured 2026-11-18 — blueprint `app-dashy`, Admins only; the only application whose gate is the public sign-in page, since `dashy.ashudev.com` answers the internet | `dashy.ashudev.com` |
| qBittorrent | forward-auth gate | configured 2026-11-18 — blueprint `app-qbittorrent`, Admins only; its own login is bypassed for the pod network so the gate is the only prompt, and port-forward still asks (the break-glass); no `/api` exemption because every API client uses the Service name | `qbittorrent.kub.local` |
| Syncthing | forward-auth gate | configured 2026-11-19 — blueprint `app-syncthing`, Admins only; it has no single sign-on of its own, so the gate is the only user check. Its own GUI login stays until the operator removes it, then Insecure Admin Access is what lets Traefik in | `syncthing.kub.home.ashudev.com` |
| Sonarr (anime) | forward-auth gate | configured 2026-11-18; its own login is off; `/api` exempt | `sonarr-anime.kub.local` |
| Plex | none | no gate and no `lan-only`: Plex clients cannot complete a browser sign-in, so the host answers the internet and Plex login is the gate | `plex.kub.home.ashudev.com` |
| Seerr | none | no gate and no `lan-only`; Plex login is the gate, and local sign-in is off | `seerr.kub.home.ashudev.com` |

Where the application offers no other way in, the password form is closed as well: **Argo CD** has
`admin.enabled: "false"` (that built-in user is its only password login) and **Vikunja** has
`auth.local.enabled: false`. Both leave one route — authentik — so the break-glass for each is a
configmap or env flip plus a rollout, written next to the setting in the chart.

## Traps

Three traps this table exists to prevent:

- **`kube-system-authentik` is not authentication.** It is a headers middleware. The gate is
  `kube-system-authentik-forwardauth`, and a route carrying only the former is protected by `lan-only`
  and nothing else.
- **A missing gate must say why.** A chart that keeps `lan-only` alone carries the reason next to its
  middleware list, as `autobrr` and `tautulli` do.
- **A provider answers only once the embedded outpost lists it.** Blueprints create the provider and
  the application, but the outpost is a separate object: `outpost-embedded.yaml` sets its provider
  list whole, so a provider added there and not listed is created and then ignored — its host answers
  **404**, exactly like a missing blueprint. `docs.kub.local` did that on 2026-11-17 until `docs` was
  added; the same file had kept `vikunja`, whose proxy provider went with the OIDC migration.

## Adding a tool

1. Copy `cluster/helm/authentik/files/app-bazarr.yaml`, change the slug, the display name and
   `external_host`. It is a **proxy** provider in `forward_single` mode, no client secret.
2. Add the file's name to the `metaapplyblueprint` list in `outpost-embedded.yaml`, and add a
   `!Find` line for the provider to that file's `providers` list. Missing either one is a silent
   404 from the outpost at the browser.
3. **Expect one re-apply on a brand-new app.** The first sync creates that app's blueprint instance
   in the same discovery pass that applies `outpost-embedded.yaml`, so the outpost's `!Find` can run
   before the provider exists, the file rolls back, and the new host answers `404` while every other
   provider keeps working. Restart the worker so discovery runs again with the provider now present:

   ```bash
   kubectl -n authentik delete pod -l app.kubernetes.io/component=worker
   ```

   That is what the chart's PostSync hook does, and it is the reliable way. The blueprint API in
   authentik 2026.8 no longer serves `/api/v3/blueprints/instances/` (404); the instance list is
   `GET /api/v3/managed/blueprints/?search=outpost` and applying one is
   `POST /api/v3/managed/blueprints/{pk}/apply/`. That apply returns `200` and changes nothing for a
   mounted blueprint, because only the **worker** pod mounts the blueprints ConfigMap — the server pod
   that answers the API cannot read the file. `sonarr-anime` hit this on 2026-11-18 and `syncthing` on
   2026-11-19, where the outpost line was applied 60 ms before the provider was created.
4. In the tool's chart values, put `authentik-forwardauth` after `lan-only` in the middleware list.
5. Bump both chart versions, open one PR, sync `authentik` and the tool.

Adding the middleware without step 2 is the mistake made once already: the route asks authentik
about a host authentik has never heard of, and authentik answers its own 404 page. The status code
is the fastest diagnostic — `404` means no provider for that host, `403` means the provider exists
but its policy refuses, `302` to authentik means it works.

## Adding a tool that speaks OIDC

Radarr and Radarr Anime are the worked example (`app-radarr.yaml`) — the same shape as `autobrr`,
minus the middleware. Five things differ from the proxy recipe above:

1. **An OAuth2 provider, named apart.** `authentik_providers_oauth2.oauth2provider` with a name like
   `radarr-oidc`, never `radarr`: a proxy provider *is* an OAuth2 provider underneath, so reusing the
   name updates that object in place and keeps the forward-auth redirect URIs.
2. **Grant types, explicitly.** A blueprint-created provider starts with none.
3. **A strict redirect URI** naming the app's callback. Check what the app sends rather than guessing;
   for an ASP.NET app with no `CallbackPath` override it is `https://<host>/signin-oidc`.
4. **No gate in the chart.** Remove `authentik-forwardauth` from the route and keep `lan-only`; the
   app prompts by itself, and a gate in front is a second prompt that still carries no identity.
5. **`authenticationRequired: enabled` in the app, not `disabledForLocalAddresses`.** The local bypass
   lets a request Traefik forwards in arrive as the admin user without passing authentik at all.

**The user identifier is the hashed `sub`, not the email.** authentik sends no verified email, so an
app that accepts "a verified email or the subject" (Radarr's `oidcUserIdentifier`) matches nothing and
answers `Unable to sign in with your identity provider`. The app logs the identifier it saw at error
level — `User '<64 hex characters>' is not authorized` — and that string is what goes in the setting.
It is per account, not per provider, so the same value serves both Radarr instances.

### FreshRSS: native OIDC needs the official image

FreshRSS implements OIDC with the Apache module `mod_auth_openidc`. Only its **official**
`freshrss/freshrss` image carries it (Debian, x86_64); the LinuxServer image the chart used to run is
nginx and cannot. So the chart swapped images, which is the reason this app, unlike Radarr, has no
`-oidc` suffix on its provider name: there was never a proxy provider under `freshrss` to collide
with.

Two details differ from the Radarr recipe. The callback is `https://freshrss.kub.home.ashudev.com:443/i/oidc/`,
with the port spelled out because `mod_auth_openidc` builds it from `X-Forwarded-Port` and authentik
compares the whole string. And the app protects **`/i/` only**, so `/p/api/` keeps its own login; the
route needs no `/api` rule for that, unlike an app whose whole surface sits behind its sign-in.

Its data root is `DATA_PATH=/config/data`, a subdirectory of the claim: the claim root was chowned to
uid 1100 by the previous image and the container can change a file's group but not its mode, so a
data root there could not be written by the Apache worker.

One setting decides whether the app really signs in through authentik: **Authentication method must
be HTTP authentication** (`auth_type: http_auth`). The module authenticates the request, but FreshRSS
still asks for its own form login unless it is told to trust the identity Apache hands it; the
install wizard defaults to `form` and offers the choice. The same mode looks the account up by name,
so the FreshRSS user must be the authentik username — there is no provisioning on first sign-in.

## Adding a tool that speaks LDAP

The Lounge is the worked example: no OIDC, but a built-in LDAP plugin, so an LDAP bind is its native
path. Five things differ from the OIDC recipe:

1. **An LDAP provider and a separate outpost.** `authentik_providers_ldap.ldapprovider` plus an
   `authentik_outposts.outpost` of `type: ldap`. The embedded outpost serves proxy providers only and
   cannot serve an LDAP provider, so the outpost is its own workload: the chart renders it as
   `templates/ldap-outpost.yaml`, image `ghcr.io/goauthentik/ldap:<tag>`, port 3389, Service
   `authentik-ldap` in namespace `authentik`.
2. **The authentication flow, not the implicit-consent flow.** A bind validates a password, so
   `authorization_flow` is `default-authentication-flow`. Pointing it at the proxy providers'
   implicit-consent flow produces a bind that authenticates nothing.
3. **The outpost's token is a Secret, and authentik generates it.** Creating the outpost object creates
   its service account and token. Read it once with
   `GET /api/v3/core/tokens/<token_identifier>/view_key/` and store it in Secret `authentik-ldap`
   (the runbook has the one-liner). Until it exists the pod is in `CreateContainerConfigError`, which
   is the expected state, not a failed sync.
4. **Direct bind in the app, no service account.** `config.js` sets `baseDN` and `primaryKey: "cn"` and
   omits `searchDN`. Adding `searchDN` switches The Lounge to the advanced path, where it first binds
   a system account from `rootDN`/`rootPassword` and searches for the person — an extra credential, an
   extra permission, and the same access decision in two places.
5. **Access is the application binding.** authentik refuses a bind from someone who cannot open the
   application, so the policy binding on the LDAP application is the only gate. No group filter in
   `config.js`.

The DN authentik exposes is `cn=<username>,ou=users,dc=ldap,dc=goauthentik,dc=io`, so
`primaryKey: "cn"` and `baseDN: "ou=users,dc=ldap,dc=goauthentik,dc=io"`.

**`mfa_support` is a trap in both directions.** Off, a person with a device enrolled cannot bind at
all. On, a password containing `;` is split at the semicolon and rejected. Leave it off while nobody
has a device enrolled (the flow's MFA stage skips then), and flip it in the same change as the first
enrolment, telling that person to type `password;123456`.

**The app's local passwords stop working.** The LDAP plugin replaces the local login; accounts,
networks and history are keyed by username and survive, but every person needs an authentik password
and the same username.

## Migrating Vikunja to OIDC

It is the only application here that supports OIDC and does not use it, and the board is where the
loss is visible: forward-auth hands Vikunja one shared identity, so every card the operator writes and
every card an agent writes need labels or bots to be told apart.

The migration is five steps, in this order, and the order is the safety:

1. **Done 2026-11-16.** OAuth2 provider `vikunja-oidc` (pk 62, client id
   `eqfj9awPPBaZSDan6HikbXBYSJgh1nok8ahhVKG4`) and application `vikunja-oidc`, whose slug is what
   makes the discovery document exist:
   `https://authentik.kub.home.ashudev.com/application/o/vikunja-oidc/.well-known/openid-configuration`.
   The provider is cloned from `autobrr-oidc`: `sub_mode: hashed_user_id`, claims in the ID token,
   strict redirect to `https://vikunja.kub.local/auth/openid/authentik`.

   **A provider created over the API has `grant_types: []`**, and an empty list means no grant is
   ever allowed: every authorize request comes back `invalid_request — The request is otherwise
   malformed`, in authentik and in the application, with nothing in the event log. Set it explicitly,
   or the login can never work:

   ```bash
   PATCH /api/v3/providers/oauth2/<pk>/  {"grant_types": ["authorization_code", "refresh_token"]}
   ```

   That cost a debugging round on 2026-11-16; a provider cloned in the UI does not have it.
2. **Done 2026-11-16, except the Secret.** `cluster/helm/vikunja/values.yaml` carries the OIDC env
   block; the client secret is read from Secret `vikunja-oidc`, key `CLIENTSECRET`, which the operator
   creates:

   ```bash
   read -rsp "vikunja-oidc client secret: " S; echo
   kubectl -n vikunja create secret generic vikunja-oidc \
     --from-literal=CLIENTSECRET="$S" --dry-run=client -o yaml | kubectl apply -f -
   unset S
   ```
3. Sign in through OIDC and prove a ticket is stamped with the real account.
4. **Done 2026-11-16.** `authentik-forwardauth` is gone from `uiMiddlewares`; `lan-only` stays, and
   `/api` was outside the gate all along, so bot tokens and the MCP keep working.

   The account is linked by `issuer` + `subject` in the `users` table, which is what the "link from
   account settings" button would have written. Two consequences worth remembering:

   - **Password login is refused** once `issuer != 'local'` (`ErrAccountIsNotLocal`). The break-glass
     is `UPDATE users SET issuer='local', subject=NULL WHERE username='ashuv';` plus a password reset,
     or any API token, which are local users and unaffected.
   - **A failed OIDC login creates a second account** with the same subject, and then two rows match
     one subject. Retire the newer one by clearing its subject and renaming it; never leave two.
5. **Done 2026-11-20.** `cluster/helm/authentik/files/app-vikunja.yaml` now declares what the cluster
   runs — the OAuth2 provider `vikunja-oidc` (client id, strict redirect
   `https://vikunja.kub.local/auth/openid/authentik`, the `openid` and `profile` scopes plus the local
   `vikunja: email verified` one), the application `vikunja-oidc` whose slug names the issuer, and the
   Admins-only binding. The retired forward-auth proxy provider is gone, so re-applying the blueprint
   can no longer re-point the board at a gate.

   The file reads the client secret from `VIKUNJA_CLIENT_SECRET` in `authentik-sso`, which holds the
   same value as Secret `vikunja-oidc`/`CLIENTSECRET` in the `vikunja` namespace: Secrets do not cross
   namespaces, so the key is copied, not shared ([`docs/secrets.md`](../secrets.md)). Until that key
   exists the file fails to apply — atomically, so the live provider is left untouched and the board
   keeps working.

## Media tools behind the gate

Nine media tools plus Calibre-Web were added on 2026-11-15. The design and the plan that produced
this are `docs/superpowers/specs/2026-11-14-media-tools-authentik-design.md` and
`docs/superpowers/plans/2026-11-14-media-tools-authentik.md`.

**The vocabulary matters here.** A *forward-auth gate* is a Traefik middleware that asks authentik
about every request before letting it through; the tool behind it never learns who you are unless
it is told via a header. *OIDC* is the other way: the tool itself talks to authentik and holds the
session.

### What is protected how

| Tool | Route | Protection | The tool's own login |
|---|---|---|---|
| calibre | `https://calibre.kub.local` | **OIDC, no gate** — verified live 2026-11-15 | renamed to the authentik username; the password stays as break-glass |
| sonarr | `https://sonarr.kub.local` | forward-auth; **`/api` exempt** since 2026-11-15 | off |
| radarr | `https://radarr.kub.local` | **OIDC, no gate** since 2026-11-18; **`/api` exempt** | OIDC; password login off |
| lidarr | `https://lidarr.kub.local` | forward-auth; **`/api` exempt** since 2026-11-15 | off |
| prowlarr | `https://prowlarr.kub.local` | forward-auth; **`/api` exempt** since 2026-11-15 | off, probes fixed |
| bazarr | `https://bazarr.kub.local` | forward-auth; `/api` exempt | none (always was) |
| bazarr-anime | `https://bazarr-anime.kub.local` | forward-auth; `/api` exempt | none (always was) |
| tautulli | `https://tautulli.kub.local` | **no gate** since 2026-11-18; `lan-only` alone | its own login; the Plex-only toggle is not in the official docs |
| maintainerr | `https://maintainerr.kub.local` | forward-auth; **`/api` exempt** (`lan-only` only) since 2026-11-15 | none exists |
| autobrr | `https://autobrr.kub.local` | **OIDC, no gate** | replaced by OIDC |
| qbittorrent | `https://qbittorrent.kub.local` | forward-auth since 2026-11-18, no `/api` exemption | bypassed for `10.42.0.0/16` the same day; still asked on localhost, so port-forward is the break-glass |
| sonarr-anime | `https://sonarr-anime.kub.local` | forward-auth; **`/api` exempt** since 2026-11-18 | off |
| radarr-anime | `https://radarr-anime.kub.local` | **OIDC, no gate** since 2026-11-18; **`/api` exempt** | OIDC; password login off |

Every route also carries `lan-only`. That middleware allows `192.168.1.0/24` (LAN), `10.8.0.0/24`
(WireGuard) and `10.42.0.0/16` (pods — see the first trap below).

### Every media-stack route is HTTPS since 2026-11-15

The whole namespace moved to TLS, not just Calibre-Web. Each chart now renders two IngressRoutes: the real
one on `websecure`, and a `-redirect` twin on `web` that answers with a permanent redirect. Two objects
are shared rather than copied per chart, both in `cluster/helm/platform/traefik-gate`:

| Object | What it does |
|---|---|
| `kube-system/redirect-to-https` | `redirectScheme: https`, on every `-redirect` router |
| `kube-system/internal-tls` | the TLSOption every TLS router selects (TLS 1.2 minimum, ECDHE suites) |

The certificate is the cluster default, `kube-system/local-cert`, from the private CA in
[`internal-ca.md`](../runbooks/internal-ca.md). Consequences worth knowing:

- **Every client needs the root installed.** A browser without it gets a warning, not an outage. The
  per-client commands are in `internal-ca.md`.
- **The `-redirect` routers carry `lan-only` and nothing else.** Putting the gate there would send a
  plain-HTTP visitor through authentik before the redirect, and authentik would send them back to `http`.
- **The authentik proxy providers say `https://<tool>.kub.local` in `external_host`**, and autobrr's
  registered callback is `https://autobrr.kub.local/api/auth/oidc/callback`. The outpost takes its session
  cookie's `Secure` flag from that scheme, and autobrr builds its callback from `X-Forwarded-Proto`; a
  mismatch shows up as a redirect loop or a refused callback.
- **Anything calling `http://<tool>.kub.local` from a script now gets a `301`.** That includes API clients
  that do not follow redirects; switch them to `https://` and give them the root.
- **The Servarr apps need `ASPNETCORE_FORWARDEDHEADERS_ENABLED=true`** to trust `X-Forwarded-Proto`. ASP.NET
  builds absolute URLs from the scheme it believes in, so without it the login redirect and any absolute
  link say `http://`, and the browser takes needless hops through the 301. It is set in sonarr, radarr,
  lidarr, prowlarr and both anime twins. The other tools emit relative redirects and do not care.

### Turning off the tool's own login (the \*arr family)

These builds **refuse authentication fully off** and revert it. The supported setting is "disabled
for local addresses" plus a trusted-network list, and it is inert until the app restarts:

```bash
# for sonarr, radarr, lidarr: api/v3; prowlarr: api/v1
curl -s -H "X-Api-Key: $KEY" "http://localhost:8989/api/v3/config/host" -o /tmp/host.json
jq '.authenticationRequired="disabledForLocalAddresses"
  | .trustedNetworks="10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16"
  | .allowedHosts="0.0.0.0"' /tmp/host.json > /tmp/new.json
curl -s -X PUT -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  --data @/tmp/new.json "http://localhost:8989/api/v3/config/host"
curl -s -X POST -H "X-Api-Key: $KEY" -H "Content-Length: 0" \
  "http://localhost:8989/api/v3/system/restart"
```

- `0.0.0.0/0` is **rejected** by `trustedNetworks`; the three private ranges are the widest value
  that validates.
- `allowedHosts` and a non-empty password are required once `authenticationRequired` is not
  `enabled`. Send the object read from `GET` back unchanged apart from those fields, so the
  existing password hash survives.
- **Prowlarr is different**: it rejects `0.0.0.0` in `allowedHosts` and wants host names, which
  makes ASP.NET reject the kubelet's probe (pod IP is not a name) and the pod crash-loops. Fix by
  giving the probes `Host: prowlarr.kub.local` — see `prowlarr/templates/deployment.yaml`.
- Tautulli has **no API command** for its web auth; it is a UI toggle under Settings → Web
  Interface. That is why it is the one tool still asking for a login.
- The service route is behind the gate, so this needs `kubectl -n media-stack port-forward
  svc/<tool> <port>:<port>` from a terminal; the agent's shell cannot run `kubectl`.

### Keeping an API reachable

API clients send an API key, not a browser session, so they cannot complete a sign-in redirect.
The pattern, in place on `bazarr`, `bazarr-anime`, `sonarr`, `radarr`, `lidarr`, `prowlarr`,
`tautulli` and `maintainerr`, is a second rule on the same IngressRoute:

```yaml
ingressRoute:
  apiBypassMiddlewares:
    - name: lan-only
      namespace: kube-system
```

It renders `Host(...) && PathPrefix(/api)` with `priority: 100`. `lan-only` stays, so the API is
reachable from the LAN, WireGuard and the pod network that middleware already allows — never the
internet — and the tool's own key is the credential. **Only do this for a tool whose API is
authenticated.** The Servarr apps and Tautulli qualify: `X-Api-Key` on every `/api` request for
sonarr, radarr, lidarr and prowlarr, `apikey` in the query string for Tautulli. The UI route keeps
the gate in every case, so the exemption is one path, not the whole host.

`apiBypassMiddlewares: []` turns the exemption off: the template's `with` then renders no second
rule, and `/api` falls back under the gated route above.

`maintainerr` carries the exemption too, since 2026-11-15, and it is the exception that proves the
rule: upstream 3.32.0 checks **no** credential on inbound requests — the key on its Settings page is
only sent by the app to itself — so the exemption removes the gate rather than replacing it with a
key check. It was enabled deliberately, to let scripts query the API, with `lan-only` as the only
control left. The chart README section "The API exemption, and what it costs" records the
consequence; a `basicauth` middleware on the `/api` rule would be the way to close it while keeping
scripts working.

### Calibre-Web: native OIDC instead of the gate

Calibre-Web Automated **v4.0.8 speaks generic OIDC** — `cps/oauth_bb.py` calls authentik out by name — so
it uses autobrr's shape, not the gate's: an `authentik_providers_oauth2.oauth2provider` named `calibre` in
`cluster/helm/authentik/files/app-calibre.yaml`, and a route with no auth middleware on it. An earlier pass
put `strip-authentik-header` and `authentik-forwardauth` on this route on the belief that it had no OIDC;
that belief was wrong for this build.

The gate is the wrong shape here for a second reason: **`/opds` and the Kobo sync endpoints authenticate
with basic auth** (the Calibre-Web username and password) and cannot follow a redirect to authentik. A
gate would need an exemption for them; native OIDC needs none.

**It is the one app here that needs HTTPS.** The OIDC button only renders when *Login type* is `OAuth`, and
that setting forces `SESSION_COOKIE_SECURE=True` (`cps/__init__.py:134`) — a `Secure` cookie that a
plain-HTTP browser discards, so the callback would return without its session. Its own dropdown says
"Use OAuth (requires HTTPS)". So the chart serves `https://calibre.kub.local` on `websecure`, with the
`web` router only redirecting, and the certificate is the cluster default from the `TLSStore`
(`kube-system/local-cert`, private, SAN `*.kub.local`). Clients without that root show a warning; that is
expected and accepted.

**The default certificate had expired on 2026-05-06** and nobody noticed for ten months, because nothing
could reissue it. `hack/internal-cert.sh` now signs a new leaf from a private CA kept in
`~/.config/secrets/kub-local`, and reuses that CA on later runs so clients keep working. Replace the Secret
afterwards — Traefik serves it immediately:

```bash
kubectl -n kube-system create secret tls local-cert \
  --cert=$HOME/.config/secrets/kub-local/kub.local.pem \
  --key=$HOME/.config/secrets/kub-local/kub.local-key.pem \
  --dry-run=client -o yaml | kubectl apply -f -
```

The root to install once on each client is `~/.config/secrets/kub-local/local-ca.pem`.
**Back up `local-ca-key.pem` from that directory to the password manager.** Its loss is what forced a new
root this time: the previous CA's certificate was still in `C:\Users\ashuv\Documents\Scans\rootCA.pem` and
in Bitwarden, but its private key was nowhere, so the trusted root could not issue anything again.
[`internal-ca.md`](../runbooks/internal-ca.md) holds the backup rules, the restore drill, the renewal command and the
per-client trust install.

Replacing the Secret is a **delete then create**, not an `apply`: the object in place is `Opaque` and a
Secret's `type` is immutable.

```bash
kubectl -n kube-system delete secret local-cert --ignore-not-found
kubectl -n kube-system create secret tls local-cert --save-config \
  --cert=$HOME/.config/secrets/kub-local/kub.local.pem \
  --key=$HOME/.config/secrets/kub-local/kub.local-key.pem
```

Setup, in this order — step 1 must happen **before** the blueprint merges, or the file rolls back and
creates nothing:

1. Client secret into the authentik Secret, **before** the PR merges. No restart command is needed: the
   chart's `PostSync` hook deletes the worker pod on every sync, and the worker reads the Secret through
   `envFrom` when it starts.

   ```bash
   S=$(openssl rand -hex 32)
   kubectl -n authentik patch secret authentik-sso --type=merge \
     -p "$(jq -nc --arg s "$S" '{stringData:{CALIBRE_CLIENT_SECRET:$s}}')"
   ```

   Keep `$S` for step 3: it is typed into Calibre-Web by hand, because Secrets do not cross namespaces.
   autobrr keeps its copy in its own Secret, `autobrr-oidc`; Calibre-Web has nowhere but `app.db`.

2. **Rename the local account** so the sign-in lands in it. Calibre-Web matches the provider's
   `preferred_username` against its own user names and, on no match, creates a second account — which
   loses the administrator role and every shelf. Do it in Admin → Users: the account is `admin` until
   then, and `vyrtualsynthese` afterwards. Its password is not changed by the rename, so the break-glass
   route survives.

3. In Calibre-Web, Settings → Basic Configuration → OAuth, generic provider: activate it, then fill in
   the metadata URL `https://authentik.kub.home.ashudev.com/application/o/calibre/.well-known/openid-configuration`,
   OAuth Redirect Host `https://calibre.kub.local`, client id `calibre`, the secret from step 1, scopes
   `openid profile email`, username field `preferred_username`, admin group `authentik Admins`. Then in
   Login Settings set *Login type* to **Use OAuth (requires HTTPS)**, and tick **Enable OAuth Group-Based
   Admin Role Management**. Saving restarts Calibre-Web on its own; if the "OpenID Connect" button is
   still missing afterwards, delete the pod.

   The password fields stay on the login page: the login type gates which *extra* buttons appear, it does
   not remove the local login unless "disable standard login" is ticked. Leave that tickbox alone — it is
   the break-glass route.

The `groups` claim comes from authentik's own `profile` scope mapping, which already lists the user's
group names. So every account in `authentik Admins` becomes a Calibre-Web administrator on login, without
anyone touching Calibre-Web's user list — and an account removed from the group loses the role again.
That only reaches people the application's policy already lets in, which is `authentik Admins` alone.

Calibre-Web cannot log the user out of authentik: its OIDC logout clears its own session and nothing
more, and it has no end-session call. Same limitation as autobrr.

**Verified live 2026-11-15.** `/login` serves the SSO button next to the password form; `/login/generic`
answers `302` to `https://authentik.kub.home.ashudev.com/application/o/authorize/` carrying
`client_id=calibre`, `redirect_uri=https://calibre.kub.local/login/generic/authorized` and
`scope=email openid profile`; authentik's provider points back at that same redirect URI; and signing in
from the button lands in Calibre-Web as `vyrtualsynthese`.

**Break-glass over HTTP only works on `localhost`.** With `OAuth` as the login type the session cookie is
`Secure` everywhere, and browsers accept a `Secure` cookie only on an https origin or on
`http://localhost`. So:

```bash
kubectl -n media-stack port-forward svc/calibre-web-automated 8083:8083
# http://localhost:8083 → password → in. Another host name over http would not work.
```

If the login type has to be reverted without the UI: it is the `config_login_type` row in the `settings`
table of `/config/app.db` on the config claim; `0` is standard, `2` is OAuth.

### Recovery

- **Read access while authentik is down:** `kubectl -n media-stack port-forward svc/<tool> <port>`
  reaches the tool without Traefik, and therefore without the gate.
- **Remove the gate from one route:** revert its `middlewares` entry to `lan-only` and merge;
  ArgoCD applies it. This is the tested path, not an emergency `kubectl edit`.
- **Calibre-Web** keeps a local password on purpose; it is the way back in when the OIDC client secret
  is wrong or authentik is unreachable. Sign in at `http://localhost:8083` after a port-forward, with the
  account named after the authentik username and its original password.

### Traps found the hard way

1. **`lan-only` blocked the cluster's own pods.** Its ranges did not include `10.42.0.0/16`, so any
   *server-side* OIDC call to a public hostname got `403`. That broke autobrr's discovery and
   would have broken Grafana's token call, Argo CD's Dex issuer and pgAdmin's metadata fetch. The
   pod range is now allowed; it adds no reachability pods did not already have over Service DNS.
2. **A legacy `Ingress` can shadow an `IngressRoute`.** autobrr had a 2024 `Ingress` with
   `redirect-https` on the same host; every request got a `301` and the gate never ran. Check
   `kubectl get ingress -n media-stack` before believing a route is broken.
3. **The embedded outpost reloads its application map on a delay** of about two minutes. A `404`
   right after a blueprint apply is not a failure. Those requests are logged by Django
   (`authentik.asgi`), while the outpost binary logs them with `ak-axum`; the API is the reliable
   source of truth, not the log tail. A `404` that survives several reloads is not the delay: the
   outpost file rolled back, and "Adding a tool" step 3 is the cure.
4. **A proxy provider is an OAuth2 provider underneath.** Naming a new OIDC provider the same as an
   existing proxy provider updates that object and keeps its forward-auth redirect URIs. Give the
   OIDC provider its own name (`autobrr-oidc`).
5. **`!Env` secrets are read at apply time.** Changing the Secret does not re-apply a blueprint;
   restart the worker (so `envFrom` is re-read) and force the apply with
   `POST /api/v3/managed/blueprints/{pk}/apply/`.
6. **`kubectl create secret ... --from-literal=k="$VAR"` can silently store the literal `"$VAR"`.**
   Copy the value out of the source Secret instead:
   `--from-literal=k="$(kubectl -n authentik get secret authentik-sso -o jsonpath='{.data.KEY}' | base64 -d)"`.
7. **autobrr cannot log you out of authentik.** Its logout destroys its own session and returns
   `204`; it has no OIDC logout URL (v1.90.0 and `main`), though the provider advertises
   `…/application/o/autobrr/end-session/`. Logging out and back in can also fail with
   "no state found in session" until a hard reload, because the browser may replay a cached
   `/api/auth/oidc/config` response. Neither is fixable from git; both are worth an upstream issue.
8. **`kube-system/local-cert` cannot be replaced with `kubectl apply`.** The Secret in place was made
   with `--from-file`, so its type is `Opaque`, and a Secret's type is immutable: adding
   `type: kubernetes.io/tls` is refused with `field is immutable`. Delete and create instead — see
   [`internal-ca.md`](../runbooks/internal-ca.md).
9. **Firefox does not read the Windows certificate store.** `certutil.exe -addstore -user Root` covers
   Chrome and Edge and does nothing for Firefox, which keeps its own NSS store; the CA has to be
   imported there too, with *Trust this CA to identify websites* ticked. Setting
   `security.enterprise_roots.enabled` to `true` makes Firefox follow Windows and removes the
   duplicate step.
10. **Calibre-Web's OIDC needs HTTPS, and its login type gates the button.** The button only renders
    when *Login type* is `OAuth`, and that setting forces `SESSION_COOKIE_SECURE`, which a plain-HTTP
    browser discards. Hence the TLS route. Leave *disable standard login* off so the password stays
    as break-glass on `http://localhost:8083`.

## Still open

- **qbittorrent** is gated since 2026-11-18 and its own login was bypassed the same day:
  `bypass_auth_subnet_whitelist_enabled=true` with `bypass_auth_subnet_whitelist=10.42.0.0/16` in
  `qBittorrent.conf`. A browser through the gate lands in the WebUI with no second prompt, so the
  gate is the only control for anything reaching it over the pod network. `bypass_local_auth` stays
  false, so `kubectl port-forward svc/qbittorrent 8080:8080` still asks for the password and is the
  way in while authentik is down. No `/api` exemption, because cross-seed, `qbittorrent-exporter` and
  autobrr all reach it by Service name.
- **tautulli** has no gate since 2026-11-18 and keeps its own login. Plex-only sign-in exists as a UI
  toggle in v2.19.0, but `docs.tautulli.com` documents neither it nor a procedure for it, so the
  chart leaves the login alone.
- **No media-stack API is behind a sign-in any more** (2026-11-15; sonarr-anime and radarr-anime joined
  on 2026-11-18; radarr's two were the last). Every chart built on the shared `ingressroute.yaml` has
  the `apiBypassMiddlewares` block, and the four gated tools that check their own key — sonarr,
  sonarr-anime, lidarr, prowlarr — set it to `lan-only`, joining `bazarr`, `bazarr-anime` and
  `maintainerr`; `radarr` and `radarr-anime` carry the same exemption but behind their own OIDC rather
  than a gate. `tautulli` dropped its exemption with its gate on 2026-11-18, and `qbittorrent` joined
  the gated set the same day without one — its clients use the Service name, so its API stays behind
  the gate while still working. The charts without a sign-in (autobrr, calibre, kavita, plex, seerr)
  never had one, so they need nothing.
  Any new chart that copies the template must decide this explicitly.
- **maintainerr's `/api` is open to the LAN** (since 2026-11-15, by decision). The UI keeps the
  authentik gate; the API does not, and upstream checks no key on inbound requests. Close it with
  `apiBypassMiddlewares: []`, or put a `basicauth` middleware on the `/api` rule.
- autobrr's cached-config failure (trap 7) would go away with a `Cache-Control: no-store` header on
  `/api/auth/oidc/config`.
