# radarr and radarr-anime chart refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the `radarr` and `radarr-anime` charts to the hand-maintained chart contract, on the `prowlarr` shape, with named media mounts, `/ping` probes, `warn` logging, correct sizing, a working exportarr wiring, and `radarr-anime` no longer running its app as root.

**Architecture:** Two independent charts, each with the `prowlarr` values schema and the shared unprefixed helpers. Each chart renders Deployment, Service, ServiceAccount, config PVC and IngressRoute. The two NFS media claims are named values. The app upgrade to `6.4.4` and the `radarr-anime` UID change happen in the same charts; the cluster-side pre-flight is an operator runbook because the harness cannot write to the cluster.

**Tech Stack:** Helm 4.x, Kubernetes YAML, ArgoCD, Traefik v2 CRDs.

**Spec:** `docs/superpowers/specs/2026-11-12-radarr-chart-refactor-design.md`

## Global Constraints

- Image: `lscr.io/linuxserver/radarr:6.4.4.10685-ls326`; `appVersion: "6.4.4.10685-ls326"`; chart `version: 1.0.0`.
- Releases and fullnames stay `radarr` and `radarr-anime`; claims stay `radarr-config` and `radarr-anime-config`.
- Both charts: `strategy: Recreate`, `service.port: 7878`, node `streaming`.
- Media mounts: `downloads` → claim `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` at `/downloads`; `media` → claim `pvc-plex-media` at `/movies`; both read-write.
- `env`: `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`, `RADARR__LOG__LEVEL=warn`, `RADARR__LOG__CONSOLELEVEL=warn`.
- Security: `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`; **no** `runAsNonRoot`, `runAsUser`, `fsGroup` or capability drop (the LinuxServer init needs root).
- Resources: requests `10m` CPU / `256Mi` memory; limit `1Gi` memory; no CPU limit.
- Probes on `/ping`: startup 5s × 60; liveness 20s delay, 30s period, 3 failures; readiness 10s delay, 15s period, 3 failures.
- Ingress: `traefik.containo.us/v1alpha1`, entry point `web`, no TLS, hosts `radarr.kub.local` / `radarr-anime.kub.local`.
- Exportarr `v2.3.0`, port `9707`, API key from the Secret named after the release (`radarr` / `radarr-anime`), key `secret`.
- No ConfigMap, no `networking.k8s.io`, no `32400`, no `chart-example.local`, no tabs.
- Every object carries `app.labels`; every name goes through the `app.*` helpers.
- The five rendered objects per release are exactly: Deployment, Service, ServiceAccount, IngressRoute (all `<fullname>`) and PVC `<fullname>-config`.

## Review Focus

- **Claim-name drift is silent data loss.** A release named `radarr` must render `claimName: radarr-config`, and `--set fullnameOverride=demo` must render `demo-config`. Pinned in Tasks 1 and 2.
- **`prune: true` turns a missing or renamed object into a live deletion.** The render must contain exactly the five object names, no more and no fewer. Pinned in Tasks 1 and 2.
- **`radarr-anime` at PUID 0 starts, then fails on the first write after the drop to 1100** unless `/config` is chowned first. The chart must render 1100, and the README must carry the runbook. Pinned in Task 2.
- **Exportarr stays 401 if the Secret is not patched**, and a fresh pod hides the failure until the next scrape. The README must say the Secret repair is separate from the chart change. Pinned in Tasks 1 and 2.
- **Auto-sync with `selfHeal` rolls both apps before the pre-flight.** The operator pauses automation first; the plan must not assume a manual-sync cluster. Pinned in the handover section.

---

### Task 0: Commit the approved design and this plan

**Files:**
- Add: `docs/superpowers/specs/2026-11-12-radarr-chart-refactor-design.md`
- Add: `docs/superpowers/plans/2026-11-12-radarr-chart-refactor.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the documents every later task is measured against.

- [ ] **Step 1: Confirm the branch**

Run: `git branch --show-current`
Expected: `feature/radarr-chart-refactor`

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-11-12-radarr-chart-refactor-design.md \
        docs/superpowers/plans/2026-11-12-radarr-chart-refactor.md
git commit -m "📝 docs(radarr): add chart refactor design and plan"
```

