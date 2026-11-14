# SSO access model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the homelab three access levels (admin, user, guest) enforced by authentik groups, one capability-gated application (FileBrowser folder access), and optional MFA that actually applies on Discord and Plex logins.

**Architecture:** Groups in an authentik blueprint define the levels; each application blueprint binds to the levels that may open it, in `any` mode, replacing the single `app-access` expression policy currently on `develop`. Grafana and ArgoCD map the admin group to their admin role; pgAdmin, Kavita and Discourse keep a recorded local promotion. FileBrowser is protected by a Traefik forward-auth middleware pointing at authentik's embedded outpost and consumes the `X-authentik-username` header with its native proxy auth; folder scopes stay in its database. The MFA validation stage is bound into the password flow and the source authentication flow with its "skip if no device" default.

**Tech Stack:** authentik 2026.8.5 (blueprints), Traefik v2 CRDs (`traefik.containo.us/v1alpha1`), K3s, ArgoCD 3.5.2 (hand-run Helm release), FileBrowser value-only chart.

**Spec:** `docs/superpowers/specs/2026-11-14-sso-access-model-design.md`

**Base:** `feature/sso-access-model` cut from `origin/develop` at `30315fb6`.

## Global Constraints

- authentik chart and image stay `2026.8.5`; only blueprints and chart values change.
- Traefik CRDs are `traefik.containo.us/v1alpha1` (v2), never `traefik.io/v1alpha1` and never a new `networking.k8s.io` Ingress.
- `cluster/argocd/**` files are CRLF; this plan does not edit them. Strip `\r` when reading them in scripts.
- No secret goes into git. This plan adds no new secret.
- No `syncPolicy.automated` in any Application file.
- FileBrowser image must be `v2.63.23` (last 2.63.x, contains the proxy-auth scope fix from 2.63.19).
- Middleware names live in `kube-system` and are referenced as `kube-system-<name>@kubernetescrd`.
- Commit messages use an emoji prefix: `🚀` feature, `🐛` fix, `📝` docs, `♻️` refactor, `🔧` chore.
- YAML: 2 spaces, lowercase keys.
- Never commit to `develop` or `main`.

## Review Focus

- A user in `homelab-users` must be refused by Grafana and pgAdmin. No user-safe application is wired yet, so "refused everywhere" is the correct current outcome, not a failure.
- A guest must finish enrolment and see an empty application list; a bookmarked admin-only app URL must be refused by authentik, not by the app.
- Someone who enrolled TOTP or WebAuthn must be challenged when signing in with Discord or Plex; someone with no device must not be.
- A request carrying a forged `X-authentik-username` must never be treated as that user by FileBrowser.
- Removing someone from `homelab-users` must remove application access at the next sign-in, while leaving their account and guest sign-in intact.

---

### Task 1: Level groups and enrolment default

Creates `homelab-users` and `homelab-guests` beside the existing `users`/`apps` groups (both stay until Task 5, or every `!Find` in the app blueprints fails), and points new enrolments at `homelab-guests`. The applications still bind the `app-access` policy after this task; nothing changes for users yet.

**Files:**
- Rename: `cluster/helm/authentik/files/users.yaml` → `cluster/helm/authentik/files/groups.yaml`
- Modify: `cluster/helm/authentik/files/sources.yaml:134-138`
- Modify: `cluster/helm/authentik/files/app-{argocd,grafana,kavita,pgadmin}.yaml` (the `ashulab-users` metaapply reference only)
- Modify: `cluster/helm/authentik/Chart.yaml` (`0.2.0` → `0.3.0`)

**Interfaces:**
- Produces: blueprint metadata name `ashulab-groups`; groups `homelab-users`, `homelab-guests`; `default-source-enrollment-write` writes `homelab-guests`.

- [ ] **Step 1: Assert the current state (RED)**

```bash
helm template t cluster/helm/authentik -f cluster/helm/authentik/values.yaml --namespace authentik > /tmp/before.yaml
grep -c "name: homelab-users" /tmp/before.yaml   # expect 0
grep -n "create_users_group" /tmp/before.yaml    # expect name: users
```

