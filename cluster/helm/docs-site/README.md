# docs-site

Every repository under `vyrtualsynthese` that carries a `docs/` folder, rendered as a MkDocs
Material site and served on the intranet at `https://docs.kub.local`.

This chart is self-contained (pattern A): there is no upstream chart for it. The builder is a Python
script shipped in the chart (`files/build.py`), rendered into a ConfigMap, and run by a CronJob every
minute. A second workload is an unprivileged nginx that serves the resulting directory read-only.

## What is built

- Only `main` and `develop`. Feature branches, tags and worktrees are out of scope.
- One site per (repository, ref), served at `/<repo>/<ref>/`. All of them are listed on `/`, with
  the last build time and, when there is no site, why.
- A failed build never replaces a working site: the previous one keeps serving and the index page
  says what broke and when.
- A repository joins by carrying a `docs/` folder with at least one Markdown file. Nothing is added
  to this repo, the chart or a list — discovery is the GitHub API. A repository leaves by dropping
  the folder.
- A repository that ships its own `mkdocs.yml` is built with it, verbatim. Otherwise the shared
  template in `files/mkdocs.yml.tpl` is used: Material, search, Mermaid, navigation from the file
  tree, so adding a page needs no nav edit. A site whose documentation has no `index.md` gets a
  generated landing page listing its pages.

`--strict` is never passed on the command line. Repositories link to files outside `docs/`, and a
strict build would fail on them forever; a repository that wants warnings to fail sets `strict: true`
in its own `mkdocs.yml`.

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

## Why the route is LAN-only

`docs.kub.local` carries `kube-system-lan-only@kubernetescrd` and nothing else. The content is
private repository documentation — paths, hostnames, the odd credential-shaped string if a
repository is careless — and a public route would need an authentication gate in front of every
page. The TLS router uses the cluster's default certificate (`internal-tls` option in `kube-system`,
no `secretName`, no `certResolver`), and the `web` router redirects to it after the LAN check.

## First run and the token

The builder needs a fine-grained GitHub token with `Contents: read` and `Metadata: read`, in Secret
`github-docs-ro` key `token`. Creating it, the first job, rotation and rollback are in
`docs/runbooks/docs-site-first-run.md`.

## Rendering locally

```bash
helm template docs-site cluster/helm/docs-site -n docs
```

The builder can also run outside the cluster; `python3 cluster/helm/docs-site/files/build.py
--dry-run` prints the plan and writes nothing (it needs `GITHUB_TOKEN`, `SITE_ROOT` and `STATE`).
