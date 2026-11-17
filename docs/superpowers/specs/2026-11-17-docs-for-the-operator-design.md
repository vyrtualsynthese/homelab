# Documentation for the operator — design

Date: 2026-11-17
Status: draft for operator approval
Supersedes: the documentation shape sketched in
[`2026-11-10-homelab-stabilisation-design.md`](2026-11-10-homelab-stabilisation-design.md) §8.6, which
named `docs/architecture.md` and `docs/disaster-recovery.md`; neither was ever written.

## 1. Intent

The documentation has to serve one reader — the operator — in three ways:

1. **Understand and remember why the homelab is built this way.** Not the dated audit, not the plan
   history: the shape, the constraints behind it, and the handful of rules that explain most of the
   odd-looking choices.
2. **Read the in-depth material agents wrote.** Designs and plans are already in the repo and stay in
   the repo. A Vikunja ticket points at one; the operator wants to open it, read it comfortably, and
   find the others like it.
3. **Run the homelab without an agent.** The routine jobs — ship a change, find what broke, roll it
   back, add an app, fix a stuck claim, rotate a secret — need a page each, written as steps.

The failure this fixes: the docs are accurate and dense, but they are organised as an agent's
briefing, not a person's handbook. The home page tells a reader to "read in this order" through an
audit; the biggest pages are 5,000-word reference walls; the file tree is the navigation; and there is
almost nothing to do, only things to know.

## 2. What exists today

Measured on `develop` at `8e3fa6c1`, 2026-11-17.

- `https://docs.kub.local` already renders every repository's `docs/` folder with MkDocs Material.
  The `docs-site` chart rebuilds a moved branch head within a minute or two. The site is behind
  `lan-only` and the authentik forward-auth gate.
- The homelab repository has **no `mkdocs.yml`**. The builder therefore uses its shared template:
  navigation is the raw file tree, Material search works, Mermaid renders. `docs/README.md` is the
  home page because MkDocs treats it as the index.
- The living documentation is about **46,000 words**; `docs/superpowers/` holds **31 plans and 27
  specs**, about **155,000 words**. Both are readable; neither is navigable by theme.
- The three longest living pages are `runbooks/authentik-apps.md` (5,378 words),
  `charts/README.md` (5,690) and `observability-conventions.md` (4,153). Each contains several
  distinct documents glued together.
- `status.md` is a cluster snapshot dated 2026-11-14 and is already stale. The audits are dated
  2026-11-10 by design.
- `runbooks/README.md` lists six runbooks that do not exist, and omits none of the files that do.
- `AGENTS.md` §5 duplicates `docs/secrets.md` and is already known to be out of date.
- `main` carries no `docs/` folder at all and last moved on 2026-05-03; it is also GitHub's default
  branch. The docs site's `main` row therefore reads "no docs on this ref". This project works on
  `develop` only and files the `main` question as a separate task.

## 3. Goals

- A home page that says what the place is and where to start, in one screen.
- Three reading paths that each work end to end: understand, operate, and read agent work.
- Every living topic has one obvious page, with the reasoning and the procedure separated.
- Every routine day-2 job has a page the operator can follow without asking an agent.
- The plans and specs stay exactly where they are, and become reachable by theme and by ticket.
- A reader can tell a living page from a snapshot and a completed record at a glance.
- Links that exist today keep working, or land on a page that says where the content went.

## 4. Non-goals

- Editing, moving, renaming or deleting anything under `docs/superpowers/`. The writing-plans and
  executing-plans skills find files by path; this project does not touch them. The only file added
  there is this design, and later its plan, as the convention requires.
- Rewriting the in-depth plans into summaries. They are the record. This project indexes them and
  makes them comfortable to read.
- Replacing Markdown in git with a wiki or a database. The docs site is a reading surface.
- Writing the backup, Ansible or disaster-recovery procedures that do not exist yet. The pages say
  what is true and link the designs.
