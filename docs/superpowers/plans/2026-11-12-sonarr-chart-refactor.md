# sonarr and sonarr-anime chart refactor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the `sonarr` and `sonarr-anime` charts on the repo chart contract, bump Sonarr to
`4.0.20.3014-ls4`, size memory from measurement, add `warn` logging and `/ping` probes, and fix the
exporter target that has pointed at port 7878 since it was written.

**Architecture:** Two independent charts in the shape of `cluster/helm/media-stack/prowlarr`:
`app.*` naming helpers, one values block per concern, a Deployment with an `exportarr` sidecar, a
Service, a ServiceAccount, a `local-path` config claim, and a Traefik IngressRoute whose API group
and host come from values. Live object names do not change, so ArgoCD updates in place.

**Tech Stack:** Helm 4.2.4, Kubernetes YAML, ArgoCD. No CI; verification is `helm lint`,
`helm template` and grep assertions. `kubeconform` is not installed on this machine — the contract
mentions it, but the check is skipped and that is recorded in the PR.

**Spec:** `docs/superpowers/specs/2026-11-12-sonarr-chart-refactor-design.md`

## Global Constraints

- Image `ghcr.io/linuxserver/sonarr:4.0.20.3014-ls4` in both charts; `appVersion: "4.0.20.3014-ls4"`;
  chart `version: 1.0.0`.
- Exporter image `ghcr.io/onedr0p/exportarr:v2.3.0` (newest release), port 9707.
- Release names stay `sonarr` and `sonarr-anime`; objects render as `sonarr`, `sonarr-anime`,
  `sonarr-config`, `sonarr-anime-config`.
- Config claim: `<fullname>-config`, `local-path`, `ReadWriteOnce`, `128Mi`, mounted at `/config`.
- Media claims: `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` at `/downloads`,
  `pvc-plex-media` at `/movies`, both read-write.
- Service port 8989, port named `http`; exporter target `http://<fullname>:8989`.
- Env: `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`, `SONARR__LOG__LEVEL=warn`,
  `SONARR__LOG__CONSOLELEVEL=warn`.
- Resources: sonarr `10m`/`256Mi` request, `768Mi` memory limit; sonarr-anime `10m`/`384Mi` request,
  `1Gi` limit. No CPU limits.
- Probes on `/ping`: startup 5s × 30, liveness initial 20s / period 30s / 3 failures, readiness
  initial 10s / period 15s / 3 failures.
- Hardening: `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`. Never `runAsNonRoot`
  or `runAsUser` — the LinuxServer s6 init needs root before dropping to 1100.
- IngressRoute: `traefik.containo.us/v1alpha1` held in a value, `web` entry point, hosts
  `sonarr.kub.local` / `sonarr-anime.kub.local`, no TLS, no middleware.
- No tabs, no `latest`, no `networking.k8s.io`, no references to `sonarr-webui` or `32400`.
- `~/.config/secrets/homelab.env` already has empty `SONARR_API_KEY` and `SONARR_ANIME_API_KEY`;
  it is outside git and is never committed.

## Review Focus

- **Claim name drift is silent data loss.** The render must contain `claimName: sonarr-config` (and
  `sonarr-anime-config`), and `--set fullnameOverride=demo` must produce `demo-config`. Anything else
  creates a new empty claim and Sonarr starts with an empty library.
- **The exporter target is the bug this change exists to fix.** The render must contain
  `value: "http://sonarr:8989"` and `value: "http://sonarr-anime:8989"`, including under
  `fullnameOverride`, and must contain no `7878`.
- **The IngressRoute must reference the port numerically.** A named port that does not exist on the
  Service renders a route with no endpoints; assert `port: 8989` in both the Service and the route.
- **The log level hides everything if the variable names are wrong.** Assert both
  `SONARR__LOG__LEVEL` and `SONARR__LOG__CONSOLELEVEL` are present with value `warn`; Sonarr silently
  ignores unknown environment variables.
- **A non-root UID starts then fails on writes.** The render must contain no `runAsUser`,
  `runAsNonRoot` or `fsGroup`, because the claims are root-owned hostPath directories.
