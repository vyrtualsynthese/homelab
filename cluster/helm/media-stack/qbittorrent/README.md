# qbittorrent

qBittorrent behind a WireGuard tunnel, its WebUI on the LAN through authentik, and VueTorrent
installed into its config volume by an init container. The chart follows the contract in
[`docs/charts/README.md`](../../../../docs/charts/README.md); `autobrr` is the reference it was
reshaped from.

## Upstream

- Website: https://www.qbittorrent.org/
- Source: https://github.com/qbittorrent/qBittorrent
- Image: https://github.com/linuxserver/docker-qbittorrent

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/media-stack/qbittorrent.yaml`
with the chart in this directory and `values.yaml` next to it. The release name is the Application
name, `qbittorrent`, so every object lands on the `qbittorrent` / `qbittorrent-vpn-config` names.

```bash
# what the cluster will render, exactly
helm template qbittorrent cluster/helm/media-stack/qbittorrent --namespace media-stack
```

| File | Renders |
|---|---|
| `templates/deployment.yaml` | the Deployment: two init containers, the WireGuard sidecar, qBittorrent |
| `templates/service.yaml` | the ClusterIP Service on the WebUI port |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no API token mounted |
| `templates/persistentvolumeclaim.yaml` | the WireGuard claim, the only claim this chart owns |
| `templates/ingressroute.yaml` | the Traefik IngressRoute and its plain-HTTP redirect |
| `templates/NOTES.txt` | install notes |

There is no ConfigMap: the app reads `qBittorrent.conf` from its config volume, and everything this
chart sets is an environment variable rendered inline in the pod template.

## Routing: gated, and no `/api` exemption

`https://qbittorrent.kub.local`, TLS from the cluster default certificate, with `web` only
redirecting. Two middlewares, in this order:

1. `kube-system-lan-only@kubernetescrd` — the network floor. An off-LAN request is refused with
   `403` before any round trip.
2. `kube-system-authentik-forwardauth` — the gate. Its authentik side is
   `cluster/helm/authentik/files/app-qbittorrent.yaml`, a `forward_single` proxy provider for this
   host bound to the `authentik Admins` group, so a signed-in non-admin gets `403` instead of this
   page.

There is **no `/api` rule**, unlike the *arr charts, and that is deliberate. Nothing in the cluster
reaches this host: `cross-seed` and `qbittorrent-exporter` both use `http://qbittorrent:8080`, and
every URL autobrr logs is a Service name. An API exemption would open the API path on every LAN
machine for a client that does not exist. If something is ever pointed at the hostname, that is the
moment to add one, not before.

The WebUI's own login is **bypassed for the pod network, not disabled**. `qBittorrent.conf` carries
`bypass_auth_subnet_whitelist_enabled=true` and `bypass_auth_subnet_whitelist=10.42.0.0/16`, so a
browser arriving through Traefik lands straight in the WebUI and the gate is the only prompt. Those
preferences live on the config claim, not in this chart: **re-apply them on a rebuilt volume**.

`bypass_local_auth` stays `false`, so `kubectl port-forward svc/qbittorrent 8080:8080` still asks for
the password. That is the break-glass while authentik is down; do not remove the gate without
restoring that login first.

## The privileged sidecar

The `qbittorrent-vpn` container runs `ghcr.io/linuxserver/wireguard` and is the pod's network path:
it creates the tunnel interface, so qBittorrent's traffic leaves through the VPN and not the node's
default route. That is the app's requirement, and it is the one deliberate exception to the
contract's hardening rule in this chart:

| Setting | Why it cannot be dropped |
|---|---|
| `privileged: true` | Creating the WireGuard interface and loading the module need full device access |
| `capabilities.add: [NET_ADMIN, SYS_MODULE]` | `NET_ADMIN` configures the interface, `SYS_MODULE` loads `wireguard` |
| `seccompProfile: Unconfined` | Privileged containers are not confined; stated explicitly so the pod-level `RuntimeDefault` is not misread as applying here |
| hostPath `/dev/net/tun` | The image writes the tunnel through this device |

Everything else in the pod is hardened: the pod-level seccomp profile is `RuntimeDefault` and
qBittorrent itself has `allowPrivilegeEscalation: false`.

qBittorrent's own container keeps the default capability set. The LinuxServer image starts as root,
prepares `/config` and `/downloads` through s6, then drops to `PUID`/`PGID` 1100; dropping `ALL` and
handing back only `CHOWN`/`SETUID`/`SETGID` works for some of these images but has not been verified
against this one, and the failure mode is a pod that will not start. `runAsNonRoot` is not an option
for the same reason. This is the same position as `sonarr` and `prowlarr`.

## The three claims

| Mount | Claim | Owner |
|---|---|---|
| `/config` | `pvc-nfs-qbittorrent-config-nfs-subdir-external-provisioner` | the `nfs-qbittorrent-config` Application — **not this chart** |
| `/downloads` | `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` | the `nfs-qbittorrent-download` Application — **not this chart** |
| `/config` (sidecar) | `qbittorrent-vpn-config` | this chart, `local-path`, `128Mi` |

