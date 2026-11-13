# unpackerr chart refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the unpackerr Helm chart to the repo chart contract, take the image from 0.16.1 to v1.0.1, and remove the four API keys from git.

**Architecture:** One local chart with `app.*` naming helpers and explicit per-instance values (`sonarr0`, `sonarr1`, `radarr0`, `radarr1`), rendered into `UN_<APP>_<i>_*` environment variables by a single `app.starrEnv` helper. API keys come from the existing per-app Secrets via `secretKeyRef`. No Service, no ingress, no metrics listener. Liveness on `/`; readiness off.

**Tech Stack:** Helm 4.x, Kubernetes YAML, ArgoCD. No CI; verification is `helm lint`, `helm template` and grep checks.

**Spec:** `docs/superpowers/specs/2026-11-13-unpackerr-chart-refactor-design.md`

## Global Constraints

- Image `golift/unpackerr:v1.0.1`; `appVersion: "v1.0.1"`; chart `version: 1.0.0`.
- Release and fullname stay `unpackerr`; the Deployment and ServiceAccount are named `unpackerr`.
- Secret names per instance: `sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`; every one uses key `secret`.
- Downloads: existing claim `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner`, mounted at `/downloads`, read-write.
- Security: `runAsNonRoot`, UID/GID `1100`, `allowPrivilegeEscalation: false`, all capabilities dropped, `seccompProfile: RuntimeDefault`, ServiceAccount token not mounted.
- Resources: requests `10m` CPU / `64Mi` memory; limit `512Mi` memory; no CPU limit.
- Container port `5656`, named `http`; `UN_WEBSERVER_LISTEN_ADDR` is rendered from it, so listener and probe cannot drift.
- No Service, no IngressRoute, no `networking.k8s.io`, no `prometheus.io/*` annotation, no readiness probe, no tabs.
- Every object carries `app.labels`; every name goes through `app.fullname`, `app.selectorLabels` or `app.serviceAccountName`.
- No literal credential anywhere in the chart, including in comments.

## Review Focus

- **An API key rendered as a plain `value:` instead of `secretKeyRef`** puts a credential back in git. Every `UN_*_API_KEY` entry must be followed by `valueFrom`.
- **Instance index drift** (`instances.sonarr1` included with index `0`, or an instance included twice) silently overwrites another instance's env vars. Each of the four `UN_<APP>_<i>_URL` names must appear exactly once, with the matching URL.
- **Probe/port drift**: a changed `container.port` must move the listener, the named port and the probe together. The render must show one number in `UN_WEBSERVER_LISTEN_ADDR` and `containerPort`.
- **UID 1100 on a root-owned NFS export** starts then fails on the first write or delete. The README must carry the leftover-directory note so the next person knows where to look.
- **A readiness probe reintroduced by copy-paste** would restart the pod on an *arr outage. The render must contain no `readinessProbe`, and the README must say why.
- **`noauth` plus a future Service** publishes an unauthenticated control API at a stable address. The README must make that a decision to re-open, not a default.

---

### Task 0: Commit the approved design and this plan

**Files:**
- Add: `docs/superpowers/specs/2026-11-13-unpackerr-chart-refactor-design.md`
- Add: `docs/superpowers/plans/2026-11-13-unpackerr-chart-refactor.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the documents every later task is measured against.

- [ ] **Step 1: Confirm the branch**

Run: `git branch --show-current`
Expected: `feature/unpackerr-chart-refactor`

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-11-13-unpackerr-chart-refactor-design.md \
        docs/superpowers/plans/2026-11-13-unpackerr-chart-refactor.md
git commit -m "📝 docs(unpackerr): add chart refactor design and plan"
```

---

### Task 1: Move the image to v1.0.1 and fix the chart metadata

**Files:**
- Modify: `cluster/helm/media-stack/unpackerr/Chart.yaml`
- Modify: `cluster/helm/media-stack/unpackerr/values.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: `image.tag: "v1.0.1"`, which Task 2's verification checks, and the Chart metadata that Task 3 documents.

- [ ] **Step 1: Prove the chart currently runs 0.16.1**

Run: `helm template unpackerr cluster/helm/media-stack/unpackerr | grep 'image:'`
Expected: `image: "golift/unpackerr:0.16.1"`

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: unpackerr
description: Unpackerr extracts completed downloads so Lidarr, Radarr and Sonarr can import them.
type: application
# Chart version. It moves independently of the image.
version: 1.0.0
# Must equal image.tag in values.yaml. unpackerr tags carry a leading v.
appVersion: "v1.0.1"
icon: https://unpackerr.zip/img/unpackerr.png
home: https://unpackerr.zip
sources:
  - https://github.com/Unpackerr/unpackerr
keywords:
  - torrent
  - usenet
  - extraction
  - automation
```

