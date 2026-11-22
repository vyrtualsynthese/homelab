# GPU allocatable alert — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `nvidia.com/gpu` ─ "present but allocatable 0" state visible within ten minutes, and make the documented recovery findable from the alert and both runbooks.

**Architecture:** One Grafana-managed alert rule created over the Grafana API (no rule lives in git in this cluster), plus edits to two runbooks, the alert reference and the changelog. No cluster workload, chart or host change.

**Tech Stack:** Grafana unified alerting (v13.3.1, rules in its database), PromQL against Prometheus 3.16.0, MkDocs pages under `docs/`.

**Spec:** `docs/superpowers/specs/2026-11-22-gpu-validator-markers-design.md`

## Global Constraints

- Rule name `GpuNotAllocatable`, group `homelab-core`, `for: 10m`, severity `warning`.
- Expression `1 - kube_node_status_allocatable{resource="nvidia_com_gpu"}`; condition is "above 0" on the last value of `A`.
- `noDataState: OK`, `execErrState: OK`.
- The rule is never committed as an object; the repository records it in prose only, like the other 42.
- `curl` needs `--resolve grafana.kub.local:443:192.168.1.46`; plain name resolution fails in this shell.
- Credentials come from `~/.config/secrets/homelab.env` (`GRAFANA_TOKEN`).
- No host change, no chart change, no kube-state-metrics label allowlist, no watchdog (spec, Non-goals).

## Review Focus

- **A healthy node must not alert.** The expression reads 0 when allocatable is 1; a rule whose condition is "below 1" or an expression left as `== 0` would be silently dead or permanently firing. The eval in Task 1 pins it.
- **A node without an NVIDIA card must not alert.** `nas` and `nuc0` carry `gpu_intel_com_i915`, and `rbp2` carries neither; `1 - nothing` must yield no series. Task 1's eval and Task 4's prose pin it.
- **A restarting k3s must not page.** Ten minutes of `for` covers the operator settling, which took about 90 s on 2026-11-22. Task 1's read-back pins the duration.
- **The message must name the command.** A reader woken by it should not have to open a runbook to act; the description carries the pod-delete command and the runbook link. Task 1 pins the annotation text.
- **The number in `alerts.md` must match the live count.** The document states 42 and 19/23; the rule makes it 43 and 19/24. Task 4 pins it and its read-back compares against the API.

---

### Task 1: Create and verify the `GpuNotAllocatable` rule

