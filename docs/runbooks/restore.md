# Restore from an off-box backup

**Destructive where it says so.** Sections 1 and 3 read only. Section 2's last part replaces a live
application's configuration, and section 4 replaces a server's whole state: both are marked.
Section 2's put-back was drilled on 2026-11-22; section 4 was rehearsed the same day in
[`restore-etcd.md`](restore-etcd.md).

> **In one line.** How to get a file, a database or a cluster state back out of the OVH bucket, and
> the drill log that says which of these has actually been tried.
>
> **Use it when** something was deleted, a claim is gone, a database has to be rebuilt, or the
> monthly restore drill is due · **Verified** 2026-11-22: sections 1–3 restored and read back, Sonarr
> stopped and restarted from a snapshot put back onto its live claim (32 s of downtime), the etcd
> snapshot restored into a throwaway single-node K3s, all four repositories read from a laptop
> outside the cluster · **Kind:** how-to
>
> **Sources** `cluster/helm/backup/values.yaml`, `docs/how-it-works/backups.md`, `docs/secrets.md`,
> `docs/reference/alerts.md`

The nightly jobs write four restic repositories into one bucket. Restic is the tool that reads them:
it holds the file list, the encryption and the history. There is no other way in.

**If the cluster itself is gone** — no restic pod, no `backup-restic` Secret, no `kubectl` — this page
does not apply. Take [`last-resort.md`](last-resort.md): a laptop, the vault and the bucket, and the
bucket proved before anything is pulled.

| Repository | Written by | Holds |
|---|---|---|
| `nodes-nas` | `backup-node-nas`, 04:30 | the PostgreSQL dumps (`globals.sql` and `<db>-<UTC>.dump`), Grafana's database |
| `nodes-nuc0` | `backup-node-nuc0`, 05:00 | ten claims from nine applications and the node's `/etc` |
| `nodes-streaming` | `backup-node-streaming`, 05:30 | the media stack's configuration and the node's `/etc` |
| `nfs` | `backup-nfs`, 06:00 | syncthing, koillection's uploads, the ACME certificate store, the qBittorrent config |

A node job's snapshot carries `stream=<name>` and `node=<name>`; an NFS one carries `stream=nfs`
and `path=<tag>` instead of a node, because it is one job reading `nas` directly rather than a
per-node stream. Retention is 7 daily, 4 weekly and 12 monthly for the three node repositories and
7 daily, 4 weekly, 24 monthly and 10 yearly for `nfs`, pruned on Sunday at noon: on a node repository
anything older than three months is gone, and a file deleted three weeks ago is still in a weekly
snapshot. On `nfs` a deleted photo stays recoverable for ten years.

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

Two kinds of claim, and the difference decides which path to restore; a `sqlite` one is
written in two places:

| Row kind | What was written | Where it is in the snapshot |
|---|---|---|
| `sqlite` | each database, copied through SQLite's backup API — **the databases only**, nothing else | `/staging/claims/<namespace>/<claim>/<path/of/the/db>` |
| `sqlite` | the claim directory without its live database files and without the row's `exclude` entries | its host path, for example `/mnt/ssd0/pvc-<id>_<namespace>_<claim>/…` |
| `files` | the claim directory as it is | its host path |

A `sqlite` app therefore needs **both** halves to be rebuilt: the database from `/staging/`, the rest
of its configuration from the claim's host path. The two land in the same snapshot, so two
`--include` flags on one `restore` brings them back together.

For a `sqlite` claim, the staged copy under `/staging/` is the one to use. The live file was being
written while the job read it, and its `-wal` file may hold changes the database needs: restoring the
raw file can give a database that opens and is missing the last few minutes, or one that does not
open at all.

Example, Sonarr's configuration (a drill can stop after the checks and go to Step 0's teardown). Its
claim id is the directory name in the path list, so find it first:

```sh
restic -r "$NODES_STREAMING" ls latest | grep '_media-stack_sonarr-config/' | head -3
restic -r "$NODES_STREAMING" restore latest --target /out \
  --include '/staging/claims/media-stack/sonarr-config' \
  --include '/mnt/ssd0/pvc-<id>_media-stack_sonarr-config'
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
files are replaced. Two things about this stack would trip a first attempt: the deployment's pods carry
`app.kubernetes.io/instance=sonarr`, not `app=sonarr`, so a `kubectl wait` on the wrong selector
returns at once and the swap starts under a running application; and every Application in this
cluster carries `syncPolicy.automated` with `selfHeal` from the UI (git records none), so a
scaled-down Deployment is reverted inside the controller's reconcile interval — mid-swap, in the
worst case.

Pause the automation first — patching the Application is what its UI toggle does, and it is undone
below:

```bash
kubectl -n argocd get application sonarr -o jsonpath='{.spec.syncPolicy}' > /tmp/sonarr-syncpolicy.json
kubectl -n argocd patch application sonarr --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
kubectl -n media-stack scale deploy sonarr --replicas=0
kubectl -n media-stack wait --for=delete pod -l app.kubernetes.io/instance=sonarr --timeout=120s
```

The writer pod mounts the claim on the node that holds it, and the restored tree is staged in it
**before** the application is stopped, so the swap itself is seconds:

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
      command: ["sleep", "3600"]
      volumeMounts:
        - name: config
          mountPath: /config
  volumes:
    - name: config
      persistentVolumeClaim:
        claimName: sonarr-config
EOF
kubectl -n media-stack wait --for=condition=Ready pod/sonarr-config-writer --timeout=120s
kubectl cp ./restored/. media-stack/sonarr-config-writer:/tmp/staged
```

The swap: the old tree moves aside **inside the claim** — never out of it, never deleted — which is a
rename on the same filesystem, and the restored tree is copied in its place.

```bash
kubectl -n media-stack exec sonarr-config-writer -- sh -c '
set -e
cd /config
mkdir -p /config/_drill-aside-20261122
for f in /config/*; do case "$f" in /config/_drill-aside-*) ;; *) mv "$f" /config/_drill-aside-20261122/ ;; esac; done
for f in /config/.[!.]*; do [ -e "$f" ] || continue; case "$f" in /config/_drill-aside-*) ;; *) mv "$f" /config/_drill-aside-20261122/ ;; esac; done
cp -a /tmp/staged/. /config/
rm -f /config/sonarr.pid
mv /config/_drill-aside-*/MediaCover /config/MediaCover
chown -R 1100:1100 /config
'
```

Four details in that block are deliberate:

- `MediaCover` is excluded from the backup as rebuilt art. Moving it back out of the aside spares a
  hundred poster refetches; it is not configuration, so its absence would prove nothing.
- `sonarr.pid` comes back stale. Sonarr overwrites a pid file whose process is gone, but deleting it
  removes the question.
- `logs/`, `logs.db` and `Backups/` stay in the aside: the application recreates them, and the row
  excludes them on purpose.
- `1100:1100` is the LinuxServer `PUID`/`PGID` this stack uses; an image that runs as another user
  needs that user's ids, and the application's chart `values.yaml` is the authority.

Start it again, then put the automation back:

```bash
kubectl -n media-stack scale deploy sonarr --replicas=1
kubectl -n media-stack wait --for=condition=Ready pod -l app.kubernetes.io/instance=sonarr --timeout=180s
kubectl -n argocd patch application sonarr --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{"enabled":true,"prune":true,"selfHeal":true}}}}'
```

Success, in this order — the checks are the ones run on 2026-11-22:

- `/api/v3/system/status` answers `200` with the key from `/config/config.xml`.
- `/api/v3/series` returns the number of series the restored database holds, and their
  `statistics.episodeFileCount` sums to the database's `EpisodeFiles` count.