The two NFS claims are named by value and mounted, never created, here. Their names are the live
ones: renaming either is data loss, and the contract's `<fullname>-config` rule does not apply to a
claim this chart does not own. The reason the settings live on NFS at all is historical; it is
recorded debt, not a decision to revisit in a chart pass.

The WireGuard claim is the only one this chart creates. Its name defaults to
`<fullname>-vpn-config`, which is exactly the claim the pod mounts today, so the derived name moved
no data. It holds `wg0.conf` and the peer keys: **deleting the release deletes the tunnel identity**
and a new peer has to be registered with the VPN provider.

## Ports, one value

`service.port` (8080) drives the container port, the Service port and the IngressRoute target. The
announce port (`torrent.port`, 18515) is opened on the container as TCP and UDP and forwarded through
the tunnel; it is deliberately not on the Service, because nothing in the cluster talks to it.

The WebUI really does listen on 8080: the previous values carried 32400, Plex's port left over from
the scaffold, and the Service template ignored the value, so the two could disagree silently.

## Probes

**Liveness is a TCP connect to the WebUI port. Readiness is off. This is the honest option, not a
shortcut.**

qBittorrent exposes no unauthenticated health endpoint. The only public path is `auth/login`;
everything else under `/api/v2` answers `403` without a session, and the auth bypass covers
`10.42.0.0/16` (the pod network) while the kubelet's own source address is not guaranteed to be
inside it. An `httpGet` probe would therefore either fail forever or start depending on the bypass
by accident — the second failure is worse, because it would break with the login settings.

A TCP probe fails when nothing is listening, which is the case a restart can fix. Readiness would add
nothing: the NFS mounts are checked by the `wait-for-downloads` init container at startup, and the
torrent session has no status endpoint to ask.

## Resources

Sizing is measured, not invented. Re-measured 2026-11-18 over the 5 days Prometheus retains:

| Container | p95 | Peak | Request | Limit |
|---|---|---|---|---|
| qBittorrent | 9726Mi | 12256Mi | 6Gi | 12Gi |
| WireGuard sidecar | 15Mi | 22Mi | 16Mi | 64Mi |
| `init-qbittorrent` | — | — | 16Mi | 64Mi |
| `wait-for-downloads` | — | — | 8Mi | 32Mi |

Most of qBittorrent's working set is download page cache, not the process: RSS was 0.24Gi on
2026-11-13, so the request stays there and the limit sits above the peak. The two init containers run
for seconds, shorter than the scrape interval, so they carry a protection ceiling only.

To re-measure (Prometheus is on `nas`):

```bash
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=max_over_time(container_memory_working_set_bytes{namespace="media-stack",pod=~"qbittorrent-.*",container="qbittorrent"}[5d])/2^20'
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=rate(container_cpu_usage_seconds_total{namespace="media-stack",pod=~"qbittorrent-.*",container="qbittorrent"}[5m])'
```

There is no CPU limit, here or anywhere in this cluster: a limit throttles the short burst the app
needs and protects nothing, while a small request is all the scheduler needs.

## VueTorrent

The `init-qbittorrent` container installs the alternative WebUI from a GitHub release into
`/config/ui`, and only when the `version.txt` already there differs from `vueTorrent.version`. The
long version of the contract between that installer and qBittorrent — including the two preference
names that silently revert the UI — is in the comments above `vueTorrent` in `values.yaml`. Read it
before changing either value.

Renovate tracks `vueTorrent.version` and `vueTorrent.initTag` through the `# renovate:` annotations.
The `depName` on each annotation must stay equal to the value on the line under it.

## Metrics and logs

This chart announces no metrics: qBittorrent has no exporter, and `cluster/helm/media-stack/qbittorrent-exporter`
runs as its own Application against `http://qbittorrent:8080`. Nothing here should add a scrape
annotation.

The log level is **debt of the `bazarr` kind**: no documented variable selects qBittorrent's level,
so the chart sets none, the image logs at `INFO`, and the app's own log file and the WebUI activity
view are the operational record. Raise it from `qBittorrent.conf` by hand if a live problem needs
more, and put it back.

## After a merge

- ArgoCD syncs on its poll or on the operator's sync. Nothing here needs a manual apply.
- The Deployment rolls by itself: the pod template changed. `strategy: Recreate` means a short
  outage of the WebUI, and an in-flight download is not affected — qBittorrent checkpoints to its
  config claim.
- The three claims are unchanged, so there is no first-start step and no data move. A **rebuilt**
  config volume does need the bypass preferences above re-applied, and a **deleted** WireGuard claim
  needs the tunnel re-registered.
- The new ServiceAccount replaces `default` on the pod; nothing reads the Kubernetes API.

## Uninstalling

Deleting the release deletes the `qbittorrent-vpn-config` claim with it. Take a copy of the WireGuard
keys first if the tunnel has to survive. The two NFS claims belong to other Applications and are
untouched.
