# Traefik under ArgoCD — design

**Status:** draft for approval · **Date:** 2026-11-23 · **Audited:** live cluster, same day
**Board:** homelab #363 (task 466) · **Approach chosen:** A, adopt in place

## Intent

Traefik is the last hand-moved Helm release in the cluster: chart `42.4.1`, revision 12,
`kube-system`, applied from `cluster/infrastructure/traefik/values-v3.yaml` by a person running
`helm`. Every other workload is an Application, so the router is the one thing whose values and image
drift without ArgoCD noticing, and `cluster/infrastructure/` being outside GitOps is written down as
a known exception in `docs/how-it-works/gitops.md`.

The card puts it under an Application with a **manual sync only**, keeps the release name so the
Service and the certificate store keep their identity, and records how to get back out.

## What the audit found (2026-11-23)

- `helm template traefik 42.4.1 -n kube-system -f values-v3.yaml` renders seven objects. Four are
  readable with the agent's RBAC and match the live objects: `ServiceAccount/traefik` and
  `IngressRoute/traefik-dashboard` byte-identical, `Deployment/traefik` and `Service/traefik`
  differing only in fields the API server defaults (`progressDeadlineSeconds`,
  `terminationMessagePath`, `sessionAffinity`, `ipFamilies`, …). Three are not readable —
  `ClusterRole/traefik-kube-system`, `ClusterRoleBinding/traefik-kube-system`,
  `IngressClass/traefik` — because the agent's RBAC excludes them.
- The live Service carries `k8slens-edit-resource-version: v1` and orders its ports
  `mc, sttcp, studp, web, websecure, stdiscovery` where the render orders them
  `mc, sttcp, web, websecure, stdiscovery, studp`. Someone edited it in Lens; the order is drift and
  the first sync settles it.
- ArgoCD is `v3.6.2`, `application.instanceLabelKey: argocd.argoproj.io/instance`, and
  `admin.enabled: false`, so a sync needs the operator's authentik sign-in.
- Application name decides the Helm release name ArgoCD renders with. The chart builds its instance
  label as `{{ .Release.Name }}-{{ .Release.Namespace }}`, so an Application named `traefik`
  reproduces the live `traefik-kube-system` — which matters because the Deployment's
  `spec.selector` is immutable and a mismatch would fail the sync instead of adopting.
- Baseline for the proof: pod `traefik-7f8b9d6b48-79zcs`, created 2026-11-22T19:22:34Z, 0 restarts.

## Goals

1. A wrapper chart at `cluster/helm/platform/traefik/` that pins upstream chart `42.4.1` exactly and
   holds the release's only values file.
2. An Application `traefik` in `cluster/argocd/projects/platform/` that adopts the existing objects in
   place and does not sync by itself.
3. A no-op sync that changes nothing, with the evidence recorded.
4. The way out — and the `helm uninstall` trap adoption creates — written into
   `docs/runbooks/traefik-release.md`.

## Non-goals

- The CRDs. They stay outside ArgoCD.
- Auto-sync or `selfHeal` on this Application, by the operator's decision.
- Re-installing the release (approach B).
- The content of the 2.11 and 25.0.3 values files.
- `traefik-gate`'s middlewares, which already have an Application.
- Rebuilding the addon path.

## Anti-goals

- A sync that restarts the router. If the pod name changes or its restart count moves, the adoption
  was wrong, whatever the Application says.
- An `ignoreDifferences` list written from memory. Every entry has to come from an observed diff.
- A second router process. One replica, `Recreate`; two certificate owners corrupt `/data/acme.json`
  and re-request every name.

## Constraints

- `cluster/argocd/**` is CRLF, and **no** Application records `syncPolicy.automated`: a root-app sync
  rewrites the Application to match git and silently switches automation off.
- The rendered release name must equal the live one, or the immutable selector fails.
- The desired state must not carry `clusterIP`, `nodePort`, `healthCheckNodePort`, the
  `load-balancer-cleanup` finalizer, or API-defaulted fields.
- Manual sync only.
- Chart contract (`docs/charts/README.md`): `appVersion` follows the first `tag:` in `values.yaml`,
  which is `v3.7.19`, so `appVersion: "3.7.19"` — no exemption needed.
- `hack/check-docs.sh` requires the application catalogue to name exactly the Applications that
  exist, and every cited path to resolve.

## Approaches

| | A — adopt in place | B — recreate under ArgoCD |
|---|---|---|
| What happens | ArgoCD takes ownership of the objects Helm created | the Helm release is forgotten and ArgoCD creates every object |
| Service | kept: same `clusterIP`, same six `nodePort` values | recreated: new `clusterIP`, `nodePort`s reassigned unless pinned |
| Pod | kept, no restart | restarted under `Recreate` |
| Ingress | no blip; the port list reorders | a gap of a minute or two, every public name blinking |
| Provenance | two records until the Helm release secret is deleted | one record from the start |

A was chosen: the render already equals the live objects, so B buys cleaner provenance at the price
of an outage it does not need.

## The parts decided

1. **Chart.** `cluster/helm/platform/traefik/Chart.yaml`: `name: traefik`, `version: 0.1.0`,
   `appVersion: "3.7.19"`, one dependency `traefik` at `42.4.1` from
   `https://traefik.github.io/charts`. No `templates/`: the chart adds nothing to the upstream
   render, and a wrapper that adds objects stops being a wrapper. `.gitignore` gains the `charts/`
   and `Chart.lock` entries the other wrappers have.
