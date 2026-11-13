# bazarr chart refactor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two dead Bazarr instances' stalled remote chart with two local charts that follow the repo contract, switch both ArgoCD Applications to them, and record the two API keys in `homelab.env` for the day the exporter is enabled.

**Architecture:** Two independent chart directories, `bazarr` and `bazarr-anime`, built from the `sonarr` / `sonarr-anime` pair. Each renders five objects: Deployment, Service, ServiceAccount, config claim and IngressRoute. The config claim is `local-path` on `streaming`; the shared `pvc-plex-media` claim is mounted read-write at `/media`. The `exportarr` block exists but is disabled, so no scrape annotation renders. The Applications move from pattern B (remote chart + value files) to pattern A (local chart path).

**Tech Stack:** Helm 4.2.4 (host), ArgoCD Application CRs, Traefik `traefik.containo.us/v1alpha1` IngressRoute, LinuxServer Bazarr `1.6.2`, `local-path` and NFS subdir provisioners.

**Spec:** `docs/superpowers/specs/2026-11-13-bazarr-chart-refactor-design.md`

## Global Constraints

- `image.tag` is `1.6.2`, `appVersion: "1.6.2"`. Never `latest`, never an empty tag.
- `strategy: Recreate`; `nodeSelector: kubernetes.io/hostname: streaming`.
- Config claim is `<fullname>-config`, `storageClassName: local-path`, `128Mi`, mounted at `/config`.
- Media claim is `pvc-plex-media`, mounted at `/media`, `readOnly: false`.
- Service is ClusterIP on 6767/TCP; container port defaults to the service port.
- IngressRoute is `traefik.containo.us/v1alpha1`, `entryPoints: [web]`, hosts `bazarr.kub.local` and `bazarr-anime.kub.local`, no TLS, no redirect middleware.
- Environment is inline `env:` in the Deployment. No ConfigMap.
- `exportarr.enabled: false`. No `prometheus.io/*` annotation may render in this state.
- LinuxServer env: `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`. No `runAsNonRoot`, `runAsUser` or `fsGroup`; `allowPrivilegeEscalation: false` and `seccompProfile: RuntimeDefault`; capabilities kept for the s6 init.
- No tabs anywhere. No `networking.k8s.io` object.
- Every file under `cluster/argocd/**` uses CRLF line endings.
- These charts have no unit tests. `helm lint`, `helm template` and the greps in each task are the gate.
- Commit prefixes: `📝 docs:`, `🚀 feat:`, `🔧 chore:`.

## Review Focus

1. **A scrape annotation while the exporter is off** creates a permanently down Prometheus target, the `unpackerr` failure. Pinned in Tasks 2 and 3 by grepping the rendered output for `prometheus.io`, which must be empty.
2. **The `bazarr-anime` Deployment selector change is immutable.** Syncing without deleting the old Deployment fails. Pinned in Task 7 by the handover body that deletes `deployment/bazarr-anime` before the first sync.
3. **CRLF lost in the Application files** breaks the repo convention and shows every line as changed. Pinned in Task 4 by `file` reporting CRLF and by `git diff --stat` staying small.
4. **Enabling exportarr v3 with the v2 variable name** (`APIKEY` instead of `API_KEY`) leaves the sidecar unauthenticated. Pinned by the values comment in Task 2, the README note in Task 2, and the doc note in Task 6.
5. **A `local-path` claim matching the old Terminating `longhorn-static` PV** instead of provisioning fresh. Pinned in Task 7 by checking the bound claim's `storageClassName` after sync.

---

### Task 1: Branch and record the design

**Files:**
- Create: `docs/superpowers/plans/2026-11-13-bazarr-chart-refactor.md` (this file)
- Existing: `docs/superpowers/specs/2026-11-13-bazarr-chart-refactor-design.md` (untracked)

**Interfaces:**
- Consumes: nothing.
- Produces: branch `feature/bazarr-chart-refactor`, based on `origin/develop` (`f518615f`), with both documents committed.

- [ ] **Step 1: Create the feature branch**

The session is in worktree `shiny-meadow` on a detached HEAD that is already `origin/develop`.

```bash
git rev-parse HEAD origin/develop   # both must print f518615fd4b700c3b43ee90ad7908222a094a8a1
git switch -c feature/bazarr-chart-refactor
```

