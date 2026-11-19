# NFS provisioner refactor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace nine uneven chart directories for eight NAS exports with one wrapper chart, eight values files and eight Applications, preserving every live object name.

**Architecture:** `cluster/helm/nfs/provisioner` is a `v2` chart whose only dependency is upstream `nfs-subdir-external-provisioner` `4.0.18`. Shared settings live in its `values.yaml` under the dependency's key; `values/<release>.yaml` carries the four knobs that differ per export (path, `nameOverride`, `fullnameOverride`, StorageClass name or access mode). Each ArgoCD Application keeps its name, namespace and release identity and points at the one chart with its own values file.

**Tech Stack:** Helm 4.3.1 (`helm dependency build`, `helm template`), ArgoCD Applications with `helm.valueFiles`, yq for assertions.

**Spec:** `docs/superpowers/specs/2026-11-19-nfs-provisioner-refactor-design.md`

## Global Constraints

- Dependency pinned exactly: `nfs-subdir-external-provisioner` version `4.0.18`, repository `https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner`; image `registry.k8s.io/sig-storage/nfs-subdir-external-provisioner:v4.0.2`.
- NFS server `192.168.1.41`; `mountOptions` always exactly `nfsvers=4.2`, `rsize=1048576`, `wsize=1048576`, `nolock`, `async` — the PV and PVC templates are gated on that list being non-empty.
- Chart: `apiVersion: v2`, `name: nfs-provisioner`, `version: 0.1.0`, `appVersion: "4.0.2"`, `type: application`.
- Every values key for the subchart is nested under `nfs-subdir-external-provisioner:`; top-level keys are ignored silently.
- Resources: requests `cpu: 10m`, `memory: 64Mi`; limits `memory: 192Mi`; no CPU limit. `nodeSelector: kubernetes.io/hostname: streaming`. `podSecurityContext` `runAsUser/runAsGroup/fsGroup: 1100`. `strategyType: Recreate`, `replicaCount: 1`.
- StorageClass: `create: true`, `defaultClass: false`, `allowVolumeExpansion: true`, `reclaimPolicy: Delete`, `archiveOnDelete: true`, `volumeBindingMode: Immediate`; `accessModes` `ReadWriteOnce` unless the task says otherwise.
- CRLF on every file under `cluster/argocd/**`; LF everywhere else.
- No `syncPolicy.automated` in any Application (AGENTS.md §7.1).
- Board: file the task in the Vikunja `homelab` project and move it to `Doing` before Task 1; move it to `Validation` with the branch, PR and verification output at Task 6.

## Review Focus

1. **A values key placed at the wrapper's top level.** The subchart then renders its own defaults — path `/nfs-storage`, class `nfs-client`, empty server — and the sync creates a ninth StorageClass pointing nowhere. Pinned by Task 2's path and class assertions.
2. **`mountOptions` emptied or unset.** PV and PVC disappear from the render, the sync prunes them, and the claim goes `Terminating` — this is how four pairs in this cluster were made. Pinned by Task 2 asserting both objects exist for all eight.
3. **Name drift.** A wrong `nameOverride` fails the sync on an immutable selector; a wrong `fullnameOverride` creates a second claim and orphans a mounted one. Pinned by Task 2's exact-name and selector matrix.
4. **CRLF lost on the eight Application files.** ArgoCD still parses them, so nothing complains, and the next edit produces a whole-file diff. Pinned by Task 3's `file` check.
5. **A chart directory deleted while an Application still points at it.** ArgoCD reports Synced/Healthy deploying nothing — the exact bug being fixed. Pinned by Task 4's reference loop.

---

### Task 1: The wrapper chart and its shared values

**Files:**
- Create: `cluster/helm/nfs/provisioner/Chart.yaml`
- Create: `cluster/helm/nfs/provisioner/values.yaml`
- Create: `cluster/helm/nfs/provisioner/README.md`
- Modify: `.gitignore` (after the `cluster/helm/argocd/Chart.lock` line)

**Interfaces:**
- Consumes: nothing.
- Produces: a chart at `cluster/helm/nfs/provisioner` that renders the upstream provisioner from `values.yaml` alone, and accepts per-release overrides under the key `nfs-subdir-external-provisioner` with the subchart's own field names (`nameOverride`, `fullnameOverride`, `nfs.path`, `nfs.volumeName`, `storageClass.name`, `storageClass.accessModes`).

- [ ] **Step 1: Confirm the chart does not exist yet**

