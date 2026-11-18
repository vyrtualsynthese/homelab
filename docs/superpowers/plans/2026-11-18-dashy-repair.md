# Dashy Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the start page at `dashy.kub.local` from a config committed to git, behind the authentik forward-auth gate, admins only, with the image tag pinned.

**Architecture:** The wrapper chart at `cluster/helm/dashy` keeps its pinned upstream dependency and gains one `files/conf.yml` plus one template that renders it into the `dashy-conf` Secret the upstream chart mounts. Three Traefik CRD routers replace the four current ones, all on the internal host. A new authentik blueprint creates the forward-auth provider and binds it to `authentik Admins`, and the outpost file lists it.

**Tech Stack:** Helm 3, Traefik v2 CRDs (`traefik.containo.us/v1alpha1`), ArgoCD, authentik blueprints (2026.8.5), Dashy 4.9.19.

**Spec:** `docs/superpowers/specs/2026-11-18-dashy-repair-design.md`

## Global Constraints

- Traefik `traefik.containo.us/v1alpha1` CRDs only; no `networking.k8s.io` Ingress.
- Every non-public router carries `kube-system-lan-only@kubernetescrd`.
- Hosts: `dashy.kub.local` (only) and `kub.home.ashudev.com` (redirect only). The string
  `dashy.ashudev.com` must not survive in a chart or a living document; `docs/superpowers/**` and
  `docs/audit/**` are dated records and keep it.
- Image tag exactly `4.9.19`; no `latest` in the chart.
- Node pin `kubernetes.io/hostname: nuc0`; the retired `node-type` label must not appear in this chart.
- Websecure router on `dashy.kub.local`: no `certResolver`, no `secretName`; `tls.options` = `internal-tls` in `kube-system`.
- Middleware order on that router: `lan-only`, then `authentik-forwardauth`, then `security`.
- authentik: proxy provider `forward_single`, `external_host: https://dashy.kub.local`, application slug `dashy`, one policy binding to group `authentik Admins`.
- File endings LF for everything this plan touches. Do not touch `cluster/argocd/**` (CRLF).
- No credentials in `files/conf.yml`. Anything secret later goes into `env.existingSecret`.

## Review Focus

- **A tile whose host no router serves** would be a dead link. Pinned by Task 2, step 4 (every `url:` authority is one of the 33 known hosts).
- **An icon slug that 404s** renders a blank tile. Pinned by Task 2, step 5 (`curl -f` on every `hl-` URL).
- **A config edit that never reaches the running pod**: the Secret is mounted with `subPath`, so the file does not update in place. Pinned by Task 1, step 6 (the `dashy.kub.local/config` pod annotation renders) and Task 2, step 7 (the annotation value is bumped).
- **A forward-auth provider missing from the outpost** answers 404 at the browser. Pinned by Task 4, step 4 (both lists in `outpost-embedded.yaml` name `dashy`).
- **`lan-only` after the gate**: an off-LAN request would hit authentik before being refused. Pinned by Task 3, step 4.

---

### Task 1: Pin the image and the node, and own the config Secret

**Files:**
- Modify: `cluster/helm/dashy/values.yaml`
- Create: `cluster/helm/dashy/templates/secret-config.yaml`
- Create: `cluster/helm/dashy/files/conf.yml` (placeholder; Task 2 fills it)

**Interfaces:**
- Consumes: nothing.
- Produces: Secret `dashy-conf` with key `conf` in the release namespace, consumed through `static.existingSecret`; the pod annotation key `dashy.kub.local/config`, whose value Task 2 bumps.

- [ ] **Step 1: Confirm the chart does not render the Secret today**

```bash
cd cluster/helm/dashy && helm dependency build >/dev/null
helm template test . -f values.yaml | grep -c 'kind: Secret'
```
Expected: `0`.

- [ ] **Step 2: Edit `values.yaml`**

- `image.tag: "4.9.19"`, and replace the `# Overrides the image tag…` comment with: `# Pinned: Renovate bumps this. Dashy rebuilds its static bundle at container start.`
- Delete the whole `affinity:` block and add:
  ```yaml
  nodeSelector:
    kubernetes.io/hostname: nuc0
  ```