Expected: `HEAD is now at f518615f` (or no output) and `git status -sb` prints
`## feature/bazarr-chart-refactor`.

- [ ] **Step 2: Commit the design and the plan**

```bash
git add docs/superpowers/specs/2026-11-13-bazarr-chart-refactor-design.md \
        docs/superpowers/plans/2026-11-13-bazarr-chart-refactor.md
git commit -m "📝 docs(bazarr): add the chart refactor design and plan"
```

Expected: one commit, two files added.

---

### Task 2: Build the `bazarr` chart

**Files:**
- Create: `cluster/helm/media-stack/bazarr/Chart.yaml`
- Create: `cluster/helm/media-stack/bazarr/values.yaml`
- Create: `cluster/helm/media-stack/bazarr/README.md`
- Create: `cluster/helm/media-stack/bazarr/templates/_helpers.tpl`
- Create: `cluster/helm/media-stack/bazarr/templates/deployment.yaml`
- Create: `cluster/helm/media-stack/bazarr/templates/service.yaml`
- Create: `cluster/helm/media-stack/bazarr/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/bazarr/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/bazarr/templates/ingressroute.yaml`
- Create: `cluster/helm/media-stack/bazarr/templates/NOTES.txt`
- Modify: `cluster/helm/media-stack/bazarr/values.yaml` (the old values-only file is replaced)

**Interfaces:**
- Consumes: `cluster/helm/media-stack/sonarr` as the template and values source.
- Produces for Task 3: a chart whose templates are copied verbatim and whose values differ only in `name`/`host`/`claim`. The values keys Task 4 relies on are `image.repository`, `image.tag`, `service.port`, `ingressRoute.host`, `storage.className`, `storage.size`, `media.claim`, `media.mountPath`, `exportarr.enabled`, `nodeSelector`, `probes.*.path`.

- [ ] **Step 1: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: bazarr
description: Bazarr is the subtitle manager for Sonarr and Radarr. It watches both libraries, finds missing subtitles and writes them next to the videos.
type: application
# Chart version. Renovate bumps this when the image tag moves.
version: 1.0.0
# Must match image.tag in values.yaml.
appVersion: "1.6.2"
icon: https://www.bazarr.media/assets/img/logo.png
home: https://www.bazarr.media/
sources:
  - https://github.com/morpheus65535/bazarr
keywords:
  - subtitles
  - pvr
  - automation
