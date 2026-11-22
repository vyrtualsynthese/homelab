# Retire the `node-type` label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `AGENTS.md` §4 literally true — the `node-type` label does not exist on the nodes, no chart asks for it, and the pages describe what actually happened.

**Architecture:** One chart moves from a required `nodeAffinity` on `node-type=general` to the house `nodeSelector` on `nuc0`; the pages and chart notes that treated the label as absent are corrected to say it was present but retired, and what really kept the pods they describe `Pending`. The four node labels are removed only after the exporter has synced, so no pod is left unschedulable.

**Tech Stack:** Helm 3 charts rendered by ArgoCD, Markdown docs published by the homelab docs site, `kubectl` for the one cluster-side step.

**Spec:** `docs/superpowers/specs/2026-11-22-node-type-label-removal-design.md`

## Global Constraints

- Chart placement rule: `nodeSelector: kubernetes.io/hostname: <node>`; `node-type` must not appear in any rendered chart (AGENTS.md §4).
- The chart change must be deployed (`pihole-exporter` Application `Synced`, pod carrying the new `nodeSelector`) **before** the label is removed. The current affinity is `requiredDuringSchedulingIgnoredDuringExecution`; removing the label first leaves the exporter `Pending` on its next restart.
- Node labels are not in git and no reconciler owns them: the removal is four `kubectl label node <n> node-type-` calls, recorded in the card, not in a manifest.
- Never `kubectl apply` or `kubectl edit` a GitOps-managed object. The chart reaches the cluster through the merge.
- YAML: 2 spaces, lowercase keys; files under `cluster/argocd/**` keep CRLF — none of this change touches them.
- `hack/check-charts.sh` is the chart gate; `helm lint` alone misses nothing here but both are cheap.

## Review Focus

1. A page that still says an old affinity "matched no node" — the reader concludes placement, not a missing claim, was the blocker. Pinned by Task 2's `grep -rn "matched no node"` returning nothing.
2. Any chart in `cluster/helm/` still rendering `node-type` — its pod goes `Pending` the moment the label is gone. Pinned by Task 1's repo-wide `grep` and `helm template` sweep.
3. The label removed before the exporter resynced — the exporter `Pending` within one restart. Pinned by Task 3's live-spec check, which precedes the removal.
4. A doc claiming the label is gone while it is still on the nodes — the same defect, mirrored. Pinned by Task 3's `kubectl get nodes -L node-type`.
5. The removal recorded nowhere — a future node rebuild silently pairs a stale page with a label that was never there. Pinned by Task 3's card comment naming the four commands and their output.

---

### Task 1: Pin `pihole-exporter` to `nuc0` by hostname

**Files:**
- Modify: `cluster/helm/monitoring/pihole-exporter/values.yaml:61-74`

**Interfaces:**
- Consumes: nothing.
- Produces: a chart that renders `nodeSelector: {kubernetes.io/hostname: nuc0}` and no `affinity:` key. Task 3 reads exactly that key off the live pod.

- [ ] **Step 1: Replace the affinity block with the house pin**

Current tail of the file:

```yaml
nodeSelector: {}

tolerations: {}

affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: node-type
              operator: In
              values:
                - general
```

Replace with:

```yaml
# Pinned the way every other workload is: a hostname, not the retired
# `node-type` label. `nuc0` is where this pod has run, and the exporter
# reaches the Pi-holes over the network, so the node is a home, not a need.
nodeSelector:
  kubernetes.io/hostname: nuc0

tolerations: {}

affinity: {}
```

- [ ] **Step 2: Render the chart and check both keys**

Run:

```bash
helm template t cluster/helm/monitoring/pihole-exporter -f cluster/helm/monitoring/pihole-exporter/values.yaml \
  | grep -A2 'nodeSelector' ; \
helm template t cluster/helm/monitoring/pihole-exporter -f cluster/helm/monitoring/pihole-exporter/values.yaml | grep -c 'node-type'
```

Expected: `nodeSelector:` then `kubernetes.io/hostname: nuc0`; the second command prints `0`.

- [ ] **Step 3: Lint the chart and sweep the repo**

Run:

```bash
helm lint cluster/helm/monitoring/pihole-exporter
grep -rn 'node-type' cluster/helm/*/*/templates cluster/helm/*/templates 2>/dev/null || echo "no template renders node-type"
```

