# Wire an application to authentik

Four applications are wired in this phase: Argo CD, Grafana, pgAdmin and Kavita. The authentik
side is blueprint files in the chart (`files/app-*.yaml`) and needs only the client secrets.
The application side is different per app: two read it from a Secret, one is configured in its
UI, one is configured in ConfigMaps created by hand.

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

Kavita stores OIDC settings in its database, so this is a one-time pass in its UI (recorded here
because a rebuild needs it again):

1. Sign in to Kavita as administrator.
2. **Settings → OpenID Connect**.
3. **Authority**: `https://authentik.kub.home.ashudev.com/application/o/kavita/`
4. **Client Id**: `kavita`; **Secret**: the kavita value.
5. **Provision Accounts**: on. **Require Verified Emails**: off.
6. Save, then restart the Kavita pod (`kubectl -n media-stack rollout restart deploy/kavita`).

## 5. Argo CD

Argo CD is a hand-run Helm release, not an Application in this repo. Its Dex connector and RBAC
live in `cluster/helm/argocd/values.yaml`, so a chart upgrade keeps them. It uses its bundled Dex,
so the redirect is `/api/dex/callback`.

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

Capability groups grant one extra thing on top of the level: `filebrowser-editors` admits to
FileBrowser, whose folder scopes are set per account in FileBrowser.

## FileBrowser (forward auth)

FileBrowser has no OIDC. It authenticates on `X-authentik-username`, which the Traefik
forward-auth middleware sets after the outpost approves the request; a second middleware removes
any client-supplied value first, and must run before the forward-auth one.

**One assignment stays manual:** the blueprint creates the proxy provider, the application and the
capability group, but the provider must be attached to the embedded outpost by hand, in
**Applications → Outposts → authentik Embedded Outpost → Edit → Providers → add FileBrowser**. The
outpost object is managed by authentik and keyed differently from a blueprint-created object, so a
blueprint entry risks a duplicate-name rollback of the whole file. Until this is done the outpost
answers 403 for the host.

After a rebuild these two settings live only in FileBrowser's database:

```bash
kubectl -n media-stack exec deploy/filebrowser -- filebrowser config set \
  --auth.method=proxy --auth.header=X-authentik-username
```

Accounts are created before the person joins `filebrowser-editors`: proxy auth auto-creates an
account on first login with a per-user home, which is wider than intended if they sign in first.
Usernames must match authentik's `username` exactly.

```bash
kubectl -n media-stack exec deploy/filebrowser -- filebrowser users add \
  '<username>' "$(openssl rand -hex 24)" --perm.admin --lockPassword
kubectl -n media-stack exec deploy/filebrowser -- filebrowser users add \
  '<editor-username>' "$(openssl rand -hex 24)" --scope '<scope-path>' \
  --lockPassword --perm.execute=false
```

Folder scopes can be changed later with `filebrowser users edit --scope` or in the admin UI.
Upstream archived FileBrowser on 2026-09-01; never expose it except through this middleware chain.

## MFA

Optional. TOTP and WebAuthn (YubiKey) are offered from **MFA Devices** in the user's own
settings. The validation stage asks when a device is enrolled and skips when none is, and it is
bound to both `default-authentication-flow` and `default-source-authentication`; the second is
what makes it apply to Discord and Plex logins rather than only the password form.

Two YubiKeys is two WebAuthn devices on the same account; either satisfies the prompt.

Recovery: delete the lost device in the admin interface and let the person enrol again. The local
`akadmin` account is deliberately left without a device as break-glass for authentik itself.

## Verification

- Each application's login page offers authentik, and signing in lands back in the app.
- A guest sees an empty application list; a bookmarked application URL is refused by authentik.
- Argo CD: the admin can sync; a `homelab-users` member reads only.
- FileBrowser: a forged `X-authentik-username` still redirects to authentik.