```

- [ ] **Step 2: Write `values.yaml` from the `sonarr` values**

Copy `cluster/helm/media-stack/sonarr/values.yaml`, then apply this delta. Keep every other key unchanged, including `replicaCount`, `strategy`, `nameOverride`, `fullnameOverride`, `imagePullSecrets`, `serviceAccount`, `podAnnotations`, `podLabels`, `podSecurityContext`, `securityContext`, `service.type`, `container.port`, the `ingressRoute` block except `host`, `extraEnv`, `envFrom`, `tolerations`, `affinity`.

| Key | Value |
|---|---|
| `image.repository` | `ghcr.io/linuxserver/bazarr` |
| `image.tag` | `"1.6.2"` |
| `service.port` | `6767` |
| `ingressRoute.host` | `bazarr.kub.local` |
| `env` | `TZ: "Europe/Paris"`, `PUID: "1100"`, `PGID: "1100"` |
| `downloads` | delete the block and its comment |
| `media.mountPath` | `/media` |
| `media.claim` | `pvc-plex-media` |
| `media.readOnly` | `false` |
| `exportarr.enabled` | `false` |
| `exportarr.image` | `ghcr.io/onedr0p/exportarr:v2.3.0`, `IfNotPresent` |
| `exportarr.port` | `9707` |
| `exportarr.apiKey` | `secretName: ""`, `secretKey: secret` |
| `exportarr.resources` | requests `2m`/`56Mi`, limits `112Mi` |
| `resources` | requests `10m`/`256Mi`, limits `768Mi` |
| `probes.startup` | `enabled: true`, `path: /`, `periodSeconds: 5`, `timeoutSeconds: 5`, `failureThreshold: 60` |
| `probes.liveness` | `enabled: true`, `path: /`, `initialDelaySeconds: 20`, `periodSeconds: 30`, `timeoutSeconds: 5`, `failureThreshold: 3` |
| `probes.readiness` | `enabled: true`, `path: /`, `initialDelaySeconds: 10`, `periodSeconds: 15`, `timeoutSeconds: 5`, `failureThreshold: 3` |
| `nodeSelector` | `kubernetes.io/hostname: streaming` |
| `storage.size` | `128Mi` |
| `storage.className` | `local-path` |

Rewrite the comments around these decisions:

- Image: LinuxServer image, starts as root, prepares `/config`, drops to `PUID`/`PGID`; why that rules out `runAsNonRoot`.
- `env`: Bazarr's logger has only INFO and DEBUG, and no environment variable selects either. Do **not** add a log-level key. Point at the chart README's exception.
- `exportarr`: off because v2.3.0's Bazarr collector walks every episode and takes tens of seconds at the 60 s scrape interval; `DISABLE_EPISODE_METRICS` is unreleased. Enabling it needs exportarr v3, which also renames the sidecar's `APIKEY` variable to `API_KEY`.
- `resources`: no measurement exists (the pod has been Pending since 2026-09-02). Same house pair as the Sonarr charts; re-measure after it runs.
- `probes`: `/` is the only unauthenticated endpoint; the API's status endpoint needs the API key, which a probe cannot read; readiness shares it by the documented exception.

- [ ] **Step 3: Copy the five app-independent templates verbatim**

```bash
cp cluster/helm/media-stack/sonarr/templates/_helpers.tpl \
   cluster/helm/media-stack/sonarr/templates/service.yaml \
   cluster/helm/media-stack/sonarr/templates/serviceaccount.yaml \
   cluster/helm/media-stack/sonarr/templates/persistentvolumeclaim.yaml \
   cluster/helm/media-stack/sonarr/templates/ingressroute.yaml \
   cluster/helm/media-stack/sonarr/templates/NOTES.txt \
   cluster/helm/media-stack/bazarr/templates/
```

No name change: the helpers are unprefixed (`app.*`) by contract.

- [ ] **Step 4: Copy `deployment.yaml` and apply the app-specific delta**

```bash
cp cluster/helm/media-stack/sonarr/templates/deployment.yaml \
   cluster/helm/media-stack/bazarr/templates/deployment.yaml
```

Then:

1. Replace "Sonarr" with "Bazarr" in the template comments.
2. In `volumeMounts`, keep the conditional `config` mount and the `media` mount, and delete the `downloads` entry.
3. In `volumes`, keep the conditional `config` volume and the `media` volume, and delete the `downloads` volume.
4. In the exporter sidecar, keep `args: [bazarr]`. Leave the env name `APIKEY` as it is: that is the v2.3.0 spelling, and the values comment in Step 2 records the v3 rename.
5. Leave the annotation block guarded by `.Values.exportarr.enabled` exactly as it is: with the block off, no annotation renders.

- [ ] **Step 5: Write the chart README**

Follow the section order of `cluster/helm/media-stack/sonarr/README.md` and use these facts:

- Title `# bazarr`; internal-only `bazarr.kub.local` on the `web` entry point, no TLS, no auth.
- Deployed by ArgoCD from `cluster/argocd/projects/media-stack/bazarr.yaml`; release name `bazarr`; five objects: Deployment, Service (6767), ServiceAccount, claim `bazarr-config`, IngressRoute.
- Differences table: one media claim instead of the Sonarr pair (`pvc-plex-media` at `/media`); the exporter is present but off; probes on `/`; no log-level variable.
- **Documented exceptions:** probes share `/` and why; Bazarr logs at INFO only (no warning level, no env variable), with the UI history and the future exporter as the operational record; the exporter is off until exportarr v3 and the v3 rename; the image starts as root then drops to 1100, so no `runAsNonRoot`, no `runAsUser`, no `fsGroup`.
- **Media:** the claim is read-write because Bazarr writes subtitle files next to the videos.
- **Secrets and the exporter:** no Secret is needed while the block is off. To enable later: paste `BAZARR_API_KEY` from Settings → General → API Key, create Secret `bazarr` with key `secret`, set `exportarr.enabled: true`, add the v3 image tag and `DISABLE_EPISODE_METRICS`, and change the sidecar env from `APIKEY` to `API_KEY`.
- **Upgrading and rolling back:** the config database migrates forward on first start and not back; take a copy of `/config` before an image bump.
- No claim that the chart can grow the 128Mi config volume: `local-path` cannot expand it.

