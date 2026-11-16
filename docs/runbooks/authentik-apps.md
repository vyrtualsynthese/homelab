# Wire an application to authentik

State, as of 2026-11-16:

| Application | authentik objects in git | Application configured | How |
|---|---|---|---|
| Grafana | yes | **yes** | Secret + chart values |
| pgAdmin | yes | **yes** | `config_local.py` in a Secret |
| Argo CD | yes | **yes** | `cluster/helm/argocd/values.yaml` + `helm upgrade` |
| Kavita | yes | **yes** | `POST /api/settings`, stored in its database |
| FileBrowser | yes | no | proxy auth, forward-auth middleware |

Four applications have providers and application objects in the chart (`files/app-*.yaml`); Grafana,
pgAdmin, Kavita and Argo CD are configured on the application side. FileBrowser is the one still
waiting.

Client IDs are fixed and public: `argocd`, `grafana`, `pgadmin`, `kavita`.

## 1. Create the four client secrets

Run once, as the operator. They go into `authentik-sso`, which the worker already mounts, and the
values are needed again for the applications, so copy the printed four into the password manager.

```bash
G=$(openssl rand -hex 24); P=$(openssl rand -hex 24)
K=$(openssl rand -hex 24); A=$(openssl rand -hex 24)
kubectl -n authentik patch secret authentik-sso --type merge -p "$(jq -n \
  --arg g "$G" --arg p "$P" --arg k "$K" --arg a "$A" \
  '{stringData:{"GRAFANA_CLIENT_SECRET":$g,"PGADMIN_CLIENT_SECRET":$p,
                "KAVITA_CLIENT_SECRET":$k,"ARGOCD_CLIENT_SECRET":$a}}')"
printf 'grafana=%s\npgadmin=%s\nkavita=%s\nargocd=%s\n' "$G" "$P" "$K" "$A"
```

Then sync `authentik`. The four `app-*.yaml` blueprints create the providers and applications;
the worker log names any file that fails, and a failure is atomic per file.

## 2. Grafana

The chart reads the secret from a Secret in `monitoring`; nothing secret is in git.

```bash
kubectl -n monitoring create secret generic grafana-oidc \
  --from-literal=GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET='<grafana value>'
```

Sync `grafana`. The login page gains **Sign in with authentik**; anyone admitted there becomes a
Grafana **Viewer** (`role_attribute_path: "'Viewer'"`). Raise a specific user to Editor or Admin
in Grafana's own UI when needed.

## 3. pgAdmin

pgAdmin reads `config_local.py`; the chart mounts it from a Secret, so the client secret stays
out of git.

pgAdmin has exactly one login source, so it redirects straight to authentik: an existing
authentik session means no prompt. Its sign-out ends the authentik session too.

```bash
P='<pgadmin value>'
cat > /tmp/pgadmin-oidc.py <<EOF
AUTHENTICATION_SOURCES = ["oauth2"]
OAUTH2_CONFIG = [{
    "OAUTH2_NAME": "authentik",
    "OAUTH2_DISPLAY_NAME": "authentik",
    "OAUTH2_CLIENT_ID": "pgadmin",
    "OAUTH2_CLIENT_SECRET": "$P",
    "OAUTH2_SERVER_METADATA_URL": "https://authentik.kub.home.ashudev.com/application/o/pgadmin/.well-known/openid-configuration",
    "OAUTH2_SCOPE": "openid email profile",
    # Must be inside the provider entry: pgAdmin reads this key from
    # OAUTH2_CONFIG, not from the module. It substitutes {id_token} before
    # sending the browser to authentik's end-session endpoint.
    "OAUTH2_LOGOUT_URL": "https://authentik.kub.home.ashudev.com/application/o/pgadmin/end-session/?id_token_hint={id_token}",
}]
# Saved server passwords are encrypted with pgAdmin's SECRET_KEY instead of a
# master password nobody set under SSO.
MASTER_PASSWORD_REQUIRED = False
# Send the browser straight to authentik instead of showing pgAdmin's login
# page with a single button. Suppressed right after a logout so the sign-out
# sticks.
OAUTH2_AUTO_LOGIN = True
EOF
kubectl -n databases create secret generic pgadmin-oidc --from-file=config_local.py=/tmp/pgadmin-oidc.py
rm /tmp/pgadmin-oidc.py
```

