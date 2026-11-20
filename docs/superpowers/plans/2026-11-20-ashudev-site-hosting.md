# ashudev.com off GitHub Pages — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve `ashudev.com` from an nginx chart in the homelab cluster, from the same `gh-pages`
bytes GitHub Pages serves today, with `www` redirecting and the apex records pointing at
`80.14.57.209`.

**Architecture:** A copy of `cluster/helm/ashuvidz-website` — nginx-unprivileged, content cloned by
an init container and refreshed by a git sidecar from the site's own repository — deployed by its own
ArgoCD Application, plus a `www` router that redirects. The DNS change lives in `infra-main/dns` and
is applied by a `workflow_dispatch`, because that stack is gated.

**Tech Stack:** Helm (hand-written chart), ArgoCD, Traefik v2 IngressRoute CRDs, OpenTofu with the
`ovh` provider, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-20-ashudev-site-hosting-design.md`

## Global Constraints

- Files under `cluster/argocd/**` use **CRLF**; write them with `\r\n` and verify with `cat -A`.
- Ingress is `traefik.containo.us/v1alpha1` only. Never `networking.k8s.io/v1`.
- `appVersion` is `1.31.7` and must equal the leading number of the first `tag:` in `values.yaml`
  (`1.31.7-alpine`). `hack/check-app-version.sh` enforces it.
- `ashudev.com` and `www.ashudev.com` both join `PUBLIC_HOSTS` in `hack/check_exposure.py`; a public
  host carries **no** `lan-only` middleware.
- `nodeSelector: kubernetes.io/hostname: nuc0`; memory limits are mandatory on every container.
- No secret is involved: the repository is public and nothing authenticates.
- The site's content, its template and its webpack build are untouched. `gh-pages` is the source.
- `MX`, `SPF` and `DKIM` records are never edited. Mail is the constraint that cannot be traded.

## Review Focus

1. **`www` resolves but has no certificate** — the failure `www.ashuvidz.com` shows today
   (`SSL: no alternative certificate subject name matches target host name`). The `www` router must
   exist and carry `tls.certResolver: myresolver`, not only a redirect middleware.
2. **Apex `AAAA` left in place** — IPv6 clients would keep reaching GitHub while IPv4 clients reach
   the cluster; the records are deleted, not repointed.
3. **Mail interrupted by a zone rewrite** — the plan must show `MX` records unchanged; a diff that
   touches them is a stop.
4. **A rejected `gh-pages` push leaving a stale site silently** — Pages rewrites that branch, so
   `git pull --ff-only` in the sidecar can fail forever and the old content keeps serving. This
   chart fetches and resets instead.
5. **An empty clone that looks healthy** — the init container must fail the pod when `index.html` is
   absent, and readiness must probe `/`, not `/healthz`.

---

### Task 1: The chart, and the public-host entries

**Files:**
- Create: `cluster/helm/ashudev-website/` (`Chart.yaml`, `values.yaml`, `README.md`, `templates/*`)
- Modify: `hack/check_exposure.py` (`PUBLIC_HOSTS`)
- Modify: `CHANGELOG.md` (`## [Unreleased]` → `### Added`)

**Interfaces:**
- Produces: chart path `cluster/helm/ashudev-website`, release name `ashudev`, `app.fullname`
  `ashudev-nginx`, Service `ashudev-nginx` on port 80 → container 8080, `nodeSelector` `nuc0`,
  IngressRoutes `ashudev`, `ashudev-redirect`, `ashudev-www`, Middleware `ashudev-nginx-www`.

- [ ] **Step 1: Write the render check and watch it fail**

```bash
cd /home/vsynthese/.local/share/opencode/worktree/f2827a/site-hosting
helm template ashudev cluster/helm/ashudev-website --namespace ashudev >/dev/null
```
Expected: `Error: chart "cluster/helm/ashudev-website" not found`.

- [ ] **Step 2: Copy the reference chart**

```bash
cp -r cluster/helm/ashuvidz-website cluster/helm/ashudev-website
```

- [ ] **Step 3: Retarget `Chart.yaml`**

`name: ashudev-website`, `description: ashudev.com - the site's published branch served by nginx,
pulled from its own git repository`, `version: 1.0.0`, `appVersion: "1.31.7"`.

- [ ] **Step 4: Retarget `values.yaml`**

Exactly these value changes, comments reworded from `ashuvidz` to `ashudev` where they name the site:

```yaml
content:
  repository: https://github.com/vyrtualsynthese/ashudev-website.git
  ref: gh-pages
ingressRoute:
  host: ashudev.com
  # new block, next to `host:`
  www:
    enabled: true
    host: www.ashudev.com
    # 301 to the apex, keeping the path
    replacement: https://ashudev.com/$1
    permanent: true
```

`nameOverride: nginx` stays, so `app.fullname` is `ashudev-nginx`.

- [ ] **Step 5: Make the sidecar survive a rewritten branch**

In `templates/deployment.yaml`, replace the `git-sync` command body with a fetch-and-reset, so a
force-pushed `gh-pages` (which Pages does) is adopted instead of leaving the site stale:

```yaml
            - |
              while true; do
                git -c safe.directory=/site -C /site fetch --depth 1 --quiet origin \
                  "{{ .Values.content.ref }}" \
                  && git -c safe.directory=/site -C /site reset --hard --quiet FETCH_HEAD \
                  || echo "git fetch failed, still serving the previous content" >&2
                sleep {{ .Values.content.intervalSeconds }}
              done
```

Keep the init container's `test -f /site/index.html` exactly as it is.

- [ ] **Step 6: Add the `www` router to `templates/ingressroute.yaml`**

Inside the existing `{{- if .Values.ingressRoute.enabled }}` block, guarded by
`{{- if .Values.ingressRoute.www.enabled }}`, emit a Middleware and an IngressRoute:

```yaml
apiVersion: {{ .Values.ingressRoute.apiVersion }}
kind: Middleware
metadata:
  name: {{ include "app.fullname" . }}-www
spec:
  redirectRegex:
    regex: ^https?://{{ .Values.ingressRoute.www.host | replace "." "\\." }}/(.*)
    replacement: {{ .Values.ingressRoute.www.replacement }}
    permanent: {{ .Values.ingressRoute.www.permanent }}
---
apiVersion: {{ .Values.ingressRoute.apiVersion }}
kind: IngressRoute
metadata:
  name: {{ default .Release.Name .Values.ingressRoute.name }}-www
spec:
  entryPoints:
    - web
    - websecure
  routes:
    - kind: Rule
      match: Host(`{{ .Values.ingressRoute.www.host }}`)
      middlewares:
        - name: {{ include "app.fullname" . }}-www
          namespace: {{ .Release.Namespace }}
      services:
        - kind: TraefikService
          name: noop@internal
  tls:
    certResolver: {{ .Values.ingressRoute.tls.certResolver }}
```

The `tls` block is the point of this router: without it no certificate is requested for `www`, which
is the defect `www.ashuvidz.com` has today.

- [ ] **Step 7: Render and check the three routes and the certificate**

```bash
helm lint cluster/helm/ashudev-website
helm template ashudev cluster/helm/ashudev-website --namespace ashudev > /tmp/opencode/ashudev-render.yaml
grep -E 'name: ashudev(-www|-redirect)?$|Host\(`|certResolver|name: ashudev-nginx-www' /tmp/opencode/ashudev-render.yaml
grep -c 'test -f /site/index.html' /tmp/opencode/ashudev-render.yaml
grep -A4 'readinessProbe:' /tmp/opencode/ashudev-render.yaml | grep -c 'path: /$'
```
Expected: lint `1 chart(s) linted, 0 chart(s) failed`; the render shows `Host(`ashudev.com`)` with
`certResolver: myresolver`, `Host(`www.ashudev.com`)` with `certResolver: myresolver`, and the
middleware `ashudev-nginx-www`; `1` for the init container's guard on `index.html`, and `1` for a
readiness path of exactly `/` — an empty clone must fail the pod and a broken clone must leave the
Service, rather than a restart loop on `/healthz`.

- [ ] **Step 8: Prove the sidecar no longer depends on fast-forward**

```bash
helm template ashudev cluster/helm/ashudev-website --namespace ashudev | grep -E 'fetch --depth 1|reset --hard' | wc -l
```
Expected: `2` — one line each.

- [ ] **Step 9: Add both hosts to the public list, and run the gate check**

In `hack/check_exposure.py`, add `"ashudev.com"` and `"www.ashudev.com"` to `PUBLIC_HOSTS` with a
comment naming them as the site and its redirect.

```bash
hack/check-exposure.sh --only ashudev
```
Expected: `checked: 3 routes` and `result: OK`. A failure reading "not on the public list" means the
entry is missing or misspelled.

- [ ] **Step 10: README and CHANGELOG**

`cluster/helm/ashudev-website/README.md`: copy the reference's structure, with these facts changed —
content is `ashudev-website`, branch `gh-pages` (the built output Pages published, so no build runs
in the cluster); three routes (`ashudev`, `ashudev-redirect`, `ashudev-www`); the sidecar fetches and
resets because Pages rewrites the branch; the `www` router exists to obtain a certificate for that
name. Keep the link to `../../../docs/charts/README.md`.

`CHANGELOG.md`, under `## [Unreleased]` → `### Added`:

```markdown
- `ashudev.com` is served by this cluster: a third static-site chart, on the `ashuvidz-website`
  pattern, publishing the site's `gh-pages` branch. `www.ashudev.com` redirects to the apex and
  carries its own certificate.
```

- [ ] **Step 11: Run the repo checks**

```bash
hack/check-exposure.sh
hack/check-app-version.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```
Expected: exposure `result: OK` across all routes; app-version `result: OK`; pytest `ok`, no failures.

- [ ] **Step 12: Commit**

```bash
git add cluster/helm/ashudev-website hack/check_exposure.py CHANGELOG.md
git commit -m "🚀 feat(ashudev-website): serve ashudev.com from the cluster"
```

### Task 2: The Application, and proof in the cluster

**Files:**
- Create: `cluster/argocd/projects/ashudev.yaml` (CRLF)

**Interfaces:**
- Consumes: chart path `cluster/helm/ashudev-website` from Task 1.
- Produces: ArgoCD Application `ashudev` in namespace `argocd`, destination namespace `ashudev`,
  release name `ashudev` → `app.fullname` `ashudev-nginx`.

- [ ] **Step 1: Write the Application file with CRLF**

Same shape as `cluster/argocd/projects/ashuvidz.yaml`, with `name: ashudev`, destination namespace
`ashudev`, `path: cluster/helm/ashudev-website`, and `helm.valueFiles: [values.yaml]`. No
`syncPolicy.automated`: this repo leaves auto-sync to the UI on purpose, and a file that declares it
is reverted by the root app on its next sync.

```bash
printf '%s\r\n' 'apiVersion: argoproj.io/v1alpha1' 'kind: Application' 'metadata:' '  name: ashudev' '  namespace: argocd' 'spec:' '  destination:' '    namespace: ashudev' '    server: https://kubernetes.default.svc' '  project: default' '  source:' "    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'" '    targetRevision: develop' '    path: cluster/helm/ashudev-website' '    helm:' '      valueFiles:' '        - values.yaml' > cluster/argocd/projects/ashudev.yaml
cat -A cluster/argocd/projects/ashudev.yaml | head -2
```
Expected: both lines end `^M$`.

- [ ] **Step 2: Check that the path the Application names exists, CRLF-safe**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")
```
Expected: no output.

- [ ] **Step 3: Commit and open the pull request**

```bash
git add cluster/argocd/projects/ashudev.yaml
git commit -m "🚀 feat(ashudev): deploy the site from its own Application"
git push -u origin feature/static-site-hosting
```
Open the PR against `develop` with both commits, and state in the body that merging changes no DNS:
the route answers only once the apex records move.

- [ ] **Step 4: After the merge — sync once and watch the clone**

```bash
kubectl -n argocd get application ashudev -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
kubectl -n ashudev get pods -o wide
kubectl -n ashudev logs deploy/ashudev-nginx -c clone-content --tail=5
```
Expected: pod `Running` on `nuc0`; the init container log ends with a clone, not an error. If the
Application is `OutOfSync` and unsynced, sync it once from the UI — auto-sync is not written in git,
and this app needs one sync, because later content changes arrive through the sidecar.

- [ ] **Step 5: Prove the site is served before DNS moves**

```bash
kubectl -n ashudev port-forward svc/ashudev-nginx 8080:80 &
sleep 2; curl -sS http://127.0.0.1:8080/ | head -c 120; echo
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/assets/  # a directory: 403/404 is fine
```
Expected: the first 120 bytes of `<!doctype html>…<title>Ashu Dev</title>`. A `403` with an empty
`/site` means the clone failed and readiness will keep the pod out of the Service — check the init
log, not the DNS.

### Task 3: The DNS flip in `infra-main`

**Files:**
- Modify: `infra-main/dns/ashudev.tf` (`locals`, `apex_a`, `apex_aaaa`, `www`)

**Interfaces:**
- Consumes: the cluster's public address `80.14.57.209` and the `ashudev` IngressRoutes from Task 2.

- [ ] **Step 1: Read the current records and write down the expected diff**

```bash
cd /home/vsynthese/.local/share/opencode/worktree/f2827a/clever-nebula
tofu -chdir=infra-main/dns init -input=false -backend-config=$HOME/.config/secrets/ovh-s3-backend.tfbackend >/dev/null
TF_VAR_GIT_TOKEN=$GITHUB_PERSONAL_ACCESS_TOKEN tofu -chdir=infra-main/dns plan -no-color | tail -5
```
Expected: `No changes.` — the zone matches the file today.

- [ ] **Step 2: Replace the Pages addresses with the cluster's**

In `locals`: replace `pages_ipv4` and `pages_ipv6` with

```hcl
  # One address: the homelab cluster, which now serves the site. It was four,
  # for GitHub Pages.
  site_ipv4 = "80.14.57.209"
```

and delete the `apex_aaaa` resource entirely (the house has no usable IPv6: no
`*.kub.home.ashudev.com` name carries an `AAAA`). `apex_a` becomes a single record:

```hcl
resource "ovh_domain_zone_record" "apex_a" {
  zone      = local.zone
  fieldtype = "A"
  subdomain = ""
  target    = local.site_ipv4
  ttl       = 3600
}
```

`www` becomes `target = "ashudev.com."`.

- [ ] **Step 3: Prove the diff touches no mail record**

```bash
TF_VAR_GIT_TOKEN=$GITHUB_PERSONAL_ACCESS_TOKEN tofu -chdir=infra-main/dns plan -no-color > /tmp/opencode/dns-plan.txt
grep -cE 'will be (replaced|destroyed)' /tmp/opencode/dns-plan.txt
grep -E '^  # ovh_domain_zone_record\.' /tmp/opencode/dns-plan.txt | sort | uniq -c
```
Expected: exactly the `apex_a` (1 added, 4 destroyed), `apex_aaaa` (4 destroyed) and `www` (1
replaced) resources. **If any `mx`, `spf`, `dkim` or `txt` resource appears in the plan, stop** — the
change is wrong and mail is at stake.

- [ ] **Step 4: Commit and open the pull request**

```bash
git switch -c feature/ashudev-dns-flip origin/develop
git add infra-main/dns/ashudev.tf
git commit -m "🌐 feat(dns): point ashudev.com at the cluster instead of GitHub Pages"
git push -u origin feature/ashudev-dns-flip
```
The PR body carries the plan output from Step 3 and says, in one line, that `MX` is unchanged and
Pages stays published as the rollback.

- [ ] **Step 5: Merge, then apply by dispatch — the stack is gated**

```bash
export GH_TOKEN="$GITHUB_PERSONAL_ACCESS_TOKEN"
gh workflow run tofu-apply.yaml --repo vyrtualsynthese/infra-main \
  -f stack=infra-main/dns -f command=apply
sleep 45; gh run list --repo vyrtualsynthese/infra-main --limit 2
```
Expected: the run applies the plan; the record count goes from four apex `A` to one.

### Task 4: Verify the live site, mail included

- [ ] **Step 1: Wait for the certificate, then check it**

The resolver uses the `TLS-ALPN-01` challenge, so the certificate can only be issued once the name
points at the cluster. The first HTTPS request may fail; retry for up to two minutes.

```bash
curl -sSI https://ashudev.com/ | head -3
echo | openssl s_client -connect ashudev.com:443 -servername ashudev.com 2>/dev/null | openssl x509 -noout -subject -dates
```
Expected: `HTTP/2 200` and a certificate whose subject is `CN=ashudev.com`.

- [ ] **Step 2: Check `www`, which is the half that is broken on the other site**

```bash
curl -sSI https://www.ashudev.com/ | head -4
curl -sS -o /dev/null -w '%{http_code} -> %{redirect_url}\n' https://www.ashudev.com/services.html
```
Expected: `HTTP/2 301` with `location: https://ashudev.com/`, and the path preserved to
`https://ashudev.com/services.html`.

- [ ] **Step 3: Check IPv6 is gone, not pointed elsewhere**

```bash
dig +short AAAA ashudev.com @1.1.1.1 | wc -l
dig +short A ashudev.com @1.1.1.1
```
Expected: `0` and `80.14.57.209`.

- [ ] **Step 4: Check mail, which is the one thing that must not move**

```bash
dig +short MX ashudev.com @1.1.1.1 | sort
dig +short TXT ashudev.com @1.1.1.1 | grep -c spf1
```
Expected: the five `ASPMX`/`ALT` Google hosts, and `1`. Then send a message to
`valentin.ronteix@ashudev.com` from an outside account and confirm it arrives — the operator does
this one, because it needs a second mailbox.

- [ ] **Step 5: Record the rollback**

Pages stays published for a week: rolling back is reverting the DNS PR and dispatching `apply`.
Note that in the report and on the card; disabling Pages is a later decision, not this change.

### Task 5: File the two defects found on the way

- [ ] **Step 1: File one card in `Backlog` on the homelab project (12), label `task`**

Title: `www.ashuvidz.com has no router and no certificate`. Body: the name resolves to
`80.14.57.209` but no IngressRoute matches it, so it answers `SSL: no alternative certificate subject
name matches target host name`; the fix is the `www` block this plan adds to `ashudev-website`,
applied to the `ashuvidz` chart.

- [ ] **Step 2: File one card in `Backlog` on the infra-main project, label `task`**

Title: `ashuvidz.com still resolves to GitHub Pages over IPv6`. Body: the four
`2606:50c0:800[0-3]::153` `AAAA` records remain in the zone, so a v6 client reaches GitHub while a v4
client reaches the cluster; they are deleted, as `ashudev.com`'s were.
