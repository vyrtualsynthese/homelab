# Rebuild the docs site

> **In one line.** The docs site is a render of every repository's `docs/` folder, rebuilt within a
> minute of a branch head moving; its index is the only place that says why a page is missing.
>
> **Use it when** a page is missing or stale, or a build has to be run by hand · **Verified**
> 2026-11-17 · **Kind:** living page

## What runs

`https://docs.kub.local/` serves one site per repository and ref, at `/<repo>/<ref>/`: this repository
on `develop` is `/homelab/develop/`. The root page is a catalogue: one row per pair, with the verdict.

The builder is a CronJob in namespace `docs` running a Python script in the MkDocs Material image. An
unprivileged nginx serves the same directory read-only on port 8080. Both are pinned to `nuc0`, where
the rendered tree sits on a `local-path` claim; a pod moved elsewhere cannot mount it.

No list is maintained in this repository or the chart. A repository joins by carrying a `docs/` folder
with a Markdown file, discovered through the GitHub API; one shipping its own `mkdocs.yml` is built
verbatim, the rest with a shared template. The chart and its trust model are in
[the chart's README](../../cluster/helm/docs-site/README.md).

## When it rebuilds

It runs every minute, and cheaply: the builder asks GitHub for each branch head with a conditional
request, so an unchanged head answers `304` at no rate-limit cost. Only a moved head is fetched — a
tarball, not a clone — and rebuilt.

Only `main` and `develop` are built; feature branches, tags and worktrees are out of scope, so a page
written on a branch appears after the merge. A full rebuild runs daily (`fullRebuildAge`, 86400s),
which catches a change to the shared template that no head records.

The state file at `/data/state.json` is what makes the next run cheap: per pair, the head it last saw,
the last good build, the verdict and the failure count. It sits outside the served tree; a changed
`builder.revision` retries failed pairs at once.

## Rebuild it by hand

```bash
kubectl -n docs create job --from=cronjob/docs-site-build docs-manual-1
kubectl -n docs logs job/docs-manual-1 -f
```

Wait for the next minute when the change is on `develop`; a hand-run job is for proving a fix landed. A
second run needs the next number in the name, and overlapping the scheduled run is tolerated.

Cluster writes are yours: an agent's kubeconfig is read-only, so an agent cannot run this job and will
ask you. First start, token, rotation and rollback are in
[the first-run runbook](../runbooks/docs-site-first-run.md).

## Why a page is missing

The index answers this. Usual causes, in order:

**The ref was never built.** Branches, tags and worktrees are not on the site; only `main` and
`develop` are.

**The page is not on that ref.** The row reads `no docs on this ref`: the folder is on `develop` and
not on `main`, the branch carries no `docs/` at all, or it carries one with no Markdown.

**The build failed.** The row reads `build failed:` followed by the reason, and what is served behind
it is the last good site, if there was one — so the page is old, not absent.

**The page is not in the navigation.** This repository lists its nav by hand in its own `mkdocs.yml`;
an unlisted page renders at its address but no link leads to it.

**The token cannot see the repository.** The row reads `not readable by the token`, a verdict only for
a repository the builder has seen before; one never listed is missing from the index silently, so check
the token's repository selection — a *selected* fine-grained token hides the rest.

## Read a failure

Start at the index with the status column: an old row with `build failed:` is a finished run. The time
is the last good build, not the last attempt, and the index is written at the end of a run only, so a
run that dies mid-build leaves the previous one standing.

Then the Job's log: one line per pair (`build`, `skip`, `absent`, `failed`) and a closing summary. The
diagnosis is the `# mkdocs exit <code>:` line; the log keeps the tail of MkDocs output, the index a
one-line, 400-character cut. A failed build is kept in `/data/work/.failed/` for about fifteen minutes
(`WORK_SWEEP_SECONDS`).

`403` or `404` on every pair is the token's permissions, not the builder.

## Why it is gated

The route carries `lan-only` then `authentik-forwardauth`: an off-LAN request is refused before
authentik is asked anything, and a LAN request still needs a session from the `docs` provider, bound to
`authentik Admins`.

The content is the argument: private pages, paths and hostnames. Static files have no identity of their
own and no OIDC to prefer, so forward-auth is the only mechanism there is.