Run: `helm template test ./cluster/helm/nfs/provisioner`
Expected: FAIL — `Error: path "cluster/helm/nfs/provisioner" not found`.

- [ ] **Step 2: Create `cluster/helm/nfs/provisioner/Chart.yaml`**

```yaml
apiVersion: v2
name: nfs-provisioner
description: NFS subdir external provisioners — one release per NAS export
type: application
version: 0.1.0
appVersion: "4.0.2"
dependencies:
  - name: nfs-subdir-external-provisioner
    version: 4.0.18
    repository: https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner
```

- [ ] **Step 3: Create `cluster/helm/nfs/provisioner/values.yaml`**

```yaml
# Shared by all eight exports. A release adds only what differs, in values/<release>.yaml.
# Everything here is read by the subchart, so it stays under the dependency's name.
nfs-subdir-external-provisioner:
  replicaCount: 1
  strategyType: Recreate

  image:
    repository: registry.k8s.io/sig-storage/nfs-subdir-external-provisioner
    tag: v4.0.2
    pullPolicy: IfNotPresent

  nfs:
    server: 192.168.1.41
    # The subchart renders the static PV and PVC only when this list is non-empty.
    # Emptying it deletes both objects on the next sync.
    mountOptions:
      - nfsvers=4.2
      - rsize=1048576
      - wsize=1048576
      - nolock
      - async
    reclaimPolicy: Retain

  storageClass:
    create: true
    defaultClass: false
    allowVolumeExpansion: true
    reclaimPolicy: Delete
    archiveOnDelete: true
    accessModes: ReadWriteOnce
    volumeBindingMode: Immediate

  podSecurityContext:
    runAsUser: 1100
    runAsGroup: 1100
    fsGroup: 1100

  # Measured 2026-11-18 over 5 days, across the provisioners in media-stack:
  # p95 49Mi, peak 99Mi (working set). No CPU limit, per the house rule.
  resources:
    requests:
      cpu: 10m
      memory: 64Mi
    limits:
      memory: 192Mi

  nodeSelector:
    kubernetes.io/hostname: streaming
```

- [ ] **Step 4: Add the build artifacts to `.gitignore`**

Append below `cluster/helm/argocd/Chart.lock`:

```
# Built by ArgoCD at sync time (see the NFS provisioner design §The target shape)
cluster/helm/nfs/provisioner/charts/
cluster/helm/nfs/provisioner/Chart.lock
```

- [ ] **Step 5: Render from the shared values only and read the danger**

Run:
```bash
helm dependency build cluster/helm/nfs/provisioner
helm template test ./cluster/helm/nfs/provisioner | yq -r 'select(.kind=="StorageClass" or .kind=="PersistentVolume") | .kind + "/" + .metadata.name + " " + (.spec?.nfs?.path // .provisioner)'
```
Expected: `StorageClass/nfs-client cluster.local/test-nfs-subdir-external-provisioner` and
`PersistentVolume/pv-test-nfs-subdir-external-provisioner /nfs-storage`. This is the state a missing
per-release file produces — the reason every export needs one.

- [ ] **Step 6: Write `cluster/helm/nfs/provisioner/README.md`**

Six lines: what the chart is, that each export is one release with `values/<release>.yaml`, that
`fullnameOverride` and `nameOverride` are what keep live object names stable, that `mountOptions` must
stay non-empty or the PV/PVC are pruned, and that `charts/` is built at sync time by ArgoCD.

- [ ] **Step 7: Verify the build is reproducible and untracked**

Run: `helm dependency build cluster/helm/nfs/provisioner && git status --short cluster/helm/nfs/provisioner`
Expected: `helm dependency build` succeeds; `git status` lists only `Chart.yaml`, `values.yaml` and
`README.md` — `charts/` and `Chart.lock` are ignored.

- [ ] **Step 8: Commit**

```bash
git add cluster/helm/nfs/provisioner .gitignore
git commit -m "🚀 feat(nfs): add the provisioner wrapper chart"
```

---

### Task 2: The eight per-export values files

**Files:**
- Create: `cluster/helm/nfs/provisioner/values/plex-media.yaml`
- Create: `cluster/helm/nfs/provisioner/values/nfs-audio.yaml`
- Create: `cluster/helm/nfs/provisioner/values/books-media.yaml`
- Create: `cluster/helm/nfs/provisioner/values/nfs-qbittorrent-download.yaml`
- Create: `cluster/helm/nfs/provisioner/values/nfs-qbittorrent-config.yaml`
- Create: `cluster/helm/nfs/provisioner/values/nfs-syncthing.yaml`
- Create: `cluster/helm/nfs/provisioner/values/stash-nfs.yaml`
- Create: `cluster/helm/nfs/provisioner/values/traefik-nfs.yaml`

