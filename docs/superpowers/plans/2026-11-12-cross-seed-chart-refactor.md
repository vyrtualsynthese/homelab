# cross-seed chart refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the cross-seed Helm chart around the image's actual needs, using the repo chart contract and the autobrr chart as a reference for principles, then correct the documentation so the reference is no longer presented as a directory to copy.

**Architecture:** One local chart with `app.*` naming helpers and named values for cross-seed's two app-specific mounts: the `/config` claim plus a read-only `config.js` Secret file, and qBittorrent's NFS torrent directory, also read-only. Liveness only on `/api/ping`; no readiness probe, no ingress. The chart runs as UID/GID 1100 after a one-time ownership change on the node.

**Tech Stack:** Helm 4.2.4, Kubernetes YAML, ArgoCD. No CI; verification is `helm lint`, `helm template`, and grep checks.

**Spec:** `docs/superpowers/specs/2026-11-12-cross-seed-chart-refactor-design.md`

## Global Constraints

- Image: `ghcr.io/cross-seed/cross-seed:6.13.9`; `appVersion: "6.13.9"`; chart `version: 1.0.0`.
- Release and fullname stay `cross-seed`; objects are `cross-seed` (Deployment, Service, ServiceAccount) and `cross-seed-config` (claim).
- Config claim: `<fullname>-config`, `storageClassName: local-path`, `ReadWriteOnce`, `128Mi`, mounted at `/config`.
- `config.js`: Secret `cross-seed`, key `config.js`, read-only `subPath` mount at `/config/config.js`.
- Torrents: existing claim `pvc-nfs-qbittorrent-config-nfs-subdir-external-provisioner`, `subPath: qBittorrent/BT_backup`, mounted read-only at `/torrents`.
- Security: `runAsNonRoot`, UID/GID `1100`, `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`, all capabilities dropped, ServiceAccount token not mounted.
- Resources: requests `50m` CPU / `128Mi` memory; limit `512Mi` memory; no CPU limit.
- Service: `ClusterIP`, port `2468`; liveness on `GET /api/ping`; no readiness probe.
- No IngressRoute, no `networking.k8s.io`, no `busybox`, no `cross-seed2`, no tabs.
- Every object carries `app.labels`; every name goes through `app.fullname`, `app.selectorLabels`, `app.serviceAccountName` or `app.configClaimName`.
- Documentation must present autobrr as a reference for principles and the contract, never as a byte-for-byte template.

## Review Focus

- **Claim name drift is silent data loss.** A render with release `cross-seed` must produce `claimName: cross-seed-config`, and `--set fullnameOverride=demo` must produce `demo-config`.
- **The read-only config mount hides claim edits.** The chart must mount the Secret with `subPath` and `readOnly: true`, and the README must say a Secret change needs a pod restart.
- **UID 1100 on a root-owned claim starts then fails on the first database write.** The chart must render `runAsNonRoot`, `runAsUser: 1100`, and the README must carry the ownership runbook.
- **Readiness must not be faked with liveness.** The render must contain no `readinessProbe`, and the README must explain that no dependency-aware unauthenticated endpoint exists.
- **The NFS torrent mount must stay read-only.** The render must show `subPath: qBittorrent/BT_backup` and `readOnly: true` on that volume and mount.

---

### Task 0: Commit the approved design and this plan

**Files:**
- Add: `docs/superpowers/specs/2026-11-12-cross-seed-chart-refactor-design.md`
- Add: `docs/superpowers/plans/2026-11-12-cross-seed-chart-refactor.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the documents every later task is measured against.

- [ ] **Step 1: Confirm the branch**

Run: `git branch --show-current`
Expected: `feature/cross-seed-chart-refactor`

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-11-12-cross-seed-chart-refactor-design.md \
        docs/superpowers/plans/2026-11-12-cross-seed-chart-refactor.md
git commit -m "📝 docs(cross-seed): add chart refactor design and plan"
```

---

### Task 1: Rebuild the cross-seed chart

