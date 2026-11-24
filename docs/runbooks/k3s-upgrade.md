# Moving K3s to a new minor

Date: 2026-11-21/22 and 2026-11-24 · **Proved** on `v1.30.1+k3s1` → `v1.31.14+k3s1`, then
`v1.31.14+k3s1` → `v1.33.13+k3s2`, then → `v1.36.6+k3s1`, four nodes, one at a time

> **In one line.** The `system-upgrade-controller` swaps one node's k3s binary, the node restarts, and
> the next node waits for a human to point the plan at it.
>
> **Use it when** K3s has to move to a new minor version, or a single node is stuck on an older one.
> The next step up is 1.37 and that is a decision, not a scheduled hop.
> · **Verified** 2026-11-24: all four nodes on `v1.36.6+k3s1`, containerd `2.3.7-k3s1.36`, Ready,
> etcd `ok`, both Plans pinned to the version they reached, controller kubectl `v1.36.2`; five minors
> climbed in three chains on this procedure, quorum never lost, and no ingress blip in the last three
> · **Kind:** how-to
>
> **Sources** `cluster/infrastructure/system-upgrade/controller.yaml`,
> `cluster/infrastructure/system-upgrade/controller-env.yaml`, `docs/runbooks/restore-etcd.md`, and the
> live Plans, Jobs and node versions, read on 2026-11-24

## What this touches

| Node | Role | Note |
|---|---|---|
| `nuc0` | control plane + etcd | runs Argo CD's controller and server |
| `rbp2` | control plane + etcd | ARM64 Pi; only DaemonSets run there |
| `streaming` | control plane + etcd | Traefik, the media stack, the GPU — see the ingress note below |
| `nas` | worker | Prometheus, Grafana, Loki and PostgreSQL; serves the NFS exports from the host |

**etcd quorum is two of three.** Never let two control-plane nodes restart at once. `concurrency: 1`
on both plans is what enforces this, and the plan selector is what stops the controller from walking
on to the next node unattended.

k3s restarts in place; containers get one `SIGTERM` and come back on the same node. Nothing is
rescheduled, which matters because every workload here is pinned to a named node and could not
reschedule anyway.

## Before you start

**Argo CD's supported range is 1.34–1.37.** On 1.33 the controller is one minor below its floor and
says so; that warning is expected and is not a new fault. It disappears at 1.34 and comes back at
1.37, which the plan does not reach.

**The kubelet refuses cgroup v1 from 1.35.** All four nodes are on cgroup v2, checked on
2026-11-24 by reading the host's mount table for `/sys/fs/cgroup`:

```bash
# privileged pod pinned to the node, host root at /host
grep ' /sys/fs/cgroup ' /host/proc/1/mountinfo | sed 's/.* - //' | cut -d' ' -f1
```

`cgroup2` on every node; the driver was already `systemd`.

**1. Capture the pre-flight.** Write it to a file and compare against it afterwards. The numbers that
changed on 2026-11-21 were only the node versions and the container restart counts.

```bash
kubectl get nodes -o wide
kubectl get pods -A --no-headers | awk '$4!="Running" && $4!="Completed"'
kubectl -n argocd get applications -o json \
  | jq -r '.items[]|"\(.metadata.name) \(.status.sync.status)/\(.status.health.status)"' | sort
kubectl get --raw /healthz/etcd
```

**2. Check that nothing sits on an API the climb removes.** The five releases drop very little, and a
hit only shows after the fact, as an object a client can no longer read.

```bash
hack/check-api-removals.sh
```

It reads the served group/versions (`kubectl get --raw /apis`), the live volumes for a schema the
climb changes, and every `*.yaml` under `cluster/` and `kubectl/` — what Argo CD re-applies — and
matches them against the removal table in `hack/check_api_removals.py`. **Expected: `0 hits`.** One
`note:` is normal on 1.31: `flowcontrol.apiserver.k8s.io/v1beta3` is still served, 1.32 drops it, and
`v1` carries the same objects.

