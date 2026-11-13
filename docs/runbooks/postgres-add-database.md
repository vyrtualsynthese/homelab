# Add a database to PostgreSQL

**Changes the cluster:** creates a role, a database and two Secrets. Reversible only while the
database is empty — see [Rollback](#rollback).

One database per application, created when that application is rebuilt. No database is created in
advance, so this procedure is the record of what exists and why. It is run by hand, as the
`postgres` superuser, and takes about five minutes.

## Preconditions

- The instance is healthy and ArgoCD reports the `postgres` Application `Synced`:

  ```bash
  kubectl -n databases rollout status statefulset/postgres --timeout=120s
  ```

- The superuser password is to hand: it is `postgres-password` in the `databases/postgres-superuser`
  Secret, kept in the operator's password manager. The psql path below does not need it (local
  socket, peer authentication); pgAdmin does.
- The application's namespace **already exists and is listed in `networkPolicy.consumerNamespaces`**
  in `cluster/helm/databases/postgres/values.yaml`. The database's NetworkPolicy denies every
  namespace not on that list, so a missing entry shows up as a connection timeout in step 4, long
  after the cause. Add it in a commit and let ArgoCD sync before starting.
- `openssl` on the workstation.
- Avoid 03:30, when the nightly dump runs: a database created mid-run is simply dumped from the
  following night.

## Naming

Pick `<app>` once; it is the role name, the database name and the suffix of the Secret in the
`databases` namespace. Lowercase ASCII letters, digits and underscores only — no dashes, no dots,
because the name is pasted into SQL unquoted. 63 characters at most. Examples, taken from the
application table in the design (§2): `firefly`, `wikijs`, `authentik`. The name is not always the
application's directory name: the wiki application's role and database are `wikijs`, and its
namespace is `wiki`.

The second Secret keeps whatever name the application's chart already reads. Where a chart has no
existing Secret, use `<app>-db` with the key `password`.

## Step 1 — generate the password

```bash
APP=firefly               # role, database and the Secret name pg-<app>
APP_NS=firefly            # the namespace the application runs in
APP_SECRET=firefly-db     # the name and key its chart reads; see Naming
PW=$(openssl rand -hex 24)
```

Hex on purpose: the value contains no character that SQL or a shell would need escaped.

## Step 2 — create the two Secrets

```bash
kubectl -n databases create secret generic "pg-$APP" --from-literal=password="$PW"
kubectl -n "$APP_NS" create secret generic "$APP_SECRET" --from-literal=password="$PW"
```

If the application chart reads a different key (`DB_PASSWORD`, `POSTGRES_PASSWORD`, …), use that key
on the second command **and in the check pod's manifest below**. The key on `pg-<app>` stays
`password`. The two values must be identical: the role is created from one copy and the application
logs in with the other.

Success: both commands print `secret/pg-<app> created` and `secret/<app>-db created`.

## Step 3 — create the role and the database

Use psql inside the pod, not pgAdmin: the session runs on the local socket as the `postgres` OS user
(peer authentication), so no password is needed on the command line, and the pod's shell history
disappears with the pod. pgAdmin keeps a query history that is visible in the browser, which would
outlive the password it recorded.

```bash
kubectl -n databases exec -it statefulset/postgres -c postgres -- psql -U postgres -d postgres
```

In that session, paste the statements below, replacing `<app>` with `$APP` and `<password>` with the
value of `$PW`:

```sql
CREATE ROLE <app> LOGIN PASSWORD '<password>' NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE DATABASE <app> OWNER <app>;
REVOKE ALL ON DATABASE <app> FROM PUBLIC;
GRANT CONNECT ON DATABASE <app> TO <app>;
GRANT CONNECT ON DATABASE <app> TO backup;
```

What each line is for:

| Statement | Why it is not optional |
|---|---|
| `CREATE ROLE … NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION` | The application role is a plain login. A role that can create databases, or read past row-level access rules, turns one compromised application into the whole instance. |
| `CREATE DATABASE … OWNER <app>` | The role owns its database and nothing else. |
| `REVOKE ALL ON DATABASE <app> FROM PUBLIC` | Without it PostgreSQL's default lets **any** role that can log in connect to this database. Guessing a database name would be enough. |
| `GRANT CONNECT ON DATABASE <app> TO <app>` | Redundant for the owner today, explicit and still correct if ownership ever moves. |
| `GRANT CONNECT ON DATABASE <app> TO backup` | The nightly dump runs as `backup` and enumerates the databases that exist. Without this grant a new database appears in the list and its dump fails; with it, the same night's dump covers it and nobody has to remember anything. |

