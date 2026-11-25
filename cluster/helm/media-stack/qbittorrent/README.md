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
| `templates/deployment.yaml` | the Deployment: three init containers, the WireGuard sidecar, qBittorrent |
| `templates/service.yaml` | the ClusterIP Service on the WebUI port |
| `templates/serviceaccount.yaml` | the ServiceAccount, with no API token mounted |
| `templates/persistentvolumeclaim.yaml` | the two claims this chart owns, on `local-path` |
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

## Cluster traffic across the tunnel

The tunnel is a kill switch: `AllowedIPs` is `0.0.0.0/0`, so wg-quick puts a default route in its
own table and a rule that sends everything there. It does keep local access, but only for prefixes
the pod's main table knows specifically — `ip rule add table main suppress_prefixlength 0`. A pod's
main table holds its own subnet and a default route, nothing else, so **every ClusterIP matched
nothing specific and went into the tunnel**, where no route to `10.43.0.0/16` exists.

That is the whole of the 2026-11-19 finding: the post-download hook calls `cross-seed:2468`, the
SYN disappeared into the tunnel, and the connection died after 127 s — the kernel's SYN retry
budget, not a `curl` timeout. 154 such lines in 24 h, 76 % of that container's log bytes.

The `vpn-cluster-routes` init container adds `10.42.0.0/16` (pods) and `10.43.0.0/16` (Services) to
the main table through the pod's own gateway before the sidecar starts; the sidecar's rule then
prefers them. It needs `NET_ADMIN` and nothing else. The ranges are `vpn.clusterRoutes` in
`values.yaml`, so another in-cluster dependency is one line there.

Proof that the tunnel is what swallowed the traffic is the sidecar's own startup log:

```
[#] wg set wg0 fwmark 51820
[#] ip -4 rule add not fwmark 51820 table 51820
[#] ip -4 rule add table main suppress_prefixlength 0
[#] ip -4 route add 0.0.0.0/0 dev wg0 table 51820
```

**One step stays in the app.** The hook itself is a qBittorrent preference on the config claim, not
a chart value, and it should carry `--max-time 10` so a future route fault costs ten seconds rather
than two minutes. Set it under Tools → Options → Downloads → *Run external program on torrent
finished*.

## The two claims, and one host path

| Mount | What backs it | Owner |
|---|---|---|
| `/config` | claim `qbittorrent-config` | this chart, `local-path`, `2Gi` |
| `/downloads` | host path `/mnt/ultrafaststorage/nfs` | the `nas` host, through `storage.downloads.hostPath` |
| `/config` (sidecar) | claim `qbittorrent-vpn-config` | this chart, `local-path`, `128Mi` |

`/downloads` is the export root, and the pod reads and writes it **from `nas` itself**, where the
directory already is. The *arrs, unpackerr and FileBrowser keep mounting the same directory through
the `nfs-qbittorrent-download` Application; this chart no longer references that claim at all. A
`hostPath` rather than a claim because the export root exists already, and a `local-path` claim would
create a subdirectory beside it that no NFS client would read. `type: Directory` makes a missing
export fail the pod instead of creating an empty tree and downloading into it.

The reason is the Web UI stall, and it is measured rather than guessed: on `streaming` the app's
~2,000 cycles a second of open, 16 KiB write and close made the NFS client leave a page waiting to
be written for an I/O nothing performs, and the thread serving the Web UI waited on it for six
minutes fifty seconds. On a local filesystem the same load did not wedge it — the 35 minutes the
export spent on a local claim on `streaming` answered the API in 14 ms throughout — and the failure
there was the disk under it, a 25,241-hour BX500 at 24-50 MB/s, not the local path. `nas` writes the
export from its own NVMe at 456 MB/s. The whole record is
[`docs/qbittorrent-webui-stalls.md`](../../../../docs/qbittorrent-webui-stalls.md), the move is
[`docs/runbooks/qbittorrent-to-nas.md`](../../../../docs/runbooks/qbittorrent-to-nas.md).

`/config` was the exception to the `local-path` rule, and it was the case the rule exists for:
`app/preferences` reads that directory on every exporter scrape, and on NFS it blocked for seconds
at a time, taking the WebUI and every API client with it. It moved on 2026-11-25 (board card 758),
keeping its name — `qbittorrent-config`, the `<fullname>-config` the contract asks for. The old NFS
export is retained on `nas` as the rollback copy until the migration has been observed; nothing
mounts it. It moved again on 2026-11-25, to the node the app now runs on, with its contents copied
across; the runbook above is the order.

The WireGuard claim holds `wg0.conf` and the peer keys: **deleting the release deletes the tunnel
identity** and a new peer has to be registered with the VPN provider. The config claim holds the
resume data: deleting it loses the torrent list.

## Ports, one value

`service.port` (8080) drives the container port, the Service port and the IngressRoute target. The
announce port (`torrent.port`, 18515) is opened on the container as TCP and UDP and forwarded through
the tunnel; it is deliberately not on the Service, because nothing in the cluster talks to it.

**The port is open from the internet, and that is the provider's doing.** Nothing here creates the
forwarding: qBittorrent's own UPnP/NAT-PMP is off (`upnp: false`), and the sidecar is a plain
`wg-quick` client with no port-forward client in it, so the rule that forwards 18515 is one the VPN
provider holds, not one this chart declares. Verified 2026-11-26 (board card 541): a TCP connect from
outside to `109.235.50.35:18515`, the tunnel's exit address and the port qBittorrent reports as
`listen_port`, succeeded while 18514 and 18516 were refused; the pod's own socket table showed inbound
`ESTABLISHED` connections on `10.191.241.13:18515` from public peer addresses; and
`/api/v2/transfer/info` answered `connection_status: connected` with
`last_external_address_v4: 109.235.50.35`. Two consequences:

- `qbittorrent_transfer_connection_status` reads `firewalled` for a few minutes after a restart and
  then returns to `connected`. That is the normal wait for the first incoming peer, not a broken
  forward — a forward that is gone stays `firewalled`.
- The forwarding is provider-side state, not GitOps state. Rebuilding the tunnel identity
  (`qbittorrent-vpn-config`) is the moment to check it again, and nothing in the cluster notices if
  it is missing: the client keeps downloading from outgoing peers only, and no alert watches that
  metric.

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

A TCP probe fails when nothing is listening, which is the case a restart can fix. It is still a
gamble with data, and the chart carries it knowingly: a restart does not re-run `wait-for-downloads`,
so a probe firing while the downloads export is unready is how torrents have been dropped before. It
stayed during the 2026-11 stall only because that stall had a cause outside the app — the NFS client
under `/downloads`, which the move to `nas` takes away — and it restarted the container 15 times in
12 h while it lasted.
Readiness would add nothing: the export mount is checked by the `wait-for-downloads` init container at
startup, and the torrent session has no status endpoint to ask.

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
`/config/ui`. It runs when `version.txt` there differs from `vueTorrent.version`, **or** when
`public/index.html` is missing: the app serves `<RootFolder>/public`, so a tree carrying the right
version and no page is a broken tree, and the installer puts it back.

It **cannot fail the pod**: an unreachable package repository, a failed download, a truncated zip or a
read-only claim each leave `/config/ui` untouched, log a `WARNING` line naming the step that failed,
and exit 0, so qBittorrent starts on its stock WebUI instead of the pod never starting. The two
network steps are bounded (`timeout 60` on `apk`, `wget -T 20`) so a blackholed address does not hold
pod start for the kernel's full TCP retry budget.

Two things follow a successful install, both on the config claim:

- `WebUI\AlternativeUIEnabled=true` is written into `/config/qBittorrent/qBittorrent.conf`, which is
  then chowned to `PUID`/`PGID`. qBittorrent clears that preference itself when it finds no UI, and
  it rewrites the whole file from memory on exit, so the only moment it can be set safely is here,
  before the app starts. It is written **only after an install**, so an operator who turns the
  alternative UI off in the WebUI keeps it off across restarts, and a file that already reads `true`
  is not touched. An existing file is rewritten through its own inode, so a root-run init container
  cannot leave the app a file it is unable to save into — the chown after it is best-effort, and the
  file keeps whatever owner it had if the export refuses the change. A file the installer creates and
  cannot hand over is removed instead, and the log says so.
- the archive is kept as `/config/vuetorrent-<version>.zip`; a later install of that same version
  uses it when github.com is unreachable.

That closes the loop the fallback opened: "files present, preference false, stock UI" was the state a
successful install could leave behind, and it no longer exists. A **first** boot with no network
still serves the stock WebUI — there is nothing on the claim to install from and no image carries the
files. That gap is accepted. One state is left to the manual `curl` in the comments above
`vueTorrent` in `values.yaml`: a preference cleared while the tree is complete, which no install
follows and therefore no restart repairs.

Renovate tracks `vueTorrent.version` and `vueTorrent.initTag` through the `# renovate:` annotations.
The `depName` on each annotation must stay equal to the value on the line under it.

## Metrics and logs

This chart announces no metrics: qBittorrent has no exporter, and `cluster/helm/media-stack/qbittorrent-exporter`
runs as its own Application against `http://qbittorrent:8080`. Nothing here should add a scrape
annotation.

The log level is **debt with no knob at all**, one step past `bazarr`'s. qBittorrent 5.3.0 has no
verbosity setting: `app/setPreferences` exposes only `file_log_enabled`, `file_log_path`,
`file_log_max_size` and the rotation keys (`src/webui/api/appcontroller.cpp` at tag
`release-5.3.0`), and `qBittorrent.conf` carries no level either. The API key does not help — there
is nothing for it to set. So the chart sets none, the app logs at its own fixed level, and the
WebUI's Log view plus the log file are the operational record. That volume cannot be turned down
from here; it stays debt until upstream adds a level.

## After a merge

- ArgoCD syncs on its poll or on the operator's sync. Nothing here needs a manual apply.
- The Deployment rolls by itself: the pod template changed. `strategy: Recreate` means a short
  outage of the WebUI, and an in-flight download is not affected — qBittorrent checkpoints to its
  config claim.
- The two claims and the `/downloads` host path are unchanged on an ordinary upgrade, so there is no
  first-start step and no data move. A **rebuilt**
  config volume starts with no `/config/ui` and no `qBittorrent.conf`: the installer fetches the UI
  from the cached archive or GitHub and sets the preference, so it needs nothing here. It does need
  the bypass preferences above re-applied, and a **deleted** WireGuard claim needs the tunnel
  re-registered.
- The new ServiceAccount replaces `default` on the pod; nothing reads the Kubernetes API.

## Uninstalling

Deleting the release deletes the `qbittorrent-vpn-config` and `qbittorrent-config` claims with it.
Take a copy of the WireGuard keys first if the tunnel has to survive, and of the config claim if the
torrent list does. `/downloads` is a directory on `nas`, not an object of this release: nothing here
deletes it, and the *arrs keep reading it over NFS.
