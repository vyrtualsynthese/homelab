# FileBrowser Quantum behind authentik — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the archived FileBrowser with FileBrowser Quantum behind authentik OIDC, with no password form, an editor scoped to the creator folder, and public single-file share links.

**Architecture:** The existing wrapper chart in `cluster/helm/filebrowser` gains the community `filebrowser-quantum` chart as an OCI dependency and keeps its three templates (IngressRoute, TLSOption, NFS PV+claim). The app's `config.yaml` is rendered from the wrapper's values. Authentik holds one OAuth2 provider; the route becomes public and the app's own OIDC session is the only gate.

**Tech Stack:** Helm 4 (OCI dependencies), ArgoCD, Traefik v2 CRDs, authentik blueprints and its REST API, K3s.

**Spec:** [`docs/superpowers/specs/2026-11-19-filebrowser-quantum-design.md`](../specs/2026-11-19-filebrowser-quantum-design.md)

## Global Constraints

- Traefik v2 CRDs only (`traefik.containo.us/v1alpha1`); no `networking.k8s.io/v1` Ingress. The upstream chart's `ingress.enabled` stays `false`.
- Chart version and image tag are both `2.0.2-stable`. No `latest`, no `2.1.x`.
- Every route not on the public allowlist carries `kube-system-lan-only@kubernetescrd`; this route is on it, so it must carry no gate at all.
- Secrets are never committed. The app's client secret is `CLIENTSECRET` in a Secret; authentik's copy is `FILEBROWSER_CLIENT_SECRET` in `authentik-sso`.
- The app runs as uid 1100 (`runAsUser`, `fsGroup`), `runAsNonRoot: true`.
- Files under `cluster/argocd/` use CRLF; the files this plan touches under `cluster/helm/` use LF.
- `hack/check-exposure.sh` and `hack/check_docs_links.py` must pass before the PR.

## Review Focus

- **A share link opened with no cookie must return the file.** OIDC-only login must not capture `/share/...`; if it does, the fan use case is dead while every authenticated test still passes.
- **A brand-new non-admin OIDC user must see zero sources.** If `defaultEnabled` is lost in a later values edit, the editor silently inherits every NAS export.
- **The public host must stay ungated.** Re-adding `lan-only` "for safety" makes the site unreachable from the internet while `helm template` still looks fine.
- **A missing creator export must stop the pod, not show an empty folder.** A silent empty source is worse than a failed mount.
- **Writes must succeed as uid 1100.** Reads succeed under almost any uid; the first upload is the real test.

---

### Task 1: Swap the chart and render the workload

**Files:**
- Modify: `cluster/helm/filebrowser/Chart.yaml`
- Modify: `cluster/helm/filebrowser/values.yaml`
- Modify: `.gitignore`

**Interfaces:**
- Produces: Service `filebrowser-filebrowser-quantum` on port 8080; Deployment `filebrowser-filebrowser-quantum`; ConfigMap `filebrowser-filebrowser-quantum` holding `config.yaml`; Secrets consumed by name `filebrowser-oidc`.
- Produces: values keys `filebrowser-quantum.*` (subchart) and `traefik.service` / `traefik.port` used by `templates/ingressroute.yaml`.

- [ ] **Step 1: Write the failing assertions**

Run, expecting failures because the chart still renders the archived app:

```bash
cd cluster/helm/filebrowser && helm dependency build >/dev/null
helm template filebrowser . -f values.yaml > /tmp/fb.yaml
grep -q 'image: "ghcr.io/gtsteffaniak/filebrowser:2.0.2-stable"' /tmp/fb.yaml && echo IMAGE-OK || echo IMAGE-FAIL
grep -q 'name: filebrowser-filebrowser-quantum$' /tmp/fb.yaml && echo NAME-OK || echo NAME-FAIL
grep -q 'port: 8080' /tmp/fb.yaml && echo PORT-OK || echo PORT-FAIL
```

