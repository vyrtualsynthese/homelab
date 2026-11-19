# Dashy on a public address Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve Dashy at `https://dashy.ashudev.com` behind the authentik gate, and turn `dashy.kub.local` and `kub.home.ashudev.com` into redirects to it.

**Architecture:** Six Traefik `IngressRoute`s in `cluster/helm/dashy` replace the current four: a public TLS router with forward-auth, an HTTP mirror that only redirects, and two routers per retired hostname that redirect through one `redirect-to-dashy` regex. The authentik proxy provider follows the new host, and authentik's own route drops `lan-only` so the sign-in page answers the same internet the dashboard does.

**Tech Stack:** Helm (chart wrapper over `selfhosted-helmcharts/dashy` 1.0.0), Traefik v2 CRDs (`traefik.containo.us/v1alpha1`), authentik blueprints, `hack/check_exposure.py`.

**Spec:** `docs/superpowers/specs/2026-11-19-dashy-public-design.md`

## Global Constraints

- Traefik v2 CRDs only, `traefik.containo.us/v1alpha1`; never `networking.k8s.io/v1`.
- `hack/check_exposure.py`: a host on `PUBLIC_HOSTS` must **not** carry `kube-system-lan-only@kubernetescrd`, and every other host must.
- Public hostnames use `certResolver: myresolver`; `*.kub.local` uses the cluster default certificate plus `tls.options: {name: internal-tls, namespace: kube-system}` and no `certResolver`.
- Chart files are LF. `cluster/argocd/**` is CRLF; this plan does not touch it.
- Authentik's blueprint is rendered from `cluster/helm/authentik/files/*.yaml` into a ConfigMap by `templates/blueprints-configmap.yaml`.
- Commit prefixes: `🚀` feature, `🐛` fix, `📝` docs.

## Review Focus

- **A request from outside the LAN** must reach the authentik sign-in page and then the dashboard. If it reaches a 403, the `lan-only` removal on the authentik router is missing.
- **The sign-in loop.** The provider's `external_host`, the router's host and the prefix in `AUTHENTIK_WEB__BASE_URL` decide the outpost redirect; if any of them disagrees, the browser bounces between two 302s.
- **Path preservation on the redirects.** `https://dashy.kub.local/some/path` must land on `https://dashy.ashudev.com/some/path`, single hop, `301`.
- **The exposure checker's two directions.** A public host that gains `lan-only` (or an authentik host left ungated and unlisted) must fail `hack/check-exposure.sh`.
- **Plain HTTP.** `http://dashy.ashudev.com` must answer a redirect and must not be gated, or an off-LAN browser stops at plain HTTP instead of reaching TLS.

---

### Task 1: Public Dashy route and the two redirects

**Files:**
- Modify: `cluster/helm/dashy/templates/ingress.yaml` (both documents)
- Modify: `cluster/helm/dashy/templates/ingress-redirect.yaml` (both documents)
- Modify: `cluster/helm/dashy/templates/middleware.yaml`
- Modify: `hack/check_exposure.py:14-20`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: router names `dashy` (public `websecure`), `dashy-public-redirect` (public `web`), `dashy-local` (new, `dashy.kub.local` `websecure`), `dashy-redirect` (`dashy.kub.local` `web`); the existing `redirect-kub-to-dashy` / `redirect-kub-to-dashy-secure` names are unchanged. Middleware `dashy/redirect-to-dashy` now targets `https://dashy.ashudev.com`.

- [ ] **Step 1: Render the current charter to get the baseline**

```bash
helm dependency build cluster/helm/dashy
helm template test cluster/helm/dashy -f cluster/helm/dashy/values.yaml | grep -c '^kind: IngressRoute'
```

Expected: `4`.

- [ ] **Step 2: Rewrite `templates/ingress.yaml` as two routers**

First document: keep the name `dashy`, namespace `dashy`, entry point `websecure`, host
`dashy.ashudev.com`, middlewares `authentik-forwardauth` then `security` (both `kube-system`), service
`dashy:8080`, and `tls: certResolver: myresolver` (no `secretName`, no `options`). Second document:
name `dashy-public-redirect`, entry point `web`, same host, middleware `redirect-https`
(`kube-system`) only, same service. Replace the file's header comment with two lines: one saying the
address is public and the gate is `authentik-forwardauth` (the plain `authentik` middleware is headers
and authenticates nothing), one saying no `lan-only` is allowed here because
`hack/check_exposure.py` treats a gated public host as a violation.

- [ ] **Step 3: Rewrite `templates/ingress-redirect.yaml` as two routers**