- [ ] **Step 3: Bump only the tag in `values.yaml`**

Change `tag: 0.16.1` to `tag: "v1.0.1"` and leave the rest of the file alone; Task 2 replaces it.

- [ ] **Step 4: Verify**

```bash
helm lint cluster/helm/media-stack/unpackerr --namespace media-stack
helm template unpackerr cluster/helm/media-stack/unpackerr | grep 'image:'
```

Expected: `1 chart(s) linted, 0 chart(s) failed`; `image: "golift/unpackerr:v1.0.1"`.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/media-stack/unpackerr/Chart.yaml cluster/helm/media-stack/unpackerr/values.yaml
git commit -m "⬆️ upgrade(unpackerr): move the image to v1.0.1 and fix chart metadata"
```

---

### Task 2: Rebuild the chart around the app's needs

**Files:**
- Modify: `cluster/helm/media-stack/unpackerr/values.yaml`
- Modify: `cluster/helm/media-stack/unpackerr/templates/_helpers.tpl`
- Modify: `cluster/helm/media-stack/unpackerr/templates/deployment.yaml`
- Create: `cluster/helm/media-stack/unpackerr/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/unpackerr/templates/NOTES.txt`

**Interfaces:**
- Consumes: `image.tag: "v1.0.1"` from Task 1.
- Produces: rendered env names `UN_<APP>_<i>_URL|API_KEY|PATHS_<n>|PROTOCOLS|TIMEOUT|DELETE_ORIG|DELETE_DELAY`, plus `UN_WEBSERVER_LISTEN_ADDR`; helpers `app.name`, `app.fullname`, `app.chart`, `app.labels`, `app.selectorLabels`, `app.serviceAccountName`, `app.starrEnv`; values keys listed in Step 2, which Task 3's README documents.

- [ ] **Step 1: Prove the current defects**

```bash
helm template unpackerr cluster/helm/media-stack/unpackerr > /tmp/unpackerr-before.yaml
grep -c 'UN_SONARR_0_API_KEY' /tmp/unpackerr-before.yaml   # 1
grep -A1 'UN_SONARR_0_API_KEY' /tmp/unpackerr-before.yaml | grep -c 'value:'      # 1 -> literal key
grep -c 'prometheus.io/scrape' /tmp/unpackerr-before.yaml  # 1
grep -c 'runAsUser' /tmp/unpackerr-before.yaml            # 0
grep -c 'livenessProbe' /tmp/unpackerr-before.yaml        # 0
grep -c 'resources:' /tmp/unpackerr-before.yaml           # 1, with an empty body
```

These are what the rebuild removes.

- [ ] **Step 2: Write `values.yaml`**

```yaml
# Default values for unpackerr.
#
# unpackerr extracts completed downloads in place so the *arr apps can import
# them. It has no web UI in this deployment: the v1 UI exists on the pod IP but
# nothing publishes it, and the chart renders no Service.

# -- Override the chart name.
nameOverride: ""
# -- Override the generated release name.
fullnameOverride: ""

# -- Deployment strategy. `Recreate` on purpose: a second pod would scan the same
# queue and extract the same archive into the same directory.
strategy: Recreate

image:
  repository: golift/unpackerr
  # -- Pin the tag. Renovate manages this. Keep appVersion equal to it.
  tag: "v1.0.1"
  pullPolicy: IfNotPresent

imagePullSecrets: []

serviceAccount:
  # -- Create a dedicated ServiceAccount. unpackerr never calls the Kubernetes API.
  create: true
  automount: false
  name: ""
  annotations: {}

# -- Annotations added to the pod. There are no `prometheus.io/*` entries: v1
# serves /metrics behind an API key, so the annotation-based scrape job cannot
# use it. Adding them back without a credential produces a permanently down
# target.
podAnnotations: {}
podLabels: {}

