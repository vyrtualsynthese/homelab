# recyclarr chart refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the recyclarr Helm chart as a contract-compliant CronJob: values that drive the render, the Secret mounted where the app reads it, the existing config claim kept and named from the release, and documentation that records what is deliberately absent.

**Architecture:** One local chart with `app.*` naming helpers copied from `autobrr`. A single `CronJob` runs `recyclarr sync` on a stated timezone; the config claim stays at `/config`, and Secret `recyclarr` key `recyclarr.yml` is mounted read-only at `/config/recyclarr.yml` with `subPath`. No init container, no shell, no Service, no ingress, no probes, no metrics. The pod runs as UID/GID 1000 — the image's own default and the owner of the existing claim — pinned to `nuc0`, where the claim's PV lives.

**Tech Stack:** Helm 4.2.4, Kubernetes YAML, ArgoCD. No CI; verification is `helm lint`, `helm template`, and grep checks.

**Spec:** `docs/superpowers/specs/2026-11-13-recyclarr-chart-refactor-design.md`

## Global Constraints

- Image `ghcr.io/recyclarr/recyclarr:8.8.1`; `appVersion: "8.8.1"`; chart `version: 1.0.0`.
- Release and fullname stay `recyclarr`; rendered objects are `recyclarr` (CronJob, ServiceAccount) and `recyclarr-config` (claim).
- Claim: `<fullname>-config`, `storageClassName: local-path`, `ReadWriteOnce`, `128Mi`, mounted at `/config`; rendered only when `storage.enabled` and `storage.existingClaim` is empty.
- Config file: Secret `recyclarr`, key `recyclarr.yml`, read-only `subPath` mount at `/config/recyclarr.yml`, `defaultMode: 0444`.
- Schedule `0 6 * * *`, `timeZone: Europe/Paris`, `concurrencyPolicy: Forbid`, `startingDeadlineSeconds: 300`, `backoffLimit: 1`, `activeDeadlineSeconds: 1800`, history 3/3.
- Security: `runAsNonRoot`, UID/GID `1000`, `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`, all capabilities dropped, ServiceAccount token not mounted.
- Resources: requests `50m` CPU / `128Mi` memory; limit `1Gi` memory; no CPU limit.
- No Service, IngressRoute, probes, metrics block, `busybox`, `initContainers`, `PUID`, `PGID`, `5128Mi`, `networking.k8s.io`, `latest`, or tabs.
- Every object carries `app.labels`; every name goes through `app.fullname`, `app.selectorLabels`, `app.serviceAccountName` or `app.configClaimName`.
- Do not touch the Secret's content or the live claim.

## Review Focus

- **The claim name must stay `recyclarr-config`.** The live PV is bound to that name; a render that produces anything else orphans it. Check the default render and `--set fullnameOverride=demo` → `demo-config`.
- **Pinning to the wrong node makes the pod unschedulable.** The claim is a `local-path` PV on `nuc0`; a render saying `streaming` fails at mount time, not at scheduling time.
- **The Secret must be readable by a non-root process.** `defaultMode: 0444` and the `subPath` mount must both be present; dropping either produces `CreateContainerConfigError` or a permission error after start.
- **UID 1000 is load-bearing.** The claim was chowned to 1000 by the old init container. Promoting to 1100 without a manual chown makes the run fail on the first write, after the pod reports Running.
- **A sync is idempotent, but a killed sync is not.** `concurrencyPolicy` must be `Forbid`, not `Replace`; the values file and the render must agree.

---

### Task 0: Commit the approved design and this plan

**Files:**
- Add: `docs/superpowers/specs/2026-11-13-recyclarr-chart-refactor-design.md`
- Add: `docs/superpowers/plans/2026-11-13-recyclarr-chart-refactor.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the documents every later task is measured against.

- [ ] **Step 1: Confirm the branch**

Run: `git branch --show-current`
Expected: `feature/recyclarr-chart-refactor`

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-11-13-recyclarr-chart-refactor-design.md \
        docs/superpowers/plans/2026-11-13-recyclarr-chart-refactor.md
git commit -m "📝 docs(recyclarr): add chart refactor design and plan"
```

---

### Task 1: Rebuild the chart

**Files:**
- Modify: `cluster/helm/media-stack/recyclarr/Chart.yaml`
- Modify: `cluster/helm/media-stack/recyclarr/values.yaml`
- Modify: `cluster/helm/media-stack/recyclarr/templates/_helpers.tpl`
- Create: `cluster/helm/media-stack/recyclarr/templates/cronjob.yaml`
- Create: `cluster/helm/media-stack/recyclarr/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/recyclarr/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/recyclarr/templates/NOTES.txt`
- Delete: `cluster/helm/media-stack/recyclarr/templates/cron.yaml`
- Delete: `cluster/helm/media-stack/recyclarr/templates/pvc-config.yaml`