- Rewriting `AGENTS.md`. It stays the agent brief. One edit is in scope: §5's stale secret table is
  replaced by a pointer to `docs/secrets.md` after the inventory moves, because two tables for the
  same facts is the defect this project exists to remove. No other section is reworded.

## 5. Anti-goals

- **A second source of truth.** Moving content is allowed; copying it is not. Each fact has one
  home, and the other pages link to it.
- **A navigation so deep it becomes a maze.** Seven top-level tabs, two clicks to any living page.
- **A rewrite that loses the reasoning.** The new pages summarise and point at the deep documents;
  they do not replace the design's argument with a to-do list.
- **Pages that look maintained but are not.** Every living page carries a verified date. Every
  snapshot says it is one.

## 6. Constraints

- MkDocs Material 9.8.1 in the builder, with `pymdownx.superfences` for Mermaid. The same files must
  stay readable on GitHub, so no MkDocs-only syntax in the body (no `!!!` admonitions; use
  blockquotes).
- `docs/README.md` remains the home page. MkDocs treats it as the index, and `AGENTS.md` and the root
  `README.md` already link to it.
- The docs site builds `main` and `develop` only. A change is invisible until it reaches one of them.
- The site is gated, so agents and scripts cannot read it. Ticket `docs:` links stay on GitHub; the
  site link is for the human.
- Files under `cluster/argocd/**` keep CRLF; the new files are Markdown and use LF.
- No cluster change is needed. The only new non-Markdown file is `mkdocs.yml` at the repository root,
  which the builder already supports verbatim.
- Plain language: media and design terms are glossed the first time they appear in a page, the way
  `AGENTS.md` requires of agents.

## 7. Approaches considered

**A — Curated map only.** Add `mkdocs.yml` with a hand-written nav, a new home page and section
landing pages; leave every page's content untouched. Cheap and low-risk, but it fixes navigation
only: the walls stay walls and the stale pages stay stale.

**B — Rewrite the evergreen layer, keep the history layer. (chosen)** Same nav and home page as A,
plus new "how it works" and "running it" pages, splits along seams the long pages already have, and
explicit freshness labels. `docs/superpowers/` is untouched; the audits and decisions stay as dated
records.

**C — A separate handbook beside the docs.** Fastest to write and worst to own: two sources of truth
for the same cluster, which is the failure the existing documentation was built to prevent.

B costs more writing than A, but it is the only option that delivers all three intents. The operator
chose B on 2026-11-17.

## 8. Information architecture

Seven top-level sections. The tree below shows the living pages and marks new pages with `+`.

```text
docs/
  README.md                              rewritten: start here
  how-it-works/                          new section
    README.md                            + the homelab in one page, with a diagram
    the-shape-and-why.md                 + what broke in 2026, and the rules that followed
    nodes.md                             + four cluster nodes, two edge Pis, placement, heat
    gitops.md                            + the repo, ArgoCD, the four app patterns, sync policy
    storage.md                           + local-path, NFS classes, Longhorn's corpse, backups
    network.md                           + Traefik, DNS, the LAN gate, TLS and the internal CA
    identity.md                          + authentik, the groups, and how an app is protected
    observability.md                     + what watches the cluster, and what it misses
    media.md                             + Plex, the *arr apps, qBittorrent, and the books pair
    applications.md                      + the catalogue: every app, where it runs, what protects it
  running-it/                            new section
    README.md                            + "I want to…" index
    every-day.md                         + the five-minute check
    ship-a-change.md                     + edit, validate, PR, merge, sync, verify
    roll-back.md                         + revert, roll back, restart, and what not to do
    add-an-application.md                + chart, Application, route, gate, secret, claim, docs
    fix-a-pending-pod.md                 + decide the cause before changing anything
    rotate-a-secret.md                   + both copies, one ALTER ROLE, one restart
    use-the-board.md                     + columns, cards, validation, and the plan link
    rebuild-the-docs-site.md             + how the site builds, and why a page is missing
    disaster-recovery.md                 + what would be lost today, and what exists
  reference/                             new section for the long-form detail
    README.md                            + index
    chart-notes.md                       + per-app chart decisions, moved out of charts/README
    chart-sizing.md                      + measured resource sizing, moved out of charts/README
    metrics.md                           + scrape model, exporters, measured health facts
    alerts.md                            + the 29 rules, silences and known gaps
    dashboards.md                        + the seven dashboards and how to change one
    sso-applications.md                  + per-app SSO matrix and the media-tools traps
    routes.md                            + hostname → app → gate → certificate → chart
  charts/README.md                       trimmed: the contract, then links to the two reference pages
  observability-conventions.md           trimmed: the overview and how to query, then links
  secrets.md                             completed with the inventory from AGENTS.md §5
  status.md                              refreshed, dated, and retitled "what is broken right now"
  agent-protocol.md                      kept; gains the `site:` line and a summary block
  agent-work.md                          + how to read the plans, and an index by theme
  qbittorrent-webui-stalls.md            kept; labelled an open fault
  runbooks/                              existing procedures, README rewritten, statuses added
  decisions/                             kept; README rewritten as a real index
  audit/                                 kept; snapshot banner on each, new README index
  superpowers/                           untouched (designs and plans)
```