podSecurityContext:
  # -- Runtime's syscall filter. The image is a static Go binary on Alpine.
  seccompProfile:
    type: RuntimeDefault

securityContext:
  # -- The image declares no USER and would run as root. UID 1100 is what
  # qBittorrent, Sonarr and the rest of media-stack use, and the NFS download
  # export already accepts it.
  runAsNonRoot: true
  runAsUser: 1100
  runAsGroup: 1100
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL

container:
  # -- HTTP listener port, named `http` in the pod. The chart renders
  # UN_WEBSERVER_LISTEN_ADDR and the liveness probe from this one value, so the
  # listener and the probe cannot disagree.
  port: 5656

# -- Environment variables, rendered inline into the container spec. Changing a
# value changes the pod template, so the Deployment rolls by itself.
env:
  TZ: "Europe/Paris"
  # Cuts the all-zero queue summary. The noise floor is one line per tracked item
  # per minute (measured on 0.16.1 on 2026-11-13, about 13k lines a day, mostly
  # "Imported, elapsed: N"). unpackerr has no log level; `quiet` is not an option
  # because it silences errors too.
  UN_ACTIVITY: "true"
  UN_INTERVAL: "1m"
  UN_START_DELAY: "1m"
  UN_RETRY_DELAY: "5m"
  UN_MAX_RETRIES: "10"
  UN_PARALLEL: "1"
  UN_FILE_MODE: "0644"
  UN_DIR_MODE: "0755"
  # The web server is the liveness target. `noauth` stops v1 from generating a
  # temporary password at every start and printing it into Loki. Nothing
  # publishes this port, so it is reachable by pod IP and port-forward only.
  UN_WEBSERVER_UI_PASSWORD: "noauth"
  # UN_DEBUG: "false"
  # UN_LOG_QUEUES: "5m"
  # UN_PROGRESS: "1m"

# -- Starr instances. The key names the app and the index that appears in the
# environment: `sonarr0` renders UN_SONARR_0_*, `radarr1` renders UN_RADARR_1_*.
# Adding an instance is a block here plus one include line in the Deployment.
instances:
  sonarr0:
    url: "http://sonarr:8989"
    apiKeySecret:
      name: sonarr
      key: secret
    paths:
      - /downloads
    protocols: "torrent,TorrentDownloadProtocol"
    timeout: 1m
    deleteOrig: false
    deleteDelay: 30m
  sonarr1:
    url: "http://sonarr-anime:8989"
    apiKeySecret:
      name: sonarr-anime
      key: secret
    paths:
      - /downloads
    protocols: "torrent,TorrentDownloadProtocol"
    timeout: 1m
    deleteOrig: false
    deleteDelay: 30m
  radarr0:
    url: "http://radarr:7878"
    apiKeySecret:
      name: radarr
      key: secret
    paths:
      - /downloads
    protocols: "torrent,TorrentDownloadProtocol"
    timeout: 1m
    deleteOrig: false
    deleteDelay: 30m
  radarr1:
    url: "http://radarr-anime:7878"
    apiKeySecret:
      name: radarr-anime
      key: secret
    paths:
      - /downloads
    protocols: "torrent,TorrentDownloadProtocol"
    timeout: 1m
    deleteOrig: false
    deleteDelay: 30m

downloads:
  # -- qBittorrent's NFS export, the same claim the *arr apps mount. The chart
  # does not create it.
  existingClaim: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner
  mountPath: /downloads
  readOnly: false

# -- Variables sourced from a Secret, as `{ name, valueFrom }`. Kept separate so
# a secret can never be pasted into the plain map above by accident.
extraEnv: []
# -- Bulk `envFrom` sources, usually `secretRef`.
envFrom: []
# -- Additional volumes, rendered verbatim into the pod spec.
volumes: []
# -- Additional volume mounts, rendered verbatim into the container.
volumeMounts: []

nodeSelector:
  # -- Every workload is pinned to a named node.
  kubernetes.io/hostname: streaming

tolerations: []
affinity: {}

# Sized from a measurement on 2026-11-13: 15 MiB and ~0.02m CPU while idle.
# The request covers idle plus margin; the limit is headroom for the extraction
# children, which were not running when the measurement was taken. Confirm with
# `kubectl top pod` during a burst and adjust. Any non-zero request also moves
# the pod out of the BestEffort class, which is what it had before.
resources:
  requests:
    cpu: 10m
    memory: 64Mi
  limits:
    memory: 512Mi