- [ ] **Step 2: Rename the file and write the new content**

`git mv cluster/helm/authentik/files/users.yaml cluster/helm/authentik/files/groups.yaml`, then:

```yaml
version: 1
metadata:
  name: ashulab-groups
entries:
  - model: authentik_core.group
    id: homelab-users
    identifiers:
      name: homelab-users
    attrs:
      name: homelab-users
      is_superuser: false
      # May open the applications marked user-safe, always in the plain user
      # role. Application administrator is the built-in `authentik Admins`.
  - model: authentik_core.group
    id: homelab-guests
    identifiers:
      name: homelab-guests
    attrs:
      name: homelab-guests
      is_superuser: false
      # Default landing for every new identity. Signs in, reaches nothing.
  # Temporary: retired by Task 5 once memberships have moved. Keep until then
  # so the application blueprints' !Find lookups still resolve.
  - model: authentik_core.group
    id: users
    identifiers:
      name: users
    attrs:
      name: users
      is_superuser: false
  - model: authentik_core.group
    id: apps
    identifiers:
      name: apps
    attrs:
      name: apps
      is_superuser: false
```

- [ ] **Step 3: Point enrolment at the guest group**

In `files/sources.yaml`, the last entry becomes:

```yaml
      create_users_group: !Find [authentik_core.group, [name, homelab-guests]]
```

- [ ] **Step 4: Update the required blueprint name in the four app files**

In each of `app-argocd.yaml`, `app-grafana.yaml`, `app-kavita.yaml`, `app-pgadmin.yaml`, change every `name: ashulab-users` under a `metaapplyblueprint` identifier to `name: ashulab-groups`. Leave the `ashulab-app-access` metaapply in place; Task 2 removes it.

- [ ] **Step 5: Bump the chart version and render (GREEN)**

`Chart.yaml`: `version: 0.3.0`.

```bash
helm template t cluster/helm/authentik -f cluster/helm/authentik/values.yaml --namespace authentik > /tmp/after.yaml
grep -c "name: homelab-users" /tmp/after.yaml    # expect 2 (identifiers + attrs)
grep -c "name: homelab-guests" /tmp/after.yaml   # expect 2
grep -c "name: homelab-guests\]" /tmp/after.yaml # expect 1 (create_users_group)
grep -c "ashulab-groups" /tmp/after.yaml         # expect 5 (metadata + 4 apps)
grep -c "ashulab-app-access" /tmp/after.yaml     # expect 4 (removed in Task 2)
```

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/authentik
git commit -m "🚀 feat(sso): add homelab-users and homelab-guests level groups"
```

---

### Task 2: Retire `app-access` and bind the applications to levels

Removes the single expression policy and gives each wired application explicit group bindings. Multi-level applications must run in `any` mode; in `all` mode two bindings require both groups, which is the trap the policy was working around.

**Files:**
- Delete: `cluster/helm/authentik/files/access-policy.yaml`
- Modify: `cluster/helm/authentik/files/app-argocd.yaml`
- Modify: `cluster/helm/authentik/files/app-grafana.yaml`
- Modify: `cluster/helm/authentik/files/app-pgadmin.yaml`
- Modify: `cluster/helm/authentik/files/app-kavita.yaml`
- Modify: `cluster/helm/authentik/Chart.yaml` (`0.3.0` → `0.3.1`)

**Interfaces:**
- Consumes: groups from Task 1.
- Produces: admin binding to `authentik Admins` on all four apps; user binding to `homelab-users` on Kavita only; no reference to `app-access` or `apps` anywhere in `files/`.

- [ ] **Step 1: Assert the current state (RED)**

```bash
grep -rc "app-access" cluster/helm/authentik/files/ | grep -v ":0"
grep -c "group, \[name, apps\]" <(helm template t cluster/helm/authentik -f cluster/helm/authentik/values.yaml --namespace authentik)
```

Expected: four files reference `app-access`; no group bindings exist.

- [ ] **Step 2: Delete the policy and its references**

`git rm cluster/helm/authentik/files/access-policy.yaml`. In each of the four app files, remove the `metaapplyblueprint` entry naming `ashulab-app-access`.

- [ ] **Step 3: Bind admin-only apps to the admin level**

In `app-argocd.yaml`, `app-grafana.yaml`, `app-pgadmin.yaml`, replace the final binding with:

```yaml
  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf <app>-app
      order: 0
    attrs:
      group: !Find [authentik_core.group, [name, authentik Admins]]
