# autobrr

autobrr watches IRC announce channels and sends matching releases to your download client. It is the
reference chart for every hand-maintained chart in this repo — see
[`docs/charts/README.md`](../../../../docs/charts/README.md) for the contract it satisfies.

## Upstream

- Website: https://autobrr.com/
- Docs: https://autobrr.com/introduction
- Source: https://github.com/autobrr/autobrr

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from
`cluster/argocd/projects/media-stack/autobrr.yaml`, using the chart in this directory and
`values.yaml` next to it. The release name is the Application name, `autobrr`, so every object lands
on the `autobrr` / `autobrr-config` / `autobrr-env` names.

```bash
# what the cluster will render, exactly
helm template autobrr cluster/helm/media-stack/autobrr --namespace media-stack
```

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. Tag is always pinned. | `ghcr.io/autobrr/autobrr`, `v1.85.0` |
| `strategy` | Deployment strategy. Must stay `Recreate` with a config volume. | `Recreate` |
| `service.port` | Port the Service exposes, and what the IngressRoute points at. | `7474` |
| `container.port` | Only set when the app listens on a different port than the Service. | `null` (falls back to `service.port`) |
| `ingressRoute.*` | Traefik CRD routing: host, entry points, middlewares, TLS. | `autobrr.kub.local`, `web`, no TLS |
| `storage.*` | The config claim: class, size, mount path, or reuse an existing claim. | `local-path`, `128Mi`, `/config` |
| `env` | Non-secret variables, rendered **inline** into the container. Changing one rolls the pod. | `TZ`, `AUTOBRR__HOST`, `AUTOBRR__PORT` |
| `extraEnv` | Variables sourced from a Secret, as `{ name, valueFrom }`. | `[]` |
| `nodeSelector` | Node pin. Every workload is pinned. | `kubernetes.io/hostname: streaming` |

`probs`, `resources`, `podSecurityContext` and `securityContext` are present but deliberately
unwired in this pass: the chart renders no probes and sets no default limits. Wiring them is a
separate change, tracked in the design.

## Ingress

Always a Traefik `IngressRoute`, never `networking.k8s.io/v1` Ingress. The API group is a value
(`ingressRoute.apiVersion`) so the Traefik v3 migration becomes a values edit rather than a template
edit. It must stay `traefik.containo.us/v1alpha1` while the cluster runs Traefik 2.x.

`kub.local` hosts use the `web` entry point and no TLS; they resolve through the Pi-hole wildcard
record. Hosts under `kub.home.ashudev.com` use `websecure` with `certResolver: myresolver`.

## Using this chart as a template

Copy the directory, then change only:

1. `Chart.yaml` — `name`, `description`, `home`, `sources`, `keywords`.
2. `values.yaml` — `image`, `service.port`, `container.port` if needed, `ingressRoute.host`,
   `nodeSelector`, `env`.
3. Nothing in `templates/`. The helpers are unprefixed (`app.fullname`, `app.labels`,
   `app.selectorLabels`) precisely so a copy works without a rename sweep.

The trade-off of unprefixed helpers: do not use two of these charts as subcharts of the same parent.
This repo never does.

## Files

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment, including the env variables inline |
| `templates/service.yaml` | the ClusterIP Service |
| `templates/serviceaccount.yaml` | the ServiceAccount |
| `templates/persistentvolumeclaim.yaml` | the config claim, only when `storage.enabled` and no `existingClaim` |
| `templates/ingressroute.yaml` | the Traefik IngressRoute |
| `templates/NOTES.txt` | the install notes |

There is deliberately no ConfigMap: this app takes its configuration from
environment variables only. A chart for an app that reads a `config.yaml` adds
its own `templates/configmap.yaml` and mounts it — see the rule in
[`docs/charts/README.md`](../../../../docs/charts/README.md).

## Uninstalling

The config claim is created by this chart, so deleting the release deletes the claim. Take a copy
first if it holds anything you want.