### Navigation

`mkdocs.yml` at the repository root carries an explicit `nav:`. The builder uses a repository's own
`mkdocs.yml` verbatim, so this one file controls the whole over-`develop` site.

| Tab | Contains | Purpose |
|---|---|---|
| Start here | `README.md` | The one-screen answer to "what is this and where do I start?" |
| How it works | `how-it-works/` | The mental model, one topic per page |
| Running it | `running-it/`, `status.md` | Day-2 tasks, plus what is currently broken |
| Reference | `reference/`, `charts/README.md`, `secrets.md`, the qBittorrent fault | The long-form facts the other pages point at |
| Runbooks | `runbooks/` | Procedures that were executed at least once |
| History | `decisions/`, `audit/` | Why a choice was made, and what was true on a date |
| Agent work | `agent-work.md`, `agent-protocol.md` | How to read a plan, and the board protocol |

Material features: `navigation.tabs`, `navigation.sections`, `navigation.indexes`, `navigation.top`,
`toc.follow`, `content.code.copy`, `search.suggest`, `search.highlight`. Each section directory has a
`README.md` that becomes its landing page. MkDocs 1.6 treats a missing nav file as a warning, not a
failure, so `main` (which lags `develop`) still builds while the new pages are missing.

## 9. What happens to each existing page

| Page | Action |
|---|---|
| `README.md` | Rewritten as the home page: what the place is, five rules worth remembering, three reading paths, where each kind of knowledge lives. |
| `agent-protocol.md` | Kept. Gains a summary block and the `site:` line in a ticket's fixed block. Linked from `running-it/use-the-board.md`. |
| `status.md` | Refreshed from the live cluster on 2026-11-17; retitled; summary block names it a snapshot and gives the refresh commands. |
| `secrets.md` | Kept at its path; absorbs the `AGENTS.md` §5 inventory, marked "compiled from the charts, not verified against the cluster". Linked from `running-it/rotate-a-secret.md`. |
| `charts/README.md` | Keeps the contract, the configuration rules, the UID trap and the checks. "Resource sizing" moves to `reference/chart-sizing.md`; "What good looks like" moves to `reference/chart-notes.md`. Inbound anchors are updated. |
| `observability-conventions.md` | Keeps "what runs where", "how to query" and "how metrics get in". Exporter quirks, health facts, dashboards, alert rules, silences and gaps move to `reference/metrics.md`, `dashboards.md` and `alerts.md`; this page becomes the overview and links them. |
| `qbittorrent-webui-stalls.md` | Kept at its path; summary block labels it an open fault with no fix applied. Linked from `how-it-works/media.md`. |
| `runbooks/README.md` | Rewritten: only files that exist, one line each, a status (`proved`, `pending`, `completed record`) and when to use it. |
| `runbooks/authentik-apps.md` | Kept at its path, because chart values point here. Trimmed to the procedure, access levels, MFA and verification. The per-app matrix, the media-tools detail and the traps move to `reference/sso-applications.md`. |
| `runbooks/books-cutover.md` | Kept; marked a completed record, kept because it is the only write-up of the books stack's first cutover. |
| `runbooks/docs-site-first-run.md` | Kept; marked a completed record. Linked from `running-it/rebuild-the-docs-site.md`. |
| `runbooks/internal-ca.md`, `postgres-add-database.md`, `postgres-restore.md`, `alert-relay.md` | Kept as living procedures; linked from the relevant "running it" and "how it works" pages. |
| `runbooks/vikunja-database.md`, `vikunja-first-start.md`, `vikunja-board-seed.md` | Kept; marked completed records (the board is running). `vikunja-database.md` is still the reference when the board's database is rebuilt. |
| `decisions/*` | Kept. `README.md` becomes an index that explains the record format and lists the two records with their one-line decision. |
| `audit/*` | Kept. Each gains a three-line snapshot banner and a link to `status.md`. New `audit/README.md` explains that this is history, not current state. |
| `AGENTS.md` | One edit: §5's secret table is replaced by a pointer to `docs/secrets.md` in the stage that completes the inventory. No other section changes. |

