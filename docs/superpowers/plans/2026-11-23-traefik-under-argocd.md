# Traefik under ArgoCD — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the Traefik release under a manual-sync ArgoCD Application that adopts the live objects in place, and record the way out.

**Architecture:** A wrapper chart at `cluster/helm/platform/traefik/` pins upstream chart `42.4.1` as a dependency and carries the release's only values file, nested under `traefik:`. An Application at `cluster/argocd/projects/platform/traefik.yaml` renders it with `helm.skipCrds: true` and no auto-sync. ArgoCD's first sync adopts the objects Helm created; the pod is never replaced.

**Tech Stack:** Helm 3, ArgoCD 3.6.2, K3s, Traefik v3, the repository's `hack/` checkers and pytest suite.

**Spec:** `docs/superpowers/specs/2026-11-23-traefik-under-argocd-design.md`

## Global Constraints

- Every file under `cluster/argocd/**` uses CRLF line endings; `hack/check-exposure.sh` fails an LF rewrite.
- No Application records `syncPolicy.automated`; sync policy lives in the ArgoCD UI, and a root-app sync rewrites the file to match git.
- The Application keeps `helm.skipCrds: true` and no auto-sync. Manual sync only, by the operator's decision.
- Chart `appVersion` is `3.7.19` — the leading number of the first `tag:` in `values.yaml`, with the `v` dropped (`docs/charts/README.md` requirement 1).
- The dependency version is pinned exactly: `version: 42.4.1`. No range, no `latest`.
- The Deployment's `spec.selector` is immutable; the rendered release name must stay `traefik` in `kube-system`, which is what produces the live `app.kubernetes.io/instance: traefik-kube-system`.
- Desired state must not carry `clusterIP`, `nodePort`, `healthCheckNodePort`, the `load-balancer-cleanup` finalizer, or API-defaulted fields.
- Documentation: no new page, so no `mkdocs.yml` edit. Every page touched keeps its banner and gets `Verified 2026-11-23`.
- Plain language in every doc: a design or media term that a DevOps reader would not know gets glossed in the same sentence (`~/.config/opencode/AGENTS.md`).
- Commit messages carry an emoji prefix (`⬆️` upgrade, `🐛` fix, `🚀` feature, `♻️` refactor, `📝` docs, `🔧` chore).

## Review Focus

- **A sync that restarts the router.** The adoption is invalid if the pod name changes or its restart count moves: a restart is what re-requests certificates, and Let's Encrypt allows five duplicates a week. Pinned in Task 1 (render equality) and Task 7 (pod identity before and after).
- **Values nested under the wrong key.** A flat `values.yaml` inside the wrapper renders chart defaults; the first sync would then rewrite the router's entry points, ACME resolver and claim. Pinned by the render-diff step in Task 1.
- **`skipCrds` missing.** ArgoCD then applies `crds/`: fifteen `hub.traefik.io` CRDs that need a licence, and ownership of the ten `traefik.io` ones the manual step owns. The diff can never be empty. Pinned by the assertion step in Task 2.
- **The dashboard's gate silently unverified.** `hack/check-exposure.sh` names its values file by path; once the file moves, a stale path means the check stops seeing the route instead of failing. Pinned by the red/green step in Task 1.
- **`helm uninstall traefik` after adoption.** The Helm release record survives the adoption, so an uninstall would delete the running router. Pinned in Task 4 (the runbook states the trap) and Task 7 (the record is deleted).

---

### Task 1: The wrapper chart, and the values move

**Files:**
- Create: `cluster/helm/platform/traefik/Chart.yaml`
- Create: `cluster/helm/platform/traefik/values.yaml` (from `git mv cluster/infrastructure/traefik/values-v3.yaml`)
- Create: `cluster/helm/platform/traefik/README.md`
- Rename: `cluster/infrastructure/traefik/values.yaml` → `cluster/infrastructure/traefik/values-25.0.3.yaml`
- Modify: `.gitignore` (after the `gpu-operator` block, line 49)
- Modify: `hack/check-exposure.sh:137`