- `/api/v3/health` says nothing about the database or the root folders.
- `deploy/sonarr` logs no `database is locked` and no `no such table`.
- `/config/sonarr.db` has a modification time after the start: the application is writing to the file
  it was given. ArgoCD returns to `Synced Healthy`, and the pod shows `RESTARTS 0`.

Rollback if a check fails: move the aside tree back the same way, or drop it and restore the same
snapshot again. Once the checks pass the aside can go — everything it holds that is worth keeping is
in the snapshot, and the rest is what the row excludes:

```bash
kubectl -n media-stack exec sonarr-config-writer -- rm -rf /config/_drill-aside-20261122
kubectl -n media-stack delete pod sonarr-config-writer
```

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

## Section 4 — etcd

An etcd snapshot is the cluster's entire object state, so restoring it is done into a *test* cluster
first, never into the running one. The procedure, the token requirement, the single-member caveat
and the measured times are [`restore-etcd.md`](restore-etcd.md); this section keeps only where the
file comes from.

Each control-plane server uploads its own snapshot to the bucket's `etcd/` prefix every six hours,
so the file is fetched from the bucket rather than out of a restic repository:

```sh
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
aws --endpoint-url https://s3.gra.io.cloud.ovh.net s3 ls s3://ashudev-homelab-backup/etcd/
aws --endpoint-url https://s3.gra.io.cloud.ovh.net s3 cp \
  s3://ashudev-homelab-backup/etcd/<file> .
```

Success: one object per server per six-hour run, `etcd-snapshot-<server>-<unix seconds>.zip`,
6–7 MB each (measured 2026-11-25). All three servers hold the same object state; `nas` is a worker
and never uploads.

**The snapshot opens only under the token it was taken with.** It is the value in
`/var/lib/rancher/k3s/server/token` on the servers, kept in the password manager, see
[`../secrets.md`](../secrets.md), and it has to be passed to the reset with `--token` or
`K3S_TOKEN`: a file placed at that path by hand is overwritten when K3s starts. Without the right
token the restore writes the database and then stops with `bootstrap data already found and
encrypted with different token` — not a partial restore, a server whose bootstrap data will not
open. [`restore-etcd.md`](restore-etcd.md) Step 2 has the three failed attempts and the one that
works.

The two control-plane jobs copied the snapshot directory into their restic streams until 2026-11-25,
as the bridge until this upload landed. Those copies are still in the snapshots the monthly tail
keeps and can still be restored, but no night adds one, and `backup-verify` now reports the upload
as `etcd-s3 server=<name> file=… age_h=…` instead of reading a copy out of the stream.

## Section 5 — an edge Pi's own state

The two Pis write their own repositories, so this is the one restore that needs no cluster at all: a
machine with restic and the bucket password is enough ([`last-resort.md`](last-resort.md)).

```sh
export RESTIC_REPOSITORY=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/edge-rbp0
export RESTIC_PASSWORD=…            # the homelab-backup-last-resort item
restic snapshots --tag stream=edge-rbp0 --latest 1
restic restore latest --target /tmp/pi-restore
```

The target keeps the path the snapshot stored, so the tree lands at
`/tmp/pi-restore/var/lib/pi-backup/tree`. Hand that directory to `restore` on the host — the
directory holding `dockers/`:

```sh
sudo /usr/local/sbin/hosts-pi.sh restore /tmp/pi-restore/var/lib/pi-backup/tree <host>
sudo /usr/local/sbin/hosts-pi.sh verify <host>
```

The restore passes when the four things a rebuild needs are there, not when the tree matches the live
one byte for byte — `docker-ps.txt`, `host.txt` and the three exclusions make the two differ by
design:

1. `dockers/etc-pihole/gravity.db` opens and `pragma integrity_check` answers `ok` (with a non-zero
   `select count(*) from domainlist`, so an empty database does not pass);
