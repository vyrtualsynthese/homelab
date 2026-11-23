# Restore the cluster's object state from an etcd snapshot

> **In one line.** Put an etcd snapshot — the cluster's database of every object — back into a K3s
> server, with the token it was taken under, and read the objects out of it.
>
> **Use it when** the control plane is unquorate, one server will not start, or the drill that proves
> the etcd copy restores is due · **Verified** 2026-11-22: a production snapshot restored into a
> throwaway single-node K3s on the workstation and its objects read back, 16 seconds end to end ·
> **Kind:** how-to
>
> **Sources** `docs/how-it-works/backups.md`, `cluster/helm/backup/values.yaml`,
> [`restore.md`](restore.md) section 4, [`../secrets.md`](../secrets.md)

**Destructive where it says so.** The restore replaces the datastore of the server it runs on: every
object that server holds ends up as the snapshot has it, and anything written since the snapshot is
gone. A rehearsal runs on a throwaway server that holds nothing else. On a live server it is the
last-resort move, and the reason it is rehearsed first.

An etcd snapshot is not a file backup. It holds the definition of the cluster — every namespace,
Deployment, Secret, ArgoCD Application and PVC object — and none of the bytes those objects point
at: no claim's contents, no NFS export, not the certificate store. A rebuilt cluster has its objects
back and its applications still empty; the claims come back from the restic streams, by
[`restore.md`](restore.md), and the two halves are independent.

## Preconditions

- **The snapshot file.** One file per server per 12 hours, inside the `nodes-streaming` and
  `nodes-nuc0` restic repositories, under `/host/etcd-snapshots/`. The official K3s upload to the
  bucket's `etcd/` prefix has not landed: there is no `etcd/` prefix in the bucket and no
  `k3s-etcd-snapshot-s3-config` Secret in `kube-system` (read on 2026-11-22). Until it does, the
  copy in the restic stream is the only one, and its age is what `backup-verify` reports as
  `etcd stream=… age_h=…`.
- **The K3s server token.** It is what the snapshot was taken under, and the restore refuses to
  finish without it. It is in the password manager, item `homelab-backup-last-resort`, field
  `K3S_SERVER_TOKEN`; [`../secrets.md`](../secrets.md) lists it with the other three values the
  bucket needs. Its shape is `K10<…>::server:<…>`, 108 characters here.
- **A K3s binary of the same version as the snapshot**, `v1.31.14+k3s1` for the files the cluster
  writes today. Being generous, any `1.31.x` reads them; being strict costs one download.
- **A machine that is not a cluster host**, with about 1 GB free. The rehearsal in the drill log ran
  in a privileged container on the workstation, under WSL2, with no KVM and no VM image.

## Step 1 — pull the snapshot out of the bucket

From a restic shell in the cluster, the one [`restore.md`](restore.md) Step 0 creates, or from a
laptop with [`last-resort.md`](last-resort.md)'s four values when there is no cluster at all:

```sh
export NODES_STREAMING=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nodes-streaming
restic -r "$NODES_STREAMING" ls latest | grep '^/host/etcd-snapshots/'
restic -r "$NODES_STREAMING" restore latest --target /out --include '/host/etcd-snapshots'
ls -l /out/host/etcd-snapshots/
```

Success: one file per 12-hour run, named `etcd-snapshot-<server>-<unix seconds>`, plus a
`pre-131-*` file per server from the 1.31 move. Measured sizes on 2026-11-22: 41–80 MB per file, nine
files, 514.8 MiB in total, restored in 3 seconds.

The numbers are the snapshot's **creation** time, in Unix seconds; `date -u -d @1795348801` turns
one back into `2026-11-22 12:00:01Z`. Take the newest file from a control-plane server, which is
`streaming` or `nuc0` here: `nas` is a worker and `rbp2` a control plane that may be behind, and the
newest object state is the point of a restore.

The file is a plain etcd database — `xxd -l 32` shows the bbolt magic bytes `edda0ced` — zipped only
when `etcd-snapshot-compress` is on, which it is not. Copy it as it is; nothing has to be opened
before the server sees it.

## Step 2 — settle the token before anything starts

The datastore carries a copy of the cluster's bootstrap data — the cluster CA, the service-account
keys — encrypted under a key derived from the server token. A restore on a server with a different
token gets the database back and then stops:

