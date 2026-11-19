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