- **The image must be a release tag, not the series tag.** Assert `4.0.20.3014-ls4` and that
  `image.tag` equals `Chart.yaml`'s `appVersion`.

---

### Task 0: Commit the approved design and this plan

**Files:**
- Add: `docs/superpowers/specs/2026-11-12-sonarr-chart-refactor-design.md`
- Add: `docs/superpowers/plans/2026-11-12-sonarr-chart-refactor.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the documents every later task is measured against.

- [ ] **Step 1: Confirm the branch**

Run: `git branch --show-current`
Expected: `feature/sonarr-chart-refactor`

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-11-12-sonarr-chart-refactor-design.md \
        docs/superpowers/plans/2026-11-12-sonarr-chart-refactor.md
git commit -m "📝 docs(sonarr): add chart refactor design and plan"
```

---

### Task 1: Rebuild the sonarr chart

**Files:**
- Modify: `cluster/helm/media-stack/sonarr/Chart.yaml`
- Modify: `cluster/helm/media-stack/sonarr/values.yaml`
- Modify: `cluster/helm/media-stack/sonarr/templates/_helpers.tpl`
- Modify: `cluster/helm/media-stack/sonarr/templates/deployment.yaml`
- Modify: `cluster/helm/media-stack/sonarr/templates/service.yaml`
- Modify: `cluster/helm/media-stack/sonarr/templates/ingress.yaml` → rename to `ingressroute.yaml`
- Modify: `cluster/helm/media-stack/sonarr/templates/pvc-config.yaml` → rename to `persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/sonarr/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/sonarr/templates/NOTES.txt`
- Create: `cluster/helm/media-stack/sonarr/README.md`

**Interfaces:**
- Consumes: `cluster/helm/media-stack/prowlarr` as the object-shape reference (`_helpers.tpl`,
  `deployment.yaml`, `service.yaml`, `serviceaccount.yaml`, `persistentvolumeclaim.yaml`,
  `ingressroute.yaml`, `NOTES.txt`); the contract in `docs/charts/README.md`.
- Produces: values keys `nameOverride`, `fullnameOverride`, `replicaCount`, `strategy`, `image.*`,
  `imagePullSecrets`, `serviceAccount.*`, `podAnnotations`, `podLabels`, `podSecurityContext`,
  `securityContext`, `service.*`, `container.port`, `ingressRoute.*`, `storage.*`, `env`,
  `extraEnv`, `envFrom`, `downloads.*`, `media.*`, `exportarr.*`, `resources.*`, `probes.*`,
  `nodeSelector`, `tolerations`, `affinity`; rendered object names `sonarr` (Deployment, Service,
  ServiceAccount, IngressRoute) and `sonarr-config` (PVC).

- [ ] **Step 1: Prove the current chart fails the checks this task will make pass**

```bash
helm template sonarr cluster/helm/media-stack/sonarr -n media-stack > /tmp/sonarr-before.yaml
grep -c 'http://sonarr:7878' /tmp/sonarr-before.yaml          # expect 1
grep -c 'livenessProbe' /tmp/sonarr-before.yaml               # expect 0
grep -c 'seccompProfile' /tmp/sonarr-before.yaml              # expect 0
grep -c 'namespace: sonarr-config' /tmp/sonarr-before.yaml; grep -c 'name: sonarr-config' /tmp/sonarr-before.yaml
grep -c 'SONARR__LOG__LEVEL' /tmp/sonarr-before.yaml          # expect 0
grep -c 'port: 32400' /tmp/sonarr-before.yaml                 # expect 1
```
Expected: 7878 present, zero probes, zero hardening, zero log level, the bogus 32400 port.

- [ ] **Step 2: Write `Chart.yaml`**

`apiVersion: v2`, `name: sonarr`, description “Sonarr is a PVR for TV series: it watches indexers,
grabs releases and files them into the library Plex reads.”, `type: application`, `version: 1.0.0`,
`appVersion: "4.0.20.3014-ls4"`, `icon: https://raw.githubusercontent.com/Sonarr/Sonarr/develop/Logo/1024.png`,
`home: https://sonarr.tv/`, `sources: [https://github.com/Sonarr/Sonarr]`,
`keywords: [pvr, tv, torrent, usenet, automation]`.