Expected: `IMAGE-FAIL`, `NAME-FAIL`, `PORT-FAIL`.

- [ ] **Step 2: Add the dependency**

`cluster/helm/filebrowser/Chart.yaml` becomes:

```yaml
apiVersion: v2
name: filebrowser
description: FileBrowser Quantum - web file browser over the NAS exports, OIDC only
type: application
version: 0.2.0
appVersion: "2.0.2-stable"
dependencies:
  - name: filebrowser-quantum
    version: 2.0.2-stable
    repository: oci://ghcr.io/softwaredam/helm-charts
```

- [ ] **Step 3: Ignore the built dependency**

Append to `.gitignore`, after the `nfs/provisioner` block:

```
# Built by ArgoCD at sync time
cluster/helm/filebrowser/charts/
cluster/helm/filebrowser/Chart.lock
```

- [ ] **Step 4: Rewrite the values**

`cluster/helm/filebrowser/values.yaml` — keep the `nfsServer`, `nfsMountOptions` and `nfsVolumes` block exactly as it is today, then replace everything from `filebrowser:` down with:

```yaml
filebrowser-quantum:
  image:
    repository: ghcr.io/gtsteffaniak/filebrowser
    # renovate: datasource=docker depName=gtsteffaniak/filebrowser
    tag: 2.0.2-stable
    pullPolicy: IfNotPresent

  # PUID/PGID of the rest of the stack; the NAS exports are written by it.
  securityContext:
    runAsUser: 1100
    runAsNonRoot: true
    fsGroup: 1100

  resources:
    requests:
      cpu: 1m
      memory: 64Mi
    limits:
      memory: 256Mi

  persistence:
    enabled: true
    keep: true
    storageClass: local-path
    accessMode: ReadWriteOnce
    data:
      size: 1Gi
    config:
      size: 1Gi

  extraVolumes:
    - name: stash-volume
      persistentVolumeClaim:
        claimName: filebrowser-stash
    - name: audio-volume
      persistentVolumeClaim:
        claimName: filebrowser-audio
    - name: qbittorrent-download-volume
      persistentVolumeClaim:
        claimName: filebrowser-download

  extraVolumeMounts:
    - name: stash-volume
      mountPath: /srv/various
    - name: audio-volume
      mountPath: /srv/audio
    - name: qbittorrent-download-volume
      mountPath: /srv/torrent

  # The client id is public; the secret is the only value from a Secret.
  extraEnv:
    - name: FILEBROWSER_OIDC_CLIENT_ID
      value: filebrowser
    - name: FILEBROWSER_OIDC_CLIENT_SECRET
      valueFrom:
        secretKeyRef:
          name: filebrowser-oidc
          key: CLIENTSECRET

  config:
    server:
      sources:
        - path: /srv/various
          name: various
          config:
            defaultEnabled: false
        - path: /srv/audio
          name: audio
          config:
            defaultEnabled: false
        - path: /srv/torrent
          name: torrent
          config:
            defaultEnabled: false
    auth:
      methods:
        # No password form: OIDC is the only way in, which is also what makes
        # the app redirect straight to authentik.
        password:
          enabled: false
          signup: false
        oidc:
          enabled: true
          issuerUrl: https://authentik.kub.home.ashudev.com/application/o/filebrowser/
          clientId: filebrowser
          scopes: openid email profile
          userIdentifier: preferred_username
          adminGroup: authentik Admins
          userGroups:
            - authentik Admins
            - filebrowser-editors
    http:
      port: 8080
      trustProxyHeaders: true
      externalUrl: https://filebrowser.kub.home.ashudev.com

traefik:
  apiVersion: traefik.containo.us/v1alpha1
  host: filebrowser.kub.home.ashudev.com
  certResolver: myresolver
  tlsOption: filebrowser-tlsoption
  service: filebrowser-filebrowser-quantum
  port: 8080
  middlewares:
    - namespace: kube-system
      name: lan-only
    - namespace: kube-system
      name: security
```