```
level=fatal msg="bootstrap data already found and encrypted with different token"
```

That was measured twice. The rule that follows: **the token is passed to the reset**, with `--token`
or in `K3S_TOKEN`. Three things were measured on the way there, and each costs a run if it is
rediscovered:

| Attempt | Result |
|---|---|
| `--cluster-reset` with a token file placed by hand at `/var/lib/rancher/k3s/server/token` | Fails the same way. K3s generates its own token on start and **overwrites that file** (108 bytes in, a different 108 bytes out, compared by hash) |
| `--cluster-reset --token-file=…` | `fatal msg="/var/lib/rancher/k3s/server/token does not exist, please pass --token to complete the restoration"` — the reset does not read `--token-file` |
| `--token "$TOK"` | Restores, decrypts the bootstrap data, exits 0 with the message below |

So, from the vault:

```sh
read -r -s K3S_TOKEN   # paste the K3S_SERVER_TOKEN value, then Enter
export K3S_TOKEN
```

`K3S_TOKEN` in the environment is the same value as `--token`, without putting it in `ps` output or
a shell history.

## Step 3 — the reset and the restore

Two shapes, depending on what the target server already holds. Both end with a single-member etcd
holding the snapshot's objects.

**A server with no datastore** — a rebuilt node, or the throwaway. One command, and it exits by
itself:

```sh
k3s server --cluster-reset --cluster-reset-restore-path=/out/host/etcd-snapshots/<file>
```

**A server that already runs** — this node's own datastore is damaged and is being replaced. Stop
K3s first, because the reset takes the etcd port:

```sh
sudo systemctl stop k3s
sudo k3s server --cluster-reset --cluster-reset-restore-path=/out/host/etcd-snapshots/<file>
sudo systemctl start k3s
```

The rehearsal used neither `systemctl` nor the installer: it ran the release binary
`v1.31.14+k3s1` in a `rancher/k3s:v1.31.14-k3s1` container, privileged, with a named volume for
`/var/lib/rancher/k3s` and the snapshot directory mounted read-only. That is the same process with
the same data directory, and it leaves the workstation with nothing installed.

Success, in either shape:

```
msg="restored snapshot" path=/…/<file> …
msg="kvstore restored" current-rev=1228163651
msg="Removed member from the backend"            (once per old member — five here)
level=fatal msg="… Managed etcd cluster membership has been reset, restart without --cluster-reset flag now. Backup and delete ${datadir}/server/db on each peer etcd server and rejoin the nodes"
```

That last line is the normal ending, not a failure: an exit code of 0 with a deliberately fatal
message, because the reset is only half the job. Start the server normally — without
`--cluster-reset`, with the same token — and it comes up on the restored data.

## Step 4 — read the objects back

Wait for the API, then check that what came back is the snapshot's state and not a fresh cluster:

```sh
kubectl get ns
kubectl get nodes -o wide
kubectl get applications -n argocd | wc -l
kubectl get deploy,sts,pvc,secret -A --no-headers | wc -l
```

What the drill read, from the `streaming` snapshot of 2026-11-22 12:00:01Z:

| Read | Value |
|---|---|
| namespaces | 25, including `argocd`, `media-stack`, `monitoring`, `vikunja`, `authentik` |
| Deployments / StatefulSets / PVCs / pods | 66 / 3 / 47 / 115 |
| Secrets | 376, `backup-restic` among them |
| ArgoCD Applications | 60, `sonarr` reading `Synced Healthy` |
| nodes | `nas`, `nuc0`, `rbp2`, `streaming`, all `v1.31.14+k3s1`, with ages of 2–3 years |

Two checks are worth more than the counts:

- **The node objects are stale on purpose.** `kubectl get node streaming -o
  jsonpath='{.status.conditions[?(@.type=="Ready")].lastHeartbeatTime}'` printed
  `2026-11-22T11:55:25Z`, the last heartbeat before the snapshot at 12:00Z. `Ready` on those four
  nodes is a stored status, not a live one: nothing is running on them in this cluster.
- **A Secret comes back byte-identical.** Compare one whose value exists elsewhere, rather than
  opening it:

  ```sh
  kubectl -n backup get secret backup-restic -o jsonpath='{.data.RESTIC_PASSWORD}' \
    | base64 -d | tr -d '\n' | sha256sum
  tr -d '\n' < ~/.config/secrets/restic-password | sha256sum
  ```

  The drill compared those two hashes: identical. That is what "the token was right" buys — the
  objects are the originals, readable, not a decrypted-looking shell.