**Interfaces:**
- Consumes: the chart from Task 1, released under the Application's name.
- Produces: for each release name, a render whose object names equal the live cluster's. Later tasks delete the old directories and rewrite the Applications against exactly these release names.

Every file has the same shape — four or five keys under `nfs-subdir-external-provisioner:` — with the
values from this table. `nameOverride` is omitted where the column says *default*.

| values file | `nfs.path` | `nameOverride` | `fullnameOverride` | `storageClass.name` | `accessModes` |
|---|---|---|---|---|---|
| `plex-media.yaml` | `/mnt/storage/nfs/plexmedia` | `plex-media` | `plex-media` | `plex-media` | default |
| `nfs-audio.yaml` | `/mnt/storage/nfs/audio` | default | `nfs-audio-nfs-subdir-external-provisioner` | `nfs-audio` | `ReadWriteMany` |
| `books-media.yaml` | `/mnt/storage/nfs/books` | default | `books-media-nfs-subdir-external-provisioner` | `nfs-books` | `ReadWriteMany` |
| `nfs-qbittorrent-download.yaml` | `/mnt/ultrafaststorage/nfs` | default | `nfs-qbittorrent-download-nfs-subdir-external-provisioner` | `qbittorrent-download` | default |
| `nfs-qbittorrent-config.yaml` | `/mnt/storage/nfs/qbittorrent-config` | default | `nfs-qbittorrent-config-nfs-subdir-external-provisioner` | `nfs-qbittorrent-config` | `ReadWriteMany` |
| `nfs-syncthing.yaml` | `/mnt/storage/nfs/syncthing` | `nfs-syncthing` | `nfs-syncthing` | `nfs-syncthing` | default |
| `stash-nfs.yaml` | `/mnt/storage/nfs/stash` | `nfs-stash` | `stash-nfs-nfs-stash` | `stash-nfs` | default |
| `traefik-nfs.yaml` | `/mnt/storage/traefik` | `traefik-nfs` | `traefik-nfs` | `traefik-nfs` | default |

Also set `nfs.volumeName` per file to the current value in the matching old `values.yaml`
(`nfs-books`, `nfs-audio`, `nfs-qbittorrent-config`, `qbittorrent-download`, `nfs-syncthing`,
`plex-media`, `stash-nfs`, `traefik-nfs`).

- [ ] **Step 1: Write the parity script and watch it fail**

Save as `/tmp/nfs-parity.sh` (not committed):

```bash
#!/usr/bin/env bash
set -euo pipefail
chart=./cluster/helm/nfs/provisioner
expect() { # release, fullname, class, selector, accessMode, export path
  local r=$1 full=$2 class=$3 sel=$4 am=$5 path=$6 out
  out=$(helm template "$r" "$chart" -f "$chart/values/$r.yaml")
  for want in \
    "PersistentVolume/pv-$full" \
    "PersistentVolumeClaim/pvc-$full" \
    "Deployment/$full" \
    "ServiceAccount/$full" \
    "StorageClass/$class"; do
    grep -qx "$want" <<<"$(yq -r 'select(.kind) | .kind + "/" + .metadata.name' <<<"$out")" \
      || { echo "FAIL $r: missing $want"; exit 1; }
  done
  [[ $(yq -r 'select(.kind=="Deployment") | .spec.selector.matchLabels.app' <<<"$out") == "$sel" ]] \
    || { echo "FAIL $r: selector app is not $sel"; exit 1; }
  [[ $(yq -r 'select(.kind=="PersistentVolume") | .spec.accessModes[0]' <<<"$out") == "$am" ]] \
    || { echo "FAIL $r: accessMode is not $am"; exit 1; }
  [[ $(yq -r 'select(.kind=="PersistentVolume") | .spec.nfs.path' <<<"$out") == "$path" ]] \
    || { echo "FAIL $r: export path is not $path"; exit 1; }
  echo "ok $r"
}
expect plex-media               plex-media                                               plex-media             plex-media                      ReadWriteOnce  /mnt/storage/nfs/plexmedia
expect nfs-audio                nfs-audio-nfs-subdir-external-provisioner                nfs-audio              nfs-subdir-external-provisioner ReadWriteMany  /mnt/storage/nfs/audio
expect books-media              books-media-nfs-subdir-external-provisioner              nfs-books              nfs-subdir-external-provisioner ReadWriteMany  /mnt/storage/nfs/books
expect nfs-qbittorrent-download nfs-qbittorrent-download-nfs-subdir-external-provisioner qbittorrent-download   nfs-subdir-external-provisioner ReadWriteOnce  /mnt/ultrafaststorage/nfs
expect nfs-qbittorrent-config   nfs-qbittorrent-config-nfs-subdir-external-provisioner   nfs-qbittorrent-config nfs-subdir-external-provisioner ReadWriteMany  /mnt/storage/nfs/qbittorrent-config
expect nfs-syncthing            nfs-syncthing                                            nfs-syncthing          nfs-syncthing                   ReadWriteOnce  /mnt/storage/nfs/syncthing
expect stash-nfs                stash-nfs-nfs-stash                                      stash-nfs              nfs-stash                       ReadWriteOnce  /mnt/storage/nfs/stash
expect traefik-nfs              traefik-nfs                                              traefik-nfs            traefik-nfs                     ReadWriteOnce  /mnt/storage/traefik
```

