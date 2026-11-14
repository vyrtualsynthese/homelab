# Media tools behind authentik — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put authentik's forward-auth gate in front of autobrr, bazarr, sonarr, lidarr, radarr, qbittorrent, maintainerr, prowlarr and tautulli, one application at a time, starting with Sonarr.

**Architecture:** One authentik proxy provider per tool (`forward_single`, external host `http://<tool>.kub.local`), all attached to the embedded outpost, and one existing Traefik middleware (`kube-system/authentik-forwardauth`) added to each tool's IngressRoute. The tools' own logins are turned off afterwards, in their UIs, because none of those settings lives in git.

**Tech Stack:** authentik 2026.8.5 blueprints (YAML in `cluster/helm/authentik/files/`), Traefik 2 CRDs, Helm charts under `cluster/helm/media-stack/`, ArgoCD on branch `develop`.

**Spec:** `docs/superpowers/specs/2026-11-14-media-tools-authentik-design.md`

## Global Constraints

- Never commit to or push `develop`. Work happens on `feature/media-tools-authentik`; changes reach the cluster only through a pull request, merged by the operator.
- Every changed chart gets a patch version bump in its `Chart.yaml`, matching the repo's existing practice.
- `cluster/argocd/**` is CRLF; the files touched here are not, so no line-ending changes.
- Ingress stays on the `web` entry point, `http://<tool>.kub.local`, no TLS.
- Middleware references are written `<namespace>-<name>@kubernetescrd`: `kube-system-authentik-forwardauth@kubernetescrd`.
- `external_host` must match the browser's scheme exactly: `http://<tool>.kub.local`, never `https://`.
- No `strip-authentik-header` on these routes: none of the nine trusts an authentik header.
- Every policy binding targets the built-in group `authentik Admins`.
- Verification commands run against the live cluster and the operator's browser; there is no test suite in this repo.

## Review Focus

- **A non-browser client reaching a gated host** (phone app, script, cron) gets a login redirect where it expected data, and after its tool's login is disabled it fails silently. Pinned by Task 2 step 4 and Task 5 step 4.
- **A tool-to-tool URL stored in a database rather than git** (Maintainerr's arr entries, autobrr's qBittorrent entry) breaks when its target is gated. Pinned by Task 2 step 5.
- **The provider missing from the outpost** answers 403 on every request with no clue why. Pinned by Task 1 step 6.
- **authentik down takes all nine tools with it.** Pinned by Task 2 step 7 (the port-forward drill).
- **A cookie loop on every request** if `external_host` and the browser scheme disagree. Pinned by Task 2 step 3.

---

### Task 1: Sonarr's authentik application and the outpost attachment

No traffic changes hands here: creating an application gates nothing until a chart carries the middleware.

**Files:**
- Create: `cluster/helm/authentik/files/app-sonarr.yaml`
- Create: `cluster/helm/authentik/files/outpost-embedded.yaml`
- Modify: `cluster/helm/authentik/Chart.yaml` (version bump)

**Interfaces:**
- Produces: the group `authentik Admins` policy binding pattern every later tool copies, and the `ashulab-outpost-embedded` blueprint whose `providers` list later tools append to.

- [ ] **Step 1: Write `app-sonarr.yaml`**

```yaml
version: 1
metadata:
  name: ashulab-app-sonarr
entries:
  - model: authentik_blueprints.metaapplyblueprint
    attrs:
      identifiers:
        name: ashulab-groups
      required: true

  # Forward auth: no internal host and no client secret. The outpost checks the
  # request and answers Traefik; the tool itself never sees an authentik header,
  # because its own login is switched off in the UI instead.
  - model: authentik_providers_proxy.proxyprovider
    id: sonarr-provider
    identifiers:
      name: sonarr
    attrs:
      name: sonarr
      mode: forward_single
      # Scheme must match the browser: these hosts are served on `web`, plain
      # HTTP. The outpost takes the session cookie's Secure flag from this
      # scheme, so `https` here would break the cookie on an http host.
      external_host: http://sonarr.kub.local
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]

  - model: authentik_core.application
    id: sonarr-app
    identifiers:
      slug: sonarr
    attrs:
      name: Sonarr
      slug: sonarr
      provider: !KeyOf sonarr-provider
      meta_launch_url: http://sonarr.kub.local/
      policy_engine_mode: any

  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf sonarr-app
      order: 0
    attrs:
      group: !Find [authentik_core.group, [name, authentik Admins]]
```

