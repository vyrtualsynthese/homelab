# First run of the docs site

Date: 2026-11-16

**Changes the cluster:** creates the `docs` namespace, one Secret, one claim and two workloads. See
[Rollback](#rollback) for how all of it is removed again.

The docs site renders every repository's `docs/` folder as a MkDocs Material site and serves it at
`https://docs.kub.local`. This procedure is the first start: it creates the GitHub token the builder
reads with, deploys the chart, runs the builder by hand once, and proves the result over the route.

Run it once, from the LAN. The builder needs a token before its first pod can start, so Steps 1 to 3
come before any sync.

## Names

| Thing | Value |
|---|---|
| Namespace | `docs` |
| Application (ArgoCD) | `docs-site`, in namespace `argocd` |
| Chart | `cluster/helm/docs-site` |
| CronJob, manual Job | `docs-site-build`; `docs-first-1` below |
| Deployment, Service | `docs-site`, `docs-site` on port `8080` |
| Claim | `docs-site-site`, 2Gi, `local-path`, on `nuc0` |
| Address | `https://docs.kub.local` |
| Certificate | the default `kube-system/local-cert` (identity `CN=kub.local`) |
| Gate | `lan-only`, then `authentik-forwardauth` (provider `docs`, `authentik Admins` only) |
| Secret | `github-docs-ro`, key `token` |

## Preconditions

- The workstation resolves `docs.kub.local` to the cluster.
- `curl` and `openssl` on the workstation; `kubectl` against the cluster.
- `~/.config/secrets/kub-local/local-ca.pem` present (the cluster's internal CA, minted by
  [`internal-ca.md`](internal-ca.md)).
- `~/.config/secrets/keys.env` holds `GITHUB_PERSONAL_ACCESS_TOKEN` on this workstation; Step 1 reads
  it from there.
- `nuc0` has room for a 1Gi builder pod and a 64Mi nginx pod: `kubectl top node nuc0`.

## Step 1 — the token

There is no GitHub UI step and no token to paste. The Secret is seeded from the machine's own token,
so the whole of Step 2 is one command:

```bash
set -a; . ~/.config/secrets/keys.env; set +a
printf '%s' "$GITHUB_PERSONAL_ACCESS_TOKEN" \
  | kubectl -n docs create secret generic github-docs-ro --from-file=token=/dev/stdin
```

The value never reaches the command line (so not `/proc/*/cmdline`, not shell history), and the same
command rotates it with `--dry-run=client -o yaml | kubectl apply -f -`.

**What that token can do.** It is the operator's machine token: it can push. The builder only reads
with it, and three things bound the risk rather than the token's rights:

- the build runs as uid 65534, so a repository's `hooks:` code cannot read the token out of the
  builder's `/proc/1/environ`;
- the pod mounts no service-account token, so that code cannot reach the API server either;
- the token is never written to the state file, a log line, or the served tree.

A dedicated fine-grained token (`Contents: read`, `Metadata: read`, all repositories) is still the
better end state when this one is rotated by hand anyway; nothing in the chart changes, only this
command.

## Step 2 — the namespace and the Secret

The Secret must exist before the CronJob's first pod starts, so the namespace comes first:

```bash
kubectl create namespace docs
```

Then the command from Step 1, and a check that the key holds what was read:

```bash
kubectl -n docs get secret github-docs-ro -o jsonpath='{.data.token}' | base64 -d | head -c 8
# expect: the first characters of the token (github_pat_…), then no error
```

The builder reads `GITHUB_TOKEN` from that key and never writes it anywhere: not to the state file,
not to a log line, not into the served tree.

## Step 3 — sync the Application

The commit adding the chart and the Application reaches the cluster through the root Application
(see [`../../AGENTS.md`](../../AGENTS.md) §2). This Application carries no `syncPolicy.automated` —
that lives in the ArgoCD UI, and a PR that adds it to the file is reverted on the next root sync — so
enable auto-sync once in the UI, or click **Sync**.

```bash
kubectl -n argocd get application docs-site \
  -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
# expect: Synced Healthy
```

The route is gated by authentik, and the provider it names comes from the `authentik` Application,
not this one: sync `authentik` too, or wait for its blueprint scan. Until `app-docs.yaml` has been
applied, `https://docs.kub.local/` answers authentik's 404 instead of a sign-in redirect (Step 6).

## Step 4 — the nginx pod is up

```bash
kubectl -n docs get pods
# expect: docs-site-… , READY 1/1, STATUS Running
```

The init container creates `/data/site` and leaves a placeholder page only when there is none, so the
pod is ready before the first build. A pod stuck in `CreateContainerConfigError` is the missing
Secret from Step 2.

The route is gated, so an unauthenticated request never reaches the placeholder — it is redirected to
authentik instead. That 302 is Step 6's first check; here it only proves the pod serves something:

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://docs.kub.local/
# expect: 302
```

## Step 5 — the first build

```bash
kubectl -n docs create job --from=cronjob/docs-site-build docs-first-1
kubectl -n docs logs job/docs-first-1 -f
# expect: one line per (repository, ref) — build, skip or absent — and a closing summary
```

Expected shape, not a fixed list: the repositories are discovered, not listed anywhere.

```
# full rebuild: FULL_REBUILD_AGE elapsed or no previous full run
build homelab/develop <sha>
absent homelab/main no docs/ directory
…
# build 21, absent 7, API calls left 4936/5000
```

What must **not** appear: a traceback, or a `GITHUB_TOKEN` in any line. A `403` or `404` on every
repository is the token's permissions (Step 1), not the builder.

The first run takes about a minute for the current catalogue; the job's own deadline is 900s.

## Step 6 — the gate, then the site

The gate first. Without a session, the route answers a redirect to authentik's sign-in page:

```bash
curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' https://docs.kub.local/
# expect: 302 followed by the authentik sign-in address
```

A **404** here is not a missing route: it is authentik saying it has no provider for this host, which
means the blueprint `cluster/helm/authentik/files/app-docs.yaml` has not been applied yet, **or** that
the `docs` provider exists but is not attached to the embedded outpost — an unattached provider
answers the same 404. Sync the `authentik` Application (or wait for its next blueprint scan) and
repeat. A **200** means the gate is missing from the route — the site is then protected by `lan-only`
alone.

Then sign in. Open `https://docs.kub.local/` in a browser; authentik asks for the operator account,
returns you to the index, and the site renders:

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://docs.kub.local/
# expect: 200 once the browser holds a session (the cookie is what the check needs)
```

```bash
curl -sk https://docs.kub.local/ | sed -n '1,60p'
# expect: the index table — one row per (repository, ref), the build time, and a status
```

```bash
curl -sk https://docs.kub.local/homelab/develop/ | head -20
# expect: the Material page for this repository's docs
```

A repository whose documentation has no `index.md` of its own gets a generated landing page that
lists its pages, so `/<repo>/<ref>/` always answers a page and never a directory listing.

The provider is bound to `authentik Admins`, so an account in `homelab-users` signs in to authentik
and is then refused by the application. Widening it is one more policy binding in the same blueprint;
nothing in this chart changes.

## Step 7 — the certificate

`-k` in the commands above accepts any certificate; that is deliberate, because those checks are
about what the site answers, not who signed it. The chain is checked once, here.

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  --cacert ~/.config/secrets/kub-local/local-ca.pem https://docs.kub.local/
# expect: 200

openssl s_client -connect docs.kub.local:443 -servername docs.kub.local </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer
# expect: subject=CN = kub.local
#         issuer=CN = ashulab internal CA
```

The TLS router sets `tls.options.name: internal-tls` and deliberately no `secretName` and no
`certResolver`: `kube-system/local-cert` is the default certificate, and `internal-tls` is the
kube-system TLSOption every internal app uses.

## Step 8 — rotation

The token is the machine's, so rotation is the same command as Step 1, replacing the Secret in place:

```bash
set -a; . ~/.config/secrets/keys.env; set +a
printf '%s' "$GITHUB_PERSONAL_ACCESS_TOKEN" \
  | kubectl -n docs create secret generic github-docs-ro \
      --from-file=token=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -
```

The next scheduled run reads the new value. A pod that is already running keeps the old one, so if a
job is in flight, either wait for it or delete it:

```bash
kubectl -n docs delete job -l batch.kubernetes.io/cronjob-name=docs-site-build
```

If the machine token is ever replaced by a dedicated read-only one, this is the only place that
changes.

## Success, in one place

- `Synced Healthy`, one nginx pod `1/1`, and `https://docs.kub.local/` answers `200` after the
  authentik sign-in.
- Without a session the same address answers `302` to authentik, and from outside the LAN a `403`.
- The index lists every repository with docs, and names the ones without, with the last build time.
- `https://docs.kub.local/homelab/develop/` shows the Material site.
- The certificate validates against `local-ca.pem`, `subject CN=kub.local`, `issuer CN=ashulab internal CA`.
- The first job's log has one line per pair, a closing summary, and no traceback and no token.
- `./hack/check-exposure.sh` passes.

## Rollback

The claim holds output only: every page on it can be rebuilt by the next run, so nothing is lost
except one build cycle.

Retire it in git **first**, as [`vikunja-first-start.md`](vikunja-first-start.md#rollback) explains —
deleting the Application with `kubectl` does not retire anything, because the root Application
recreates it on the next sync:

```bash
git fetch origin
git switch -c chore/retire-docs-site origin/develop
git rm cluster/argocd/projects/platform/docs-site.yaml
git rm -r cluster/helm/docs-site
git commit -m "🔥 remove the docs site"
git push origin chore/retire-docs-site
# then open a pull request against develop and merge it (AGENTS.md §Git workflow)
```

Then the namespace, which removes the workloads, the Secret and the claim:

```bash
kubectl delete namespace docs
```

And last the token in GitHub (Step 1), which is the credential this whole procedure creates.

## What this procedure does not cover

- Which repositories appear: discovery is the GitHub API, and a repository opts in with a `docs/`
  folder holding at least one Markdown file. There is no list to edit.
- The content of a site: a repository owns its own documentation, and its own `mkdocs.yml` if it has
  one.
- The daily full rebuild: `fullRebuildAge` in the chart's values, 86400s, no procedure needed.
- The gate and the certificate themselves: shared with every other internal application, see
  [`authentik-apps.md`](authentik-apps.md) and [`internal-ca.md`](internal-ca.md). The provider is the
  one object this procedure adds to authentik, through `app-docs.yaml`.

## Measured

Pending — filled in after the operator runs Steps 1 to 7 and pastes 4, 5 and 7 back, and again after
the acceptance checks in the plan's Task 6 (discovery latency, merge latency).