probes:
  liveness:
    enabled: true
    # v1 serves the SPA at `/` outside the auth wrappers, so it answers 200 while
    # the process serves. unpackerr has no health endpoint: every /api route
    # needs a permission, and the kubelet has no API key.
    path: /
    port: http
    initialDelaySeconds: 20
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    # No unauthenticated dependency-aware endpoint exists, and the chart renders
    # no Service, so there is no traffic to gate. Pointing this at liveness
    # would turn an *arr outage into a pod restart.
    enabled: false
```

- [ ] **Step 3: Write `templates/_helpers.tpl`**

Use the same helper bodies and names as `cluster/helm/media-stack/autobrr/templates/_helpers.tpl`
(`app.name`, `app.fullname`, `app.chart`, `app.labels`, `app.selectorLabels`,
`app.serviceAccountName`) — they contain no app-specific text, so copy them verbatim. Keep the file's
leading comment saying the names are shared by convention so a chart can be rebuilt without a rename
sweep. Drop `app.containerPort` and `app.configClaimName`: this chart has no Service and creates no
claim.

Add one helper, `app.starrEnv`, taking a dict with `prefix` (string, for example `SONARR`), `index`
(int), and `instance` (the values block). It renders, in this order:

```text
- name: UN_<prefix>_<index>_URL          value: <url>
- name: UN_<prefix>_<index>_API_KEY      valueFrom.secretKeyRef {name, key}
- name: UN_<prefix>_<index>_PATHS_<n>    value: <path>          (one per entry, n from 0)
- name: UN_<prefix>_<index>_PROTOCOLS    value: <protocols>
- name: UN_<prefix>_<index>_TIMEOUT      value: <timeout>
- name: UN_<prefix>_<index>_DELETE_ORIG  value: <deleteOrig>    (rendered as "true"/"false")
- name: UN_<prefix>_<index>_DELETE_DELAY value: <deleteDelay>
```

Every `value` is quoted; `API_KEY` has no `value` at all. Use `default "secret"` for
`apiKeySecret.key`.

- [ ] **Step 4: Write `templates/deployment.yaml`**

Start from `cluster/helm/media-stack/cross-seed/templates/deployment.yaml` and apply these deltas.
Everything else stays: metadata, `app.labels`, `selector`, `serviceAccountName`,
`podSecurityContext`, `imagePullSecrets`, `nodeSelector`, `tolerations`, `affinity`, raw
`volumes`/`volumeMounts`.

- `replicas: 1` literal, with a comment that a second pod would extract the same archive twice.
- `strategy: {{ .Values.strategy }}`.
- The container port block:

```yaml
          ports:
            - name: http
              containerPort: {{ .Values.container.port }}
              protocol: TCP
```

- The env block, in this order: the `env` map, `UN_WEBSERVER_LISTEN_ADDR` built from
  `container.port`, four `app.starrEnv` includes, then `extraEnv`, then `envFrom`:

```yaml
          env:
            {{- range $name, $value := .Values.env }}
            - name: {{ $name }}
              value: {{ $value | toString | quote }}
            {{- end }}
            # Rendered from container.port so the listener and the probe cannot drift.
            - name: UN_WEBSERVER_LISTEN_ADDR
              value: "0.0.0.0:{{ .Values.container.port }}"
            {{- include "app.starrEnv" (dict "prefix" "SONARR" "index" 0 "instance" .Values.instances.sonarr0) | nindent 12 }}
            {{- include "app.starrEnv" (dict "prefix" "SONARR" "index" 1 "instance" .Values.instances.sonarr1) | nindent 12 }}
            {{- include "app.starrEnv" (dict "prefix" "RADARR" "index" 0 "instance" .Values.instances.radarr0) | nindent 12 }}
            {{- include "app.starrEnv" (dict "prefix" "RADARR" "index" 1 "instance" .Values.instances.radarr1) | nindent 12 }}
            {{- with .Values.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- with .Values.envFrom }}
          envFrom:
            {{- toYaml . | nindent 12 }}
          {{- end }}