- [ ] **Step 2: Write `outpost-embedded.yaml`**

```yaml
version: 1
metadata:
  name: ashulab-outpost-embedded
entries:
  # Blueprint order is not guaranteed. FileBrowser's provider is already
  # attached by hand today, and this file takes over that assignment: setting
  # `providers` replaces the whole list, so every provider must be here or it is
  # detached.
  - model: authentik_blueprints.metaapplyblueprint
    attrs:
      identifiers:
        name: ashulab-app-filebrowser
      required: true
  - model: authentik_blueprints.metaapplyblueprint
    attrs:
      identifiers:
        name: ashulab-app-sonarr
      required: true

  # The embedded outpost is a managed object. Its identifiers are `managed`, not
  # `name`: keying on the name creates a duplicate outpost and rolls back the
  # whole file. MANAGED_OUTPOST is `goauthentik.io/outposts/embedded`
  # (authentik/outposts/apps.py).
  - model: authentik_outposts.outpost
    identifiers:
      managed: goauthentik.io/outposts/embedded
    attrs:
      providers:
        - !Find [authentik_providers_proxy.proxyprovider, [name, filebrowser]]
        - !Find [authentik_providers_proxy.proxyprovider, [name, sonarr]]
```

- [ ] **Step 3: Bump the chart version**

`cluster/helm/authentik/Chart.yaml`: `version` goes from `0.5.1` to `0.5.2`.

- [ ] **Step 4: Render the chart**

Run: `helm template test cluster/helm/authentik -f cluster/helm/authentik/values.yaml --set authentik.server.ingress.enabled=false 2>&1 | head -20`
Expected: no YAML error; the ConfigMap `authentik-blueprints` contains both new file names. If the chart has dependencies, run `helm dependency build cluster/helm/authentik` first.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/authentik
git commit -m "🚀 feat(sso): give Sonarr an authentik forward-auth application"
```

- [ ] **Step 6: Open the pull request, get it merged, then verify**

After the merge and the `authentik` sync:

```bash
kubectl -n authentik logs deploy/authentik-worker --tail=200 | grep -i blueprint
```
Expected: `ashulab-app-sonarr` and `ashulab-outpost-embedded` reported as applied, with no `EntryInvalidError` and no rollback.

```bash
kubectl -n authentik exec deploy/authentik-worker -- \
  ak shell -c "from authentik.outposts.models import Outpost; o=Outpost.objects.get(managed='goauthentik.io/outposts/embedded'); print(o.name, [p.name for p in o.providers.all()])"
```
Expected: `authentik Embedded Outpost ['filebrowser', 'sonarr']`, exactly one outpost line.

If the outpost entry fails or a second outpost appears, delete `outpost-embedded.yaml`, re-sync, attach `sonarr` by hand in **Applications → Outposts → authentik Embedded Outpost → Edit → Providers**, and record that in the runbook instead. Do not leave a duplicate outpost behind.

Browser check: `http://sonarr.kub.local` still returns Sonarr's own login, unchanged. `http://sonarr.kub.local/outpost.goauthentik.io/start` answers authentik rather than Sonarr.

---

### Task 2: Gate Sonarr

The first real traffic change, on the tool with the most internal callers.

**Files:**
- Modify: `cluster/helm/media-stack/sonarr/values.yaml` (`ingressRoute.middlewares`)
- Modify: `cluster/helm/media-stack/sonarr/Chart.yaml` (version bump)