`lan-only` stays for this task; Task 3 removes it.

- [ ] **Step 5: Render and assert**

```bash
cd cluster/helm/filebrowser && helm dependency build && helm template filebrowser . -f values.yaml > /tmp/fb.yaml
grep -q 'image: "ghcr.io/gtsteffaniak/filebrowser:2.0.2-stable"' /tmp/fb.yaml && echo IMAGE-OK
grep -q 'name: filebrowser-filebrowser-quantum$' /tmp/fb.yaml && echo NAME-OK
grep -q 'port: 8080' /tmp/fb.yaml && echo PORT-OK
grep -q 'kind: Ingress$' /tmp/fb.yaml && echo INGRESS-BAD || echo NO-CORE-INGRESS
grep -c 'defaultEnabled: false' /tmp/fb.yaml
grep -q 'runAsUser: 1100' /tmp/fb.yaml && echo UID-OK
```

Expected: `IMAGE-OK`, `NAME-OK`, `PORT-OK`, `NO-CORE-INGRESS`, `3` from the count, `UID-OK`. The ConfigMap holds the three sources and the OIDC block; inspect it once with `grep -A40 'config.yaml' /tmp/fb.yaml` to confirm the YAML is nested as written.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/filebrowser/Chart.yaml cluster/helm/filebrowser/values.yaml .gitignore
git commit -m "🚀 feat(filebrowser): run FileBrowser Quantum with OIDC only"
```

---

### Task 2: Add the creator export as a fourth source

**Files:**
- Modify: `cluster/helm/filebrowser/values.yaml`

**Interfaces:**
- Produces: PV `pv-filebrowser-creator`, claim `filebrowser-creator`, mount `/srv/creator`, source name `creator`.

- [ ] **Step 1: Write the failing assertion**

```bash
helm template filebrowser cluster/helm/filebrowser -f cluster/helm/filebrowser/values.yaml > /tmp/fb.yaml
grep -q 'name: pv-filebrowser-creator' /tmp/fb.yaml && echo PV-OK || echo PV-FAIL
grep -q 'path: /mnt/storage/nfs/creator' /tmp/fb.yaml && echo PATH-OK || echo PATH-FAIL
```

Expected: `PV-FAIL`, `PATH-FAIL`.

- [ ] **Step 2: Add the volume and the source**

To `nfsVolumes:` append:

```yaml
  - claim: filebrowser-creator
    pv: pv-filebrowser-creator
    path: /mnt/storage/nfs/creator
    accessMode: ReadWriteMany
```

To `filebrowser-quantum.extraVolumes` append:

```yaml
    - name: creator-volume
      persistentVolumeClaim:
        claimName: filebrowser-creator
```

To `extraVolumeMounts` append:

```yaml
    - name: creator-volume
      mountPath: /srv/creator
```

To `config.server.sources` append:

```yaml
        - path: /srv/creator
          name: creator
          config:
            defaultEnabled: false
```

- [ ] **Step 3: Render and assert**

```bash
helm template filebrowser cluster/helm/filebrowser -f cluster/helm/filebrowser/values.yaml > /tmp/fb.yaml
grep -q 'name: pv-filebrowser-creator' /tmp/fb.yaml && echo PV-OK
grep -q 'path: /mnt/storage/nfs/creator' /tmp/fb.yaml && echo EXPORT-OK
grep -q 'mountPath: /srv/creator' /tmp/fb.yaml && echo MOUNT-OK
grep -c 'kind: PersistentVolume$' /tmp/fb.yaml
```

Expected: `PV-OK`, `EXPORT-OK`, `MOUNT-OK`, and `4`.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/filebrowser/values.yaml
git commit -m "🚀 feat(filebrowser): mount the creator export"
```

---

### Task 3: Make the route public and allowlist it