- [ ] **Step 3: Write `values.yaml`**

Copy `cluster/helm/media-stack/prowlarr/values.yaml` as the base, keep its comment style, and change
only these keys:

- `image.repository: ghcr.io/linuxserver/sonarr`, `image.tag: "4.0.20.3014-ls4"`.
- `service.port: 8989`.
- `ingressRoute.host: sonarr.kub.local`.
- `env` adds `SONARR__LOG__LEVEL: "warn"` and `SONARR__LOG__CONSOLELEVEL: "warn"` next to `TZ`,
  `PUID`, `PGID`, with a one-line comment that the console level is the copy Fluent Bit reads.
- `resources`: requests `cpu: 10m`, `memory: 256Mi`; limits `memory: 768Mi`. Comment the numbers as
  measured over 30 days: average 216Mi, peak 299Mi, CPU p95 31m.
- `probes`: same three blocks and values as the Global Constraints, all `path: /ping`.
- Replace prowlarr's `volumes`/`volumeMounts` lists with two named blocks:

```yaml
downloads:
  claim: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner
  mountPath: /downloads
  readOnly: false

media:
  claim: pvc-plex-media
  mountPath: /movies
  readOnly: false
```

- Keep `exportarr` with `image.tag: "v2.3.0"`, `port: 9707`, `apiKey.secretKey: secret`,
  `secretName: ""`, and the same resources. Comment that the Secret defaults to the release name
  (`sonarr`) and that its `secret` key must hold the API key.

- [ ] **Step 4: Write `templates/_helpers.tpl`**

Copy prowlarr's file verbatim. It already provides `app.name`, `app.fullname`, `app.chart`,
`app.labels`, `app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName`,
`app.containerPort` and `app.exportarrSecretName`.

- [ ] **Step 5: Write `templates/deployment.yaml`**

Copy prowlarr's `deployment.yaml` and change:

- Main container name stays `{{ .Chart.Name }}`, port name `http`, container port from
  `app.containerPort`.
- Volumes: config (as prowlarr), then `{{ .Values.downloads.mountPath }}` from
  `persistentVolumeClaim.claimName: {{ .Values.downloads.claim }}`, then the same for `media`.
  Mount each named `downloads` / `media` with its `mountPath` and `readOnly`.
- Exporter sidecar env `URL` becomes
  `value: "http://{{ include "app.fullname" . }}:{{ .Values.service.port }}"`; keep `PORT`,
  `APIKEY` from `app.exportarrSecretName` / `exportarr.apiKey.secretKey`, and `args: [sonarr]`.
- Scrape annotations stay rendered from `exportarr.enabled` as in prowlarr, including
  `prometheus.io/path: "/metrics"`.

- [ ] **Step 6: Write `templates/service.yaml`, `serviceaccount.yaml`, `persistentvolumeclaim.yaml`, `ingressroute.yaml`, `NOTES.txt`**

Copy each from prowlarr unchanged; they read only values this chart already defines. Rename the old
`ingress.yaml` and `pvc-config.yaml` files into the new names so no duplicate objects render.

- [ ] **Step 7: Write `README.md`**

Sections, in this order: what Sonarr is and how ArgoCD deploys it (`helm template sonarr ...`); the
differences from the reference (`prowlarr`), as a table — LinuxServer image with root s6 init, two
NFS media mounts, exporter target fixed to 8989, probes shared on `/ping`; documented contract
exceptions (shared readiness/liveness because the only dependency is the SQLite database on
`/config`; no `runAsNonRoot` because of s6); the Secret `sonarr` with key `secret` and how to restore
it from `$SONARR_API_KEY` (`kubectl -n media-stack patch secret sonarr --type merge -p ...`); the
upgrade and rollback note for the one-way 4.0.19 → 4.0.20 database migration; and the claim-size note
(`local-path` cannot grow, hostPath does not enforce the number).

