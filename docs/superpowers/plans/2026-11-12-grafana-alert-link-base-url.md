# Grafana alert link base URL — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Source and Silence links in Discord alerts open Grafana at `http://grafana.kub.local/` instead of `http://localhost:3000/`.

**Architecture:** Grafana derives every absolute URL (including the alert notification links) from `server.root_url`. That key is unset in the wrapper chart, so Grafana falls back to localhost. The change adds `root_url` and `domain` under the upstream chart's `grafana.ini` values, which the chart renders into its ConfigMap; a `checksum/config` annotation already rolls the Deployment. No rule, contact point, route or certificate changes.

**Tech Stack:** Helm 4 (wrapper chart, upstream `grafana` 14.4.1), ArgoCD, Grafana 13.3.1 unified alerting, Traefik IngressRoute.

**Spec:** `docs/superpowers/specs/2026-11-12-grafana-alert-link-base-url.md`

## Global Constraints

- Only `cluster/helm/monitoring/grafana/values.yaml` and `docs/observability-conventions.md` change. Rules, silences, contact point `discord-homelab` and the IngressRoute are untouched.
- `root_url` is exactly `http://grafana.kub.local/` — plain HTTP, trailing slash, matching the existing `web` entrypoint route.
- Values file uses 2-space indentation and lowercase keys.
- `cluster/helm/monitoring/*/charts/` and `Chart.lock` are gitignored; never commit them.
- Do not enable `syncPolicy.automated` anywhere (house rule).
- Commit messages use an emoji prefix (`🔧` for config, `📝` for docs).

## Review Focus

- **ConfigMap merge:** the added `[server]` section must not displace the existing `datasources` ConfigMap or the chart's default `grafana.ini` keys. Verified by rendering the chart and reading the output.
- **Scheme/host match:** the link host must be exactly the host Traefik serves on entrypoint `web` (`grafana.kub.local`), otherwise the link lands on a 404.
- **Existing sessions:** changing `domain` can invalidate the admin cookie for the browser. Acceptable; the admin password is in the `grafana` Secret.
- **Off-LAN phones:** `kub.local` resolves only through Pi-hole. Out of scope; recorded in the doc.
- **Unset keys:** no `GF_SERVER_ROOT_URL` env may override the ini file. Confirmed absent on 2026-11-12; re-check with `resources_get` if the rendered config seems ignored.

---

### Task 1: Set `root_url` and `domain` in the Grafana wrapper

**Files:**
- Modify: `cluster/helm/monitoring/grafana/values.yaml` (the `grafana:` block, after `fullnameOverride`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `appUrl = http://grafana.kub.local/`, which Task 3 verifies and Task 2 documents.

- [x] **Step 1: Add the `grafana.ini` block**

In `cluster/helm/monitoring/grafana/values.yaml`, under `grafana:` and immediately after `fullnameOverride: grafana`, add:

```yaml
  grafana.ini:
    server:
      root_url: http://grafana.kub.local/
      domain: grafana.kub.local
```

(`grafana.ini` is the literal dotted key the upstream chart reads; do not split it into nested `grafana:` → `ini:` keys.)

- [x] **Step 2: Build the chart dependencies**

Run: `helm dependency build cluster/helm/monitoring/grafana`
Expected: `Saving 1 chart` / `Deleting outdated charts`, exit 0. Creates the gitignored `charts/` and `Chart.lock`.

- [x] **Step 3: Render and confirm the values reach the ConfigMap**

Run:

```bash
helm template grafana cluster/helm/monitoring/grafana --namespace monitoring \
  | awk '/kind: ConfigMap/{p=1} p' | grep -A2 '^\[server\]'
```

Expected: `root_url = http://grafana.kub.local/` and `domain = grafana.kub.local` under `[server]`.

- [x] **Step 4: Confirm the datasource ConfigMap survives the render**

Run:

```bash
helm template grafana cluster/helm/monitoring/grafana --namespace monitoring \
  | grep -c 'name: Prometheus'
```

Expected: at least `1`. Zero means the values structure displaced the datasources block and the change must be corrected before committing.

- [x] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/grafana/values.yaml
git commit -m "🔧 fix(grafana): set root_url so alert links point at grafana.kub.local"
```

### Task 2: Document the base URL

**Files:**
- Modify: `docs/observability-conventions.md` (the `## How to query the stack` section, Grafana paragraph)

**Interfaces:**
- Consumes: the URL from Task 1.
- Produces: nothing later tasks read; operator-facing documentation.

- [x] **Step 1: Add the explanatory sentence**

After the sentence that starts `**Grafana** — \`http://grafana.kub.local\`.`, add:

```
`server.root_url` is set to that host in the wrapper chart, because Grafana builds the
Source and Silence links of every Discord alert from it; leaving it unset produced
`http://localhost:3000/`.
```

- [x] **Step 2: Commit**

```bash
git add docs/observability-conventions.md
git commit -m "📝 docs(observability): record Grafana's alert-link base URL"
```

### Task 3: Verify against the live cluster after sync

This task runs after the PR is merged to `develop` and ArgoCD has synced. It needs the
Grafana service-account token, not cluster write access.

**Files:** none.

**Interfaces:**
- Consumes: `appUrl` produced by Task 1.

- [x] **Step 1: Confirm ArgoCD rolled the pod with the new ConfigMap**

Run: `source ~/.config/secrets/homelab.env; curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" http://grafana.kub.local/api/frontend/settings | python3 -c 'import json,sys; print(json.load(sys.stdin)["appUrl"])'`
Expected: `http://grafana.kub.local/`. If it still prints localhost, wait for the Deployment rollout, then re-run.

- [x] **Step 2: Send one test notification from the UI**

Open Alerting → Contact points → `discord-homelab` → **Test**.

Do not script this step. Grafana 13 exposes no working HTTP test route for a receiver
created through the legacy provisioning API: `/api/v1/provisioning/contact-points/<uid>/test`
and `/api/alertmanager/grafana/config/api/v1/receivers/<name>/test` return 404, and
`/apis/notifications.alerting.grafana.app/v1beta1/namespaces/default/receivers/<name>/test`
answers `Receiver not found`. The webhook URL is a redacted secure field, so a temporary
receiver cannot be rebuilt either.

- [x] **Step 3: Read the Discord message**

The test message's Source and Silence links must start with `http://grafana.kub.local/`.
If Discord is not reachable at verification time, record this step as the only outstanding
check rather than claiming it passed.
