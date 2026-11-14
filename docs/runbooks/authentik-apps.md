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

```bash
P='<pgadmin value>'
cat > /tmp/pgadmin-oidc.py <<EOF
AUTHENTICATION_SOURCES = ["oauth2", "internal"]
OAUTH2_CONFIG = [{
    "OAUTH2_NAME": "authentik",
    "OAUTH2_DISPLAY_NAME": "authentik",
    "OAUTH2_CLIENT_ID": "pgadmin",
    "OAUTH2_CLIENT_SECRET": "$P",
    "OAUTH2_SERVER_METADATA_URL": "https://authentik.kub.home.ashudev.com/application/o/pgadmin/.well-known/openid-configuration",
    "OAUTH2_SCOPE": "openid email profile",
}]
EOF
kubectl -n databases create secret generic pgadmin-oidc --from-file=config_local.py=/tmp/pgadmin-oidc.py
rm /tmp/pgadmin-oidc.py
```

Sync `pgadmin`. `internal` stays in the source list until an OIDC user has been promoted to the
pgAdmin Administrator role; remove it only after that.

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

Argo CD is not managed by this repo: `argocd-cm`, `argocd-secret` and `argocd-rbac-cm` are edited
by hand. It uses its bundled Dex, so the redirect is `/api/dex/callback`.

```bash
A='<argocd value>'
kubectl -n argocd patch secret argocd-secret --type merge \
  -p "$(jq -n --arg a "$A" '{stringData:{"dex.authentik.clientSecret":$a}}')"
```

Append to `argocd-cm` (patch `data`):

```yaml
url: http://argocd.kub.local
dex.config: |
  connectors:
    - config:
        issuer: https://authentik.kub.home.ashudev.com/application/o/argocd/
        clientID: argocd
        clientSecret: $dex.authentik.clientSecret
        insecureEnableGroups: true
        scopes: [openid, profile, email]
      name: authentik
      type: oidc
      id: authentik
```

Append to `argocd-rbac-cm`:

```yaml
policy.csv: |
  g, users, role:readonly
```

Restart `deploy/argocd-server` after the ConfigMap change. The local `admin` account stays
enabled as break-glass.

## Granting access

Apps bind to the `apps` group; `users` only means "may sign in". A new Discord or Plex user
therefore reaches nothing until added:

In the admin interface: **Directory → Groups → apps → Users → Add existing user**. Removing
somebody from `apps` takes their applications away at their next sign-in; it does not stop them
signing in to authentik.

## Verification

- Each application's login page offers authentik, and signing in lands back in the app.
- A person not in `users` sees the application refused by authentik (the dashboard hides it and
  the provider rejects the authorisation).
- Argo CD: the signed-in user can read applications but cannot sync or edit until added to a
  higher role in `argocd-rbac-cm`.