On a fresh server, write a ConfigMap before the restore and look for it after: it is gone, which
proves the restore replaced the datastore rather than merging into it.

## Step 5 — the single-member caveat

After a reset the etcd has **one member**, this server. The removal lines in Step 3 are the other
servers being taken out of the membership; their node objects stay in the restored state, `Ready`
with an old heartbeat, until they rejoin.

- A real restore is therefore finished in two more steps on the other control-plane servers:
  delete `/var/lib/rancher/k3s/server/db` on each, then start K3s and let it rejoin. K3s's own
  message says exactly that. The datastore on those servers is the *old* etcd, and leaving it is how
  an old member with old data rejoins.
- **Never wipe two control-plane servers at once.** etcd needs a majority of its members — two of
  three — to accept a write, and one server does not make a majority of three. The order is: restore
  one, let it serve, then the second, then the third.
- The restored node re-registers itself under its own hostname: the drill's server appeared as a new
  node about 20 seconds after the start, beside the four restored objects.
- The old `k3s-etcd-snapshot-*` objects and the `EtcdSnapshotFile` custom resource are the snapshot
  bookkeeping, not the data. A restore does not need them and does not remove them.

## Measured times

On the workstation under WSL2 (28 vCPU, 31 GB), single-node `v1.31.14+k3s1`, snapshot of 49 MB:

| Step | Time |
|---|---|
| restic restore of the whole etcd directory, 514.8 MiB | 3 s |
| `--cluster-reset` and the restore, fresh datastore | 7 s |
| `--cluster-reset` and the restore, existing datastore | 7 s |
| start → `kubectl get ns` answers | 9–11 s |
| start → the API serves the restored objects | ~16–18 s from a stopped server |

`kvstore restored current-rev=1228163651`, and the defragment that follows does not grow the
database: 49,401,888 bytes in, 27,324,416 in use out.

## Teardown

The rehearsal leaves nothing behind:

```sh
docker rm -f k3s-drill
docker volume rm k3s-drill
rm -rf /out/host/etcd-snapshots
```

On a real server there is nothing to uninstall: the datastore is the one that was just restored, and
the other servers are rejoined as above. A throwaway installed with the script uninstalls with
`/usr/local/bin/k3s-uninstall.sh` from the same version.

## Traps

| Symptom | Cause |
|---|---|
| `bootstrap data already found and encrypted with different token` after `kvstore restored` | The token is missing or wrong. Pass the vault's `K3S_SERVER_TOKEN` with `--token`/`K3S_TOKEN`; a file at `/var/lib/rancher/k3s/server/token` is not enough and is overwritten |
| the retry fails exactly the same way | The failed attempt left the restored database plus local bootstrap data under the wrong token. Delete `${datadir}/server/db` before retrying, as the message says |
| `/var/lib/rancher/k3s/server/token does not exist, please pass --token` | `--token-file` on the reset path. Use `--token` |
| `this server is a not a member of the etcd cluster. Found […172.17.0.3…], expect: …172.17.0.2…` | The container's address changed between the reset and the start. Only a container problem: run the drill with `--network host`, or one container for both steps. A real node's address is stable |
| `error: the server doesn't have a resource type "etcdsnapshots"` | The CRD is `etcdsnapshotfiles.k3s.cattle.io`, and on a restored cluster it is present only if the snapshot held it |
| the API answers `503` for minutes and etcd logs a membership mismatch | A datastore restored on one address and started on another, same as above; check the peer URL in the log against the container's IP |

## Rollback

None on the server the restore ran on: the datastore was replaced, and the file it replaced is not
kept. That is why a rehearsal runs on a throwaway and a real restore is taken from the newest
snapshot after the decision is made. Before a real restore, copy the server's current
`/var/lib/rancher/k3s/server/db` aside — it is the only copy of the state being replaced, and it is
sometimes readable enough to recover the objects written since.

## Recording the drill

One row per rehearsal, dated in UTC. The row and its `restoreDrills` entry in
`cluster/helm/monitoring/prometheus/values.yaml` change together, or `RestoreDrillOverdue` cannot
see the drill.
