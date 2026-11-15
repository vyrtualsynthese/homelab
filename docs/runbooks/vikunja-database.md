# The board's database

Date: 2026-11-15

**Changes the cluster:** creates a namespace, a role, a database and one Secret. Reversible only
while the board holds nothing — see [Rollback](#rollback).

Vikunja, the task board, keeps its state in the cluster's shared PostgreSQL 18 in the `databases`
namespace. By default it would keep that state in a file next to itself; pointed at PostgreSQL
before the database exists, it exits at startup and the pod restarts in a loop (crashloops) while
the operator hunts a cause. So this procedure runs **once, before the board's first start**, and it
is the record of what exists on that instance and why.

It is a specialisation of [`postgres-add-database.md`](postgres-add-database.md), which holds the
reasoning behind every statement below. Read that document if a step here fails in a way this one
does not explain.

## Names

| Thing | Value |
|---|---|
| Role (the login the board uses) | `vikunja` |
| Database | `vikunja` |
| Owner of the database | `vikunja` |
| Namespace the board runs in | `vikunja` |
| Secret holding the password | `vikunja-db` in namespace `vikunja` |
| Key inside that Secret | `VIKUNJA_DATABASE_PASSWORD` |

No `databases/pg-vikunja` Secret is created, unlike the other applications in the parent runbook:
`vikunja-db` is the only copy of the password. Losing it is recoverable — set a new one with
`ALTER ROLE` and recreate the Secret (see [Rollback](#rollback)) — but nothing else holds it.

The rest of the connection details are the board's configuration, not part of this procedure:
host `postgres.databases.svc.cluster.local`, port `5432`, database `vikunja`, user `vikunja`.

## Preconditions

- The instance is healthy:

  ```bash
  kubectl -n databases rollout status statefulset/postgres --timeout=120s
  # expect: statefulset "postgres" successfully rolled out
  ```

- `openssl` on the workstation.
- The board's Application has **not** synced yet, or is scaled to zero. A pod that starts before
  this procedure finishes will crashloop on the missing database.
- Nothing needs adding to `networkPolicy.consumerNamespaces` in
  `cluster/helm/databases/postgres/values.yaml`: that policy is switched off (`enabled: false`,
  see the comment in the file), so every namespace in the cluster can reach the instance. When it
  is switched on, `vikunja` must be in the list before this procedure is run.
- Avoid 03:30, when the nightly dump runs. A database created mid-run is simply dumped from the
  following night.

## Step 1 — create the namespace

```bash
kubectl create namespace vikunja
# expect: namespace/vikunja created
```

The Secret in Step 4 cannot exist before its namespace, and the Application's
`CreateNamespace=true` only takes effect at sync time — which is after the pod that needs the
Secret. Keep this line; it is not redundant. If the namespace is already there from an earlier
attempt, the command fails with `AlreadyExists` and that is not a problem: continue at Step 2.

## Step 2 — generate the password

```bash
PGPASSWORD=$(openssl rand -base64 24)
```

Base64 is safe here because none of the characters it can produce (`A–Z`, `a–z`, `0–9`, `+`, `/`)
needs escaping inside SQL single quotes or inside a shell heredoc — no quote, no backslash, no `$`.
Twenty-four bytes encode to 32 characters and no padding, so the value is always 32 long. Do not
export it: only `$PGPASSWORD` in this shell is used, and the commands below read it directly.

## Step 3 — create the role and the database

The statements below go through one psql session inside the pod. That session runs on the local
socket as the `postgres` OS user (peer authentication), so no password is needed on the command
line. The heredoc is written so the password is expanded by your shell and never appears in a
command line or in the pod's shell history.

```bash
kubectl -n databases exec -i statefulset/postgres -c postgres -- psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE vikunja LOGIN PASSWORD '$PGPASSWORD' NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE DATABASE vikunja OWNER vikunja;
REVOKE ALL ON DATABASE vikunja FROM PUBLIC;
GRANT CONNECT ON DATABASE vikunja TO vikunja;
GRANT CONNECT ON DATABASE vikunja TO backup;
SQL
```

Why each line is there, in one clause each — the full argument is in the parent runbook:

| Statement | Why it is not optional |
|---|---|
| `CREATE ROLE … NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION` | A plain login: an application role that can create databases, or read past row-level rules, turns one compromised application into the whole instance. |
| `CREATE DATABASE … OWNER vikunja` | The role owns its database and nothing else. |
| `REVOKE ALL ON DATABASE vikunja FROM PUBLIC` | Without it, PostgreSQL's default lets **any** role that can log in connect here. |
| `GRANT CONNECT … TO vikunja` | Redundant for the owner today, correct if ownership ever moves. |
| `GRANT CONNECT … TO backup` | The nightly dump runs as `backup`; without this the new database appears in its list and the dump fails. |

Success is five command tags in order:

```
CREATE ROLE
CREATE DATABASE
REVOKE
GRANT
GRANT
```

With `ON_ERROR_STOP=1`, a failure stops at the first error and prints it, so fewer than five tags
means the run did not finish.

## Step 4 — create the Secret

```bash
kubectl -n vikunja create secret generic vikunja-db \
  --from-literal=VIKUNJA_DATABASE_PASSWORD="$PGPASSWORD"
# expect: secret/vikunja-db created
```

The key name is Vikunja's own environment variable, so the chart can mount the Secret as
environment and read it without a mapping. The value must be the one the role was created with:
`AlreadyExists` here means the Secret survived from an earlier attempt, and the role's password may
not match it — delete that Secret and re-run both Step 3's `CREATE ROLE` (or `ALTER ROLE`) and this
command from one value.

## Step 5 — verify

```bash
kubectl -n databases exec statefulset/postgres -c postgres -- \
  psql -U postgres -tAc "select datname from pg_database where datname='vikunja'"
# expect: vikunja

kubectl -n vikunja get secret vikunja-db -o jsonpath='{.data.VIKUNJA_DATABASE_PASSWORD}' \
  | base64 -d | wc -c
# expect: 32
```

The first proves the database exists. The second proves the Secret holds a decodable value of the
expected length: a Secret stores its values base64-encoded, and `jsonpath` returns that stored form,
so the value is decoded before it is counted. Any number greater than 0 means the key is there;
`0`, an empty line, or a base64 error means the Secret or the key is wrong.

Neither of the two proves the password the board will use is the one the role holds. That is what
the next check is for: a pod standing in the board's place, reading the board's Secret and logging
in.

```bash
kubectl -n vikunja delete pod pg-check --ignore-not-found
kubectl -n vikunja apply -f - <<'EOF'
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
              name: vikunja-db
              key: VIKUNJA_DATABASE_PASSWORD
      command:
        - psql
        - -h
        - postgres.databases.svc.cluster.local
        - -U
        - vikunja
        - -d
        - vikunja
        - -tAc
        - select current_user, current_database()
EOF
kubectl -n vikunja wait --for=jsonpath='{.status.phase}'=Succeeded pod/pg-check --timeout=90s
kubectl -n vikunja logs pg-check
# expect: vikunja|vikunja
kubectl -n vikunja delete pod pg-check
```

`postgres:18.7` is the instance's own tag, from `cluster/helm/databases/postgres/values.yaml`. The
query reads and writes nothing, so the database is left as empty as it was found. What a failure
means:

| Output | Cause |
|---|---|
| `password authentication failed for user "vikunja"` | The Secret and the role hold different values. Set the role's password to the Secret's value (`ALTER ROLE vikunja PASSWORD '…'`) or recreate both from one value. |
| `permission denied for database "vikunja"` | The `REVOKE`/`GRANT` block was not applied, or was applied to another database name. Re-run Step 3's last three statements. |
| `FATAL: database "vikunja" does not exist` | Step 3 stopped after `CREATE ROLE`. The name is case-sensitive and must match exactly. |
| `wait` times out, logs show `connection timed out` | DNS or routing, not the policy: the NetworkPolicy is off. Check that the pod's `-h` is `postgres.databases.svc.cluster.local` and that the `databases` namespace is up. |
| logs show `could not translate host name` | Same as above: the Service is `postgres`, not `postgres-postgresql`. |

Delete the check pod even after a failure — it is not managed by git, and nothing else will.

## Recording it

The log at the end of [`postgres-add-database.md`](postgres-add-database.md) lists the databases
that exist on this instance and is the input to a rebuild. Append one row for `vikunja`, in
namespace `vikunja`, with `vikunja-db` in the Secret-pair column and `-` for the missing
`databases/pg-vikunja`, so the next person sees that the omission was deliberate.

## Success, in one place

- `kubectl get namespace vikunja` shows it `Active`.
- `select datname … 'vikunja'` prints `vikunja`.
- `wc -c` on the decoded Secret's key prints `32`.
- The check pod prints `vikunja|vikunja`.
- The row is in the parent runbook's log.

Nothing here is in git except this document: the namespace, the role, the database and the Secret
are hand-made cluster state. A rebuild repeats this procedure.

## Rollback

Only while the board holds nothing worth keeping. This destroys whatever is in it.

```bash
kubectl -n vikunja scale deploy/vikunja --replicas=0 2>/dev/null   # if the board is running

kubectl -n databases exec -i statefulset/postgres -c postgres -- psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DROP DATABASE vikunja;
DROP ROLE vikunja;
SQL

kubectl -n vikunja delete secret vikunja-db
```

- `DROP DATABASE` refuses while a session is connected, which is why the board is scaled down
  first. Scale it back up if the rollback was a mistake.
- `DROP ROLE` fails if the role still owns objects in another database. That is a sign this is not
  a fresh database: investigate rather than force it.
- Deleting the namespace (`kubectl delete namespace vikunja`) removes the Secret with it, and also
  the board's data volume. This runbook does not need that: deleting the Secret is enough.
- If only the password is lost, do not roll back. Set a new one — `ALTER ROLE vikunja PASSWORD
  '<new value>'` in the pod — then delete and recreate `vikunja-db` with that value, and restart the
  board.
- A database that already holds data is not rolled back: restore its newest dump, see
  [`postgres-restore.md`](postgres-restore.md).
