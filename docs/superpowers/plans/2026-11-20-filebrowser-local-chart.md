# FileBrowser local chart — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the upstream FileBrowser Quantum dependency with a local chart that pins the pod to `nuc0`.

**Architecture:** `cluster/helm/filebrowser` stops depending on `oci://ghcr.io/softwaredam/helm-charts` and renders the Deployment, Service, ConfigMap, Secret, ServiceAccount and two `local-path` claims itself, keeping every live name. The existing IngressRoute, TLSOption and NFS templates stay as they are.

**Tech Stack:** Helm 4, ArgoCD, Traefik v2 CRDs, K3s.

**Spec:** [`docs/superpowers/specs/2026-11-20-filebrowser-local-chart-design.md`](../specs/2026-11-20-filebrowser-local-chart-design.md)

## Global Constraints

- Names: Deployment, Service, ConfigMap, Secret, ServiceAccount = `<release>-filebrowser-quantum`; claims = that name plus `-config` and `-data`. With release `filebrowser` this is the live set.
- Selector labels stay `app: filebrowser-quantum` and `release: <release>`; the Deployment selector is immutable.
- `nodeSelector: kubernetes.io/hostname: nuc0`; config claim stays `local-path` 1 Gi, data claim 1 Gi.
- Image stays `ghcr.io/gtsteffaniak/filebrowser:2.0.2-stable`, `appVersion: "2.0.2"`, chart version `0.3.0`.
- One port value: `service.port: 8080` feeds the container port, the Service, the probes and `http.port` in `config.yaml`.
- No `networking.k8s.io` Ingress; the route keeps `security` only (public allowlist).
- `cluster/helm/**` uses LF, no tabs.

## Review Focus

- **ArgoCD must patch, not replace.** A changed Deployment selector fails the sync; the render check compares selector and every object name to the live cluster, not just to the previous render.
- **The database claim must not be renamed or dropped.** `filebrowser-filebrowser-quantum-config` holds users, scopes and the OIDC linkage; its name comes from `app.fullname`, which must render exactly the live string.
- **OIDC stays the only login.** `config.auth` and the two OIDC environment entries move unchanged; a dropped `FILEBROWSER_OIDC_CLIENT_SECRET` locks every browser out.
- **Uploads still run as uid 1100 with primary gid 1000.** The pod `securityContext` moves unchanged, including `supplementalGroups` and `fsGroup`.
- **A merged chart must keep rendering after the dependency is gone.** The `charts/` directory is gitignored and may hold a stale subchart locally; remove it before the render check or it double-renders.

---

### Task 1: Own the chart and pin the node

**Files:**
- Modify: `cluster/helm/filebrowser/Chart.yaml`
- Modify: `cluster/helm/filebrowser/values.yaml`
- Create: `cluster/helm/filebrowser/templates/_helpers.tpl`
- Create: `cluster/helm/filebrowser/templates/deployment.yaml`
- Create: `cluster/helm/filebrowser/templates/service.yaml`
- Create: `cluster/helm/filebrowser/templates/serviceaccount.yaml`
- Create: `cluster/helm/filebrowser/templates/configmap.yaml`
- Create: `cluster/helm/filebrowser/templates/secret.yaml`
- Create: `cluster/helm/filebrowser/templates/persistentvolumeclaim.yaml`
- Modify: `.gitignore` (drop the now-unused `filebrowser` `charts/` and `Chart.lock` ignores)

**Interfaces:**
- Consumes: nothing from another task.
- Produces: rendered Deployment `filebrowser-filebrowser-quantum` with `nodeSelector` `nuc0`; Service `filebrowser-filebrowser-quantum` on 8080; ConfigMap and Secret of the same name; claims `filebrowser-filebrowser-quantum-config` and `-data`; values `nfsVolumes` and `traefik.*` unchanged for the existing templates.

- [ ] **Step 1: Pull the released upstream chart and record what it renders, as the baseline**

```bash
rm -rf /tmp/opencode/fb-baseline && git worktree add /tmp/opencode/fb-baseline origin/develop
cd /tmp/opencode/fb-baseline/cluster/helm/filebrowser && helm dependency build >/dev/null
helm template filebrowser . --namespace filebrowser > /tmp/opencode/fb-before.yaml
```

Expected: exit 0, and `/tmp/opencode/fb-before.yaml` names the objects listed in the spec.

- [ ] **Step 2: Write `Chart.yaml` without dependencies**

Version `0.3.0`, `appVersion: "2.0.2"`, description "FileBrowser Quantum — web file browser over the NAS exports, OIDC only".

- [ ] **Step 3: Rewrite `values.yaml` at the root**

Move the `filebrowser-quantum:` block to the root, drop `fullnameOverride`-style subchart keys, add `nodeSelector`, `tolerations`, `affinity`, `service.port`, `probes`, `podSecurityContext`, `securityContext`, `serviceAccount`, `persistence.config/data`, `adminPassword`, `extraEnv`, `extraVolumes`, `extraVolumeMounts`, and `config` with `server.logging` at `warning|error`. Keep the existing comments that carry the uid/mergerfs and OIDC reasoning.

