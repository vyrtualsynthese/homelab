# Traefik stability — PR1 (configuration correctness) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the correctness and hygiene gaps the 2026-11-21 Traefik audit found, so the release can be upgraded to v3 without discovering them mid-flight.

**Architecture:** One release values file (`cluster/infrastructure/traefik/values.yaml`) drives the Traefik install; one hand-applied manifest adds the missing HTTP→HTTPS redirect; three dead manifests are deleted; six v2-only `sslRedirect` fields are dropped; Prometheus keeps the latency buckets the new dashboard will read. Nothing touches the certificate store, the CRD group, or any route's host and middleware.

**Tech Stack:** Helm 3 (chart `25.0.3+up25.0.0`, Traefik 2.10.7), Traefik v2 CRDs, Prometheus scrape config, `hack/` shell+Python checkers.

**Spec:** `docs/superpowers/specs/2026-11-21-traefik-stability-design.md`

## Global Constraints

- `cluster/argocd/**` is CRLF; the files in this plan are LF. Preserve what each file has.
- Traefik manifest group stays `traefik.containo.us/v1alpha1` until the v3 PR; the chart's own dashboard route stays whatever it renders (`traefik.io/v1alpha1`).
- Never reference `redirect-https` on the new route: use `redirect-to-https` (the `traefik-gate` object 18 routers already name).
- No route's host, entry point set or middleware list changes, except the dashboard route moving from `web` to `websecure`.
- `cluster/infrastructure/**` is applied by hand; this PR ends with an operator `helm upgrade`.
- Commit messages: emoji prefix (`🐛 fix:`, `🔧 chore:`, `📝 docs:`).

## Review Focus

- A gateway that answers `http://traefik.kub.local` must still redirect (a `301` from `redirect-to-https`) instead of 404 once the dashboard route leaves `web`.
- The metric scrape must survive `expose: false`: Prometheus reads the pod annotation, not the Service.
- `helm upgrade` with the new values must not change the certificate store path, the claim, or any entry point.
- Nothing that still names `redirect-https`, `security` or `default-security` may be left pointing at a deleted object.
- The values file must render with the exact chart the release runs, not a newer one from the index.

---

### Task 1: Release values — dashboard on TLS, metrics unpublished, budget, buckets

**Files:**
- Modify: `cluster/infrastructure/traefik/values.yaml`

**Interfaces:**
- Produces: the values the operator upgrades with; `ingressRoute.dashboard.entryPoints` = `["websecure"]`, `tls.options` = `{name: internal-tls, namespace: kube-system}`, `ports.metrics.expose` = `false`, `resources`, `metrics.prometheus.buckets` = `0.1,0.25,0.5,1.0,2.5,5.0,10.0`.

- [x] **Step 1: Edit the `ingressRoute.dashboard` block** to `entryPoints: ["websecure"]` and add the TLSOption. Keep `enabled`, `matchRule` and the two middlewares byte-identical.

- [x] **Step 2: Set `ports.metrics.expose: false`**, with a comment that Prometheus scrapes the pod's 9100 through the annotation, so the LAN needs no published port.

- [x] **Step 3: Add `resources`** — `requests: {cpu: 10m, memory: 64Mi}`, `limits: {memory: 256Mi}` — with the measured numbers in the comment (2 m CPU, 63 MiB working set).

- [x] **Step 4: Widen `metrics.prometheus.buckets`** to `0.1,0.25,0.5,1.0,2.5,5.0,10.0` and say why in the comment (p95 is unusable when the last finite bucket is 2.5).

- [x] **Step 5: Render against the running chart**

```bash
helm template traefik /tmp/traefik-25.0.3/traefik -n kube-system -f cluster/infrastructure/traefik/values.yaml | grep -A4 'name: traefik-dashboard'
```
Expected: one `IngressRoute` with `entryPoints: [websecure]` and a `tls.options.name: internal-tls`.

- [x] **Step 6: Commit**

```bash
git add cluster/infrastructure/traefik/values.yaml
git commit -m "🐛 fix(traefik): serve the dashboard over internal TLS and stop publishing metrics"
```

### Task 2: The HTTP redirect twin for the dashboard

**Files:**
- Create: `cluster/infrastructure/traefik/dashboard/redirect.yaml`

**Interfaces:**
- Consumes: `kube-system/lan-only` and `kube-system/redirect-to-https`, both live from `traefik-gate`.
- Produces: host `traefik.kub.local` on `web`, answering `301` to `https://`.