2. `dockers/config/wg-easy.db` opens and `pragma integrity_check` answers `ok`;
3. `dockers/pihole.env` is present: the panel password is in it;
4. `boot/` holds both `config.txt` and `cmdline.txt`.

**Verified 2026-11-26:** not yet restored — the first run of this section is the drill that makes it
`proved`.

## Teardown

```bash
kubectl -n backup delete pod restic-shell
```

The pod's `/out` is an `emptyDir`: it disappears with the pod, which is why everything is copied out
before the pod is deleted. The bucket is untouched by every command above.

## The drill log

One drill a month, rotating through sections 1–5. A drill counts once its row is written: the point
of the table is that the next person can see what has been tried recently, not what is believed.

| Date (UTC) | Drill | Duration | Result | By |
|---|---|---|---|---|
| 2026-11-21 | Section 1 — PostgreSQL dumps (`nodes-nas`) | ~3 min | 26 dumps plus `globals.sql` (7 `CREATE ROLE`) restored from snapshot `e36ab944` into `/out`. The first attempt found no `globals.sql`: the roles capture landed after that morning's dump, so a dump and a `nodes-nas` run were made first and the restore repeated | opencode (agent) |
| 2026-11-21 | Section 2 — Sonarr's database (`nodes-streaming`) | ~1 min | `sonarr.db` restored from `/staging` (42.9 MiB, no `-wal` beside it), `pragma integrity_check` → `ok`, 103 series and 6 212 episodes | opencode (agent) |
| 2026-11-21 | Section 3 — a Syncthing file (`nfs`) | ~1 min | `Photos/Argentique/IMG_20240121_0008.jpg` restored from snapshot `173c4caa`; sha256 `a90eb14a…` identical to the live file read through the Syncthing pod | opencode (agent) |
| 2026-11-22 | Section 2 — Sonarr put back on its live claim (`nodes-streaming`) | ~11 min, 32 s of that downtime | Snapshot `7ac41aa9` (05:30 Paris): `sonarr.db` 45 096 960 B from `/staging` with `pragma integrity_check` → `ok`, 103 series / 6 213 episodes / 566 files / 3 983 history rows, and the claim tree (`config.xml`, `asp/`, `Sentry/`) from its host path. ArgoCD automation paused on the Application, Deployment scaled to 0 at 18:54:26Z, old tree moved to `_drill-aside-20261122` (93.3 MiB), restored tree copied in with `MediaCover` moved back, `chown 1100:1100`; ready again at 18:54:58Z. `/api/v3/system/status` `200`, `/api/v3/series` 103 with 566 episode files, `/api/v3/health` clean apart from the pre-existing Cathode-Ray.Tube indexer warning, no `database is locked` or `no such table`, `sonarr.db` mtime advancing, ArgoCD `Synced Healthy` and `RESTARTS 0`. Aside removed once the checks passed; rollback is the same snapshot, restored again | opencode (agent) |
| 2026-11-22 | Outside the cluster — all four repositories ([`last-resort.md`](last-resort.md) Step 1) | ~2 min | No cluster consulted: `restic` 0.18 on a laptop listed `nodes-nas` `44e80ca9` / 108.2 MiB, `nodes-nuc0` `80f5025f` / 811.2 MiB, `nodes-streaming` `d434572d` / 652.0 MiB, `nfs` `c517d884` / 91.0 MiB, and decrypted a tree from `nfs` (`qbittorrent-config`). The password and the S3 pair came from the Bitwarden item `homelab-backup-last-resort` with the on-disk copies moved aside. That item was written from this machine the same morning, so the run proves the credentials and the procedure, not yet a second machine. The `restore` in that run left a lock behind (PID 1222890 on `Val-Gaming`, written 02:20:56Z) that nobody released: `backup-prune` failed on it at noon the same day, and it took a scratch pod and an `unlock` to clear | opencode (agent) |
| 2026-11-22 | Section 4 — etcd snapshot into a throwaway K3s ([`restore-etcd.md`](restore-etcd.md)) | ~4 min, 16 s of it the restore itself | Snapshot `etcd-snapshot-streaming-1795348801` (49 401 888 B, taken 12:00:01Z) restored from `nodes-streaming` snapshot `57c1df7d` into `v1.31.14+k3s1` in a privileged container on the workstation under WSL2. `--cluster-reset` 7 s, start → API 9 s; a marker ConfigMap from before the restore was gone; 25 namespaces, 66 Deployments, 3 StatefulSets, 47 PVCs, 115 pods, 376 Secrets, 60 ArgoCD Applications came back; `streaming`'s Ready heartbeat read `2026-11-22T11:55:25Z`; `backup-restic`'s `RESTIC_PASSWORD` byte-identical to the local copy. Without the vault token the restore stops at `bootstrap data already found and encrypted with different token` | opencode (agent) |
| 2026-11-26 | Section 4 — the snapshot restored twice, to prove the access re-check ([`restore-etcd.md`](restore-etcd.md) Steps 3 and 6) | ~2 min, 6 s per reset | Snapshot `etcd-snapshot-streaming-1795712405.zip` (8 615 061 B, taken 17:00:05Z) restored into `v1.36.6+k3s1` in a privileged container, host network, `--disable traefik,servicelb`. That restored state was then drifted — a `drill-revoked` namespace with a ServiceAccount, RoleBinding and Secret, `opencode-mcp-view` deleted, a key added to `argocd-initial-admin-secret` — and the same snapshot restored again. The two diffs, 283 RBAC lines and 363 Secret hashes, named all three cases: `opencode-mcp-view` in the restored state only, the three `drill-revoked` objects in the pre-restore read only, the admin Secret's hash rolled back. `k3s-serving` and `k3s-drill.node-password.k3s` also differed, the restoring node re-issuing its own credentials at start | opencode (agent) |