- Add under `podAnnotations` (new key, top level next to `serviceAccount`):
  ```yaml
  podAnnotations:
    # The config Secret is mounted with subPath, so an update to `files/conf.yml`
    # changes nothing inside the running container until the pod is replaced.
    # Bump this value in the same commit as any config change.
    dashy.kub.local/config: "1"
  ```
- Keep `static.existingSecret: dashy-conf`, `image.pullPolicy: IfNotPresent`, `probs`, `resources` and `ingress.enabled: false` as they are.

- [ ] **Step 3: Create `templates/secret-config.yaml`**

```yaml
{{- /* The dashboard definition is a file in this chart, not a hand-made
Secret. It is rendered here so ArgoCD owns it and the old `dashy-conf` is
replaced on the next sync. Nothing in it is secret today; credentials for
widgets later belong in `env.existingSecret`, which the upstream chart
injects as environment variables. */}}
{{- if .Values.static.existingSecret }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.static.existingSecret }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "dashy.labels" . | nindent 4 }}
type: Opaque
stringData:
  conf: |
{{ .Files.Get "files/conf.yml" | indent 4 }}
{{- end }}
```

- [ ] **Step 4: Create a placeholder `files/conf.yml`** containing exactly:

```yaml
pageInfo:
  title: ashulab
```

- [ ] **Step 5: Render and check the Secret, the tag and the node pin**

```bash
cd cluster/helm/dashy && helm template test . -f values.yaml > /tmp/dashy.yaml
grep -A3 '^  name: dashy-conf' /tmp/dashy.yaml | head -4        # Secret exists
grep -c 'ghcr.io/lissy93/dashy:4.9.19' /tmp/dashy.yaml          # 1
grep -c 'node-type' /tmp/dashy.yaml                             # 0
grep -c 'kubernetes.io/hostname: nuc0' /tmp/dashy.yaml          # 1
grep -c 'latest' /tmp/dashy.yaml                                # 0
```
Expected: the Secret header prints, then `1`, `0`, `1`, `0`.

- [ ] **Step 6: Check the annotation reaches the pod template**