Run: `bash /tmp/nfs-parity.sh`
Expected: FAIL on the first release — `no such file or directory … values/plex-media.yaml`.

- [ ] **Step 2: Write the eight values files from the table**

`values/plex-media.yaml`, as the template the others follow:

```yaml
nfs-subdir-external-provisioner:
  nameOverride: plex-media
  fullnameOverride: plex-media
  nfs:
    path: /mnt/storage/nfs/plexmedia
    volumeName: plex-media
  storageClass:
    name: plex-media
```

`values/nfs-audio.yaml` adds the access mode:

```yaml
nfs-subdir-external-provisioner:
  fullnameOverride: nfs-audio-nfs-subdir-external-provisioner
  nfs:
    path: /mnt/storage/nfs/audio
    volumeName: nfs-audio
  storageClass:
    name: nfs-audio
    accessModes: ReadWriteMany
```

- [ ] **Step 3: Run the parity script**

Run: `bash /tmp/nfs-parity.sh`
Expected: eight `ok` lines, no `FAIL`.

- [ ] **Step 4: Check the matrix against the live cluster before anything is deleted**

Through the read-only MCP, read the eight Deployments, the eight PVs, the eight PVCs and the eight
StorageClasses, and confirm each name, selector, access mode and NFS path equals the task's table.
Expected: full agreement. A disagreement means the table is wrong, not the cluster — fix the values
file and re-run Step 3.

- [ ] **Step 5: Prove the PV/PVC gate holds for all eight**

Run:
```bash
for f in cluster/helm/nfs/provisioner/values/*.yaml; do
  helm template x ./cluster/helm/nfs/provisioner -f "$f" \
    | yq -r 'select(.kind=="PersistentVolume" or .kind=="PersistentVolumeClaim") | .kind' | wc -l
done
```
Expected: `2` eight times.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/nfs/provisioner/values
git commit -m "🚀 feat(nfs): add one values file per export"
```

---

### Task 3: Point the eight Applications at the wrapper

**Files:**
- Modify: `cluster/argocd/projects/nfs/plex-media.yaml`
- Modify: `cluster/argocd/projects/nfs/nfs-audio.yaml`
- Modify: `cluster/argocd/projects/nfs/books-media.yaml`
- Modify: `cluster/argocd/projects/nfs/nfs-qbittorrent-download.yaml`
- Modify: `cluster/argocd/projects/nfs/nfs-qbittorrent-config.yaml`
- Modify: `cluster/argocd/projects/nfs/nfs-syncthing.yaml`
- Modify: `cluster/argocd/projects/nfs/stash-nfs.yaml`
- Modify: `cluster/argocd/projects/nfs/traefik-nfs.yaml`

**Interfaces:**
- Consumes: `cluster/helm/nfs/provisioner` and its `values/<release>.yaml` (Task 2).
- Produces: eight Applications whose rendered object set replaces what the cluster runs today; same names, namespaces and destinations.

Each file keeps its `metadata.name`, `namespace: argocd`, `project: default` and destination
(namespaces: `media-stack` for six, `syncthing` for `nfs-syncthing`, `kube-system` for `traefik-nfs`).
`spec.source` becomes:

```yaml
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/nfs/provisioner
    helm:
      releaseName: <metadata.name>
      valueFiles:
        - values/<metadata.name>.yaml