**Interfaces:**
- Produces: a chart whose `helm template traefik cluster/helm/platform/traefik -n kube-system` output is byte-equal to the live objects once server-defaulted fields are normalised. Task 2's Application points at this path; Tasks 4 and 5 cite `cluster/helm/platform/traefik/values.yaml` as the live values file.

- [ ] **Step 1: Move the live values into the chart and nest them**

```bash
mkdir -p cluster/helm/platform/traefik
git mv cluster/infrastructure/traefik/values-v3.yaml cluster/helm/platform/traefik/values.yaml
python3 - <<'EOF'
import pathlib
p = pathlib.Path("cluster/helm/platform/traefik/values.yaml")
lines = p.read_text(encoding="utf-8").splitlines()
body = ["  " + line if line.strip() else line for line in lines]
p.write_text("\n".join(body) + "\n", encoding="utf-8")
EOF
```

- [ ] **Step 2: Replace the file's header comment**

The file opens with `# Chart 42.4.1, Traefik 3.7.19. The v3 release of W5 task 8.` and a paragraph about deriving it. Replace that opening with a comment stating: this is the live values file of the `traefik` Application; the `traefik:` indentation below is the wrapper's subchart key and must not be flattened; the two older files (`values-25.0.3.yaml`, `values-2.11.yaml`) in `cluster/infrastructure/traefik/` describe earlier chart lines. Keep every existing explanation below it, unindented as a comment, and keep the body exactly as Step 1 left it.

- [ ] **Step 3: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: traefik
description: Traefik - the cluster's ingress router
type: application
version: 0.1.0
appVersion: "3.7.19"
dependencies:
  - name: traefik
    version: 42.4.1
    repository: https://traefik.github.io/charts
```

- [ ] **Step 4: Write `README.md`**

State, in plain language: what the chart is (a wrapper pinning the upstream chart at one exact version, adding no objects of its own); that ArgoCD renders it with `helm.skipCrds`, so the chart's `crds/` directory is applied by hand from a pulled tarball and a dependency bump needs that step first (`docs/runbooks/traefik-release.md`); that the release name and destination namespace are fixed because the Deployment's selector is immutable; that the live values file is this chart's `values.yaml` and it nests under `traefik:`; and that a Renovate bump of the dependency is safe to merge because the Application syncs only when a person syncs it.

- [ ] **Step 5: Add the `.gitignore` entries**

After the `cluster/helm/platform/gpu-operator/Chart.lock` line:

```
# Built by ArgoCD at sync time
cluster/helm/platform/traefik/charts/
cluster/helm/platform/traefik/Chart.lock
```

- [ ] **Step 6: Prove the move broke the gate check, then repair it**

```bash
hack/check-exposure.sh --only platform/traefik; echo "exit=$?"
```

Expected now: FAIL or a Python traceback, because line 137 still reads `cluster/infrastructure/traefik/values.yaml`, which Step 7 renames away — this step runs after Step 7 if the file no longer exists, so record which of the two failures you saw. Then change line 137 to:

```bash
  dashboard="$ROOT/cluster/helm/platform/traefik/values.yaml"
```

and re-run; expected: `cluster/helm/platform/traefik` reports its routes, `result: OK`, `exit=0`.

- [ ] **Step 7: End the file-name clash**

```bash
git mv cluster/infrastructure/traefik/values.yaml cluster/infrastructure/traefik/values-25.0.3.yaml
```

- [ ] **Step 8: Render and compare against the live objects**

```bash
export KUBECONFIG=$HOME/.kube/mcp-readonly.yaml
helm dependency build cluster/helm/platform/traefik
helm lint cluster/helm/platform/traefik -f cluster/helm/platform/traefik/values.yaml
helm template traefik cluster/helm/platform/traefik -n kube-system > /tmp/opencode/render.yaml
kubectl -n kube-system get deploy,svc,sa,ingressroute.traefik.io \
  -l app.kubernetes.io/instance=traefik-kube-system -o yaml > /tmp/opencode/live.yaml
```

Save this as `/tmp/opencode/live-diff.py` and run it:

```python
import copy, difflib, yaml

def load(path):
    return [d for d in yaml.safe_load_all(open(path)) if d]

strip = {'uid', 'resourceVersion', 'creationTimestamp', 'generation', 'managedFields'}