No historical file is deleted. The only deletions are seven dead rows in `runbooks/README.md` and the
content that has moved to a new home (checked with `grep` before removing).

## 10. Page conventions

Every living page opens with one blockquote, in this shape:

```markdown
# Plain title

> **In one line.** What this page answers, in one or two sentences.
>
> **Use it when** … · **Verified** 2026-11-17 · **Kind:** living page
```

`Kind` is one of `living page`, `snapshot` (with the date), or `completed record` (with the date).
The blockquote renders on GitHub too, unlike a MkDocs admonition.

- **Order.** The answer first, then the why, then the procedure, then links. A reader who stops after
  the first paragraph still has what they came for.
- **Length.** An overview page aims for 300–800 words and one diagram. A how-to aims for one screen
  per step and under 1,200 words. A reference page may be long but starts with a summary table and a
  table of contents; any section over ~150 lines gets its own file.
- **One fact, one home.** A page that needs a fact from elsewhere links to it and says why it
  matters. It does not restate it. `status.md` is the only page that carries counts.
- **Diagrams.** Mermaid, as code, for the whole system, the media pipeline and the request path. The
  GitHub rendering is the fallback.
- **Vocabulary.** Media and design terms are glossed once, in plain words: "the *arr apps (Sonarr,
  Radarr, Lidarr and Prowlarr, the tools that fetch and organise media)", "an indexer (the search
  site a *arr app asks for releases)". System terms need no gloss for this reader.
- **Freshness.** A page whose facts came from a snapshot says so; a page verified today says
  "Verified 2026-11-17". Re-verify rather than guess.

## 11. Content plan for the new pages

This is the contract between the design and the plan. Each entry says what the page must answer and
which existing file is its source. The builder writes the page; it does not invent facts.

### How it works

| Page | Must answer | Source |
|---|---|---|
| `README.md` | What are the moving parts, how does a request reach an app, and how does a commit reach the cluster? One diagram, one page, five rules to remember. | stabilisation design §8, `AGENTS.md` §1–§4 |
| `the-shape-and-why.md` | What failed in 2026, what it exposed, and the four constraints that explain the shape: no replicated storage, every pod pinned, GitOps for apps, free software only. What is accepted as a risk and why. | stabilisation design §1–§7, §13; `audit/observability-storage.md` |
| `nodes.md` | Which machines exist, what each is for, what runs where, how placement and `local-path` interact, what "no failover" costs, and the two accepted thermal faults. | design §8.1; decisions; live cluster |
| `gitops.md` | How a commit reaches the cluster, the repo layout, the four chart patterns, why sync policy is in the UI, and what to edit for a given change. | `AGENTS.md` §2–§3; design §8.3–§8.4 |
| `storage.md` | Why config is on `local-path` and media on NFS, the claim naming, the binding trap, what happened to Longhorn, and the honest state of backups. | design §8.2; `charts/README.md` UID section; audit storage |
| `network.md` | How traffic enters, DNS names, the LAN gate, TLS with the internal CA, the public hostname list, and the difference between the headers middleware, the forward-auth gate and OIDC. | design §8; `charts/README.md`; `runbooks/internal-ca.md`; live routes |
| `identity.md` | authentik's role, the two sign-in sources, the three access levels, MFA, break-glass, and the rule for choosing a mechanism per app. | `runbooks/authentik-apps.md`; SSO designs |
| `observability.md` | What watches the cluster, how metrics and logs arrive, what the dashboards are for, where alerts go, and the known holes. | `observability-conventions.md`; split reference pages |
| `media.md` | The pipeline from indexer to playback, the storage paths, the books pair, and which apps are gated how. Gloss the media vocabulary. | cluster values; authentik runbook; books design |
| `applications.md` | The catalogue: application, namespace, node, route, protection, chart pattern, and a pointer to status. | live cluster + `cluster/argocd/projects` |

