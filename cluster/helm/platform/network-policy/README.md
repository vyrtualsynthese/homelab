# network-policy

One namespace's ingress floor, and the allows its consumers need on top of it.

Every pod in this cluster shares one flat network: any pod reaches any Service, whatever namespace it
lives in, so a compromised Plex or Seerr can open PostgreSQL, authentik, the CrowdSec LAPI and
Prometheus. The only credential standing in the way is each service's own login. This chart is the
first layer that stops at the packet: a namespace gets a policy that denies every inbound connection,
then one object per consumer that opens exactly the port that consumer uses.

## What it renders

Two shapes, both in `networking.k8s.io/v1`:

| Object | When | What it does |
|---|---|---|
| `default-deny-ingress` | `defaultDeny: true` | selects every pod in the namespace (`podSelector: {}`) and lists no ingress rule, so every inbound connection is refused |
| `allow-<name>` | one per `accept` entry | selects one pod and opens the ports named there to the peers named there |

NetworkPolicies are additive, so the allows open their ports on top of the floor and nothing else
gets in. Egress is out of the chart on purpose: a namespace-wide egress deny would have to name DNS,
NFS on `192.168.1.41`, the object-storage bucket and the internet for every workload, and one missing
entry cuts an app off silently. It is its own change.

## One Application per namespace

The chart is rendered once per namespace, each time with its own file under `values/`, so a rollout
is one namespace at a time and a rollback is one Application deleted:

| Application | Namespace | Values file |
|---|---|---|
| `network-policy-vikunja` | `vikunja` | `values/vikunja.yaml` |
| `network-policy-filebrowser` | `filebrowser` | `values/filebrowser.yaml` |
| `network-policy-databases` | `databases` | `values/databases.yaml` |
| `network-policy-media-stack` | `media-stack` | `values/media-stack.yaml` |

`media-stack` needed a different measurement. Its twenty-one applications call each other on
addresses held in each application's own database rather than in its chart, so a matrix read from git
would be a guess. `values/media-stack.yaml` names, above the rules, the API or file each pair was
read from on 2026-11-26 — an app's settings endpoint, its mounted config, or its own log. The NFS
provisioner pods accept nothing and have no entry; Unpackerr has no listener.

A values file is the matrix a reviewer reads. `accept[].from[]` takes:

| Key | Meaning |
|---|---|
| `namespace` | a pod in that namespace, matched through `kubernetes.io/metadata.name`; omit it for this namespace |
| `podSelector` | narrow to these pod labels; with `namespace` it names one pod of another namespace |
| `ports` | the TCP ports that peer may use, as numbers |

Application consumers in another namespace name that namespace and no pod, because their pod labels come
from upstream charts and move on upgrade. Inside `media-stack` every caller names its own pod, because
those charts are the repository's and the label is part of them. The infrastructure callers — Traefik,
Prometheus, and the two alert-relay pods — name their pod too, since `kube-system` and `monitoring`
hold far more than one workload. The two relay pods are the exception to the namespace rule among
application consumers because they are the only ones that reach the board directly instead of through
Traefik, and because a label on a per-run CronJob pod is the only handle the policy has on it.

## Adding a consumer

1. Add the peer to the right `values/<namespace>.yaml`.
2. `helm template test cluster/helm/platform/network-policy -n <namespace> -f cluster/helm/platform/network-policy/values/<namespace>.yaml`
3. Commit, let ArgoCD see it, then sync that one Application. Pruning is on for this chart, so a rule
   removed from values leaves the cluster instead of surviving its justification.

## What the first sync proved

The question was whether this K3s enforces a `NetworkPolicy` peer at all — two measurements said no:

- **2026-11-13, on K3s 1.31.14.** A policy selecting the PostgreSQL pod refused every source,
  including the namespaces it named, while the same probe reached the database with no policy at all.
  That is why `databases/postgres` shipped its policy switched off.
- **2026-11-22, same version.** With the Argo CD chart's rule as the only one on the controller, a
  pod in `monitoring` was refused on `8082`; a rule whose peer was an `ipBlock` let it through.
  [The observability page](../../../../docs/observability-conventions.md) records it.

**It works now, on `v1.36.6+k3s1`, and it was the appliance itself that answered.** The three
Applications were synced one at a time on 2026-11-26 from 18:39 UTC. The suite was green — page 200,
`up{namespace="vikunja"}` at `1` — which proves nothing, because an unenforced floor looks exactly
like that. What decided it was a caller nobody had listed: the `monitoring` namespace holds the alert
relay, which opens a card by talking to `vikunja.vikunja.svc.cluster.local:3456` **directly**, not
through Traefik. Its lifecycle CronJob ran one minute after the sync, died, retried twice and failed
with `BackoffLimitExceeded`; the next night's run printed the error:

```
urllib.error.URLError: <urlopen error [Errno 111] Connection refused>
```

`Connection refused` is a policy drop, not a missing route or a dead service — the pod was running and
the name resolved. So the floor holds, a `namespaceSelector` peer translates, and both directions of a
rule are live. The relay and its CronJob are now in `values/vikunja.yaml`.

## The checks, for the next namespace

```bash
# 1. the floor holds: a pod that is not on the allow list cannot reach the Service
kubectl -n default run np-probe --rm -it --restart=Never --image=busybox:1.36.1 -- \
  sh -c 'nc -w 5 -z <svc>.<namespace>.svc <port> && echo OPEN || echo DENIED'
# expect: DENIED — a timeout or a refusal. "OPEN" means the floor is not enforced.

# 2. the allow holds: the route still answers through Traefik
curl -s -o /dev/null -w '%{http_code}\n' \
  --cacert ~/.config/secrets/kub-local/local-ca.pem https://<host>/

# 3. every scrape survives
source ~/.config/secrets/homelab.env
curl -sG -u "ops:$PROMETHEUS_AUTH_PASSWORD" http://prometheus.kub.internal/api/v1/query \
  --data-urlencode 'query=up{namespace="<namespace>"}'
# expect: one result per exporter, value "1"
```

| Result | Reading |
|---|---|
| 1 DENIED, 2 and 3 fine | the namespace is fenced and its allows are right |
| 1 OPEN | nothing is enforced — delete the Application and stop |
| 1 DENIED, 2 refuses | the allow is missing a caller, not broken: find who serves that route |
| 3 drops alone | the `monitoring` peer is wrong, on the exporter's port |

A `kubectl` probe from the LAN or a node is the one thing that cannot answer: it runs as the node, not
as a pod, so a NetworkPolicy does not apply to it and it reports `OPEN` whether or not the floor works.

## `media-stack`, applied and walked on 2026-11-26

The namespace went last, at 18:55 UTC, once the three floors before it had proven the engine. The three
checks above are not enough for it: a missed allow breaks one caller at its next run rather than at a
page load, so every pair in its values file was walked instead of sampled.

| Check | What it proves | Result |
|---|---|---|
| `nc` from a pod in `default` to six Services, `vikunja` among them as the control | the floor | six refused |
| the 51 pairs whose caller lives in the namespace, each resolved to its running pod and probed on its Service | the allows | 51 accepted — the `qbittorrent` one only through the ClusterIP its `hostAlias` names, because that pod resolves with public DNS |
| a throwaway pod carrying `app.kubernetes.io/name=recyclarr`, because that caller is a completed Job | the `recyclarr` allow | four *arrs accepted; a pod that has just started is refused for its first second |
| `indexer/test` and `downloadclient/test` through Radarr's and Sonarr's own APIs | the *arr to Prowlarr and *arr to qBittorrent hops, not just the socket | Radarr 13 of 14 indexers, Sonarr 13 of 15, the failures upstream `429`s; qBittorrent accepted by both |
| `up{namespace="media-stack"}` | the `monitoring` peer, per exporter | 10 of 10 at `1` |
| every routed hostname of the namespace | the `kube-system/traefik` peer | 16 answers, each the app's own status code |
| seven pairs the matrix does not carry, `bazarr` to `qbittorrent` and `radarr` to `plex` among them | nothing extra gets in | seven refused |

No pod restarted and no readiness probe failed across the sync, and no application log holds a
connection error from it.

Two things cost time in the probing, and both read as a denial when they are not:

- `busybox` `nc` takes the host and the port as two arguments. `host:port` prints `nc: missing port
  number` and exits non-zero. The images differ — `busybox` in some, `netcat-openbsd` in others — so one
  one-liner is not portable across the namespace; split the arguments.
- `qbittorrent` runs with `dnsPolicy: None` and public resolvers, so `cross-seed.media-stack.svc` does
  not resolve in its pod. It reaches the Service through the `hostAlias` the chart sets there, and a
  probe of that ClusterIP is the one that answers.

**The lesson the miss cost, and it is the point of this section:** a green suite proves the allows that
exist, not the ones that should. The callers most likely to be forgotten are the ones that act on an
event — an alert, a nightly job — because they are silent until the moment they are needed. Grep the
repository for the Service name and for the hostname, and read the CronJobs, before calling a values
file complete.

