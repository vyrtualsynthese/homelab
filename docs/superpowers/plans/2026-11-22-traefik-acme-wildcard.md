# Traefik ACME wildcard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Serve all fifteen Let's Encrypt hostnames from two certificates — one DNS-01 wildcard for `ashudev.com`, one SAN pair for `ashuvidz.com` — and prune the per-host certificates afterwards.

**Architecture:** A second ACME resolver in the Traefik values uses the OVH DNS API; one "anchor" route per registered domain carries the whole name list in `tls.domains`, every other route drops `certResolver` and is served by the certificate store over SNI. Credentials are a zone-scoped OVH consumer key in a `kube-system` Secret.

**Tech Stack:** Traefik chart `42.4.1` / Traefik `3.7.19`, Helm, Kubernetes IngressRoute v3 CRDs, OVH API, lego's `ovh` DNS provider.

**Spec:** `docs/superpowers/specs/2026-11-22-traefik-acme-wildcard-design.md`

## Global Constraints

- Traefik chart `42.4.1`, Traefik `3.7.19`; resolver key is `certificatesResolvers` (not `certResolvers`).
- CRDs are `traefik.io/v1alpha1`; `traefik.containo.us` no longer exists.
- The store is `/data/acme.json` on `pvc-traefik-nfs`; both resolvers use that one path.
- The wildcard name list is `ashudev.com`, `*.ashudev.com`, `*.kub.home.ashudev.com` and `kub.home.ashudev.com`, and it appears in exactly one route.
- The `ashuvidz.com` list is `ashuvidz.com`, `www.ashuvidz.com`, in exactly one route.
- No route, entry point, middleware or `tls.options` changes.
- `*.kub.local` stays on the internal CA and is not touched.
- Secrets never go in git. The Secret is `kube-system/traefik-ovh` with `OVH_ENDPOINT`, `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET`, `OVH_CONSUMER_KEY`.
- Every non-public route keeps `kube-system-lan-only@kubernetescrd`; `hack/check-exposure.sh` must pass.

## Review Focus

- A route left with `certResolver: myresolver` re-requests its own per-host certificate, silently defeating the change — the rendered output must show exactly two resolver-bearing routes.
- `www.ashudev.com` and `www.ashuvidz.com` must still answer with a trusted certificate after their resolver is removed; they rely on SNI matching in the store.
- The OVH key is scoped: it can write records under `ashudev.com` only, and nothing else.
- A wrong `domains` list (missing apex, or `kub.home.ashudev.com` instead of the wildcard) yields a certificate that does not match, visible as a name-check failure after issuance.
- `values.yaml` and `values-2.11.yaml` are historical; adding a resolver to them would mislead the next release.

---

### Task 1: Second resolver and OVH environment

**Files:**
- Modify: `cluster/infrastructure/traefik/values-v3.yaml` (the `certificatesResolvers` block, after `metrics.prometheus`)

**Interfaces:**
- Produces: resolver names `myresolver` (TLS-ALPN, unchanged) and `ovhresolver` (DNS-01, new); Secret name `traefik-ovh`; env `OVH_PROPAGATION_TIMEOUT=300`, `OVH_POLLING_INTERVAL=10`.

- [x] **Step 1: Add `envFrom` and `env` above `certificatesResolvers`**

```yaml
# The OVH DNS-01 provider reads its credentials from the environment. The
# Secret holds the four values only; the two timeouts are configuration and
# stay readable here.
envFrom:
  - secretRef:
      name: traefik-ovh
env:
  - name: OVH_PROPAGATION_TIMEOUT
    value: "300"
  - name: OVH_POLLING_INTERVAL
    value: "10"
```

- [x] **Step 2: Keep `myresolver` and add `ovhresolver`**

```yaml
certificatesResolvers:
  myresolver:
    acme:
      email: valentin.ronteix@ashudev.com
      tlsChallenge: true
      storage: /data/acme.json
  ovhresolver:
    acme:
      email: valentin.ronteix@ashudev.com
      storage: /data/acme.json
      dnsChallenge:
        provider: ovh
        delayBeforeCheck: 60
```