| Release | Stops serving | Use instead |
|---|---|---|
| 1.32 | `flowcontrol.apiserver.k8s.io/v1beta3` (FlowSchema, PriorityLevelConfiguration) | `flowcontrol.apiserver.k8s.io/v1` |
| 1.33 | nothing | — |
| 1.34 | nothing | — |
| 1.35 | `storage.k8s.io/v1alpha1` (VolumeAttributesClass) | `storage.k8s.io/v1` |
| 1.35 | `storagemigration.k8s.io/v1alpha1` (StorageVersionMigration) | `storagemigration.k8s.io/v1beta1` |
| 1.36 | `PersistentVolume.spec.portworxVolume` (in-tree Portworx) | the `pxd.portworx.com` CSI driver |
| 1.36 | `groupsnapshot.storage.k8s.io/v1alpha1` (SnapshotMetadataService) | `groupsnapshot.storage.k8s.io/v1beta1` |

Sources, read on 2026-11-23: the [deprecated API migration
guide](https://kubernetes.io/docs/reference/using-api/deprecation-guide/) for 1.32, and the
[1.35](https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.35.md) and
[1.36](https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.36.md) changelogs;
1.33 and 1.34 have neither a guide section nor a changelog entry, which is the absence itself.

**On a hit**, fix the object or the manifest before the plan is touched: a manifest takes the
surviving version, a `portworxVolume` moves to its CSI driver. A hit cannot be repaired after the
node that serves it has restarted.

**3. Copy etcd off the node, because the next step restarts it.** The copy holds the cluster's object
state, not one node's: any server's copy restores the whole of it. What the node in the file name
records is *which server wrote the file*, and K3s takes it from where the command runs, not from
`--name`: a snapshot is `<name>-<node>-<time>`. Pin the pod with `nodeName` to the server whose restart
is next, pass the version alone to `--name`, and read the printed file name back:

```bash
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata: {name: etcd-snapshot-once, namespace: kube-system}
spec:
  nodeName: <node>                 # the control-plane node this copy is for
  hostNetwork: true
  hostPID: true
  restartPolicy: Never
  tolerations: [{operator: Exists}]
  containers:
    - name: snap
      image: docker.io/rancher/k3s:v1.36.6-k3s1   # the version the node runs *before* the hop
      securityContext: {privileged: true}
      command: [/bin/sh, -c, "chroot /host /usr/local/bin/k3s etcd-snapshot save --name pre-<version> && ls -l /host/var/lib/rancher/k3s/server/db/snapshots | tail -3"]
      volumeMounts: [{name: host-root, mountPath: /host}]
  volumes:
    - {name: host-root, hostPath: {path: /}}
YAML
kubectl -n kube-system logs etcd-snapshot-once
```

The `ls` must show `pre-<version>-<node>-<time>`, with the node you pinned in the middle. A different
node there means the manifest was carried over from the previous step, and the copy is not from the
node it is for: fix the pod, not the tag. The path in that `ls` is the **host's**, reached through the
`/host` mount: pointed at the pod's own `/var/lib/...` it prints `No such file or directory` while the
snapshot was in fact written, which is what the 2026-11-24 run saw on `nuc0`.

A worker has nothing to copy: `nas` has no `/var/lib/rancher/k3s/server/db/snapshots` at all, and its
restart cannot change object state. The server copies across the run are the rollback points for the
whole upgrade; do not take one "for the worker" on whichever server you happen to be on.

Then push it into the off-site repository, from the node that holds the file. Re-running the whole
streaming backup job works but reads 9 GiB to upload 60 MiB; a pod that backs up only the snapshot
directory takes five seconds:

```bash
# same container spec as the backup job's `restic` container, with:
#   command: sh -c 'restic backup /host/etcd-snapshots --tag etcd-pre<version>-<node>'
#   volumes: the `etcd-snapshots` hostPath only (/var/lib/rancher/k3s/server/db/snapshots)
#   nodeName: <the same node as the snapshot pod>
kubectl -n backup logs <pod>          # "snapshot <id> saved", then `restic ls` names the file
```

`--tag` carries the node from the file name, never the plan's target, so the two cannot disagree.
`--host <node>` does the same for restic's own group key, and it matters for the retention: `forget`
works per host, and a copy whose host is a pod name is a group of one that no `keep-*` rule can ever
drop. `rbp2` has no stream of its own, so its copies go to `nodes-streaming`, the control-plane
stream that is not already carrying them.

**What the 1.31 run left, read on 2026-11-22.** Every snapshot pod in that run was pinned to
`streaming` while `--name` carried the node being restarted, so four files were written, all on
`streaming`, and all said so: `pre-131-nuc0-streaming-1795301064`,
`pre-131-rbp2-streaming-1795303043`, `pre-131-streaming-streaming-1795303491` and
`pre-131-nas-streaming-1795303870`. The node directories confirm it: `nas` has no snapshot directory,
and `nuc0` and `rbp2` hold no pre-1.31 file. Nothing was missing — an etcd copy is cluster state — but
three copies sat in the bucket tagged `etcd-pre131-{rbp2,streaming,nas}`, each under a host of its own,
so each was a `forget` group of one that no retention rule would ever drop. The two mislabelled ones
were forgotten on 2026-11-22 (`restic forget 5a6d795d d434572d --prune`, 72 KiB of packs freed);
`etcd-pre131-streaming` is kept as the pre-1.31 point.

The NAS is not an option: `/mnt/storage` and `/mnt/storage/nfs` are handed to pods read-only, and only
the per-claim subdirectories are writable. A copy there would land inside a backed-up stream.

**4. Pin the version *and* the target node** on the plan the node belongs to. Two plans exist:
`server-plan` matches `node-role.kubernetes.io/control-plane In [true]`, `agent-plan` matches
`DoesNotExist` (its `prepare` step waits for `server-plan` to finish).

```bash
kubectl -n system-upgrade get plan server-plan -o jsonpath='{.spec}'
kubectl -n system-upgrade patch plan server-plan --type=merge -p '{"spec":{
  "version":"v1.36.6+k3s1",
  "nodeSelector":{"matchExpressions":[
    {"key":"node-role.kubernetes.io/control-plane","operator":"In","values":["true"]},
    {"key":"kubernetes.io/hostname","operator":"In","values":["nuc0"]}]}}}'
```

`spec.version` overrides `channel`; never leave `channel: stable` as the only selector. The hostname
expression is not decoration — without it the controller upgrades the next matching node the moment the
first one reports the new version.

**5. Move the controller's kubectl client with the plan.** Both plans use `cordon: true`, which puts a
`cordon` **init container** in every upgrade job, its image taken from
`SYSTEM_UPGRADE_JOB_KUBECTL_IMAGE` in `cluster/infrastructure/system-upgrade/controller-env.yaml`. The
pin is on the node binary's minor: kubectl supports one minor either side of the API server, and the
value is read once at container start, so bump it before the hop that leaves the window and restart
the controller. The tag must be one `rancher/kubectl` actually publishes — it tracks Kubernetes
patches, not k3s ones, and a name it does not have leaves the job in `Init:ImagePullBackOff` until
its deadline while the node stays where it was:

```bash
# the newest published tag on the minor, before the climb
curl -s "https://hub.docker.com/v2/repositories/rancher/kubectl/tags?page_size=100" | jq -r '.results[].name' | grep '^v1\.36\.'
kubectl apply -f cluster/infrastructure/system-upgrade/controller-env.yaml
kubectl -n system-upgrade rollout restart deploy/system-upgrade-controller
```

## Watch it

```bash
for i in $(seq 1 90); do
  kubectl get node <node> -o jsonpath='{.metadata.name} {.status.nodeInfo.kubeletVersion} {.status.conditions[?(@.type=="Ready")].status}{"\n"}'
  kubectl -n system-upgrade get jobs --no-headers | grep <node>
  sleep 10
done
```

The controller creates `apply-<plan>-on-<node>-with-…`, which:

1. pulls `rancher/k3s-upgrade:<version>`,
2. replaces `/usr/local/bin/k3s` and restarts the k3s service,
3. the node goes `NotReady` for a few seconds and returns ready on the new version.

Serve the API from another control plane while the target restarts, without editing the kubeconfig:

```bash
kubectl --server=https://192.168.1.27:6443 get nodes
```

## Verify, then point the plan at the next node

```bash
kubectl get nodes -o wide                       # version and containerd
kubectl get pods -A --field-selector spec.nodeName=<node> --no-headers | grep -v -E 'Running|Completed'
kubectl get --raw /healthz/etcd
kubectl -n argocd get applications -o json | jq -r '[.items[]|select(.status.sync.status=="Synced" and .status.health.status=="Healthy")]|length'
curl -s -o /dev/null -w '%{http_code}\n' --resolve dashy.ashudev.com:443:192.168.1.46 https://dashy.ashudev.com/
# on the node that carries the GPU, the scheduler resource, not just nvidia-smi:
kubectl get node streaming -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'   # expect 1
```

Expected: the node on the pinned version and `Ready`, no unhealthy pod that was healthy before, etcd
`ok`, the Argo count unchanged, and every host answering as before. On `streaming`, the allocatable
line is the one that catches the 2026-11-22 fault: every GPU pod was `Running` and `nvidia-smi`
still worked, while the scheduler resource read 0. The recovery is one pod delete and takes about 90
seconds — [`gpu-operator.md`](gpu-operator.md#the-gpu-drops-to-0-after-a-restart). Then patch the
plan's hostname list to the next node and repeat.

## What actually happened on 2026-11-21/22

| Node | Version seen after | Time to `Ready` | Notes |
|---|---|---|---|
| `nuc0` | 40 s | 40 s | containerd 1.7.15 → 2.1.5 at the same time |
| `rbp2` | 160 s | 160 s | ARM64 Pi, four times slower than the others |
| `streaming` | 20 s | 20 s | ingress down **15 s**, measured with a probe every 2 s |
| `nas` | 20 s | 20 s | PostgreSQL shut down cleanly, NFS kept serving |

Total elapsed: about forty minutes for all four, including the etcd copies and the waits.

**Ingress blip.** A probe hit `https://dashy.ashudev.com/` every two seconds through the `streaming`
upgrade: last good answer 00:25:14, seven failed probes from 00:25:58 to 00:26:10, first good answer
00:26:15. Traefik restarts with k3s, and the Service's six `nodePort` values do not change.

**Plex is slower than the ingress.** It answered `503` for about three minutes while its two containers
started, and `plex` and `calibre-web-automated` sat `Progressing` in Argo for that window. The ingress
being back is not the same as the apps being back.

**Backups still work, proved after each node.** Re-running that node's job is the check that the host
mounts, the SQLite staging and restic survive the containerd jump: `nuc0` 629 files, `streaming` 21 005
files, `nas` 599 files, each ending in a new snapshot. Delete each re-run job once its log shows the
snapshot: `kubectl -n backup delete job <name>`. Nothing else ever removes it (see the traps).

**GPU: the pods survived, the scheduler resource did not.** `nvidia-container-toolkit`, the device
plugin, `dcgm-exporter` and `intel-gpu-plugin` all came back, and `nvidia-smi -L` in the Plex pod
still listed the Quadro P400 — but the node advertised `nvidia.com/gpu: 0` for about forty minutes,
and only the allocatable check noticed. Deleting the validator pod restored it in 90 seconds; cause
and recovery are in [`gpu-operator.md`](gpu-operator.md#the-gpu-drops-to-0-after-a-restart). The
first version of this paragraph said the GPU "survived" on the strength of the pod list alone.

## What actually happened on 2026-11-24

Five minors in one evening, three chains: 1.32 and 1.33, then 1.34, 1.35 and 1.36, each chain
`nuc0` → `rbp2` → `streaming` → `nas`.

**1.32 and 1.33**

| Node | 1.32 to `Ready` | 1.33 to `Ready` | Notes |
|---|---|---|---|
| `nuc0` | 33 s | 27 s | containerd `2.1.5-k3s1.32` → `2.2.5-k3s1.33` at the second hop |
| `rbp2` | 152 s | 268 s | ARM64 Pi; slower on the second hop than the first |
| `streaming` | 27 s | 22 s | ingress down **6 s**, three failed probes 01:16:24–01:16:28 |
| `nas` | 27 s | 32 s | PostgreSQL and the NFS exports unaffected |

- **`dashy` flipped not-ready after each `nuc0` restart and recovered on its own.** Its probe period is
  90 s, so a readiness change can lag the node by that long; the pod kept serving throughout (`wget`
  from a pod on `nuc0` answered while the kubelet still said not-ready). A `ClusterIPNotAllocated`
  event on its Service repaired itself in the same window.
- **Backups replayed:** `nuc0` 636 files, `streaming` 787, `nas` 608.

**1.34, 1.35 and 1.36**

| Node | 1.34 | 1.35 | 1.36 |
|---|---|---|---|
| `nuc0` | ~55 s, by hand (see the trap below) | 32 s | 31 s |
| `rbp2` | 5 m 31 s, about 4 m of it failed image pulls | 226 s | 215 s |
| `streaming` | 31 s | 31 s | 32 s |
| `nas` | 31 s | 31 s | 31 s |

- **containerd ends on `2.3.7-k3s1.36`**; it moved once, at 1.36, with no separate step.
- **No ingress blip.** A probe every 2 s through each `streaming` step saw no failed sample in 1.34,
  1.35 or 1.36, unlike the 6-second one at 1.33.
- **The GPU held.** `nvidia.com/gpu` read `1` after the `streaming` steps in 1.35 and 1.36. It was
  not recorded after 1.34; the applications came back, which the 2026-11-22 fault proved is not the
  same check.
- **Backups replayed after every node that has one**, each ending in a saved snapshot, then the
  manual job deleted: `nuc0` 639 files at 1.36, `streaming` 786-787, `nas` 608.
- **The controller's kubectl image is a published tag on the node's minor, not the k3s patch.** See
  the trap below; it cost fifteen minutes of a failed job at 1.34.

## Traps

| Symptom | Cause |
|---|---|
| the plan upgrades every node without being asked | the hostname expression is missing from the selector, and `spec.version` alone applies to everything the plan matches |
| the upgrade job's pod sits at `Unknown` and a second job appears | normal: k3s restarted under the first job, the kubelet lost it, and the second job finds the binary already replaced and exits 0. Read the *second* job's log |
| `kubectl` fails with `connection refused` on port 6443 | the target node is the backend your kubeconfig names. Use `--server` against another control plane |
| `kubectl debug node/<node>` gives no output | the debug pod is deleted when its command exits; create the pod yourself, as above |
| a `node-exporter` target reads `up 0` for a minute | the restart window after containerd comes back; it recovers. If it stays 0, the host port is not bound, which containerd 2.x changed |
| `host-path missing: /host/etc/exports` in a `nuc0` or `streaming` job log | `/etc/exports` exists on `nas` alone. The job prints the line and carries on; it is not a failure |
| a `backup-node-*` job created by hand sits beside the scheduled ones for days | `kubectl create job --from=cronjob/…` is not a scheduled run, and nothing prunes it as the history limits turn over: the two `first-*` jobs from 2026-11-20 were still there, `Failed`, 25 hours and five scheduled runs later, and read as a live backup failure. Read the log for `snapshot <id> saved`, then `kubectl -n backup delete job <name>` |
| the upgrade job sits `0/1` in `Init:ImagePullBackOff` until its 900 s deadline, and the node never moves | the cordon init container pulls `SYSTEM_UPGRADE_JOB_KUBECTL_IMAGE`, and the tag named there does not exist. `rancher/kubectl` publishes kubernetes patches, not k3s ones: `v1.34.13` has no image while `v1.34.9` does. Check the tag on Docker Hub before the climb and pin the newest on the node's minor |

## Rolling back

**A node is one-way in practice.** The plan stores no previous release, and the controller only ever
installs forward: there is no `rollback` verb. Going back means running K3s's installer on that node
with the older `INSTALL_K3S_VERSION` (`/usr/local/bin/k3s` is replaced, the new binary is not kept), or
rebuilding the node. Neither has been rehearsed here, and the 2026-11-21 run had no SSH to the nodes,
so a node that failed to come back would have needed physical or console access.

What limits the damage: etcd keeps quorum on the other two servers while a control plane restarts, the
snapshot in the OVH bucket predates the change, and a worker failing leaves the cluster serving. Plan
the rollback *before* touching the first node.

Restoring etcd from a snapshot is written down and has been rehearsed: [`restore-etcd.md`](restore-etcd.md)
has the commands, the token requirement and the measured times. What has not been rehearsed is the
*rollback of a node that fails mid-upgrade*: the restore has only been run on a throwaway server, and
the snapshot in the bucket predates the change.

## After the run

- Both plans stay pinned to the version they reached; the last hostname list names the last node
  upgraded. The 1.32–1.36 climb is done (cards 520 and 521); 1.37 is a decision of its own and has
  no card yet.
- Update `AGENTS.md` §7, the plan, and this page with the version as it is now.
- A K3s upgrade does **not** touch the Traefik release. Confirm it is still this repository's chart
  and not a K3s-bundled addon: `kubectl -n kube-system get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].image}'`
  should print the image pinned in `cluster/helm/platform/traefik/values.yaml`.