**Files:**
- Modify: `cluster/helm/filebrowser/values.yaml` (`traefik.middlewares`)
- Modify: `hack/check_exposure.py` (`PUBLIC_HOSTS`)
- Modify: `AGENTS.md` (§4 public list)

**Interfaces:**
- Produces: an ungated IngressRoute for `filebrowser.kub.home.ashudev.com`; a `PUBLIC_HOSTS` entry the checker and `docs/reference/routes.md` both read.

- [ ] **Step 1: Write the failing assertion**

```bash
helm template filebrowser cluster/helm/filebrowser -f cluster/helm/filebrowser/values.yaml \
  | grep -q 'kube-system-lan-only@kubernetescrd' && echo STILL-GATED || echo ALREADY-PUBLIC
```

Expected: `STILL-GATED`.

- [ ] **Step 2: Drop `lan-only` from the route**

In `cluster/helm/filebrowser/values.yaml`, the `traefik.middlewares` list becomes only the `security` entry. Add above it:

```yaml
  # Public on purpose: the editor signs in from outside, and share links go to
  # people with no account at all. The gate is the app's own authentik OIDC
  # session; `lan-only` here would make both unreachable.
```

- [ ] **Step 3: Allowlist the host**

In `hack/check_exposure.py`, add to `PUBLIC_HOSTS`:

```python
        # FileBrowser: the app signs in through authentik, and the normal share
        # links it mints are opened by people with no account.
        "filebrowser.kub.home.ashudev.com",
```

- [ ] **Step 4: Run the checker**

```bash
hack/check-exposure.sh
```

Expected: exit 0, no violation for `filebrowser`. Then prove the checker actually sees the route:

```bash
hack/check-exposure.sh --only filebrowser
```

Expected: exit 0 with `filebrowser` in the route count.

- [ ] **Step 5: Update the prose list**

In `AGENTS.md` §4, extend the public list with `filebrowser.kub.home.ashudev.com` and keep the sentence's shape (it names why each public host is public). `docs/reference/routes.md` is updated in Task 6, together with the live values.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/filebrowser/values.yaml hack/check_exposure.py AGENTS.md
git commit -m "🚀 feat(gate): serve FileBrowser publicly, gated by OIDC"
```

---

### Task 4: Replace the forward-auth provider with an OAuth2 provider

**Files:**
- Modify: `cluster/helm/authentik/files/app-filebrowser.yaml`

**Interfaces:**
- Produces: OAuth2 provider name `filebrowser-oidc`, client id `filebrowser`, issuer `https://authentik.kub.home.ashudev.com/application/o/filebrowser/`, redirect `https://filebrowser.kub.home.ashudev.com/api/auth/oidc/callback`.
- Consumes: `FILEBROWSER_CLIENT_SECRET` from the `authentik-sso` Secret (Task 7 creates it).

- [ ] **Step 1: Write the failing assertion**

```bash
grep -q 'authentik_providers_oauth2.oauth2provider' cluster/helm/authentik/files/app-filebrowser.yaml && echo OAUTH2-OK || echo OAUTH2-MISSING
```

Expected: `OAUTH2-MISSING`.

- [ ] **Step 2: Replace the provider entry**

Replace the `authentik_providers_proxy.proxyprovider` entry (leave the group, application and two policy bindings in place) with:

```yaml
  # Native OIDC: the app holds its own session, keeps share links anonymous, and
  # knows which person is browsing. A forward-auth gate would answer the sign-in
  # wall to share links and hand every browser the same identity.
  #
  # Blueprint-created providers start with no grant types (see app-calibre.yaml),
  # so both are named explicitly.
  - model: authentik_providers_oauth2.oauth2provider
    id: filebrowser-provider
    identifiers:
      name: filebrowser-oidc
    attrs:
      name: filebrowser-oidc
      client_type: confidential
      grant_types:
        - authorization_code
        - refresh_token
      client_id: filebrowser
      # Read from the authentik-sso Secret at apply time. Changing it needs a
      # worker restart, because !Env is resolved when the blueprint is applied.
      client_secret: !Env FILEBROWSER_CLIENT_SECRET
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]
      signing_key: !Find [authentik_crypto.certificatekeypair, [name, authentik Self-signed Certificate]]
      redirect_uris:
        - matching_mode: strict
          redirect_uri_type: authorization
          url: https://filebrowser.kub.home.ashudev.com/api/auth/oidc/callback
      property_mappings:
        - !Find [authentik_providers_oauth2.scopemapping, [managed, goauthentik.io/providers/oauth2/scope-openid]]
        - !Find [authentik_providers_oauth2.scopemapping, [managed, goauthentik.io/providers/oauth2/scope-email]]
        - !Find [authentik_providers_oauth2.scopemapping, [managed, goauthentik.io/providers/oauth2/scope-profile]]
```

The application entry keeps `provider: !KeyOf filebrowser-provider`, so only the provider changes.

- [ ] **Step 3: Check the file parses and the entries match the pattern**

```bash
python3 -c "import yaml,sys; d=yaml.safe_load(open('cluster/helm/authentik/files/app-filebrowser.yaml')); print(len(d['entries']))"
grep -q 'authentik_providers_oauth2.oauth2provider' cluster/helm/authentik/files/app-filebrowser.yaml && echo OAUTH2-OK
grep -q 'authentik_providers_proxy' cluster/helm/authentik/files/app-filebrowser.yaml && echo PROXY-STILL-THERE || echo PROXY-GONE
```

Expected: a count of 6, `OAUTH2-OK`, `PROXY-GONE`.

Note: `!Find`/`!Env` are authentik tags, so a plain YAML parser sees them as unknown tags; if `yaml.safe_load` refuses, use `python3 -c "import yaml; yaml.load(open(...), Loader=yaml.BaseLoader)"` instead and assert the entry count.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/authentik/files/app-filebrowser.yaml
git commit -m "🚀 feat(authentik): give FileBrowser a native OIDC provider"
```

---

### Task 5: Create the editor's authentik account

**Files:**
- None in git. The account lives in authentik; the creation is recorded in the runbook by Task 6.

**Interfaces:**
- Consumes: `AUTHENTIK_TOKEN` from `~/.config/secrets/homelab.env`; the `filebrowser-editors` and `homelab-guests` groups from the blueprint.
- Produces: authentik user `editor` (or the operator's chosen username), member of both groups, with a password the operator hands over.

**Runs after Task 7 Step 3**, because the groups only exist once the updated blueprint has been applied. Steps 1–3 of Task 7 come first.

- [ ] **Step 1: Read the group ids**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -sS -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
  'https://authentik.kub.home.ashudev.com/api/v3/core/groups/?name=filebrowser-editors' \
  | jq -r '.results[0].pk'
curl -sS -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
  'https://authentik.kub.home.ashudev.com/api/v3/core/groups/?name=homelab-guests' \
  | jq -r '.results[0].pk'
```

Expected: two numeric ids. If either is empty, the blueprint has not been applied — stop and finish Task 7's sync first.

- [ ] **Step 2: Create the user**

```bash
G_EDITORS=<id from step 1>; G_GUESTS=<id from step 1>
curl -sS -X POST -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
  -H 'Content-Type: application/json' \
  'https://authentik.kub.home.ashudev.com/api/v3/core/users/' \
  -d "{\"username\":\"editor\",\"name\":\"Editor\",\"email\":\"\",\"is_active\":true,\"groups\":[$G_EDITORS,$G_GUESTS]}" \
  | jq -r '.pk'
```

Expected: a numeric user id. A `400` naming `username` means it already exists; look it up with `?username=editor` and use its id.

- [ ] **Step 3: Set the password**

