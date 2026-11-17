# A pod that will not start

> **In one line.** A pod stuck in `Pending` is one of two faults — never placed, or placed and its
> container never started — and the event at the end of `describe pod` says which.
>
> **Use it when** a pod is `Pending`, `CreateContainerConfigError` or `ImagePullBackOff` and you
> want the cause · **Verified** 2026-11-17 · **Kind:** living page

## First, read the event

```bash
kubectl -n <ns> get pod <pod> -o wide
kubectl -n <ns> describe pod <pod> | tail -n 30
```

The `Events` give the diagnosis: the scheduler why it would not place the pod, the kubelet why it
would not start it. An empty `NODE` means it was never placed; a node means it was, its
container waiting.

Never patch a GitOps-managed object; the sync reverts it
([gitops.md](../how-it-works/gitops.md)).

## No claim to bind

**The claim is missing.** The event says `persistentvolumeclaim "<name>" not found`. The chart
names a claim it does not create, or its Application did not sync; those on
[the status page](../status.md) waiting on a claim nobody recreated are this fault. Fix the chart,
not a hand-made claim.

**The class is missing.** `storageclass.storage.k8s.io "<class>" not found`. Check
`kubectl get storageclass`; use `local-path` or a working NFS class, not the strays in
[storage.md](../how-it-works/storage.md#claims-and-names).

**The provisioner is down.** The claim waits with `waiting for a volume to be created`. Read
`kubectl -n <ns> describe pvc <name>`, then find its pod; `nfs-ssd` is dead this way, so use a class
whose provisioner runs.

## The claim bound to the wrong node

The event says `didn't find available persistent volumes to bind` or `node(s) had volume node
affinity conflict`; they name where the pod may **not** run, not where the volume is.

```bash
kubectl -n <ns> get pvc <name> -o wide
kubectl get pv <volume> -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms}'
```

`local-path` binds on first consumer, to the node of the first pod that used the claim; a rebuild
that pins elsewhere leaves it welded to the old machine. `lidarr`, 2026-11-13:
its claim bound to `nuc0` while the retired pod was still eligible, then the pod pinned to
`streaming` stayed `Pending`.

Recovery, only on an empty claim. Resolve the PV's directory and list it on the bound node:

```bash
kubectl get pv <volume> -o jsonpath='{.spec.hostPath.path}'
```

An empty claim goes; anything else is configuration: move the app back to that node, or copy it
out first.

```bash
kubectl -n <ns> delete pvc <name>   # 1. the claim: Terminating until step 2, then ArgoCD recreates
kubectl -n <ns> delete pod <pod>    # 2. then the pod
```

The order matters: the claim alone leaves the pod queued on the old identity, with no fresh
pass. A `Retain` volume stays `Released`; delete it and its directory. Habit: scale the old
Deployment to zero before the sync that creates the claim
([the binding trap](../how-it-works/storage.md#the-binding-trap)).

## A missing Secret

The pod is placed, the container never starts: `CreateContainerConfigError`.

```bash
kubectl -n <ns> describe pod <pod> | grep -A3 'State:'
```

The message names the Secret or key. Create it in the cluster, never in git; the kubelet starts
the container itself. The inventory: [secrets.md](../secrets.md). A pod running before the key
disappeared keeps the old environment: delete it to pick up the change.

## The image will not pull

`ErrImagePull`, then `ImagePullBackOff`.

```bash
kubectl -n <ns> describe pod <pod> | tail -n 15
```

`manifest unknown` is a missing tag; check `image.tag` against `appVersion`.
`unauthorized` is a private registry with no pull secret: create the pull secret, then name it in
the chart's `imagePullSecrets` — never on the pod. `no such host` is DNS or the registry, a node
problem. Change the tag in the chart.

## Not enough room

Nothing placed it; the event says why:

```bash
kubectl describe node <node> | grep -A6 'Allocated resources'
```

`Insufficient memory` is usual; every container carries a request. The pod is pinned, so only its
node counts; another node is no candidate until `nodeSelector` changes, and the claim stays on
the old node, so a move means a fresh claim and leaves the configuration behind
([nodes.md](../how-it-works/nodes.md)). Lower the request to what
[Homelab · Applications](https://grafana.kub.local/d/homelab-applications) measured. Disk pressure
is a node fault: the `nas` SSD behind the `nas`-pinned configuration claims is dying
([every-day.md](every-day.md)).

## It is starting, not Pending

`Running` with `0/1` ready, or `CrashLoopBackOff`, is a different fault: the pod was placed and the
process is failing; no scheduler event helps.

```bash
kubectl -n <ns> get pod <pod>                  # the RESTARTS column
kubectl -n <ns> logs <pod> --previous
```

Read its last words, then the restarts panel on
[Health](https://grafana.kub.local/d/homelab-health) and one namespace in
[Applications](https://grafana.kub.local/d/homelab-applications). Usual causes: a configuration it
cannot write, a database it cannot reach, and a claim its UID does not own
([the UID trap](../charts/README.md#changing-the-uid-breaks-the-config-volume)).