- [x] **Step 3: Render and confirm the flags**

Run:
```bash
helm template traefik traefik/traefik --version 42.4.1 \
  -f cluster/infrastructure/traefik/values-v3.yaml \
  | grep -E 'certificatesresolvers\.(myresolver|ovhresolver)|OVH_' | sort
```
Expected: three `myresolver` flags (email, storage, tlsChallenge), four `ovhresolver` flags (email, storage, dnsChallenge.delayBeforeCheck, dnsChallenge.provider), and the two `OVH_` env names.

- [x] **Step 4: Commit**

```bash
git add cluster/infrastructure/traefik/values-v3.yaml
git commit -m "🚀 feat(traefik): add the OVH DNS-01 resolver"
```

---

### Task 2: `ashudev.com` anchor

**Files:**
- Modify: `cluster/helm/ashudev-website/values.yaml:101-102`
- Modify: `cluster/helm/ashudev-website/templates/ingressroute.yaml:32-39` (apex `tls`) and `:104-106` (`www` `tls`)

**Interfaces:**
- Consumes: `ovhresolver` from Task 1.
- Produces: the only route carrying `certResolver: ovhresolver`; `ingressRoute.tls.domains` is a list of `{main, sans}`.

- [x] **Step 1: Add the name list to the values**

```yaml
  tls:
    # -- The anchor for every name under ashudev.com: this route is the only one
    # -- that asks for a certificate, and it asks for the whole list. DNS-01 is
    # -- the only challenge Let's Encrypt accepts for a wildcard.
    certResolver: ovhresolver
    domains:
      - main: ashudev.com
        sans:
          - "*.ashudev.com"
          - "*.kub.home.ashudev.com"
          - "kub.home.ashudev.com"
```

- [x] **Step 2: Render `domains` in the apex template**

Inside the existing `{{- with .Values.ingressRoute.tls }}` block, after the `certResolver` block:

```yaml
    {{- with .domains }}
    domains:
      {{- toYaml . | nindent 6 }}
    {{- end }}
```

- [x] **Step 3: Stop the `www` router from asking**

Replace

```yaml
  tls:
    certResolver: {{ .Values.ingressRoute.tls.certResolver }}
```

with

```yaml
  # `www` is covered by `*.ashudev.com`; asking again would order a second
  # certificate for one name.
  tls: {}
```

- [x] **Step 4: Render and confirm**

Run: `helm template test cluster/helm/ashudev-website -f cluster/helm/ashudev-website/values.yaml | grep -B2 -A8 'domains:'`
Expected: one `domains:` block with `main: ashudev.com` and two `sans`, under `certResolver: ovhresolver`; the `www` route has `tls: {}`.

- [x] **Step 5: Commit**

```bash
git add cluster/helm/ashudev-website
git commit -m "🚀 feat(ashudev): anchor the ashudev.com wildcard certificate"
```

---

### Task 3: `ashuvidz.com` anchor

**Files:**
- Modify: `cluster/helm/ashuvidz-website/values.yaml:101-102`
- Modify: `cluster/helm/ashuvidz-website/templates/ingressroute.yaml:32-39` and `:104-106`

**Interfaces:**
- Consumes: `myresolver` (unchanged).
- Produces: the only route carrying `certResolver: myresolver`.

- [x] **Step 1: Add the two-name list to the values**

```yaml
  tls:
    # -- The zone still answers from AWS, so DNS-01 cannot write the challenge
    # -- record. One certificate over TLS-ALPN carries both names instead.
    certResolver: myresolver
    domains:
      - main: ashuvidz.com
        sans:
          - www.ashuvidz.com
```

- [x] **Step 2: Render `domains` in the apex template**

Same block as Task 2, Step 2.

- [x] **Step 3: Replace the `www` `tls` block with `tls: {}`**, with the comment `# www is in the apex certificate's SAN list.`

- [x] **Step 4: Render and confirm**