`backup` already holds the predefined `pg_read_all_data` role, so it can read the tables once it can
connect; no per-table grant is needed.

Success is five command tags in order:

```
CREATE ROLE
CREATE DATABASE
REVOKE
GRANT
GRANT
```

Then check the shape from the same session:

```sql
\du <app>
\l <app>
```

- `\du` lists the role with no `Superuser`, `Create role` or `Create DB` attribute.
- `\l` lists the database owned by `<app>`, and its access-privileges column must **not** start with
  `=` — that would be PUBLIC's own entry, i.e. the revoke did not happen. It should contain
  `<app>=CTc/<app>` and `backup=c/<app>`.

## Step 4 — prove the connection from the application's namespace

A connection from the `databases` namespace (pgAdmin, the pod itself) proves nothing about the
application's path: the NetworkPolicy is a per-namespace allowlist. This pod stands in the
application's place and reads its Secret, so it also proves that the value the application will use
is the one the role holds.

```bash
kubectl -n "$APP_NS" delete pod pg-check --ignore-not-found
kubectl -n "$APP_NS" apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: pg-check
spec:
  restartPolicy: Never
  containers:
    - name: psql
      image: postgres:18.7
      env:
        - name: PGPASSWORD
          valueFrom:
            secretKeyRef:
              name: $APP_SECRET
              key: password
      command:
        - psql
        - -h
        - postgres.databases.svc.cluster.local
        - -U
        - $APP
        - -d
        - $APP
        - -v
        - ON_ERROR_STOP=1
        - -c
        - CREATE TABLE _check (id int); INSERT INTO _check VALUES (1); SELECT count(*) FROM _check; DROP TABLE _check;
EOF
kubectl -n "$APP_NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/pg-check --timeout=90s
kubectl -n "$APP_NS" logs pg-check
kubectl -n "$APP_NS" delete pod pg-check
```

The pod is a plain client pod pulled on first use; `postgres:18.7` is the instance's tag, from
`cluster/helm/databases/postgres/values.yaml`.

Success is this, in the logs:

```
CREATE TABLE
INSERT 0 1
 count
-------
     1
(1 row)

DROP TABLE
```

The table is created and dropped again, so the database is left as empty as it was found. What a
failure means:

| Output | Cause |
|---|---|
| `wait` times out, logs show `connection timed out` | The namespace is not in `networkPolicy.consumerNamespaces`. Fix the values file and let ArgoCD sync; the policy is the gate, not DNS. |
| `password authentication failed for user "<app>"` | The two Secrets do not hold the same value, or the role was created with a different one. Recreate both from one value and `ALTER ROLE`. |
| `permission denied for database "<app>"` | The `REVOKE`/`GRANT` block was not applied, or was applied to another database name. Re-run step 3's last three statements. |
| `FATAL: database "<app>" does not exist` | Step 3 stopped after the role. The database name is case-sensitive and must match exactly. |

## Step 5 — record it

Append one row to the log below. The log is the list of databases that exist on this instance, and
the input to a rebuild: nothing recreates these databases automatically.

| Date (UTC) | Application | Role = database | App namespace | Secret pair | Operator | Notes |
|---|---|---|---|---|---|---|

No database has been created through this procedure yet (2026-11-13). The first row is written by
the W2 acceptance drill.

## Success, in one place

- `databases/pg-<app>` and `<app-ns>/<app>-db` exist and hold the same value.
- `\du <app>` shows a login role with no superuser, create-role or create-database attribute.
- `\l <app>` shows the database owned by `<app>`, with no PUBLIC entry in its ACL and `backup=c`.
- The check pod from the application's namespace prints a count of `1`.
- The row is in the log above.

## Rollback

Only while the database holds nothing worth keeping. This destroys whatever is in it.

```sql
-- as postgres, in the pod
DROP DATABASE <app>;
DROP ROLE <app>;
```

```bash
kubectl -n databases delete secret "pg-$APP"
kubectl -n "$APP_NS" delete secret "$APP_SECRET"
```

- `DROP DATABASE` refuses while a session is connected. Stop the application's pod first
  (`kubectl -n "$APP_NS" scale deploy/<app> --replicas=0`), and remember to scale it back if the
  rollback was a mistake.
- `DROP ROLE` fails if the role still owns objects in another database. That is a sign this is not a
  fresh database: investigate rather than force it.
- Dumps already taken stay on the `postgres-dumps` claim until the 14-day retention deletes them.
  They are not deleted by this rollback and contain the data the drop removed — which is the way
  back if the drop was wrong.
- To recover a database that already holds data, do not roll back: restore its newest dump, see
  [`postgres-restore.md`](postgres-restore.md).