**Files:**
- Modify: `cluster/helm/media-stack/cross-seed/Chart.yaml`
- Modify: `cluster/helm/media-stack/cross-seed/values.yaml`
- Modify: `cluster/helm/media-stack/cross-seed/templates/_helpers.tpl`
- Modify: `cluster/helm/media-stack/cross-seed/templates/deployment.yaml`
- Modify: `cluster/helm/media-stack/cross-seed/templates/service.yaml`
- Modify: `cluster/helm/media-stack/cross-seed/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/cross-seed/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/cross-seed/templates/NOTES.txt`
- Delete: `cluster/helm/media-stack/cross-seed/templates/configmap-env.yaml`
- Delete: `cluster/helm/media-stack/cross-seed/templates/pvc-cross-seed-config.yaml`
- Delete: `cluster/helm/media-stack/cross-seed/templates/pvc-cross-seed.yaml`

**Interfaces:**
- Consumes: the chart contract in `docs/charts/README.md`; autobrr's `_helpers.tpl`, `service.yaml`, `serviceaccount.yaml` and `persistentvolumeclaim.yaml` as the reference for object shape.
- Produces (for Task 2's README): values keys `image.*`, `strategy`, `args`, `service.*`, `container.port`, `env`, `extraEnv`, `envFrom`, `storage.*`, `configFile.*`, `torrents.*`, `volumes`, `volumeMounts`, `resources.*`, `probes.*`, `securityContext`, `podSecurityContext`, `serviceAccount.*`, `nodeSelector`, `tolerations`, `affinity`; rendered objects `cross-seed` (ServiceAccount), `cross-seed-config` (PVC), `cross-seed` (Service, port 2468), `cross-seed` (Deployment).

- [ ] **Step 1: Prove the current chart violates the contract**

```bash
helm template cross-seed cluster/helm/media-stack/cross-seed --namespace media-stack > /tmp/cross-seed-before.yaml
grep -c 'busybox' /tmp/cross-seed-before.yaml          # expect 1
grep -c 'cross-seed2' /tmp/cross-seed-before.yaml      # expect 1 or more
grep -c 'runAsUser' /tmp/cross-seed-before.yaml        # expect 0
grep -c 'readinessProbe' /tmp/cross-seed-before.yaml   # expect 0
grep -c 'kind: IngressRoute' /tmp/cross-seed-before.yaml # expect 0
```

These are the defects the refactor removes: the init container, the unused second claim, and the missing hardening.

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: cross-seed
description: Cross-seeds existing torrents across multiple trackers using Torznab indexers.
type: application
# Chart version. It moves independently of the image.
version: 1.0.0
# Must equal image.tag in values.yaml. cross-seed image tags drop the release's leading v.
appVersion: "6.13.9"
icon: https://www.cross-seed.org/img/cross-seed.svg
home: https://www.cross-seed.org/
sources:
  - https://github.com/cross-seed/cross-seed
keywords:
  - torrent
  - cross-seed
  - automation
```

- [ ] **Step 3: Write `values.yaml`**

```yaml
# Default values for cross-seed.
#
# cross-seed is not a web app here: the UI is never exposed. Everything it needs
# is the /config claim, a config.js Secret, and qBittorrent's torrent directory.

# -- Override the chart name.
nameOverride: ""
# -- Override the generated release name.
fullnameOverride: ""

replicaCount: 1

# -- Required with a ReadWriteOnce config volume.
strategy: Recreate

image:
  repository: ghcr.io/cross-seed/cross-seed
  # -- Pin the tag. Renovate manages this. Keep appVersion equal to it.
  tag: "6.13.9"
  pullPolicy: IfNotPresent

imagePullSecrets: []

serviceAccount:
  create: true
  # -- cross-seed never calls the Kubernetes API; do not mount a token.
  automount: false
  name: ""
  annotations: {}

podAnnotations: {}
podLabels: {}

# local-path is hostPath-backed and the kubelet does not apply fsGroup to it.
# The claim is chowned to 1100:1100 on the node before UID 1100 is enabled.
podSecurityContext:
  seccompProfile:
    type: RuntimeDefault

securityContext:
  runAsNonRoot: true
  runAsUser: 1100
  runAsGroup: 1100
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL

service:
  type: ClusterIP
  port: 2468

container:
  # -- Only set when the container listens on a different port than the Service.
  port: null

# -- Image entrypoint is catatonit + cross-seed, so only the subcommand is passed.
args:
  - daemon

env:
  TZ: "Europe/Paris"
  # -- The image already sets this; stated so the mount path is explicit.
  CONFIG_DIR: "/config"

extraEnv: []
envFrom: []

storage:
  enabled: true
  existingClaim: ""
  className: local-path
  accessModes:
    - ReadWriteOnce
  size: 128Mi
  mountPath: /config

configFile:
  # -- Secret that holds config.js. It must already exist in the namespace.
  secretName: cross-seed
  key: config.js
  # -- Read-only subPath mount inside the config claim.
  mountPath: /config/config.js

torrents:
  # -- qBittorrent's BT_backup on NFS. cross-seed reads it and injects through the API.
  existingClaim: pvc-nfs-qbittorrent-config-nfs-subdir-external-provisioner
  subPath: qBittorrent/BT_backup
  mountPath: /torrents
  readOnly: true

volumes: []
volumeMounts: []

nodeSelector:
  kubernetes.io/hostname: streaming

tolerations: []
affinity: {}

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    # No CPU limit, so torrent parsing is never throttled.
    memory: 512Mi

probes:
  liveness:
    enabled: true
    # cross-seed's unauthenticated health endpoint.
    path: /api/ping
    initialDelaySeconds: 20
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    # No dependency-aware unauthenticated endpoint exists; /api/status needs
    # the API key. The Service carries no user traffic, so readiness is off.
    enabled: false
```

- [ ] **Step 4: Write `templates/_helpers.tpl`**

Use the same helper names and behavior as `cluster/helm/media-stack/autobrr/templates/_helpers.tpl`: `app.name`, `app.fullname`, `app.chart`, `app.labels`, `app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName`, `app.containerPort`. Copy the helper bodies verbatim; they contain no app-specific text. Change only the file's leading comment to say the names are shared by convention so a chart can be copied without a rename sweep.

- [ ] **Step 5: Write `templates/deployment.yaml`**

Start from autobrr's `templates/deployment.yaml` and apply these deltas. Everything else (metadata, labels, selector, serviceAccountName, pod security context, env rendering, envFrom, ports, resources, nodeSelector, tolerations, affinity, raw `volumes`/`volumeMounts`) stays the same.

Add args after the image:

```yaml
          {{- with .Values.args }}
          args:
            {{- toYaml . | nindent 12 }}
          {{- end }}
```

Add the config-file mount after the `storage.mountPath` mount:

```yaml
            {{- if .Values.configFile.secretName }}
            - name: config-file
              mountPath: {{ .Values.configFile.mountPath }}
              subPath: {{ .Values.configFile.key }}
              readOnly: true
            {{- end }}
```

Add the torrents mount after the config-file mount:

```yaml
            {{- if .Values.torrents.existingClaim }}
            - name: torrents
              mountPath: {{ .Values.torrents.mountPath }}
              {{- with .Values.torrents.subPath }}
              subPath: {{ . }}
              {{- end }}
              readOnly: {{ .Values.torrents.readOnly }}
            {{- end }}
```

Add the two volumes after the storage volume:

```yaml
        {{- if .Values.configFile.secretName }}
        - name: config-file
          secret:
            secretName: {{ .Values.configFile.secretName }}
            defaultMode: 0444
            items:
              - key: {{ .Values.configFile.key }}
                path: {{ .Values.configFile.key }}
        {{- end }}
        {{- if .Values.torrents.existingClaim }}
        - name: torrents
          persistentVolumeClaim:
            claimName: {{ .Values.torrents.existingClaim }}
            readOnly: {{ .Values.torrents.readOnly }}
        {{- end }}
```

There must be no `initContainers` block, no `configmap` volume, and no `/cross-seed` mount.

- [ ] **Step 6: Write the remaining templates**

- `service.yaml`: same as autobrr's.
- `serviceaccount.yaml`: same as autobrr's; it already honours `serviceAccount.automount`.
- `persistentvolumeclaim.yaml`: same as autobrr's; renders only when `storage.enabled` and no `storage.existingClaim`, and names the claim with `app.configClaimName`.
- `NOTES.txt`:

```text
{{ include "app.fullname" . }} has been deployed to namespace {{ .Release.Namespace }}.

The web UI is not exposed. Reach it with a port-forward:

  kubectl -n {{ .Release.Namespace }} port-forward svc/{{ include "app.fullname" . }} {{ .Values.service.port }}:{{ .Values.service.port }}

Configuration is mounted read-only from Secret {{ .Values.configFile.secretName }}
at {{ .Values.configFile.mountPath }}. Changing it requires a pod restart.

State is stored in the claim {{ include "app.configClaimName" . }}.
{{- if not .Values.storage.existingClaim }}

That claim is created by this chart, so deleting the release deletes it too.
{{- end }}
```

- [ ] **Step 7: Delete the obsolete templates**

```bash
git rm cluster/helm/media-stack/cross-seed/templates/configmap-env.yaml \
       cluster/helm/media-stack/cross-seed/templates/pvc-cross-seed-config.yaml \
       cluster/helm/media-stack/cross-seed/templates/pvc-cross-seed.yaml
```

- [ ] **Step 8: Lint and render**

```bash
helm lint cluster/helm/media-stack/cross-seed --namespace media-stack
helm template cross-seed cluster/helm/media-stack/cross-seed --namespace media-stack > /tmp/cross-seed-after.yaml
```

Expected: lint reports `1 chart(s) linted, 0 chart(s) failed`; template exits 0.

- [ ] **Step 9: Check the rendered contract**

```bash
grep -q 'claimName: cross-seed-config' /tmp/cross-seed-after.yaml && echo "claim ok"
grep -q 'path: /api/ping' /tmp/cross-seed-after.yaml && echo "liveness ok"
! grep -q 'readinessProbe' /tmp/cross-seed-after.yaml && echo "readiness absent"
grep -q 'runAsUser: 1100' /tmp/cross-seed-after.yaml && echo "uid ok"
grep -q 'mountPath: /config/config.js' /tmp/cross-seed-after.yaml && echo "config file ok"
grep -q 'subPath: qBittorrent/BT_backup' /tmp/cross-seed-after.yaml && echo "torrents subPath ok"
grep -q 'readOnly: true' /tmp/cross-seed-after.yaml && echo "read-only mounts ok"
! grep -q 'busybox\|cross-seed2\|kind: IngressRoute\|networking.k8s.io' /tmp/cross-seed-after.yaml && echo "legacy parts absent"
! grep -rnP '\t' cluster/helm/media-stack/cross-seed/templates && echo "no tabs"
```

Expected: every line prints its `ok` message; nothing is missing.

- [ ] **Step 10: Check name derivation**

```bash
helm template cross-seed cluster/helm/media-stack/cross-seed --namespace media-stack \
  --set fullnameOverride=demo > /tmp/cross-seed-demo.yaml
grep -q 'claimName: demo-config' /tmp/cross-seed-demo.yaml && echo "derived claim ok"
grep -q 'name: demo' /tmp/cross-seed-demo.yaml && echo "derived name ok"
```

Expected: both `ok` lines print.

- [ ] **Step 11: Commit**

```bash
git add cluster/helm/media-stack/cross-seed
git commit -m "♻️ refactor(cross-seed): rebuild chart around the image's needs"
```

---

### Task 2: Rewrite the documentation

**Files:**
- Create: `cluster/helm/media-stack/cross-seed/README.md`
- Modify: `docs/charts/README.md:3-18`, `:42-51`, `:53-61`, `:85-88`, `:104-112`
- Modify: `cluster/helm/media-stack/autobrr/README.md:3-5`, `:81-92`, `:105-108`

**Interfaces:**
- Consumes: the values keys and rendered object names produced by Task 1.
- Produces: the house rule that autobrr is a reference for principles, not a directory to copy.

- [ ] **Step 1: Write the cross-seed README**

Cover, in this order:

1. What cross-seed does, upstream links (website `https://www.cross-seed.org/`, source `https://github.com/cross-seed/cross-seed`), and the image source: GHCR only; the Docker Hub namespace is a legacy mirror.
2. Deployment: ArgoCD Application `cluster/argocd/projects/media-stack/cross-seed.yaml`, release name `cross-seed`, four rendered objects, and the local render command.
3. Values table: `image.*`, `strategy`, `args`, `service.port`, `storage.*`, `configFile.*`, `torrents.*`, `probes.*`, `resources.*`, `securityContext`, `nodeSelector`.
4. Configuration: `config.js` comes from Secret `cross-seed`, key `config.js`, mounted read-only with `subPath`; a change needs a pod restart; the UI is not exposed.
5. State: everything under `/config` (`cross-seed.db`, `cross-seeds/`, `torrent_cache/`, `logs/`); the claim is created by the chart unless `storage.existingClaim` is set.
6. Torrents: qBittorrent's `BT_backup` over NFS, mounted read-only at `/torrents`; cross-seed injects through the API, so it never writes there.
7. Probes: liveness `GET /api/ping`; readiness deliberately absent because no unauthenticated dependency-aware endpoint exists and the Service carries no user traffic.
8. UID 1100 runbook: config claim must be chowned on the node first; order is scale to zero, resolve claim to PV to host path, `chown -R 1100:1100`, scale back, confirm a write.
9. Upgrade rule: `image.tag` and `appVersion` move together; Renovate updates the tag only.
10. Files table and uninstall note, including that the old `cross-seed2` claim was removed from the chart and must be deleted manually after an emptiness check.

- [ ] **Step 2: Rewrite the reference section of `docs/charts/README.md`**

Replace lines 3-8 with:

```markdown
Most charts in `cluster/helm/` are written here rather than vendored. This page is the checklist
every one of them is expected to satisfy. [`cluster/helm/media-stack/autobrr`](../../cluster/helm/media-stack/autobrr)
is the reference implementation: a worked example of the principles and the contract below, not a
directory to copy byte for byte.

**Use `autobrr` as a reference for naming, labels, claim shape and probe honesty, then build the
chart the image actually needs.**
```

Keep the "Why a reference chart and not a shared library" section, but replace its last paragraph
(lines 17-18) with:

```markdown
The consequence is real and accepted: a fix to a shared principle must be applied 20-odd times. The
contract below is what makes that mechanical. Each chart is allowed to diverge in its values and its
templates where the image demands it; it is not allowed to diverge from the contract.
```

- [ ] **Step 3: Add the file-from-Secret and readiness rules**

In "How configuration reaches the app", change the third row to:

```markdown
| A file the app reads, such as `config.yaml` | **A ConfigMap mounted as a volume**, or a **Secret** when the file contains credentials | Only here does the extra object earn its place: the app needs a file on disk, not a variable. A `subPath` mount does not update on change, so the chart README must say a restart is required |
```

After the "Required once the app needs it" table, add:

```markdown
Readiness applies when the app serves traffic or exposes a dependency-aware endpoint. If the image
has no unauthenticated endpoint that reports dependency health, do not point readiness at the
liveness path just to fill the field: disable readiness and say why in the chart README. `cross-seed`
is the worked example.

This narrows requirement 13 rather than waiving it: 13 still applies whenever the app serves traffic
or exposes an endpoint that reports dependency health, and steps aside only for a chart like
`cross-seed` that does neither.
```

- [ ] **Step 4: Update the hardening table, checks and examples**

In the "Changing the UID" table, change the rows to:

```markdown
| `autobrr` | yes | capabilities dropped, seccomp, no privilege escalation | **yes — 1100**, claim chowned first |
| `cross-seed` | liveness only | capabilities dropped, seccomp, no privilege escalation, no API token | **yes — 1100**, claim chowned first |
| everything else | no | no | no |
```

Replace lines 104-106 with:

```markdown
Copy-paste leftovers are still the most common defect: every chart in this repo started from another
one. Grep for the original app's name and for its storage names before you commit, and check that the
claim name is derived rather than hardcoded.
```

Replace the "What good looks like" paragraph with two examples:

```markdown
`autobrr` (2026-11-11): five objects render, ingress fully driven by values, env inline in the
Deployment, claim name derived from the release.

`cross-seed` (2026-11-12): four objects render, no ingress because the UI is not exposed, named
`configFile` and `torrents` values instead of raw pod spec, liveness only, claim name derived from
the release. It shares the naming helpers and the generic Service, ServiceAccount and claim
templates with `autobrr`; the Deployment and the values are built around cross-seed's read-only
config file, its NFS torrent directory and its liveness-only probe.
```

- [ ] **Step 5: Update the autobrr README**

Replace lines 3-5 with:

```markdown
autobrr watches IRC announce channels and sends matching releases to your download client. It is the
reference chart for the principles every hand-maintained chart follows — see
[`docs/charts/README.md`](../../../../docs/charts/README.md) for the contract it satisfies.
```

Replace the "Using this chart as a template" section (lines 81-92) with:

```markdown
## Using this chart as a reference

Read the contract, then build the chart the image needs. Take from this chart:

- the naming helpers (`app.fullname`, `app.labels`, `app.selectorLabels`);
- the claim name derived from the release;
- values-driven routing, when the app is exposed at all;
- the probe rule: liveness on the app's own health endpoint, readiness only when it says something
  different.

Adapt everything else to the image. Add named values for the app's own mounts and configuration
sources instead of hiding them in raw pod spec. Leave out what the app does not need; `cross-seed`
has no IngressRoute because its UI is not exposed.

Do not copy this directory byte for byte. A chart that shares this chart's text but not its app's
requirements is harder to maintain than one that shares only the contract.
```

Replace lines 105-108 (the ConfigMap paragraph) with:

```markdown
There is deliberately no ConfigMap: this app takes its configuration from environment variables only.
A chart for an app that reads a file adds a ConfigMap, or mounts a Secret when the file contains
credentials, and documents whether a change needs a restart — see the rule in
[`docs/charts/README.md`](../../../../docs/charts/README.md).
```

- [ ] **Step 6: Check the documentation**

```bash
! grep -rn 'reference implementation to copy\|Copy `autobrr`\|change only:' docs/charts/README.md cluster/helm/media-stack/autobrr/README.md && echo "copy language gone"
grep -n 'cross-seed' docs/charts/README.md
test -f cluster/helm/media-stack/cross-seed/README.md && echo "readme exists"
```

Expected: "copy language gone", the cross-seed mentions, and "readme exists".

- [ ] **Step 7: Commit**

```bash
git add docs/charts/README.md cluster/helm/media-stack/autobrr/README.md \
        cluster/helm/media-stack/cross-seed/README.md
git commit -m "📝 docs(charts): make autobrr a reference, not a template"
```

---

### Task 3: Whole-repo verification and handover

**Files:** none.

**Interfaces:**
- Consumes: Tasks 1 and 2.
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

Both are pre-existing on `develop` and unrelated to this branch: `plex-media.yaml` points at
`cluster/helm/nfs/plex-media` while the directory is `plexmedia`, and `nfs-audio.yaml` points at
`$values/cluster/helm/nfs/audio/values.yaml` while the directory is `nfs-audio`. Any third line is a
new breakage introduced by this branch and must be fixed.

- [ ] **Step 3: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push -u origin feature/cross-seed-chart-refactor
```

- [ ] **Step 4: Open the pull request**

Use the GitHub MCP `create_pull_request`: head `feature/cross-seed-chart-refactor`, base `develop`. Title
`♻️ refactor(cross-seed): rebuild chart around the image's needs`. The body covers the four objects,
the removed init container and `cross-seed2` claim, the image upgrade to `6.13.9`, the probe decision,
the UID 1100 pre-flight, and the documentation change. Do not merge; the operator reviews and merges.

---

## Post-merge operator runbook

The chart changes live state. Do these in order, after the PR is approved and before/after the
manual ArgoCD sync (auto-sync is off for this Application):

1. **Config drift check.** Compare the Secret with the file on the claim:

   ```bash
   kubectl -n media-stack get secret cross-seed -o jsonpath='{.data.config\.js}' | base64 -d > /tmp/secret-config.js
   kubectl -n media-stack exec deploy/cross-seed -- cat /config/config.js > /tmp/claim-config.js
   diff -u /tmp/secret-config.js /tmp/claim-config.js
   ```

   If they differ, stop and decide which wins. The Secret is the intended source.

2. **`cross-seed2` emptiness check.** `kubectl -n media-stack exec deploy/cross-seed -- du -sh /cross-seed`.
   If it holds data, copy it off and stop.

3. **Ownership.** Scale the Deployment to zero, resolve `cross-seed-config` to its PV and the PV to
   its host path, `chown -R 1100:1100` that directory on `streaming`.

4. **Sync** the Application manually and confirm the Deployment rolls, the pod runs as 1100, the
   liveness probe passes, and the `rss` / `inject` jobs appear in the logs.

5. **Delete `cross-seed2`** once the new pod is healthy: `kubectl -n media-stack delete pvc cross-seed2`.