```

- Probes: copy cross-seed's liveness block and add the readiness block guarded by
  `.Values.probes.readiness.enabled`, so the file keeps both shapes and the values decide. Use
  `port: {{ .Values.probes.liveness.port }}` and the same for readiness.
- The downloads volume and mount:

```yaml
            - name: downloads
              mountPath: {{ .Values.downloads.mountPath }}
              {{- if .Values.downloads.readOnly }}
              readOnly: true
              {{- end }}
```

```yaml
        - name: downloads
          persistentVolumeClaim:
            claimName: {{ .Values.downloads.existingClaim }}
            {{- if .Values.downloads.readOnly }}
            readOnly: true
            {{- end }}
```

- No `initContainers`, no ConfigMap volume, no `/config` mount, no literal claim name, no API key.

- [ ] **Step 5: Write `templates/serviceaccount.yaml` and `templates/NOTES.txt`**

`serviceaccount.yaml`: the same shape as `cluster/helm/media-stack/autobrr/templates/serviceaccount.yaml`,
guarded by `.Values.serviceAccount.create`, naming itself with `app.serviceAccountName`, and
honouring `serviceAccount.annotations` and `serviceAccount.automount`.

`NOTES.txt`:

```text
{{ include "app.fullname" . }} has been deployed to namespace {{ .Release.Namespace }}.

This deployment is not exposed. The v1 web UI is available on a port-forward:

  kubectl -n {{ .Release.Namespace }} port-forward deploy/{{ include "app.fullname" . }} {{ .Values.container.port }}:{{ .Values.container.port }}

API keys are read from the existing Secrets named in .Values.instances; none is
stored in the chart.

Prometheus metrics are off: v1 requires an API key for /metrics, which the
cluster's annotation-based scrape job cannot send.
```

- [ ] **Step 6: Lint and render**

```bash
helm lint cluster/helm/media-stack/unpackerr --namespace media-stack
helm template unpackerr cluster/helm/media-stack/unpackerr --namespace media-stack > /tmp/unpackerr-after.yaml
```

Expected: lint reports `1 chart(s) linted, 0 chart(s) failed`; template exits 0.

- [ ] **Step 7: Check the rendered contract**

```bash
cd cluster/helm/media-stack/unpackerr

grep -q 'image: "golift/unpackerr:v1.0.1"' /tmp/unpackerr-after.yaml && echo "image ok"
[ "$(grep -A1 '_API_KEY$' /tmp/unpackerr-after.yaml | grep -c 'valueFrom:')" -eq 4 ] && echo "4 secret refs"
! grep -A1 '_API_KEY$' /tmp/unpackerr-after.yaml | grep -q 'value:' && echo "no literal keys"
! grep -qE '[0-9a-f]{32}' /tmp/unpackerr-after.yaml && echo "no hex keys"
for k in SONARR_0 SONARR_1 RADARR_0 RADARR_1; do
  [ "$(grep -c "UN_${k}_URL$" /tmp/unpackerr-after.yaml)" -eq 1 ] || echo "BAD INDEX $k"
done
grep -A1 'UN_SONARR_1_URL$' /tmp/unpackerr-after.yaml | grep -q 'sonarr-anime:8989' && echo "anime url ok"
grep -A1 'UN_RADARR_1_URL$' /tmp/unpackerr-after.yaml | grep -q 'radarr-anime:7878' && echo "anime radarr ok"
grep -q 'value: "0.0.0.0:5656"' /tmp/unpackerr-after.yaml && echo "listen addr ok"
grep -q 'containerPort: 5656' /tmp/unpackerr-after.yaml && echo "port ok"
grep -q 'TorrentDownloadProtocol' /tmp/unpackerr-after.yaml && echo "protocols ok"
! grep -q 'prometheus.io' /tmp/unpackerr-after.yaml && echo "no scrape annotation"
grep -q 'runAsUser: 1100' /tmp/unpackerr-after.yaml && echo "uid ok"
grep -q 'claimName: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner' /tmp/unpackerr-after.yaml && echo "claim ok"
grep -q 'memory: 64Mi' /tmp/unpackerr-after.yaml && grep -q 'memory: 512Mi' /tmp/unpackerr-after.yaml && echo "resources ok"
! grep -q 'readinessProbe' /tmp/unpackerr-after.yaml && echo "no readiness"
! grep -q 'UN_WEBSERVER_METRICS' /tmp/unpackerr-after.yaml && echo "metrics env gone"
! grep -qE '0\.16\.1|setGateway|LIDARR' /tmp/unpackerr-after.yaml && echo "legacy gone"
! grep -rnP '\t' templates && echo "no tabs"
```

Expected: every `ok` line prints, no `BAD INDEX` line, nothing missing.

- [ ] **Step 8: Check name derivation and a second render mode**

```bash
helm template unpackerr cluster/helm/media-stack/unpackerr --set fullnameOverride=demo > /tmp/unpackerr-demo.yaml
grep -q 'name: demo' /tmp/unpackerr-demo.yaml && echo "derived name ok"
grep -q 'serviceAccountName: demo' /tmp/unpackerr-demo.yaml && echo "derived sa ok"

