# FreshRSS native OIDC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FreshRSS signs users in through authentik with its own OIDC flow, replacing the form login.

**Architecture:** Swap the chart's image to the official Debian build (the only one with `mod_auth_openidc`), point `DATA_PATH` at a fresh `/config/data` so the 1100-owned claim root is never the data root, and add an intelligible OAuth2 provider blueprint in authentik. The route keeps `lan-only` and gains no middleware; the app's `/p/api/` stays outside OIDC, so the phone app and the feed sync keep working.

**Tech Stack:** Helm 3 chart (`cluster/helm/news/freshrss`), Traefik CRD IngressRoute, authentik blueprints (`cluster/helm/authentik/files`), ArgoCD.

**Spec:** `docs/superpowers/specs/2026-11-19-freshrss-oidc-design.md`

## Global Constraints

- No secret value in git, in `values.yaml`, or in a blueprint. The client secret is `!Env FRESHRSS_CLIENT_SECRET` (from `authentik-sso`); the app reads both keys from `news/freshrss-oidc` through `extraEnv`.
- Every non-public route keeps `kube-system-lan-only@kubernetescrd`. OIDC is not a substitute for it.
- Image tags are pinned, never floating.
- YAML: 2 spaces, lowercase keys.
- Do not commit to `develop` or `main`; work stays on `feature/freshrss-authentik`.
- Docs state only what the source does; gloss a media/identity term once in plain words if it is needed.

## Review Focus

- **First boot on the old claim.** The claim root is owned by uid 1100 and the container may not be able to chmod it. Expected: `www-data` can create the database. Pinned by Task 1's render check on `DATA_PATH=/config/data`.
- **OIDC callback mismatch.** authentik must see exactly `https://freshrss.kub.home.ashudev.com:443/i/oidc/`. Expected: the browser returns without `redirect_uri` errors. Pinned by Task 2's blueprint grep.
- **API still reachable.** The phone app and `hack/freshrss_sync.py` send an API password and cannot follow a sign-in redirect. Expected: no auth middleware on the route and no `/api` rule. Pinned by Task 1's route check.
- **No admin after the first sign-in.** The wizard's default user must be the exact claim (`vyrtualsynthese`), or the account has no rights. Pinned by Task 2's operator-steps grep in the chart README.
- **Secret in the wrong authentik Secret.** `!Env` reads only what `envFrom` mounts. Expected: `FRESHRSS_CLIENT_SECRET` in `authentik-sso`. Pinned by Task 2's docs grep.

---

### Task 1: Chart runs the official image with OIDC

**Files:**
- Modify: `cluster/helm/news/freshrss/Chart.yaml`
- Modify: `cluster/helm/news/freshrss/values.yaml`
- Modify: `cluster/helm/news/freshrss/templates/deployment.yaml` (comments only)
- Modify: `cluster/helm/news/freshrss/templates/NOTES.txt`
- Modify: `cluster/helm/news/freshrss/README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the values keys `image.repository = freshrss/freshrss`, `image.tag = 1.30.4`, `env.DATA_PATH = /config/data`, `env.OIDC_ENABLED`, `env.OIDC_PROVIDER_METADATA_URL`, `env.OIDC_CLIENT_ID = freshrss`, `env.OIDC_REMOTE_USER_CLAIM = preferred_username`, `env.OIDC_SCOPES`, `env.OIDC_X_FORWARDED_HEADERS`, `extraEnv` with `OIDC_CLIENT_SECRET` and `OIDC_CLIENT_CRYPTO_KEY` from Secret `freshrss-oidc`. Task 2 names the same client id and the same Secret.

- [ ] **Step 1: Write the failing render check**

Run this block and expect the first grep to fail (the image is still LinuxServer):

```bash
cd cluster/helm/news/freshrss
rendered="$(helm template news-freshrss . --namespace news)"
grep -q 'image: "freshrss/freshrss:1.30.4"' <<<"$rendered" || echo "FAIL image"
grep -q 'name: DATA_PATH' <<<"$rendered" || echo "FAIL data path"
grep -q 'name: OIDC_ENABLED' <<<"$rendered" || echo "FAIL oidc"
grep -q 'name: OIDC_CLIENT_SECRET' <<<"$rendered" || echo "FAIL secret"
grep -q 'mountPath: /config$' <<<"$rendered" || echo "FAIL mount"
```

- [ ] **Step 2: Update `Chart.yaml`**

`version: 1.1.0`, `appVersion: "1.30.4"`, and the description stays. Nothing else changes.

- [ ] **Step 3: Update `values.yaml`**

Image block: `repository: freshrss/freshrss`, `tag: 1.30.4`, and a comment naming the Debian x86_64 image as the one with `mod_auth_openidc`.

`env` block, replacing PUID/PGID:

```yaml
env:
  TZ: "Europe/Paris"
  # FreshRSS's supported writable root. Left at a subdirectory of the claim on
  # purpose: the claim root was chowned to uid 1100 by the image this chart used
  # to run, and the container can change its group but not its mode (no FOWNER),
  # so a data root there could not be written by the Apache worker.
  DATA_PATH: "/config/data"
  # The official image refreshes feeds from its own cron; the LinuxServer image
  # did this for itself.
  CRON_MIN: "1,31"
  OIDC_ENABLED: "1"
  OIDC_PROVIDER_METADATA_URL: "https://authentik.kub.home.ashudev.com/application/o/freshrss/.well-known/openid-configuration"
  OIDC_CLIENT_ID: "freshrss"
  OIDC_REMOTE_USER_CLAIM: "preferred_username"
  OIDC_SCOPES: "openid profile email"
  # Traefik terminates TLS; mod_auth_openidc builds the callback from these.
  OIDC_X_FORWARDED_HEADERS: "X-Forwarded-Host X-Forwarded-Port X-Forwarded-Proto"
