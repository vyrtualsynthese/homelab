# GPU operator into GitOps — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the hand-installed NVIDIA GPU Operator into a pinned wrapper chart plus an ArgoCD
Application, so the GPU stack is reproducible from git, with the host driver as the only remaining
hand step.

**Architecture:** Wrapper chart at `cluster/helm/platform/gpu-operator` (pattern D) depending on
NVIDIA's `gpu-operator` chart `v26.3.1`, with every image tag pinned to what runs today, the driver
declared host-managed, NFD workers scoped to `streaming`, and a chart-owned Service carrying the
Prometheus scrape annotation. An Application in `cluster/argocd/projects/platform/` deploys it.

**Tech Stack:** Helm 3, ArgoCD Application + root app, Kubernetes 1.30 (K3s), NVIDIA GPU Operator
v26.3.1, Prometheus service-endpoint discovery.

**Spec:** `docs/superpowers/specs/2026-11-20-gpu-operator-gitops-design.md`

## Global Constraints

- Upstream chart pinned to exactly `v26.3.1`; image tags pinned to the live inventory: operator and
  validator `v26.3.1`, toolkit `v1.19.0`, device plugin and gfd `v0.19.0`, dcgm-exporter
  `4.5.1-4.8.0-distroless`, NFD `v0.18.3`, mig-manager `v0.14.0`.
- Toolkit env stays exactly: `CONTAINERD_CONFIG=/var/lib/rancher/k3s/agent/etc/containerd/config.toml`,
  `CONTAINERD_SOCKET=/run/k3s/containerd/containerd.sock`,
  `CONTAINERD_RUNTIME_CLASS=nvidia`, `CONTAINERD_SET_AS_DEFAULT=true`.
- `driver.enabled: false`. No containerised driver.
- Files under `cluster/argocd/**` are CRLF, 2-space indent, lowercase keys; never add
  `syncPolicy.automated`.
- The chart renders 2 NVIDIA CRDs plus 3 NFD CRDs from the dependency; none may be dropped.
- Memory requests and limits on every container this chart renders or declares.
- No secrets in git.

## Review Focus

- A `helm template` that silently loses the CRDs or the ClusterPolicy renders a chart that deploys
  nothing — check the object list, not just the exit code.
- ArgoCD's `helm dependency build` fetches `gpu-operator` at sync time; `charts/` and `Chart.lock`
  must stay gitignored or the sync fights the repo.
- The DCGM Service selector must match the exporter pods (`app=nvidia-dcgm-exporter`), or Prometheus
  discovers a Service with no endpoints and GPU temperature silently disappears from the dashboard.
- Pinning `node-feature-discovery.worker.nodeSelector` to `streaming` drops NFD from the other three
  nodes; nothing in the repo reads NFD labels, but the operator's own `nvidia.com/gpu.deploy.*`
  labels are unaffected only because GFD, not NFD, writes them.
- The operator's upgrade-state label survives everything this plan does; the runbook must say so or
  the next person will chase it as a fault.

---

### Task 1: Wrapper chart with pinned values

**Files:**
- Create: `cluster/helm/platform/gpu-operator/Chart.yaml`
- Create: `cluster/helm/platform/gpu-operator/values.yaml`
- Modify: `.gitignore` (after the `cluster/helm/argocd/` block)

**Interfaces:**
- Produces: a chart whose `helm template` output Task 2 deploys and Task 3 documents. The Service
  it renders is `nvidia-dcgm-exporter-metrics` in the release namespace, selecting
  `app: nvidia-dcgm-exporter` on port 9400, annotated `prometheus.io/scrape: "true"`.

- [ ] **Step 1: Write `Chart.yaml`**

`apiVersion: v2`, `name: gpu-operator`, `description: NVIDIA GPU Operator - GPU stack`,
`type: application`, `version: 0.1.0`, `appVersion: v26.3.1`, and one dependency:
`name: gpu-operator`, `version: v26.3.1`, `repository: https://helm.ngc.nvidia.com/nvidia`.

- [ ] **Step 2: Write `values.yaml`**

Everything under the `gpu-operator:` key is NVIDIA's. Set, with a comment on each decision:

```yaml
gpu-operator:
  # The host driver is installed and upgraded on `streaming` by hand; the
  # operator must not build one against a moving HWE kernel. This is the
  # declaration that makes a rebuild safe — see docs/runbooks/gpu-operator.md.
  driver:
    enabled: false

  operator:
    version: v26.3.1
    resources: {requests: {cpu: 50m, memory: 96Mi}, limits: {memory: 192Mi}}
  validator:
    version: v26.3.1
    resources: {requests: {cpu: 10m, memory: 16Mi}, limits: {memory: 64Mi}}
  toolkit:
    version: v1.19.0
    env: [the four CONTAINERD_* entries, global constraint]
    resources: {requests: {cpu: 10m, memory: 32Mi}, limits: {memory: 96Mi}}
  devicePlugin:
    version: v0.19.0
    resources: {requests: {cpu: 10m, memory: 32Mi}, limits: {memory: 96Mi}}
  gfd:
    version: v0.19.0
    resources: {requests: {cpu: 10m, memory: 48Mi}, limits: {memory: 128Mi}}
  dcgmExporter:
    version: 4.5.1-4.8.0-distroless
    resources: {requests: {cpu: 50m, memory: 512Mi}, limits: {memory: 1Gi}}
  migManager:
    resources: {requests: {cpu: 10m, memory: 16Mi}, limits: {memory: 64Mi}}
```

Sizing comes from 7-day peaks in Prometheus: operator 89 MiB, dcgm-exporter 531 MiB, gfd 76 MiB,
toolkit 48 MiB, device plugin 42 MiB. Say so in a comment; the numbers are a measurement, not a
guess.

Then the NFD block (subchart values, nested under `gpu-operator.node-feature-discovery`): image
repository `registry.k8s.io/nfd/node-feature-discovery`, tag `v0.18.3`;
`worker.nodeSelector: {kubernetes.io/hostname: streaming}`;
`master.nodeSelector` and `gc.nodeSelector`: `{kubernetes.io/hostname: nuc0}`. Keep the subchart's
own resource defaults.

Then `extraObjects` with the metrics Service: `apiVersion: v1`, `kind: Service`,
`metadata.name: nvidia-dcgm-exporter-metrics`, `metadata.namespace: '{{ .Release.Namespace }}'`,
annotation `prometheus.io/scrape: "true"`, `spec.type: ClusterIP`,
`spec.selector: {app: nvidia-dcgm-exporter}`, one port `metrics` 9400/targetPort 9400/TCP. Comment
why it exists: the ClusterPolicy CRD has no field for Service annotations, and the operator's own
Service is not ours to annotate.

- [ ] **Step 3: Add the build artefacts to `.gitignore`**

After the `cluster/helm/argocd/charts/` + `Chart.lock` block, add the same two lines for
`cluster/helm/platform/gpu-operator/charts/` and `cluster/helm/platform/gpu-operator/Chart.lock`,
with the comment `# Built by ArgoCD at sync time`.

- [ ] **Step 4: Render and check the object list**

Run: `helm dependency build cluster/helm/platform/gpu-operator && helm template t cluster/helm/platform/gpu-operator -f cluster/helm/platform/gpu-operator/values.yaml --include-crds | grep '^kind:' | sort | uniq -c`
Expected: 5 `CustomResourceDefinition` (2 `nvidia.com`, 3 `nfd.k8s-sigs.io`), 1 `ClusterPolicy`,
1 `DaemonSet` (NFD worker), 3 `Deployment` (operator, NFD master, NFD gc), 2 `Job`, 5
`ClusterRole`/`ClusterRoleBinding` pairs, 2 `Role`/`RoleBinding` pairs, 4 `ServiceAccount`,
2 `ConfigMap`, and `nvidia-dcgm-exporter-metrics` among the Services.

- [ ] **Step 5: Check the rendered values against the live inventory**

Run: `helm template t cluster/helm/platform/gpu-operator -f cluster/helm/platform/gpu-operator/values.yaml | grep -E 'image:|nodeSelector|hostname|driver' | head -40`
Expected: operator `nvcr.io/nvidia/gpu-operator:v26.3.1`; NFD `registry.k8s.io/nfd/node-feature-discovery:v0.18.3`
on `streaming`; ClusterPolicy `driver.enabled: false`, `toolkit.version: v1.19.0`,
`devicePlugin.version: v0.19.0`, `dcgmExporter.version: "4.5.1-4.8.0-distroless"`,
`gfd.version: v0.19.0`, `validator.version: v26.3.1`, and the four `CONTAINERD_*` env entries.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/platform/gpu-operator .gitignore
git commit -m "🚀 feat(gpu): chart the GPU operator, pinned to the running release"
```

---

### Task 2: ArgoCD Application

**Files:**
- Create: `cluster/argocd/projects/platform/gpu-operator.yaml`

**Interfaces:**
- Consumes: the chart path from Task 1.

- [ ] **Step 1: Write the Application**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: gpu-operator
  namespace: argocd
spec:
  project: default
  destination:
    server: "https://kubernetes.default.svc"
    namespace: gpu-operator
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
  source:
    repoURL: "https://github.com/vyrtualsynthese/homelab.git"
    targetRevision: develop
    path: cluster/helm/platform/gpu-operator
```