- [ ] **Step 6: Lint the chart**

```bash
helm lint cluster/helm/media-stack/bazarr -n media-stack
```

Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 7: Render the chart and check the object set**

```bash
helm template bazarr cluster/helm/media-stack/bazarr -n media-stack > /tmp/bazarr.yaml
grep -E '^kind:|^  name:' /tmp/bazarr.yaml
```

Expected, in some order: `ServiceAccount bazarr`, `PersistentVolumeClaim bazarr-config`, `Service bazarr`,
`Deployment bazarr`, `IngressRoute bazarr`. No `ConfigMap`.

- [ ] **Step 8: Run the contract greps against the render and the chart**

```bash
grep -c 'prometheus.io' /tmp/bazarr.yaml                          # expect 0
grep -c 'node-type' /tmp/bazarr.yaml                              # expect 0
grep -c 'networking.k8s.io' /tmp/bazarr.yaml                      # expect 0
grep -rnP '\t' cluster/helm/media-stack/bazarr/templates || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/bazarr/values.yaml || echo "no floating tags"
grep -n 'storageClassName: local-path' /tmp/bazarr.yaml           # expect one line
grep -n 'mountPath: /media' /tmp/bazarr.yaml                       # expect one line
grep -n 'Host(`bazarr.kub.local`)' /tmp/bazarr.yaml
```

Expected: `0`, `0`, `0`, `no tabs`, `no floating tags`, and one hit for each of the last three.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/media-stack/bazarr
git commit -m "🚀 feat(bazarr): rebuild the chart to the repo contract"
```

---

### Task 3: Build the `bazarr-anime` chart

**Files:**
- Create: `cluster/helm/media-stack/bazarr-anime/Chart.yaml`
- Create: `cluster/helm/media-stack/bazarr-anime/values.yaml`
- Create: `cluster/helm/media-stack/bazarr-anime/README.md`
- Create: `cluster/helm/media-stack/bazarr-anime/templates/` (the same seven files)
- Modify: `cluster/helm/media-stack/bazarr-anime/values.yaml` (the old values-only file is replaced)

**Interfaces:**
- Consumes: the complete `bazarr` chart from Task 2.
- Produces for Task 4: object names `bazarr-anime`, `bazarr-anime-config`, host `bazarr-anime.kub.local`, chart name `bazarr-anime`.

- [ ] **Step 1: Copy the chart directory**

```bash
cp -a cluster/helm/media-stack/bazarr/. cluster/helm/media-stack/bazarr-anime/
```

This overwrites the old values-only `values.yaml`.

- [ ] **Step 2: Change the identity**

In `Chart.yaml`:

- `name: bazarr-anime`
- `description: A second Bazarr instance for the anime library. Same image and shape as bazarr, with its own config claim, host and API key.`

In `values.yaml`:

- `ingressRoute.host: bazarr-anime.kub.local`

Everything else — image, port, storage, media, env, resources, probes, exporter block, node selector — stays identical.

- [ ] **Step 3: Write the chart README**

Copy the `bazarr` README and change: title `# bazarr-anime`; host `bazarr-anime.kub.local`; Application path `cluster/argocd/projects/media-stack/bazarr-anime.yaml`; claim `bazarr-anime-config`; Secret name `bazarr-anime`; key `BAZARR_ANIME_API_KEY`. Add one sentence: the anime instance is identical to `bazarr` except for name, host and key, and is a separate chart because the repo forbids a shared chart.

- [ ] **Step 4: Lint and render**

```bash
helm lint cluster/helm/media-stack/bazarr-anime -n media-stack
helm template bazarr-anime cluster/helm/media-stack/bazarr-anime -n media-stack > /tmp/bazarr-anime.yaml
grep -E '^kind:|^  name:' /tmp/bazarr-anime.yaml
```