---

### Task 1: Rebuild the `radarr` chart

**Files:**
- Modify: `cluster/helm/media-stack/radarr/Chart.yaml`
- Modify: `cluster/helm/media-stack/radarr/values.yaml`
- Modify: `cluster/helm/media-stack/radarr/templates/_helpers.tpl`
- Modify: `cluster/helm/media-stack/radarr/templates/deployment.yaml`
- Modify: `cluster/helm/media-stack/radarr/templates/service.yaml`
- Create: `cluster/helm/media-stack/radarr/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/radarr/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/radarr/templates/ingressroute.yaml`
- Create: `cluster/helm/media-stack/radarr/templates/NOTES.txt`
- Create: `cluster/helm/media-stack/radarr/README.md`
- Delete: `cluster/helm/media-stack/radarr/templates/pvc-config.yaml`
- Delete: `cluster/helm/media-stack/radarr/templates/ingress.yaml`

**Interfaces:**
- Consumes: the contract in `docs/charts/README.md`; `cluster/helm/media-stack/prowlarr` as the reference implementation.
- Produces: values keys `nameOverride`, `fullnameOverride`, `replicaCount`, `strategy`, `image.*`, `imagePullSecrets`, `serviceAccount.*`, `podAnnotations`, `podLabels`, `podSecurityContext`, `securityContext`, `service.*`, `container.port`, `ingressRoute.*`, `storage.*`, `env`, `extraEnv`, `envFrom`, `downloads.*`, `media.*`, `volumes`, `volumeMounts`, `exportarr.*`, `nodeSelector`, `tolerations`, `affinity`, `resources.*`, `probes.*`; helper names `app.name`, `app.fullname`, `app.chart`, `app.labels`, `app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName`, `app.containerPort`, `app.exportarrSecretName`.

- [ ] **Step 1: Record the baseline render and object names**

```bash
helm template radarr cluster/helm/media-stack/radarr --namespace media-stack > /tmp/radarr-before.yaml
awk '/^kind:/{k=$2} /^  name:/{print k, $2}' /tmp/radarr-before.yaml | sort
```

Expected: `Deployment radarr`, `IngressRoute radarr`, `PersistentVolumeClaim radarr-config`, `Service radarr`. This is the set that must survive.

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: radarr
description: Radarr manages movie libraries, watches indexers and hands release grabs to a download client.
type: application
# Chart version. Renovate bumps this when the image tag moves.
version: 1.0.0
# Must match image.tag in values.yaml.
appVersion: "6.4.4.10685-ls326"
icon: https://raw.githubusercontent.com/Radarr/Radarr/develop/Logo/1024.png
home: https://radarr.video/
sources:
  - https://github.com/Radarr/Radarr
keywords:
  - movies
  - torrent
  - usenet
  - automation
```

- [ ] **Step 3: Write `values.yaml`**

```yaml
# Default values for radarr.
# This chart follows the contract in docs/charts/README.md and the shape of
# `prowlarr`, the worked *arr example. The differences are the ones this app needs:
# a LinuxServer image that starts as root and drops to PUID/PGID, an exportarr
# sidecar, probes on Radarr's own `/ping`, and the two media claims it reads.

# -- Override the chart name.
nameOverride: ""
# -- Override the generated release name.
fullnameOverride: ""

replicaCount: 1

# -- `Recreate` is required with a ReadWriteOnce config volume.
strategy: Recreate

image:
  # LinuxServer publishes on lscr.io. The image starts as root and drops to
  # PUID/PGID through s6, so the securityContext cannot use runAsNonRoot.
  repository: lscr.io/linuxserver/radarr
  # -- Pin the tag. Never `latest`. Renovate manages this.
  tag: "6.4.4.10685-ls326"
  pullPolicy: IfNotPresent

imagePullSecrets: []

serviceAccount:
  # -- Create a dedicated ServiceAccount with no mounted token: Radarr never
  # talks to the Kubernetes API.
  create: true
  automount: false
  # -- Defaults to the fullname.
  name: ""
  annotations: {}

# -- Extra pod annotations. Do not repeat `prometheus.io/*` here: the exportarr
# block owns them, so the announced port cannot drift from the listener.
podAnnotations: {}
podLabels: {}