First document: keep the name `dashy-redirect`, entry point `web`, host `dashy.kub.local`, middlewares
`lan-only` then `redirect-to-dashy`, service `noop@internal` (`kind: TraefikService`). Second document:
name `dashy-local`, entry point `websecure`, host `dashy.kub.local`, same two middlewares, same no-op
service, and `tls: options: {name: internal-tls, namespace: kube-system}` (no `certResolver`). Update
the file's comment: the internal names now redirect to the public address, and the `websecure` twin
exists so the TLS handshake completes before the redirect.

- [ ] **Step 4: Point the regex at the public address**

In `templates/middleware.yaml`, the `redirect-to-dashy` middleware becomes:

```yaml
spec:
  redirectRegex:
    regex: "^https?://(?:dashy\\.kub\\.local|kub\\.home\\.ashudev\\.com)(/.*)?$"
    replacement: "https://dashy.ashudev.com$1"
    permanent: true
```

The optional group keeps a query string (`/path?x=1` is inside `$1`) and leaves a bare host with no
path. It matches no other hostname, so `plex` and the rest are untouched.

- [ ] **Step 5: Render and confirm the router set**

```bash
helm template test cluster/helm/dashy -f cluster/helm/dashy/values.yaml > /tmp/dashy.yaml
grep -c '^kind: IngressRoute' /tmp/dashy.yaml
grep -A3 'match: Host' /tmp/dashy.yaml | grep -E 'match|name' | head -20
```

Expected: `6`, with hosts `dashy.ashudev.com` twice, `dashy.kub.local` twice, and
`kub.home.ashudev.com` twice.

- [ ] **Step 6: Run the exposure checker and watch it fail on the new public host**

```bash
hack/check-exposure.sh --only dashy
```

Expected: FAIL, `dashy.yaml: dashy.ashudev.com: missing kube-system-lan-only@kubernetescrd` (the
checker sees a host it does not know is public).

- [ ] **Step 7: Allowlist the host**

In `hack/check_exposure.py`, add `"dashy.ashudev.com"` to `PUBLIC_HOSTS` with a one-line comment
naming the forward-auth gate as the control.

- [ ] **Step 8: Re-run the checker**

```bash
hack/check-exposure.sh --only dashy
```

Expected: `result: OK`.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/dashy/templates hack/check_exposure.py
git commit -m "🚀 feat(dashy): serve the start page publicly and redirect its old names"
```

---

### Task 2: authentik answers the internet, and the provider follows

**Files:**
- Modify: `cluster/helm/authentik/templates/ingressroute.yaml` (both documents)
- Modify: `cluster/helm/authentik/files/app-dashy.yaml`
- Modify: `hack/check_exposure.py` (`PUBLIC_HOSTS`)

**Interfaces:**
- Consumes: `dashy.ashudev.com` route from Task 1.
- Produces: nothing later tasks import; the blueprint's `external_host` is what the outpost compares against the browser.

- [ ] **Step 1: Remove `lan-only` from both authentik routers**

In `cluster/helm/authentik/templates/ingressroute.yaml`, delete the
`- name: kube-system-lan-only@kubernetescrd` line from the `websecure` router and from the `web`
router. Keep `kube-system-security@kubernetescrd`, `kube-system-redirect-https@kubernetescrd` and the
`tls` block (including `options: authentik-tlsoption`). Add one comment above the `websecure` route
record: the sign-in page must answer the internet because the public dashboard behind it lives there,
and `security` is the remaining middleware.

- [ ] **Step 2: Render authentik and confirm the middleware list**

```bash
helm dependency build cluster/helm/authentik
helm template test cluster/helm/authentik -f cluster/helm/authentik/values.yaml | grep -A12 'authentik.kub.home.ashudev.com' | grep -E 'name:|match:|certResolver'
```

Expected: no `lan-only` on either company of the host; `security` present on `websecure`,
`redirect-https` on `web`, `certResolver: myresolver` intact.

- [ ] **Step 3: Run the checker and watch it fail**

```bash
hack/check-exposure.sh --only authentik
```

Expected: FAIL, `authentik.kub.home.ashudev.com: missing kube-system-lan-only@kubernetescrd`.

- [ ] **Step 4: Allowlist the sign-in host**

Add `"authentik.kub.home.ashudev.com"` to `PUBLIC_HOSTS` in `hack/check_exposure.py`, with a comment
saying it is public because it is the sign-in page of the public dashboard.

- [ ] **Step 5: Re-run the checker**

```bash
hack/check-exposure.sh --only authentik
```

Expected: `result: OK`.

- [ ] **Step 6: Point the provider and the application at the new host**

In `cluster/helm/authentik/files/app-dashy.yaml`: `external_host: https://dashy.ashudev.com` and
`meta_launch_url: https://dashy.ashudev.com/`. Update the two comments that name `dashy.kub.local`
(scheme/host note under `external_host`, and the "Admins only" note) to the new host. The mode stays
`forward_single`, and the policy binding stays `authentik Admins`.