**Files:**
- None in the repository (the rule lives in Grafana's database)

**Interfaces:**
- Consumes: `$GRAFANA_TOKEN`;
  `https://grafana.kub.local/api/v1/provisioning/alert-rules` (GET, POST, GET by uid);
  `https://grafana.kub.local/api/v1/eval` (POST).
- Produces: rule uid, folder `homelab`, group `homelab-core`, title `GpuNotAllocatable`.

- [x] **Step 1: Capture the baseline**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -sk -m 15 --resolve grafana.kub.local:443:192.168.1.46 \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/alert-rules \
  | python3 -c 'import json,sys,collections; r=json.load(sys.stdin); print(len(r), collections.Counter(x["ruleGroup"] for x in r))'
```

Expected: `42 Counter({'homelab-core': 23, 'homelab-core-fast': 19})`.

- [x] **Step 2: Confirm the query shape against Prometheus**

```bash
curl -sG -m 10 --resolve prometheus.kub.local:80:192.168.1.46 \
  http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=1 - kube_node_status_allocatable{resource="nvidia_com_gpu"}'
```

Expected: one series, `node="streaming"`, value `0`. If it returns `1`, stop: the GPU has dropped
again and the card's state is live — run the workaround, then continue.

- [x] **Step 3: Create the rule**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
cat > /tmp/opencode/gpu-rule.json <<'JSON'
{
  "folderUID": "homelab",
  "ruleGroup": "homelab-core",
  "title": "GpuNotAllocatable",
  "condition": "C",
  "data": [
    {
      "refId": "A",
      "queryType": "",
      "relativeTimeRange": {"from": 600, "to": 0},
      "datasourceUid": "PBFA97CFB590B2093",
      "model": {
        "editorMode": "code",
        "expr": "1 - kube_node_status_allocatable{resource=\"nvidia_com_gpu\"}",
        "instant": true,
        "intervalMs": 1000,
        "maxDataPoints": 43200,
        "range": false,
        "refId": "A"
      }
    },
    {
      "refId": "C",
      "queryType": "",
      "relativeTimeRange": {"from": 0, "to": 0},
      "datasourceUid": "__expr__",
      "model": {
        "conditions": [
          {
            "evaluator": {"params": [0], "type": "gt"},
            "operator": {"type": "and"},
            "query": {"params": ["A"]},
            "reducer": {"type": "last"},
            "type": "query"
          }
        ],
        "datasource": {"type": "__expr__", "uid": "__expr__"},
        "expression": "A",
        "intervalMs": 1000,
        "maxDataPoints": 43200,
        "refId": "C",
        "type": "threshold"
      }
    }
  ],
  "noDataState": "OK",
  "execErrState": "OK",
  "for": "10m",
  "annotations": {
    "summary": "{{ $labels.node }} advertises zero nvidia.com/gpu",
    "description": "The card still works for containers through the nvidia runtime class, so Plex and nvidia-smi are unaffected; only the scheduler resource is gone, and the next pod that asks for a GPU stays Pending. A restart cleared the validator's markers in /run/nvidia/validations. Run kubectl -n gpu-operator delete pod -l app=nvidia-operator-validator; capacity returns in about 90 seconds. Cause and checks: docs/runbooks/gpu-operator.md."
  },
  "labels": {"severity": "warning"}
}
JSON
curl -sk -m 15 --resolve grafana.kub.local:443:192.168.1.46 \
  -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  -d @/tmp/opencode/gpu-rule.json \
  https://grafana.kub.local/api/v1/provisioning/alert-rules
```

Expected: HTTP 200/201 and a body whose `uid` is new and `title` is `GpuNotAllocatable`. Note the uid.

- [x] **Step 4: Read the rule back**

```bash
UID=$(curl -sk --resolve grafana.kub.local:443:192.168.1.46 \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/alert-rules \
  | python3 -c 'import json,sys; print([x["uid"] for x in json.load(sys.stdin) if x["title"]=="GpuNotAllocatable"][0])')
curl -sk --resolve grafana.kub.local:443:192.168.1.46 \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/v1/provisioning/alert-rules/$UID" \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["title"], r["ruleGroup"], r["for"], r["labels"], r["noDataState"], r["execErrState"]); print(r["data"][0]["model"]["expr"]); print(r["data"][1]["model"]["conditions"][0]["evaluator"])'
```

Expected: `GpuNotAllocatable homelab-core 10m {'severity': 'warning'} OK OK`, the expression from
Step 3, and evaluator `{'params': [0], 'type': 'gt'}`.

- [x] **Step 5: Prove the condition pipeline evaluates**

```bash
python3 - <<'PY'
import json
r = json.load(open("/tmp/opencode/gpu-rule.json"))
json.dump({"condition": r["condition"], "data": r["data"]}, open("/tmp/opencode/gpu-eval.json", "w"))
PY
curl -sk -m 30 --resolve grafana.kub.local:443:192.168.1.46 \
  -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  -d @/tmp/opencode/gpu-eval.json https://grafana.kub.local/api/v1/eval
```

Expected: HTTP 200 with frames from `A` and `C`; no error, and the `C` frame shows a value of `0`
(the threshold is not met, which is the healthy result). A 400 with an expression error means the
rule would never evaluate; fix it before leaving the task.

- [x] **Step 6: Commit**

Nothing to commit in this task — the rule exists only in Grafana. Record the uid in the card comment
written in Task 4.

---

### Task 2: Record the fault and the accepted workaround in the GPU runbook

**Files:**
- Modify: `docs/runbooks/gpu-operator.md`
  - the ledger at the top (line 8, `Verified`)
  - a new section after `## What a node reboot does to these pods` (before `## Related`)
  - the `## Checking the GPU` command block, to name the allocatable check first

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: the section title `## The GPU drops to 0 after a restart`, which Task 1's alert
  description and Task 3 both point at.

- [x] **Step 1: Correct the reboot paragraph**

In `## What a node reboot does to these pods`, the paragraph ending "each time every pod in
`gpu-operator` came back at that instant without intervention" must stop implying that is enough.
Add, after it:

> A pod coming back is not the GPU coming back. On 2026-11-22 the pods were all `Running` and the
> node still advertised `nvidia.com/gpu: 0`; the section below is why.

- [x] **Step 2: Add the section**

Insert before `## Related`, with this content:

```markdown
## The GPU drops to 0 after a restart

**The fault.** A k3s restart on `streaming` leaves the node advertising `nvidia.com/gpu: 0` while
every pod in `gpu-operator` reads `Running`. The validator writes its readiness markers to
`/run/nvidia/validations`, which is a tmpfs: the restart clears it. The validator pod is not
recreated by a restart, so its init containers — the steps that write the markers — do not run
again, and the device plugin's init container waits forever for `toolkit-ready` instead of failing.

Nothing else notices. Plex reaches the card through `runtimeClassName: nvidia`, so `nvidia-smi -L`
still lists the Quadro P400 and playback works. Only a pod that **asks the scheduler** for
`nvidia.com/gpu` is affected, and it stays `Pending`.

**Detect it.** `GpuNotAllocatable` (Grafana, `homelab-core`, 10 minutes) fires when a node that has
registered the card reports zero allocatable. The command below is also the first line of the K3s
upgrade's `streaming` check:

```bash
kubectl get node streaming -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'   # expect 1
```

**Recover it.** Delete the validator pod; the operator recreates it, its init containers run again,
and the resource returns in about 90 seconds (measured 2026-11-22):

```bash
kubectl -n gpu-operator delete pod -l app=nvidia-operator-validator
kubectl get node streaming -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'   # 1 again
```

**Why this is accepted rather than fixed.** The marker path is hardcoded on both sides: the
validator's `/run/nvidia/validations` is a constant, and the device plugin's gate names the same
path. The validator reads an `OUTPUT_DIR` override, but moving only the writer would strand the
reader, so it is unusable. There is no chart setting for either side. Keeping the markers across a
restart would need a host bind mount onto persistent storage, which is boot-order configuration on
one machine and outside git, next to the hand-installed driver. The alert is what closes the gap: the
state cannot persist unnoticed for more than ten minutes.
```

- [x] **Step 3: Update the ledger**

Change line 8 to read `**Verified** 2026-11-22 · **Kind:** living page`, and add to the "In one line"
paragraph that the restart failure is one of the cases the page covers.

- [x] **Step 4: Check the links and the page**

```bash
python3 hack/check_docs_links.py
```

Expected: `0 broken relative links` and exit 0.

- [x] **Step 5: Commit**

```bash
git add docs/runbooks/gpu-operator.md
git commit -m "📝 docs(gpu): record the allocatable-to-zero restart and its workaround"
```

---

### Task 3: Make the K3s upgrade check catch it

**Files:**
- Modify: `docs/runbooks/k3s-upgrade.md` (the `## Verify, then point the plan at the next node` block,
  and the `**GPU survived.**` paragraph in `## What actually happened on 2026-11-21/22`)

**Interfaces:**
- Consumes: the section from Task 2 (`docs/runbooks/gpu-operator.md`, `## The GPU drops to 0 after a restart`).
- Produces: nothing other tasks consume.

- [x] **Step 1: Add the check to the verify block**

After the `curl` line in that block, add:

```bash
# on the node that carries the GPU, the scheduler resource, not just nvidia-smi:
kubectl get node streaming -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'   # expect 1
```

and after the "Expected:" sentence, add:

> On `streaming`, the allocatable line is the one that catches the 2026-11-22 fault: every GPU pod
> was `Running` and `nvidia-smi` still worked, while the scheduler resource read 0. The recovery is
> one pod delete and takes about 90 seconds — `runbooks/gpu-operator.md`, "The GPU drops to 0 after a
> restart".

- [x] **Step 2: Correct the record**

Replace the `**GPU survived.**` paragraph with:

> **GPU: the pods survived, the scheduler resource did not.** `nvidia-container-toolkit`, the device
> plugin, `dcgm-exporter` and `intel-gpu-plugin` all came back, and `nvidia-smi -L` in the Plex pod
> still listed the Quadro P400 — but the node advertised `nvidia.com/gpu: 0` for about forty minutes,
> and only the allocatable check noticed. Deleting the validator pod restored it in 90 seconds; cause
> and recovery are in `docs/runbooks/gpu-operator.md`, "The GPU drops to 0 after a restart". The
> first version of this paragraph said the GPU "survived" on the strength of the pod list alone.

- [x] **Step 3: Check the links**

```bash
python3 hack/check_docs_links.py
```

Expected: `0 broken relative links`.

- [x] **Step 4: Commit**

```bash
git add docs/runbooks/k3s-upgrade.md
git commit -m "📝 docs(k3s): check the GPU scheduler resource, not just nvidia-smi"
```

---

### Task 4: Alert reference and changelog

**Files:**
- Modify: `docs/reference/alerts.md` (counts at lines 17-18, the rule table, and a paragraph on the
  rule's shape after the `GpuTemperatureHigh` reasoning)
- Modify: `CHANGELOG.md` (`## [Unreleased]` → `### Added`)

**Interfaces:**
- Consumes: the rule from Task 1 (title, group, for, severity, expression).
- Produces: nothing other tasks consume.

- [x] **Step 1: Update the counts**

`**homelab-core-fast** at **60 s** (19 rules) and **homelab-core** at **300 s** (23 rules).
**42 rules**` becomes `(24 rules)` and `**43 rules**`. Nothing else in that paragraph changes: a
10-minute `for` keeps the rule in `homelab-core` by the existing split rule.

- [x] **Step 2: Add the table row**

After the `GpuTemperatureHigh` row (line 60):

```
| `GpuNotAllocatable` | allocatable `nvidia.com/gpu` reads 0 on a node that registered the card | 10m | warning | inactive |
```

- [x] **Step 3: Explain the shape**

After the `GpuTemperatureHigh` material, add a short paragraph:

> **`GpuNotAllocatable` watches the scheduler resource, not the card.** The card stays usable through
> the `nvidia` runtime class after a restart, so `GpuTemperatureHigh` and Plex are unaffected and
> `nvidia-smi` still lists it; only `nvidia.com/gpu` on the node drops to 0, and a pod that requests
> it stays `Pending`. The expression is `1 - kube_node_status_allocatable{resource="nvidia_com_gpu"}`
> — kube-state-metrics rewrites the dots in the resource name to underscores, the series exists only
> on a node whose device plugin registered the card, and the `nvidia.com/gpu.present` node label is
> not available: kube-state-metrics adds node labels only for names in its
> `--metric-labels-allowlist`, and none is configured (adding one is a non-goal). The rule is what
> makes the accepted workaround safe:
> `docs/runbooks/gpu-operator.md`, "The GPU drops to 0 after a restart".

- [x] **Step 4: Add the changelog entry**

Under `## [Unreleased]` → `### Added`, as the first bullet:

```markdown
- `GpuNotAllocatable`: a warning in `homelab-core` when a node that has registered the NVIDIA card
  advertises zero allocatable `nvidia.com/gpu`. A k3s restart on `streaming` clears the GPU
  operator's validator markers in `/run/nvidia/validations` and does not re-run the init containers
  that write them, so the scheduler resource silently goes to 0 while every GPU pod stays `Running`
  and Plex keeps transcoding. The workaround — delete the validator pod, about 90 seconds — is now
  recorded in `docs/runbooks/gpu-operator.md`, and the K3s upgrade's `streaming` check reads the
  allocatable value instead of trusting `nvidia-smi`.
```

- [x] **Step 5: Verify the counts against the live API**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -sk --resolve grafana.kub.local:443:192.168.1.46 \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/alert-rules \
  | python3 -c 'import json,sys,collections; r=json.load(sys.stdin); print(len(r), collections.Counter(x["ruleGroup"] for x in r))'
```

Expected: `43 Counter({'homelab-core': 24, 'homelab-core-fast': 19})` — the numbers now written in
the document.

- [x] **Step 6: Run the checker suite and the link check**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
python3 hack/check_docs_links.py
```

Expected: zero failures, `0 broken relative links`.

- [x] **Step 7: Commit**

```bash
git add docs/reference/alerts.md CHANGELOG.md
git commit -m "📝 docs(alerts): record GpuNotAllocatable and the forty-third rule"
```

---

### Task 5: Card and handover

**Files:**
- None in the repository.

**Interfaces:**
- Consumes: the branch `feature/gpu-validator-markers`, the rule uid from Task 1, the commits from
  Tasks 2-4.
- Produces: card homelab #575 in `Validation` with the branch, PR and verification in its
  description.

- [x] **Step 1: Push and open the pull request**

```bash
git fetch origin && git rebase origin/develop
git push -u origin feature/gpu-validator-markers
```

Open the PR against `develop` with `github_create_pull_request`, body naming the fault, the decision
(B) and the five commits.

- [x] **Step 2: Comment on the card and move it**

Post one comment containing: the rule uid, the read-back line, the eval result, the two doc paths and
the PR link. Move the card to `Validation` (it is a `task`).

- [x] **Step 3: Retire the design and plan**

Leave both under `docs/superpowers/` as committed; they are the record and stay out of the nav.