def clean(doc):
    doc = copy.deepcopy(doc)
    for key in strip:
        doc['metadata'].pop(key, None)
    doc['metadata'].pop('annotations', None)
    doc['metadata'].pop('labels', None)
    doc.pop('status', None)
    if doc['kind'] == 'Service':
        for key in ('clusterIP', 'clusterIPs', 'healthCheckNodePort'):
            doc['spec'].pop(key, None)
        for port in doc['spec'].get('ports', []):
            port.pop('nodePort', None)
    if doc['kind'] == 'Deployment':
        doc['spec']['template']['metadata'].pop('annotations', None)
        doc['spec']['template']['metadata'].pop('labels', None)
    return doc

rendered = {d['kind']: clean(d) for d in load('/tmp/opencode/render.yaml')}
live = {d['kind']: clean(d) for d in load('/tmp/opencode/live.yaml')[0]['items']}
for kind in sorted(set(rendered) | set(live)):
    a, b = rendered.get(kind), live.get(kind)
    if a is None or b is None:
        print(f"{kind}: present in one side only")
        continue
    if a == b:
        print(f"SAME {kind}")
        continue
    print(f"DIFF {kind}")
    for line in difflib.unified_diff(
        yaml.safe_dump(a, sort_keys=True).splitlines(),
        yaml.safe_dump(b, sort_keys=True).splitlines(),
        'rendered', 'live', lineterm='', n=0,
    ):
        print("   ", line)
```

Expected: `SAME IngressRoute`, `SAME ServiceAccount`, and for `Deployment` and `Service` only server-defaulted lines (`progressDeadlineSeconds`, `terminationMessagePath`, `restartPolicy`, `schedulerName`, `serviceAccount`, `apiVersion: v1` on `fieldRef`, `sessionAffinity`, `ipFamilies`, `allocateLoadBalancerNodePorts`, `internalTrafficPolicy`, the `load-balancer-cleanup` finalizer) plus the Service port order (`studp` before `web` live, after it rendered). **No** difference in arguments, image, node selector, claim, strategy, ports' names or `targetPort` means the nesting preserved the values. If the diff shows chart-default arguments (a second resolver, a missing `--serversTransport.maxIdleConnsPerHost`), the `traefik:` indentation is wrong — stop and fix Step 1.

- [ ] **Step 9: Run the chart checkers**

```bash
hack/check-charts.sh --only platform/traefik
hack/check-app-version.sh --only platform/traefik
hack/check-exposure.sh --only platform/traefik
```

Expected: all three exit 0; `check-app-version` prints the chart as checked, not exempt.

- [ ] **Step 10: Commit**

```bash
git add cluster/helm/platform/traefik cluster/infrastructure/traefik .gitignore hack/check-exposure.sh
git commit -m "🚀 feat(traefik): wrap the release in a chart the repository owns"
```

---

### Task 2: The Application, and the RBAC that can see what it manages

**Files:**
- Create: `cluster/argocd/projects/platform/traefik.yaml`
- Modify: `kubectl/mcp-readonly-rbac.yaml` (the `opencode-mcp-read-crds` ClusterRole's `rules:`)

**Interfaces:**
- Consumes: the chart path from Task 1.
- Produces: Application `traefik` in namespace `kube-system`, which Task 7 syncs. The RBAC grant lets Task 7 read `ClusterRole/traefik-kube-system`, `ClusterRoleBinding/traefik-kube-system` and `IngressClass/traefik` after the operator applies the file.

- [ ] **Step 1: Write the Application**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: traefik
  namespace: argocd
spec:
  project: default
  destination:
    server: "https://kubernetes.default.svc"
    namespace: kube-system
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
  source:
    repoURL: "https://github.com/vyrtualsynthese/homelab.git"
    targetRevision: develop
    path: cluster/helm/platform/traefik
    helm:
      skipCrds: true
```

No `ignoreDifferences` yet: the design says every entry comes from a diff someone observed, and Task 7 observes it. No `automated` block, ever.

- [ ] **Step 2: Make it CRLF**

```bash
python3 - <<'EOF'
import pathlib
p = pathlib.Path("cluster/argocd/projects/platform/traefik.yaml")
text = p.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
p.write_text(text, encoding="utf-8", newline="")
EOF
file cluster/argocd/projects/platform/traefik.yaml
```

