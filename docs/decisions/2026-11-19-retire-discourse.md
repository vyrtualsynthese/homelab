# Discourse is retired, not repaired

Date: 2026-11-19
Decided by: operator
Status: active

## The decision

`discourse`, the forum behind `forum.ashuvidz.com`, is gone: the chart, the ArgoCD `Application`,
the namespace and its public hostname. It had not served a page since 2026-08-02, when its pod went
`Pending` because the claim it mounts — `discourse2` — was a Longhorn volume and Longhorn was gone.

What went with it, so it is not discovered later:

| Gone | Was | Where it is now |
|---|---|---|
| `cluster/helm/discourse` | the pattern D wrapper around the Bitnami chart 16.0.2: four `IngressRoute` templates and a redirect middleware | git history |
| `cluster/argocd/projects/discourse.yaml` | how the chart reached the cluster | git history |
| the `discourse` namespace | Deployment, Redis `StatefulSet`, three Services, the `discourse` Secret and an 8 Gi `local-path` claim for Redis | deleted by hand after the merge |
| `forum.ashuvidz.com` | one of the four public hostnames | removed from `hack/check_exposure.py`'s allowlist, from `AGENTS.md`, `docs/how-it-works/network.md`, `docs/reference/routes.md` and `traefik-gate`'s README; three public hosts remain |
| `discourse.kub.home.ashudev.com` | a LAN name that only redirected to the forum | deleted with the namespace |
| the `discourse` entry in the PostgreSQL network policy | permission for the namespace to reach the shared instance | removed from `databases/postgres` values |
| the Dashy "Forum" tile | the start page's link to the community | removed from `dashy/files/conf.yml` |

## Why

The application was already without data, and nothing depended on it.

- **The pod never left `Pending`.** It mounts claim `discourse2`, which the 2026 Longhorn failure
  released; the replacement claim was never created, and `docs/status.md` had listed the app as
  broken since the 17 November snapshot.
- **The database is not there either.** `pg_database_size_bytes` on the shared PostgreSQL, read on
  2026-11-19, lists `acceptance`, `authentik`, `postgres`, `template0`, `template1` and `vikunja`.
  There is no `discourse` database to export or drop, so the posts, users and uploads were lost
  before this record and this record does not destroy them.
- **The public host answered `404`.** Checked on 2026-11-19 over both schemes: `http` redirects to
  `https`, and `https` has no backend behind it.
- **What still ran was one Redis.** A 1/1 `StatefulSet` on an 8 Gi `local-path` claim, serving a
  forum that could not start. That is the only resource the teardown frees.

The alternative — leaving the chart so the repo and the cluster keep matching — was rejected for the
same reason the seven applications went on 2026-11-18: a dead app in the status board is a fault that
still looks fixable, and a public hostname with no working backend is an open door onto nothing.

## What it costs if it is wrong

The forum's content is not recoverable: it was lost with the Longhorn volume and the database, not
with this removal. Reinstating the application means a new role and database through
[`runbooks/postgres-add-database.md`](../runbooks/postgres-add-database.md), a fresh Secret, a claim
on `local-path`, a rebuilt chart, and asking the DNS provider for `forum.ashuvidz.com` again. The
data that would make a forum worth restoring is not available to restore it from.