- [x] **Step 1: Write the manifest** — `IngressRoute` in `kube-system`, `traefik.containo.us/v1alpha1`, `entryPoints: [web]`, match `Host(\`traefik.kub.local\`)`, middlewares `[{name: lan-only, namespace: kube-system}, {name: redirect-to-https, namespace: kube-system}]`, service `api@internal` of kind `TraefikService` (the same service the chart's route uses).

- [x] **Step 2: Check it is the only `web` router for that host**

```bash
grep -rn 'traefik.kub.local' cluster/infrastructure/traefik/ | grep -v snapshot
```
Expected: the values file (websecure) and this file (web) only.

- [x] **Step 3: Commit**

```bash
git add cluster/infrastructure/traefik/dashboard/redirect.yaml
git commit -m "🚀 feat(traefik): redirect the dashboard's HTTP host to its TLS route"
```

### Task 3: Delete dead manifests, drop `sslRedirect`

**Files:**
- Delete: `cluster/infrastructure/traefik/dashboard/middleware.yaml`, `cluster/infrastructure/traefik/common/middleware-security.yaml`, `cluster/infrastructure/traefik/common/redirect-https.yaml`
- Modify: `cluster/infrastructure/traefik/middleware/security.yaml`, `cluster/helm/syncthing/templates/middleware-security.yaml`, `cluster/helm/n8n/templates/middleware-security.yaml`, `cluster/helm/news/freshrss/templates/middleware-security.yaml`, `cluster/helm/koillection/templates/middleware-security.yaml`

**Interfaces:**
- Produces: no remaining reference to `traefik-dashboard-basicauth`, `default-security`, or the headers field `sslRedirect`.

- [x] **Step 1: Confirm nothing names the three deleted objects**

```bash
grep -rn 'traefik-dashboard-basicauth\|default-security' cluster/ docs/ hack/ | grep -v snapshot
```
Expected: no output (the `#532` dead file and its Secret are the only names).

- [x] **Step 2: Delete the three files and the five `sslRedirect: true` lines.**

- [x] **Step 3: Verify the object that stays is the one the charts name**

```bash
grep -rn 'redirect-https@' cluster/helm | head
grep -c 'name: redirect-https' cluster/infrastructure/traefik/middleware/https-redirect.yaml
```
Expected: three references, and the file still defines `redirect-https`.

- [x] **Step 4: Render the four charts**

```bash
for c in cluster/helm/syncthing cluster/helm/n8n cluster/helm/news/freshrss cluster/helm/koillection; do helm template t "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"; done
```
Expected: no `FAIL`.

- [x] **Step 5: Commit**

```bash
git add -A cluster/infrastructure/traefik cluster/helm
git commit -m "🔧 chore(traefik): delete three dead manifests and the v3-dropped sslRedirect field"
```

### Task 4: Keep the latency histogram, in both scrape jobs

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (two `regex:` lines and the comment above `kubernetes-pods`)
- Modify: `docs/reference/metrics.md` (the `kubernetes-pods` row of the drop table)

**Interfaces:**
- Produces: `traefik_.*_bucket` no longer dropped, so `traefik_router_request_duration_seconds_bucket` reaches the head and a p95 panel is possible.

- [x] **Step 1: Remove `|traefik_.*_bucket` from both jobs' regex**, leaving `qbittorrent_torrent_.*|n8n_.*_bucket` identical in both.

- [x] **Step 2: State the cost in the values comment** — 336 bucket series once the buckets widen, 0.4 % of the 84,747-series head measured 2026-11-21.

- [x] **Step 3: Update the `metrics.md` row** to drop `traefik_*_bucket` from its "Dropped" cell and say the latency panel is its reader.

- [x] **Step 4: Verify the two lists are identical**

```bash
grep -n 'traefik_\.\*_bucket' cluster/helm/monitoring/prometheus/values.yaml || echo GONE
```
Expected: `GONE`.

- [x] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/prometheus/values.yaml docs/reference/metrics.md
git commit -m "🚀 feat(monitoring): keep Traefik's latency buckets for the p95 panel"
```

### Task 5: Dashy's Traefik tile points at HTTPS

**Files:**
- Modify: `cluster/helm/dashy/files/conf.yml`

- [x] **Step 1: Change the URL to `https://traefik.kub.local/dashboard/`.**
- [x] **Step 2: Render the chart** — `helm template t cluster/helm/dashy -f cluster/helm/dashy/values.yaml >/dev/null` — expected exit 0.
- [x] **Step 3: Commit** — `🐛 fix(dashy): link the Traefik tile over TLS`.

### Task 6: Write the traps into the runbook and the references

**Files:**
- Modify: `docs/runbooks/traefik-release.md` (expected-output paragraph at the 401 check, trap table)
- Modify: `docs/reference/routes.md` (the dashboard row moves out of "Internal without TLS")
- Modify: `AGENTS.md` §4 internal-TLS paragraph

- [x] **Step 1: Runbook** — the expected line becomes `http 301 → https 401`, name both URLs, and add traps: dashboard served on `web` only until 2026-11-21, metrics published on 9101 for no reader, orphans in `acme.json` keep renewing.
- [x] **Step 2: `routes.md`** — move `traefik.kub.local` into the internal-TLS table with TLS `internal CA`; "Four routes" becomes "Three routes"; "The four rows in the last table are served on `web` itself" becomes three.
- [x] **Step 3: `AGENTS.md`** — add the dashboard to the internal-TLS examples, naming `internal-tls`.
- [x] **Step 4: Commit** — `📝 docs(traefik): record the dashboard TLS, the published metrics port and the cert orphans`.

### Task 7: The audit page

**Files:**
- Create: `docs/audit/traefik-2026-11-21.md`
- Modify: `docs/README.md` or `mkdocs.yml` nav if the audit section is indexed

**Interfaces:**
- Produces: the evidence the design cites, reproducible by command.

- [x] **Step 1: Write the page** — one-line summary, what was measured and how (MCP, Prometheus proxy, Grafana API, `hack/check-exposure.sh`), the findings table with severity, the certificate inventory, and what is deferred to which card.
- [x] **Step 2: Check the page renders as GitHub Markdown** — no `!!!` admonitions, tables intact.
- [x] **Step 3: Commit** — `📝 docs(audit): the Traefik audit of 2026-11-21`.

### Task 8: Full verification, push, pull request

- [x] **Step 1: Run every gate**

```bash
bash hack/check-charts.sh
bash hack/check-exposure.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```
Expected: charts OK, `result: OK`, tests all pass.

- [x] **Step 2: Rebase on develop, push**

```bash
git fetch origin && git rebase origin/develop && git push -u origin feature/traefik-audit
```

- [x] **Step 3: Open the PR** against `develop` with the spec, the audit page, the operator step (`helm upgrade traefik /tmp/traefik-25.0.3/traefik -n kube-system -f cluster/infrastructure/traefik/values.yaml`) and the verification output in the body.

- [x] **Step 4: Board** — move the task card to `Validation` with branch, PR link and verification output; file `Backlog` cards for the certificate-store cleanup, the access log, and the `ipAllowList` pre-v3 rename.

---

# Part 2: the observability PR

Branch `feature/traefik-observability`, from `origin/develop` at `03520668`, after PR1
([#1386](https://github.com/vyrtualsynthese/homelab/pull/1386)) was merged and its release applied
(`helm history traefik` revision 3). The database is Grafana's; this PR writes to it through the HTTP
API and commits a copy.

### Task 9: The dashboard

**Files:** `cluster/helm/monitoring/grafana/export/traefik/dashboard.json` (created from the API).

- [x] Create uid `homelab-traefik`, title `Homelab · Traefik`, folder `Homelab`, 30 s refresh, 6 h
  default range, links to the native dashboard and to `reference/alerts.md`.
- [x] Four rows, fourteen panels: traffic per router, status classes, p95/p99 per router, 5xx share,
  bytes, open connections, certificate table, last successful reload, reload failures, scrape target.
- [x] Validate every expression against Prometheus through the datasource proxy before saving; the
  certificate table needed an `organize` transform to drop the twelve metadata columns.

### Task 10: The three rules

**Files:** `cluster/helm/monitoring/grafana/export/traefik/rules.json`.

- [x] `TraefikRouterErrorRate`, `TraefikRouterLatencyHigh`, `TraefikConfigReloadFailing` in
  `homelab-core`, `for: 10m`, `severity: warning`, `noDataState: OK`, same shape as
  `CertificateExpiring`.
- [x] Put the comparison **inside** the PromQL (`> 0.10`, `> 1`, `> 0`): the condition only tests
  "above 0", so a rule returning a raw ratio or a raw p95 fires on every router.
- [x] Check each with `POST /api/v1/eval`: all three return no data on a healthy router.
- [x] Confirm the group: 23 rules in `homelab-core`, 19 in `homelab-core-fast`, 42 total.

### Task 11: The export

**Files:** `cluster/helm/monitoring/grafana/export/traefik/{dashboard.json,rules.json,README.md}`.

- [x] `dashboard.json` is a ready-to-post body (`{dashboard, folderUid, overwrite}`) with `id` and
  `version` stripped; `rules.json` the three rules as the provisioning API returns them.
- [x] README: snapshot dated 2026-11-21, database authoritative, why not provisioned, and the
  re-apply commands.

### Task 12: Docs and the correction

**Files:** `docs/reference/dashboards.md`, `docs/reference/alerts.md`, `docs/reference/metrics.md`,
`docs/runbooks/traefik-release.md`, this plan.

- [x] `dashboards.md`: ten screens become eleven, new row and section, the export named as the one
  exception to "not in git".
- [x] `alerts.md`: 39 rules and 20+19 become 42 and 23+19, three rows, and the reasoning for the
  request floor and the provisional latency threshold.
- [x] `metrics.md`: the drop table's reader count moves to eleven dashboards and 42 rules, and says
  why the Traefik buckets came off the list.
- [x] Correct the redirect code: `redirect-to-https` carries `permanent: true`, so the observed answer
  is `301`, not the `308` PR1's spec and runbook wrote. Measured with
  `curl --resolve traefik.kub.local:80:192.168.1.46` on 2026-11-21.
