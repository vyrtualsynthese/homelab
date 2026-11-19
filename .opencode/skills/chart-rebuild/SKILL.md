---
name: chart-rebuild
description: Use when rebuilding or aligning a hand-maintained Helm chart in the homelab repo — an app stuck Pending on a claim its chart never creates, wrong Service port, missing probes or memory limits, boilerplate Chart.yaml, or a chart that predates the contract. Covers cluster/helm charts that are not vendored, media-stack included.
---

# Chart rebuild

A chart rebuild is not hard, it is long: ten files against a written contract.
One agent pass is about 30 tool calls, and a rebuild does not fit in one. The
usual failure is not a wrong decision — it is that the research never stops and
**nothing is written**. Two baseline runs on this repo both ended that way.

**The rule: three passes, each ending in files on disk and a render. Research is
scoped to the pass, and anything not needed yet is deferred, not chased.**

The standard is `docs/charts/README.md`. Read it once, at the start of the first
pass, and do not re-derive it here. `cluster/helm/media-stack/autobrr` is the
reference shape; the newest sibling in the same area is the closest precedent.

## Before the first pass: is this already done?

The repo and the cluster move under you — parallel sessions, other branches,
`develop` ahead of your worktree. Thirty seconds here saves a full pass:

```bash
git fetch origin
git log --oneline origin/develop -- cluster/helm/<area>/<app>
```

If the chart was rebuilt upstream, or the live pod is already `Running` on a
new image, **stop and report**. Do not rebuild finished work. Likewise check the
live object before designing a rename: the pod is the truth about what exists.

## Scope, settled once

- **A public-facing chart keeps its routes, hosts, TLS and middleware exactly as
  they are.** The contract puts public apps out of scope because W5 and W7 change
  that wiring. Rebuilding the chart is not permission to redesign the
  certificate, add a middleware, or move a host.
- **One app per pass.** A pair is two rebuilds with the same decisions.
- **State what the app loses.** If the old claim is gone, first start is empty:
  that goes in the chart README and in the hand-off, never left to be discovered.

## Pass 1 — facts and the core

Gather only what these five files need. Stop when they render.

| Fact | Where | Why |
|---|---|---|
| Image repository and exact tag | Registry tag list, upstream releases | A tag that does not exist never starts |
| Real listening port | Image docs, or the live container port | The values file is usually wrong; Kavita carried Plex's 32400 |
| Probe endpoint and what it checks | Upstream source, or the live pod | `/` answers 200 with a broken backend; a readiness path that reads the database is not the liveness path |
| Config claim: name, class, size, owner | Live PVC → PV → hostPath | Renaming a claim that holds data is data loss |
| Measured working set | Prometheus, `container_memory_working_set_bytes` | Never ran? Say unmeasured, do not invent precision |

Write, in this order, running `helm template` after the deployment:

1. `Chart.yaml` — `version` bumped, `appVersion` equal to the image tag. Do it
   first: a stale `appVersion` makes every later render report the wrong image.
2. `values.yaml` — the facts land here.
3. `templates/_helpers.tpl` — `app.*` helpers only.
4. `templates/deployment.yaml` — env inline, probes, resources, hardening, mounts.
5. `templates/persistentvolumeclaim.yaml` — only if the chart owns its config.

The claim name must come from one value and be the name the pod already
mounts, unless that claim is gone — then say so.

## Pass 2 — exposure and metadata

No new facts. The object names come from `resources_list` on the live
Deployment, Service, IngressRoutes, Middlewares, TLSOption and PVC.

6. `templates/service.yaml` — one value drives container port, Service and every
   probe or exporter reference. Never a second copy of the number.
7. `templates/serviceaccount.yaml` — created by the chart, token not mounted.
8. Routes, middlewares, TLSOption — the existing names, as values-driven
   templates, with the TLS and middleware content unchanged. **Add the new
   file, render it, then remove the old one.** Never delete a template before
   its replacement renders: a pass that dies in between leaves a chart that
   silently deploys fewer objects than the cluster has.

Run `helm template` and compare the rendered object names with the live ones.

## Pass 3 — docs, gate, hand-off

9. `README.md` + `NOTES.txt` — what differs from the reference, the known debt
   and its reason, and the post-sync steps.
10. `docs/charts/README.md` — the app's row in the probe/hardening/UID table, and
    one paragraph in "What good looks like".

```bash
cd cluster/helm/<area>/<app>
helm lint . --namespace <ns>
helm template <release> . --namespace <ns> | kubeconform -strict -summary

grep -rnP '\t' templates/ || echo "no tabs"
grep -rn 'latest' values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io' templates/ || echo "no core Ingress"
grep -rn '<original app name>' templates/ values.yaml || echo "no copy-paste leftovers"
grep -rn 'node-type' values.yaml templates/ || echo "no retired label"
```

Then the repo-wide reference checks in `AGENTS.md` §6. If `kubeconform` is
missing, report the check as not run instead of claiming it passed.

Then say what happens after merge: who syncs, that a new claim binds only once a
pod schedules, the first-start steps done by hand (admin user, library roots,
fresh API key), what is lost, and what is debt.

## When the pass ends

You will run out of steps. That is expected, not a failure. End at a boundary:

- the tree renders — `helm template` exits 0;
- no file is half-written;
- no template was removed before its replacement rendered;
- the report names the next pass and its exact files.

A pass that ends with a rendering chart and an accurate hand-off is a success,
even if four of ten files exist. The failure to avoid is a pass that ends with
a thorough report and an untouched tree.

## Traps

| Trap | Consequence | Counter |
|---|---|---|
| Port typed twice | Probe or exporter points at nothing, silently | One value, three consumers |
| New claim with the old pod still up | Claim binds to the node the old pod made eligible; pod never schedules | Scale the old Deployment to zero before the first sync, or delete the old pod first |
| Claim renamed to the derived name | A claim holding data is abandoned | Match the live name, or state plainly that the data is gone |
| `runAsUser` on a root-written claim | App cannot read `/config` | Chown the host directory first, or start from a fresh claim |
| No log-level variable | An invented variable silently does nothing | Record the level as debt |
| Public chart in scope | TLS and auth get redesigned by accident | Preserve routes verbatim |
| Template deleted before its replacement renders | A pass that dies leaves objects missing from the release | Add first, render, then remove |

## Red flags

| Thought | Reality |
|---|---|
| "I'll finish the research first, then write" | That is the failure this skill exists to stop. Write after five facts. |
| "One more thing before I start the files" | Defer it to the next pass or to debt. |
| "I'll delete these stale templates now and write the new ones next" | That is where the pass dies. Add first, render, then remove. |
| "The values file says the port" | The values file is what is being fixed. Ask the image. |
| "The README is quick, I'll do it at the end" | It carries the debt and the first-start truth. Budget a pass for it. |
| "While I'm here I can improve the TLS" | Out of scope. W5 owns it. |
| "256Mi looks about right" | Say unmeasured. A round number presented as measured is a lie with a citation. |
| "The old claim is gone, so any name works" | Then say so, and confirm the old pod is gone before the new claim binds. |

## Quick reference

| Symptom | First move |
|---|---|
| `Pending`, "persistentvolumeclaim X not found" | The chart references a claim it does not create. Add the claim template |
| `Pending`, "didn't find available persistent volumes" | Claim welded to the wrong node: delete claim, then pod |
| `Synced` but unhealthy | ArgoCD is fine; the chart is the defect |
| Service port and app port disagree | One value, three consumers, or the bug returns |
| Exporter target down | Compare its target port with the container port before touching the key |
