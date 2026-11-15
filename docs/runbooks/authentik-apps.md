# Wire an application to authentik

State, as of 2026-11-14:

| Application | authentik objects in git | Application configured | How |
|---|---|---|---|
| Grafana | yes | **yes** | Secret + chart values |
| pgAdmin | yes | **yes** | `config_local.py` in a Secret |
| Argo CD | yes | no | `cluster/helm/argocd/values.yaml` + `helm upgrade` |
| Kavita | yes | no | one pass in its own UI |
| FileBrowser | yes | no | proxy auth, forward-auth middleware |

Four applications have providers and application objects in the chart (`files/app-*.yaml`); only
Grafana and pgAdmin have been configured on the application side. Argo CD and Kavita therefore do
not sign anyone in yet, whatever their authentik objects say. The access matrix in the design
treats them as targets until their section below has been run.

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

Not wired yet. Kavita stores OIDC settings in its database, so this is a one-time pass in its UI
(recorded here because a rebuild needs it again):

1. Sign in to Kavita as administrator.
2. **Settings → OpenID Connect**.
3. **Authority**: `https://authentik.kub.home.ashudev.com/application/o/kavita/`
4. **Client Id**: `kavita`; **Secret**: the kavita value.
5. **Provision Accounts**: on. **Require Verified Emails**: off.
6. Save, then restart the Kavita pod (`kubectl -n media-stack rollout restart deploy/kavita`).

## 5. Argo CD

Not wired yet: Argo CD has no Dex connector and an empty RBAC policy. It is a hand-run Helm
release, not an Application in this repo. Its connector and RBAC live in
`cluster/helm/argocd/values.yaml`, so a chart upgrade keeps them. It uses its bundled Dex, so the
redirect is `/api/dex/callback`.

The client secret is not in git: it lives in `argocd-secret` under
`dex.authentik.clientSecret`, and must equal `ARGOCD_CLIENT_SECRET` in the `authentik-sso`
secret.

```bash
A='<argocd value from authentik-sso>'
kubectl -n argocd patch secret argocd-secret --type merge \
  -p "$(jq -n --arg a "$A" '{stringData:{"dex.authentik.clientSecret":$a}}')"
```

Apply the release:

```bash
helm -n argocd upgrade argocd argo/argo-cd --version 10.4.1 \
  -f cluster/helm/argocd/values.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=180s
```

The values carry `g, authentik Admins, role:admin` and `g, homelab-users, role:readonly`. The
local `admin` account stays enabled as break-glass.

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
| calibre | `calibre.kub.local` | **OIDC, no gate** | renamed to the authentik username; the password stays as break-glass |
| sonarr | `sonarr.kub.local` | forward-auth | off |
| radarr | `radarr.kub.local` | forward-auth | off |
| lidarr | `lidarr.kub.local` | forward-auth | off |
| prowlarr | `prowlarr.kub.local` | forward-auth | off, probes fixed |
| bazarr | `bazarr.kub.local` | forward-auth; `/api` exempt | none (always was) |
| bazarr-anime | `bazarr-anime.kub.local` | forward-auth; `/api` exempt | none (always was) |
| tautulli | `tautulli.kub.local` | forward-auth | **still on — needs a UI toggle** |
| maintainerr | `maintainerr.kub.local` | forward-auth | none exists |
| autobrr | `autobrr.kub.local` | **OIDC, no gate** | replaced by OIDC |
| qbittorrent | `qbittorrent.kub.local` | **not gated yet** | — |

Every route also carries `lan-only`. That middleware allows `192.168.1.0/24` (LAN), `10.8.0.0/24`
(WireGuard) and `10.42.0.0/16` (pods — see the first trap below).

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
The pattern, in place on `bazarr` and `bazarr-anime`, is a second rule on the same IngressRoute:

```yaml
ingressRoute:
  apiBypassMiddlewares:
    - name: lan-only
      namespace: kube-system
```

It renders `Host(...) && PathPrefix(/api)` with `priority: 100`. `lan-only` stays, so the API is
reachable from the LAN, not the internet, and the tool's own key is the credential. **Only do this
for a tool whose API is authenticated**: maintainerr has no login and no key, so exempting its API
would undo its gate entirely.

### Calibre-Web: native OIDC instead of the gate

Calibre-Web Automated **v4.0.8 speaks generic OIDC** — `cps/oauth_bb.py` calls authentik out by name — so
it uses autobrr's shape, not the gate's: an `authentik_providers_oauth2.oauth2provider` named `calibre` in
`cluster/helm/authentik/files/app-calibre.yaml`, and a route carrying `lan-only` and nothing else. An
earlier pass put `strip-authentik-header` and `authentik-forwardauth` on this route on the belief that it
had no OIDC; that belief was wrong for this build.

The gate is the wrong shape here for a second reason: **`/opds` and the Kobo sync endpoints authenticate
with basic auth** (the Calibre-Web username and password) and cannot follow a redirect to authentik. A
gate would need an exemption for them; native OIDC needs none, and the app's own session is all the
browser needs.

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
   OAuth Redirect Host `http://calibre.kub.local`, client id `calibre`, the secret from step 1, scopes
   `openid profile email`, username field `preferred_username`, admin group `authentik Admins`, and tick
   **Enable OAuth Group-Based Admin Role Management**. Saving restarts Calibre-Web on its own; if the
   "OpenID Connect" button is still missing afterwards, delete the pod.

The `groups` claim comes from authentik's own `profile` scope mapping, which already lists the user's
group names. So every account in `authentik Admins` becomes a Calibre-Web administrator on login, without
anyone touching Calibre-Web's user list — and an account removed from the group loses the role again.
That only reaches people the application's policy already lets in, which is `authentik Admins` alone.

Calibre-Web cannot log the user out of authentik: its OIDC logout clears its own session and nothing
more, and it has no end-session call. Same limitation as autobrr.

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

### Still open

- **qbittorrent** has no gate. Its `templates/ingress.yaml` is hand-written rather than the shared
  `ingressroute.yaml`, so it needs a middleware list added before the values can carry one.
- **tautulli** still shows its own login (UI toggle, above).
- The other tools' APIs (sonarr, radarr, lidarr, prowlarr, tautulli, maintainerr) are still gated.
  Only bazarr and bazarr-anime have the `/api` rule and the `apiBypassMiddlewares` template block;
  the other charts need the same block copied in.
- autobrr's cached-config failure (trap 7) would go away with a `Cache-Control: no-store` header on
  `/api/auth/oidc/config`.