**Interfaces:**
- Consumes: `kube-system-authentik-forwardauth@kubernetescrd`, live in the cluster (`cluster/infrastructure/traefik/middleware/authentik-forwardauth.yaml`), and Sonarr's provider from Task 1.

- [ ] **Step 1: Add the middleware**

In `cluster/helm/media-stack/sonarr/values.yaml`, replace `middlewares: []` with:

```yaml
  middlewares:
    - name: authentik-forwardauth
      namespace: kube-system
```

- [ ] **Step 2: Bump the chart version**

`cluster/helm/media-stack/sonarr/Chart.yaml`: version goes up by one patch.

- [ ] **Step 3: Verify the render before committing**

Run:
```bash
helm template test cluster/helm/media-stack/sonarr -f cluster/helm/media-stack/sonarr/values.yaml | grep -A4 "kind: Rule"
```
Expected: the route carries `middlewares: - name: kube-system-authentik-forwardauth@kubernetescrd`.

- [ ] **Step 4: Commit, open the pull request, get it merged, then check the gate**

```bash
git add cluster/helm/media-stack/sonarr
git commit -m "🚀 feat(media-stack): gate Sonarr behind authentik"
```

After the merge and sync:

```bash
curl -sI http://sonarr.kub.local/ | head -5
```
Expected: `302` with a `Location` under `authentik.kub.home.ashudev.com`. A `200` means the middleware is not on the route.

Then, in a private browser window: `http://sonarr.kub.local/` shows the authentik login, and after signing in lands on Sonarr. A redirect loop here means the provider's `external_host` says `https` while the host serves `http`.

- [ ] **Step 5: Check the integrations that call Sonarr**

Expect no change, because they use Service DNS. Verify rather than assume:

- Prowlarr → Settings → Apps: Sonarr shows a green test.
- Bazarr → Settings → Sonarr: connection OK.
- Maintainerr → Settings → Sonarr connection: the stored URL reads `http://sonarr.media-stack.svc.cluster.local:8989`, not a `kub.local` name.
- A Sonarr search that reaches an indexer returns releases, which proves Prowlarr → Sonarr is intact end to end.

- [ ] **Step 6: Turn off Sonarr's own login**

In Sonarr: Settings → General → **Authentication Required: Disabled for Local Addresses**, and add `0.0.0.0/0, ::/0` to **Ignore Authentication for these networks**. Save.

Then repeat step 4's browser check: the authentik login appears once and Sonarr opens without a second prompt.

- [ ] **Step 7: Record the recovery route and drill it**

```bash
kubectl -n media-stack port-forward svc/sonarr 8989:8989
```
Expected: `http://localhost:8989` reaches Sonarr with no authentik involvement. Kill the port-forward afterwards.

If this works, the runbook's recovery section is written from what was just observed.

---

### Task 3: The remaining eight applications

Eight blueprint files, then eight chart values. Each tool is its own commit so a failure names itself.

**Files:**
- Create: `cluster/helm/authentik/files/app-{autobrr,bazarr,lidarr,radarr,qbittorrent,maintainerr,prowlarr,tautulli}.yaml`
- Modify: `cluster/helm/authentik/files/outpost-embedded.yaml` (append eight providers)
- Modify: `cluster/helm/authentik/Chart.yaml`

No chart under `cluster/helm/media-stack/` is touched by this task: the middleware belongs to Task 4.

**Interfaces:**
- Consumes: Task 1's file shape, Task 2's values shape.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the eight blueprint files**

Copy `app-sonarr.yaml` and substitute these values, changing `id`, `identifiers.name`, `slug`, `attrs.name` and `external_host`/`meta_launch_url`:

| slug | `id` | display name | host |
|---|---|---|---|
| autobrr | autobrr-provider | Autobrr | `http://autobrr.kub.local` |
| bazarr | bazarr-provider | Bazarr | `http://bazarr.kub.local` |
| lidarr | lidarr-provider | Lidarr | `http://lidarr.kub.local` |
| radarr | radarr-provider | Radarr | `http://radarr.kub.local` |
| qbittorrent | qbittorrent-provider | qBittorrent | `http://qbittorrent.kub.local` |
| maintainerr | maintainerr-provider | Maintainerr | `http://maintainerr.kub.local` |
| prowlarr | prowlarr-provider | Prowlarr | `http://prowlarr.kub.local` |
| tautulli | tautulli-provider | Tautulli | `http://tautulli.kub.local` |

Each file's `metadata.name` is `ashulab-app-<slug>`, and each `metaapplyblueprint` block points at `ashulab-groups`.

- [ ] **Step 2: Append the eight providers to `outpost-embedded.yaml`**

Add one `metaapplyblueprint` entry per new file, and one line per provider to `providers`, keeping `filebrowser` and `sonarr` in the list. Order within the list does not matter.

- [ ] **Step 3: Bump the authentik chart version and render**

```bash
helm template test cluster/helm/authentik -f cluster/helm/authentik/values.yaml >/dev/null && echo OK
```
Expected: `OK`.

- [ ] **Step 4: Commit and merge, then verify all ten providers are attached**

```bash
git add cluster/helm/authentik
git commit -m "🚀 feat(sso): add the remaining media tools to authentik"
```

After the merge:
```bash
kubectl -n authentik logs deploy/authentik-worker --tail=300 | grep -i blueprint
```
Expected: every `ashulab-app-*` and `ashulab-outpost-embedded` applied, no rollback. Then re-run the outpost query from Task 1 step 6; expect all ten names in one line.

Browser check per tool: the authentik tile launches the tool. No middleware is on any of these routes yet, so nobody is gated and nothing can break.

---

### Task 4: Gate the remaining eight

**Files:**
- Modify: eight `values.yaml` files
- Modify: `cluster/helm/media-stack/qbittorrent/templates/ingress.yaml`
- Modify: eight `Chart.yaml` files

**Interfaces:**
- Consumes: Task 2's values shape; `kube-system-authentik-forwardauth@kubernetescrd`.

- [ ] **Step 1: Gate the seven standard charts**

In each of `autobrr`, `bazarr`, `lidarr`, `radarr`, `maintainerr`, `prowlarr`, `tautulli`, apply Task 2 step 1's `middlewares` block to `ingressRoute.middlewares`, and bump the chart version. Commit each chart separately:

```bash
git commit -m "🚀 feat(media-stack): gate <tool> behind authentik"
```

- [ ] **Step 2: Give qBittorrent's chart a middleware list**

`cluster/helm/media-stack/qbittorrent/values.yaml`, under the existing `ingress:` key, add:

```yaml
  # -- Middlewares, rendered as `<namespace>-<name>@kubernetescrd`.
  middlewares: []
```

`cluster/helm/media-stack/qbittorrent/templates/ingress.yaml`, inside the rule, before `services:`:

```yaml
      {{- with .Values.ingress.middlewares }}
      middlewares:
        {{- range . }}
        - name: {{ printf "%s-%s@kubernetescrd" .namespace .name }}
        {{- end }}
      {{- end }}
```

Then set the value:

```yaml
  middlewares:
    - name: authentik-forwardauth
      namespace: kube-system
```

- [ ] **Step 3: Verify every render**

```bash
for c in autobrr bazarr lidarr radarr maintainerr prowlarr tautulli qbittorrent; do
  helm template test cluster/helm/media-stack/$c -f cluster/helm/media-stack/$c/values.yaml \
    | grep -q "kube-system-authentik-forwardauth@kubernetescrd" && echo "OK $c" || echo "MISSING $c"
done
```
Expected: eight `OK` lines.

- [ ] **Step 4: Commit, merge, and verify each host**