### Running it

| Page | Must answer |
|---|---|
| `README.md` | "I want to…" table: symptom or intention → page or runbook. |
| `every-day.md` | What to look at in five minutes: cluster health, firing alerts, the board's validation queue, disk and heat. What "normal" looks like, and the three accepted faults. |
| `ship-a-change.md` | Branch, edit, the local checks, PR, merge, sync, verify. The sync-policy trap. Where a docs change goes. |
| `roll-back.md` | Revert in git versus roll back in ArgoCD versus restart a pod; what each does to a claim; why `kubectl edit` is not the answer. |
| `add-an-application.md` | The checklist: chart, `values.yaml`, Application, node pin, claim, route, middleware, secret, docs page, verification. |
| `fix-a-pending-pod.md` | The decision tree: unschedulable claim, claim bound to the wrong node, missing Secret, image pull, resource request. The commands to tell them apart. |
| `rotate-a-secret.md` | The inventory, the two copies a database password has, `ALTER ROLE`, the restart, and the verification. |
| `use-the-board.md` | Columns, three card kinds, how to file a card, how to validate one, what `site:` adds, and how to read the ticket's plan. |
| `rebuild-the-docs-site.md` | How the site is built, the manual Job, why a page is missing (ref not built, build failed, page not in nav), and where the index says so. |
| `disaster-recovery.md` | What would be lost today, what does not exist yet (backups, Ansible, a tested DR runbook), and which designs own the fix. No invented procedure. |

### Reference

| Page | Must answer |
|---|---|
| `README.md` | What lives here and why it is long-form. |
| `chart-notes.md` | The per-app chart decisions and exceptions, moved from `charts/README.md` "What good looks like", with the app names as headings. |
| `chart-sizing.md` | The measured resource tables and the rule for re-measuring, moved from the same file. |
| `metrics.md` | How scraping works, exporter quirks, and the measured facts (series, targets, drives, temperatures) with their date. |
| `alerts.md` | The 29 rules and what each protects, the silences and why, the pattern rules, and the known gaps. |
| `dashboards.md` | The seven dashboards, what each is for, and the API calls that change one. |
| `sso-applications.md` | The per-app matrix (native OIDC, proxy, gate, LAN only), the media-tools detail, the traps, and the procedure to add a tool. |
| `routes.md` | Every hostname with its app, gate, TLS and whether it is public. |

### Agent work

`docs/agent-work.md`:

- what `docs/superpowers/` is, why the paths are fixed, and how a spec and a plan pair up;
- how to read a plan (the checkbox convention, and that the board is the state);
- an index by theme, one line per document, linking the plan and its spec: stabilisation
  programme, media charts, books, PostgreSQL, identity, observability, board and docs, and the
  smaller one-offs;
- a note that the files are written for agents: the top block and the checkboxes are instructions,
  not decoration, and the body is the reasoning.

## 12. MkDocs configuration

`mkdocs.yml` at the repository root, using the same extensions as the builder's template plus the
navigation features:

- `site_name: AshuLab homelab`, `docs_dir: docs`, `use_directory_urls: true`.
- Theme `material`, `scheme: slate`, the seven features listed above.
- `markdown_extensions`: `admonition`, `attr_list`, `def_list`, `tables`, `toc` with permalinks,
  `pymdownx.superfences` with the Mermaid fence, `pymdownx.details`, `pymdownx.tabbed` in
  `alternate_style`.
- `plugins: [search]`.
- An explicit `nav:` for the seven tabs and the living pages only. Plans and specs are not listed
  one by one; they are reached from `agent-work.md` and from search. MkDocs still builds and indexes
  them.
- `validation.nav.omitted_files: ignore`, so the untouched `superpowers/` pages do not produce a
  warning on every build. `absolute_links` and `unrecognized_links` stay at their defaults; the
  render check below decides whether they need tuning.
- The nav is grown stage by stage, alongside the four PRs. MkDocs 1.6 treats a missing nav file as a
  warning, not a failure, so a stage that lists a page the next stage writes still builds.

## 13. Tickets and the docs site

A ticket's fixed block currently carries `repo:`, `docs:`, `branch:` and `pr:`. It gains one line:

```text
site: [Rendered page](https://docs.kub.local/homelab/develop/superpowers/plans/<plan>/)
```

- `docs:` keeps the GitHub URL, because agents cannot get through the authentik gate.
- `site:` is the rendered page, for the human. It points at `develop`, the branch the site builds
  while work is in progress. It is a Markdown link, never a bare URL: a bare URL renders inert and
  is a defect in the card, the same rule that applies to `docs:`.
- The path inside the site is the file's path under `docs/` with `.md` removed and a trailing slash:
  `docs/superpowers/plans/x.md` → `superpowers/plans/x/`; `docs/agent-protocol.md` →
  `agent-protocol/`.
- `site:` is added only when the file exists on `develop`. A plan that lives on a feature branch
  renders nowhere until it merges; the protocol says so instead of offering a 404.
- `docs/agent-protocol.md` gains the line and the rule, and `render_description()` in
  `hack/vikunja_migrate.py` renders it, because that function is what writes the fixed block on
  generated cards. `hack/vikunja-seed.sh` only carries the project front-page blurb and needs no
  change.
- **One backfill run.** A one-off script with a `--dry-run` adds `site:` to cards in `Backlog`,
  `Doing` and `Validation` that carry a `docs:` path and no `site:` line. `Done` and `Idea` cards are
  left alone. The script is idempotent and reports what it would change.
- A re-run of `hack/vikunja_migrate.py` compares descriptions, so its renderer must know about
  `site:` before the backfill; otherwise it would strip the line from every card in its map. The
  renderer change lands first.

## 14. Freshness and history

- `status.md` is the single page with counts and broken-app lists. It is refreshed in this project
  and carries the date and the commands to refresh it.
- The audits become explicitly historical: a banner, a link to `status.md`, and no edits to their
  findings.
- The runbooks keep a status: `proved <date>`, `pending`, or `completed record <date>`. The README
  table lists only files that exist.
- Decisions keep their format; the index explains it.
- `qbittorrent-webui-stalls.md` is an open fault, not history. It moves under Reference in the nav
  and keeps its path.

## 15. Delivery in four stages

The work ships as four pull requests, not one. Each is rebased on `develop`, merged before the next
starts, and appears on `https://docs.kub.local/homelab/develop/` while the next is being written.
The operator reviews six to ten pages at a time instead of thirty.

