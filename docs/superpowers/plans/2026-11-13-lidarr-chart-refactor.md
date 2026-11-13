# Lidarr chart refactor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Lidarr Helm chart on the repo's contract so the app starts on a working config
claim, exports metrics, and keeps its LAN-only route.

**Architecture:** One self-contained chart in `cluster/helm/media-stack/lidarr`, shaped like `sonarr`:
a LinuxServer image that starts as root and drops to `PUID`/`PGID`, an `exportarr` sidecar rendered
from one values block, two named NFS media mounts beside the config claim, and probes on Lidarr's own
`/ping`. The chart creates the config claim; the image's own init prepares `/config`.

**Tech Stack:** Helm 4 (`apiVersion: v2` chart), Traefik v2 CRDs (`traefik.containo.us/v1alpha1`),
Kubernetes Deployment/Service/ServiceAccount/PVC, image `lscr.io/linuxserver/lidarr:3.1.6.5079-ls48`,
sidecar `ghcr.io/onedr0p/exportarr:v2.3.0`.

**Spec:** `docs/superpowers/specs/2026-11-13-lidarr-chart-refactor-design.md`

## Global Constraints

- Chart contract: `docs/charts/README.md`. `appVersion` must equal `image.tag`; every object name
  comes from `app.*` helpers; no tabs anywhere; `helm lint` and `helm template` clean.
- Ingress is a Traefik `IngressRoute`; the API group is a value. Never `networking.k8s.io/v1`.
- Routing: `entryPoints: [web]`, host `lidarr.kub.local`, `pathPrefix: /`, `tls.enabled: false`, no
  middlewares.
- Image: `lscr.io/linuxserver/lidarr`, tag `3.1.6.5079-ls48`, chart `version: 1.0.0`,
  `appVersion: "3.1.6.5079-ls48"`.
- `strategy: Recreate`, claim `<fullname>-config` = `lidarr-config`, `local-path`, `128Mi`,
  `mountPath: /config`.
- Media claims: `downloads` = `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` at
  `/downloads` read-write; `audio` = `pvc-nfs-audio-nfs-subdir-external-provisioner` at `/audio`
  read-write.
- `nodeSelector: kubernetes.io/hostname: streaming`.
- Service/container port `8686`, port name `http`, no `lidarr-webui` anywhere.
- Env: `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`, `LIDARR__LOG__LEVEL=warn`,
  `LIDARR__LOG__CONSOLELEVEL=warn`.
- Security: pod `seccompProfile: RuntimeDefault`; container `allowPrivilegeEscalation: false`; **no**
  `runAsNonRoot`, `runAsUser` or `fsGroup`; capabilities kept (the s6 init needs root).
- Probes on `/ping`: startup `periodSeconds 5`, `failureThreshold 30`; liveness `initialDelaySeconds
  20`, `periodSeconds 30`, `failureThreshold 3`; readiness `initialDelaySeconds 10`,
  `periodSeconds 15`, `failureThreshold 3`; all `timeoutSeconds 5`.
- Exporter: `exportarr.enabled: true`, port `9707`, Secret `lidarr` key `secret`, resources
  `56Mi`/`112Mi`, sidecar args `["lidarr"]`.
- Resources: requests `cpu 10m`, `memory 256Mi`; limits `memory 1Gi`; no CPU limit.
- `~/.config/secrets/homelab.env` is outside git and is never committed.

## Review Focus

- **Claim name drift.** The render must contain `claimName: lidarr-config`, and
  `--set fullnameOverride=demo` must yield `demo-config`. A different name leaves the pod `Pending`
  on a claim that does not exist — the exact failure this change repairs.
- **A read-only audio mount.** Lidarr renames, moves and deletes inside its root folder. If
  `/audio` renders `readOnly: true`, imports fail at write time, long after the pod reports Ready.
- **A non-root UID or `fsGroup`.** The claim is a root-owned hostPath directory; `runAsUser` makes the
  container fail at startup and `fsGroup` silently does nothing. The render must contain none of them.
- **Exporter with no key.** `APIKEY` reads Secret `lidarr` key `secret`. If that Secret is missing the
  sidecar container does not start and the pod is `1/2`; if the key is stale the target answers 401.
  Both point at the Secret, never at the chart.
- **Unknown log-level variables.** Lidarr ignores variables it does not know and silently keeps
  `debug`. Both `LIDARR__LOG__LEVEL` and `LIDARR__LOG__CONSOLELEVEL` must be present with `warn`.

---

### Task 1: Rebuild the chart

