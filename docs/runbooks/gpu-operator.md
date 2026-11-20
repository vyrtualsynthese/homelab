# The GPU stack: what git owns and what the host owns

> **In one line.** The NVIDIA GPU Operator is a chart in git, pinned to the release that runs; the
> host driver on `streaming` is the one thing installed by hand, and this page records both halves.
>
> **Use it when** the GPU disappears from a pod, Plex stops transcoding, the DCGM GPU panels go
> empty, or someone asks why the node still carries `nvidia.com/gpu-driver-upgrade-state` ·
> **Verified** 2026-11-20 · **Kind:** living page

## The two halves

| Half | Owner | Where it is written down |
|---|---|---|
| Operator chart, image tags, ClusterPolicy, NFD scope | git | `cluster/helm/platform/gpu-operator/`, deployed by `cluster/argocd/projects/platform/gpu-operator.yaml` |
| Kernel driver and CUDA libraries on `streaming` | the host, by hand | this page |

The split is deliberate. `driver.enabled: false` in the chart tells the operator never to build a
driver; the operator then only configures containerd, advertises the GPU and exports its metrics.

## The operator, from git

The chart is a wrapper (repo pattern D) around NVIDIA's `gpu-operator` chart, pinned to `v26.3.1`
and to every image it renders. Its four container-toolkit settings exist because K3s is not a
standard Kubernetes install:

- containerd config `/var/lib/rancher/k3s/agent/etc/containerd/config.toml`
- containerd socket `/run/k3s/containerd/containerd.sock`
- runtime class `nvidia`, registered and set as the default

Without them the toolkit writes `/etc/containerd/config.toml`, which K3s never reads: the `nvidia`
runtime class is never registered and every GPU pod fails to start.

Node Feature Discovery, which the operator uses to label the node, runs its worker only on
`streaming` — the only node with a GPU. Its master and garbage collector sit on `nuc0`.

DCGM, the metrics exporter for the card, is scraped through a Service this chart owns,
`nvidia-dcgm-exporter-metrics`, because the operator creates its own Service and the ClusterPolicy
has no field for adding the `prometheus.io/scrape` annotation to it.

## The host driver

On 2026-11-20 `streaming` runs driver `580.178.04` with CUDA runtime `13.0`, on Ubuntu 22.04 with
the HWE kernel `6.8.0-149`. It was installed by hand and nothing in this repository reinstalls it.

The card is a Quadro P400: Pascal generation, compute 6.1, 2 GiB, graphics mode. It is not MIG
capable — MIG is how a data-centre card is split into isolated slices — and MPS, the software
alternative for sharing one card between processes, is not used here. Pascal support is what keeps
the driver on an older branch, and the host kernel moves with `unattended-upgrades`, so a
containerised driver would have to be rebuilt against the kernel on every bump. Installing on the
host, from the distribution's own packages, survives that.

A rebuild therefore needs, on `streaming`, in this order:

1. the driver and CUDA runtime installed for the running kernel, `nvidia-smi` working outside
   Kubernetes;
2. the chart synced, which registers the `nvidia` runtime class and starts the toolkit, device
   plugin, GPU feature discovery and DCGM;
3. nothing else. The `nvidia.com/gpu.deploy.*` node labels are written by the operator from the
   chart's values, so with `driver.enabled: false` it labels the driver as externally provided by
   itself; nothing about the split depends on a hand-set label.

## Checking the GPU

Run these after anything that touches the node, the driver or the chart:

```bash
kubectl get node streaming -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'   # expect 1
kubectl -n media-stack exec deploy/plex -- nvidia-smi -L                          # expect the P400
```

Then play a title that needs transcoding and confirm in Plex that it is transcoding rather than
direct playing. That is the only check that proves the whole chain — driver, runtime class, NVENC
(the card's hardware video encoder) — and it takes about a minute.

The metrics side:

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=DCGM_FI_DEV_GPU_TEMP'
```

Expect one series labelled `modelName="Quadro P400"`. It feeds the GPU panels on
**Homelab · Platform** and **Homelab · Showcase**, and the `GpuTemperatureHigh` alert (above 80 °C).

## `nvidia.com/gpu-driver-upgrade-state=pod-restart-required`

This label is **residue, not a fault**. It was written while the driver was still the operator's to
manage, and it stayed when the driver moved to the host.

The operator's upgrade controller cannot clear it: its `cluster-policy` object selects nodes by
`app=nvidia-driver-daemonset`, and with `driver.enabled: false` that DaemonSet no longer exists, so
the controller reports **0 nodes in every state** and has nothing to reconcile. Nothing schedules a
rollout, and nothing needs one.

If it is ever in the way, it can be removed with
`kubectl label node streaming nvidia.com/gpu-driver-upgrade-state- --overwrite`. Removing it changes
nothing else.

## What a node reboot does to these pods

All GPU pods on `streaming` restart with the host. Their last state reads `Unknown` with exit code
255, because kubelet kills the containers as the node goes down; that is a shutdown, not a crash
loop. Measured example: `streaming` rebooted on 2026-11-16 at 03:50 and 2026-11-17 at 04:21, and each
time every pod in `gpu-operator` came back at that instant without intervention.

A restart count that climbs on the same days as a reboot needs no investigation. One that climbs on
quiet days does.

## Related

- The Ansible playbook still does not install the driver — see the open item in
  [`audit/ansible-hosts.md`](../audit/ansible-hosts.md).