# `fsGroup` is deliberately absent: local-path is hostPath-backed and the kubelet
# does not apply fsGroup ownership to it. The LinuxServer init needs root to
# prepare /config, then drops to PUID/PGID.
podSecurityContext:
  seccompProfile:
    type: RuntimeDefault

securityContext:
  allowPrivilegeEscalation: false

service:
  type: ClusterIP
  port: 7878

container:
  # -- Container port. Defaults to `service.port`; set only when the app listens
  # somewhere else.
  port: null

ingressRoute:
  # -- Render a Traefik IngressRoute. This repo never uses networking.k8s.io.
  enabled: true
  # -- API group. Flip to `traefik.io/v1alpha1` when Traefik v3 lands.
  apiVersion: traefik.containo.us/v1alpha1
  entryPoints:
    - web
  host: radarr.kub.local
  pathPrefix: /
  middlewares: []
  labels: {}
  annotations: {}
  tls:
    enabled: false
    secretName: ""
    certResolver: ""
    options: ""

storage:
  # -- Render the config claim. Radarr keeps its SQLite database there.
  enabled: true
  existingClaim: ""
  className: local-path
  accessModes:
    - ReadWriteOnce
  size: 128Mi
  mountPath: /config

# -- Inline environment. A change rolls the pod, so no ConfigMap.
env:
  TZ: "Europe/Paris"
  # LinuxServer images drop from root to these IDs after preparing /config.
  PUID: "1100"
  PGID: "1100"
  # Warnings and errors only: Fluent Bit ships every line to Loki unfiltered and
  # Radarr's default is `info`, one line per request, scan and import. The file
  # level and the console level (the one Loki sees) move together.
  RADARR__LOG__LEVEL: "warn"
  RADARR__LOG__CONSOLELEVEL: "warn"

# -- Variables sourced from a Secret, as `{ name, valueFrom }`.
extraEnv: []
envFrom: []

# qBittorrent's completed downloads. Radarr imports from here into the library.
downloads:
  existingClaim: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner
  mountPath: /downloads

# The Plex movie library. Radarr writes new release folders here.
media:
  existingClaim: pvc-plex-media
  mountPath: /movies

# -- Additional volumes, rendered verbatim.
volumes: []
# -- Additional volume mounts, rendered verbatim.
volumeMounts: []

exportarr:
  # -- Render the sidecar and its scrape annotations together.
  enabled: true
  image:
    repository: ghcr.io/onedr0p/exportarr
    pullPolicy: IfNotPresent
    tag: "v2.3.0"
  # -- Port exportarr listens on, and the port in the scrape annotation.
  port: 9707
  apiKey:
    # -- Secret holding Radarr's API key. Empty means the fullname.
    secretName: ""
    secretKey: secret
  resources:
    requests:
      cpu: 2m
      memory: 56Mi
    limits:
      memory: 112Mi

nodeSelector:
  # -- Every workload is pinned; local-path claims are node-bound.
  kubernetes.io/hostname: streaming

tolerations: []
affinity: {}

# Sized from Prometheus over 30 days: 257Mi peak working set, 20m peak CPU.
# No CPU limit, here or anywhere in this cluster: a limit throttles the short
# burst an import or a library scan needs. The memory limit is about 4x the
# observed peak.
resources:
  requests:
    cpu: 10m
    memory: 256Mi
  limits:
    memory: 1Gi

# Probes on `/ping`, Radarr's own unauthenticated health endpoint.
# Liveness and readiness share it on purpose: the only dependency is the SQLite
# database, and a database that cannot be opened stops the process from starting.
# Startup gets 300s because the first start after a version bump migrates it.
probes:
  startup:
    enabled: true
    path: /ping
    periodSeconds: 5
    timeoutSeconds: 5
    failureThreshold: 60
  liveness:
    enabled: true
    path: /ping
    initialDelaySeconds: 20
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    enabled: true
    path: /ping
    initialDelaySeconds: 10
    periodSeconds: 15
    timeoutSeconds: 5
    failureThreshold: 3