**Interfaces:**
- Consumes: the contract in `docs/charts/README.md`; `cluster/helm/media-stack/autobrr/templates/{persistentvolumeclaim,serviceaccount,_helpers}.tpl` as the reference for object shape.
- Produces (for Task 2 and 3): values keys `image.*`, `serviceAccount.*`, `podAnnotations`, `podLabels`, `podSecurityContext`, `securityContext`, `args`, `env`, `extraEnv`, `envFrom`, `schedule`, `timeZone`, `concurrencyPolicy`, `startingDeadlineSeconds`, `backoffLimit`, `activeDeadlineSeconds`, `successfulJobsHistoryLimit`, `failedJobsHistoryLimit`, `storage.*`, `configFile.*`, `volumes`, `volumeMounts`, `nodeSelector`, `tolerations`, `affinity`, `resources.*`; rendered `recyclarr` (CronJob), `recyclarr` (ServiceAccount), `recyclarr-config` (PVC).

- [ ] **Step 1: Prove the current chart's defects**

```bash
helm template recyclarr cluster/helm/media-stack/recyclarr --namespace media-stack > /tmp/recyclarr-before.yaml
grep -c 'busybox' /tmp/recyclarr-before.yaml            # expect 1
grep -c 'PUID' /tmp/recyclarr-before.yaml               # expect 1
grep -c '5128Mi' /tmp/recyclarr-before.yaml             # expect 1
grep -c 'nodeSelector' /tmp/recyclarr-before.yaml       # expect 0
grep -c 'kind: ServiceAccount' /tmp/recyclarr-before.yaml # expect 0
grep -c 'app.kubernetes.io/version: "8' /tmp/recyclarr-before.yaml # expect 0
```

These are the defects the refactor removes: the init container, the ignored `nodeSelector`, the missing ServiceAccount, and the version label that says `1.0.0`.

- [ ] **Step 2: Write `Chart.yaml`**

`description`, `home: https://recyclarr.dev/`, `sources: [https://github.com/recyclarr/recyclarr]`, `icon: https://recyclarr.dev/img/recyclarr.png`, keywords `radarr`, `sonarr`, `trash-guides`, `automation`, `version: 1.0.0`, `appVersion: "8.8.1"`.

- [ ] **Step 3: Write `values.yaml`**

Keys and defaults exactly as the Global Constraints say, plus:

```yaml
nameOverride: ""
fullnameOverride: ""
imagePullSecrets: []
serviceAccount: {create: true, automount: false, name: "", annotations: {}}
podAnnotations: {}
podLabels: {}
podSecurityContext: {seccompProfile: {type: RuntimeDefault}}
securityContext: {runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000,
                  allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
args: [sync]
env: {TZ: "Europe/Paris", NO_COLOR: "1",
      RECYCLARR_CONFIG_DIR: "/config", RECYCLARR_DATA_DIR: "/config"}
extraEnv: []
envFrom: []
storage: {enabled: true, existingClaim: "", className: local-path,
          accessModes: [ReadWriteOnce], size: 128Mi, mountPath: /config}
configFile: {secretName: recyclarr, key: recyclarr.yml,
             mountPath: /config/recyclarr.yml}
volumes: []
volumeMounts: []
nodeSelector: {kubernetes.io/hostname: nuc0}
tolerations: []
affinity: {}
resources: {requests: {cpu: 50m, memory: 128Mi}, limits: {memory: 1Gi}}
```

Comment each block with the reason from the design: why 1000, why `nuc0`, why the timezone is
explicit, why `Forbid`, why the memory limit is unmeasured, why `RECYCLARR_DATA_DIR` is `/config`.

- [ ] **Step 4: Write `templates/_helpers.tpl`**

Copy `app.name`, `app.fullname`, `app.chart`, `app.labels`, `app.selectorLabels`,
`app.serviceAccountName`, `app.configClaimName` from `cluster/helm/media-stack/autobrr/templates/_helpers.tpl`
verbatim. Do **not** copy `app.containerPort`; this chart has no Service.

- [ ] **Step 5: Write `templates/cronjob.yaml`**

