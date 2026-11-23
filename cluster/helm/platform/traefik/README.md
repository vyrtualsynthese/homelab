# traefik

The cluster's ingress router, as a wrapper chart.

It depends on the official Traefik chart at one exact version and adds no object of its own: every
object in the release comes from the upstream chart, rendered with `values.yaml`. The wrapper exists
so the version, the values and the image are in git and applied by ArgoCD, instead of a `helm
upgrade` typed on a laptop.

## What is fixed, and why

- **The Application is named `traefik` and the destination is `kube-system`.** The chart builds its
  instance label as `<release>-<namespace>`, which is the live `traefik-kube-system`, and the
  Deployment's `spec.selector` is immutable. A different name renders a different selector and the
  sync fails instead of adopting the running objects.
- **`values.yaml` is nested under `traefik:`.** That key is what the wrapper hands to the subchart.
  Flattening it renders the chart's defaults: a second certificate resolver, the stock entry points
  and a claim nobody asked for.
- **The sync is manual.** The Application records no `automated`, by the operator's decision: a bad
  sync here takes down every route, including the ArgoCD UI itself, so a sync is always a decision
  somebody makes.

## CRDs are not synced

The Application sets `helm.skipCrds`, so ArgoCD never renders the chart's `crds/` directory. That
keeps the fifteen `hub.traefik.io` CRDs (which need a licence and gate nothing here) out of the
cluster, and leaves the ten `traefik.io` CRDs to the manual step that already exists.

A chart bump therefore needs its CRDs applied from a pulled tarball **before** anybody syncs:

```bash
helm pull traefik/traefik --version <new version> --untar --untardir /tmp/traefik-next
for f in /tmp/traefik-next/traefik/crds/traefik.io_*.yaml; do kubectl apply -f "$f"; done
```

`docs/runbooks/traefik-release.md` has the full procedure, including the render diff that has to be
read before the sync.

## Renovate

The dependency version and the image tag are managed by Renovate like every other chart in this
repository. A bump merged without the CRD step above cannot reach the cluster on its own, because the
Application syncs only when a person syncs it — that is what the manual choice buys.