Expected: lint prints `1 chart(s) linted, 0 chart(s) failed`; the grep prints the fallback line (comments in `values.yaml` are allowed and stay).

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/monitoring/pihole-exporter/values.yaml
git commit -m "🐛 fix(pihole-exporter): pin to nuc0 by hostname, not the retired node-type label"
```

### Task 2: Correct the pages and the chart notes

**Files:**
- Modify: `docs/reference/chart-notes.md:311` (koillection), `docs/reference/chart-notes.md:534` (syncthing)
- Modify: `docs/how-it-works/nodes.md:48-52`
- Modify: `docs/how-it-works/applications.md:112-123`
- Modify: `cluster/helm/syncthing/README.md:25`, `cluster/helm/syncthing/values.yaml:139-141`
- Modify: `cluster/helm/koillection/README.md:22`
- Modify: `cluster/helm/media-stack/maintainerr/README.md:139-141`

**Interfaces:**
- Consumes: Task 1's chart state — every sentence below says no chart selects the label.
- Produces: the pages Task 3's verification is judged against; they claim the label was removed on 2026-11-22.

- [ ] **Step 1: Correct the two chart-notes sentences**

`chart-notes.md`, koillection paragraph — replace:

> The pod is pinned to `nuc0` by `nodeSelector`, replacing an `affinity` on the retired
> `node-type: general` label that matched no node at all.

with:

> The pod is pinned to `nuc0` by `nodeSelector`, replacing an `affinity` on the `node-type: general`
> label. That label was still on `nuc0` at the time, so it was never what kept the pod `Pending` —
> the two claims the chart mounted and never created were. The label itself was removed from the
> nodes on 2026-11-22.

`chart-notes.md`, syncthing paragraph — replace:

> The pod is pinned to `nuc0` — where the app already ran, and where AGENTS.md places the general
> catalogue — instead of carrying the retired `node-type: general` affinity, which matched no node and
> left the replacement pod `Pending`.

with:

> The pod is pinned to `nuc0` — where the app already ran, and where AGENTS.md places the general
> catalogue — instead of carrying the `node-type: general` affinity. That label answered on `nuc0`,
> so it was not what kept the replacement pod `Pending`: the `Terminating` claim was. The label was
> removed from the nodes on 2026-11-22.

- [ ] **Step 2: Correct `nodes.md`**

Replace the paragraph beginning `Older charts are the exception:` and ending `observed on.` with:

> No chart selects `node-type` any more. The label was never in git — it existed only on the
> machines, one value per node — and it was removed from all four on 2026-11-22; the last chart that
> asked for it, `monitoring/pihole-exporter`, moved to a hostname pin the same day. A chart that still
> carried such an affinity would now fail at scheduling instead of quietly landing on `nuc0`.

- [ ] **Step 3: Correct the `applications.md` section**

Replace the whole section from `## The older charts still select the retired label` up to, but not
including, `## Where to look next` with:

```markdown
## No chart selects `node-type`

Every chart pins its pod with `nodeSelector: kubernetes.io/hostname`. `monitoring/pihole-exporter`
was the last one that rendered a required node affinity on `node-type: general`; it moved to a
hostname pin on 2026-11-22, the day the label was removed from the four nodes. `syncthing` and
`thelounge` were the two before it, rebuilt onto hostname pins on 2026-11-19; `koillection`, `mktxp`
and `news-freshrss` keep the old text in comments only and pin by hostname. `AGENTS.md` §4 keeps the
rule: a new chart asks for a hostname, never a label.

The Node column is where each pod was observed, not what the chart asks for. None of the rows above
depends on the removed label.
```

- [ ] **Step 4: Correct the three chart files and the maintainerr sentence**

`cluster/helm/syncthing/README.md`, the `Pod pinned to nuc0` row — replace `The old `node-type:
general` affinity is retired and matched no node, which is why a replacement pod sat `Pending`` with
`The old `node-type: general` affinity was replaced by this pin; the label answered on `nuc0`, so the
`Pending` pod was the `Terminating` claim, not placement`.

`cluster/helm/syncthing/values.yaml` — replace the comment lines

```yaml
  # The old `node-type: general` affinity is retired and matched no node, which
  # left the replacement pod Pending.
```

with

```yaml
  # The old `node-type: general` affinity is replaced by this pin. The label
  # answered on nuc0, so the Pending pod was the Terminating claim, not placement.
```