```

Use the existing `!KeyOf` id (`argocd-app`, `grafana-app`, `pgadmin-app`). Set that application's `policy_engine_mode` to `any` in the same edit — with one binding it behaves identically today, and it stops a future second binding from silently becoming AND. Replace the comment above the binding with `# Admin level only: this application has no user surface.`

- [ ] **Step 4: Give Kavita both levels**

Set Kavita's application entry to `policy_engine_mode: any`, then replace the final binding with:

```yaml
  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf kavita-app
      order: 0
    attrs:
      group: !Find [authentik_core.group, [name, authentik Admins]]
  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf kavita-app
      order: 10
    attrs:
      group: !Find [authentik_core.group, [name, homelab-users]]
```

- [ ] **Step 5: Bump the version and render (GREEN)**

`Chart.yaml`: `version: 0.3.1`.

```bash
helm template t cluster/helm/authentik -f cluster/helm/authentik/values.yaml --namespace authentik > /tmp/after.yaml
grep -c "app-access" /tmp/after.yaml              # expect 0
grep -c "name: authentik Admins" /tmp/after.yaml  # expect 0 (rendered refs use [name, ...])
grep -c "\[name, authentik Admins\]" /tmp/after.yaml # expect 4
grep -c "\[name, homelab-users\]" /tmp/after.yaml    # expect 1 (kavita)
grep -c "policy_engine_mode: any" /tmp/after.yaml # expect 4 (kavita + three admin-only)
```

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/authentik
git commit -m "♻️ refactor(sso): replace the app-access policy with level bindings"
```

The remaining user-safe applications in the matrix (Wiki.js, Discourse, Ghostfolio, Firefly III, Postiz) are not wired to authentik yet; the per-application programme wires them, and each must bind `homelab-users` plus `authentik Admins` with `policy_engine_mode: any`.

---

### Task 3: ArgoCD SSO through its own chart values

ArgoCD has no Dex connector and an empty RBAC policy. Both ConfigMaps are rendered by the `argo-cd` Helm release, so the settings go in the repo's values file and are applied with `helm upgrade`; a `kubectl patch` would be wiped by the next upgrade.

**Files:**
- Modify: `cluster/helm/argocd/values.yaml` (`configs:` block at line 12)
- Live: `argocd-secret` key `dex.authentik.clientSecret` (operator, from `authentik-sso`)

**Interfaces:**
- Consumes: `authentik Admins`, `homelab-users`; existing provider `argocd` with redirect `http://argocd.kub.local/api/dex/callback`.
- Produces: `argocd-cm` with `dex.config`, `argocd-rbac-cm` with `policy.csv`.

- [ ] **Step 1: Confirm the release name and current values**

```bash
helm -n argocd list
helm -n argocd get values argocd | head -20
kubectl -n argocd get cm argocd-cm -o jsonpath='{.data.dex\.config}' ; echo
```

Expected: one release named `argocd`; empty `dex.config`.

- [ ] **Step 2: Add the connector and RBAC to the values file**

Under `configs:` in `cluster/helm/argocd/values.yaml`:

```yaml
configs:
  params:
    server.insecure: true
  cm:
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
  rbac:
    policy.csv: |
      g, authentik Admins, role:admin
      g, homelab-users, role:readonly
```

- [ ] **Step 3: Put the client secret in the release's secret (operator)**

The value is the `ARGOCD_CLIENT_SECRET` already in `authentik-sso`; the same string must be in both places.