Sync `pgadmin`. **Break-glass**: with `internal` removed there is no local login; to recover, recreate
the Secret with `AUTHENTICATION_SOURCES = ["oauth2", "internal"]` and restart the pod.

The OIDC user needs promotion to pgAdmin's Administrator role once (User Management), and the two
imported servers must be marked **Shared?** so other users see them; both are pgAdmin-side actions.

## 4. Kavita

**Configured on 2026-11-15.** Kavita keeps its OIDC settings in its own SQLite database, so there is
no chart value and no manifest to change: the settings are written through its API with the admin key
from `KAVITA_API_KEY` in `~/.config/secrets/homelab.env`. The client secret is the
`KAVITA_CLIENT_SECRET` already in `authentik-sso`; the provider in `files/app-kavita.yaml` is
confidential, `client_id: kavita`, with redirects `/signin-oidc` and `/signout-callback-oidc`.

The settings object is read and written whole: a partial `POST` resets every field it omits.

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -sS -H "x-api-key: $KAVITA_API_KEY" \
     https://kavita.kub.home.ashudev.com/api/settings \
  | jq --arg s "$KAVITA_CLIENT_SECRET" '.oidcConfig += {
      enabled: true,
      authority: "https://authentik.kub.home.ashudev.com/application/o/kavita/",
      clientId: "kavita",
      secret: $s,
      provisionAccounts: true,
      requireVerifiedEmail: false,
      syncUserSettings: false,
      autoLogin: true,
      disablePasswordAuthentication: true
    }' \
  | curl -sS -X POST -H "x-api-key: $KAVITA_API_KEY" -H 'Content-Type: application/json' \
         --data @- https://kavita.kub.home.ashudev.com/api/settings
```

`enabled`, `authority`, `clientId` and `secret` are read when the process starts, so restart after
changing them: `kubectl -n media-stack rollout restart deploy/kavita`. `autoLogin` and
`disablePasswordAuthentication` take effect immediately.

Why each flag is what it is:

- `provisionAccounts: true` — a user in the `homelab-users` group gets a Kavita account on first
  login. `defaultRoles` stays empty on purpose: the account is created but cannot log in until a
  role is granted, which is the least-privilege rule for new users.
- `requireVerifiedEmail: false` — authentik does not return `email_verified` by default, and Kavita
  refuses to link the account when it is missing.
- `syncUserSettings: false` — roles stay managed in Kavita, not in authentik.
- `autoLogin: true`, `disablePasswordAuthentication: true` — the login page goes straight to
  authentik and the password form is gone.

**Break-glass.** Disabling password authentication applies to admins too — the check in
`AccountController` has no role exemption, whatever the setting's tooltip says — so the API key is
the only local credential left. Kavita always honours it:

- browser: `https://kavita.kub.home.ashudev.com/login?skipAutoLogin=true&apiKey=<KAVITA_API_KEY>`
  (`skipAutoLogin` is required, or `autoLogin` redirects to authentik first);
- scripts: the `x-api-key` header.

To bring the password form back, `POST` the settings again with
`disablePasswordAuthentication: false` and `autoLogin: false`, using that key.

Verified on 2026-11-15: `GET /oidc/login` answers `302` to
`.../application/o/authorize/?client_id=kavita&redirect_uri=https%3A%2F%2Fkavita.kub.home.ashudev.com%2Fsignin-oidc&…`,
and `POST /api/account/login` with a password answers
`401 Password authentication has been disabled, login via OpenID Connect`.

## 5. Argo CD

Argo CD runs its bundled Dex, so the redirect address is `/api/dex/callback`. It is a hand-run Helm
release, not an Application in this repo. Its connector, RBAC and ingress live in
`cluster/helm/argocd/values.yaml`, so a chart upgrade keeps them.

**The scheme is part of the address.** Argo CD compares the SSO return URL against its own
`configs.cm.url` and answers `Invalid redirect URL: the protocol and host (including port) must
match …` on any difference, before authentik is contacted. The browser reaches this host over TLS,
so `url` is `https://argocd.kub.local`, and the provider in `files/app-argocd.yaml` lists
`https://argocd.kub.local/api/dex/callback`. A plain-HTTP value here breaks every sign-in, SSO and
local form alike, while the page itself still loads.