`batch/v1` `CronJob` named `app.fullname`, labels `app.labels`. `spec` reads `schedule`, `timeZone`,
`concurrencyPolicy`, `startingDeadlineSeconds`, `successfulJobsHistoryLimit`, `failedJobsHistoryLimit`
from values. `jobTemplate.metadata.labels` is `app.labels`; `jobTemplate.spec` takes `backoffLimit`
and `activeDeadlineSeconds` from values. The pod template carries `app.selectorLabels` plus
`podLabels` and `podAnnotations`; `restartPolicy: OnFailure`; `serviceAccountName` from
`app.serviceAccountName`; `imagePullSecrets`, `podSecurityContext`, container `securityContext`,
`resources`, `nodeSelector`, `tolerations` and `affinity` rendered from values, each omitted when empty
except `resources`. Container named `recyclarr`, `args` from values (**no `command`**), `env` from the
`env` map then `extraEnv` then `envFrom`. Mounts: the claim at `storage.mountPath` when
`storage.enabled`, the Secret file at `configFile.mountPath` with `subPath: configFile.key` and
`readOnly: true` when `configFile.secretName`, then raw `volumeMounts`. Volumes: the claim as
`persistentVolumeClaim.claimName: app.configClaimName` when enabled, the Secret with
`defaultMode: 0444` and one item, then raw `volumes`. No `initContainers`, no `command`, no ports, no
probes.

- [ ] **Step 6: Write `persistentvolumeclaim.yaml` and `serviceaccount.yaml`**

Copy both from `autobrr` unchanged except the file names; the helpers already produce the correct
names and the values keys are identical.

- [ ] **Step 7: Write `NOTES.txt`**

State that the release is a CronJob, not a service; how to trigger a run now
(`kubectl -n <ns> create job --from=cronjob/<fullname> <fullname>-manual`); how to read the result
(`kubectl -n <ns> logs job/<fullname>-manual`); that configuration is Secret
`<configFile.secretName>` mounted at `<configFile.mountPath>`; and where state lives
(`app.configClaimName`, plus the warning that deleting the release deletes a chart-created claim).

- [ ] **Step 8: Delete the obsolete templates**

```bash
git rm cluster/helm/media-stack/recyclarr/templates/cron.yaml \
       cluster/helm/media-stack/recyclarr/templates/pvc-config.yaml
```

- [ ] **Step 9: Lint and render**

```bash
helm lint cluster/helm/media-stack/recyclarr --namespace media-stack
helm template recyclarr cluster/helm/media-stack/recyclarr --namespace media-stack > /tmp/recyclarr-after.yaml
```

Expected: `1 chart(s) linted, 0 chart(s) failed`; template exits 0.

- [ ] **Step 10: Check the rendered contract**

```bash
grep -q 'claimName: recyclarr-config' /tmp/recyclarr-after.yaml && echo "claim ok"
grep -q 'schedule: "0 6 \* \* \*"' /tmp/recyclarr-after.yaml && echo "schedule ok"
grep -q 'timeZone: Europe/Paris' /tmp/recyclarr-after.yaml && echo "timezone ok"
grep -q 'concurrencyPolicy: Forbid' /tmp/recyclarr-after.yaml && echo "concurrency ok"
grep -q 'subPath: recyclarr.yml' /tmp/recyclarr-after.yaml && echo "config file ok"
grep -q 'readOnly: true' /tmp/recyclarr-after.yaml && echo "read-only ok"
grep -q 'runAsUser: 1000' /tmp/recyclarr-after.yaml && echo "uid ok"
grep -q 'automountServiceAccountToken: false' /tmp/recyclarr-after.yaml && echo "no api token ok"
grep -q 'kubernetes.io/hostname: nuc0' /tmp/recyclarr-after.yaml && echo "node ok"
grep -q 'app.kubernetes.io/version: "8.8.1"' /tmp/recyclarr-after.yaml && echo "version ok"
! grep -qE 'busybox|initContainers|PUID|PGID|5128Mi|command:|kind: Service$|IngressRoute|livenessProbe|readinessProbe' /tmp/recyclarr-after.yaml && echo "legacy parts absent"
```

Expected: every `... ok` line prints, nothing missing.

- [ ] **Step 11: Check name derivation and the claims switch**

```bash
helm template recyclarr cluster/helm/media-stack/recyclarr --namespace media-stack \
  --set fullnameOverride=demo > /tmp/recyclarr-demo.yaml
grep -q 'claimName: demo-config' /tmp/recyclarr-demo.yaml && echo "derived claim ok"

helm template recyclarr cluster/helm/media-stack/recyclarr --namespace media-stack \
  --set storage.existingClaim=shared-config > /tmp/recyclarr-existing.yaml
! grep -q 'kind: PersistentVolumeClaim' /tmp/recyclarr-existing.yaml && echo "no claim rendered"
grep -q 'claimName: shared-config' /tmp/recyclarr-existing.yaml && echo "existing claim ok"
```

Expected: all three lines.

- [ ] **Step 12: Commit**

```bash
git add cluster/helm/media-stack/recyclarr
git commit -m "♻️ refactor(recyclarr): rebuild chart around the CronJob's needs"
```

---

### Task 2: Document the chart