```bash
kubectl -n argocd patch secret argocd-secret --type merge \
  -p "$(jq -n --arg s '<ARGOCD_CLIENT_SECRET>' '{stringData:{"dex.authentik.clientSecret":$s}}')"
```

- [ ] **Step 4: Apply the chart and wait**

```bash
helm -n argocd upgrade argocd <chart-ref> -f cluster/helm/argocd/values.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=180s
kubectl -n argocd get cm argocd-cm -o jsonpath='{.data.dex\.config}' | head -5
```

Expected: the connector is present in `argocd-cm`.

- [ ] **Step 5: Verify both levels**

Sign in as the admin: the **Log in via authentik** button appears and grants Sync. Sign in as a `homelab-users` member: applications are read-only. The local `admin` account still logs in as break-glass.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/argocd/values.yaml
git commit -m "🚀 feat(argocd): sign in through authentik with level-based RBAC"
```

---

### Task 4: Move memberships and promote administrators (operator step)

Groups are identified by name; the level groups exist but are empty, and the old groups still carry everyone.

**Files:**
- None in git (UI only).

**Interfaces:**
- Consumes: `homelab-users`, `homelab-guests`, `authentik Admins`.
- Produces: populated level groups; `apps`/`users` empty.

- [ ] **Step 1: Copy memberships**

In authentik **Directory → Groups**: for each person in `apps`, add them to `homelab-users`; add every identity that is not in `apps` to `homelab-guests`. Admins may stay only in `authentik Admins`.

- [ ] **Step 2: Promote the administrator where groups cannot do it**

pgAdmin: sign in through OIDC as the operator → **User management** → own account → Administrator.

Kavita keeps its administrator as an account flag too, but Kavita is not wired yet (no OIDC settings in its database), so this waits until it is: sign in through OIDC, then **Settings → Users** → own account → Role: Admin.

- [ ] **Step 3: Verify a guest**

A private window with an account only in `homelab-guests`: login succeeds, application list empty, `https://pgadmin.kub.local/` refuses the sign-in through authentik.

- [ ] **Step 4: Verify a user**

An account in `homelab-users`: the application list is empty and Grafana and pgAdmin both refuse it. That is the expected state until a user-safe application (Kavita, Wiki.js) is wired.

- [ ] **Step 5: Verify revocation**

Add a test account to `homelab-users`, confirm Grafana still refuses it, remove it, sign in again: same refusal, account intact.

---

### Task 5: Retire the old groups

**Files:**
- Modify: `cluster/helm/authentik/files/groups.yaml`
- Modify: `cluster/helm/authentik/Chart.yaml` (`0.3.1` → `0.3.2`)

- [ ] **Step 1: Confirm nothing references the old groups**

```bash
grep -rnE "name: (apps|users)$" cluster/helm/authentik/files/
```

Expected: only the two temporary entries in `groups.yaml`. Any binding is a bug; fix before proceeding.

- [ ] **Step 2: Remove the entries (GREEN check)**

Delete the two entries and the temporary comment. Bump to `0.3.2`.

```bash
helm template t cluster/helm/authentik -f cluster/helm/authentik/values.yaml --namespace authentik > /tmp/after.yaml
grep -c "name: apps" /tmp/after.yaml    # expect 0
grep -c "name: users" /tmp/after.yaml   # expect 0
```

- [ ] **Step 3: Commit, then delete the old groups in the UI**

```bash
git add cluster/helm/authentik
git commit -m "♻️ refactor(sso): retire the users and apps groups"
```

After the sync and a clean worker log, delete the empty `users` and `apps` groups.

---

### Task 6: Forward-auth middleware and FileBrowser image

`cluster/infrastructure/**` is not managed by any Application in this repo; it is applied by hand, as the existing middlewares already are.

**Files:**
- Create: `cluster/infrastructure/traefik/middleware/authentik-forwardauth.yaml`
- Create: `cluster/infrastructure/traefik/middleware/strip-authentik-header.yaml`
- Modify: `cluster/helm/media-stack/filebrowser/values.yaml` (image tag and router middleware annotation)

