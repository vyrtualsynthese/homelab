# Wire an application to authentik

**Native SSO wins over the middleware** (as of 2026-11-16): an application that speaks OIDC is
configured to speak it, because the forward-auth gate cannot tell the app who the person is — every
browser that passes it arrives as the same identity.

Which application sits on which mechanism, what each route's state is, and what is still open:
[reference/sso-applications.md](../reference/sso-applications.md) — the matrix, the media-tools
detail and the two trap lists. This page keeps the procedures.

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

Argo CD runs its bundled Dex, so the redirect address is `/api/dex/callback`. Since 2026-11-19 it is
an Application in this repository, not a hand-run release: `cluster/argocd/projects/platform/argocd.yaml`
syncs the wrapper chart at `cluster/helm/argocd`, and that chart's values carry the connector, RBAC
and ingress. Rebuilding it from nothing is [`argocd.md`](argocd.md).

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
`https` redirect address, then let the `argocd` Application sync. The reverse order leaves a window
where the provider still knows only `http` and refuses the new redirect.

A values change reaches the cluster on the next sync of the `argocd` Application — there is no
`helm upgrade` step, and the Helm release record is deleted on adoption. If the controller itself is
down, [`argocd.md`](argocd.md) has the by-hand rebuild.

The values carry `g, authentik Admins, role:admin` and `g, homelab-users, role:readonly`. There is
**no local account**: `admin.enabled: "false"`, so authentik is the only way in. Break-glass is
patching `argocd-cm` back to `"true"` and rolling the server out, then signing in with the password
in `argocd-secret` — the command sits in `cluster/helm/argocd/values.yaml`, next to the setting.

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

Capability groups grant one extra thing on top of the level: `filebrowser-editors` admits its members
to FileBrowser, which then decides which folders they see.

## FileBrowser (OIDC, configured 2026-11-19)