Run: `helm template test cluster/helm/ashuvidz-website -f cluster/helm/ashuvidz-website/values.yaml | grep -B2 -A8 'domains:'`
Expected: one `domains:` block with `main: ashuvidz.com` and `sans: [www.ashuvidz.com]`, under `certResolver: myresolver`.

- [x] **Step 5: Commit**

```bash
git add cluster/helm/ashuvidz-website
git commit -m "🚀 feat(ashuvidz): put both names in one certificate"
```

---

### Task 4: Drop the resolver from the other eleven charts

**Files (values-driven — blank the value):**
- `cluster/helm/filebrowser/values.yaml:224`
- `cluster/helm/media-stack/kavita/values.yaml:115`
- `cluster/helm/media-stack/plex/values.yaml:247`
- `cluster/helm/media-stack/seerr/values.yaml:170`
- `cluster/helm/news/freshrss/values.yaml:215`
- `cluster/helm/thelounge/values.yaml:84`

**Files (hardcoded — delete the line from the template):**
- `cluster/helm/authentik/templates/ingressroute.yaml:25`
- `cluster/helm/dashy/templates/ingress.yaml:32`
- `cluster/helm/dashy/templates/ingress-old-secure.yaml:22`
- `cluster/helm/koillection/templates/ingress-public.yaml:31`
- `cluster/helm/n8n/templates/ingress-public.yaml:31`
- `cluster/helm/syncthing/templates/ingress-public.yaml:35`

**Interfaces:**
- Consumes: the certificate store populated by Tasks 2 and 3.
- Produces: no route outside the two anchors sets `certResolver`.

- [x] **Step 1: Blank the six values**

Set each to `certResolver: ""` and leave a one-line comment above the first of each family saying the name is in the `ashudev.com` wildcard.

- [x] **Step 2: Delete the six hardcoded lines**, keeping the `tls:` and `options:` blocks intact.

- [x] **Step 3: Prove only two routes ask for a certificate**

Run:
```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  case "$c" in *authentik*|*dashy*|*filebrowser*|*koillection*|*n8n*|*syncthing*|*thelounge*|*freshrss*|*media-stack/kavita*|*media-stack/plex*|*media-stack/seerr*|*ashudev-website*|*ashuvidz-website*)
    helm template test "$c" -f "$c/values.yaml" 2>/dev/null ;;
  esac
done | grep -c 'certResolver:'
```
Expected: `2`.

- [x] **Step 4: Commit**

```bash
git add cluster/helm
git commit -m "♻️ refactor(charts): serve named routes from the shared certificates"
```

---

### Task 5: Documentation and changelog

**Files:**
- Modify: `docs/reference/routes.md` (the TLS legend, the two intro paragraphs, the `www`/redirect note)
- Modify: `docs/how-it-works/network.md` (the `TLS on kub.local` section and a new paragraph after it)
- Modify: `docs/secrets.md` (add `traefik-ovh`)
- Modify: `CHANGELOG.md` (`## [Unreleased]` → `Changed`)
- Modify: `docs/runbooks/acme-store.md` (add the two-certificate store section and the second keep-list)

**Interfaces:**
- Produces: a reader can name the two certificates and the anchor routes from the docs.

- [x] **Step 1: `routes.md`** — change the TLS legend to say Let's Encrypt means one of two certificates: the `ashudev.com` wildcard (`ashudev.com`, `*.ashudev.com`, `*.kub.home.ashudev.com`, requested by the `ashudev.com` route) or the `ashuvidz.com` pair. Replace "six carry their own certificate" with "six are covered by the `ashudev.com` wildcard".

- [x] **Step 2: `network.md`** — after the internal-CA paragraphs, add a short section naming the two certificates, the anchor routes, the DNS-01/OVH mechanism, and the fact that no per-host certificate exists any more.

- [x] **Step 3: `secrets.md`** — add a row for `kube-system/traefik-ovh`: four keys, the OVH scope, and where the values are kept outside git.