- [ ] **Step 7: Confirm the blueprint renders the new host**

```bash
helm template test cluster/helm/authentik -f cluster/helm/authentik/values.yaml | grep -c 'dashy.ashudev.com'
```

Expected: at least `2` (the provider's `external_host` and the application's `meta_launch_url`), and:

```bash
helm template test cluster/helm/authentik -f cluster/helm/authentik/values.yaml | grep -c 'dashy.kub.local'
```

Expected: `0`.

- [ ] **Step 8: Commit**

```bash
git add cluster/helm/authentik hack/check_exposure.py
git commit -m "🚀 feat(authentik): answer the sign-in page publicly for the public dashboard"
```

---

### Task 3: Documents that describe the routes and the gate

**Files:**
- Modify: `docs/reference/routes.md`
- Modify: `docs/reference/sso-applications.md:45`
- Modify: `AGENTS.md` (§4 public list)
- Modify: `docs/status.md` (Dashy row, and the "no gate" list if it names Dashy)

**Interfaces:**
- Consumes: the hosts and middleware lists Task 1 and Task 2 produce.
- Produces: nothing code depends on.

- [ ] **Step 1: Move Dashy into the Public table**

In `docs/reference/routes.md`, add a row for `dashy.ashudev.com` (`dashy`, gate
`authentik-forwardauth` + `security`, Let's Encrypt, public yes) and a row for
`authentik.kub.home.ashudev.com` (gate `security` only, Let's Encrypt, public yes), and remove
`dashy.kub.local` and `authentik.kub.home.ashudev.com` from the internal table. Update the summary
sentence above the tables (count of public hosts, currently "three"). Update "What the table does not
list": `dashy.kub.local` and `kub.home.ashudev.com` now both redirect to `dashy.ashudev.com`.

- [ ] **Step 2: Update the SSO reference**

`docs/reference/sso-applications.md:45`: the Dashy row's host column becomes `dashy.ashudev.com`, and
the gate column says it is the only public application behind forward-auth.

- [ ] **Step 3: Update the repo rules**

`AGENTS.md` §4, the sentence that lists the public routes: it must read `plex`, `seerr`,
`ashuvidz.com`, `dashy.ashudev.com` and `authentik.kub.home.ashudev.com`, with one clause saying the
last is public because it is the sign-in page for the first.

- [ ] **Step 4: Update the status page**

`docs/status.md`: the `dashy` row mentions the public address and the gate. Leave any other row
alone.

- [ ] **Step 5: Check the docs links and the pages that cite routes**

```bash
python3 hack/check_docs_links.py
grep -rn "dashy.kub.local\|authentik.kub.home.ashudev.com" docs/reference AGENTS.md | head
```

Expected: the checker exits 0; the remaining mentions are the intentional ones (redirects, SSO issuer).

- [ ] **Step 6: Commit**

```bash
git add docs AGENTS.md
git commit -m "📝 docs(routes): record the public dashboard and its public sign-in page"
```

---

### Task 4: Prove it on the cluster

**Files:** none.

**Interfaces:**
- Consumes: the merged chart. Argo CD has no API token (`docs/agent-protocol.md`), so the sync is the
  operator's act or the poll.

- [ ] **Step 1: Confirm the full checker is green before the PR**

```bash
hack/check-exposure.sh
```

Expected: `result: OK`, every chart rendered (no `SKIP`).

- [ ] **Step 2: After the sync, read the live routes**

Through the read-only Kubernetes MCP: `IngressRoute` in namespace `dashy` must carry six routers with
the middleware lists above, and `authentik`'s two routers must carry `security` and no `lan-only`.

- [ ] **Step 3: Check the redirects from the LAN**

```bash
curl -sI https://dashy.kub.local/ | head -3
curl -sI https://kub.home.ashudev.com/ | head -3
```

Expected: `301` with `location: https://dashy.ashudev.com/`.

- [ ] **Step 4: Check the gate from the LAN**

```bash
curl -sI https://dashy.ashudev.com/ | grep -i '^location'
```

Expected: a `302` to `authentik.kub.home.ashudev.com/outpost.goauthentik.io/start?...`, not the
dashboard HTML.

- [ ] **Step 5: Ask the operator for the off-LAN check**

The sign-in page and the dashboard from mobile data is the one check no machine here can make.

- [ ] **Step 6: Close the card**

Move card 254 to `Validation` with the branch, the PR link and the verification output in the
description, per `docs/agent-protocol.md`.