**Interfaces:**
- Produces: `kube-system/authentik-forwardauth`, `kube-system/strip-authentik-header`; FileBrowser `v2.63.23`; router chain strip → forwardauth → security.

- [ ] **Step 1: Write the forward-auth middleware**

```yaml
apiVersion: traefik.containo.us/v1alpha1
kind: Middleware
metadata:
  name: authentik-forwardauth
  namespace: kube-system
spec:
  forwardAuth:
    # The embedded outpost is served by the server pod: Service port 80 maps to
    # container 9000. Do not point this at the public Ingress.
    address: http://authentik-server.authentik.svc.cluster.local/outpost.goauthentik.io/auth/traefik
    trustForwardHeader: true
    authResponseHeaders:
      - X-authentik-username
      - X-authentik-groups
      - X-authentik-email
      - X-authentik-name
      - X-authentik-uid
```

- [ ] **Step 2: Write the stripping middleware**

```yaml
apiVersion: traefik.containo.us/v1alpha1
kind: Middleware
metadata:
  name: strip-authentik-header
  namespace: kube-system
spec:
  headers:
    # FileBrowser trusts X-authentik-username blindly. An empty value removes
    # the header, so a client-supplied value never reaches the app; the outpost
    # then sets it from the authenticated session.
    customRequestHeaders:
      X-authentik-username: ""
```

- [ ] **Step 3: Apply and confirm**

```bash
kubectl apply -f cluster/infrastructure/traefik/middleware/authentik-forwardauth.yaml
kubectl apply -f cluster/infrastructure/traefik/middleware/strip-authentik-header.yaml
kubectl -n kube-system get middleware authentik-forwardauth strip-authentik-header
```

- [ ] **Step 4: Update FileBrowser values**

```yaml
  tag: "v2.63.23"
```

and:

```yaml
    traefik.ingress.kubernetes.io/router.middlewares: "kube-system-strip-authentik-header@kubernetescrd,kube-system-authentik-forwardauth@kubernetescrd,kube-system-security@kubernetescrd"
```

- [ ] **Step 5: Verify the chain before switching the app (GREEN)**

```bash
curl -sI https://filebrowser.kub.home.ashudev.com/ | head -5
```

Expected: `302` with `Location:` containing `authentik.kub.home.ashudev.com`. A `200` means the chain is wrong; stop.

The router stays a `networking.k8s.io` Ingress because the remote chart renders one; converting it is out of scope and is a pre-existing known issue.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/media-stack/filebrowser/values.yaml cluster/infrastructure/traefik/middleware
git commit -m "🚀 feat(filebrowser): protect the router with authentik forward auth"
```

---

### Task 7: authentik proxy provider and capability group

**Files:**
- Create: `cluster/helm/authentik/files/app-filebrowser.yaml`
- Modify: `cluster/helm/authentik/Chart.yaml` (`0.3.2` → `0.4.0`)

**Interfaces:**
- Consumes: `ashulab-groups`.
- Produces: `filebrowser-editors`; provider `filebrowser` (`forward_single`); application slug `filebrowser`; bindings to `authentik Admins` (0) and `filebrowser-editors` (10); provider attached to `authentik Embedded Outpost`.

- [ ] **Step 1: Write the blueprint**

```yaml
version: 1
metadata:
  name: ashulab-app-filebrowser