**Recorded 2026-11-22.** Sections 1–3 have each been restored and read back, section 2 has now been put
back onto the live claim and served from it, section 4 has been restored into a throwaway K3s with its
objects read back, and the bucket opens from a laptop with no cluster at all.

The table above is the human record; the alert reads a machine-readable copy. `restoreDrills` in
`cluster/helm/monitoring/prometheus/values.yaml` holds one entry per row (Unix seconds;
`date -u -d 2026-11-21 +%s` produces one), node-exporter serves them as
`homelab_restore_drill_timestamp_seconds`, and `RestoreDrillOverdue` warns when the newest row is
more than 90 days old — three missed monthly drills. A row added here without the matching entry is
invisible to that alert, so the two change together; [`../reference/alerts.md`](../reference/alerts.md)
is the rule's page.

**A killed session leaves a lock, and the lock outlives it.** Restic holds a lock for the length of
most commands — shared for reads, exclusive for writes — and releases it only on a clean exit. The
laptop run on 2026-11-22 was killed before that, so the `nfs` repository stayed locked for ten hours
and the noon `backup-prune` failed on it: the retention policy was never applied, and no rule noticed,
because `backup-prune` had no successful run to be missing. A second lock, shared and minutes old,
appeared on `nodes-streaming` the same evening from a read whose process had already exited. End any
session that touched a repository, from whatever machine ran it:

```bash
for r in "$NODES_NAS" "$NODES_NUC0" "$NODES_STREAMING" "$NFS"; do restic -r "$r" unlock; done
```

`unlock` removes a lock it can judge stale, including one made on another host — run from a pod in
`backup`, it cleared both of the 2026-11-22 `nfs` locks. It left the fresh one, which had not aged
past its threshold; `restic -r "$NFS" unlock --remove-all` cleared that once the laptop showed the
PID was gone. `--remove-all` is the escape hatch for exactly that case, and only when you have
confirmed the owning process is dead and no job is writing: never during the 06:00 `backup-nfs`
window or a running prune. [`last-resort.md`](last-resort.md) Step 2 ends the same way.

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