```

Coordinate the merge as one change: a partial rewrite leaves some Applications on deleted directories.

- [ ] **Step 1: Write the checks and watch them fail**

```bash
grep -rl '\$values/cluster/helm/nfs' cluster/argocd/projects/nfs/     # expect: nfs-audio.yaml, books-media.yaml
grep -rL 'values/.*\.yaml' cluster/argocd/projects/nfs/                # expect: all eight listed
```
Both should fail against today's files. Record the output for the PR body.

- [ ] **Step 2: Rewrite the eight files**

Use `edit` on each file rather than `write`, preserving CRLF. The two files with `sources:` (two
entries: upstream chart plus `ref: values`) collapse to the single `source:` above; the six others
replace `path:` and gain `helm.releaseName` and `helm.valueFiles`.

- [ ] **Step 3: Prove the line endings survived**

Run: `file cluster/argocd/projects/nfs/*.yaml | grep -vc CRLF`
Expected: `0` — every file still reports `with CRLF line terminators`.

- [ ] **Step 4: Prove every referenced path and values file exists (CRLF-safe)**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects/nfs | sed 's/^    path: *//' | tr -d "\"' \r")

while read -r v; do [ -f "cluster/helm/nfs/provisioner/$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '^ *- values/' cluster/argocd/projects/nfs | sed 's/^ *- //' | tr -d "\"' \r")
```
Expected: no output.

- [ ] **Step 5: Prove no Application carries automation or a two-source form**

```bash
grep -rn 'syncPolicy' cluster/argocd/projects/nfs/ ; grep -rn 'ref: values' cluster/argocd/projects/nfs/
```
Expected: no output from either.

- [ ] **Step 6: Render each Application's source the way ArgoCD will**

```bash
for a in plex-media nfs-audio books-media nfs-qbittorrent-download nfs-qbittorrent-config nfs-syncthing stash-nfs traefik-nfs; do
  helm template "$a" ./cluster/helm/nfs/provisioner -f "cluster/helm/nfs/provisioner/values/$a.yaml" >/dev/null && echo "ok $a"
done
```
Expected: eight `ok` lines.

- [ ] **Step 7: Commit**

```bash
git add cluster/argocd/projects/nfs
git commit -m "♻️ refactor(nfs): point the provisioner Applications at the wrapper chart"
```

---

### Task 4: Delete the nine old chart directories

**Files:**
- Delete: `cluster/helm/nfs/books-media/`, `cluster/helm/nfs/nfs-audio/`, `cluster/helm/nfs/nfs-qbittorrent-config/`, `cluster/helm/nfs/nfs-qbittorrent-download/`, `cluster/helm/nfs/nfs-syncthing/`, `cluster/helm/nfs/plexmedia/`, `cluster/helm/nfs/stash-nfs/`, `cluster/helm/nfs/traefik-nfs/`, `cluster/helm/nfs/minio-nfs/`

**Interfaces:**
- Consumes: Task 3's Applications, which no longer reference any of these paths.
- Produces: `cluster/helm/nfs/` containing only `provisioner/`.

- [ ] **Step 1: Prove nothing outside the directory still points at it**

```bash
grep -rn 'cluster/helm/nfs/' cluster --include='*.yaml' | grep -v 'nfs/provisioner'
```
Expected: no output. Anything here is a missed reference — fix it before deleting.

- [ ] **Step 2: Delete the nine directories**

```bash
git rm -r cluster/helm/nfs/books-media cluster/helm/nfs/nfs-audio \
  cluster/helm/nfs/nfs-qbittorrent-config cluster/helm/nfs/nfs-qbittorrent-download \
  cluster/helm/nfs/nfs-syncthing cluster/helm/nfs/plexmedia cluster/helm/nfs/stash-nfs \
  cluster/helm/nfs/traefik-nfs cluster/helm/nfs/minio-nfs
