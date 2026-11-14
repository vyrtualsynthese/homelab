# Authentik phase 3 — Argo CD, Grafana, pgAdmin, Kavita

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** These four applications authenticate through authentik, and only people in `users` get
in. Discord and Plex remain the ways to sign in to authentik itself.

**Architecture:** Each application gets one blueprint file in the chart that creates one OIDC
provider, one application and one group binding. Three applications read their client secret from
a Secret in their own namespace; Kavita is configured once in its UI. Argo CD is configured by
hand because this repo does not manage the Argo CD installation.

**Tech Stack:** authentik 2026.8.5, Grafana 13.3.1, pgAdmin 9.19, Kavita 0.9.1, Argo CD 3.5.

**Spec:** `docs/superpowers/specs/2026-11-14-authentik-sso-design.md` (phase 3), and
`docs/runbooks/authentik-apps.md` for the operator steps.

## Global Constraints

- One blueprint per app, one client ID per app: `argocd`, `grafana`, `pgadmin`, `kavita`.
- No secret in git: `client_secret` is `!Env <APP>_CLIENT_SECRET`, read from `authentik-sso`.
- Every provider: confidential client, strict redirect URI, authentik's self-signed signing key,
  the implicit-consent authorization flow, the default invalidation flow, the openid/email/
  profile scope mappings, and the two grant types `authorization_code` and `refresh_token`
  (blueprint-created providers start with none, unlike the UI wizard).
- Every application: `policy_engine_mode: all` and one group binding to `apps`, which starts
  empty. `users` is the right to sign in, not the right to open anything.
- The operator (user 4) is added to `apps` by hand after the first sync, so the apps stay usable.
- Redirect URIs are exact. HTTP is used where the app is served over HTTP; the issuer stays HTTPS.
- Commit prefixes use an emoji; never commit to `main` or `develop`.

## Review Focus

1. **A member of `users` cannot get in.** Redirect URI typo, missing scope mapping, or the
   application's binding absent. Task 1 checks each provider's redirect and binding through the
   API before any app is touched.
2. **A non-member can get in.** The binding exists but `policy_engine_mode` is `any` or the
   binding is on the provider instead of the application. Task 1 asserts `policy_engine_mode`.
3. **Secret missing at sync time.** `!Env` fails the whole file and the provider never appears.
   Task 1 checks the four keys exist before syncing.
4. **HTTP redirect rejected.** Three apps are served over HTTP on `*.kub.local`. If authentik
   refuses the URI, the provider blueprint fails and the log names it; the fallback is TLS on the
   app, not a weaker redirect.
5. **Argo CD lockout.** Dex misconfiguration can lock out the UI while the CLI still works. The
   local `admin` account stays enabled; Task 5 keeps it as break-glass and tests the new path in a
   private window.

---

### Task 1: Providers, applications and access bindings

**Files:**
- Create: `cluster/helm/authentik/files/app-argocd.yaml`, `app-grafana.yaml`, `app-pgadmin.yaml`,
  `app-kavita.yaml`
- Modify: `docs/secrets.md`

**Interfaces:**
- Produces: providers and applications with slugs `argocd`, `grafana`, `pgadmin`, `kavita`;
  client IDs identical to the slugs; client secrets `ARGOCD_CLIENT_SECRET`,
  `GRAFANA_CLIENT_SECRET`, `PGADMIN_CLIENT_SECRET`, `KAVITA_CLIENT_SECRET` in `authentik-sso`.

- [ ] **Step 1: Create the four client secrets**

Run the block in `docs/runbooks/authentik-apps.md` §1. Expected: the patch is accepted and four
values print. Verify the key names only:

```bash
kubectl -n authentik get secret authentik-sso -o json | jq -r '.data | keys[]' | sort
```

- [ ] **Step 2: Commit and sync**

The four blueprint files are already written. Commit, open the PR, merge, sync. After the sync:

```bash
TOKEN=$(grep '^AUTHENTIK_TOKEN=' ~/.config/secrets/homelab.env | cut -d= -f2-)
for slug in argocd grafana pgadmin kavita; do
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://authentik.kub.home.ashudev.com/api/v3/core/applications/$slug/" \
    | jq -c '{slug,provider_name:.provider.name,policy_engine_mode}'
done
```

Expected: four applications, each with a provider, `policy_engine_mode` equal to `all`.

- [ ] **Step 3: Check each provider's redirect and bindings**

```bash
for slug in argocd grafana pgadmin kavita; do
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://authentik.kub.home.ashudev.com/api/v3/providers/oauth2/$slug/" \
    | jq -c '{name,client_id,redirect_uris,property_mappings:(.property_mappings|length)}'
done
```

Expected: the exact redirect URI per app, `client_id` equal to the slug, three scope mappings.

### Task 2: Grafana

**Files:** `cluster/helm/monitoring/grafana/values.yaml` (already written).

- [ ] **Step 1: Create the chart's secret**

```bash
kubectl -n monitoring create secret generic grafana-oidc \
  --from-literal=GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET='<grafana value>'
```

- [ ] **Step 2: Merge, sync, and sign in**

The login page offers **Sign in with authentik**. Expected: signing in returns to Grafana as
`Viewer`; the browser tab shows the Grafana home page, not an OAuth error.

### Task 3: pgAdmin

**Files:** `cluster/helm/databases/pgadmin/values.yaml` (already written).

- [ ] **Step 1: Create the config secret**

Run §3 of the runbook. Expected: `secret/pgadmin-oidc created`, key `config_local.py`.

- [ ] **Step 2: Merge, sync, and sign in**

Expected: pgAdmin's login page offers **authentik** beside the internal login; signing in creates
a pgAdmin user and lands on the dashboard.

### Task 4: Kavita

**Files:** none; the settings live in Kavita's database.

- [ ] **Step 1: Configure and restart**

Run §4 of the runbook. Expected after restart: a **Login with SSO** button; signing in returns to
Kavita signed in, with the account provisioned on first use.

### Task 5: Argo CD

**Files:** none in git; `argocd-cm`, `argocd-secret`, `argocd-rbac-cm` are hand-managed.

- [ ] **Step 1: Patch the secret, ConfigMap and RBAC**

Run §5 of the runbook, then `kubectl -n argocd rollout restart deploy/argocd-server`.

- [ ] **Step 2: Verify without locking out**

In a private window open `http://argocd.kub.local`, press **Log in via authentik**, and confirm the
application list appears. The local `admin` login still works in a normal window and stays as
break-glass.

## Success, in one place

- Four applications in authentik, each bound to `apps` with `policy_engine_mode: all`.
- A newly enrolled Discord or Plex user can sign in to authentik and reaches no application.
- Grafana, pgAdmin, Kavita and Argo CD all sign in through authentik.
- A person outside `users` is refused by authentik before reaching the app.
- No client secret exists anywhere in git.
