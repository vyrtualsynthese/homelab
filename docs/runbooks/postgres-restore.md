# Restore a PostgreSQL database

**Destructive.** Restoring replaces the target database: every table, row and index in `<db>` ends
up as the dump has it, and anything written after the dump is gone. The drill path below restores
into a throwaway instance and only *reads* production. The same `pg_restore`, pointed at the live
database, destroys what is in it.

This runbook has two uses. The first is the **drill**, run before anything goes wrong: it proves the
dumps restore and that the person doing it knows the steps. The second is the **real restore**, after
data loss. The commands are the same; only the target changes, see
[Restoring the production instance](#restoring-the-production-instance).

## Preconditions

- A dump exists, no older than 14 days (the retention on the `postgres-dumps` claim).
- `kubectl` access to the cluster, namespace `databases`, and the ability to create a pod there.
- No dump run in progress, because the claim is ReadWriteOnce (one node's disk, one writer of
  record — several pods on the *same* node can mount it):

  ```bash
  kubectl -n databases get pods -l app=postgres-dump
  ```

  Empty output means the 03:30 job is not running. Both pods below are pinned to `nas`, where the
  claim lives; a pod scheduled elsewhere stays `Pending` on the volume.
- `<db>` and `<role>` are known: they are in the log of
  [`postgres-add-database.md`](postgres-add-database.md). A database on this instance was created
  only by that procedure, so the log is the authority on the pair.
- The image `postgres:18.7` is already on `nas` (the instance runs it), so the pods below start
  without a pull.

## Step 1 — find the newest dump

Mount the claim read-only in a pod that does nothing but list it. It stays up for the rest of the
procedure, so the dump file is one `ls` away.

```bash
kubectl -n databases apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: dumps-ls
spec:
  restartPolicy: Never
  nodeSelector:
    kubernetes.io/hostname: nas
  securityContext:
    runAsUser: 999
    runAsGroup: 999
  containers:
    - name: ls
      image: postgres:18.7
      command: ["sh", "-c", "sleep 7200"]
      volumeMounts:
        - name: dumps
          mountPath: /dumps
          readOnly: true
  volumes:
    - name: dumps
      persistentVolumeClaim:
        claimName: postgres-dumps
EOF
kubectl -n databases exec dumps-ls -- ls -lt /dumps
```

The newest file is the first line. Names are `<db>-<UTC>.dump`, for example
`firefly-20261113T033002Z.dump`, so the timestamp in the name is the moment the dump was taken —
`ls -lt` sorts by modification time, which is when the dump finished writing.

Success: the list contains `<db>-<recent>.dump` and a few older ones. What a failure means:

| Output | Cause |
|---|---|
| directory empty | No application database has been dumped, or the job has been failing; check `kubectl -n databases get jobs -l app=postgres-dump`. |
| only `<db>-…dump.partial` | The last run died mid-dump. It is not verified and must not be used; take an older `.dump`. |
| no file for `<db>` | The database did not exist at 03:30 the night before, or the run failed on it. Check the dump Job's logs. |

Only files ending in `.dump` are good: the script writes `.partial`, checks the file is non-empty
and that `pg_restore --list` can read it, and only then renames it.

## Step 2 — start a scratch instance

A separate, empty PostgreSQL of the same major version, in the same namespace, with its own
`emptyDir` as data directory and the dumps claim mounted read-only. It listens on `127.0.0.1` only,
so nothing else in the cluster can reach it while it exists.

```bash
kubectl -n databases apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: postgres-restore
spec:
  restartPolicy: Never
  nodeSelector:
    kubernetes.io/hostname: nas
  securityContext:
    runAsUser: 999
    runAsGroup: 999
    fsGroup: 999
  containers:
    - name: postgres
      image: postgres:18.7
      args: ["-c", "listen_addresses=127.0.0.1"]
      env:
        - name: PGDATA
          value: /var/lib/postgresql/data/pgdata
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: postgres-superuser
              key: postgres-password
      volumeMounts:
        - name: data
          mountPath: /var/lib/postgresql/data
        - name: dumps
          mountPath: /dumps
          readOnly: true
  volumes:
    - name: data
      emptyDir: {}
    - name: dumps
      persistentVolumeClaim:
        claimName: postgres-dumps
EOF
kubectl -n databases wait --for=condition=Ready pod/postgres-restore --timeout=120s
kubectl -n databases logs postgres-restore
```

Success: the log ends with `database system is ready to accept connections`. The `wait` above only
proves the container started; the log line is the real readiness check. The `emptyDir` is deleted
with the pod, so nothing here can be confused with the real data later.

`POSTGRES_PASSWORD` is required by the image on first start; with `listen_addresses=127.0.0.1` and
`pg_hba` defaults it is only reachable from inside the pod's own network namespace, which is all
this procedure needs.

## Step 3 — restore the dump

Open a shell in the scratch pod. Everything below runs inside it, as the `postgres` OS user, over
the local socket — no password, nothing on the cluster network.

```bash
kubectl -n databases exec -it postgres-restore -- bash
```

```bash
# 1. The role the restored objects will belong to. No password: it never logs in here.
psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE <role> LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;"

# 2. The target database, owned by the role exactly as in production. Ownership matters:
#    from PostgreSQL 15 on, schema "public" belongs to the database owner, so a role that
#    does not own the target cannot create tables in it.
createdb -U postgres -O <role> "<db>_restore"

# 3. The dump itself.
pg_restore -U postgres -d "<db>_restore" --no-owner --role=<role> --exit-on-error \
  /dumps/<db>-<UTC>.dump
echo "exit status: $?"
```

Any statement in the dump that names a role — a table grant to `backup`, an extension owned by
another role — needs that role to exist in the scratch instance too, or it fails. The dump's table
of contents lists them: `pg_restore --list /dumps/<db>-<UTC>.dump` before running the restore.

Why these flags:

| Flag | Effect |
|---|---|
| `--no-owner` | Does not replay the original `ALTER … OWNER TO`. Ownership in the scratch instance comes from the role the restore runs as, which is what makes a dump restorable on a rebuilt cluster where object OIDs and owners differ. |
| `--role=<role>` | Runs the restore session as the application's role, so the objects it creates belong to that role. The role must exist first (statement 1), or `SET ROLE` fails immediately. |
| `--exit-on-error` | Stops at the first failing statement instead of collecting warnings. Without it a partially restored database can still exit 0 with `warning: errors ignored on restore: N`, and the row counts later would not tell you which tables are missing. |

If the dump contains `CREATE EXTENSION`, the restore may stop there: `SET ROLE <role>` is not a
superuser, and most extensions require one. Create the extensions first, as the superuser:

```bash
psql -U postgres -d "<db>_restore" -c "CREATE EXTENSION IF NOT EXISTS <extension>;"
```

`pg_restore --list /dumps/<db>-<UTC>.dump | grep -i 'EXTENSION'` says which ones, without touching
the target.

Success: `pg_restore` prints nothing, and `echo $?` prints `0`. It printing `errors ignored on
restore` means the restore is **not** good — stop here and read the errors.

Leave the shell with `exit` when done; the pod keeps running for the checks below.

## Step 4 — check the row counts

Two or three tables, counted on both sides. Pick tables that the application writes to, not lookup
tables that never change: a restore that drops every row of a static table looks identical to a
correct one.

List the tables first, in the scratch instance:

```bash
kubectl -n databases exec -it postgres-restore -- \
  psql -U postgres -d "<db>_restore" -c '\dt'
```

Then count, from the production instance (a count only, as the superuser over the local socket — no
password needed) and from the scratch instance:

```bash
kubectl -n databases exec -it statefulset/postgres -c postgres -- \
  psql -U postgres -d "<db>" -c 'SELECT count(*) FROM <table1>;' -c 'SELECT count(*) FROM <table2>;'

kubectl -n databases exec -it postgres-restore -- \
  psql -U postgres -d "<db>_restore" -c 'SELECT count(*) FROM <table1>;' -c 'SELECT count(*) FROM <table2>;'
```

Success: identical numbers for every table counted. Which is exact for a fresh dump and the right
expectation in a disaster. In a drill, production keeps writing: a difference has to be explained by
the time between the dump and the count — check the dump's timestamp in its name — and a table that
lost rows instead of gaining them is not explained by time.

Do not use `pg_stat_user_tables.n_live_tup` for this: it is an estimate refreshed by the statistics
collector, and a fresh restore has not been analysed yet.

## Step 5 — teardown

```bash
kubectl -n databases delete pod postgres-restore dumps-ls
kubectl -n databases get pods
```

Deleting `postgres-restore` deletes its `emptyDir`; deleting `dumps-ls` releases the read-only mount
on the claim. The claim, the StatefulSet and the Service are untouched — never delete those as part
of a drill.

Success: `get pods` no longer lists either pod, and `postgres-0` is still `Running`.

## Step 6 — record the drill

A drill that is not written down did not happen; the next person has to assume the dumps are
untested.

| Date (UTC) | Database | Dump restored | Tables checked | Source rows | Restored rows | Result | Operator |
|---|---|---|---|---|---|---|---|

No drill has been run yet (2026-11-13). The first row is the W2 acceptance drill.

## Restoring the production instance

The same steps, with the target being the real instance instead of the scratch one. This is the case
after `nas` survives but the database pod or its claim did not.

1. Let ArgoCD recreate the `postgres` StatefulSet with an empty `postgres-data` claim, and wait for
   `Job/postgres-bootstrap` to complete. That recreates the `backup` and `postgres_exporter` roles,
   not the application roles.
2. For each database in the add-database log, in this order: recreate the role and database (steps
   1–3 of [`postgres-add-database.md`](postgres-add-database.md)), get the dump onto the database
   pod, run the `pg_restore` line from Step 3 with `-d <db>` — the database exists already, so no
   `createdb` — then start the application. The database pod does not mount the dumps claim, so the
   file is copied over first:

   ```bash
   kubectl -n databases cp dumps-ls:/dumps/<db>-<UTC>.dump postgres-0:/tmp/
   kubectl -n databases exec -it statefulset/postgres -c postgres -- bash
   # inside: pg_restore -U postgres -d "<db>" --no-owner --role=<role> --exit-on-error \
   #           /tmp/<db>-<UTC>.dump
   ```

   The restore must finish **before** the application pod starts, otherwise it writes into a
   database that is still being filled and the row counts never match again.
3. If a Secret for the database is gone, recreate both copies with one value, then
   `ALTER ROLE <role> PASSWORD '<value>'`. The Secrets are not inside the dumps.
4. If the target database still holds data that matters, dump it before overwriting it — the restore
   in step 2 does not ask twice.

The case this runbook cannot fix: **`nas`'s disk is lost.** The dumps live on it, the offsite copy
is W4 and does not exist yet, and there is no other copy. That is stated in the design (§6.8) and it
is the reason W2 alone is not a backup strategy.