```bash
grep -c 'dashy.kub.local/config' /tmp/dashy.yaml
```
Expected: `1` (inside `template.metadata.annotations`).

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/dashy/values.yaml cluster/helm/dashy/templates/secret-config.yaml cluster/helm/dashy/files/conf.yml
git commit -m "🔧 chore(dashy): pin the image, the node and the config mount"
```

---

### Task 2: Write the dashboard definition

**Files:**
- Modify: `cluster/helm/dashy/files/conf.yml` (full replacement of the placeholder)
- Modify: `cluster/helm/dashy/values.yaml` (annotation bump only)

**Interfaces:**
- Consumes: the Secret mount from Task 1.
- Produces: the file content the Secret carries.

- [ ] **Step 1: Write `files/conf.yml`** with this shape and these entries. `pageInfo.title` is `ashulab`, `pageInfo.description` is `every service on the lab`. `appConfig`: `theme: callisto`, `layout: horizontal`, `iconSize: medium`, `defaultOpeningMethod: newtab`, `statusCheck: false`, `preventWriteToDisk: true`, `disableConfiguration: true`, `enableServiceWorker: false`, `faviconApi: local`.

| Section | Title | URL | Icon |
|---|---|---|---|
| Media (`fas fa-film`) | Plex | `https://plex.kub.home.ashudev.com` | `hl-plex` |
| | Seerr | `https://seerr.kub.home.ashudev.com` | `hl-jellyseerr` |
| | Sonarr | `https://sonarr.kub.local` | `hl-sonarr` |
| | Sonarr (anime) | `https://sonarr-anime.kub.local` | `hl-sonarr` |
| | Radarr | `https://radarr.kub.local` | `hl-radarr` |
| | Radarr (anime) | `https://radarr-anime.kub.local` | `hl-radarr` |
| | Lidarr | `https://lidarr.kub.local` | `hl-lidarr` |
| | Prowlarr | `https://prowlarr.kub.local` | `hl-prowlarr` |
| | Bazarr | `https://bazarr.kub.local` | `hl-bazarr` |
| | Bazarr (anime) | `https://bazarr-anime.kub.local` | `hl-bazarr` |
| | qBittorrent | `https://qbittorrent.kub.local` | `hl-qbittorrent` |
| | autobrr | `https://autobrr.kub.local` | `hl-autobrr` |
| | Maintainerr | `https://maintainerr.kub.local` | `hl-maintainerr` |
| | Tautulli | `https://tautulli.kub.local` | `hl-tautulli` |
| Books and reading (`fas fa-book`) | Calibre-Web Automated | `https://calibre.kub.local` | `hl-calibre-web` |
| | Kavita | `https://kavita.kub.home.ashudev.com` | `hl-kavita` |
| | Koillection | `https://koillection.kub.home.ashudev.com` | `hl-koillection` |
| | FreshRSS | `https://freshrss.kub.home.ashudev.com` | `hl-freshrss` |
| Home and tools (`fas fa-toolbox`) | FileBrowser | `https://filebrowser.kub.home.ashudev.com` | `hl-filebrowser` |
| | Syncthing | `https://syncthing.kub.home.ashudev.com` | `hl-syncthing` |
| | The Lounge | `https://thelounge.kub.home.ashudev.com` | `hl-thelounge` |
| | n8n | `https://n8n.kub.home.ashudev.com` | `hl-n8n` |
| | Vikunja | `https://vikunja.kub.local` | `hl-vikunja` |
| | Docs | `https://docs.kub.local` | `hl-mkdocs` |
| Infrastructure (`fas fa-server`) | Argo CD | `https://argocd.kub.local` | `hl-argocd` |
| | Authentik | `https://authentik.kub.home.ashudev.com` | `hl-authentik` |
| | Grafana | `https://grafana.kub.local` | `hl-grafana` |
| | Prometheus | `http://prometheus.kub.local` | `hl-prometheus` |
| | Loki | `http://loki.kub.local` | `hl-loki` |
| | pgAdmin | `http://pgadmin.kub.local` | `hl-pgadmin` |
| | Traefik | `http://traefik.kub.local/dashboard/` | `hl-traefik` |
| Elsewhere (`fas fa-globe`) | ashuvidz.com | `https://ashuvidz.com` | `icon: favicon` |
| | Forum | `https://forum.ashuvidz.com` | `hl-discourse` |

Where two items share an icon or a name stem, add a `description` that says which is which:
`television` for Sonarr, `films` for Radarr, `subtitles` for Bazarr, `anime` added to the three
anime entries. Add `music` (Lidarr), `indexers` (Prowlarr), `downloads` (qBittorrent), `requests`
(Seerr).

- [ ] **Step 2: Parse the file**

```bash
python3 -c "import yaml,sys; c=yaml.safe_load(open('cluster/helm/dashy/files/conf.yml')); print(sum(len(s['items']) for s in c['sections']))"
```
Expected: `33`.

- [ ] **Step 3: Check the file is inside the rendered Secret**

```bash
cd cluster/helm/dashy && helm template test . -f values.yaml | grep -c 'title: ashulab'
```
Expected: `1`.

- [ ] **Step 4: Every URL authority is a known host**

```bash
python3 - <<'EOF'
import yaml, urllib.parse
c = yaml.safe_load(open('cluster/helm/dashy/files/conf.yml'))
allowed = set("""plex.kub.home.ashudev.com seerr.kub.home.ashudev.com sonarr.kub.local
sonarr-anime.kub.local radarr.kub.local radarr-anime.kub.local lidarr.kub.local
prowlarr.kub.local bazarr.kub.local bazarr-anime.kub.local qbittorrent.kub.local
autobrr.kub.local maintainerr.kub.local tautulli.kub.local calibre.kub.local
kavita.kub.home.ashudev.com koillection.kub.home.ashudev.com freshrss.kub.home.ashudev.com
filebrowser.kub.home.ashudev.com syncthing.kub.home.ashudev.com thelounge.kub.home.ashudev.com
n8n.kub.home.ashudev.com vikunja.kub.local docs.kub.local argocd.kub.local
authentik.kub.home.ashudev.com grafana.kub.local prometheus.kub.local loki.kub.local
pgadmin.kub.local traefik.kub.local ashuvidz.com forum.ashuvidz.com""".split())
bad = [i['url'] for s in c['sections'] for i in s['items']
       if urllib.parse.urlparse(i['url']).hostname not in allowed]
print(bad or "ok")
EOF
```
Expected: `ok`.