```bash
U=<id from step 2>
PW=$(openssl rand -base64 18)
curl -sS -X POST -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
  -H 'Content-Type: application/json' \
  "https://authentik.kub.home.ashudev.com/api/v3/core/users/$U/set_password/" \
  -d "{\"password\":\"$PW\"}" -o /dev/null -w '%{http_code}\n'
printf 'editor password: %s\n' "$PW"
```

Expected: `204`, then the password on stdout. It goes to the operator's password manager, never into git.

- [ ] **Step 4: Verify membership**

```bash
curl -sS -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
  "https://authentik.kub.home.ashudev.com/api/v3/core/users/?username=editor" \
  | jq -r '.results[0].groups_obj[].name'
```

Expected: `filebrowser-editors` and `homelab-guests`.

No commit: nothing in git changed.

---

### Task 6: Documentation

**Files:**
- Create: `cluster/helm/filebrowser/README.md`
- Create: `CHANGELOG.md`
- Modify: `docs/reference/sso-applications.md`
- Modify: `docs/runbooks/authentik-apps.md` (FileBrowser section)
- Modify: `docs/how-it-works/applications.md`
- Modify: `docs/status.md`
- Modify: `docs/reference/routes.md`
- Modify: `mkdocs.yml` (only if a new page is added to `docs/`; the chart README and `CHANGELOG.md` are outside it)

**Interfaces:**
- Consumes: the facts in the spec; the account created in Task 5.
- Produces: the runbook section a later session reads instead of rediscovering the traps.

- [ ] **Step 1: Write the chart README**