Traefik terminates that TLS: the chart's core Ingress is disabled (`server.ingress.enabled: false`)
and two IngressRoutes in `extraObjects` replace it, because one Ingress cannot carry the LAN gate,
the default internal certificate and a redirect-only plain-HTTP router at once. The TLS router sets
no secret, so it is served `kube-system/local-cert` (see [`internal-ca.md`](internal-ca.md)).

The client secret is not in git: it lives in `argocd-secret` under
`dex.authentik.clientSecret`, and must equal `ARGOCD_CLIENT_SECRET` in the `authentik-sso`
secret. If they differ, the browser completes the authentik login and Dex then fails the token
exchange with `invalid_client`; the fix is the patch below.

```bash
A='<argocd value from authentik-sso>'
kubectl -n argocd patch secret argocd-secret --type merge \
  -p "$(jq -n --arg a "$A" '{stringData:{"dex.authentik.clientSecret":$a}}')"
```

Order matters on the first cut-over to TLS: sync `authentik` first, so the provider lists the
`https` redirect address, then apply the release. The reverse order leaves a window where the
provider still knows only `http` and refuses the new redirect.

Apply the release:

```bash
helm -n argocd upgrade argocd argo/argo-cd --version 10.4.1 \
  -f cluster/helm/argocd/values.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=180s
```

The values carry `g, authentik Admins, role:admin` and `g, homelab-users, role:readonly`. The
local `admin` account stays enabled as break-glass; it is the only local account, so an authentik
e-mail typed into that form is always refused, whatever the password.

## Granting access

Three levels, decided by group membership:

| Level | Group | Effect |
|---|---|---|
| Admin | `authentik Admins` | Administers authentik; application administrator where the app maps groups |
| User | `homelab-users` | Opens the user-safe applications in their plain user role |
| Guest | `homelab-guests` | Default landing on enrolment; signs in, reaches nothing |

Promote with **Directory → Groups → homelab-users → Add existing user**; demote by removing them
from it. Enrolment always lands in `homelab-guests`. Each application binds the levels it admits
in its own blueprint, in `any` mode, so an application can be admin-only while another is open to
users. Removing somebody from a group takes their applications away at their next sign-in; it
does not stop them signing in to authentik.

Capability groups grant one extra thing on top of the level: `filebrowser-editors` exists but is
unused until FileBrowser is wired to authentik.

## FileBrowser (local login, authentik deferred)

FileBrowser is **not** behind authentik for now. It runs in namespace `filebrowser` with its own
accounts; the forward-auth chain, the embedded-outpost attachment and `filebrowser-editors` are
deferred. The provider is not attached to the outpost. The procedure to wire it later stays in
`docs/superpowers/plans/2026-11-14-sso-access-model.md`.

The first admin is created from the `filebrowser` Secret when the database is empty. Rotate it with:

```bash
kubectl -n filebrowser exec deploy/filebrowser -- filebrowser users update admin --password '<new>'
```

Non-admin accounts keep the same shape as before, now in the new namespace:

```bash
kubectl -n filebrowser exec deploy/filebrowser -- filebrowser users add \
  '<username>' "$(openssl rand -hex 24)" --scope '<scope-path>' \
  --lockPassword --perm.execute=false
```

Upstream archived FileBrowser on 2026-09-01; treat it as unmaintained.

## MFA

Optional. TOTP and WebAuthn (YubiKey) are offered from **MFA Devices** in the user's own
settings. The validation stage asks when a device is enrolled and skips when none is, and it is
bound to both `default-authentication-flow` and `default-source-authentication`; the second is
what makes it apply to Discord and Plex logins rather than only the password form.

Two YubiKeys is two WebAuthn devices on the same account; either satisfies the prompt.

Recovery: delete the lost device in the admin interface and let the person enrol again. The local
`akadmin` account is deliberately left without a device as break-glass for authentik itself.

## Verification

- Each authentik-managed application's login page offers authentik, and signing in lands back in
  the app.
- FileBrowser signs in with the local admin created from the `filebrowser` Secret; it does not
  redirect to authentik.
