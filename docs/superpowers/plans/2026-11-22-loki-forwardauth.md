# Loki forward-auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put `kube-system-authentik-forwardauth@kubernetescrd` in front of `loki.kub.local`, over the cluster's internal TLS, so a LAN client without a session is redirected to authentik instead of reaching the log query API.

**Architecture:** The Loki chart grows the same two-router shape Grafana already renders: a `websecure` router carrying `lan-only` then forward-auth with `tls.options: internal-tls`, and a `web` router that only redirects. Identity comes from a new authentik blueprint in the established `files/app-*.yaml` form, whose proxy provider is listed in `outpost-embedded.yaml` in the same commit. Grafana and Fluent Bit keep talking to `loki.monitoring.svc.cluster.local:3100`, so Loki's own configuration does not change.

**Tech Stack:** Helm wrapper charts, Traefik `traefik.io/v1alpha1` CRDs, authentik blueprints applied from a ConfigMap.

**Spec:** Board card [#425 / task 590](https://vikunja.kub.local/tasks/590). The design was agreed in conversation on 2026-11-22: approach 1 (forward-auth plus internal TLS), audience `authentik Admins` only, scripts moved to Grafana's datasource proxy.

## Global Constraints

- YAML: 2 spaces, lowercase keys. Commit prefixes use an emoji: `⬆️` upgrade, `🐛` fix, `🚀` feature, `♻️` refactor, `📝` docs.
- Every non-public route carries `kube-system-lan-only@kubernetescrd`; `hack/check-exposure.sh` must pass.
- Internal TLS means: `entryPoints: [websecure]`, no `secretName`, no `certResolver`, `tls.options.name: internal-tls` with `namespace: kube-system`.
- The redirect router lists `lan-only` first, then `kube-system-redirect-to-https@kubernetescrd`.
- Cross-namespace middleware references are `kube-system-<name>@kubernetescrd`.
- Chart `version:` stays `0.1.0`; this repository does not bump wrapper chart versions.
- Plans and specs under `docs/superpowers/` stay out of `mkdocs.yml`'s nav.

## Review Focus

- **Traefik answers a request to `http://loki.kub.local` from off-LAN.** Expect `403` from `lan-only` on the redirect router, never a redirect that reveals the TLS router first.
- **A gated browser from the LAN with no session.** Expect a redirect to the authentik sign-in, then the Loki UI at `/ui` — not a 404 from an outpost that was never told about the provider.
- **Grafana's log panels.** Expect data with no configuration change, because the datasource is the Service DNS name.
- **The documented `curl` recipes.** Expect every one to name a path that still answers once the host is gated.
- **A stale authentik provider.** Expect `outpost-embedded.yaml` and `files/app-loki.yaml` to land together; a provider missing from the outpost list answers 404 on its host.

---

### Task 1: Loki's routers — internal TLS, forward-auth, redirect twin

**Files:**
- Modify: `cluster/helm/monitoring/loki/templates/ingressroute.yaml`

**Interfaces:**
- Consumes: the `kube-system-*@kubernetescrd` middlewares `lan-only`, `authentik-forwardauth`, `redirect-to-https`, and the `kube-system/internal-tls` TLSOption, all already live.
- Produces: two IngressRoutes in `monitoring`, `loki` on `websecure` and `loki-redirect` on `web`, both matching `Host(\`loki.kub.local\`)` and forwarding to service `loki` port `3100`.

- [ ] **Step 1: Rewrite the template's header comment and the first router**

Replace the comment that says the LAN gate is the only access control with the reason the gate is here: the store holds every namespace's logs for 30 days and answers without authentication, so it sits behind the same outpost as Grafana's dashboards. Keep the existing `apiVersion`/labels/metadata lines. Router `loki`: `entryPoints: [websecure]`, one `Rule` route on `Host(\`{{ .Values.ingress.host }}\`)` with middleware names `kube-system-lan-only@kubernetescrd` then `kube-system-authentik-forwardauth@kubernetescrd`, service `{{ .Values.ingress.service }}` port `{{ .Values.ingress.port }}`, and `tls.options.name: internal-tls`, `namespace: kube-system`.

- [ ] **Step 2: Append the redirect router**

Same document style after `---`: metadata name `{{ .Chart.Name }}-redirect`, same labels, `entryPoints: [web]`, one route on the same host with `kube-system-lan-only@kubernetescrd` then `kube-system-redirect-to-https@kubernetescrd`, same service and port, no `tls:` block.

- [ ] **Step 3: Render the chart and read the two routers**

Run: `helm template test cluster/helm/monitoring/loki -f cluster/helm/monitoring/loki/values.yaml` (run `helm dependency build cluster/helm/monitoring/loki` first, or use the checker below if it is not available offline).
Expected: two `kind: IngressRoute` documents, `loki` with `websecure` and the `internal-tls` option, `loki-redirect` with `web` and no `tls` key.

- [ ] **Step 4: Run the exposure checker on this chart**

Run: `hack/check-exposure.sh --only loki`
Expected: exit 0; the chart's route count is 2 and no violation line mentions `username` or `middleware`.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/loki/templates/ingressroute.yaml
git commit -m "🚀 feat(loki): gate the log store behind authentik over internal TLS"
```

---

### Task 2: The authentik side — provider, application, outpost membership

**Files:**
- Create: `cluster/helm/authentik/files/app-loki.yaml`
- Modify: `cluster/helm/authentik/files/outpost-embedded.yaml`

**Interfaces:**
- Consumes: `ashulab-groups` blueprint (for `authentik Admins`), the flows `default-provider-authorization-implicit-consent` and `default-provider-invalidation-flow`, the managed outpost `goauthentik.io/outposts/embedded`.
- Produces: proxy provider named `loki`, application slug `loki`, both visible to the embedded outpost so `loki.kub.local` stops answering 404 after the sign-in.

- [ ] **Step 1: Write `files/app-loki.yaml`**

Copy the shape of `files/app-dashy.yaml`: `version: 1`, `metadata.name: ashulab-app-loki`, the `metaapplyblueprint` entry requiring `ashulab-groups`, a `authentik_providers_proxy.proxyprovider` with `id: loki-provider`, `identifiers.name: loki`, `mode: forward_single`, `external_host: https://loki.kub.local` (with a comment saying the scheme must stay in step with the `websecure` router in Task 1, or the cookie is `Secure` on a plain-HTTP page and the browser loops), the two flow `!Find` references; an `authentik_core.application` with `slug: loki`, `name: Loki`, `meta_launch_url: https://loki.kub.local/ui`, `meta_icon` set to the logo the Dashy tile already uses (`https://raw.githubusercontent.com/grafana/loki/main/docs/sources/logo.png`); and one `authentik_policies.policybinding` on target `!KeyOf loki-app`, order 0, `group: !Find [authentik_core.group, [name, authentik Admins]]`, with a comment saying why admins only: the store holds every namespace's logs.

- [ ] **Step 2: Register the provider with the embedded outpost**

In `files/outpost-embedded.yaml`, add `ashulab-app-loki` to the `metaapplyblueprint` list and `!Find [authentik_providers_proxy.proxyprovider, [name, loki]]` to `attrs.providers`. Both edits are required — the file replaces the whole provider list.

- [ ] **Step 3: Render the blueprint ConfigMap and check both entries**

Run: `helm template test cluster/helm/authentik -f cluster/helm/authentik/values.yaml | grep -n 'app-loki.yaml\|name, loki'`
Expected: the ConfigMap carries an `app-loki.yaml` key, and the outpost entry has exactly one `name, loki` find.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/authentik/files/app-loki.yaml cluster/helm/authentik/files/outpost-embedded.yaml
git commit -m "🚀 feat(authentik): add the loki forward-auth provider"
```

---

### Task 3: Docs, the dashboard tile, and the changelog

**Files:**
- Modify: `docs/reference/routes.md` (the Loki row, the plain-HTTP table's intro, the trailing paragraph)
- Modify: `docs/observability-conventions.md` (the Loki paragraph, the query recipes, the access-log sentence)
- Modify: `docs/reference/log-budget.md` (the "How to measure it" intro and its calls table)
- Modify: `docs/reference/metrics.md` (the `curl … /config` line)
- Modify: `cluster/helm/dashy/files/conf.yml` (the Loki tile)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the host's new scheme (`https://loki.kub.local`), the gate (`forward-auth`, Admins only), and Grafana's datasource proxy path `/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/…`.
- Produces: no new identifiers; every doc line that promised an unauthenticated store now names the substitute.

- [ ] **Step 1: Move the Loki row in `docs/reference/routes.md`**

Cut the row from *Internal without TLS* and add it to the TLS table after `lidarr`, with Gate `forward-auth; Admins only` and TLS `internal CA`. Then fix the two sentences the move falsifies: "Three routes serve plain HTTP" becomes two, and "The three rows in the last table are served on `web` itself" becomes two. Say in the gate cell that the query API is behind the same gate as the UI, so a reader does not look for an `/api` exemption.

- [ ] **Step 2: Rewrite the Loki paragraph in `docs/observability-conventions.md`**

`https://loki.kub.local`, behind forward-auth (Admins only) over the internal certificate; note that `/ui` and `/loki/api/v1/*` both need the session, and that Grafana and Fluent Bit are unaffected because they use `loki.monitoring.svc.cluster.local:3100`. Replace the "Agents do not need cluster access for any of this" promise with the Loki exception: Loki's recipes go through Grafana's proxy with `GRAFANA_TOKEN`. Give the working example:

```bash
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query={job="fluent-bit"} | detected_level="error"'
```

- [ ] **Step 3: Fix the access-log paragraph's reason**

The paragraph that drops `RequestPath` because "`loki.kub.local` has no authentication and a 30-day retention" now cites a condition that is gone. Keep the decision (task 544 owns the field list) and say so: the store is gated since this change, the path stays dropped because a full log would still carry Plex tokens, FileBrowser share links and OIDC codes into the *Traefik access log itself*, which is read by anyone holding a session. Add one line pointing at the card that revisits the field list.

- [ ] **Step 4: Fix the two remaining `curl http://loki.kub.local` recipes**

In `docs/reference/log-budget.md`, the "Loki answers without authentication at `http://loki.kub.local`" sentence becomes the Grafana-proxy form with the same `$GRAFANA_TOKEN` header and the `P8E80F9AEF21F6940` uid; the table's `Call` column keeps its paths, which now append to the proxy prefix. In `docs/reference/metrics.md`, the `/config` line becomes `curl -sk -H "Authorization: Bearer $GRAFANA_TOKEN" "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/config"`.

- [ ] **Step 5: Point the Dashy tile at HTTPS**

In `cluster/helm/dashy/files/conf.yml`, the Loki tile's `url` becomes `https://loki.kub.local`.

- [ ] **Step 6: Write the changelog entry**

Under `## [Unreleased]` → `### Added`: one entry naming the gate, the internal TLS move, the Admins-only binding, the reason (30 days of every namespace's logs reachable by any LAN device), and the query path that replaces `curl http://loki.kub.local`.

- [ ] **Step 7: Verify no page still advertises an open store**

Run: `grep -rn "loki.kub.local" docs cluster/helm/dashy | grep -v snapshot | grep -v superpowers`
Expected: every remaining line uses `https://` or the Grafana proxy; no line says "no authentication" about the host.

- [ ] **Step 8: Commit**

```bash
git add docs cluster/helm/dashy/files/conf.yml CHANGELOG.md
git commit -m "📝 docs: record the loki gate and how to query logs through Grafana"
```

---

### Task 4: Whole-tree verification and handover

**Files:** none.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: a PR against `develop`.

- [ ] **Step 1: Run the checker suite**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
Expected: exit 0.

- [ ] **Step 2: Run the chart and exposure checkers**

Run: `hack/check-charts.sh` then `hack/check-exposure.sh`
Expected: both exit 0, with `monitoring/loki` and `authentik` in the passing list.

- [ ] **Step 3: Check the ArgoCD references**

Run both loops from `AGENTS.md` §6.
Expected: no `BROKEN PATH` or `BROKEN VALUES` line.

- [ ] **Step 4: Rebase, push, open the PR**

```bash
git fetch origin && git rebase origin/develop
git push origin feature/425-loki-forwardauth
```

Then open the PR against `develop` with the verification output in the body.

- [ ] **Step 5: After the merge, prove the gate on the live host**

Run: `curl -sI http://loki.kub.local | head -3`, then `curl -sI https://loki.kub.local | head -3` and check for the authentik redirect, then Grafana Explore on the Loki datasource, then `curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query --data-urlencode 'query=sum(count_over_time({job="fluent-bit"}[1h]))'`.
Expected: `308` to `https`, then a `302` to `authentik.kub.home.ashudev.com`; the Grafana query answers `"status":"success"` with a non-zero count, and Fluent Bit's ingester rate stays flat across the sync.