```bash
git add cluster/helm/media-stack
git commit -m "🚀 feat(media-stack): gate the remaining media tools behind authentik"
```

After the merge, per tool:
```bash
curl -sI http://<tool>.kub.local/ | head -1
```
Expected: `302` for all eight. `qbittorrent` is the one most likely to be wrong, because its template was hand-edited rather than generated from the shared shape.

- [ ] **Step 5: Turn off each tool's own login**

Applied through each tool's REST API over a port-forward (the gate blocks the ingress path, and
`kubectl` is not available to the agent). The port-forward is the same recovery route as Task 2 step
7. Send the whole current object back with the one field changed; never a partial body.

| Tool | Change | Note |
|---|---|---|
| radarr, lidarr, prowlarr | `PUT /api/v3/config/host` (prowlarr: `/api/v1/config/host`) with `authenticationMethod: none` | was `forms` on the arrs; read it back afterwards and confirm it survived a restart |
| bazarr | `POST /api/system/settings` with `general.authentication_type: None` | verify by a cookie-less request for the page |
| tautulli | `PUT /api/v2/settings` with `general.authentication_method` cleared | keep `TAUTULLI_API_KEY` unchanged |
| qbittorrent | `POST /api/v2/app/setPreferences` with `web_ui_auth_subnet_whitelist_enabled: true` and `web_ui_auth_subnet_whitelist: 10.42.0.0/16` | whitelists the pod network, which is where Traefik's requests come from |
| maintainerr | nothing: no login exists | |
| autobrr | disable in the UI if the software allows; otherwise accept the second prompt and note it | no API path confirmed |

Verification per tool, in this order: the gated host still returns `302` to authentik, and the
port-forward reaches the tool with no prompt.


- [ ] **Step 6: Verify the integrations that cross these tools**

- Maintainerr → Settings: all three arr entries show the Service name and a healthy test.
- autobrr → Settings → Download client: qBittorrent test passes.
- cross-seed and recyclarr: their next scheduled run appears in their logs without an authentication error.
- Prowlarr → Indexers → test all: passes.

---

### Task 5: Documentation

**Files:**
- Modify: `docs/runbooks/authentik-apps.md`
- Modify: `docs/superpowers/specs/2026-11-14-sso-access-model-design.md`
- Modify: `docs/charts/README.md`
- Modify: `cluster/helm/authentik/files/app-filebrowser.yaml` (trailing comment only)

- [ ] **Step 1: Runbook section**

Add a section covering: the nine tools and their slugs, the `app-<tool>.yaml` blueprint shape, the outpost file and what happens if it is deleted (attach by hand), the per-tool login settings from Task 4 step 5, the recovery routes from Task 2 step 7, and the fact that `external_host` must say `http://`.

- [ ] **Step 2: Correct the access-model spec**

Two statements are now false: that FileBrowser is the only forward-auth consumer, and the non-goal about proxy gates breaking phone apps and APIs. Rewrite both, and say the operator overrode the second knowingly.

- [ ] **Step 3: Correct the chart notes**

`docs/charts/README.md`: the tautulli and maintainerr entries say the LAN boundary is the only access control. Both now have a gate.

- [ ] **Step 4: Commit and merge**

```bash
git add docs
git commit -m "📝 docs: record the media tools' authentik gate and its recovery routes"
```

- [ ] **Step 5: Correct the stale comment in FileBrowser's blueprint**

`cluster/helm/authentik/files/app-filebrowser.yaml` ends with a comment saying the provider must be
attached to the embedded outpost by hand and that blueprints cannot do it. That is no longer true:
`outpost-embedded.yaml` owns the assignment. Rewrite the comment to say the outpost file assigns it,
and that deleting that file means attaching by hand again. Comment only — no field changes, so the
application is untouched.

```bash
git add cluster/helm/authentik/files/app-filebrowser.yaml
git commit -m "📝 docs(sso): point FileBrowser's blueprint at the outpost file"
```