No `syncPolicy.automated`, no `project:` other than `default` (the root app's project).

- [ ] **Step 2: Convert to CRLF and verify**

Run: `sed -i 's/$/\r/' cluster/argocd/projects/platform/gpu-operator.yaml && file cluster/argocd/projects/platform/gpu-operator.yaml`
Expected: `with CRLF line terminators`, and no doubled `\r`.

- [ ] **Step 3: Check the path resolves, the way the repo's other Applications do**

Run: `grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r" | while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add cluster/argocd/projects/platform/gpu-operator.yaml
git commit -m "🚀 feat(gpu): deploy the GPU operator through ArgoCD"
```

---

### Task 3: Write down the hand steps

**Files:**
- Create: `docs/runbooks/gpu-operator.md`
- Modify: `mkdocs.yml` (Runbooks nav, after the Renovate entry)
- Modify: `docs/audit/ansible-hosts.md:111` (item 5 of the "not in the repository" list)

**Interfaces:**
- Consumes: the chart path, values and decisions from Task 1.

- [ ] **Step 1: Write the runbook**

Sections, in this order: what git owns (chart, Application, pinned images, DCGM Service); what the
host owns (the driver on `streaming`, version today 580.178.04, CUDA 13.0, installed by hand, and
why the operator does not manage it — Pascal card, moving HWE kernel); how to check the GPU after a
change (`nvidia.com/gpu` allocatable 1, `nvidia-smi -L` in a `media-stack` pod, one NVENC transcode,
`DCGM_FI_DEV_GPU_TEMP` in Prometheus); the `nvidia.com/gpu-driver-upgrade-state=pod-restart-required`
label and why it is residue rather than a fault (the upgrade controller sees 0 nodes because
`cluster-policy` selects `app=nvidia-driver-daemonset`, which does not exist with the driver
disabled); what a node reboot does to the pods (they restart with the node, last state `Unknown`,
exit 255 — that is kubelet shutting them down, not a crash) with the 2026-11-16 and 2026-11-17
reboots as the measured example.

Each claim carries a measurement date. No `!!!` admonitions — blockquotes only.

- [ ] **Step 2: Add the nav entry**

`- The GPU stack: runbooks/gpu-operator.md` under `Runbooks`, after
`- Renovate: runbooks/renovate.md`.

- [ ] **Step 3: Update the audit line**

`docs/audit/ansible-hosts.md:111` currently reads `5. GPU host preparation — only
kubectl/gpu-operator-values.yaml exists, no driver install.` Change it to say the operator is now a
chart in git, the driver is the remaining hand step, and point at the runbook.

- [ ] **Step 4: Check for dangling links and nav**

Run: `grep -n "gpu" mkdocs.yml docs/audit/ansible-hosts.md | head` and confirm every relative link in
the new page resolves (`ls` the target).

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/gpu-operator.md mkdocs.yml docs/audit/ansible-hosts.md
git commit -m "📝 docs(gpu): record the GPU stack and its one hand step"
```

---

### Task 4: Whole-branch verification

**Files:** none

- [ ] **Step 1: Render every chart that can be rendered**

Run the loop from `AGENTS.md` §6 over `cluster/helm`, plus `helm template` on the new chart.
Expected: no `FAIL`, and `SKIP` only for charts whose dependencies are not built locally (including
this one before `helm dependency build`).

- [ ] **Step 2: Run the exposure check**

Run: `./hack/check-exposure.sh`
Expected: passes; the chart adds no route.

- [ ] **Step 3: Rebase and re-run**

`git fetch origin && git rebase origin/develop`, then re-run steps 1-2.

- [ ] **Step 4: Push and open the PR**

`git push -u origin feature/gpu-operator`, then a pull request to `develop` describing the five
decisions, the restart-wave finding, the one-time cutover command, and the verification output.

- [ ] **Step 5: Move the card**

Vikunja #248 to `Validation`, with branch, PR link and verification output in a comment. Not `Done`.

---

## Cutover, after merge (operator and agent together)

The ArgoCD sync creates a second GPU Operator release; the hand-installed one must go first, or two
releases manage one ClusterPolicy. Sequence:

1. `helm -n gpu-operator uninstall gpu-operator` after the PR merges but before the sync is enabled
   — the CRDs and the operator's objects survive because the chart sets `cleanupCRD: false`; verify
   with `kubectl -n gpu-operator get ds,deploy`.
2. Trigger the sync for the new Application, then watch `kubectl -n gpu-operator get pods -w`.
3. `kubectl annotate -n gpu-operator svc nvidia-dcgm-exporter prometheus.io/scrape-` — otherwise
   Prometheus scrapes the exporter twice and the GPU panels draw two lines.
4. `kubectl label node streaming nvidia.com/gpu-driver-upgrade-state- --overwrite` and record the
   driver as host-managed on the card.
5. Verify with the runbook checklist, then comment the output on the card.