**Files:**
- Rewrite: `cluster/helm/media-stack/lidarr/Chart.yaml`
- Rewrite: `cluster/helm/media-stack/lidarr/values.yaml`
- Rewrite: `cluster/helm/media-stack/lidarr/templates/_helpers.tpl`
- Rewrite: `cluster/helm/media-stack/lidarr/templates/deployment.yaml`
- Rewrite: `cluster/helm/media-stack/lidarr/templates/service.yaml`
- Create: `cluster/helm/media-stack/lidarr/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/lidarr/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/lidarr/templates/ingressroute.yaml`
- Create: `cluster/helm/media-stack/lidarr/templates/NOTES.txt`
- Delete: `cluster/helm/media-stack/lidarr/templates/ingress.yaml`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the values schema below; helpers `app.name`, `app.fullname`, `app.chart`, `app.labels`,
  `app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName`, `app.containerPort`,
  `app.exportarrSecretName`; runtime names `lidarr` (Deployment, Service, ServiceAccount,
  IngressRoute) and `lidarr-config` (claim).

- [ ] **Step 1: Write `Chart.yaml`**

`version: 1.0.0`, `appVersion: "3.1.6.5079-ls48"`, `type: application`, one-line description
("Lidarr is a music collection manager. It watches indexers, grabs releases and files them into the
audio library Plex reads."), `icon: https://raw.githubusercontent.com/Lidarr/Lidarr/develop/Logo/1024.png`,
`home: https://lidarr.audio/`, `sources: [https://github.com/Lidarr/Lidarr]`, keywords `music`, `pvr`,
`torrent`, `usenet`, `automation`.

- [ ] **Step 2: Write `_helpers.tpl`**

Copy `cluster/helm/media-stack/sonarr/templates/_helpers.tpl` verbatim. It already defines all nine
helpers above, unprefixed, and nothing in it names the app.

- [ ] **Step 3: Write `values.yaml`**

The shape below, with doc comments in the style of `sonarr/values.yaml` explaining each non-obvious
default: the image tag and registry, the fresh claim, the root-then-`PUID` init, the two media claims,
`/ping` probes, the two log-level variables, the exporter block, and the unmeasured resources.

```yaml
nameOverride: ""
fullnameOverride: ""
replicaCount: 1
strategy: Recreate
image: { repository: lscr.io/linuxserver/lidarr, tag: "3.1.6.5079-ls48", pullPolicy: IfNotPresent }
imagePullSecrets: []
serviceAccount: { create: true, automount: false, name: "", annotations: {} }
podAnnotations: {}
podLabels: {}
podSecurityContext: { seccompProfile: { type: RuntimeDefault } }
securityContext: { allowPrivilegeEscalation: false }
service: { type: ClusterIP, port: 8686 }
container: { port: null }
ingressRoute:
  enabled: true
  apiVersion: traefik.containo.us/v1alpha1
  entryPoints: [web]
  host: lidarr.kub.local
  pathPrefix: /
  middlewares: []
  labels: {}
  annotations: {}
  tls: { enabled: false, secretName: "", certResolver: "", options: "" }
storage: { enabled: true, existingClaim: "", className: local-path, accessModes: [ReadWriteOnce], size: 128Mi, mountPath: /config }
env:
  TZ: "Europe/Paris"
  PUID: "1100"
  PGID: "1100"
  LIDARR__LOG__LEVEL: "warn"
  LIDARR__LOG__CONSOLELEVEL: "warn"
extraEnv: []
envFrom: []
downloads: { claim: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner, mountPath: /downloads, readOnly: false }
audio: { claim: pvc-nfs-audio-nfs-subdir-external-provisioner, mountPath: /audio, readOnly: false }
exportarr:
  enabled: true
  image: { repository: ghcr.io/onedr0p/exportarr, pullPolicy: IfNotPresent, tag: "v2.3.0" }
  port: 9707
  apiKey: { secretName: "", secretKey: secret }
  resources: { requests: { cpu: 2m, memory: 56Mi }, limits: { memory: 112Mi } }
nodeSelector: { kubernetes.io/hostname: streaming }
tolerations: []
affinity: {}
resources: { requests: { cpu: 10m, memory: 256Mi }, limits: { memory: 1Gi } }
probes:
  startup:   { enabled: true, path: /ping, periodSeconds: 5, timeoutSeconds: 5, failureThreshold: 30 }
  liveness:  { enabled: true, path: /ping, initialDelaySeconds: 20, periodSeconds: 30, timeoutSeconds: 5, failureThreshold: 3 }
  readiness: { enabled: true, path: /ping, initialDelaySeconds: 10, periodSeconds: 15, timeoutSeconds: 5, failureThreshold: 3 }
```

- [ ] **Step 4: Write the templates**

- `deployment.yaml`, `service.yaml`, `serviceaccount.yaml`, `persistentvolumeclaim.yaml`,
  `ingressroute.yaml`, `NOTES.txt`: copy the matching files from `cluster/helm/media-stack/sonarr`.
- In `deployment.yaml`, change the two media mounts from sonarr's `downloads`/`media` to
  `downloads`/`audio` (volume name `audio`, claim `.Values.audio.claim`, mountPath
  `.Values.audio.mountPath`, readOnly `.Values.audio.readOnly`), and the exporter args from `sonarr`
  to `lidarr`. Everything else stays: the env rendering (`env` map, `extraEnv`, `envFrom`), the probe
  blocks, the scrape annotations gated on `exportarr.enabled`, the derived exporter `URL`.
- `NOTES.txt`: replace the app name in the prose only; the URLs and ports render from values.

- [ ] **Step 5: Delete the old ingress template**

```bash
git rm cluster/helm/media-stack/lidarr/templates/ingress.yaml
```

- [ ] **Step 6: Verify the render**

```bash
helm lint cluster/helm/media-stack/lidarr --namespace media-stack
helm template lidarr cluster/helm/media-stack/lidarr --namespace media-stack > /tmp/lidarr.yaml
grep -c '^kind:' /tmp/lidarr.yaml
```
Expected: `1 chart(s) linted, 0 chart(s) failed`; exit 0; `kind:` count is 5 (Deployment, Service,
ServiceAccount, PVC, IngressRoute).

- [ ] **Step 7: Verify the contract greps**

```bash
cd cluster/helm/media-stack/lidarr
grep -rnP '\t' templates/ || echo "no tabs"
grep -rn 'latest' values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io\|node-type\|lidarr-webui\|32400\|3\.1\.0\.4875' templates/ values.yaml \
  || echo "no leftovers"
grep -n 'version:\|appVersion:' Chart.yaml
grep -n 'tag:' values.yaml
```
Expected: `no tabs`, `no floating tags`, `no leftovers`; chart `version: 1.0.0` and
`appVersion: "3.1.6.5079-ls48"`; `image.tag` the same string.

- [ ] **Step 8: Verify the five focus behaviours in the rendered output**

```bash
grep -n 'claimName: lidarr-config\|mountPath: /config' /tmp/lidarr.yaml
grep -n -A3 'name: audio' /tmp/lidarr.yaml
grep -n 'readOnly\|mountPath: /audio\|mountPath: /downloads' /tmp/lidarr.yaml
grep -n 'runAsUser\|runAsNonRoot\|fsGroup' /tmp/lidarr.yaml || echo "no uid override"
grep -n 'value: "http://lidarr:8686"\|value: "9707"\|APIKEY' /tmp/lidarr.yaml
grep -n 'LIDARR__LOG__LEVEL\|LIDARR__LOG__CONSOLELEVEL\|prometheus.io/' /tmp/lidarr.yaml
grep -n 'containerPort: 8686\|port: 8686\|Host(`lidarr.kub.local`)' /tmp/lidarr.yaml
helm template demo cluster/helm/media-stack/lidarr --namespace media-stack --set fullnameOverride=demo \
  | grep -n 'claimName: demo-config\|value: "http://demo:8686"'
```
Expected: `claimName: lidarr-config`; both media mounts present with `readOnly: false` (or no
`readOnly` line for the two media volumes); `no uid override`; exporter `URL` derived and port 9707
announced; both log-level variables and the four scrape annotations present; Service and container
port 8686 and the route matching `lidarr.kub.local`; the override run renders `demo-config` and
`http://demo:8686`.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/media-stack/lidarr
git commit -m "🔧 chore(lidarr): rebuild the chart on the sonarr shape"
```

### Task 2: Chart README

**Files:**
- Create: `cluster/helm/media-stack/lidarr/README.md`

**Interfaces:**
- Consumes: the values schema and rendered names from Task 1.
- Produces: documentation only.

- [ ] **Step 1: Write the README** with the sections `sonarr/README.md` uses: what the app is and where
  it is reachable, how it is deployed here (`helm template` command), a table of where this chart
  differs from the reference, the documented exceptions to the contract (shared `/ping` endpoint and
  why it is dependency-aware, root-then-`PUID` init with no `fsGroup`, 128Mi claim), the Secret and
  the `LIDARR_API_KEY` restore command, metrics and the 401 meaning, what to do after the first start
  (root folder `/audio`, quality profile, Prowlarr app entry, qBittorrent category), and the
  unmeasured resources note.

- [ ] **Step 2: Check the claims against the chart**

```bash
grep -n 'lidarr.kub.local\|3.1.6.5079-ls48\|lidarr-config\|LIDARR_API_KEY' \
  cluster/helm/media-stack/lidarr/README.md
```
Expected: the README's host, tag, claim name and env-variable name match `values.yaml` and the env
file block.

- [ ] **Step 3: Commit**

```bash
git add cluster/helm/media-stack/lidarr/README.md
git commit -m "📝 docs(lidarr): document the chart, its probes and the fresh install"
```

### Task 3: Record Lidarr in the chart contract

**Files:**
- Modify: `docs/charts/README.md` (probes/hardening table, "What good looks like")

**Interfaces:**
- Consumes: the chart and README from Tasks 1–2.
- Produces: documentation only.

- [ ] **Step 1: Add the table row** after the `radarr-anime` row:
  `| lidarr | yes, on /ping | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | no — the image starts as root, then drops to 1100 |`

- [ ] **Step 2: Add the "what good looks like" paragraph**: the sonarr pair's shape with a second
  media claim (`/audio` beside `/downloads`), the fresh claim because the old one was Longhorn, the
  `/ping` endpoint that reads the config database and therefore serves as an honest readiness check,
  and the exporter that stays down until the operator pastes the key minted at first start.

- [ ] **Step 3: Commit**

```bash
git add docs/charts/README.md
git commit -m "📝 docs(charts): record lidarr in the chart contract"
```

### Task 4: Add the API key slot to `homelab.env`

**Files:**
- Modify: `~/.config/secrets/homelab.env` (outside git; never staged)

**Interfaces:**
- Consumes: the variable name `LIDARR_API_KEY` from Task 2's README.
- Produces: the operator's source for the `lidarr` Secret's `secret` key.

- [ ] **Step 1: Insert the block** after the `SONARR_ANIME_API_KEY` block and before `TAUTULLI_API_KEY`,
  in the file's existing style (no `export`, four `#` comment lines):

```
# Lidarr 3.1.6.5079 - Settings -> General -> API Key. Sent as the X-Api-Key header.
# API base: http://lidarr.kub.local/api/v1
# Used to restore the `lidarr` Secret so the exportarr sidecar can scrape it.
LIDARR_API_KEY=
```

- [ ] **Step 2: Verify without printing values**

```bash
grep -cE '^LIDARR_API_KEY=' ~/.config/secrets/homelab.env   # expect 1
grep -c '^export ' ~/.config/secrets/homelab.env            # unchanged
stat -c '%a' ~/.config/secrets/homelab.env                  # expect 600
git -C . status --short | grep -c homelab.env || echo "not in git"
```
Expected: `1`, the export count unchanged from before the edit, mode `600`, `not in git`.

- [ ] **Step 3: No commit** — the file is outside the repository by design.

### Task 5: Hand over the branch

**Files:** none.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: a pull request against `develop`, and the post-sync checklist for the operator.

- [ ] **Step 1: Rebase and test**

```bash
git fetch origin
git rebase origin/develop
helm lint cluster/helm/media-stack/lidarr --namespace media-stack
helm template lidarr cluster/helm/media-stack/lidarr --namespace media-stack >/dev/null && echo render-ok
```
Expected: rebase clean, lint clean, `render-ok`.

- [ ] **Step 2: Push and open the PR** against `develop` (base branch confirmed, never `main`), with
  the post-sync checklist in the body:

```bash
kubectl -n media-stack get pods -l app.kubernetes.io/name=lidarr
kubectl -n media-stack get pvc lidarr-config
kubectl -n media-stack get ingressroute lidarr
curl -s http://lidarr.kub.local/ping
kubectl -n media-stack get secret lidarr >/dev/null && echo secret-present || echo secret-MISSING
```
Expected after sync: pod `2/2` on `streaming`, claim `Bound`, exactly one IngressRoute named
`lidarr`, `/ping` answering `{"status":"OK"}`. If `secret-MISSING`, create it from the key after the
first start:

```bash
source ~/.config/secrets/homelab.env
kubectl -n media-stack create secret generic lidarr \
  --from-literal=secret="$LIDARR_API_KEY"
```

- [ ] **Step 3: After the first start**, paste the key into `homelab.env`, patch the Secret with the
  command in the chart README, and confirm the Prometheus target
  `container="lidarr-exporter"` reports `up`.