```

- [ ] **Step 4: Replace the generic templates with prowlarr's**

Copy these files from `cluster/helm/media-stack/prowlarr/templates/` verbatim, overwriting the
existing ones where they exist:

- `_helpers.tpl`
- `service.yaml`
- `serviceaccount.yaml`
- `persistentvolumeclaim.yaml`
- `ingressroute.yaml`
- `NOTES.txt`

They contain no app-specific text. Do not rename the helpers.

- [ ] **Step 5: Add the two media mounts to `templates/deployment.yaml`**

Start from `prowlarr/templates/deployment.yaml`. Keep everything: metadata, annotations, selector,
serviceAccountName, security context, inline env (`env` then `extraEnv`, then `envFrom`), port, the
three probes, resources, nodeSelector, tolerations, affinity.

In the container's `volumeMounts`, after the `config` mount and before `with .Values.volumeMounts`, add:

```yaml
            {{- if .Values.downloads.existingClaim }}
            - name: downloads
              mountPath: {{ .Values.downloads.mountPath }}
            {{- end }}
            {{- if .Values.media.existingClaim }}
            - name: media
              mountPath: {{ .Values.media.mountPath }}
            {{- end }}
```

In the pod's `volumes`, after the `config` volume and before `with .Values.volumes`, add:

```yaml
        {{- if .Values.downloads.existingClaim }}
        - name: downloads
          persistentVolumeClaim:
            claimName: {{ .Values.downloads.existingClaim }}
        {{- end }}
        {{- if .Values.media.existingClaim }}
        - name: media
          persistentVolumeClaim:
            claimName: {{ .Values.media.existingClaim }}
        {{- end }}
```

The exportarr sidecar keeps prowlarr's structure, but its `args` list must select the Radarr
handler: `args:` followed by `- radarr`, never prowlarr's `- prowlarr`. Everything else in that
sidecar block stays as prowlarr has it — image, port, `PORT`, `URL`, `APIKEY` and resources. Copying
the reference chart verbatim without this one-line change leaves exportarr running the Prowlarr
handler against Radarr, which queries the wrong API.

Its comment must also describe this chart, not Prowlarr's stale-key defect. Use:

```yaml
        # Exportarr sidecar. The API key is read from the Secret named after the
        # release; a key that no longer matches the app answers 401 and the
        # Prometheus target goes down. Replacing the Secret value is an operator
        # step, not a chart change.
```

- [ ] **Step 6: Delete the obsolete templates**

```bash
git rm cluster/helm/media-stack/radarr/templates/pvc-config.yaml \
       cluster/helm/media-stack/radarr/templates/ingress.yaml
```

- [ ] **Step 7: Lint and render**

```bash
helm lint cluster/helm/media-stack/radarr --namespace media-stack
helm template radarr cluster/helm/media-stack/radarr --namespace media-stack > /tmp/radarr-after.yaml
```

Expected: `1 chart(s) linted, 0 chart(s) failed`; template exits 0.

- [ ] **Step 8: Check the rendered contract**

```bash
awk '/^kind:/{k=$2} /^  name:/{print k, $2}' /tmp/radarr-after.yaml | sort
grep -q 'claimName: radarr-config' /tmp/radarr-after.yaml && echo "claim ok"
grep -q 'Host(`radarr.kub.local`)' /tmp/radarr-after.yaml && echo "host ok"
grep -q 'RADARR__LOG__CONSOLELEVEL' /tmp/radarr-after.yaml && echo "log level ok"
test "$(grep -c 'Probe:' /tmp/radarr-after.yaml)" = 3 && echo "three probes"
grep -q 'image: "lscr.io/linuxserver/radarr:6.4.4.10685-ls326"' /tmp/radarr-after.yaml && echo "image ok"
grep -q 'claimName: pvc-plex-media' /tmp/radarr-after.yaml && echo "media ok"
grep -q 'claimName: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner' /tmp/radarr-after.yaml && echo "downloads ok"
grep -A1 'args:' /tmp/radarr-after.yaml | grep -q -- '- radarr' && echo "exporter args ok"
! grep -q -- '- prowlarr' /tmp/radarr-after.yaml && echo "no prowlarr args"
test "$(awk '/^kind:/{c++} END{print c}' /tmp/radarr-after.yaml)" = 5 && echo "five objects"
! grep -qE '32400|chart-example|radarr-webui|networking.k8s.io|value: "0"' /tmp/radarr-after.yaml && echo "legacy absent"
! grep -rnP '\t' cluster/helm/media-stack/radarr && echo "no tabs"
```

Expected object list, exactly:

```text
Deployment radarr
IngressRoute radarr
PersistentVolumeClaim radarr-config
Service radarr
ServiceAccount radarr
```

Every `echo` prints, and no extra object appears.

- [ ] **Step 9: Check name derivation**

```bash
helm template radarr cluster/helm/media-stack/radarr --namespace media-stack \
  --set fullnameOverride=demo > /tmp/radarr-demo.yaml