```

`extraEnv` becomes the two Secret references (the chart's template concatenates it into `env`):

```yaml
extraEnv:
  - name: OIDC_CLIENT_SECRET
    valueFrom:
      secretKeyRef:
        name: freshrss-oidc
        key: client-secret
  - name: OIDC_CLIENT_CRYPTO_KEY
    valueFrom:
      secretKeyRef:
        name: freshrss-oidc
        key: client-crypto-key
```

Update the `securityContext` comment: the capabilities remain because the entrypoint runs as root and chowns the data into the `www-data` group; drop the LinuxServer nginx/php-fpm explanation.

Update the acceptance rationale: keep `/i/` for readiness, and say that with OIDC it answers `302` to authentik (a 3xx counts as ready) and no longer reads the user table.

`storage.mountPath` stays `/config`; the storage comment gains the `DATA_PATH` sentence.

- [ ] **Step 4: Update `templates/deployment.yaml`**

Only comments: the readiness comment currently says `/i/` "runs any pending migration and queries the user table"; replace with the 3xx sentence. No template logic changes.

- [ ] **Step 5: Update `templates/NOTES.txt`**

Say the app signs in through authentik, and that the setup wizard is reached after that sign-in.

- [ ] **Step 6: Rewrite the README's changed sections**

- Upstream: image link becomes `https://hub.docker.com/r/freshrss/freshrss`.
- The values table rows for image, env, storage.
- Replace the DAC_OVERRIDE section with the entrypoint/`www-data` reason, and add the `DATA_PATH` paragraph.
- Add a "First start after the swap" section with the operator steps: create `news/freshrss-oidc` and patch `authentik-sso` (Task 2 names the keys), sign in, set the default user `vyrtualsynthese` and HTTP authentication, set an API password, update `~/.config/secrets/homelab.env`, replay `hack/freshrss/feeds.opml`.
- Note that the old LinuxServer tree stays at `/config/www` and is not read.

- [ ] **Step 7: Run the render check from Step 1**

Expected: no `FAIL` line.

- [ ] **Step 8: Run the chart and exposure checks**

```bash
helm template news-freshrss cluster/helm/news/freshrss --namespace news >/dev/null && echo "render OK"
./hack/check-exposure.sh --only news
```

