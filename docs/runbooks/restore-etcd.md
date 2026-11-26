# Restore the cluster's object state from an etcd snapshot

> **In one line.** Put an etcd snapshot — the cluster's database of every object — back into a K3s
> server, with the token it was taken under, read the objects out of it, then close the access the
> restore reopened.
>
> **Use it when** the control plane is unquorate, one server will not start, or the drill that proves
> an etcd snapshot restores is due · **Verified** 2026-11-26: the newest production snapshot restored
> twice into a throwaway `v1.36.6+k3s1` on the workstation, 6 seconds per reset, and the access
> re-check proved against deliberate drift · **Kind:** how-to
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

A snapshot is also an authorisation state from the past. A ServiceAccount, RoleBinding,
ClusterRoleBinding or Secret deleted after it was taken comes back with the restore, and a Secret
rotated since comes back holding the value it had then. Steps 3 and 6 are the pair that finds and
closes what the restore reopened.

## Preconditions

- **The snapshot file.** Each control-plane server uploads its own snapshot to the bucket's `etcd/`
  prefix every six hours, named `etcd-snapshot-<server>-<unix seconds>.zip`. The bucket is the one
  the restic repositories live in, reached with the same OVH key pair, and `backup-verify` reports
  each server's newest upload as `etcd-s3 server=<name> file=… age_h=…`. A server that stops
  uploading disappears from that line, which is what the check exists for.
  The restic streams carried a copy of the same directory from 2026-11-20 to 2026-11-25, as the
  bridge until this landed; those copies are still in the `nodes-nuc0` and `nodes-streaming`
  snapshots the monthly tail keeps, and are still restorable, but nothing writes them any more.
- **The K3s server token.** It is what the snapshot was taken under, and the restore refuses to
  finish without it. It is in the password manager, item `homelab-backup-last-resort`, field
  `K3S_SERVER_TOKEN`; [`../secrets.md`](../secrets.md) lists it with the other three values the
  bucket needs. Its shape is `K10<…>::server:<…>`, 108 characters here.
- **A K3s binary of the same version as the snapshot**, `v1.36.6+k3s1` for the files the cluster
  writes today. Being generous, any `1.36.x` reads them; being strict costs one download.
- **A machine that is not a cluster host**, with about 1 GB free. The rehearsal in the drill log ran
  in a privileged container on the workstation, under WSL2, with no KVM and no VM image.

## Step 1 — pull the snapshot out of the bucket

The key pair is the restic one (item `homelab-backup-last-resort`); the prefix holds the servers'
own uploads and nothing else:

```sh
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
aws --endpoint-url https://s3.gra.io.cloud.ovh.net s3 ls s3://ashudev-homelab-backup/etcd/
aws --endpoint-url https://s3.gra.io.cloud.ovh.net s3 cp \
  s3://ashudev-homelab-backup/etcd/<file> .
```

Success: one object per server per six-hour run, `etcd-snapshot-<server>-<unix seconds>.zip`, 6–7 MB
each with compression on (measured 2026-11-25, three servers within a minute of each other).

The number in the name is the snapshot's **creation** time, in Unix seconds; `date -u -d @1795569627`
turns one back into `2026-11-25 01:20:27Z`. All three servers hold the same object state, so the
newest object from any of them is the one to take — `nas` is a worker and never uploads.

The file is a zipped etcd database, because `etcd-snapshot-compress` is on in
`/etc/rancher/k3s/config.yaml`. K3s decompresses it for the reset itself, so it is handed to
`--cluster-reset-restore-path` exactly as downloaded; `unzip -l` lists its single member if it has
to be inspected first.

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

## Step 3 — record the access surface before the restore

A restore puts back the snapshot's authorisation state, not today's. So the state it is about to
replace is written down first, while the API still answers:

```sh
mkdir -p /out/auth
kubectl get sa,rolebinding,clusterrolebinding -A -o json \
  | jq -r '.items[] | [.kind, (.metadata.namespace // "-"), .metadata.name,
      (.roleRef.kind // "-"), (.roleRef.name // "-"),
      ([.subjects[]? | "\(.kind)/\(.namespace // "-")/\(.name)"] | join(","))] | @tsv' \
  | sort > /out/auth/rbac-before.tsv

kubectl get secret -A -o json \
  | jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name)\t\(.data // {} | to_entries
      | sort_by(.key) | map("\(.key)=\(.value)") | join(","))"' \
  | while IFS="$(printf '\t')" read -r name data; do
      printf '%s\t%s\n' "$(printf '%s' "$data" | sha256sum | cut -d' ' -f1)" "$name"
    done | sort -k2 > /out/auth/secrets-before.tsv
```

Success: two files of one line per object. The first carries each object's kind, namespace, name and
`roleRef` — a dash where it has none — then its subjects as `kind/namespace/name`. The second hashes
each Secret's data map, key by key; the values stay base64 and only a digest is written, so the file
can go in the drill record.

If the control plane no longer answers — the usual reason a restore is needed — the capture cannot be
taken. The before side is then the last read of it that exists: the Secret inventory in
[`../secrets.md`](../secrets.md), the ArgoCD tree in git, a copy of these files from an earlier drill.
Say in the record which side came from where; a remembered before side makes the diff a guess.

## Step 4 — the reset and the restore

Two shapes, depending on what the target server already holds. Both end with a single-member etcd
holding the snapshot's objects.

**A server with no datastore** — a rebuilt node, or the throwaway. One command, and it exits by
itself:

```sh
k3s server --cluster-reset --cluster-reset-restore-path=/out/<file>
```

**A server that already runs** — this node's own datastore is damaged and is being replaced. Stop
K3s first, because the reset takes the etcd port:

```sh
sudo systemctl stop k3s
sudo k3s server --cluster-reset --cluster-reset-restore-path=/out/<file>
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

The `Removed member` lines are not a fixed part of the output: `v1.31.14` printed five on 2026-11-22,
and `v1.36.6` printed none on either reset of the 2026-11-26 drill, where the start cleared the
member-removal annotations from the restored node objects instead. Judge the reset by
`restored snapshot` and `kvstore restored`, not by the removals.

## Step 5 — read the objects back

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

## Step 6 — close what the restore reopened

Take the same two reads from the restored cluster, with `after` in place of `before` in the file
names, then compare:

```sh
diff -u /out/auth/rbac-before.tsv /out/auth/rbac-after.tsv
diff -u /out/auth/secrets-before.tsv /out/auth/secrets-after.tsv
```

Read each line from the restore's side:

| Diff | What happened | What to do |
|---|---|---|
| only in `after` | The snapshot had it and today's cluster did not: the restore **re-created** a path that was closed on purpose | Close it again. When a chart owns it the deletion goes in git — ArgoCD puts back anything still declared, so a deletion made only on the cluster is undone by the next sync |
| in both, Secret hash differs | The Secret was rotated after the snapshot, and the restore **rolled it back** to the old value | Rotate it again from wherever the current value lives. The hash says which Secret came back old; it never holds the value |
| only in `before` | It was created after the snapshot, and the restore **removed** it | Recreate it if a workload needs it. That is ordinary post-restore work, not a revocation |
| no output | Nothing was closed after the snapshot was taken | The clean answer, and the one worth recording |

Close the reopened paths before Step 7 brings the other control-plane servers back and workloads
resume: an access path is usable from the moment the API answers, and a ServiceAccount token signed
by the restored cluster is accepted by it.

The diff compares the cluster with its own recent past, which is all it can see — a path that was
already open when the snapshot was taken stays open and produces no line. It is not a review of the
snapshot's security, only a list of what changed between the snapshot and the restore.

The restoring node re-issues its own credentials when it starts on the restored datastore, so
`kube-system/k3s-serving` and `kube-system/<hostname>.node-password.k3s` differ on every restore.
They are not a revocation: the node's values moved because the node came up, not because anything was
rotated.

## Step 7 — the single-member caveat

After a reset the etcd has **one member**, this server. The removal lines in Step 4 are the other
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
rm -rf /out
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