grep -q 'claimName: demo-config' /tmp/radarr-demo.yaml && echo "derived claim ok"
grep -q 'name: demo' /tmp/radarr-demo.yaml && echo "derived name ok"
```

Expected: both `ok` lines print.

- [ ] **Step 10: Write `README.md`**

Cover, in this order:

1. What Radarr does, upstream links (site `https://radarr.video/`, source
   `https://github.com/Radarr/Radarr`, image `https://docs.linuxserver.io/images/docker-radarr/`), and
   that the image comes from `lscr.io`.
2. Deployment: ArgoCD Application `cluster/argocd/projects/media-stack/radarr.yaml`, release `radarr`,
   the five rendered objects, and the local render command.
3. Values table: `image.*`, `strategy`, `service.port`, `storage.*`, `downloads.*`, `media.*`,
   `ingressRoute.*`, `env`, `exportarr.*`, `probes.*`, `resources.*`, `securityContext`,
   `nodeSelector`.
4. The media mounts: which claim, which path, why both are read-write.
5. Probes: `/ping`, why liveness and readiness share it, why startup is 300s here.
6. Logs: `warn` on both sinks, and what the UI retains instead.
7. Metrics: the exportarr sidecar, the scrape annotations, and that the **Secret `radarr` must hold
   Radarr's current API key** — a stale key makes `/metrics` answer 500 with 401 in the sidecar log.
   Say that the chart change does not repair the Secret.
8. UID: `radarr` already runs as 1100; a claim recreated from scratch starts root-owned and needs
   `chown -R 1100:1100` on `streaming` before the pod can write (`autobrr` README has the procedure).
9. Upgrade rule: `image.tag` and `appVersion` move together; Renovate updates the tag only.
10. Files table and the uninstall note (deleting the release deletes the claim).

- [ ] **Step 11: Commit**

```bash
git add cluster/helm/media-stack/radarr
git commit -m "♻️ refactor(radarr): align chart with the reference contract"
```

---

### Task 2: Rebuild the `radarr-anime` chart

**Files:**
- Modify: `cluster/helm/media-stack/radarr-anime/Chart.yaml`
- Modify: `cluster/helm/media-stack/radarr-anime/values.yaml`
- Modify: `cluster/helm/media-stack/radarr-anime/templates/_helpers.tpl`
- Modify: `cluster/helm/media-stack/radarr-anime/templates/deployment.yaml`
- Modify: `cluster/helm/media-stack/radarr-anime/templates/service.yaml`
- Create: `cluster/helm/media-stack/radarr-anime/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/radarr-anime/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/radarr-anime/templates/ingressroute.yaml`
- Create: `cluster/helm/media-stack/radarr-anime/templates/NOTES.txt`
- Create: `cluster/helm/media-stack/radarr-anime/README.md`
- Delete: `cluster/helm/media-stack/radarr-anime/templates/pvc-config.yaml`
- Delete: `cluster/helm/media-stack/radarr-anime/templates/ingress.yaml`

**Interfaces:**
- Consumes: Task 1's chart files. This chart is the same chart with a different release name.
- Produces: the same values keys and helpers as Task 1. The claim derives to `radarr-anime-config`; the
  API-key Secret derives to `radarr-anime`.

- [ ] **Step 1: Copy Task 1's chart and apply the deltas**

Copy `Chart.yaml`, `values.yaml`, `README.md` and every file under `templates/` from
`cluster/helm/media-stack/radarr/` to `cluster/helm/media-stack/radarr-anime/`, overwriting, then apply
exactly these changes:

| File | Change |
|---|---|
| `Chart.yaml` | `name: radarr-anime`; description `Radarr for the anime library: a second instance with its own database, quality profile and root folder.` |
| `values.yaml` | `ingressRoute.host: radarr-anime.kub.local` |
| `README.md` | title `radarr-anime`; Application path and release name; add the UID migration runbook from Step 5 |
| `templates/deployment.yaml` | exportarr `args` is `- radarr`, inherited from Task 1; nothing changes if the copy happens after this fix |
| everything else | unchanged; the `PUID`/`PGID` values are already `"1100"` |

Do not hardcode `radarr-anime-config` or `radarr-anime` anywhere: the fullname derives both.

- [ ] **Step 2: Delete the obsolete templates**

```bash
git rm cluster/helm/media-stack/radarr-anime/templates/pvc-config.yaml \
       cluster/helm/media-stack/radarr-anime/templates/ingress.yaml
```

- [ ] **Step 3: Lint and render**

```bash
helm lint cluster/helm/media-stack/radarr-anime --namespace media-stack
helm template radarr-anime cluster/helm/media-stack/radarr-anime --namespace media-stack > /tmp/radarr-anime-after.yaml
```

Expected: lint passes; template exits 0.

- [ ] **Step 4: Check the rendered contract**

```bash
awk '/^kind:/{k=$2} /^  name:/{print k, $2}' /tmp/radarr-anime-after.yaml | sort
grep -q 'claimName: radarr-anime-config' /tmp/radarr-anime-after.yaml && echo "claim ok"
grep -q 'Host(`radarr-anime.kub.local`)' /tmp/radarr-anime-after.yaml && echo "host ok"
grep -A1 'secretKeyRef:' /tmp/radarr-anime-after.yaml | grep -q 'name: radarr-anime' && echo "exporter secret ok"
grep -A1 'args:' /tmp/radarr-anime-after.yaml | grep -q -- '- radarr' && echo "exporter args ok"
! grep -q -- '- prowlarr' /tmp/radarr-anime-after.yaml && echo "no prowlarr args"
test "$(awk '/^kind:/{c++} END{print c}' /tmp/radarr-anime-after.yaml)" = 5 && echo "five objects"
grep -A1 -- '- name: PUID' /tmp/radarr-anime-after.yaml | grep -q 'value: "1100"' && echo "uid 1100"
! grep -A1 -- '- name: PUID' /tmp/radarr-anime-after.yaml | grep -q 'value: "0"' && echo "uid not 0"
! grep -qE '32400|chart-example|radarr-webui|networking.k8s.io' /tmp/radarr-anime-after.yaml && echo "legacy absent"
```

Expected object list, exactly:

```text
Deployment radarr-anime
IngressRoute radarr-anime
PersistentVolumeClaim radarr-anime-config
Service radarr-anime
ServiceAccount radarr-anime
```

Every `echo` prints.

- [ ] **Step 5: Document the UID runbook in the README**

Add a section explaining that until 2026-11-12 this instance ran with `PUID/PGID 0`, so its config
directory is root-owned, and that the chart now renders 1100. Before the new pod starts, the operator:

1. pauses auto-sync on the Application in the ArgoCD UI;
2. scales `radarr-anime` to zero;
3. resolves `radarr-anime-config` to its PV and the PV to its host path on `streaming`;
4. runs `chown -R 1100:1100` on that directory;
5. scales back to one, or leaves it down until the merge.

