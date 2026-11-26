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
those charts are the repository's and the label is part of them. The infrastructure callers — Traefik
and Prometheus — name their pod too, since `kube-system` and `monitoring` hold far more than one
workload.

## Adding a consumer

1. Add the peer to the right `values/<namespace>.yaml`.
2. `helm template test cluster/helm/platform/network-policy -n <namespace> -f cluster/helm/platform/network-policy/values/<namespace>.yaml`
3. Commit, let ArgoCD see it, then sync that one Application. Pruning is on for this chart, so a rule
   removed from values leaves the cluster instead of surviving its justification.

## The question the first sync settles

Whether this K3s enforces the peer forms at all is not settled, and the answer decides whether these
files are a boundary or a hope:

- **2026-11-13, on K3s 1.31.14.** A policy selecting the PostgreSQL pod refused every source,
  including the namespaces it named, while the same probe reached the database with no policy at all.
  That is why `databases/postgres` shipped its policy switched off.
- **2026-11-22, same version.** With the Argo CD chart's rule as the only one on the controller, a
  pod in `monitoring` was refused on `8082`; a rule whose peer was an `ipBlock` let it through.
  [The observability page](../../../../docs/observability-conventions.md) records it.
- **2026-11-24.** All four nodes moved to K3s `v1.36.6+k3s1`. Neither measurement was repeated
  afterwards, and the policy controller is part of the server process, so the upgrade could have
  changed the answer in either direction.

So sync `network-policy-vikunja` first and check three things:

```bash
# 1. the floor holds: a pod that is not Traefik cannot reach the board
kubectl -n default run np-probe --rm -it --restart=Never --image=busybox:1.36.1 -- \
  sh -c 'nc -w 5 -z vikunja.vikunja.svc 3456 && echo OPEN || echo DENIED'
# expect: DENIED (a timeout is a deny; "OPEN" means the floor is not enforced)

# 2. the allow holds: the page still answers through Traefik
curl -sk -o /dev/null -w '%{http_code}\n' https://vikunja.kub.local/
# expect: 200

# 3. the scrape survives: Prometheus still reads the pod
source ~/.config/secrets/homelab.env
curl -sG -u "ops:$PROMETHEUS_AUTH_PASSWORD" http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=up{namespace="vikunja"}'
# expect: one result, value "1"
```

What each outcome means:

| Result | Reading | Next |
|---|---|---|
| 1 DENIED, 2 and 3 fine | the engine enforces both the floor and a `namespaceSelector` peer | sync `filebrowser`, then `databases`, then `media-stack` |
| 1 OPEN | the engine enforces nothing | delete the floor's Applications: it has to be proven before more of them exist |
| 1 DENIED, 2 refuses | the floor works and the namespace peer does not translate | revert to the `ipBlock` peer the Argo CD chart needed, and record the form that works |
| 1 DENIED, 3 drops and 2 fine | the `monitoring` peer is the broken one | same as above, on that rule |

The one object that cannot regress: a `kubectl` probe from the LAN or a node is running as the node,
not as a pod, so it is not subject to a NetworkPolicy — it will report `OPEN` whether or not the floor
works. Use a pod in a namespace that is not on the allow list.

For `media-stack` the probe above is not enough. A missed allow breaks one caller at its next run,
not at a page load, so after the sync walk the download path end to end — an *arr grabs from
Prowlarr, Prowlarr answers the Torznab query, qBittorrent accepts it, cross-seed sees the hook — and
load every routed hostname. The Pods column of `kubectl get networkpolicy -n media-stack` and the
application logs are where a missing pair shows; the values file's header names each one.