Expected: `render OK`, then `result: OK` with 2 routes.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/news/freshrss
git commit -m "🚀 feat(freshrss): run the official image with native OIDC"
```

### Task 2: authentik provider and the written record

**Files:**
- Create: `cluster/helm/authentik/files/app-freshrss.yaml`
- Modify: `docs/secrets.md`
- Modify: `docs/reference/routes.md`
- Modify: `docs/reference/sso-applications.md`
- Modify: `docs/reference/chart-notes.md`
- Modify: `docs/charts/README.md`
- Modify: `docs/news-feeds.md`

**Interfaces:**
- Consumes: Task 1's `OIDC_CLIENT_ID = freshrss`, `OIDC_PROVIDER_METADATA_URL` slug `freshrss`, Secret `news/freshrss-oidc` keys `client-secret` and `client-crypto-key`.
- Produces: the authentik provider `freshrss`, application slug `freshrss`, and the docs that name `authentik-sso` key `FRESHRSS_CLIENT_SECRET`.

- [ ] **Step 1: Write the failing content check**

```bash
grep -q 'name: freshrss$' cluster/helm/authentik/files/app-freshrss.yaml || echo "FAIL blueprint"
grep -q 'https://freshrss.kub.home.ashudev.com:443/i/oidc/' cluster/helm/authentik/files/app-freshrss.yaml || echo "FAIL redirect"
grep -q '!Env FRESHRSS_CLIENT_SECRET' cluster/helm/authentik/files/app-freshrss.yaml || echo "FAIL secret env"
grep -q 'FRESHRSS_CLIENT_SECRET' docs/secrets.md || echo "FAIL secrets doc"
grep -q 'OIDC in the app' docs/reference/routes.md || echo "FAIL routes doc"
```

Expected: every line prints `FAIL` before the work.

- [ ] **Step 2: Create the blueprint**

Copy the shape of `cluster/helm/authentik/files/app-radarr.yaml`, with these values: provider `freshrss` (no prior proxy provider exists, so the `-oidc` suffix the Radarr file needs is not needed), client id `freshrss`, secret `!Env FRESHRSS_CLIENT_SECRET`, redirect `https://freshrss.kub.home.ashudev.com:443/i/oidc/`, application name `FreshRSS`, slug `freshrss`, launch URL `https://freshrss.kub.home.ashudev.com/`, icon `https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/freshrss.png`, policy binding `authentik Admins`. Add a comment: FreshRSS protects `/i/` only, so the API keeps its own login and the route needs no `/api` rule.

- [ ] **Step 3: Update `docs/secrets.md`**

Add `freshrss-oidc | news | client-secret → OIDC_CLIENT_SECRET, client-crypto-key → OIDC_CLIENT_CRYPTO_KEY — the authentik provider's client secret and the module's encryption key` to the application table, and name `FRESHRSS_CLIENT_SECRET` in the `authentik-sso` row (or the sentence that follows it) as one key per app.

- [ ] **Step 4: Update `docs/reference/routes.md`**

The FreshRSS row's Gate becomes `` `lan-only`; OIDC in the app, `/api` on its own login ``.

- [ ] **Step 5: Update `docs/reference/sso-applications.md`**

Add a matrix row: `| FreshRSS | OIDC | configured 2026-11-19 — its own OIDC through the official image; gate absent, `/api` untouched | freshrss.kub.home.ashudev.com |`. Add a short `### FreshRSS: native OIDC needs the official image` subsection after the Radarr recipe: the LinuxServer image has no `mod_auth_openidc`, `DATA_PATH` points at `/config/data`, the callback is `/i/oidc/`, and only `/i/` is behind the module.

- [ ] **Step 6: Update `docs/reference/chart-notes.md` and `docs/charts/README.md`**

chart-notes: rewrite the freshrss section's hardening and storage paragraphs for the official image, `DATA_PATH`, and the 3xx readiness.

charts/README row: image starts as root then runs Apache as `www-data`; same four capabilities for the entrypoint's chown; readiness `/i/` now answers 302; config claim 1Gi.

- [ ] **Step 7: Update `docs/news-feeds.md`**

The log path at line 232 becomes `/config/data/users/<user>/log.txt`.

- [ ] **Step 8: Run the content check from Step 1**

Expected: no `FAIL` line.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/authentik/files/app-freshrss.yaml docs
git commit -m "🚀 feat(authentik): add the FreshRSS OIDC provider"
```

### Task 3: Whole-tree verification and handover

**Files:** none new.

**Interfaces:**
- Consumes: both earlier tasks.
- Produces: the pushed branch and the pull request.

- [ ] **Step 1: Rebase on the integration branch**

```bash
git fetch origin
git rebase origin/develop
```

Expected: no conflict; the two commits on top.

- [ ] **Step 2: Render every chart that can render**

```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```

Expected: no `FAIL` line.

- [ ] **Step 3: Check ArgoCD references and exposure**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")
./hack/check-exposure.sh
```

Expected: no `BROKEN` line, then `result: OK`.

- [ ] **Step 4: Prove no secret value is in the tree**

```bash
git grep -nE 'OIDC_CLIENT_SECRET: *"?[A-Za-z0-9]{16,}|client-secret: *[A-Za-z0-9]{16,}' -- cluster docs || echo "no literal secret"
```

Expected: `no literal secret`.

- [ ] **Step 5: Push and open the pull request**

```bash
git push origin feature/freshrss-authentik
```

Open the PR to `develop` with `github_create_pull_request`. The body carries the operator steps from Task 1 (Secrets, first sign-in, API password, feed replay) and the verification output above.

- [ ] **Step 6: Move the board card to Validation**

Update task `#411`'s description with the branch, the PR link and the verification output, then move it to the `Validation` column. ArgoCD is not token-authenticated, so the sync is the merge plus the poll; say so in the report.