- [ ] **Step 4: Write `_helpers.tpl`, `deployment.yaml`, `service.yaml`, `serviceaccount.yaml`, `configmap.yaml`, `secret.yaml`, `persistentvolumeclaim.yaml`**

Follow `cluster/helm/media-stack/autobrr` for helper and object shape, with `app.selectorLabels` rendering the legacy pair and `app.fullname` rendering `<release>-filebrowser-quantum`.

- [ ] **Step 5: Render and compare names and selectors with the baseline**

```bash
cd cluster/helm/filebrowser && rm -rf charts Chart.lock
helm template filebrowser . --namespace filebrowser > /tmp/opencode/fb-after.yaml
python3 - <<'PY'
import yaml
def ids(path):
    out=set()
    for d in yaml.safe_load_all(open(path)):
        if d: out.add((d["kind"], d["metadata"]["name"], str(d.get("spec",{}).get("selector",{}))))
    return out
before, after = ids('/tmp/opencode/fb-before.yaml'), ids('/tmp/opencode/fb-after.yaml')
print("only before:", sorted(before-after))
print("only after:", sorted(after-before))
PY
```

Expected: the only differences are the new ServiceAccount and the added `helm.sh`/`app.kubernetes.io` labels; every workload, Service, ConfigMap, Secret and claim name is identical, and the Deployment selector is identical.

- [ ] **Step 6: Check the pin**

```bash
grep -A2 'nodeSelector' /tmp/opencode/fb-after.yaml
```

Expected: `kubernetes.io/hostname: nuc0` under the Deployment's `spec.template.spec`.

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/filebrowser .gitignore
git commit -m "🚀 feat(filebrowser): own the chart and pin it to nuc0"
```

### Task 2: Record the decision in the docs

**Files:**
- Modify: `cluster/helm/filebrowser/README.md`
- Modify: `docs/how-it-works/applications.md:50`
- Modify: `docs/charts/README.md` (the probes/hardening/UID table)
- Modify: `docs/reference/chart-notes.md` (`What good looks like`)

**Interfaces:**
- Consumes: the chart from Task 1.
- Produces: nothing other tasks read.

- [ ] **Step 1: Rewrite the chart README's chart section**

Replace "Why the fork" with "Why a local chart": the upstream chart exports no scheduling field, the card's options, and why the third won. Replace the "Known gap" section with the pin, and record: the legacy selector reason, why liveness shares `/health` with readiness, the `warning|error` log level, and that a `config.yaml` change needs a restart.

- [ ] **Step 2: Update the applications table row**

`| \`filebrowser\` | \`filebrowser\` | nuc0 | A | web file browser over the media shares, FileBrowser Quantum, sign-in through authentik |`

- [ ] **Step 3: Add the two contract records**

A `filebrowser` row in `docs/charts/README.md`'s table (probes on `/health`, capabilities dropped, seccomp, no privilege escalation, uid 1100 with primary gid 1000), and a `### filebrowser` paragraph in `docs/reference/chart-notes.md` under "What good looks like".

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/filebrowser/README.md docs/how-it-works/applications.md docs/charts/README.md docs/reference/chart-notes.md
git commit -m "📝 docs(filebrowser): the chart is ours and runs on nuc0"
```

### Task 3: Validate the branch and hand it over

**Files:** none.

- [ ] **Step 1: Lint and render**

```bash
cd cluster/helm/filebrowser
helm lint . --namespace filebrowser
rm -rf charts Chart.lock && helm template filebrowser . --namespace filebrowser >/dev/null
cd - >/dev/null
```

Expected: both exit 0.

- [ ] **Step 2: Schema check**

```bash
cd cluster/helm/filebrowser && helm template filebrowser . --namespace filebrowser | kubeconform -strict -summary
```

Expected: all resources valid. If `kubeconform` is absent, report it as not run, not as a pass.

- [ ] **Step 3: The repo checks**

```bash
hack/check-app-version.sh --only cluster/helm/filebrowser
hack/check-exposure.sh --only cluster/helm/filebrowser
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```

Expected: one chart passes both scripts; the pytest suite reports zero failures.

- [ ] **Step 4: ArgoCD references still resolve** (AGENTS.md §6, CRLF-safe snippets)

Expected: no `BROKEN PATH` or `BROKEN VALUES` line.

- [ ] **Step 5: Rebase, push, open the PR**

```bash
git fetch origin
git rebase origin/develop
git push -u origin feature/filebrowser-quantum
```

Then open the PR against `develop` with the rendered-name comparison, the pin evidence and the check output in the body.

- [ ] **Step 6: Move card #427 to Validation**

Comment with the branch, the PR link, the render diff and the check output. Leave it in `Validation`; never `Done`.

## After merge

A sync applies the Deployment patch. Because the selector is unchanged, ArgoCD updates in place; the
pod rolls once for the new labels, security context and probe. The config claim is already bound to
`nuc0`, and the pod is pinned to the same node, so no rebind is needed. If the sync reports an
immutable-field error, stop: something in the selector changed, and the chart must be corrected
rather than the Deployment deleted.