entries:
  - model: authentik_blueprints.metaapplyblueprint
    attrs:
      identifiers:
        name: ashulab-groups
      required: true

  # Capability group: admits to FileBrowser. Folder scope is per account inside
  # FileBrowser, which authentik cannot describe.
  - model: authentik_core.group
    id: filebrowser-editors
    identifiers:
      name: filebrowser-editors
    attrs:
      name: filebrowser-editors
      is_superuser: false

  # No internal host and no client secret: the outpost checks the request and
  # the app authenticates on the header.
  - model: authentik_providers_proxy.proxyprovider
    id: filebrowser-provider
    identifiers:
      name: filebrowser
    attrs:
      name: filebrowser
      mode: forward_single
      external_host: https://filebrowser.kub.home.ashudev.com
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]

  - model: authentik_core.application
    id: filebrowser-app
    identifiers:
      slug: filebrowser
    attrs:
      name: FileBrowser
      slug: filebrowser
      provider: !KeyOf filebrowser-provider
      meta_launch_url: https://filebrowser.kub.home.ashudev.com/
      policy_engine_mode: any

  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf filebrowser-app
      order: 0
    attrs:
      group: !Find [authentik_core.group, [name, authentik Admins]]
  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf filebrowser-app
      order: 10
    attrs:
      group: !KeyOf filebrowser-editors

# The provider must also be attached to `authentik Embedded Outpost`, which is a
# managed object keyed by `managed`, not by `name`. A blueprint entry matching
# on `name` risks creating a duplicate and rolling back this whole file
# atomically, so that one assignment stays a UI step (step 4). Until it is done
# the outpost answers 403 for the host.
```

- [ ] **Step 2: Render and assert**

```bash
helm template t cluster/helm/authentik -f cluster/helm/authentik/values.yaml --namespace authentik > /tmp/after.yaml
grep -c "filebrowser-editors" /tmp/after.yaml            # expect >= 2
grep -c "forward_single" /tmp/after.yaml                 # expect 1
grep -c "authentik Embedded Outpost" /tmp/after.yaml     # expect 1 (comment only)
```

- [ ] **Step 3: Commit, sync, read the worker log**

```bash
git add cluster/helm/authentik
git commit -m "🚀 feat(sso): wire FileBrowser through authentik forward auth"
kubectl -n authentik logs deploy/authentik-worker --tail=200 | grep -i "blueprint\|error"
```

Expected: no blueprint error naming `app-filebrowser.yaml`.

- [ ] **Step 4: Attach the provider to the embedded outpost (operator, UI)**

**Applications → Outposts → authentik Embedded Outpost → Edit → Providers → add FileBrowser → Save.**
This is a UI step by decision: the outpost is managed and keyed differently, and a blueprint entry that fails would roll back this whole file. Until it is done, the outpost answers 403 for the host.

- [ ] **Step 5: Verify refusal for a non-member**

A `homelab-users` member opens `https://filebrowser.kub.home.ashudev.com/`: authentik's refusal page, not FileBrowser's UI.

---

### Task 8: Switch FileBrowser to proxy auth and create accounts

**Files:**
- Live: FileBrowser settings and users.
- Doc: `docs/runbooks/authentik-apps.md` (Task 10).

**Interfaces:**
- Consumes: Task 6 middleware, Task 7 provider.
- Produces: `auth.method=proxy`, `auth.header=X-authentik-username`; admin account named exactly as the operator's authentik username; editor account with a scope.

- [ ] **Step 1: Record exact usernames**

```bash
kubectl -n authentik exec deploy/authentik-worker -- ak shell -c \
  "from authentik.core.models import User; print([u.username for u in User.objects.all()])"
```

- [ ] **Step 2: Create the FileBrowser admin account**

```bash
kubectl -n media-stack exec deploy/filebrowser -- filebrowser users add \
  '<operator-username>' "$(openssl rand -hex 24)" --perm.admin --lockPassword
```

- [ ] **Step 3: Create the editor account with its scope**

Replace `<scope-path>` with the folder the editor may see, inside the FileBrowser root:

```bash
kubectl -n media-stack exec deploy/filebrowser -- filebrowser users add \
  '<editor-username>' "$(openssl rand -hex 24)" --scope '<scope-path>' \
  --lockPassword --perm.execute=false
```

- [ ] **Step 4: Switch the method and restart**

```bash
kubectl -n media-stack exec deploy/filebrowser -- filebrowser config set \
  --auth.method=proxy --auth.header=X-authentik-username
kubectl -n media-stack rollout restart deploy/filebrowser
kubectl -n media-stack rollout status deploy/filebrowser --timeout=180s
```