- [ ] **Step 8: Render and assert**

```bash
helm lint cluster/helm/media-stack/sonarr -n media-stack
helm template sonarr cluster/helm/media-stack/sonarr -n media-stack > /tmp/sonarr-after.yaml
grep -c '^kind:' /tmp/sonarr-after.yaml                       # expect 5
grep -c 'port: 8989' /tmp/sonarr-after.yaml                   # expect 2: Service and IngressRoute
grep -c 'value: warn' /tmp/sonarr-after.yaml                  # expect 2: file and console level
grep -q 'claimName: sonarr-config' /tmp/sonarr-after.yaml
grep -q 'value: "http://sonarr:8989"' /tmp/sonarr-after.yaml
grep -q 'SONARR__LOG__LEVEL' /tmp/sonarr-after.yaml
grep -q 'startupProbe' /tmp/sonarr-after.yaml
grep -q 'seccompProfile' /tmp/sonarr-after.yaml
grep -q 'image: "ghcr.io/linuxserver/sonarr:4.0.20.3014-ls4"' /tmp/sonarr-after.yaml
[ "$(grep 'tag:' cluster/helm/media-stack/sonarr/values.yaml | tr -d ' \"' | cut -d: -f2)" = \
  "$(grep '^appVersion:' cluster/helm/media-stack/sonarr/Chart.yaml | tr -d ' \"' | cut -d: -f2)" ]
! grep -q '7878\|32400\|networking.k8s.io\|runAsUser\|fsGroup' /tmp/sonarr-after.yaml
helm template sonarr cluster/helm/media-stack/sonarr -n media-stack --set fullnameOverride=demo \
  | grep -q 'claimName: demo-config'
```
Expected: lint clean, every grep succeeds, the negated grep finds nothing.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/media-stack/sonarr
git commit -m "♻️ refactor(sonarr): align chart with the reference contract"
```

---

### Task 2: Rebuild the sonarr-anime chart

**Files:** every file under `cluster/helm/media-stack/sonarr-anime/`, mirroring Task 1.

**Interfaces:**
- Consumes: Task 1's chart as the copy source; same helpers and template names.
- Produces: the same values keys and template names as Task 1, with the values below.

- [ ] **Step 1: Prove the current chart fails the same checks**

```bash
helm template sonarr-anime cluster/helm/media-stack/sonarr-anime -n media-stack > /tmp/anime-before.yaml
grep -c 'livenessProbe' /tmp/anime-before.yaml      # expect 0
grep -c 'seccompProfile' /tmp/anime-before.yaml     # expect 0
grep -c 'port: 32400' /tmp/anime-before.yaml        # expect 1
```

- [ ] **Step 2: Copy Task 1's chart, then change only these values**

- `Chart.yaml`: `name: sonarr-anime`, description “A second Sonarr instance for the anime library:
  same image and shape as `sonarr`, separate config claim, host and API key.”; version and
  `appVersion` identical to Task 1.
- `values.yaml`: `ingressRoute.host: sonarr-anime.kub.local`; `resources.requests.memory: 384Mi`;
  `resources.limits.memory: 1Gi`; comment 30-day average 302Mi, peak 380Mi, CPU p95 8m.
- `README.md`: same structure as Task 1's, with the anime numbers and the note that it shares the
  NFS claims with `sonarr` but has its own config claim and Secret.
- Templates and helpers: byte-identical to Task 1's.

- [ ] **Step 3: Render and assert**

```bash
helm lint cluster/helm/media-stack/sonarr-anime -n media-stack
helm template sonarr-anime cluster/helm/media-stack/sonarr-anime -n media-stack > /tmp/anime-after.yaml
grep -c '^kind:' /tmp/anime-after.yaml                        # expect 5
grep -c 'port: 8989' /tmp/anime-after.yaml                    # expect 2
grep -c 'value: warn' /tmp/anime-after.yaml                   # expect 2
grep -q 'claimName: sonarr-anime-config' /tmp/anime-after.yaml
grep -q 'value: "http://sonarr-anime:8989"' /tmp/anime-after.yaml
grep -q 'host: sonarr-anime.kub.local' /tmp/anime-after.yaml
grep -q 'memory: 1Gi' /tmp/anime-after.yaml
grep -q 'SONARR__LOG__CONSOLELEVEL' /tmp/anime-after.yaml
! grep -q '7878\|32400\|networking.k8s.io\|runAsUser\|fsGroup' /tmp/anime-after.yaml
helm template sonarr-anime cluster/helm/media-stack/sonarr-anime -n media-stack \
  --set fullnameOverride=demo | grep -q 'claimName: demo-config'