Expected: lint passes; objects are `ServiceAccount bazarr-anime`, `Service bazarr-anime`,
`PersistentVolumeClaim bazarr-anime-config`, `Deployment bazarr-anime`, `IngressRoute bazarr-anime`.

- [ ] **Step 5: Run the same contract greps**

```bash
grep -c 'prometheus.io' /tmp/bazarr-anime.yaml        # expect 0
grep -c 'node-type' /tmp/bazarr-anime.yaml            # expect 0
grep -c 'networking.k8s.io' /tmp/bazarr-anime.yaml    # expect 0
grep -rnP '\t' cluster/helm/media-stack/bazarr-anime/templates || echo "no tabs"
grep -rn 'latest' cluster/helm/media-stack/bazarr-anime/values.yaml || echo "no floating tags"
grep -n 'bazarr-anime.kub.local' /tmp/bazarr-anime.yaml
grep -n 'name: bazarr-config' /tmp/bazarr-anime.yaml || echo "no claim from the first chart"
```

Expected: three zeros, `no tabs`, `no floating tags`, the host hit, and `no claim from the first chart`.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/media-stack/bazarr-anime
git commit -m "🚀 feat(bazarr-anime): rebuild the chart to the repo contract"
```

---

### Task 4: Switch both Applications to the local charts

**Files:**
- Modify: `cluster/argocd/projects/media-stack/bazarr.yaml`
- Modify: `cluster/argocd/projects/media-stack/bazarr-anime.yaml`

**Interfaces:**
- Consumes: chart paths from Tasks 2 and 3.
- Produces: two Applications with a single `source:` and a local `path:`.

- [ ] **Step 1: Replace the `sources:` block in `bazarr.yaml`**

The file uses CRLF. Use the `edit` tool so the line endings are preserved; do not rewrite the file with `write`.

Replace:

```yaml
  sources:
    - repoURL: 'https://vyrtualsynthese.github.io/selfhosted-helmcharts/'
      chart: bazarr
      targetRevision: 0.1.3
      helm:
        valueFiles:
          - $values/cluster/helm/media-stack/bazarr/values.yaml
    - repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
      targetRevision: develop
      ref: values
```

with:

```yaml
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/media-stack/bazarr
```

- [ ] **Step 2: Do the same in `bazarr-anime.yaml`**

Use the same block with `path: cluster/helm/media-stack/bazarr-anime` and the
`bazarr-anime/values.yaml` value file removed.

- [ ] **Step 3: Verify line endings, YAML and paths**

```bash
file cluster/argocd/projects/media-stack/bazarr.yaml cluster/argocd/projects/media-stack/bazarr-anime.yaml
# both must say "with CRLF line terminators"
python3 - <<'PY'
import yaml
for p in ("cluster/argocd/projects/media-stack/bazarr.yaml",
          "cluster/argocd/projects/media-stack/bazarr-anime.yaml"):
    d = yaml.safe_load(open(p))
    print(p, d["spec"]["source"]["path"])
PY
for p in cluster/helm/media-stack/bazarr cluster/helm/media-stack/bazarr-anime; do
  [ -d "$p" ] && echo "OK $p" || echo "BROKEN $p"
done
grep -c 'sources:' cluster/argocd/projects/media-stack/bazarr*.yaml   # expect 0 in both
```

Expected: two CRLF reports, two printed paths, two `OK`, and `0` twice.

- [ ] **Step 4: Commit**

```bash
git add cluster/argocd/projects/media-stack/bazarr.yaml \
        cluster/argocd/projects/media-stack/bazarr-anime.yaml
git commit -m "♻️ refactor(argocd): point the bazarr applications at the local charts"
```

---

### Task 5: Add the two API-key slots to `homelab.env`

**Files:**
- Modify: `~/.config/secrets/homelab.env` (outside git, mode 600)

**Interfaces:**
- Produces: `BAZARR_API_KEY` and `BAZARR_ANIME_API_KEY`, empty, for the future Secret creation in Task 7's handover.

- [ ] **Step 1: Insert the two blocks before the Prowlarr block**

The recent *arr keys are written without an `export ` prefix; match them. Do not paste any value.

```bash
# Bazarr 1.6.2 (bazarr instance) - Settings -> General -> API Key. Sent as the
# X-API-Key header. API base: http://bazarr.kub.local/api
# Used to restore the `bazarr` Secret when the exportarr sidecar is enabled;
# the sidecar is off until exportarr v3 ships.
BAZARR_API_KEY=

