# Restore from an off-box backup

**Destructive where it says so.** Sections 1 and 3 read only. Section 2's last part replaces a live
application's configuration, and section 4 replaces a server's whole state: both are marked, and
neither belongs in a drill.

> **In one line.** How to get a file, a database or a cluster state back out of the OVH bucket, and
> the drill log that says which of these has actually been tried.
>
> **Use it when** something was deleted, a claim is gone, a database has to be rebuilt, or the
> monthly restore drill is due · **Verified** 2026-11-20, against the bucket's layout; no restore has
> been recorded yet · **Kind:** living page

The nightly jobs write four restic repositories into one bucket. Restic is the tool that reads them:
it holds the file list, the encryption and the history. There is no other way in.

| Repository | Written by | Holds |
|---|---|---|
| `nodes-nas` | `backup-node-nas`, 04:30 | the PostgreSQL dumps (`globals.sql` and `<db>-<UTC>.dump`), Grafana's database |
| `nodes-nuc0` | `backup-node-nuc0`, 05:00 | eleven applications' configuration, the node's `/etc`, and the etcd snapshot copy |
| `nodes-streaming` | `backup-node-streaming`, 05:30 | the media stack's configuration, the node's `/etc`, and the etcd snapshot copy |
| `nfs` | `backup-nfs`, 06:00 | syncthing, the books library, koillection's uploads, the ACME certificate store, the qBittorrent config |

Every snapshot carries `stream=<name>` and `node=<name>`, and the NFS ones also carry
`path=<tag>`. Retention is 7 daily, 4 weekly and 12 monthly, pruned on Sunday at noon: anything older
than three months is gone, and a file that was deleted three weeks ago is still in a weekly snapshot.

## Preconditions

- The restic repository password, from the password manager, by name. It is not in the cluster and
  not in this repository. Without it the bucket is ciphertext.
- `kubectl` access with the ability to create a pod in `backup`.
- The `backup-restic` Secret still exists (it is created by hand, see
  [`../secrets.md`](../secrets.md)), or the three values are typed into the shell below instead.

## Step 0 — a restic shell in the cluster

Restic, the password and the bucket credentials in one pod, so nothing has to be installed on the
machine you are sitting at and the password never reaches a shell history.

```bash
kubectl -n backup apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: restic-shell
spec:
  restartPolicy: Never
  nodeSelector:
    kubernetes.io/hostname: nas
  volumes:
    - name: out
      emptyDir: {}
  containers:
    - name: restic
      image: restic/restic:0.19.1
      command: ["sleep", "7200"]
      envFrom:
        - secretRef:
            name: backup-restic
      env:
        - name: RESTIC_CACHE_DIR
          value: /tmp/restic-cache
      volumeMounts:
        - name: out
          mountPath: /out
EOF
kubectl -n backup wait --for=condition=Ready pod/restic-shell --timeout=120s
kubectl -n backup exec -it restic-shell -- sh
```

Inside the shell, one short name per repository saves typing the URL:

```sh
export NODES_NAS=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nodes-nas
export NODES_NUC0=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nodes-nuc0
export NODES_STREAMING=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nodes-streaming
export NFS=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nfs
restic -r "$NFS" snapshots
```

Success: a table of snapshots with `Host` `nas` and a `Tags` column holding `stream=nfs path=…`.
`wrong password` means the Secret's `RESTIC_PASSWORD` is not the one the repositories were made
with; stop, because no command below can work around it.

Everything is restored into `/out`, the pod's empty scratch volume; nothing is written back to a live
claim from here.

## Section 1 — PostgreSQL

The dump lives in `nodes-nas`, under the `postgres-dumps` claim's directory. Find the exact path
first: the directory name carries the volume's generated id, which changes if the claim is recreated.

```sh
restic -r "$NODES_NAS" ls latest | grep '_databases_postgres-dumps/'
```

Success: lines ending in `globals.sql` and one `<database>-<UTC>.dump` per application database. Restore the
whole directory:

```sh
restic -r "$NODES_NAS" restore latest --target /out --include '/mnt/ssd0/pvc-*_databases_postgres-dumps'
ls -l /out/mnt/ssd0/pvc-*_databases_postgres-dumps/
```

Then copy it out and follow [`postgres-restore.md`](postgres-restore.md) from its Step 2:

```bash
kubectl cp backup/restic-shell:/out/mnt/ssd0 "$PWD/restored-dumps"
```

The order that matters, and the reason it is first in the shared procedure: **restore
`globals.sql` before any database dump.** The dump's objects belong to roles, and a rebuilt cluster
has none of them. `postgres-restore.md` creates the one role its database needs by hand; the globals
file is what carries every role, its password hash and its grants, so the instances that the nightly
job has been dumping since then can be recreated rather than guessed.

Only files ending in `.dump` are good; `postgres-restore.md` explains why, and what a `.partial` file
means.

## Section 2 — an application's configuration

Two kinds of claim, and the difference decides which path to restore:

| Row kind | What was written | Where it is in the snapshot |
|---|---|---|
| `sqlite` | each database copied through SQLite's backup API, plus the claim without its live database files | `/staging/claims/<namespace>/<claim>/…` |
| `files` | the claim directory as it is | its host path, for example `/mnt/ssd0/pvc-<id>_<namespace>_<claim>/…` |

For a `sqlite` claim, the staged copy under `/staging/` is the one to use. The live file was being
written while the job read it, and its `-wal` file may hold changes the database needs: restoring the
raw file can give a database that opens and is missing the last few minutes, or one that does not
open at all.

Example, Sonarr's configuration (a drill can stop after the checks and go to Step 0's teardown):

```sh
restic -r "$NODES_STREAMING" restore latest --target /out --include '/staging/claims/media-stack/sonarr-config'
find /out/staging/claims/media-stack/sonarr-config -name '*.db'
```

Success: `sonarr.db` (and no `sonarr.db-wal`, which the staging step deliberately does not produce).
Copy it out and check that SQLite accepts it and that the rows are there:

```bash
kubectl cp backup/restic-shell:/out/staging/claims/media-stack/sonarr-config/sonarr.db ./sonarr.db
python3 - <<'EOF'
import sqlite3
db = sqlite3.connect("sonarr.db")
print("integrity:", db.execute("pragma integrity_check").fetchone()[0])
print("series:", db.execute("select count(*) from Series").fetchone()[0])
EOF
```

Success: `integrity: ok` and a series count that is plausible against the last thing you watched. The
table name is the application's; `sqlite3 sonarr.db '.tables'` lists them if `Series` is not there.

For a `files` claim, restore the claim directory itself. The comment above its row in
`cluster/helm/backup/values.yaml` says what was worth keeping and what the row excludes; the snapshot
only holds what the row kept.

**Destructive: putting it back on the live claim.** The application must not be writing while the
files are replaced.

```bash
kubectl -n media-stack scale deploy sonarr --replicas=0
kubectl -n media-stack wait --for=delete pod -l app=sonarr --timeout=120s
```

Then start a writer pod that mounts the same claim, pinned to the node that holds it, and copy the
restored files in:

```bash
kubectl -n media-stack apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: sonarr-config-writer
spec:
  restartPolicy: Never
  nodeSelector:
    kubernetes.io/hostname: streaming
  securityContext:
    runAsUser: 0
  containers:
    - name: writer
      image: alpine:3.21
      command: ["sleep", "1800"]
      volumeMounts:
        - name: config
          mountPath: /config
  volumes:
    - name: config
      persistentVolumeClaim:
        claimName: sonarr-config
EOF
kubectl -n media-stack wait --for=condition=Ready pod/sonarr-config-writer --timeout=120s
kubectl cp sonarr.db media-stack/sonarr-config-writer:/config/sonarr.db
kubectl -n media-stack exec sonarr-config-writer -- chown 1100:1100 /config/sonarr.db
kubectl -n media-stack delete pod sonarr-config-writer
```

`1100:1100` is the LinuxServer `PUID`/`PGID` this stack uses; an image that runs as another user
needs that user's ids, and the application's chart `values.yaml` is the authority. Then scale the
application back up, which is the ArgoCD-managed replica count:

```bash
kubectl -n media-stack scale deploy sonarr --replicas=1
kubectl -n media-stack logs -f deploy/sonarr
```

Success: the application starts, its settings and its library are as they were, and the log has no
`database is locked` or `no such table`. If ArgoCD reverts the replica count, use its UI: its sync
policy lives there, not in git.

## Section 3 — a Syncthing file

Read-only, and the quickest of the four, which is why it is the one to run when checking that the
whole chain still works.

```sh
restic -r "$NFS" snapshots --tag path=syncthing
restic -r "$NFS" restore latest --target /out --include '/mnt/storage/nfs/syncthing/<path/of/the/file>'
sha256sum /out/mnt/storage/nfs/syncthing/<path/of/the/file>
```

Compare that hash with the live file, through the application's own pod:

```bash
kubectl -n syncthing exec deploy/syncthing -- sha256sum /data/<path/of/the/file>
```

Success: two identical hashes. A file deleted since the snapshot was taken is exactly the case this
restores: copy it back into `/data` through the same pod, then let Syncthing propagate it.

## Section 4 — etcd (pending, card #8)

Two sentences of context, because this one is unlike the others: an etcd snapshot is the cluster's
entire object state, so restoring it is done into a *test* cluster first, never into the running one.
The rehearsal belongs to the W5 platform work and card #8; this runbook only fixes where the file
comes from and the one thing that makes it readable.

The copy is inside the two control-plane streams, as a directory the node job mounted and handed to
restic:

```sh
restic -r "$NODES_STREAMING" ls latest | grep '^/host/etcd-snapshots/'
restic -r "$NODES_STREAMING" restore latest --target /out --include '/host/etcd-snapshots'
```

Success: one file per night, named `<node>-<unix-time>-<revision>`, around 110–125 MB each.

**The snapshot is encrypted with a key derived from the K3s server token.** A rebuild that installs a
new token produces a snapshot that opens to garbage, so the token from
`/var/lib/rancher/k3s/server/token` — kept in the password manager, see
[`../secrets.md`](../secrets.md) — must be in place *before* `k3s server --cluster-reset
--cluster-reset-restore-path=<file>` runs. Restoring without it is not a partial restore; it is a
server that starts with unreadable Secrets.

Once W5 lands the official S3 upload, the file comes from the bucket directly instead of from inside
this stream, and this section is rewritten with that path. Until then the copy is the bridge, and its
age is what `backup-verify` reports as `etcd stream=… age_h=…`.

## Teardown

```bash
kubectl -n backup delete pod restic-shell
```

The pod's `/out` is an `emptyDir`: it disappears with the pod, which is why everything is copied out
before the pod is deleted. The bucket is untouched by every command above.

## The drill log

One drill a month, rotating through sections 1–3; section 4 belongs to card #8. A drill counts once
its row is written: the point of the table is that the next person can see what has been tried
recently, not what is believed.

| Date (UTC) | Drill | Duration | Result | By |
|---|---|---|---|---|

**No drill has been recorded yet.** The jobs are scheduled and the layout above is checked against
the cluster, but until a row exists here, every restore is unproven.

## When a restore surprises you

- **`restic: wrong password`** — the Secret changed, or was recreated with a new value. The bucket's
  contents are still fine; the old password is in the password manager. Nothing in the cluster can
  repair this.
- **A path is not in the snapshot** — the claim's row was added after that snapshot was taken, or the
  row excludes it. `cluster/helm/backup/values.yaml` is the authority on what a row keeps, and its
  comments say what each exclusion costs.
- **`kubectl cp` fails on the pod** — the image needs `tar`; both images here have it. A file over a
  few hundred MB is better restored inside the pod and copied with `kubectl exec … tar`, in one
  stream, than through `kubectl cp`.
- **The newest snapshot is from weeks ago** — that is the failure `backup-verify` exists to catch;
  read its log with `kubectl -n backup logs job/<the verify job>` before restoring, because an old
  snapshot restores old data and the same fault will hit the next night too.