- [ ] **Step 5: Verify the operator and the runner**

Sign in through authentik: FileBrowser opens as admin with the full tree.

```bash
kubectl -n media-stack exec deploy/filebrowser -- filebrowser config get | grep -i exec
```

Expected: execution disabled.

- [ ] **Step 6: Verify the editor**

Add the editor to `filebrowser-editors`, sign in as them: only `<scope-path>` visible.

- [ ] **Step 7: Verify the forged header**

```bash
curl -sI -H 'X-authentik-username: <operator-username>' https://filebrowser.kub.home.ashudev.com/ | head -5
```

Expected: `302` to authentik. A `200` is a security failure; stop and fix.

- [ ] **Step 8: Verify revocation**

Remove the editor from `filebrowser-editors`, sign in again: authentik refusal.

---

### Task 9: Optional MFA that applies to source logins

**Files:**
- Create: `cluster/helm/authentik/files/mfa.yaml`
- Modify: `cluster/helm/authentik/Chart.yaml` (`0.4.0` → `0.5.0`)

**Interfaces:**
- Produces: `default-authentication-mfa-validation` restricted to `totp` and `webauthn`; stage bound into `default-source-authentication` at order `-10`.

- [ ] **Step 1: Write the blueprint**

```yaml
version: 1
metadata:
  name: ashulab-mfa
entries:
  # The stage exists and is bound to the password flow at order 30 with
  # not_configured_action=skip, so MFA stays optional. Restrict it to the two
  # methods offered in the UI.
  - model: authentik_stages_authenticator_validate.authenticatorvalidatestage
    identifiers:
      name: default-authentication-mfa-validation
    attrs:
      device_classes:
        - totp
        - webauthn

  # Source logins use default-source-authentication, which has no MFA stage.
  # Order -10 runs before the login stage at 0, so a failed code never creates
  # a session.
  - model: authentik_flows.flowstagebinding
    identifiers:
      target: !Find [authentik_flows.flow, [slug, default-source-authentication]]
      order: -10
    attrs:
      stage: !Find [authentik_stages_authenticator_validate.authenticatorvalidatestage, [name, default-authentication-mfa-validation]]
```

- [ ] **Step 2: Render and assert**

```bash
helm template t cluster/helm/authentik -f cluster/helm/authentik/values.yaml --namespace authentik > /tmp/after.yaml
grep -c "ashulab-mfa" /tmp/after.yaml          # expect 1
grep -A3 "default-source-authentication" /tmp/after.yaml | grep "order" | head
```

- [ ] **Step 3: Commit, sync, check the worker log**

```bash
git add cluster/helm/authentik
git commit -m "🚀 feat(sso): offer optional MFA on password and source logins"
kubectl -n authentik logs deploy/authentik-worker --tail=200 | grep -i "mfa\|error"
```

- [ ] **Step 4: Verify enrolment and challenge**

1. As the operator's own identity (not `akadmin`), **MFA Devices** → enrol TOTP.
2. Sign out, sign in via Discord: the code prompt appears.
3. Enter a wrong code: refused, no usable session.
4. Delete the device, sign in via Discord again: no prompt. If the prompt survives, the stage is not in skip mode; fix before continuing.

- [ ] **Step 5: Verify two WebAuthn devices**

Enrol two YubiKeys as separate devices; either satisfies the prompt.

---

### Task 10: Runbooks, status and design status

**Files:**
- Modify: `docs/runbooks/authentik-apps.md`
- Modify: `docs/status.md`
- Modify: `docs/superpowers/specs/2026-11-14-sso-access-model-design.md`

- [ ] **Step 1: Rewrite "Granting access"**

```markdown
## Granting access

Three levels, decided by group membership:

| Level | Group | Effect |
|---|---|---|
| Admin | `authentik Admins` | Administers authentik; application administrator where the app maps groups |
| User | `homelab-users` | Opens the user-safe applications in their plain user role |
| Guest | `homelab-guests` | Default landing on enrolment; signs in, reaches nothing |

Promote with **Directory → Groups → homelab-users → Add existing user**; demote by removing
them. Enrolment always lands in `homelab-guests`. Applications bind the levels they admit in
their own blueprint, in `any` mode.

Capability groups grant one extra thing: `filebrowser-editors` admits to FileBrowser.
```