# Bazarr 1.6.2 (bazarr-anime instance) - Settings -> General -> API Key.
# API base: http://bazarr-anime.kub.local/api
# Used to restore the `bazarr-anime` Secret when the exportarr sidecar is enabled.
BAZARR_ANIME_API_KEY=
```

- [ ] **Step 2: Verify the slots without printing values**

```bash
grep -cE '^BAZARR(_ANIME)?_API_KEY=' ~/.config/secrets/homelab.env   # expect 2
stat -c '%a' ~/.config/secrets/homelab.env                            # expect 600
grep -n 'BAZARR' ~/.config/secrets/homelab.env | sed 's/=.*/=<redacted>/'
```

Expected: `2`, `600`, and four comment lines plus the two empty assignments.

- [ ] **Step 3: No commit**

This file is outside the repository and must never be committed. Confirm `git status --porcelain` does
not mention it.

---

### Task 6: Update the repo documentation

**Files:**
- Modify: `docs/charts/README.md` (hardening table, worked examples)
- Modify: `AGENTS.md` (chart count, pattern B example, pattern B list, secret table)

**Interfaces:**
- Consumes: the chart decisions from Tasks 2 and 3.
- Produces: the repo's written record of the pair, including the metrics debt.

- [ ] **Step 1: Add the pair to the hardening table**

In `docs/charts/README.md`, after the `radarr-anime` row, add:

```markdown
| `bazarr` | yes, on `/` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — the image starts as root, then drops to 1100** |
| `bazarr-anime` | same as `bazarr` | same | **no — the image starts as root, then drops to 1100** |
```

- [ ] **Step 2: Add the pair to "What good looks like"**

After the `radarr` paragraph, add a paragraph with these facts:

- `bazarr` and `bazarr-anime` as of 2026-11-13: five objects each, one config claim and the shared
  `pvc-plex-media` at `/media`, probes on `/` with a 300 s startup budget.
- The `exportarr` block is present and off. Exportarr v2.3.0's Bazarr collector walks every episode
  and takes tens of seconds; the 60 s scrape interval would overlap, and the flag that removes the
  walk (`DISABLE_EPISODE_METRICS`) is unreleased. No scrape annotation renders while the block is
  off, so Bazarr is absent from the target list rather than present and down.
- Bazarr has no warning log level and no variable that selects one; the chart leaves the level alone
  and records the INFO volume as accepted debt.

- [ ] **Step 3: Update `AGENTS.md` counts**

- `49 Helm charts under cluster/helm/` becomes `51 Helm charts under cluster/helm/`.

- [ ] **Step 4: Update the pattern B sample and list**

In §3.B, replace the `bazarr` sample with the current `calibre` example: `chart: calibre`,
`targetRevision: 0.1.2`, `$values/cluster/helm/media-stack/calibre/values.yaml`.

In the sentence that follows, remove `bazarr, bazarr-anime` from the example list and change
`these ten are converted separately` to `these eight are converted separately`.

- [ ] **Step 5: Add the Secret row**

In §5, after the first `media-stack` row, add:

```markdown
| media-stack | `bazarr`, `bazarr-anime` | `secret` → exportarr APIKEY (only when `exportarr.enabled`; off until exportarr v3) |
```

- [ ] **Step 6: Verify the documentation edits**

```bash
sed -n '/### B\./,/### C\./p' AGENTS.md | grep -n 'bazarr' || echo "pattern B clean"
grep -n '51 Helm charts' AGENTS.md
grep -n 'these eight' AGENTS.md
grep -n 'bazarr' docs/charts/README.md | tail -6
```

Expected: `pattern B clean`, the count is `51`, the list says `eight`, and the contract has the two
hardening rows plus the worked example.

- [ ] **Step 7: Commit**

```bash
git add docs/charts/README.md AGENTS.md
git commit -m "📝 docs(charts): record the bazarr pair and its metrics debt"
```

---

### Task 7: Full verification and handover

**Files:** none beyond Tasks 1–6.

**Interfaces:**
- Consumes: everything above.
- Produces: a pushed branch and a PR against `develop`, with the post-sync runbook.

- [ ] **Step 1: Run the repo-wide chart check**

```bash
for c in cluster/helm/media-stack/bazarr cluster/helm/media-stack/bazarr-anime; do
  helm lint "$c" -n media-stack || echo "LINT FAIL $c"
  helm template test "$c" -n media-stack >/dev/null || echo "RENDER FAIL $c"