**Files:**
- Create: `cluster/helm/media-stack/recyclarr/README.md`
- Modify: `docs/charts/README.md:55-61`, `:154-160`, `:186-211`
- Modify: `docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md:151`

**Interfaces:**
- Consumes: the values keys, object names and render decisions produced by Task 1.
- Produces: the README the contract expects, and a contract document that names the batch-job case.

- [ ] **Step 1: Write the chart README**

Cover, in this order: what recyclarr does and where it comes from; how ArgoCD deploys it (Application
`cluster/argocd/projects/media-stack/recyclarr.yaml`, release `recyclarr`, three objects, the local
render command); a values table; configuration from Secret `recyclarr` key `recyclarr.yml`, read-only,
and the note that the claim may still hold the copy the old init container wrote (the mount shadows
it); state under `/config` (`resources/`, `logs/`, `state/`); the schedule and timezone, `Forbid`, the
deadlines and history; why there is no Service, ingress, probe or metrics; running as 1000 and why no
`chown` is needed; the placement (`nuc0`, because the claim is there) with the reason; the log-level
debt (v8 has no control, v9's `settings.yml` will); the two data debts from the last run
(`replace_existing_custom_formats` deprecation in four places, and `movie-anime` profile `Original`
missing `qualities`); the upgrade rule (`image.tag` and `appVersion` move together); a files table and
the uninstall note.

- [ ] **Step 2: Add the batch-job rule to the contract**

In `docs/charts/README.md`, after the readiness paragraph, add: a batch workload (a CronJob) has no
long-running process and no listener, so probes do not apply; `recyclarr` is the worked example. Keep
requirements 12 and 13 scoped to apps that serve traffic.

- [ ] **Step 3: Update the hardening table and examples**

Add a row: `| recyclarr | n/a (batch job) | capabilities dropped, seccomp, no privilege escalation, no API token | **yes — 1000**, the image's default and the claim's owner |`. Add a "What good looks like" paragraph: three objects, no listener, Secret file mounted read-only, claim derived from the release, schedule and timezone in values.

- [ ] **Step 4: Correct the placement table**

In `docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md`, move `recyclarr` from the
`streaming` row to the `nuc0` row. Its claim is a `local-path` PV on `nuc0`; the job touches no media
and needs no GPU, so the general node is its home.

- [ ] **Step 5: Check the documentation**

```bash
test -f cluster/helm/media-stack/recyclarr/README.md && echo "readme exists"
grep -n 'recyclarr' docs/charts/README.md
grep -n 'recyclarr' docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md
! grep -n 'recyclarr' docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md | grep -q 'streaming' && echo "placement corrected"
```

Expected: "readme exists", at least one recyclarr line in each document, "placement corrected".

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/media-stack/recyclarr/README.md docs/charts/README.md \
        docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md
git commit -m "📝 docs(recyclarr): document the CronJob chart and its deliberate gaps"
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

Expected output is exactly the two pre-existing lines and nothing else:

```text
BROKEN PATH cluster/helm/nfs/plex-media
BROKEN VALUES /cluster/helm/nfs/audio/values.yaml
```

- [ ] **Step 3: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push -u origin feature/recyclarr-chart-refactor
```

- [ ] **Step 4: Open the pull request**

Use the GitHub MCP `create_pull_request`: head `feature/recyclarr-chart-refactor`, base `develop`.
Title `♻️ refactor(recyclarr): rebuild chart around the CronJob's needs`. The body covers the three
rendered objects, the removed init container and `PUID`/`PGID`, the 8.8.1 bump, the keep-claim /
pin-`nuc0` decision, the schedule timezone change, and the contract document changes. Do not merge.

---

## Post-merge operator runbook

1. **Confirm the claim's owner is still 1000.** The chart now runs as 1000, so nothing should change;
   if the claim holds files owned by another UID, the run fails on its first write.

   ```bash
   kubectl -n media-stack get pvc recyclarr-config -o jsonpath='{.spec.volumeName}{"\n"}'
   kubectl get pv <pvc-volume-name> -o jsonpath='{.spec.hostPath.path}{"\n"}'
   ```

2. **Sync** the Application manually (auto-sync is off) and confirm the CronJob's next run or a
   manual job succeeds:

   ```bash
   kubectl -n media-stack create job --from=cronjob/recyclarr recyclarr-manual
   kubectl -n media-stack logs job/recyclarr-manual
   ```

3. **Confirm placement.** The pod must schedule on `nuc0`; a pod stuck `ContainerCreating` with a
   volume node-affinity error means the claim is not where the values say.

4. **Keep the claim.** Do not delete `recyclarr-config`; the chart reuses it by name. It appears in
   the release, so `helm uninstall` would delete it — ArgoCD pruning applies only if the object leaves
   git, which it does not.