`cluster/helm/filebrowser/README.md`, covering: the archived upstream and why the fork (`gtsteffaniak/filebrowser`, chart `Softwaredam/filebrowser-chart`); the four sources and the NAS export they need; OIDC-only login and the recovery (re-enable `password.enabled`, sync, read the chart's Secret); the public route and its reason; the editor's manual scope.

- [ ] **Step 2: Create `CHANGELOG.md`**

```markdown
# Changelog

## [Unreleased]

### Changed

- FileBrowser moves to FileBrowser Quantum, signs in through authentik only, and answers the
  internet so the editor can sign in and share links can be opened.
```

- [ ] **Step 3: Update the SSO page**

In `docs/reference/sso-applications.md`, the FileBrowser row moves to native OIDC with the editor's account and one-folder grant; the intro table's "applications actually wired" prose in `docs/status.md` gains FileBrowser.

- [ ] **Step 4: Rewrite the FileBrowser runbook section**

`docs/runbooks/authentik-apps.md`, section "FileBrowser": both Secrets (`filebrowser-oidc`/`CLIENTSECRET`, `authentik-sso`/`FILEBROWSER_CLIENT_SECRET`), the callback URI, the worker reload, the editor account and scope, and the recovery procedure when authentik is down.

- [ ] **Step 5: Update routes, applications and status**

`docs/reference/routes.md`: FileBrowser moves to the Public table, gate `security` only, count rises by one (the page states "five are public" — update it). `docs/how-it-works/applications.md`: the row keeps namespace `filebrowser` and names Quantum. `docs/status.md`: the `filebrowser` row.

- [ ] **Step 6: Check the links**

```bash
python3 hack/check_docs_links.py
```

Expected: exit 0. If it has flags, run it with `--help` first; it is the only link checker in the repo.

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/filebrowser/README.md CHANGELOG.md docs/reference/sso-applications.md \
  docs/runbooks/authentik-apps.md docs/how-it-works/applications.md docs/status.md \
  docs/reference/routes.md
git commit -m "📝 docs: record FileBrowser Quantum, its login and its recovery"
```

---

### Task 7: Cut over, verify end to end, clean up

**Files:** none; this task is operator actions plus read-only verification.

**Interfaces:**
- Consumes: everything above.
- Produces: the running state the design's §8 describes; cards #227 and #285 moved to `Validation`.

- [ ] **Step 1: Create the NAS export (operator)**

On `nas`, create `/mnt/storage/nfs/creator` and export it to the cluster the same way the other exports are. Verify from any node:

```bash
showmount -e 192.168.1.41 | grep creator
```

Expected: the path listed. Without it the pod cannot mount and will not start — that is the intended failure.

- [ ] **Step 2: Create both secrets (operator)**

```bash
S=$(openssl rand -hex 24)
kubectl -n authentik patch secret authentik-sso --type merge \
  -p "$(jq -n --arg s "$S" '{stringData:{"FILEBROWSER_CLIENT_SECRET":$s}}')"
kubectl -n filebrowser create secret generic filebrowser-oidc \
  --from-literal=CLIENTSECRET="$S" --dry-run=client -o yaml | kubectl apply -f -
```

Expected: the patch merges, the Secret is created. Never echo `$S` into a tracked file.

- [ ] **Step 3: Sync authentik, then filebrowser (operator)**

Sync `authentik` from the Argo CD UI first (its PostSync hook recreates the worker, which applies the new blueprint with the new `!Env`). The worker log must show `ashulab-app-filebrowser` applied and no rollback.

Then sync `filebrowser` **with Prune enabled**, so the old Deployment, Service, Secret and the two old claims are removed. AGENTS.md §7.1 explains why automation is toggled in the UI and never committed.

Finally delete the orphaned provider (operator, in the UI): the blueprint entry kept its `id` but its identifiers changed from `filebrowser` (proxy) to `filebrowser-oidc` (OAuth2), so the apply creates the new provider and leaves the old one behind. Remove it in **Directory → Federation & Providers**, with its outpost assignment. Nothing reads it, but it is a second way in that outlives its reason.

- [ ] **Step 4: Verify the workload (read-only)**

```bash
kubectl -n filebrowser get pods
kubectl -n filebrowser get pvc
```

Expected: `filebrowser-filebrowser-quantum-*` 1/1 with no restarts; four bound `filebrowser-*` media claims plus the two local-path claims.

- [ ] **Step 5: Verify the login and the redirect**

```bash
curl -sI https://filebrowser.kub.home.ashudev.com/ | head -5
```

Expected: a `302` to `https://authentik.kub.home.ashudev.com/application/o/authorize/...`, not a `200` with a password form. In a browser, the redirect is automatic.

- [ ] **Step 6: Verify both accounts and sharing (browser)**

- Operator signs in with authentik → administrator → grant own account the four sources in User management → all four visible.
- Editor signs in with the Task 5 password → `creator` only → upload one file and download it → delete is absent.
- Create a normal share for one file, open the link from a phone on mobile data with no session → the file downloads.
- Restart the pod and repeat the editor check, to prove state lives on the config claim.

- [ ] **Step 7: Clean up the leftovers (operator)**

```bash
kubectl -n media-stack delete pod -l app.kubernetes.io/name=filebrowser --ignore-not-found
kubectl -n filebrowser get pvc | grep -E 'filebrowser-(config|database)' || true
kubectl -n filebrowser delete pvc filebrowser-config filebrowser-database --ignore-not-found
kubectl -n filebrowser delete secret filebrowser --ignore-not-found
```

Expected: no `Terminating` pod in `media-stack` (card #285), and no old claims or `FB_*` Secret in `filebrowser`.

- [ ] **Step 8: File the follow-up card**

Create a card in the `homelab` project's `Backlog` for the gap the design accepted: the community chart has no `nodeSelector`/`affinity`/`tolerations`, so the pod is not pinned to a node. Title it `FileBrowser Quantum: pin the pod to a node`, and in the body name the three options (upstream PR, fork of the chart, local chart).

- [ ] **Step 9: Hand the branch over**

```bash
git fetch origin && git rebase origin/develop
hack/check-exposure.sh && python3 hack/check_docs_links.py
git push origin feature/filebrowser-quantum
```

Then open the PR against `develop`, and move #227 and #285 to `Validation` with the branch, the PR link and the verification output.