done
echo "chart check done"
```

Expected: two clean lints, two clean renders, `chart check done`, no `FAIL` line.

- [ ] **Step 2: Run the ArgoCD reference and CRLF checks**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")
file cluster/argocd/projects/media-stack/bazarr.yaml \
     cluster/argocd/projects/media-stack/bazarr-anime.yaml
```

Expected: no `BROKEN PATH`, both files report CRLF.

- [ ] **Step 3: Review the branch's history and diff**

```bash
git log --oneline origin/develop..HEAD
git diff --stat origin/develop..HEAD
git status --porcelain
```

Expected: the five commits from Tasks 1–6 (`docs`, `feat` ×2, `refactor`, `docs`), a diff limited to
the two chart directories, the two Applications, `docs/charts/README.md` and `AGENTS.md`, and a clean
status. `~/.config/secrets/homelab.env` must not appear.

- [ ] **Step 4: Rebase on `origin/develop` and re-run the gate**

```bash
git fetch origin
git rebase origin/develop
for c in cluster/helm/media-stack/bazarr cluster/helm/media-stack/bazarr-anime; do
  helm lint "$c" -n media-stack && helm template test "$c" -n media-stack >/dev/null || echo "FAIL $c"
done
```

Expected: rebase succeeds (or is a no-op), the gate passes again with zero failures.

- [ ] **Step 5: Push**

```bash
git push -u origin feature/bazarr-chart-refactor
```

Expected: a new remote branch at the rebased HEAD.

- [ ] **Step 6: Open the PR against `develop`**

Use `github_create_pull_request` with base `develop` and this body, filled with the real commit list:

```markdown
## What

Rebuild both Bazarr instances on local charts that follow `docs/charts/README.md`, and move their
ArgoCD Applications from the stalled `selfhosted-helmcharts` chart to the local paths.

- `cluster/helm/media-stack/bazarr` and `bazarr-anime`: five objects each, config claim on
  `local-path` (128Mi), `pvc-plex-media` at `/media` read-write, IngressRoute on `web`.
- Image `1.6.2`.
- `exportarr` block present, `enabled: false` until exportarr v3; no scrape annotation renders.
- `homelab.env` gains empty `BAZARR_API_KEY` / `BAZARR_ANIME_API_KEY` (not in this PR).

## After merge (operator)

1. Delete `deployment/bazarr-anime` (selector is immutable) or sync with Replace.
2. Sync both Applications **with prune** — auto-sync is off by design.
3. Check:
   ```bash
   kubectl -n media-stack get pods -l app.kubernetes.io/name=bazarr
   kubectl -n media-stack get pods -l app.kubernetes.io/name=bazarr-anime
   kubectl -n media-stack get pvc bazarr-config bazarr-anime-config -o custom-columns=NAME:.metadata.name,SC:.spec.storageClassName,STATUS:.status.phase
   kubectl -n media-stack get ingressroute bazarr bazarr-anime
   curl -sI http://bazarr.kub.local | head -1
   curl -sI http://bazarr-anime.kub.local | head -1
   ```
   Expected: one Running pod per instance, both claims `Bound` with `local-path`, both routes present,
   both hosts answer.
4. When exportarr v3 ships: paste each key from Bazarr → Settings → General into `homelab.env`,
   create Secret `<instance>` with key `secret`, set `exportarr.enabled: true`, pin the v3 tag,
   rename the sidecar env `APIKEY` → `API_KEY`, add `DISABLE_EPISODE_METRICS=true`, restart.

## Test

`helm lint` and `helm template` pass for both charts; contract greps clean; no scrape annotation while
the exporter is off.
```

- [ ] **Step 7: Report the handover**

Tell the operator: PR URL, the two files that need a pre-sync delete/sync with prune, and the
post-sync verification list. Do not merge, do not sync, do not create Secrets.
