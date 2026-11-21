# Traefik, as it ran on 2026-11-21 before the ownership cutover

A one-off record of the ingress controller's configuration at the moment it still belonged to the
K3s addon. Nothing reads this directory: it is not a Helm chart, not an ArgoCD source and not an
input to any sync. It exists so that the state before the change can be compared with the state
after, and so a rollback has the values that were actually serving traffic.

## Files

| File | What it is |
|---|---|
| `release-values.yaml` | `helm get values traefik` — the user-supplied values the release held. Directly usable as `helm -f`. |
| `release-computed-values.yaml` | `helm get values traefik --all` — every effective value, chart defaults included. |
| `release-manifest.yaml` | `helm get manifest traefik` — the seven objects the release owned: ClusterRole, ClusterRoleBinding, Deployment, IngressClass, IngressRoute, Service, ServiceAccount. |
| `release-history.txt` | `helm history traefik` — revisions, chart versions and dates. |
| `k3s-helmchart-traefik.yaml` | The K3s `HelmChart` for the router, with its finalizer and `status.jobName`. |
| `k3s-helmchart-traefik-crd.yaml` | The K3s `HelmChart` for the CRDs, which still held release `20.3.1` from 2023. |
| `k3s-helmchartconfig-traefik.yaml` | The K3s `HelmChartConfig` whose values content was the repository file `traefik-config.yaml`. |
| `crds.yaml` | All 19 Traefik `CustomResourceDefinition` objects in both API groups, as the API server held them. |
| `objects.yaml` | Every live route, middleware, TLSOption, TLSStore, TCP/UDP route, MiddlewareTCP, ServersTransport and TraefikService, both API groups: 19 `List` objects holding 115 objects. Applyable with `kubectl apply -f`. |
| `workload.yaml` | The live `Deployment`, `Service`, `IngressClass`, `PersistentVolumeClaim` and `PersistentVolume`. |
| `acme.txt` | The certificate store's sha256, size, owner, mode and certificate count. |
| `README.md` | This file. |

## What is deliberately absent

- **`acme.json`** — it holds the TLS private keys. It lives on the Retain NFS volume
  `pv-traefik-nfs` (`nas:/mnt/storage/traefik`), which is already a row of the backup matrix in
  `cluster/helm/backup/values.yaml`. `acme.txt` carries its digest instead: that digest is the proof
  that the cutover did not re-issue a certificate.
- **Secrets and kubeconfigs** — the router references Secrets by name, and the names are in the
  objects here; no value is.

## What the numbers say

At the time of the capture:

- one `Deployment` pod, `traefik-68554c769b-xgt8t`, on `streaming`, image
  `rancher/mirrored-library-traefik:2.10.7`, strategy `Recreate`;
- release `traefik`, revision 7, chart `25.0.3+up25.0.0`, status `deployed`;
- 68 `IngressRoute`, 27 `Middleware`, 14 `TLSOption` and 1 `TLSStore` on `traefik.containo.us`, plus
  1 `IngressRoute` on `traefik.io` — the dashboard route the chart now renders;
- `/data/acme.json` at
  `f074495fb3b8ad9f032a10412e7b5a6cd59f0bedbe45d9433a770150bf9ffbf9`, 429905 bytes, 34 domains;
- the claim `pvc-traefik-nfs`, 10 MiB request, bound to `pv-traefik-nfs` (`Retain`).

The after state is recorded below.

## After the cutover, same day

Ownership moved and the release was adopted at revision 8 with
`cluster/infrastructure/traefik/values.yaml`:

- the K3s `traefik` and `traefik-crd` `HelmChart` objects, the `traefik-config` addon and the
  `HelmChartConfig` are gone; `disable: [traefik, local-storage]` is in `/etc/rancher/k3s/config.yaml`
  on the three servers, and `nas` is untouched;
- the release still reports chart `25.0.3+up25.0.0` / app `v2.10.5`, now at revision 8, and its
  stored values compare equal to `cluster/infrastructure/traefik/values.yaml`;
- one pod, `traefik-79f7548b64-wqw69`, on `streaming`, image
  `rancher/mirrored-library-traefik:2.10.7`, strategy `Recreate`;
- `/data/acme.json` unchanged at `f074495f…`, 429905 bytes, 34 domains — no certificate re-issued;
- the same 68 `IngressRoute`, 27 `Middleware`, 14 `TLSOption`, 1 `TLSStore` on
  `traefik.containo.us`, and the one `traefik.io` dashboard route; the Service kept its node ports
  (`30022`, `32075`, `32076`, `32077`, `32078`, `32079`, `32360`);
- every public host answers as before (`plex` 401, `seerr` 307, `ashuvidz.com` 200, `ashudev.com`
  200, `www.ashudev.com` 301, `dashy` 302, `filebrowser` 200, `authentik` 302, dashboard 401), and
  Syncthing's 22000 is open;
- `/metrics` answers on 9100: 150 `traefik_*` series on the endpoint, 121 stored in Prometheus, and
  the pod scrapes `up=1`. The `manualRouting` 404 is gone.