FileBrowser is [FileBrowser Quantum](https://github.com/gtsteffaniak/filebrowser), the maintained fork
of the archived upstream, in namespace `filebrowser`. It authenticates through authentik natively; the
password form is **off**, so an unauthenticated browser is redirected straight to authentik.

Two Secrets carry the OAuth2 client secret, and both must hold the same value:

| Secret | Key | Read by |
|---|---|---|
| `filebrowser-oidc` (namespace `filebrowser`) | `CLIENTSECRET` | the app, as `FILEBROWSER_OIDC_CLIENT_SECRET` |
| `authentik-sso` (namespace `authentik`) | `FILEBROWSER_CLIENT_SECRET` | the worker, through `!Env` when the blueprint is applied |

Secrets do not cross namespaces, hence the pair. Create both from one generated value:

```bash
S=$(openssl rand -hex 24)
kubectl -n authentik patch secret authentik-sso --type merge \
  -p "$(jq -n --arg s "$S" '{stringData:{"FILEBROWSER_CLIENT_SECRET":$s}}')"
kubectl -n filebrowser create secret generic filebrowser-oidc \
  --from-literal=CLIENTSECRET="$S" --dry-run=client -o yaml | kubectl apply -f -
```

Changing the value means patching **both** Secrets and then restarting the worker: `!Env` is resolved
when the blueprint is applied, not when the Secret changes. The chart's PostSync hook recreates the
worker on the next `authentik` sync, which is the ordinary path.

The cutover order matters. Sync `authentik` **first**: its PostSync hook recreates the worker, which
applies the blueprint and creates `filebrowser-oidc` and the `filebrowser-editors` group. Only then
sync `filebrowser`, with **Prune** enabled, so the old Deployment, Service, Secret and the two old
claims are removed. A `filebrowser` sync first leaves the app pointing at a provider that does not
exist yet.

The blueprint is `cluster/helm/authentik/files/app-filebrowser.yaml`: provider `filebrowser-oidc`,
client id `filebrowser`, redirect URI
`https://filebrowser.kub.home.ashudev.com/api/auth/oidc/callback`, scopes `openid email profile`. The
application is bound to `authentik Admins` and `filebrowser-editors`; no outpost is involved.

**Delete the old forward-auth provider once.** The blueprint entry kept its `id` but changed its
identifiers from `filebrowser` (proxy) to `filebrowser-oidc` (OAuth2), so the apply creates the new
provider and leaves the old one behind. Remove it in **Directory → Federation & Providers**, and drop
its outpost assignment with it. Nothing reads it afterwards, but a stale provider is a second way in
that outlives its reason.

### When a sign-in fails

| Symptom | Cause | Fix |
|---|---|---|
| the app answers a callback error after authentik | the two Secrets hold different values, or the worker has not restarted since the patch | patch both from one value, restart the worker, restart the pod |
| authentik answers *The client identifier (client_id) is missing or invalid* | the blueprint that creates the provider failed, so the application still points at an older provider — usually `!Env` resolved to nothing because the worker started before the key reached `authentik-sso` | restart the worker, re-apply the blueprint, confirm `providers/oauth2/?search=<app>` shows the expected `client_id`, and delete the stale provider once the application points at the new one |
| every OIDC login is refused, the operator's included | the `groups` claim is missing or renamed, so `userGroups` matches nobody | re-enable the password method (§ Recovery), sign in, fix the profile scope mapping |
| the operator signs in and the sidebar is empty | the account has no source scopes yet — administrators are not exempt | grant the sources in **Settings → User management** |
| the pod stays in `ContainerCreating` | an export named in `nfsVolumes` is not served by the NAS | point that entry at an export the NAS actually serves; the mount error names the path |
| reads work but some folders are `permission denied` in the log | the assets export is a mergerfs pool, which sees only the container's **primary** gid; a group that is merely supplementary never reaches its permission check | set `securityContext.runAsGroup` to the folder's group, not `fsGroup` and not `supplementalGroups` |

### Accounts and folder scopes

Who gets in is decided in authentik; **what each person sees is decided inside FileBrowser**, because
authentik has no concept of a folder. Every source in the chart is `defaultEnabled: false`, so a
brand-new OIDC user starts with an empty sidebar — administrators included. After the first sign-in,
grant scopes in **Settings → User management → (the account) → Sources**: each scope is a source plus
a path inside it, and carries its own access level with a `deny` / `read` / `create` / `modify` /
`delete` set.

A scope on a **path**, not just on a source, is how one folder is granted without a dedicated NAS
export: `various` with the path `creator` shows that subdirectory of the assets export and nothing
else from it. The folder does not need its own volume, and adding one is what stalls the pod on a
mount the NAS does not serve.

The editor is a local authentik account in `filebrowser-editors` and `homelab-guests`, so nothing
depends on the Discord guild or a Plex share. Create one with:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
PW=$(openssl rand -base64 18)
curl -sS -X POST -H "Authorization: Bearer $AUTHENTIK_TOKEN" -H 'Content-Type: application/json' \
  'https://authentik.kub.home.ashudev.com/api/v3/core/users/' \
  -d "{\"username\":\"editor\",\"name\":\"Editor\",\"email\":\"\",\"is_active\":true,\"groups\":[<filebrowser-editors pk>,<homelab-guests pk>]}"
```

then `POST /api/v3/core/users/<pk>/set_password/` with `{"password": "<the generated one>"}` and hand
the password over out of band. Group ids come from `/api/v3/core/groups/?name=<name>`.

### Recovery when authentik is down

There is no local login to fall back on. Restore one through git, not through the UI:

1. In `cluster/helm/filebrowser/values.yaml`, set `auth.methods.password.enabled: true` and
   `signup: false`, and replace `adminPassword.passwordOverride` with a real password (the pinned
   `oidc-only-no-local-login` string exists only to keep renders deterministic).
2. Commit, then sync `filebrowser`.
3. Sign in as `admin` with that password and fix authentik.
4. Revert the commit and sync again.

Share links keep working during an authentik outage: the app answers `/share/...` itself, with no
gate in front. The files themselves are also reachable over SMB/NFS on the NAS, which is the recovery
that does not need the cluster at all.

## CrowdSec Web UI (OIDC, configured 2026-11-25)

Its own OIDC, from the `app-crowdsec-web-ui` blueprint: client id `crowdsec-web-ui`, confidential,
callback `https://crowdsec.kub.local/api/auth/oidc/callback`. The chart disables the password form,
so the first sign-in is the only way in; `/login?local=1` is the way past the automatic redirect.

Unlike every other application here it needs a second credential that authentik does not issue: a
CrowdSec *machine* (watcher) account, which is how it reads the LAPI's alerts and decisions. Create
it against the running LAPI, with an admin kubeconfig — the agent's shells are read-only and answer
`403`:

```bash
kubectl -n security exec deploy/crowdsec-lapi -- \
  cscli machines add crowdsec-web-ui --password "$(openssl rand -hex 32)" -f /dev/null
```

`-f /dev/null` keeps `cscli` from rewriting the container's own credentials file. Then the Secret
the chart reads, in `security` (its own namespace, because Secrets do not cross namespaces), with
the same client secret you put in `authentik-sso` as `CROWDSEC_CLIENT_SECRET`:

```bash
kubectl -n security create secret generic crowdsec-web-ui \
  --from-literal=lapi-password='<printed by cscli>' \
  --from-literal=oidc-client-secret='<CROWDSEC_CLIENT_SECRET>'
```

Order matters once: the `authentik` Application syncs first (it creates the provider), then the
machine and the Secret, then `crowdsec-web-ui`. A missing Secret is a `CreateContainerConfigError`;
a missing machine leaves the pod `Running` and the UI empty.

## The LDAP outpost (The Lounge)

The Lounge has no OIDC, so it authenticates people with an LDAP bind against authentik. The provider,
its application (`thelounge-ldap`) and the outpost (`ashulab-ldap`) are created by
`cluster/helm/authentik/files/app-thelounge-ldap.yaml`; the outpost pod and its Service
(`authentik-ldap`, port 3389) are `templates/ldap-outpost.yaml` in the same chart. Creating an outpost
generates its service account and token, and the token is the one thing git cannot hold: the pod reads
it from Secret `authentik-ldap`, key `AUTHENTIK_TOKEN`.

Create it once, after the blueprint has applied. The value is printed nowhere:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
API=https://authentik.kub.home.ashudev.com/api/v3
TOK=$(curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
  "$API/outposts/instances/?search=ashulab-ldap" \
  | jq -r '.results[] | select(.name=="ashulab-ldap") | .token_identifier')
KEY=$(curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/core/tokens/$TOK/view_key/" | jq -r '.key')
kubectl -n authentik create secret generic authentik-ldap \
  --from-literal=AUTHENTIK_TOKEN="$KEY" --dry-run=client -o yaml | kubectl apply -f -
unset KEY
```

Until the Secret exists the pod sits in `CreateContainerConfigError`; that is expected, and the rest of
the sync is healthy.

**If `TOK` comes back empty**, the outpost has no token and the Secret would hold `null`. Creating the
outpost object *does* generate the token — confirmed on 2026-11-20, when this one-liner produced a
working Secret — so an empty value means the outpost object did not apply: check the blueprint
instance (`GET $API/managed/blueprints/?search=outpost`) and the worker log first. Do not hand-craft a
token; a token that authentik did not issue for that outpost authenticates nothing.

Two checks mean the outpost is up: `GET $API/outposts/instances/?search=ashulab-ldap` lists
`ashulab-ldap` with `providers` holding `thelounge-ldap`, and
`GET $API/outposts/instances/<uuid>/health/` returns a record whose `version` is `2026.8.5`. The pod
log adds the listener line for port 3389.

A person signs in to The Lounge with their authentik username and their authentik password, not
their Lounge password — LDAP replaces the local login. If they enrol MFA later, the provider's
`mfa_support` must be turned on at the same time, and they type `password;123456`.

### Retiring the gate's objects

The forward-auth application `thelounge` and the proxy provider `thelounge` were the gate that LDAP
replaced on 2026-11-20. Git no longer declares them — `app-thelounge.yaml` is deleted and
`outpost-embedded.yaml` names neither — but **blueprints do not prune**, so the objects survive until
they are deleted through the API. Order matters twice:

1. **The commit must have synced first.** The embedded outpost's `providers` list is set whole, and a
   `!Find` on a provider that no longer exists rolls the whole file back — every host in it would go
   back to the previous list.
2. **The application before the provider.** The application holds the provider's foreign key.

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
API=https://authentik.kub.home.ashudev.com/api/v3
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE \
  -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/core/applications/thelounge/"
PK=$(curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/providers/proxy/?search=thelounge" \
  | jq -r '.results[] | select(.name=="thelounge") | .pk')
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE \
  -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/providers/proxy/$PK/"
```

Both answer `204`. The end state is one Lounge application (`thelounge-ldap`), one LDAP provider, and
no proxy provider for the app.

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
- FileBrowser redirects an unauthenticated browser to authentik; no password form is served.
- A guest sees an empty application list; a bookmarked application URL is refused by authentik.
- Argo CD: the admin can sync; a `homelab-users` member reads only.