helm template unpackerr cluster/helm/media-stack/unpackerr --set probes.readiness.enabled=true > /tmp/unpackerr-ready.yaml
grep -q 'readinessProbe' /tmp/unpackerr-ready.yaml && echo "readiness toggle ok"
```

Expected: all three lines print.

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/media-stack/unpackerr
git commit -m "♻️ refactor(unpackerr): rebuild chart around the app's needs"
```

---

### Task 3: Write the chart README

**Files:**
- Create: `cluster/helm/media-stack/unpackerr/README.md`

**Interfaces:**
- Consumes: the values keys and rendered env names produced by Task 2.
- Produces: the operator-facing record of the security debt and the upgrade path.

- [ ] **Step 1: Write the README**

Cover, in this order:

1. What unpackerr does; upstream links (`https://unpackerr.zip`, GitHub); image source Docker Hub
   `golift/unpackerr`.
2. Deployment: ArgoCD Application `cluster/argocd/projects/media-stack/unpackerr.yaml`, release name
   `unpackerr`, the rendered objects (Deployment, ServiceAccount) and the local render command.
3. Values table: `image.*`, `strategy`, `container.port`, `env`, `instances.*`, `downloads.*`,
   `resources.*`, `probes.*`, `securityContext`, `nodeSelector`.
4. Instances: the key names the app and the index (`sonarr0` → `UN_SONARR_0_*`); adding an instance is
   a values block plus one include line in `templates/deployment.yaml`; the four Secrets and the
   `secret` key.
5. Security debt: the four API keys were committed in plaintext before 2026-11-13 and are still in
   git history. Rotating them in the *arr apps and updating the Secrets is required, and is not done
   by this chart.
6. Why the UI is `noauth` and unpublished, and the note that adding a Service or a route re-opens
   that decision.
7. Probes: liveness on `/`, a process check; readiness deliberately absent and why.
8. Metrics: off; v1 requires an API key with `system:metrics:read` for `/metrics`, which the
   annotation-based scrape job cannot send. What enabling it would take.
9. Logs: no log level exists; `UN_ACTIVITY=true` removes the all-zero queue summary; the per-item
   `Status:` lines remain (about 13k a day) and cannot be removed without silencing errors; the v1 UI
   and history file are the record.
10. Resources: measured idle, what the limit covers, and to check `kubectl top` during a burst.
11. Upgrade rule: `image.tag` and `appVersion` move together; Renovate updates the tag only. The
    0.16.1 → v1.0.1 move in this chart is a major app rewrite; rollback is reverting `image.tag`,
    because the environment-variable contract did not change.
12. Leftover files: files extracted while the pod ran as root stay root-owned; a directory the new
    pod cannot clean is a manual `chown`/`rm` on the NFS export.
13. Uninstall note: the chart creates no claim; the download export is not owned by it.

- [ ] **Step 2: Check the README**

```bash
test -f cluster/helm/media-stack/unpackerr/README.md && echo "readme exists"
grep -qi 'rotat' cluster/helm/media-stack/unpackerr/README.md && echo "rotation recorded"
grep -qi 'system:metrics:read' cluster/helm/media-stack/unpackerr/README.md && echo "metrics reason recorded"
! grep -rn 'latest' cluster/helm/media-stack/unpackerr/README.md && echo "no floating tag advice"
```

Expected: all three `echo` lines print.

- [ ] **Step 3: Commit**

```bash
git add cluster/helm/media-stack/unpackerr/README.md
git commit -m "📝 docs(unpackerr): document the rebuilt chart and its debt"
```

