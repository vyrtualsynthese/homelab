# Argo CD under GitOps — design

Card 344 · 2026-11-19 · branch `feature/argocd-self-managed`

## Why

`cluster/helm/argocd/values.yaml` carries the configuration of the cluster's own GitOps controller:
domain, `server.insecure`, the Dex-to-authentik block, RBAC and the two Traefik routes. Nothing
applies it. The only Application is `argocd-root`, and it points at `cluster/argocd/`, the folder of
Applications, not at this chart. The running controller is Helm release `argocd`, chart
`argo-cd-10.4.1`, image `v3.5.2`, installed by hand.

Two consequences. Renovate keeps bumping an image tag that reaches nothing — PR #966
(`v3.5.2` → `v3.6.1`, open since 2026-11-18). And the file reads as the live configuration, so a
person or an agent believes the domain, the OIDC block and the routes are applied.

On 2026-11-19 the operator chose the self-managed route, option 1 of the card: a wrapper chart plus
an Application, pattern D.

## What success looks like

- A chart bump merged to `develop` reaches the running controller through a sync, with no `helm`
  command in the path.
- `helm template` over `cluster/helm/argocd` renders what runs, so the file is the truth.
- The version Renovate proposes is the version that deploys.
- A rebuild from nothing is written down and does not depend on anyone's memory.

## Goals

- Own Argo CD the same way it owns everything else: wrapper chart, Application, sync.
- Make the image version come from the chart dependency, so one lever moves everything.
- Write the recovery procedure, because the controller cannot roll back the change that broke it.

## Non-goals

- Upgrading to `v3.6.1` (chart `10.15.0` or later) in the same change.
- Changing routing, OIDC, RBAC, or the image repositories.
- Converting the remaining values-only charts (pattern B) in this pass.
- High availability for Argo CD.

## Anti-goals — failure that still looks like success

- **Values left at the top level.** The wrapper chart passes values to its dependency under the
  dependency's name. Top-level keys are ignored silently, the subchart renders its defaults, and the
  sync then re-enables the `admin` account, drops the Dex block and removes the Traefik routes. The
  render diff before the first sync is the gate that catches this.
- `syncPolicy.automated` written into the Application file (AGENTS.md §7.1: the root app sync rewrites
  the Application from git and turns automation off).
- `crds.install: false`. The chart carries the Argo CD CRDs in `templates/`; leaving them to a hand
  install means a chart bump cannot move them.
- A hand `helm upgrade argocd` after adoption: two owners, one object set.

## Constraints

- CRLF on every file under `cluster/argocd/**`; the chart files stay LF.
- Auto-sync lives outside git, enabled per Application in the UI.
- The agent has read-only cluster access. The first sync, the release-secret deletion and the
  auto-sync toggle are the operator's, in the UI or with `kubectl`.
- `argocd-secret` (Dex client secret, server keys) stays in the cluster and in git never. A sync that
  does not delete the Secret keeps its values, so adoption does not disturb sign-in.

## Approaches considered

1. **Self-managed, pattern D** — dependency on `argo-cd` pinned to an exact version, an Application
   beside the others, values unchanged but nested. Renovate gains a lever that works, the file is the
   truth. Costs an adoption of the live release and hands the CRDs to Argo CD. **Chosen.**
2. **Hand install stays** — delete the directory, write a runbook with the chart version and values,
   close #966. Zero risk, honest, but the controller and everything it operates stay in separate
   worlds, and a runbook drifts the way this file did.
3. **Warning on the file** — one paragraph saying a human applies it. Cheapest, and still leaves a
   values file that reads as applied while Renovate bumps into the void.

The operator picked 1 on 2026-11-19.

## Design

### 1. The chart

`cluster/helm/argocd/Chart.yaml`, new:

```yaml
apiVersion: v2
name: argocd
description: Argo CD - the cluster's GitOps controller
type: application
version: 0.1.0
appVersion: "3.5.2"
dependencies:
  - name: argo-cd
    version: 10.4.1
    repository: https://argoproj.github.io/argo-helm
```

`cluster/helm/argocd/README.MD` is deleted: it is a copy of the upstream chart's README and says
nothing about this install.

`values.yaml`: today's content, every key indented under `argo-cd:`. Comments move with their keys.
Nothing else changes — the domain, `server.insecure`, the `argocd-cm` block, `dex.config`, the RBAC
policy and the two `extraObjects` routes are all carried over whole. The routes' `{{ .Values.global.domain }}`
expression is rendered inside the subchart, where the global value is still in scope; the first task
confirms that with a render rather than assuming it.

`.gitignore` gains `cluster/helm/argocd/charts/` and `cluster/helm/argocd/Chart.lock`, as the other
wrapper charts do; Argo CD runs `helm dependency build` at sync time.

**Image pins.** `global.image.repository` stays `quay.io/argoproj/argocd`. `global.image.tag` is
removed: the chart's `appVersion` supplies it, so the chart version and the image version cannot
disagree, and Renovate has one lever instead of two. The containers the argocd image does not cover
(dex, redis and anything else the render shows) keep their repository and tag pinned explicitly.
This is the one place the values depart from pattern D's "pin every tag", and the reason is written
as a comment: for a controller that upgrades itself, a tag authored apart from its chart is the exact
defect this card is about. The rejected alternative — pin the tag, group the chart and image bumps in
a Renovate rule — leaves the two versions separately movable.