- [ ] **Step 2: Replace the ArgoCD section's RBAC block**

```markdown
## 5. Argo CD

Argo CD is a hand-run Helm release, not an Application in this repo. The Dex connector and RBAC
live in `cluster/helm/argocd/values.yaml`; the client secret lives in `argocd-secret` under
`dex.authentik.clientSecret` and must equal `ARGOCD_CLIENT_SECRET` in `authentik-sso`.

Apply with `helm -n argocd upgrade argocd <chart-ref> -f cluster/helm/argocd/values.yaml`. The
local `admin` account stays enabled as break-glass.

RBAC: `g, authentik Admins, role:admin` and `g, homelab-users, role:readonly`.
```

- [ ] **Step 3: Add the FileBrowser section**

```markdown
## FileBrowser (forward auth)

FileBrowser has no OIDC. It authenticates on `X-authentik-username`, which the Traefik
forward-auth middleware sets after the outpost approves the request; a second middleware removes
any client-supplied value first.

After a rebuild these settings live only in FileBrowser's database:

    kubectl -n media-stack exec deploy/filebrowser -- filebrowser users add \
      '<operator-username>' "$(openssl rand -hex 24)" --perm.admin --lockPassword
    kubectl -n media-stack exec deploy/filebrowser -- filebrowser config set \
      --auth.method=proxy --auth.header=X-authentik-username

Usernames must match authentik's `username` exactly. Folder scopes are per account
(`filebrowser users edit --scope`, or the admin UI). Create the account and scope before adding
the person to `filebrowser-editors`: proxy auth auto-creates an account on first login with a
per-user home, which is wider than intended.

Upstream archived FileBrowser on 2026-09-01; never expose it except through this middleware chain.
```

- [ ] **Step 4: Add the MFA section**

```markdown
## MFA

Optional. TOTP and WebAuthn (YubiKey) are offered from **MFA Devices**. The validation stage asks
when a device is enrolled and skips when none is, and it is bound to both
`default-authentication-flow` and `default-source-authentication`; the second is what makes it
apply to Discord and Plex logins.

Recovery: delete the lost device in the admin interface. `akadmin` is deliberately left without a
device as break-glass for authentik itself.
```

- [ ] **Step 5: Update `docs/status.md`**

FileBrowser: forward auth through authentik, `v2.63.23`, per-user scopes, archived upstream.
authentik: level groups, `app-access` retired, optional MFA.

- [ ] **Step 6: Mark the design approved and verify**

Set the design status to `approved and implemented, 2026-11-14.`

```bash
helm template t cluster/helm/authentik -f cluster/helm/authentik/values.yaml --namespace authentik >/dev/null && echo OK
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")
git add docs
git commit -m "📝 docs: record the access model, ArgoCD SSO, FileBrowser and MFA"
```

---

## Final verification

- [ ] `helm template` renders with no error and contains `homelab-users`, `homelab-guests`, `filebrowser-editors`, `forward_single`, `ashulab-mfa`.
- [ ] No binding in `cluster/helm/authentik/files/` names `apps` or `users`; `access-policy.yaml` is gone.
- [ ] ArgoCD reference check from `AGENTS.md` §6 passes.
- [ ] Live: guest sees an empty list; a user is refused by Grafana and pgAdmin (no user-safe app is wired yet); admin is Admin in Grafana, can sign in to pgAdmin, and — once ArgoCD is wired — can Sync there.
- [ ] Live: FileBrowser refuses a forged header, shows the editor only their scope, refuses a removed editor.
- [ ] Live: enrolled TOTP is asked for on a Discord login; deleting the device stops the prompt; two WebAuthn keys both work.
- [ ] Worker log has no blueprint failure after the last sync.
