# First start of the board

Date: 2026-11-15

**Changes the cluster:** creates the board's owner account and the first row in its database.
Read-only until Step 8; see [Rollback](#rollback) for how the whole board is removed.

The board (Vikunja) runs in the `vikunja` namespace, behind the cluster's own certificate, with
single sign-on through authentik for the web interface and an open API for machines. This procedure
is the first start: it proves each of those claims on a board whose database already exists, and it
creates the one account that owns everything.

Run it after [`vikunja-database.md`](vikunja-database.md). The board crashloops without that
database, so a pod that never becomes ready is more likely a missing database than a fault here.

## Names

| Thing | Value |
|---|---|
| Namespace | `vikunja` |
| Application (ArgoCD) | `vikunja`, in namespace `argocd` |
| Chart | `cluster/helm/vikunja` |
| Deployment, Service | `vikunja`, `vikunja` on port `3456` |
| Address | `https://vikunja.kub.local` |
| Certificate | the default `kube-system/local-cert` (identity `CN=kub.local`) |
| Gate | `lan-only` middleware, then authentik on everything but `/api` |
| Board owner | created in Step 8, in the web form |

## Preconditions

- The database exists and the board's Secret holds its password:

  ```bash
  kubectl -n databases exec statefulset/postgres -c postgres -- \
    psql -U postgres -tAc "select datname from pg_database where datname='vikunja'"
  # expect: vikunja
  ```

- The workstation resolves `vikunja.kub.local` to the cluster. Every command below runs from the LAN;
  Step 10 is the only one that runs from outside it.
- `curl` and `openssl` on the workstation.
- The window for the first run is short but real: between the board starting and Step 8, anybody who
  reaches the address can claim the owner account. Complete Steps 1 to 8 in one sitting.

## Step 1 — wait for ArgoCD

The commit that adds the chart and the Application reaches the cluster through the root Application
(see [`../../AGENTS.md`](../../AGENTS.md) §2). In the ArgoCD UI, the `vikunja` Application goes
`OutOfSync` → `Syncing` → `Synced`, and its health goes `Progressing` → `Healthy`.

```bash
kubectl -n argocd get application vikunja \
  -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
# expect: Synced Healthy
```

`Synced` alone is not enough: it only says the manifests match git, not that the pod came up.
Health is the second half of the answer. A `ComparisonError` instead means the chart does not render;
that is a git problem, not a cluster one.

## Step 2 — the pod is up

```bash
kubectl -n vikunja get pods
# expect: one pod, vikunja-… , READY 1/1, STATUS Running, RESTARTS 0
```

One pod, `1/1`, and no restarts. The board is a single process with its state in PostgreSQL, so
there is nothing to scale and no second container to wait for. A growing `RESTARTS` count sends you
to Step 3. A pod stuck in `CreateContainerConfigError` is a missing Secret — see
[`../secrets.md`](../secrets.md).

## Step 3 — the logs

```bash
kubectl -n vikunja logs deploy/vikunja | grep -i 'connected\|migrat'
# expect: the migration text, then a line saying the board connected to the database
```

Read it in order: the board runs its schema migrations first, then opens the database. Success looks
like migration lines followed by a connection line, with no `connection refused`, no
`password authentication failed` and no `database "vikunja" does not exist` anywhere in between.

These lines are plain text at `INFO`. If the grep prints nothing at all, drop the grep and read the
log whole:

```bash
kubectl -n vikunja logs deploy/vikunja --tail=40
```

| What the log says | What it means |
|---|---|
| `connection refused` | The database host or port in the chart is wrong, or PostgreSQL is not running. |
| `password authentication failed for user "vikunja"` | The Secret and the role hold different values — redo `vikunja-database.md` Step 4. |
| `database "vikunja" does not exist` | `vikunja-database.md` stopped after `CREATE ROLE`. |
| `no such host` | The host name is wrong: it is `postgres.databases.svc.cluster.local`. |

## Step 4 — plain HTTP redirects

```bash
curl -sI http://vikunja.kub.local/ | grep -i '^location\|^HTTP'
# expect: HTTP/1.1 301 and Location: https://vikunja.kub.local/
```

The plain-HTTP address answers with **301**, a permanent redirect, to the HTTPS address. That code
comes from the `vikunja-redirect` route, which runs the `lan-only` gate and then `redirect-to-https`:
a request from outside the LAN is refused here, before it is redirected anywhere.

On a newer Traefik the same middleware answers **308** instead of 301. Both are permanent redirects
and both preserve the method and body; 308 is simply the stricter of the two. Either number means
the step passed.

What must **not** appear is a redirect to authentik here. The sign-in gate applies after the scheme
changes, so a `Location` pointing at the authentik host means the redirect route is missing or
ordered wrongly.

## Step 5 — the certificate

`-k` appears in every `curl` below. It tells curl to accept the certificate without checking it, and
that is deliberate for these checks: they are about what the board answers, not about who signed it.
Skipping verification means curl proves nothing about the certificate, so the chain is checked
separately, once, here.

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  --cacert ~/.config/secrets/kub-local/local-ca.pem https://vikunja.kub.local/api/v1/info
# expect: 200
```

That is the real check: with the cluster's own certificate authority (CA) given to curl, a successful
connection means the chain the board presents leads back to that authority. Then the identity on the
certificate:

```bash
openssl s_client -connect vikunja.kub.local:443 -servername vikunja.kub.local </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer
# expect: subject=CN = kub.local
#         issuer=CN = ashulab internal CA
```

`subject` is who the certificate claims to be — `kub.local`, the cluster's internal name, which
covers `vikunja.kub.local` as a subdomain. `issuer` is who signed it: `ashulab internal CA`, minted
by [`internal-ca.md`](internal-ca.md). A browser warning on a public name such as `ashuvidz.com`
would be expected here; on `kub.local` the internal CA is in each workstation's trust store, so
there is none.

## Step 6 — the API answers, the interface does not

The point of the gate is one rule: `/api` is open to machines, everything else needs a session.

```bash
curl -sk https://vikunja.kub.local/api/v1/info | head -c 120
# expect: JSON containing "version":"v2.8.0" or later
```

```bash
curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' https://vikunja.kub.local/
# expect: 302 followed by the authentik sign-in address
```

The first call returns the board's own version document to something that has never signed in — that
is the proof that `/api` skips authentik. The version string carries a leading `v`: expect `v2.8.0`
or a later number, not `2.8.0`. The second call is redirected to authentik's sign-in page — that is
the proof that `/` does not skip it. A `200` on the second call means the gate is missing from the
route, and the board is reachable by anybody who can reach the cluster.

## Step 7 — metrics

```bash
curl -sk https://vikunja.kub.local/api/v1/metrics | head -5
# expect: # HELP and # TYPE lines, and metric names
```

That endpoint exists only because `metrics.enabled` is true in the chart, and it sits inside the same
unauthenticated `/api` route set — no session, no token. A `404` means the setting is off.

Then in Prometheus (Grafana → Explore, data source Prometheus):

```
up{namespace="vikunja"}
```

`1` for the board's pod. Note the query: the cluster scrapes through annotations, so the job is
`kubernetes-pods` for every annotated pod, and that job attaches `namespace` and `pod` labels rather
than an application name. `up{job=~".*vikunja.*"}` therefore matches nothing and proves nothing —
`0` from it is not a fault.

A `0` on `up{namespace="vikunja"}` is: the ServiceMonitor-less scrape annotation missing from the
chart, the `/api/v1/metrics` path answering something other than text, or the pod not ready.

## Step 8 — the owner account

Open `https://vikunja.kub.local` in a browser. The sign-in gate sends you to authentik; sign in there
with the operator account, and authentik returns you to the board. Vikunja's own first-run form then
asks for a username, an email address and a password.

That form creates the board's single owner: the account that holds every project and can invite
nobody. Use the operator's password manager. The password is Vikunja's own — authentik only proves
who is asking; it does not replace the board's login.

This is the step that ends the open window from the Preconditions: as soon as an owner exists, the
first-run form refuses to create a second one.

## Step 9 — registration stays closed

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://vikunja.kub.local/api/v1/register
# expect: 405
```

```bash
curl -sk -X POST -o /dev/null -w '%{http_code}\n' https://vikunja.kub.local/api/v1/register
# expect: 404
```

The registration route accepts `POST` only, so a bare `curl` gets **405**, "method not allowed" — that
is the route existing and refusing the method, not proof that registration is off. The second command
sends the right method and gets **404**: the endpoint is not there at all, which is what a board with
registration switched off reports. Expect both numbers in that order.

A `200` on the second command means registration is open and anybody who reaches the address can
make an account. Close it in the chart: `service.enableregistration: false` in the `config.yml`
block of `cluster/helm/vikunja/values.yaml`. Commit, let ArgoCD sync, repeat the two commands.

## Step 10 — refused from outside the LAN (operator only)

This step cannot be done from inside the cluster, and not from a pod: the `lan-only` middleware
treats the pod network as the LAN, so a request from a pod passes the gate and proves the opposite of
what is being checked. It runs from a network outside the house, or from the same workstation with
the WireGuard tunnel down.

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://vikunja.kub.local/
curl -sk -o /dev/null -w '%{http_code}\n' https://vikunja.kub.local/api/v1/info
# expect: 403
# expect: 403
```

Both **403**, "forbidden". The second line is the one that matters: the API skips authentik, not the
LAN gate. A `200` there would mean the board's API answers the open internet, so treat it as an
incident and check the route's middleware list before anything else.

A timeout instead of a 403 is a different answer: it usually means the tunnel is up, so the request
came from the LAN after all. Turn WireGuard off and repeat.

## Step 11 — the board's second and third runbooks

The first start ends with two rows in [`README.md`](README.md) whose status is not yet "proved":
`vikunja-first-start.md` (this file) and `vikunja-board-seed.md`, the procedure that recreates the
boards themselves from the git-tracked seed. Fill them in when the operator runs this procedure and
pastes the outputs back.

## Success, in one place

- `Synced Healthy` on the Application, one pod `1/1` with no restarts.
- The log shows migrations, then a connection, with no error between them.
- `http://` answers 301 with `Location: https://vikunja.kub.local/`.
- The certificate validates against `local-ca.pem`, `subject CN=kub.local`, `issuer CN=ashulab internal CA`.
- `/api/v1/info` answers without a session and reports `v2.8.0` or later; `/` redirects to authentik.
- `/api/v1/metrics` returns text, and `up{namespace="vikunja"}` is `1`.
- The owner account exists, and `POST /api/v1/register` answers 404.
- Both addresses answer 403 from outside the LAN.

## Rollback

Only if the board is abandoned. This destroys the boards, the tasks and the owner account. There is
no export in this procedure: the nightly dump in [`postgres-add-database.md`](postgres-add-database.md)
holds them until its retention ends.

```bash
kubectl -n argocd delete application vikunja
kubectl delete namespace vikunja
```

Then drop the database and the role, as in the rollback of
[`vikunja-database.md`](vikunja-database.md):

```bash
kubectl -n databases exec -i statefulset/postgres -c postgres -- psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DROP DATABASE vikunja;
DROP ROLE vikunja;
SQL
```

Order matters. Deleting the Application stops ArgoCD from recreating what the next two commands
remove; deleting the namespace removes the pod, the board's Secret and its volume, which is what lets
`DROP DATABASE` take the lock it needs. Deleting the namespace first would leave the board running
until ArgoCD noticed — it does notice, and that is a fight with a controller you do not need.

The last dump is on the `postgres-dumps` claim. It is the way back, and it keeps only the 14 days of
retention.

## What this procedure does not cover

- The board's own content: projects, labels and views are created by `vikunja-board-seed.md`.
- Backups: the database is in the nightly dump, nothing exports the board separately.
- The gate and the certificate themselves: those are shared with every other internal application,
  see [`authentik-apps.md`](authentik-apps.md) and [`internal-ca.md`](internal-ca.md).
