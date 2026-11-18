# docs-site

Every repository under `vyrtualsynthese` that carries a `docs/` folder, rendered as a MkDocs
Material site and served on the intranet at `https://docs.kub.local`.

This chart is self-contained (pattern A): there is no upstream chart for it. The builder is a Python
script shipped in the chart (`files/build.py`), rendered into a ConfigMap, and run by a CronJob every
minute. A second workload is an unprivileged nginx that serves the resulting directory read-only.

## What is built

- Only `main` and `develop`. Feature branches, tags and worktrees are out of scope.
- One site per (repository, ref), served at `/<repo>/<ref>/`. All of them are listed on `/`, as a
  card per repository with one row per branch, the time of its last build and, when there is no
  site, why.
- A failed build never replaces a working site: the previous one keeps serving and the index page
  says what broke and when.
- A repository joins by carrying a `docs/` folder with at least one Markdown file. Nothing is added
  to this repo, the chart or a list — discovery is the GitHub API. A repository leaves by dropping
  the folder.
- A repository that ships its own `mkdocs.yml` is built with it, verbatim. Otherwise the shared
  template in `files/mkdocs.yml.tpl` is used: Material, search, Mermaid, navigation from the file
  tree, so adding a page needs no nav edit. A site whose documentation has no `index.md` gets a
  generated landing page listing its pages.

## The identity and the branch bar

The site wears the AshuLab mark: navy `#0B1220`, cyan `#2DD4FF`, paper `#E8EEF5`, taken from
`assets/branding/ashulab/` at the root of this repository. `files/ashulab.css` carries the theme
(header, links, code, tables) and the branch bar's styling; `files/ashulab-badge.svg` and
`files/ashulab-badge-transparent.svg` are copies of the master mark, because a Helm chart can only
read files inside its own directory. Refresh those two copies when the mark changes — the build
script cannot reach `assets/`.

The builder publishes all three at `/brand/` before it builds anything, so they answer even on a run
that builds nothing, and every page links them at that absolute path. The shared template declares
them; for a repository whose own `mkdocs.yml` the builder does not touch, the builder adds the
stylesheet link and the bar to the rendered HTML instead. That is also why the bar carries its own
class names and no dependency on the repository's configuration.

Under the header, every page carries one bar: the way back to `/`, the repository, and the branch
chips. Only the branches actually published for that repository appear — usually `main` and
`develop`, one of them when the other has no `docs/`. A chip opens the same page on the other branch,
and that branch's start page when the page does not exist there, so the reader never lands on a 404.

Stamping happens after the builds, over the served tree rather than the checkout: what a chip may
offer depends on the pages that actually exist. A page already carrying the current stamp is left
alone; a repository rebuilt in the run has both of its branches restamped, because the page list on
one side may have changed what the other side can offer. Delete a page by hand and the next run
leaves a stale chip until that branch is rebuilt — a cosmetic loss, not a broken link, because the
fallback is the branch root.

The bar is attached after `</header>`, which is Material's markup, not a documented extension point.
A theme that renames the header still gets the bar after `<body>`; one that removes both gets a line
in the pod log and leaves the page as MkDocs wrote it.

`--strict` is never passed on the command line. Repositories link to files outside `docs/`, and a
strict build would fail on them forever; a repository that wants warnings to fail sets `strict: true`
in its own `mkdocs.yml`.

## The build runs repository code

A repository's own `mkdocs.yml` is trusted code: MkDocs executes its `hooks:` as Python in the builder
container. The trust boundary is therefore "repositories under the operator's account", and the
builder keeps the blast radius inside it: `mkdocs` runs as an unprivileged uid with every
credential-shaped variable stripped from its environment, so a hook cannot read the GitHub token out
of the builder's `/proc/1/environ`, and the pod mounts no service-account token, so a hook cannot
reach the API server either. `docs_dir` is also checked against the checkout before the build, but
that check is shallow — the real boundary is which repositories are in scope.

## Change detection

Each run asks GitHub for the head of every (repository, ref) with a conditional request, so an
unchanged pair answers `304` and costs no rate-limit budget. Only a moved head is fetched (a tarball
from codeload, not a clone) and rebuilt. A daily full rebuild (`fullRebuildAge`) catches a change to
the template itself.

## Rebuilding by hand

```bash
kubectl -n docs create job --from=cronjob/docs-site-build docs-manual-1
kubectl -n docs logs job/docs-manual-1 -f
```

Cluster writes are the operator's, not an agent's: the site is not an agent's output.

## Why the route is LAN-only and behind authentik

`docs.kub.local` carries `kube-system-lan-only@kubernetescrd` and then
`kube-system-authentik-forwardauth@kubernetescrd`, in that order. The content is private repository
documentation — paths, hostnames, the odd credential-shaped string if a repository is careless — so
it is not a public page: the LAN check refuses an off-LAN request before authentik is asked anything,
and the forward-auth gate then requires a session from the provider `docs` (blueprint
`cluster/helm/authentik/files/app-docs.yaml`, bound to `authentik Admins`).

The site is static files and has no identity of its own, so there is no native OIDC to prefer; the
middleware is the only mechanism available. The TLS router uses the cluster's default certificate
(`internal-tls` option in `kube-system`, no `secretName`, no `certResolver`), and the `web` router
redirects to it after the LAN check.

## First run and the token

The builder reads the token from Secret `github-docs-ro` key `token`, seeded from the machine token in
`~/.config/secrets/keys.env` — no GitHub UI step. First run, rotation and rollback are in
`docs/runbooks/docs-site-first-run.md`.

## Rendering locally

```bash
helm template docs-site cluster/helm/docs-site -n docs
```

The builder can also run outside the cluster; `python3 cluster/helm/docs-site/files/build.py
--dry-run` prints the plan and writes nothing (it needs `GITHUB_TOKEN`, `SITE_ROOT` and `STATE`).

The identity, the branch bar and the landing page have tests that need none of that:

```bash
python3 hack/tests/test_docs_builder.py
```

They run the builder's own functions against a temporary served tree: the link and the bar are
written once, a chip falls back to the branch root when the page does not exist there, an idle pass
rewrites nothing, and the landing page keeps naming every branch after a failure.