- A guest sees an empty application list; a bookmarked application URL is refused by authentik.
- Argo CD: the admin can sync; a `homelab-users` member reads only.

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
| radarr | `https://radarr.kub.local` | forward-auth; **`/api` exempt** since 2026-11-15 | off |
| lidarr | `https://lidarr.kub.local` | forward-auth; **`/api` exempt** since 2026-11-15 | off |
| prowlarr | `https://prowlarr.kub.local` | forward-auth; **`/api` exempt** since 2026-11-15 | off, probes fixed |
| bazarr | `https://bazarr.kub.local` | forward-auth; `/api` exempt | none (always was) |
| bazarr-anime | `https://bazarr-anime.kub.local` | forward-auth; `/api` exempt | none (always was) |
| tautulli | `https://tautulli.kub.local` | forward-auth; **`/api` exempt** since 2026-11-15 | **still on — needs a UI toggle** |
| maintainerr | `https://maintainerr.kub.local` | forward-auth; **`/api` exempt** (`lan-only` only) since 2026-11-15 | none exists |
| autobrr | `https://autobrr.kub.local` | **OIDC, no gate** | replaced by OIDC |
| qbittorrent | `https://qbittorrent.kub.local` | `lan-only` only — **no authentik gate yet** | its own login |
| sonarr-anime | `https://sonarr-anime.kub.local` | `lan-only` only — no authentik gate by decision | its own login |
| radarr-anime | `https://radarr-anime.kub.local` | `lan-only` only — no authentik gate by decision | its own login |

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
[`internal-ca.md`](internal-ca.md). Consequences worth knowing:

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

### Adding another tool to the gate

1. Copy `cluster/helm/authentik/files/app-bazarr.yaml`, change the slug, the display name and
   `external_host`. It is a **proxy** provider in `forward_single` mode, no client secret.
2. Add the file's name to the `metaapplyblueprint` list in `outpost-embedded.yaml`, and add a
   `!Find` line for the provider to that file's `providers` list. Missing either one is a silent
   404 from the outpost at the browser.
3. In the tool's chart values, put `authentik-forwardauth` after `lan-only` in the middleware list.
4. Bump both chart versions, open one PR, sync `authentik` and the tool.

Adding the middleware without step 2 is the mistake made once already: the route asks authentik
about a host authentik has never heard of, and authentik answers its own 404 page. The status code
is the fastest diagnostic — `404` means no provider for that host, `403` means the provider exists
but its policy refuses, `302` to authentik means it works.

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
[`internal-ca.md`](internal-ca.md) holds the backup rules, the restore drill, the renewal command and the
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
   source of truth, not the log tail.
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
   [`internal-ca.md`](internal-ca.md).
9. **Firefox does not read the Windows certificate store.** `certutil.exe -addstore -user Root` covers
   Chrome and Edge and does nothing for Firefox, which keeps its own NSS store; the CA has to be
   imported there too, with *Trust this CA to identify websites* ticked. Setting
   `security.enterprise_roots.enabled` to `true` makes Firefox follow Windows and removes the
   duplicate step.
10. **Calibre-Web's OIDC needs HTTPS, and its login type gates the button.** The button only renders
    when *Login type* is `OAuth`, and that setting forces `SESSION_COOKIE_SECURE`, which a plain-HTTP
    browser discards. Hence the TLS route. Leave *disable standard login* off so the password stays
    as break-glass on `http://localhost:8083`.

### Still open

- **qbittorrent** has no gate. Its `templates/ingress.yaml` is hand-written rather than the shared
  `ingressroute.yaml`, so it needs a middleware list added before the values can carry one.
- **tautulli** still shows its own login (UI toggle, above).
- **No media-stack API is behind the gate any more** (2026-11-15). Every chart built on the shared
  `ingressroute.yaml` has the `apiBypassMiddlewares` block, and the six gated tools that check their
  own key — sonarr, radarr, lidarr, prowlarr, tautulli — set it to `lan-only`, joining `bazarr`,
  `bazarr-anime` and `maintainerr`. Charts without an authentik gate (autobrr, sonarr-anime,
  radarr-anime, qbittorrent, calibre, kavita, plex, seerr) never had one on `/api` either, so they
  need nothing. Any new chart that copies the template must decide this explicitly.
- **maintainerr's `/api` is open to the LAN** (since 2026-11-15, by decision). The UI keeps the
  authentik gate; the API does not, and upstream checks no key on inbound requests. Close it with
  `apiBypassMiddlewares: []`, or put a `basicauth` middleware on the `/api` rule.
- autobrr's cached-config failure (trap 7) would go away with a `Cache-Control: no-store` header on
  `/api/auth/oidc/config`.
