# Rotate a secret

> **In one line.** A rotated value changes everywhere it is written down — the Secret, the role's
> password, the password manager — and the pod must restart to see it.
>
> **Use it when** rotating a database password or a client secret · **Verified** 2026-11-17 ·
> **Kind:** living page

## What a rotation touches

**The Secret in the cluster.** The only place a pod reads the value; [`secrets.md`](../secrets.md)
lists which Secret each app reads and its key.

**The consumer.** An environment variable, a mounted file, or a setting in the app's own database;
the key name is the chart's, so a renamed key gives a pod that will not start.

**The role password.** What the pod sends and the hash the role holds are separate: the Secret
changes only what the app sends; the role keeps the old value until `ALTER ROLE`.

**The pod.** `env` is read at container start, so a patched Secret never reaches a running
container; mounted files change on disk but apps re-read them at start.

## The two copies

A Secret belongs to one namespace, so a database password exists twice: beside the instance that
created the role (`databases/pg-<app>`, key `password`) and beside the app that consumes it
(`<app-ns>/<app>-db`, or whatever its chart reads) — the names in
[`postgres-add-database.md`](../runbooks/postgres-add-database.md)'s step 1. Both hold the same
value, the role a hash of it: **a rotation changes both Secrets and the role, never one of the
three.**

## Rotate

`openssl` and `jq` on the workstation; hex, so nothing needs escaping. `$PW` goes in the password
manager first.

```bash
APP=koillection; APP_NS=koillection; APP_SECRET=koillection; APP_SECRET_KEY=DB_PASSWORD
PW=$(openssl rand -hex 24)
kubectl -n databases patch secret "pg-$APP" --type merge \
  -p "$(jq -n --arg p "$PW" '{stringData:{password:$p}}')"
kubectl -n "$APP_NS" patch secret "$APP_SECRET" --type merge \
  -p "$(jq -n --arg k "$APP_SECRET_KEY" --arg p "$PW" '{stringData:{($k):$p}}')"
```

A merge patch replaces the named key and leaves the rest: koillection's `DB_NAME` and `DB_HOST` survive.
`create` fails with `AlreadyExists`.

The role, pasted interactively so it stays out of the shell history:

```bash
kubectl -n databases exec -it statefulset/postgres -c postgres -- psql -U postgres -d postgres
# ALTER ROLE <app> PASSWORD '<new value>';
```

```bash
kubectl -n "$APP_NS" rollout restart deploy/$APP
kubectl -n "$APP_NS" rollout status deploy/$APP --timeout=120s
```

Between `ALTER ROLE` and the restart, connections holding the old environment are refused: a few
`password authentication` failures are expected, then stop.

## Verify

Two questions: does the app work, and is the old value refused? The first prints nothing:

```bash
kubectl -n "$APP_NS" logs deploy/<app> --since=5m | grep 'password authentication'
```

The second runs from the instance pod **over TCP**: the local socket authenticates by peer and
accepts any value.

```bash
kubectl -n databases exec statefulset/postgres -c postgres -- \
  env PGPASSWORD='<old value>' psql -h 127.0.0.1 -U "$APP" -d "$APP" -c 'select 1'
```

`password authentication failed` is the success; a `select 1` that returns means the role holds the
old one.

## The failure mode

**Only one copy changed.** The app keeps working: its environment holds the value it started with,
and the role still accepts it. Then it restarts — a sync, a node reboot, days later — and refuses to
authenticate, no manifest changed, nothing in the log.

The quiet direction is the reverse: the `databases/` copy patched, the role untouched — nothing
fails until somebody reads that copy or rebuilds from it. The hashes show both:

```bash
kubectl -n databases get secret "pg-$APP" -o jsonpath='{.data.password}' | sha256sum
kubectl -n "$APP_NS" get secret "$APP_SECRET" \
  -o jsonpath="{.data.$APP_SECRET_KEY}" | sha256sum
```

Equal hashes mean the two copies agree, not that the role does: `ALTER ROLE` is still owed.
Unequal means one copy is stale: set it to the other's value, then `ALTER ROLE`.

## Special cases

**authentik's `envFrom`.** Exported whole, so a patch lands on the next pod start.
`AUTHENTIK_SECRET_KEY` signs sessions, so rotating it signs everybody out;
`AUTHENTIK_BOOTSTRAP_PASSWORD` only creates `akadmin` on first start — rotate that account in
the UI. Blueprints read `!Env` at apply time: restart the worker **and** re-apply
([`sso-applications.md`](../reference/sso-applications.md), trap 5); the app-specific steps are in
[`authentik-apps.md`](../runbooks/authentik-apps.md).

**A client secret has two copies, and the provider's goes first.** `PGADMIN_CLIENT_SECRET` is read
from `authentik-sso` at apply time: patch that Secret, re-apply the blueprint, then the app's copy —
or the token exchange fails after the login.

**pgAdmin's `config_local.py`** (same page). Its Secret holds one file, not keys: regenerate it with
the new client secret and replace the Secret — delete then create, or
`--dry-run=client -o yaml | kubectl apply -f -` to avoid the gap.

**Secrets that carry non-secret keys.** `DB_NAME`, `DB_HOST`, `DB_USER` and `POSTGRES_USER` are not
sensitive; the chart reads them there, so a rotation leaves them.

**A password held once.** `vikunja-db` has no `databases/` twin — no two-copy problem; nothing else
holds it: patch the Secret, `ALTER ROLE`.