### 2. The Application

`cluster/argocd/projects/platform/argocd.yaml`, CRLF, shaped like its neighbours (`traefik-gate`,
`docs-site`): name `argocd`, namespace `argocd`, destination namespace `argocd`,
`CreateNamespace=true`, `repoURL` the homelab repo, `targetRevision: develop`, path
`cluster/helm/argocd`. No automated sync and no prune flag in the file.

No `resources-finalizer.argocd.argoproj.io`, same as every other Application in the repo. The
finalizer would make a deleted file cascade-delete the control plane, and `argocd-root` prunes from
git: a rename or a move would take Argo CD down mid-sync. Without it, a deleted file removes the
Application record and leaves the controller running, untracked, which a git revert repairs.

### 3. Adoption, once, with the operator

1. **Render before touching anything.** `helm dependency build cluster/helm/argocd`, then
   `helm template argocd cluster/helm/argocd -n argocd` and `kubectl diff -f -` against the cluster.
   Every difference is resolved in the values before the first sync; a render that matches the live
   objects makes that sync a no-op for the pods.
2. Merge. `argocd-root` finds the Application, creates it, and the Application sits `OutOfSync` with
   nothing applied.
3. First sync from the UI, prune **off**. Argo CD applies the chart and labels the objects with
   `argocd.argoproj.io/instance`; the pods only restart if the render differed in step 1.
4. Verify through the read-only API: the server is up, both routes answer, SSO sign-in works, the
   child Applications still appear and still compare.
5. Delete the Helm release record once the sync is good: `helm -n argocd list` names the revision,
   then `kubectl -n argocd delete secret sh.helm.release.v1.argocd.v<rev>`. Resources are untouched;
   a stray `helm upgrade` now fails loudly instead of overwriting.
6. Enable auto-sync with prune for the `argocd` Application in the UI, and only after step 4 passes.

### 4. Steady state

Renovate's Helm manager reads `Chart.yaml`, so the dependency is the upgrade lever: bump, review the
render diff in the PR, merge, sync, watch the pods roll. The image follows `appVersion`, and no
separate PR can move it. Dex and redis keep their own pins and their own PRs; they are sidecars with
their own compatibility, not the controller version.

### 5. Recovery, written down

A new page, `docs/runbooks/argocd.md`, added to `mkdocs.yml`'s nav in the same commit:

- what owns what: root Application → this Application → this chart; the release secret is gone, so
  Helm is not an owner.
- rebuild when the controller is down or cannot sync: render the chart from the repo and apply it with
  `kubectl`, because the thing that would restore Argo CD is Argo CD. Then re-sync.
- the one secret a rebuild needs from outside git: `argocd-secret`'s `dex.authentik.clientSecret`,
  matching the authentik provider's client secret.
- deleting the Application file removes the record, not the workloads: the controller keeps running
  and is simply no longer tracked, and a revert restores the Application.
- the existing break-glass admin procedure, which lives today in a comment in `values.yaml`.

### 5a. The SSO runbook's Argo CD section

`docs/runbooks/authentik-apps.md` §5 says Argo CD is a hand-run release and ends with
`helm -n argocd upgrade argocd argo/argo-cd --version 10.4.1 -f cluster/helm/argocd/values.yaml`.
Once the values are nested, that command renders defaults against the live release — the anti-goal,
executed as written. The section is rewritten in the same commit: the hand command goes, the
statement about ownership changes, and the from-zero case points at the new runbook. The
`docs/audit/argocd-helm.md` finding stays as it is; audits are dated records.

### 6. PR #966 and this card

The proposal bumps the image alone, which is the defect. It closes as superseded: `v3.6.1` arrives
with an `argo-cd` chart at `10.15.0` or later, and that upgrade is its own change with its own render
diff, after adoption. Card 344 closes when the self-management is live and the upgrade path is
written down.

## Calls I made

- Values nested under `argo-cd:`; the top-level alternative renders defaults.
- `global.image.tag` dropped, chart `appVersion` is the version.
- Adoption stays on `10.4.1`, so the first sync changes ownership and nothing else.
- The Application lives in `projects/platform/`, next to `traefik-gate` and `docs-site`.
- Auto-sync plus prune, enabled by the operator after a clean first sync.
- No resources finalizer on the Application: a file deletion removes the record, not the cluster.
- `docs/runbooks/authentik-apps.md` §5 is corrected in the same change; its hand command becomes
  destructive once the values are nested.
- Placement, resources, probes, routing and RBAC untouched.

## Left to the builder

- The exact list of containers the chart renders and therefore the exact pins (read from the render,
  not from this document).
- Whether the nested `extraObjects` still renders the domain expression, and whether anything else
  in the chart resolves values at the parent scope.
- The runbook's rebuild commands, tested by rendering, not by applying.
- Renovate's view: confirm the Helm manager picks up the dependency and opens nothing against the
  deleted tag.