---

### Task 4: Align the chart contract page

**Files:**
- Modify: `docs/charts/README.md`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Add `unpackerr` to the hardening table**

In the "Changing the UID breaks the config volume" table, add a row after `prowlarr`:

```markdown
| `unpackerr` | liveness only, on `/` | capabilities dropped, seccomp, no privilege escalation, no API token | **yes — 1100**, no claim to chown |
```

- [ ] **Step 2: Add the worked example**

Append to "What good looks like":

```markdown
`unpackerr` as of 2026-11-13: two objects because it keeps no state of its own and is never exposed,
per-instance values (`sonarr0`, `radarr1`, …) that render `UN_<APP>_<i>_*` through one helper, API
keys from the per-app Secrets, and metrics deliberately off because v1 requires an API key for
`/metrics`. It is the worked example of the metrics rule's other half: when the exporter cannot be
scraped by the annotation job, announce nothing rather than leave a target permanently down.
```

- [ ] **Step 3: Check the page**

```bash
grep -n 'unpackerr' docs/charts/README.md
```

Expected: the table row and the paragraph.

- [ ] **Step 4: Commit**

```bash
git add docs/charts/README.md
git commit -m "📝 docs(charts): record unpackerr as a worked example"
```

---

### Task 5: Whole-repo verification and handover

**Files:** none.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: a branch that renders every chart and resolves every ArgoCD path, ready for review.

- [ ] **Step 1: Render every self-contained chart**

```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```

Expected: only the known `SKIP` lines for wrapper charts, no `FAIL`.

- [ ] **Step 2: Check ArgoCD references**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")

while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u)
```

Expected output is exactly these two lines and nothing else:

```text
BROKEN PATH cluster/helm/nfs/plex-media
BROKEN VALUES /cluster/helm/nfs/audio/values.yaml
```

Both are pre-existing on `develop` and unrelated to this branch. Any third line is a new breakage and
must be fixed.

- [ ] **Step 3: Confirm no credential survived**

```bash
! git grep -nE '[0-9a-f]{32}' -- cluster/helm/media-stack/unpackerr && echo "no keys in chart"
git diff --stat origin/develop...HEAD
```

Expected: `no keys in chart`, and the diff stat covers the chart, the README, `docs/charts/README.md`
and the spec/plan pair.

- [ ] **Step 4: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push -u origin feature/unpackerr-chart-refactor
```

- [ ] **Step 5: Open the pull request**

Use the GitHub MCP `create_pull_request`: head `feature/unpackerr-chart-refactor`, base `develop`.
Title `♻️ refactor(unpackerr): rebuild chart around the app's needs`. The body covers:

- the four API keys removed from the chart and read from the existing Secrets;
- the v1.0.1 upgrade and that `renovate/golift-unpackerr-1.x` can be closed;
- the removed scrape annotation and why metrics stay off;
- UID 1100, the resources, and the move off `BestEffort`;
- the probe decision (liveness on `/`, no readiness);
- the required follow-up: rotate the four keys, because they remain in git history.

Do not merge; the operator reviews and merges.

---

## Post-merge operator runbook

In this order, after the PR is approved and before/after the manual ArgoCD sync (auto-sync is off):

1. **Rotate the four API keys** in Sonarr, Sonarr-Anime, Radarr and Radarr-Anime, and update the four
   Secrets (`kubectl -n media-stack create secret generic <name> --from-literal=secret=<new>
   --dry-run=client -o yaml | kubectl apply -f -`). The chart reads them at pod start, so restart the
   deployment afterwards. This is required whether or not the git history is rewritten.
2. **Sync** the Application manually and confirm: the pod runs as 1100, image is `v1.0.1`, the
   liveness probe passes, and there is no generated-password line in the logs.
3. **Check the four instances poll**: logs should show `Updated (http://sonarr:8989)`,
   `Updated (http://sonarr-anime:8989)`, `Updated (http://radarr:7878)`, `Updated
   (http://radarr-anime:7878)` each cycle.
4. **Check an extraction end to end** the next time an archive is downloaded, and `kubectl top pod`
   during it to confirm the 512Mi limit.
5. **Any leftover root-owned directory** on `/downloads` that the 1100 pod cannot clean is removed
   by hand on the NFS export.
