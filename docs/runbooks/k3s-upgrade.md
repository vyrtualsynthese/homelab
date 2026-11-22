# Moving K3s to a new minor

Date: 2026-11-21/22 · **Proved** on `v1.30.1+k3s1` → `v1.31.14+k3s1`, four nodes, one at a time ·
**Kind:** living procedure

> **In one line.** The `system-upgrade-controller` swaps one node's k3s binary, the node restarts, and
> the next node waits for a human to point the plan at it.
>
> **Use it when** K3s has to move to a new minor version (1.32 next), or a single node is stuck on an
> older one · **Verified** 2026-11-22: all four nodes on `v1.31.14+k3s1`, containerd `2.1.5-k3s1.32`,
> etcd quorum never lost, one 15-second ingress blip on `streaming`.

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

**1. Capture the pre-flight.** Write it to a file and compare against it afterwards. The numbers that
changed on 2026-11-21 were only the node versions and the container restart counts.

```bash
kubectl get nodes -o wide
kubectl get pods -A --no-headers | awk '$4!="Running" && $4!="Completed"'
kubectl -n argocd get applications -o json \
  | jq -r '.items[]|"\(.metadata.name) \(.status.sync.status)/\(.status.health.status)"' | sort
kubectl get --raw /healthz/etcd
```

**2. Copy etcd off the node, because the next step restarts it.** The snapshot is written by whichever
server you run it on, so run it on a server that is not the target. With no SSH, a privileged pod that
mounts the host filesystem is the way in:

```bash
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata: {name: etcd-snapshot-once, namespace: kube-system}
spec:
  nodeName: streaming
  hostNetwork: true
  hostPID: true
  restartPolicy: Never
  tolerations: [{operator: Exists}]
  containers:
    - name: snap
      image: docker.io/rancher/k3s:v1.31.14-k3s1
      securityContext: {privileged: true}
      command: [/bin/sh, -c, "chroot /host /usr/local/bin/k3s etcd-snapshot save --name pre-<version>-<node> && ls -l /var/lib/rancher/k3s/server/db/snapshots | tail -3"]
      volumeMounts: [{name: host-root, mountPath: /host}]
  volumes:
    - {name: host-root, hostPath: {path: /}}
YAML
kubectl -n kube-system logs etcd-snapshot-once
```

Then push it into the off-site repository. Re-running the whole streaming backup job works but reads
9 GiB to upload 60 MiB; a pod that backs up only the snapshot directory takes five seconds:

```bash
# same container spec as the backup job's `restic` container, with:
#   command: sh -c 'restic backup /host/etcd-snapshots --tag etcd-pre131-<node>'
#   volumes: the `etcd-snapshots` hostPath only (/var/lib/rancher/k3s/server/db/snapshots)
#   nodeName: streaming
kubectl -n backup logs <pod>          # "snapshot <id> saved", then `restic ls` names the file
```

The NAS is not an option: `/mnt/storage` and `/mnt/storage/nfs` are handed to pods read-only, and only
the per-claim subdirectories are writable. A copy there would land inside a backed-up stream.

**3. Pin the version *and* the target node** on the plan the node belongs to. Two plans exist:
`server-plan` matches `node-role.kubernetes.io/control-plane In [true]`, `agent-plan` matches
`DoesNotExist` (its `prepare` step waits for `server-plan` to finish).

```bash
kubectl -n system-upgrade get plan server-plan -o jsonpath='{.spec}'
kubectl -n system-upgrade patch plan server-plan --type=merge -p '{"spec":{
  "version":"v1.31.14+k3s1",
  "nodeSelector":{"matchExpressions":[
    {"key":"node-role.kubernetes.io/control-plane","operator":"In","values":["true"]},
    {"key":"kubernetes.io/hostname","operator":"In","values":["nuc0"]}]}}}'
```

`spec.version` overrides `channel`; never leave `channel: stable` as the only selector. The hostname
expression is not decoration — without it the controller upgrades the next matching node the moment the
first one reports the new version.

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
```

Expected: the node on the pinned version and `Ready`, no unhealthy pod that was healthy before, etcd
`ok`, the Argo count unchanged, and every host answering as before. Then patch the plan's hostname
list to the next node and repeat.

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

**GPU survived.** `nvidia-container-toolkit`, the device plugin, `dcgm-exporter` and `intel-gpu-plugin`
all came back, and `nvidia-smi -L` in the Plex pod still lists the Quadro P400.

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

## Rolling back

**A node is one-way in practice.** The plan stores no previous release, and the controller only ever
installs forward: there is no `rollback` verb. Going back means running K3s's installer on that node
with the older `INSTALL_K3S_VERSION` (`/usr/local/bin/k3s` is replaced, the new binary is not kept), or
rebuilding the node. Neither has been rehearsed here, and the 2026-11-21 run had no SSH to the nodes,
so a node that failed to come back would have needed physical or console access.

What limits the damage: etcd keeps quorum on the other two servers while a control plane restarts, the
snapshot in the OVH bucket predates the change, and a worker failing leaves the cluster serving. Plan
the rollback *before* touching the first node.

Restoring etcd from a snapshot is **not** written yet — `restore-etcd.md` is still in the
[runbook index](README.md)'s "not written yet" list and has never been rehearsed.

## After the run

- Both plans stay pinned to the version they reached; the last hostname list names the last node
  upgraded. Move both when the next minor is due (holder: homelab cards 520 and 521).
- Update `AGENTS.md` §7, the plan, and this page with the version as it is now.
- A K3s upgrade does **not** touch the Traefik release. Confirm it is still this repository's chart
  and not a K3s-bundled addon: `kubectl -n kube-system get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].image}'`
  should print the image `values-v3.yaml` pins.