```
Expected: all assertions pass.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/media-stack/sonarr-anime
git commit -m "♻️ refactor(sonarr-anime): align chart with the reference contract"
```

---

### Task 3: Record both charts in the contract page

**Files:**
- Modify: `docs/charts/README.md` (“Probes / Hardening / Non-root UID” table and “What good looks
  like”).

**Interfaces:**
- Consumes: the values and object shapes rendered by Tasks 1 and 2.
- Produces: documentation only.

- [ ] **Step 1: Add two rows to the chart table**

| `sonarr` | yes, on `/ping` | seccomp, no privilege escalation | **no — LinuxServer s6 starts as root, then drops to 1100** |
| `sonarr-anime` | yes, on `/ping` | seccomp, no privilege escalation | **no — LinuxServer s6 starts as root, then drops to 1100** |

- [ ] **Step 2: Add a “What good looks like” paragraph**

State that `sonarr` and `sonarr-anime` (2026-11-12) are the LinuxServer-plus-exporter pair: five
objects each, two named NFS media mounts, probes on `/ping`, and an exporter block that renders the
target, the port and the scrape annotations from one place — the reason the old 7878 target cannot
come back.

- [ ] **Step 3: Commit**

```bash
git add docs/charts/README.md
git commit -m "📝 docs(charts): record the sonarr pair against the contract"
```

---

### Task 4: Whole-branch verification

**Files:** none.

- [ ] **Step 1: Run the repo-wide chart check**

```bash
for c in cluster/helm/media-stack/sonarr cluster/helm/media-stack/sonarr-anime; do
  helm lint "$c" -n media-stack || echo "LINT FAIL $c"
  helm template test "$c" -n media-stack >/dev/null || echo "RENDER FAIL $c"
done
grep -rnP '\t' cluster/helm/media-stack/sonarr*/templates || echo "no tabs"
grep -rn 'latest\|32400\|7878' cluster/helm/media-stack/sonarr*/values.yaml || echo "no stale values"
```
Expected: two clean lints, two clean renders, `no tabs`, `no stale values`.

- [ ] **Step 2: Confirm the chart owns nothing it did not before**

```bash
helm template sonarr cluster/helm/media-stack/sonarr -n media-stack | grep -E '^  name:|^kind:'
```
Expected names: `sonarr` (Deployment), `sonarr` (Service), `sonarr` (ServiceAccount), `sonarr-config`
(PVC), `sonarr` (IngressRoute).

- [ ] **Step 3: Confirm the API-key slots exist without printing values**

```bash
grep -cE '^SONARR(_ANIME)?_API_KEY=' ~/.config/secrets/homelab.env   # expect 2
```
Expected: 2. Both values are still empty until the operator pastes them.

- [ ] **Step 4: Hand the branch to the operator**

Rebase on `origin/develop`, push, open the PR against `develop`, and put the post-sync checks in the
PR body:

```bash
kubectl -n media-stack get pods -l app.kubernetes.io/name=sonarr
kubectl -n media-stack get ingressroute sonarr sonarr-anime
curl -s -H "X-Api-Key: $SONARR_API_KEY" http://sonarr.kub.local/api/v3/system/status | jq .version
curl -s http://prometheus.kub.local/api/v1/targets | jq -r '.data.activeTargets[] | select(.labels.container|test("sonarr")) | "\(.labels.container) \(.health)"'
```
Expected after sync: pods `2/2`, both routes present, version `4.0.20.3014`, both exporter targets
`up`. A target that stays `down` now points at the Secret's API key, not the port.