Expected: `ASCII text, with CRLF line terminators`.

- [ ] **Step 3: Assert the two settings the design pins**

```bash
grep -c 'skipCrds: true' cluster/argocd/projects/platform/traefik.yaml   # 1
grep -c 'automated' cluster/argocd/projects/platform/traefik.yaml        # 0
```

Expected: `1` then `0`.

- [ ] **Step 4: Grant the missing cluster-scoped reads**

In `kubectl/mcp-readonly-rbac.yaml`, inside the `opencode-mcp-read-crds` ClusterRole's `rules:`, after the `storageclasses` rule:

```yaml
  # The cluster-scoped objects a chart owns. Without these, a release cannot be checked for
  # adoption: the RBAC bindings and the IngressClass are invisible to the read-only identity.
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources: ["clusterroles", "clusterrolebindings"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingressclasses"]
    verbs: ["get", "list", "watch"]
```

- [ ] **Step 5: Verify the references resolve, as the root Application would see them**

```bash
python3 - <<'EOF'
import pathlib, yaml
for path in sorted(pathlib.Path("cluster/argocd/projects").rglob("*.yaml")):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and doc.get("kind") == "Application":
        target = pathlib.Path(doc["spec"]["source"]["path"])
        if not target.is_dir():
            print("BROKEN PATH", path)
print("references checked")
EOF
hack/check-exposure.sh --only platform/traefik; echo "exit=$?"
```

Expected: `references checked`, then `result: OK` and `exit=0` — the second command also runs the CRLF loop over `cluster/argocd/**`, which is how Step 2 is proved.

- [ ] **Step 6: Commit**

```bash
git add cluster/argocd/projects/platform/traefik.yaml kubectl/mcp-readonly-rbac.yaml
git commit -m "🚀 feat(argocd): give the Traefik release an Application, manual sync only"
```

---

### Task 3: The routes page learns the dashboard route

**Files:**
- Test: `hack/tests/test_gen_docs.py`
- Modify: `hack/gen_docs.py` (`_declares_a_route`, line 212, and the fixed text in `render_routes`, line 289)
- Modify: `docs/reference/routes.md` (generated)

**Interfaces:**
- Consumes: the chart from Task 1, whose `values.yaml` carries `traefik.ingressRoute.dashboard.enabled: true`.
- Produces: `gen_docs._declares_a_dashboard_route(values: dict) -> bool`, used by `_declares_a_route`.

- [ ] **Step 1: Write the failing test**

In `hack/tests/test_gen_docs.py`, after `RenderRoutesTests`:

```python
class DeclaresARouteTests(unittest.TestCase):
    def test_a_dashboard_values_block_declares_a_route(self):
        with temp_tree(
            {
                "cluster/helm/x/Chart.yaml": "apiVersion: v2\nname: x\nversion: 0.1.0\n",
                "cluster/helm/x/values.yaml": (
                    "traefik:\n  ingressRoute:\n    dashboard:\n      enabled: true\n"
                ),
            }
        ) as root:
            self.assertTrue(gen_docs._declares_a_route(root / "cluster/helm/x"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest hack/tests/test_gen_docs.py::DeclaresARouteTests -q`
Expected: FAIL — `AssertionError: False is not true`, because no template or values line contains the literal `IngressRoute`.

- [ ] **Step 3: Implement the detection**

In `hack/gen_docs.py`, next to `_declares_a_route`:

```python
def _declares_a_dashboard_route(values) -> bool:
    """True when a values tree carries the Traefik chart's dashboard block.

    The upstream chart renders its dashboard IngressRoute from values rather than a template
    whose text names it, so a wrapper that carries the block declares a route without saying so.
    """
```

It returns `True` when a dict at any depth has `ingressRoute.dashboard.enabled` exactly `True`, and recurses through dict values. Then extend `_declares_a_route` to fall through from its literal scan to a YAML parse of `values*.y*ml`, returning `True` on the first file that parses and satisfies the helper, and `False` if none does (an unparseable file is skipped, not fatal).