| Stage | Pull request contains | What is usable afterwards |
|---|---|---|
| 1 | `mkdocs.yml`, the new home page, all of `how-it-works/` (10 pages) | The site's navigation, the mental model, and the answer to "why is it like this?" |
| 2 | All of `running-it/` (10 pages) and the refreshed `status.md` | The day-2 tasks, and a current answer to "what is broken?" |
| 3 | `reference/` (8 pages), the `charts/README.md` and `observability-conventions.md` splits, `secrets.md` completed, the `AGENTS.md` §5 pointer, the `runbooks/README.md` rewrite and statuses | The long-form detail, the complete secret inventory, and an honest runbook index |
| 4 | `agent-work.md`, the `hack/vikunja_migrate.py` renderer and the one-off backfill, the `audit/` and `decisions/` indexes (the protocol's `site:` line landed in stage 2, with the board page) | Plans reachable by theme and from a ticket, and history clearly labelled |

The board carries one **feature** card, "Documentation for the operator", and four **task** cards,
one per stage. Each task gets the branch, the PR and the verification when the stage is handed over.
The home page is written in stage 1 with the links that exist then, and gains the "running it" and
"read a plan" paths in stages 2 and 4. No stage links to a page a later stage will create: the stage
1 pages point at the existing `observability-conventions.md`, `charts/README.md` and runbooks, and
stage 3 rewires those links to the new `reference/` pages.

Every stage, before handover:

1. **It renders.** Build with MkDocs Material 9.8.1 (the builder's exact image, or an equivalent
   environment): `mkdocs build` exits 0. Warnings are read one by one. A broken link between living
   pages is a failure; a link to a file outside `docs/` is accepted and recorded.
2. **Links resolve.** A grep over the repository for `docs/` paths finds no reference to a file that
   moved without its landing page. The chart READMEs, `AGENTS.md`, the root `README.md`, `hack/`
   scripts and the docs themselves are checked. In stage 3, the `AGENTS.md` §5 pointer is the one
   expected change to the brief.
3. **The superpowers folder is untouched.** `git diff --stat -- docs/superpowers` lists only
   additions: this design and, once written, its plan.
4. **The board describes this work.** The stage's task card carries the branch, the PR and the
   verification, and sits in `Validation`.
5. **The operator accepts the stage in the browser**, after its merge, on the `develop` site: the
   pages read as intended, the tab is in place, and one task on a "running it" page can be followed.

The project is finished when the operator can say yes to all three intents from §1: explain the
shape from the docs, open a plan from a ticket, and change or fix one thing using only a page.

## 16. Left to the builder

- The exact wording, examples and diagrams of every new page. The sources and the questions are
  fixed; the prose is not.
- Which existing page keeps a summary of a fact that now lives in `reference/`, and which links
  instead. The rule is "one home", but the seam between `how-it-works/observability.md` and
  `reference/metrics.md` will need judgement.
- The final order inside each tab, and the exact page titles.
- The wording of the three-line banner used on snapshots, as long as it is consistent.
- The implementation of the one-off `site:` backfill: it must be idempotent, offer `--dry-run`, and
  leave `Done` and `Idea` cards alone. The exact output format is the builder's call.

## 17. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| A split page loses its inbound links | A chart README or a ticket lands on a 404 | Keep the old path as the overview page; update every grep hit; check before removing content |
| Two pages state the same fact and drift | The exact problem this project fixes | One home per fact; links elsewhere; `status.md` owns the counts |
| The new pages duplicate the design's reasoning and drift from it | A reader trusts a stale summary | Each summary links the design section it came from; freshness date on the page |
| The docs site rebuilds only `main` and `develop` | Nothing appears until merge | Expected; the project works on `develop`, and every stage is checked there after its merge |
| Explicit nav makes untouched plan pages hard to find | The plans become less visible than today | `agent-work.md` indexes every plan by theme; search still indexes the text |
| The plans' agent instructions look odd on the site | Visual noise on a page meant for reading | Accepted; the files must not be edited, and the index tells the reader what the block is |
| A stage is reviewed while linking a page the next stage writes | 404s in the browser review | Each stage links only to pages that exist by its end; the plan checks links per stage |
| The `site:` backfill rewrites a description an agent edited | Lost hand-written context | Idempotent script, `--dry-run` first, open columns only, and the renderer is fixed before any backfill |
| A stale nav on `main` | Not applicable: `main` has no docs and is out of scope; a separate Backlog task owns it | The docs site already reports `main` as "no docs on this ref" |