The failure this prevents: the pod starts, then fails on its first database write.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/media-stack/radarr-anime
git commit -m "♻️ refactor(radarr-anime): align chart and drop root"
```

---

### Task 3: Record both charts in the contract

**Files:**
- Modify: `docs/charts/README.md` (the hardening table and "What good looks like")

**Interfaces:**
- Consumes: the values and rendered objects from Tasks 1 and 2.
- Produces: nothing later tasks consume; documentation only.

- [ ] **Step 1: Add the two rows to the hardening table**

After the `prowlarr` row, add:

```markdown
| `radarr` | yes, on `/ping` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — starts as root, then drops to PUID 1100** |
| `radarr-anime` | yes, on `/ping` | seccomp, no privilege escalation; capabilities kept for the LinuxServer init | **no — starts as root, then drops to PUID 1100**; moved from 0 on 2026-11-12 |
```

- [ ] **Step 2: Add a "What good looks like" entry**

After the `prowlarr` paragraph, add:

```markdown
`radarr` and `radarr-anime` (2026-11-12): five objects each, the two NFS media claims as named
`downloads` and `media` values instead of raw pod spec, probes on `/ping` with a 300s startup budget
for the database migration, `RADARR__LOG__LEVEL`/`CONSOLELEVEL` at `warn`, and claim names that
already matched the derived `<fullname>-config` so no data moved. This is also the pair that moved a
LinuxServer instance off `PUID 0`, which is the chown runbook the contract's UID section describes.
```

- [ ] **Step 3: Check the documentation**

```bash
grep -n 'radarr' docs/charts/README.md
test "$(grep -c '^| `radarr' docs/charts/README.md)" = 2 && echo "two radarr rows"
grep -q '`radarr` and `radarr-anime` (2026-11-12)' docs/charts/README.md && echo "pair recorded"
```

Expected: the radarr mentions, then `two radarr rows` and `pair recorded`.

- [ ] **Step 4: Commit**

```bash
git add docs/charts/README.md
git commit -m "📝 docs(charts): record radarr and radarr-anime in the contract"
```

---

### Task 4: Whole-repo verification and handover

**Files:** none.

**Interfaces:**
- Consumes: Tasks 1 to 3.
- Produces: a branch that renders every chart and resolves every ArgoCD path, pushed with a PR.

- [ ] **Step 1: Render every self-contained chart**

```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```

Expected: only the known `SKIP` lines for the wrapper charts, no `FAIL`.

- [ ] **Step 2: Check the ArgoCD references**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")

while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u)
```

Expected output, exactly, and nothing else:

```text
BROKEN PATH cluster/helm/nfs/plex-media
BROKEN VALUES /cluster/helm/nfs/audio/values.yaml
```

Both are pre-existing on `develop`. Any third line is a new breakage.

- [ ] **Step 3: Rebase onto the integration branch**

```bash
git fetch origin
git rebase origin/develop
```

Expected: clean rebase. If the two Plex commits on `develop` conflict, resolve in favour of
`develop` and re-run Step 1. The chart directories are untouched by them.

- [ ] **Step 4: Re-run the two chart checks after the rebase**

```bash
helm template radarr cluster/helm/media-stack/radarr --namespace media-stack >/dev/null && echo radarr
helm template radarr-anime cluster/helm/media-stack/radarr-anime --namespace media-stack >/dev/null && echo radarr-anime
```

Expected: both words print.

- [ ] **Step 5: Push and open the pull request**

```bash
git push -u origin feature/radarr-chart-refactor
```

Use the GitHub MCP `create_pull_request`: head `feature/radarr-chart-refactor`, base `develop`. Title
`♻️ refactor(radarr): align both charts with the reference contract`. The body covers:

- both charts rebuilt on the `prowlarr` shape, with the two media claims as named values;
- the image move to `lscr.io/linuxserver/radarr:6.4.4.10685-ls326`;
- `radarr-anime` moved from `PUID 0` to `1100`, and the chown runbook;
- probes on `/ping` with a 300s startup budget;
- sizing from the 30-day Prometheus peaks;
- exportarr wiring unchanged, but the Secret must be patched separately (401 today);
- the operator pre-flight order: pause auto-sync, patch Secrets, chown, merge, sync, verify.

Do not merge. The operator merges after the pre-flight.

---

## Operator handover

The merge triggers nothing until the pre-flight is done, because automation is paused first. The exact
commands are in the spec's **Operator runbook** section; do not repeat them here. The order is:

1. Pause automation on both Applications in the ArgoCD UI.
2. Patch Secrets `radarr` and `radarr-anime` from `~/.config/secrets/homelab.env`.
3. Chown `radarr-anime-config` on `streaming`.
4. Merge the PR.
5. Sync `radarr`, verify, then sync `radarr-anime`, verify (pods `2/2`, `uid=1100`, `writable`, no 401,
   `/ping` OK).
6. Re-enable automation.