- [ ] **Step 4: Run the test again**

Run: `python3 -m pytest hack/tests/test_gen_docs.py -q`
Expected: PASS, all classes.

- [ ] **Step 5: Rewrite the fixed sentence that is now false**

In `render_routes`, replace the two lines beginning `A route that no Application owns is not here: the Traefik dashboard is a Helm release value, and it is named in` with one statement that stays true: the dashboard's `web` twin is applied by hand from `cluster/infrastructure/traefik/dashboard/redirect.yaml`, so its row shows `websecure` only. Keep the following sentence about `loki.kub.local` intact.

- [ ] **Step 6: Regenerate and prove the page is stable**

```bash
python3 hack/gen_docs.py --write --only routes
git diff --stat docs/reference/routes.md
python3 hack/gen_docs.py --only routes; echo "exit=$?"
grep -n 'traefik.kub.local' docs/reference/routes.md
```

Expected: the row `| \`traefik.kub.local\` | \`traefik\` | \`kube-system\` |` with `kube-system-lan-only` and `kube-system-traefik-dashboard` in the gate column, `internal-tls` as the certificate and `no` in `Public?`; the second generator run exits 0 with no further diff.

- [ ] **Step 7: Run the doc checks that read this page**

```bash
hack/check-docs.sh --base origin/develop; echo "exit=$?"
hack/check-exposure.sh --only platform/traefik; echo "exit=$?"
```

Expected: both exit 0.

- [ ] **Step 8: Commit**

```bash
git add hack/gen_docs.py hack/tests/test_gen_docs.py docs/reference/routes.md
git commit -m "🚀 feat(docs): render the Traefik dashboard into the routes page"
```

---

### Task 4: The runbook owns the release, and says how to leave ArgoCD

**Files:**
- Modify: `docs/runbooks/traefik-release.md`
- Modify: `docs/runbooks/README.md` (the `traefik-release.md` row, line 35)

**Interfaces:**
- Consumes: the chart path and values path from Task 1, the Application name from Task 2.
- Produces: the procedure Task 7 follows, including the `helm uninstall` trap.

- [ ] **Step 1: Rewrite the header and "In one line"**

Header gains `**Adopted by ArgoCD** 2026-11-23, Application `traefik`, manual sync only`. The one-liner stops saying the release is moved with `helm` from one values file and says instead: the release is ArgoCD's, from the wrapper chart in `cluster/helm/platform/traefik`, synced by hand; `helm` remains the way to run it by hand when ArgoCD is the thing that is broken. `Verified 2026-11-23`.

- [ ] **Step 2: Rebuild "The shape of the problem"**

The table's rows become: the values the release runs with → `cluster/helm/platform/traefik/values.yaml` (nested under `traefik:`); the file a rollback to 2.11 needs → `cluster/infrastructure/traefik/values-2.11.yaml` (flat, chart `27.0.2`); the file a rollback to revision 9 needs → `values-25.0.3.yaml` (flat, chart `25.0.3`); the chart → pinned in the wrapper's `Chart.yaml` at `42.4.1`; the ArgoCD Application → `cluster/argocd/projects/platform/traefik.yaml`; the certificate store and owned objects rows stay. Add the sentence that explains why two value shapes exist: a wrapper carries the subchart's values under the dependency's key, and only the wrapper's file is the live one.

- [ ] **Step 3: Add the ArgoCD section**