```

- [ ] **Step 3: Confirm the folder holds one chart**

Run: `ls cluster/helm/nfs`
Expected: `provisioner` only.

- [ ] **Step 4: Run the repo's chart render loop (AGENTS.md §6)**

```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```
Expected: no `FAIL`; the wrapper chart is either rendered or skipped for its missing `charts/`.

- [ ] **Step 5: Run both reference checks from AGENTS.md §6**

The two CRLF-safe loops from the repo's AGENTS.md (`path:` resolves, `$values/...` resolves).
Expected: no `BROKEN PATH` and no `BROKEN VALUES`.

- [ ] **Step 6: Commit**

```bash
git add -A cluster/helm/nfs
git commit -m "🔥 chore(nfs): delete the nine per-export chart copies and the dead MinIO chart"
```

---

### Task 5: Bring the living pages in line

**Files:**
- Modify: `docs/how-it-works/storage.md:35-45`
- Modify: `docs/how-it-works/applications.md:119-123`
- Modify: `docs/status.md:164-171`, `docs/status.md:192-196`, `docs/status.md:220-224`
- Modify: `docs/reference/metrics.md:250` (only if it states a live fact; leave it if it is an example of an error message)
- Modify: `AGENTS.md:225-231` (items 5, 6, 7) and the pattern-B examples in §3
- Modify: `docs/reference/chart-notes.md` if it lists the nfs charts

**Interfaces:**
- Consumes: the final layout from Tasks 1–4.
- Produces: pages that describe the repo as it now is. `docs/audit/**` stays untouched: it records what was found in November, not what is true now.

- [ ] **Step 1: Rewrite the class table in `docs/how-it-works/storage.md`**

One row per export and class: `plex-media` (film and television), `nfs-audio` (music), `nfs-books`
(books), `qbittorrent-download` (qBittorrent's output), `nfs-qbittorrent-config`, `nfs-syncthing`,
`stash-nfs` (kept, nothing mounts it), `traefik-nfs` (certificate store). Drop the "strays" sentence
and the `nfs-plex`/`nfs-ssd` rows. Add one line naming the wrapper chart and that each export is one
release of it.

- [ ] **Step 2: Fix the two defect lines in `docs/how-it-works/applications.md`**

Lines 121–122 describe broken paths that no longer exist. Remove them; if the surrounding list needs a
replacement entry, say that the NFS Applications all point at `cluster/helm/nfs/provisioner`.

- [ ] **Step 3: Update `docs/status.md`**

The Applications table rows for the eight provisioners gain the wrapper-chart note and lose the
"ArgoCD still reports Progressing" remarks. Remove the three defect rows (194–195 and the
`nfs-qbittorrent-config` "manages nothing" paragraph). Replace the `nfs-ssd` bullet at 221–223 with a
line stating the release is gone and who ran the command.

- [ ] **Step 4: Update AGENTS.md**

§7 items 5, 6 and 7 describe references that no longer exist: replace the three with one resolved line
pointing at this change, keeping the numbering. In §3, the pattern-B example list mentions
`books-media` and `nfs-audio` as values-only charts — remove them, since they are now pattern-D
values files.

- [ ] **Step 5: Check for other stale references**

```bash
grep -rn 'cluster/helm/nfs/\(plexmedia\|nfs-audio\|books-media\|nfs-qbittorrent\|nfs-syncthing\|stash-nfs\|traefik-nfs\|minio-nfs\)' \
  docs AGENTS.md README.md | grep -v '^docs/audit/' | grep -v '^docs/superpowers/'
```
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add docs AGENTS.md
git commit -m "📝 docs(nfs): describe the wrapper chart instead of the nine copies"
```

---

### Task 6: Full verification and handover

**Files:** none created or modified.

**Interfaces:**
- Consumes: everything above.
- Produces: a green branch, a PR into `develop`, and the operator's two cluster commands.

- [ ] **Step 1: Re-run the whole gate from a clean tree**

```bash
helm dependency build cluster/helm/nfs/provisioner
bash /tmp/nfs-parity.sh
hack/check-exposure.sh
```
Expected: eight `ok` lines and the exposure check's pass line.

- [ ] **Step 2: Confirm the tree carries nothing generated**

Run: `git status --short`
Expected: empty.

- [ ] **Step 3: Rebase onto the integration branch**

```bash
git fetch origin
git rebase origin/develop
```
Expected: clean rebase; re-run Step 1 afterwards if it moved anything.

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feature/nfs-charts-cleanup
```
Open the PR with base `develop` and a body carrying: what changed, the parity output from Step 1, the
table of preserved object names, and the operator's commands:

```bash
helm uninstall nfs-hdd nfs-ssd -n default
```

- [ ] **Step 5: Move the board card to `Validation`**

Description carries the branch, the PR link, the parity output, and the two `helm uninstall` commands
still owed by the operator.

- [ ] **Step 6: Report**

Say what the first sync will do (adopt the eight existing object sets by name, change nothing), what
the operator must run, and that the four `Terminating` pairs were left alone on purpose.