- [ ] **Step 5: Every `hl-` icon resolves**

```bash
for n in $(grep -o 'hl-[a-z0-9-]*' cluster/helm/dashy/files/conf.yml | sort -u | sed 's/^hl-//'); do
  for ext in svg png; do
    curl -sfI "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/$ext/$n.$ext" >/dev/null && { echo "ok $n.$ext"; continue 2; }
  done
  echo "MISSING $n"
done
```
Expected: no `MISSING` line. Replace any missing slug with `icon: favicon` for that item.

- [ ] **Step 6: Bump the pod annotation** in `values.yaml` from `"1"` to `"2"`.

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/dashy/files/conf.yml cluster/helm/dashy/values.yaml
git commit -m "🚀 feat(dashy): put every application on the start page"
```

---

### Task 3: One internal host, one gate

**Files:**
- Modify: `cluster/helm/dashy/templates/ingress.yaml`
- Modify: `cluster/helm/dashy/templates/ingress-redirect.yaml`
- Modify: `cluster/helm/dashy/templates/ingress-old.yaml`
- Modify: `cluster/helm/dashy/templates/ingress-old-secure.yaml`
- Modify: `cluster/helm/dashy/templates/middleware.yaml`
- Modify: `cluster/helm/dashy/values.yaml` (the disabled upstream `ingress:` block only)

**Interfaces:**
- Consumes: Service `dashy` port `8080` from the upstream chart.
- Produces: routers `dashy` (websecure, `dashy.kub.local`), `dashy-redirect` (web, same host), `redirect-kub-to-dashy` (web), `redirect-kub-to-dashy-secure` (websecure, `kub.home.ashudev.com`).

- [ ] **Step 1: Confirm the current hostnames**

```bash
grep -rh 'Host(' cluster/helm/dashy/templates | sort -u
```
Expected today: four lines, two on `dashy.ashudev.com`, two on `kub.home.ashudev.com`.

- [ ] **Step 2: Rewrite `ingress.yaml`** — keep the file's header comment and CRD, replace the route block:

`match: Host(\`dashy.kub.local\`)`, `entryPoints: [websecure]`, middlewares in order `lan-only` (`kube-system`), `authentik-forwardauth` (`kube-system`), `security` (`kube-system`), service `dashy:8080`, and:
```yaml
  tls:
    options:
      name: internal-tls
      namespace: kube-system
```
Add above the route: `# Internal only. `dashy.ashudev.com` is retired: the page links to every
# application, so it is reachable from the LAN and nowhere else. The gate is
# `authentik-forwardauth`, bound to `authentik Admins` by
# cluster/helm/authentik/files/app-dashy.yaml; `authentik` alone is headers.`

- [ ] **Step 3: Rewrite `ingress-redirect.yaml`** — `entryPoints: [web]`, `Host(\`dashy.kub.local\`)`, middlewares `lan-only` then `redirect-https` (both `kube-system`), service `dashy:8080`. No `tls` block.

- [ ] **Step 4: Update the two `kub.home.ashudev.com` redirect routers** — hosts stay, service stays `noop@internal`; in `middleware.yaml` change the redirect regex replacement to `https://dashy.kub.local/$1` and keep the regex `^https?://kub\\.home\\.ashudev\\.com/(.*)`. Keep `certResolver: myresolver` on the websecure one (it is a `*.kub.home.ashudev.com` name).

- [ ] **Step 5: Update the upstream `ingress:` block in `values.yaml`** — it is disabled
  (`ingress.enabled: false`) and renders nothing, but it still carries the old host. Set
  `hosts[0].host: dashy.kub.local`, put `kube-system-lan-only@kubernetescrd` first in the
  `router.middlewares` annotation, and delete the `router.tls.certresolver` line (the internal
  router takes the cluster certificate). Leave `enabled: false`.

- [ ] **Step 6: Render and check**

```bash
cd cluster/helm/dashy && helm template test . -f values.yaml > /tmp/dashy.yaml
grep -n 'Host(' /tmp/dashy.yaml
grep -c 'dashy.ashudev.com' /tmp/dashy.yaml     # 0
grep -c 'internal-tls' /tmp/dashy.yaml          # 1
grep -c 'name: authentik-forwardauth' /tmp/dashy.yaml  # 1
grep -n -A2 'name: lan-only' /tmp/dashy.yaml    # lan-only is first in each list
```
Expected: two hosts (`dashy.kub.local` twice, `kub.home.ashudev.com` twice), then `0`, `1`, `1`, and every middleware list starting with `lan-only`.

- [ ] **Step 7: Exposure check on this chart**

```bash
./hack/check-exposure.sh --only dashy
```
Expected: no line beginning `cluster/helm/dashy:`, and the summary line `cluster/helm/dashy … 4 routes`.

- [ ] **Step 8: The old hostname is gone from the chart**

```bash
git grep -n 'dashy.ashudev.com' -- cluster docs ':!docs/superpowers' ':!docs/audit'
```
Expected: no output (the documents are updated in Task 5; the spec, this plan and the dated audit
page keep naming the old host on purpose).

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/dashy/templates cluster/helm/dashy/values.yaml
git commit -m "🔒 fix(dashy): serve it internally behind the forward-auth gate"
```

---

### Task 4: authentik application, admins only

**Files:**
- Create: `cluster/helm/authentik/files/app-dashy.yaml`
- Modify: `cluster/helm/authentik/files/outpost-embedded.yaml`

**Interfaces:**
- Consumes: nothing from earlier tasks; the host string must match Task 3's router.
- Produces: proxy provider `dashy`, application slug `dashy`, so the outpost answers `dashy.kub.local`.

- [ ] **Step 1: Confirm the host has no provider yet**

```bash
grep -rn 'dashy' cluster/helm/authentik/files/
```
Expected: no output.

- [ ] **Step 2: Create `app-dashy.yaml`** by copying `app-docs.yaml` verbatim and changing: `metadata.name: ashulab-app-dashy`; provider `id: dashy-provider`, `name: dashy`, `external_host: https://dashy.kub.local`; application `id: dashy-app`, `slug: dashy`, `name: Dashy`, `meta_launch_url: https://dashy.kub.local/`, `meta_icon: https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/dashy.svg`; the binding comment becomes: `# Admins only, for now: the page links to every application, including the
  # admin tools. Widening it is a second binding for `homelab-users`, nothing in
  # the chart.` All three entries keep their models and flows unchanged, and the binding stays `group: !Find [authentik_core.group, [name, authentik Admins]]`.

- [ ] **Step 3: Add the app to the outpost file** — one `metaapplyblueprint` entry
  ```yaml
  - model: authentik_blueprints.metaapplyblueprint
    attrs:
      identifiers:
        name: ashulab-app-dashy
      required: true
  ```
  after the `ashulab-app-docs` entry, and one provider line
  ```yaml
        - !Find [authentik_providers_proxy.proxyprovider, [name, dashy]]
  ```
  at the end of the outpost's `providers` list.

- [ ] **Step 4: Both lists name it exactly once each**

```bash
grep -c 'ashulab-app-dashy' cluster/helm/authentik/files/outpost-embedded.yaml      # 1
grep -c 'proxyprovider, \[name, dashy\]' cluster/helm/authentik/files/outpost-embedded.yaml  # 1
grep -c 'external_host: https://dashy.kub.local' cluster/helm/authentik/files/app-dashy.yaml # 1
```

- [ ] **Step 5: Both files parse as YAML with authentik's tags**

```bash
python3 - <<'EOF'
import yaml
class L(yaml.SafeLoader): pass
L.add_multi_constructor('!', lambda l, s, n: None)
for f in ('cluster/helm/authentik/files/app-dashy.yaml',
          'cluster/helm/authentik/files/outpost-embedded.yaml'):
    d = yaml.load(open(f), Loader=L)
    print(f, d['metadata']['name'], len(d['entries']))
EOF
```
Expected: `ashulab-app-dashy` with `3` entries, `ashulab-outpost-embedded` with `15`.

- [ ] **Step 6: The blueprint reaches the ConfigMap**

```bash
cd cluster/helm/authentik && helm dependency build >/dev/null 2>&1
helm template test . -f values.yaml | grep -c 'ashulab-app-dashy'
```
Expected: `1` or more.

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/authentik/files
git commit -m "🔒 feat(authentik): gate dashy to admins"
```

---

### Task 5: The documents follow

**Files:**
- Modify: `docs/reference/routes.md`
- Modify: `docs/reference/sso-applications.md`
- Modify: `docs/secrets.md`
- Modify: `docs/how-it-works/applications.md`

**Interfaces:**
- Consumes: the host and the gate from Tasks 3 and 4.
- Produces: nothing code depends on.

- [ ] **Step 1: `routes.md`** — in the "Internal over TLS" table replace the Dashy row with
  `` `dashy.kub.local` | Dashy | `dashy` | `lan-only` then forward-auth, admins only | internal CA | no `` ;
  in "What the table does not list", change the redirect-twin sentence to name `dashy.kub.local`, and
  drop Dashy from the "Six routes carry no `lan-only`" paragraph (that leaves five; change the count
  and the list). Update the `Verified` line to 2026-11-18.

- [ ] **Step 2: `sso-applications.md`** — replace the Dashy row with
  `| Dashy | forward-auth gate | configured; blueprint `app-dashy`, Admins only | `dashy.kub.local` |`
  and drop the Dashy clause from the first trap bullet if it still names it.

- [ ] **Step 3: `secrets.md`** — the `dashy-conf` row reads: `` | `dashy-conf` | `dashy` | rendered by `cluster/helm/dashy/templates/secret-config.yaml` from `files/conf.yml`; holds no credentials | ``.

- [ ] **Step 4: `how-it-works/applications.md`** — remove `dashy` from the retired-`node-type` list
  paragraph and from its parenthetical about `small` on `rbp2`.

- [ ] **Step 5: The old hostname is gone and the new one is documented**

```bash
git grep -n 'dashy.ashudev.com' -- cluster docs ':!docs/superpowers' ':!docs/audit' ; git grep -c 'dashy.kub.local' docs/reference docs/how-it-works docs/secrets.md
```
Expected: first command silent; second prints `1` or more for each of the four files.

- [ ] **Step 6: Commit**

```bash
git add docs/reference/routes.md docs/reference/sso-applications.md docs/secrets.md docs/how-it-works/applications.md
git commit -m "📝 docs(dashy): the start page moved and the gate is real"
```

---

### Task 6: Whole-repo verification

**Files:** none.

- [ ] **Step 1: Every chart still renders**

```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```
Expected: no `FAIL`.

- [ ] **Step 2: Exposure, the whole repo**

```bash
./hack/check-exposure.sh
```
Expected: exit 0; no `cluster/helm/dashy:` violation lines.

- [ ] **Step 3: The chart's checker self-test**

```bash
./hack/check-exposure.sh --self-test
```
Expected: `OK`.

- [ ] **Step 4: Branch is linear on `develop` and pushed**

```bash
git fetch origin && git rebase origin/develop
git log --oneline origin/develop..HEAD
```
Expected: five commits, one per task.

- [ ] **Step 5: Hand over**

```bash
git push origin feature/dashy-repair
```
Then open the pull request against `develop` and move card 229 to `Validation`, with the branch, the
PR link and the output of steps 2 and 3 in the description.