After "Moving to v3, chart `42.4.1`", a section stating: sync is manual by decision — the Application records no `automated`, and a root-app sync would not add it; how to sync (`argocd login argocd.kub.local --sso`, then `argocd app sync traefik --server-side --force`, because Helm's field manager owns the fields); that `helm.skipCrds` means CRDs are never touched by a sync, so a chart bump applies them from a pulled tarball first; that `ignoreDifferences` entries exist only for noise an observed `argocd app diff` named; and that a sync renders what the wrapper's `values.yaml` says, so the render diff in Task 1 is the check to repeat after editing it.

- [ ] **Step 4: Add the way out**

A section `## Getting back out of ArgoCD`, stating: deleting the Application with `kubectl delete application traefik -n argocd --cascade=orphan` leaves every object in place (the default cascade would delete the router); then `helm dependency build cluster/helm/platform/traefik && helm upgrade traefik cluster/helm/platform/traefik -n kube-system --reset-values -f cluster/helm/platform/traefik/values.yaml --wait --timeout 5m` puts the release back under Helm, which is today's state; and the trap — while the Helm release record exists (`helm -n kube-system list` shows it), `helm uninstall traefik` deletes the running router, so the record is deleted (`kubectl -n kube-system delete secret sh.helm.release.v1.traefik.v<N>`, read `<N>` from `helm history`) once the ArgoCD sync is clean, and never before.

- [ ] **Step 5: Close the addon path**

In "The ownership cutover, as it was done", add a line at the top: re-enabling the addon is not a rollback any more — the `traefik.containo.us` CRDs were deleted on 2026-11-21 and every route is on `traefik.io`, so the addon's chart `25.0.3` would read a group that holds nothing, and the ingress would go dark. The section is history, kept for a rebuilt server.

- [ ] **Step 6: Update the shelf row**

In `docs/runbooks/README.md`, the `traefik-release.md` row: status `proved 2026-11-21 …; adopted by ArgoCD 2026-11-23`, and the "Use it when" clause gains "or the Traefik Application has to be synced by hand or taken back out of ArgoCD".

- [ ] **Step 7: Verify the docs**

```bash
hack/check-docs.sh --base origin/develop; echo "exit=$?"
grep -n "values-v3\|values\.yaml" docs/runbooks/traefik-release.md | head
```

Expected: exit 0, and no line still calls `cluster/infrastructure/traefik/values.yaml` the live file.

- [ ] **Step 8: Commit**

```bash
git add docs/runbooks/traefik-release.md docs/runbooks/README.md
git commit -m "📝 docs: record the Traefik release as ArgoCD's, and the way back out"
```

---

### Task 5: The pages that go stale

**Files:**
- Modify: `docs/how-it-works/applications.md` (the plumbing table, before the `traefik-gate` row)
- Modify: `docs/how-it-works/gitops.md` (lines 36 and 85)
- Modify: `docs/how-it-works/network.md` (lines 124-129)
- Modify: `docs/runbooks/acme-store.md` (lines 38-39 and 283-284)
- Modify: `AGENTS.md` (§2, lines 34-38)

**Interfaces:**
- Consumes: the Application name from Task 2, the values path from Task 1.
- Produces: no page claims the release is installed by hand, and every path they cite exists.

- [ ] **Step 1: Add the catalogue row**

`applications.md`, plumbing table, alphabetically before `traefik-gate`:

```
| `traefik` | `kube-system` | streaming | D | the ingress router: the one door the house router forwards to |
```

- [ ] **Step 2: Correct the GitOps page**

Line 36's `cluster/infrastructure/` row stops implying the router is in that path: it keeps "Traefik configuration, middlewares, the TLS store and storage classes — **no Application owns this path**", which stays true, and the router's chart and Application are named separately. Line 85's row keeps `cluster/infrastructure/` for "a cluster-wide Traefik rule or storage class" and gains a pointer that the release itself is `cluster/helm/platform/traefik`.

- [ ] **Step 3: Correct the network page**

Replace the sentence saying the routes page cannot show the dashboard because `traefik.kub.local` is a value of the Traefik Helm release: the row is rendered from the chart now, and the `web` twin in `cluster/infrastructure/traefik/dashboard/redirect.yaml` is hand-applied and still outside the table.

- [ ] **Step 4: Repoint the certificate-store page**

`acme-store.md` lines 38-39 and 283-284: the live file is `cluster/helm/platform/traefik/values.yaml`; `values-2.11.yaml` and `values-25.0.3.yaml` describe the earlier charts. The relative Markdown links must resolve inside the repository — from `docs/runbooks/` that is `../../cluster/helm/platform/traefik/values.yaml`.

- [ ] **Step 5: Correct `AGENTS.md` §2**

The bullet that says `cluster/infrastructure/**` is applied by hand and that the Traefik release is installed from `cluster/infrastructure/traefik/values.yaml` and moved with `helm`: keep the hand-applied part for the middlewares and the TLSStore, and state that the release is an Application (`traefik`, manual sync) over the wrapper chart at `cluster/helm/platform/traefik`, whose `values.yaml` is the live file.

- [ ] **Step 6: Verify nothing cites the moved files**

```bash
grep -rn "infrastructure/traefik/values.yaml\|values-v3" AGENTS.md docs/ --include='*.md' | grep -v superpowers/
hack/check-docs.sh --base origin/develop; echo "exit=$?"
```

Expected: no hit outside `docs/superpowers/` (specs and plans are records of what was true then) and outside the runbook's "Moving to v3" and "ownership cutover" sections, which are history and say so at their head, and exit 0. The `docs/audit/` snapshot is dated evidence and is not edited.

- [ ] **Step 7: Commit**

```bash
git add docs/how-it-works docs/runbooks/acme-store.md AGENTS.md
git commit -m "📝 docs: put the Traefik release under GitOps on the pages that said otherwise"
```

---

### Task 6: Full suite, then the PR

**Files:** none new.

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: a PR against `develop`, which Task 7 depends on.

- [ ] **Step 1: Rebase onto the integration branch**

```bash
git fetch origin
git rebase origin/develop
```

- [ ] **Step 2: Run every checker and the test suite**

```bash
hack/check-charts.sh; echo "charts=$?"
hack/check-app-version.sh; echo "appversion=$?"
hack/check-exposure.sh; echo "exposure=$?"
hack/check-config-keys.sh; echo "configkeys=$?"
hack/check-docs.sh --base origin/develop; echo "docs=$?"
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests; echo "tests=$?"
```

Expected: every line prints `0`, and pytest reports no failures. Any non-zero is fixed before the PR, not explained in it.

- [ ] **Step 3: Push and open the pull request**

```bash
git push -u origin feature/traefik-argocd
```

Then, through the GitHub MCP (`create_pull_request`), base `develop`, head `feature/traefik-argocd`. The body states: what the chart is and why the values nest; that the Application adopts in place with `--server-side --force` and syncs by hand; the render-diff evidence; the two follow-ups the merge enables (the first sync and the deletion of the Helm release record); and that the card stays in `Doing` until Task 7's proof exists.

- [ ] **Step 4: Wait for the merge, then continue to Task 7**

Do not sync before the merge: the Application does not exist in the cluster until the root Application has picked the file up from `develop`.

---

### Task 7: Adopt the release on the cluster, and prove a no-op sync

**Files:**
- Modify: `cluster/argocd/projects/platform/traefik.yaml` (`ignoreDifferences`, from the diff this task observes)
- Modify: `docs/runbooks/traefik-release.md` (the measured result)
- Modify (board): the `homelab` card

**Interfaces:**
- Consumes: the merged Application, the operator's ArgoCD session, and the baseline pod `traefik-7f8b9d6b48-79zcs` (created 2026-11-22T19:22:34Z, 0 restarts).
- Produces: `Synced`/`Healthy` with an empty diff, and the card's done-when evidence.

- [ ] **Step 1: Have the operator sign in, and confirm the Application appeared**

```bash
argocd login argocd.kub.local --sso
argocd app get traefik
```

Expected: the Application exists and reports `OutOfSync`, `Missing` or `Unknown` — it has been rendered but never synced. If the operator cannot sign in, stop here: the card stays in `Doing` with a comment naming the sign-in as the blocker.

- [ ] **Step 2: Read the diff before applying it**

```bash
argocd app diff traefik | tee /tmp/opencode/first-diff.txt
export KUBECONFIG=$HOME/.kube/mcp-readonly.yaml
kubectl -n kube-system get deploy traefik \
  -o jsonpath='{.spec.selector}{"\n"}{.spec.template.metadata.labels}{"\n"}'
helm template traefik cluster/helm/platform/traefik -n kube-system \
  | python3 -c 'import sys,yaml
docs=[d for d in yaml.safe_load_all(sys.stdin) if d]
d=[x for x in docs if x["kind"]=="Deployment"][0]
print(d["spec"]["selector"]); print(d["spec"]["template"]["metadata"]["labels"])'
```

Expected: the Service's port order, plus `metadata` noise (`deployment.kubernetes.io/revision`, `finalizers`), and the two printed selector/label pairs identical — `app.kubernetes.io/instance: traefik-kube-system` in both, and the same `spec.selector.matchLabels`. The render diff in Task 1 stripped labels to compare them, so this is where the immutable selector is checked: a difference here is a `Recreate` rollout of the router, not a sync. Anything else — a changed argument, image, selector, claim — means the chart and the release disagree; stop and fix the chart, do not sync.

- [ ] **Step 3: Sync, with server-side apply and force**

```bash
argocd app sync traefik --server-side --force
argocd app get traefik
```

Expected: `Synced`, `Healthy`, and the resource list names the Deployment, Service, ServiceAccount, ClusterRole, ClusterRoleBinding, IngressClass and dashboard IngressRoute.

- [ ] **Step 4: Prove the router was not restarted**

```bash
export KUBECONFIG=$HOME/.kube/mcp-readonly.yaml
kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.creationTimestamp}{"\t"}{.status.containerStatuses[0].restartCount}{"\n"}{end}'
for h in plex.kub.home.ashudev.com dashy.ashudev.com ashuvidz.com; do
  curl -s -o /dev/null -w "$h %{http_code}\n" --resolve $h:443:192.168.1.46 https://$h/
done
```

Expected: the same pod name and creation time as the baseline, `0` restarts — so `/data/acme.json` is untouched — and the same status codes the runbook's `After` section expects.

- [ ] **Step 5: Add the `ignoreDifferences` the diff named, in git**

Take only what Step 2 showed and no more, in the expected shape:

```yaml
  ignoreDifferences:
    - group: apps
      kind: Deployment
      jsonPointers:
        - /metadata/annotations/deployment.kubernetes.io~1revision
    - group: ""
      kind: Service
      jsonPointers:
        - /metadata/finalizers
```

Keep the file CRLF. Commit, push, and open a second small PR (or add the commit to the first if it is still open): an ignore rule is a git change like any other, never an `argocd app set` on the cluster.

- [ ] **Step 6: Prove the no-op sync**

```bash
argocd app diff traefik
argocd app sync traefik
```

Expected after the follow-up merges: `argocd app diff` prints nothing, and the sync reports `no differences` / `already synced`. A remaining diff means another field needs a rule — read it and add it, do not widen the pointer set.

- [ ] **Step 7: Forget the Helm release, now that ArgoCD owns the objects**

```bash
helm -n kube-system list
helm -n kube-system history traefik | tail -1     # read <N>
kubectl -n kube-system delete secret sh.helm.release.v1.traefik.v<N>
helm -n kube-system list                          # empty
```

Expected: the release disappears from `helm list`, and nothing else changes — no pod restart, no route lost. Deleting the record is what removes the `helm uninstall` trap.

- [ ] **Step 8: Record the measured result**

In `traefik-release.md`, under the ArgoCD section: the date, the chart `42.4.1`, the one sync, the diff that was ignored and why, the pod identity unchanged, the routes answering, and that `helm list` is empty. Then move the card to `Validation` with the branch, both PR links, and this verification in its description.

---

## Self-Review

**Spec coverage:** chart and pin (Task 1), values move and nesting (Task 1), Application with `skipCrds` and no `automated` (Task 2), RBAC for the three cluster-scoped objects (Task 2), CRDs outside ArgoCD (Tasks 2 and 4), sync procedure (Tasks 4 and 7), rollback record (Task 4), the routes page (Task 3), the gate checker (Task 1), stale docs (Task 5), Renovate left alone (no task, as decided), verification (Tasks 1, 6 and 7).

**Type consistency:** `_declares_a_dashboard_route(values)` is the only new function, defined and used in Task 3. Paths are identical across tasks: `cluster/helm/platform/traefik/values.yaml`, `cluster/argocd/projects/platform/traefik.yaml`, `cluster/infrastructure/traefik/values-25.0.3.yaml`.

**Review Focus:** each line names its pinning step — pod identity (Task 1 Step 8, Task 7 Step 4), nesting (Task 1 Step 8), `skipCrds` (Task 2 Step 3), the gate check (Task 1 Step 6), the uninstall trap (Task 4 Step 4, Task 7 Step 7).