- [x] **Step 4: `CHANGELOG.md`** — under `Changed`: "Traefik serves the public names from two certificates — an `ashudev.com` DNS-01 wildcard and an `ashuvidz.com` SAN pair — instead of one per host."

- [x] **Step 5: Commit**

```bash
git add docs CHANGELOG.md
git commit -m "📝 docs(traefik): record the two-certificate setup"
```

---

### Task 6: Gates, push, pull request

- [x] **Step 1: Run the checker suite**

```bash
hack/check-charts.sh
hack/check-app-version.sh
hack/check-exposure.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```
Expected: all pass, zero failures.

- [x] **Step 2: Resolve the ArgoCD references** (CRLF-safe loop from `AGENTS.md` §6); expect no `BROKEN` lines.

- [x] **Step 3: Rebase and push**

```bash
git fetch origin && git rebase origin/develop
git push -u origin feature/traefik-acme-wildcard
```

- [x] **Step 4: Open the PR** against `develop` with the design, the rendered proof (two resolvers), and the verification output.

---

### Task 7: Live — Secret, upgrade, first issuance

**Preconditions:** the PR is merged or the operator has approved applying the branch, and the OVH consumer key is validated in the browser.

- [x] **Step 1: Create the Secret** (values read from `~/.config/secrets/homelab.env`, never printed):

```bash
kubectl -n kube-system create secret generic traefik-ovh \
  --from-literal=OVH_ENDPOINT=ovh-eu \
  --from-literal=OVH_APPLICATION_KEY="$OVH_TRAEFIK_APPLICATION_KEY" \
  --from-literal=OVH_APPLICATION_SECRET="$OVH_TRAEFIK_APPLICATION_SECRET" \
  --from-literal=OVH_CONSUMER_KEY="$OVH_TRAEFIK_CONSUMER_KEY"
```

- [x] **Step 2: Upgrade** with the runbook's command (`docs/runbooks/traefik-release.md`), using `values-v3.yaml`; wait for rollout.

- [x] **Step 3: Watch the orders**

```bash
kubectl -n kube-system logs deploy/traefik --since=10m | grep -iE 'acme|ovh|error'
```
Expected: `Unable to obtain ACME certificate` absent; two success lines naming the two certificates.

- [x] **Step 4: Prove every name answers with the right certificate**

```bash
for h in ashudev.com www.ashudev.com dashy.ashudev.com ashuvidz.com www.ashuvidz.com \
  authentik.kub.home.ashudev.com filebrowser.kub.home.ashudev.com freshrss.kub.home.ashudev.com \
  kavita.kub.home.ashudev.com koillection.kub.home.ashudev.com n8n.kub.home.ashudev.com \
  plex.kub.home.ashudev.com seerr.kub.home.ashudev.com syncthing.kub.home.ashudev.com \
  thelounge.kub.home.ashudev.com; do
  echo -n "$h: "
  openssl s_client -connect 192.168.1.46:443 -servername "$h" </dev/null 2>/dev/null \
    | openssl x509 -noout -checkhost "$h" 2>/dev/null
done
```
Expected: `Certificate ... matches` for all fifteen.

- [x] **Step 5: Confirm the store and the metric**

`traefik_tls_certs_not_after` should show 3 series (wildcard, SAN pair, internal); `acme.json` holds 18 entries until the prune.

---

### Task 8: Prune the fifteen per-host certificates

- [x] **Step 1:** Follow `docs/runbooks/acme-store.md` (the branch holding it must be merged first, or run the procedure from the PR description) with the keep-list `ashudev.com`, `ashuvidz.com`, `kub.local`.
- [x] **Step 2:** Verify the store holds two entries — `kub.local` is a Secret, not a store entry — byte-identical across a restart, and that both certificates answer for all sixteen hostnames.
- [x] **Step 3:** Record the before/after counts, sizes and md5 in the design doc and the board card.

---

### Task 9: Board and handover

- [x] **Step 1:** Move the homelab card to `Validation` with branch, PR and the verification output in the description.
- [x] **Step 2:** Comment the follow-up: `ashuvidz.com` moves to the OVH resolver when its delegation lands.