2. **Values.** `cluster/infrastructure/traefik/values-v3.yaml` moves to the chart as `values.yaml`,
   **re-indented under a `traefik:` key**: a wrapper feeds its subchart through the dependency's
   name, so a verbatim copy would render chart defaults and the first sync would rewrite the router.
   The render diff in the verification step is what proves the indentation moved nothing. `values.yaml`
   (chart 25.0.3) is renamed `values-25.0.3.yaml` to end the clash, and `values-2.11.yaml` stays.
   There is **no flat duplicate** of the v3 values: a second copy drifts, and the wrapper chart is
   itself the input a hand-run upgrade needs (see item 7). The two older files are rollback material
   for their own chart lines.
3. **Application.** `cluster/argocd/projects/platform/traefik.yaml`, name `traefik`, destination
   `kube-system`, the one source path, `syncOptions: [CreateNamespace=true]`, no `automated`, and
   `helm.skipCrds: true` — without it ArgoCD installs the chart's `crds/` directory, which would mean
   15 licensed `hub.traefik.io` CRDs that do not exist live, a permanently non-empty diff, and the
   sync taking ownership of the ten `traefik.io` CRDs the manual step is supposed to own.
   `ignoreDifferences` only for what the first observed diff names — expected: the Deployment's
   `deployment.kubernetes.io/revision` annotation and the Service's `finalizers`. The port order is
   left for the sync to fix, not for an ignore rule.
4. **CRDs outside ArgoCD.** Chart 42 ships `traefik.io` and 15 `hub.traefik.io` CRDs; the hub ones
   need a licence and gate nothing here, and a CRD apply is not something a Helm-rendered sync does
   safely. The manual step in the runbook stays the only path, and the chart README says so.
5. **RBAC so the adoption can be checked.** `kubectl/mcp-readonly-rbac.yaml` gains read on
   `rbac.authorization.k8s.io` `clusterroles`/`clusterrolebindings` and `networking.k8s.io`
   `ingressclasses`. Without it the three cluster-scoped objects the Application manages can only be
   seen after a sync, by the operator. The operator applies the file by hand, as with every change
   to it.
6. **Sync, after the merge.** `argocd app sync traefik --server-side --force`, because Helm's field
   manager owns the fields ArgoCD is about to claim. Then `argocd app diff` empty, then a second
   `argocd app sync` that reports no changes. Delete the Helm release secret
   (`sh.helm.release.v1.traefik.v12`) once the sync is clean, so `helm list` stops offering a release
   whose uninstall would delete the router.
7. **Rollback, in `traefik-release.md`.** The card asks for a rollback to the addon and that path is
   closed: the `containo.us` CRDs were deleted on 2026-11-21 and every route is on `traefik.io`, so
   re-enabling the addon restores a 2.10 router reading an empty group. The page records what is
   true instead — orphan the Application's resources (`--cascade=orphan`), then upgrade **the wrapper
   chart** by hand (`helm dependency build` + `helm upgrade traefik cluster/helm/platform/traefik -f
   .../values.yaml --reset-values`), which is today's state — plus the trap (`helm uninstall traefik`
   deletes the router while the release record exists) and the way back in. The same page's upgrade
   recipe changes shape: the input for chart 42.4.1 is the wrapper, not a flat file next to the
   upstream tarball, and the flat `values-2.11.yaml` only pairs with its own chart line.
8. **Docs that go stale.** `docs/how-it-works/applications.md` gains the `traefik` row;
   `docs/how-it-works/gitops.md` stops listing Traefik under the hand-applied path;
   `docs/how-it-works/network.md` stops calling the dashboard route a value of "the Traefik Helm
   release"; `docs/runbooks/README.md` updates the shelf row and its status; `AGENTS.md` §2 stops
   saying the release is installed by hand; `docs/runbooks/acme-store.md`'s link to the moved values
   file is repointed.
9. **The routes page stops lying.** `docs/reference/routes.md` is generated and says "A route that no
   Application owns is not here: the Traefik dashboard is a Helm release value". Adoption makes that
   false. `hack/gen_docs.py` learns that a chart whose values carry `ingressRoute.dashboard.enabled`
   declares a route (it can already build a chart's dependencies and render it), the page gains the
   `traefik.kub.local` row, and the fixed text is rewritten. If the route parser cannot read the
   dashboard's `&&`-joined match rule, the fallback is to reword the text and leave the row out —
   the page must not claim something it does not list.
10. **The check that verifies the gate follows the file.** `hack/check-exposure.sh` hard-codes
    `cluster/infrastructure/traefik/values.yaml`; it is repointed at the chart's `values.yaml`, or the
    dashboard's `lan-only` gate stops being checked at all.
11. **Renovate keeps both pins.** The dependency version and `image.tag` stay under the repo's
    existing helmv3 and helm-values management, like every other chart. A bump merged without the
    CRD-first step cannot reach the cluster by itself, because this Application syncs by hand — that
    is the safety property the manual choice buys, and the chart README states it.

## Verification

Local, before the PR: `helm lint`, `helm template` and the render diff against the live objects,
`hack/check-charts.sh`, `hack/check-app-version.sh`, `hack/check-exposure.sh`, `hack/check-docs.sh
--base origin/develop`, `python3 hack/gen_docs.py --write --only routes` with a clean re-run, and
`pytest -q hack/tests`.

After the merge, in the operator's ArgoCD session: the Application reports `Synced`/`Healthy`; a
second sync reports no changes; the pod is still `traefik-7f8b9d6b48-79zcs` with 0 restarts, so
`acme.json` is untouched; every public name still answers; `/metrics` is still scraped (`up == 1`);
and `argocd app get traefik` shows the resources under management.

## What is left to the builder

The exact `ignoreDifferences` the first diff asks for, and whether the Helm release secret is
deleted in the same session as the sync or held until the next day.