`cluster/helm/koillection/README.md`, the affinity row — replace `The label answered on `nuc0` but
is not the placement rule; it was removed on 2026-11-22. The old affinity was not what kept the pod
`Pending` — the missing claims were.` with `The label answered on `nuc0` and was not the placement
rule; it was removed on 2026-11-22. It was not what kept the pod `Pending` — the missing claims were.`
(keep the cell as one line, pipe-separated).

`cluster/helm/media-stack/maintainerr/README.md` — replace:

> The old chart mounted `maintainerr-config`, which it never created — that missing object, together
> with a retired `node-type` affinity, kept the pod `Pending` for 76 days.

with:

> The old chart mounted `maintainerr-config`, which it never created, and that missing object kept the
> pod `Pending` for 76 days. Its `node-type: general` affinity was not the cause: `nuc0` carried the
> label, and the label was not removed until 2026-11-22.

- [ ] **Step 5: Verify none of the false claims survive**

Run:

```bash
grep -rn 'matched no node' docs/ cluster/ || echo "no false claim left"
grep -rn 'node-type' docs/ cluster/helm/ | grep -v 'superpowers/'
```

Expected: the first prints the fallback. The second lists only sentences that say the label existed,
was removed, or is not the placement rule — read every hit; no hit may say the label was absent while
these pages describe a pod that ran.

- [ ] **Step 6: Commit**

```bash
git add docs/how-it-works/nodes.md docs/how-it-works/applications.md docs/reference/chart-notes.md \
  cluster/helm/syncthing/README.md cluster/helm/syncthing/values.yaml \
  cluster/helm/koillection/README.md cluster/helm/media-stack/maintainerr/README.md
git commit -m "📝 docs: say what the node-type label really did, and that it was removed on 2026-11-22"
```

### Task 3: Remove the label from the four nodes (after the merge)

**Files:** none. This runs against the cluster; the record is the card comment.

**Interfaces:**
- Consumes: Task 1's rendered `nodeSelector` on the live pod, and Task 2's pages that name the date.
- Produces: `kubectl get nodes -L node-type` with no `node-type` column; the card is moved to `Validation`.

- [ ] **Step 1: Confirm the exporter resynced before touching the nodes**

Run:

```bash
kubectl -n monitoring get pod -l app.kubernetes.io/name=pihole-exporter \
  -o jsonpath='{.items[0].spec.nodeSelector}{"\n"}{.items[0].spec.affinity}{"\n"}{.items[0].status.nodeName}{"\n"}'
```

Expected: `{"kubernetes.io/hostname":"nuc0"}`, then an empty line (no affinity), then `nuc0`.
If the selector is still absent, the Application has not synced: stop, report, and wait — do not
remove the label.

- [ ] **Step 2: Remove the label from each node**

```bash
for n in nas nuc0 rbp2 streaming; do kubectl label node "$n" node-type-; done
```

Expected: four lines, `node/nas unlabeled`, `node/nuc0 unlabeled`, `node/rbp2 unlabeled`,
`node/streaming unlabeled`.

- [ ] **Step 3: Verify the label is gone and nothing went Pending**

```bash
kubectl get nodes -L node-type
kubectl get pods -A --field-selector status.phase=Pending
kubectl -n monitoring get pod -l app.kubernetes.io/name=pihole-exporter
```

Expected: the first has no `NODE-TYPE` column; the second lists nothing; the third shows
`Running 1/1` on `nuc0`.

- [ ] **Step 4: Record it on the card and move it to `Validation`**

Comment on 638 with the four commands, their output, the PR link and the branch, and the note that
the label was never in git so this step has no commit. Then move the card to `Validation` (bucket 14,
view 13, project 2).

## Self-Review

- Spec coverage: chart (Task 1), pages and chart notes (Task 2), cluster removal with its ordering
  constraint (Task 3), no-git record (Task 3 Step 4). The spec's non-goals — chart rebuild,
  label reconciler, `docs/charts/README.md:231`, `AGENTS.md` §4 — are untouched by every task.
- Step scan: each step names the file, the exact replacement text or the exact command, and the
  output that means it passed.
- Type consistency: the key read in Task 3 (`spec.nodeSelector['kubernetes.io/hostname']`) is the key
  Task 1 renders; the date `2026-11-22` is the same in every sentence.
- Review Focus: each of the five lines has a verification step above (Task 1 Step 3, Task 2 Step 5,
  Task 3 Steps 1 and 3, Task 3 Step 4).
- Proportion: three tasks, about one screen of prose plus the exact replacement copy.
