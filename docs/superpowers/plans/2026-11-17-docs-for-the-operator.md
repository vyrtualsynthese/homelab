# Documentation for the operator — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the homelab documentation into a seven-tab MkDocs site a single reader can navigate: understand why the cluster looks the way it does, run it without an agent, and reach the plans a ticket points at.

**Architecture:** One explicit `mkdocs.yml` at the repository root controls the site the docs-site builder already renders. The living documentation is reorganised into `how-it-works/`, `running-it/` and `reference/`; the three oversized pages are split in place, keeping their old paths as overviews; `docs/superpowers/` is only added to. The work ships in four pull requests, each merged to `develop` before the next starts.

**Tech Stack:** Markdown, MkDocs Material 9.8.1 (the docs-site builder's image), Mermaid, Python 3 for the link checker and the Vikunja scripts, `uv` for the build environment, the Vikunja REST API.

**Spec:** `docs/superpowers/specs/2026-11-17-docs-for-the-operator-design.md`

## Global Constraints

- `docs/superpowers/**` is added to, never edited, moved, renamed or deleted. The only additions are the design and this plan.
- Every existing documented path stays reachable. When content moves out of a page, the page keeps an overview and links the new home.
- A living page opens with the design's summary blockquote; its `Kind` is `living page`, `snapshot` or `completed record`.
- One fact, one home. `status.md` is the only page with counts. Other pages link instead of restating.
- Plain language. Gloss a media or design term the first time it appears in a page.
- The body must render on GitHub as well as in Material: blockquotes, not `!!!` admonitions.
- Work happens on `develop`. `main` has no `docs/` folder and is out of scope.
- No cluster objects change. The only non-Markdown file added outside `hack/` and `tests/` is `mkdocs.yml`.
- Build with `uv run --quiet --with 'mkdocs-material==9.8.1' mkdocs build -d /tmp/docs-build`; it must exit 0.
- `python3 hack/check_docs_links.py` must print `0 broken relative links` before every commit.
- Commit messages use the repo's emoji prefixes, e.g. `📝 docs: ...`.
- Never commit a secret, a token or a kubeconfig. The backfill reads its token from `VIKUNJA_API_TOKEN`.

## Review Focus

- **A ticket's `site:` link lands on a plan, not the home page.** The plan opens with the writing-plans instruction block and checkbox steps, which look like a to-do list. A reader must be told, on the index page, what that block is, and the board must say the plan checkboxes are not the state. Pinned in Task 17 and Task 20.
- **A split page leaves a dead anchor.** `observability-conventions.md` links to `charts/README.md#resource-sizing-the-reviewed-set`; moving that section breaks the jump even though the file still exists. Pinned in Task 12 with a grep for the old anchor and a link-check pass.
- **The `AGENTS.md` pointer starves an agent of the secret names.** §5 is injected into every session; replacing the table with a pointer must still say where the inventory is and that the charts decide the keys. Pinned in Task 15.
- **A living page quietly becomes a snapshot.** Counts, app lists and temperatures change. Only `status.md` may carry them, and it must state its date and the commands to refresh it. Pinned in Task 7.
- **The home page offers a path to a section that has not merged yet.** Stage 1's home page must not link to `running-it/` or `agent-work.md`; each stage adds its path. Pinned in the handover task of every stage.
- **A backfill run rewrites a description an agent edited.** The script must be idempotent, default to `--dry-run`, touch only open columns, and never remove the `docs:` line. Pinned in Task 21.
- **A nav entry points at a page a later stage writes.** MkDocs only warns, but the browser shows an empty tab. Each stage's nav lists only pages that exist at the end of that stage. Pinned in every task that edits `mkdocs.yml`.

---

## Board setup (before Task 1)

**Files:** none.

- [ ] **Step 1: File the feature and its four tasks**

In the `homelab` project, create one `feature` card **Documentation for the operator**, described with
`docs:` pointing at this plan and the design, then four `task` children, one per stage, each opening
with the protocol's fixed block and a `site:` line once the plan is on `develop`:

| Task | Stage |
|---|---|
| Site frame, link checker and How it works | 1 |
| Running it and a current status page | 2 |
| Reference splits, secrets and runbooks | 3 |
| Agent work, history and ticket links | 4 |

- [ ] **Step 2: File the `main` decision as a separate Backlog task**

Title: **Decide the develop → main release**. `docs: none`. Body: `main` carries no `docs/` folder,
last moved 2026-05-03, and is GitHub's default branch. Per `AGENTS.md` the release is the operator's,
from the canonical checkout. No branch or PR yet.

- [ ] **Step 3: Move the stage 1 task to Doing** when the implementation session starts.

---

## Stage 1 — Site frame and How it works (PR 1)

Start from a fresh branch: `git fetch origin && git switch -c feature/docs-stage-1 origin/develop`.

### Task 1: The link checker, the site config and the first page

**Files:**
- Create: `hack/check_docs_links.py`
- Create: `mkdocs.yml`
- Modify: `cluster/helm/media-stack/calibre-web-automated/README.md:194`
- Create: `docs/how-it-works/the-shape-and-why.md`

**Interfaces:**
- Produces: `hack/check_docs_links.py`, run as `python3 hack/check_docs_links.py` from the repository root, exit 0 when every relative Markdown link resolves on disk.
- Produces: `mkdocs.yml` with a `nav:` every later task appends to.

- [ ] **Step 1: Write the link checker**

`hack/check_docs_links.py`, stdlib only. Walk every `*.md` under the repository root; skip any path
containing `docs/superpowers`, `.git`, `node_modules` or `.venv`. For each line, match
`\[[^\]]*\]\(([^)\s]+)\)`. Skip a target that starts with `http://`, `https://`, `mailto:`, `tel:` or
`#`. Strip a trailing `#anchor` and `?query`, then resolve the target relative to the source file's
directory. A target that does not exist is an error, printed as `<source>:<line>: <target>`.
Exit 1 if there is any error; otherwise print `0 broken relative links`.

Skip nothing else: links to files outside `docs/` are valid when the target exists, which is what
keeps `../../AGENTS.md` and the chart READMEs honest.

- [ ] **Step 2: Run the checker and fix the one real break**

Run: `python3 hack/check_docs_links.py`
Expected: one error at `cluster/helm/media-stack/calibre-web-automated/README.md:194`, a
`../../../docs/runbooks/internal-ca.md` path that is one `..` short.

Fix that link to `../../../../docs/runbooks/internal-ca.md`. Run the checker again.
Expected: `0 broken relative links`.

- [ ] **Step 3: Write `mkdocs.yml`**

Root of the repository. Keys: `site_name: AshuLab homelab`, `docs_dir: docs`, `use_directory_urls:
true`. Theme `material`, `language: en`, features `navigation.tabs`, `navigation.sections`,
`navigation.indexes`, `navigation.top`, `toc.follow`, `content.code.copy`, `search.suggest`,
`search.highlight`; palette `scheme: slate`. `markdown_extensions`: `admonition`, `attr_list`,
`def_list`, `tables`, `toc` with `permalink: true`, `pymdownx.superfences` with the `mermaid` fence,
`pymdownx.details`, `pymdownx.tabbed` with `alternate_style: true`. `plugins: [search]`. Validation:
`nav.omitted_files: ignore`.

First nav, only pages that exist after this task:

```yaml
nav:
  - Start here: README.md
  - How it works:
      - The shape and why: how-it-works/the-shape-and-why.md
```

- [ ] **Step 4: Write `docs/how-it-works/the-shape-and-why.md`**

Summary block first, `Kind: living page`, `Verified` today. Sections, in this order:

```text
## What failed, in one paragraph        the 2026 Longhorn loss and the three gaps it exposed
## The four rules that follow           no replicated storage; every pod pinned; GitOps for apps;
                                        free software and official images only
## What is accepted, not fixed          no failover; no backups yet; single-node alerting; heat
## What was rejected                    replicated storage, a wiki as source of truth, SOPS now,
                                        a shared library chart
## Where the full argument lives        links to the stabilisation design §6-§8, §13
```

Sources: the stabilisation design §1–§7 and §13, `docs/audit/observability-storage.md`. Link, do not
copy, its tables. Gloss "replicated storage" as "the same volume on more than one machine".

- [ ] **Step 5: Build and read the warnings**

Run: `uv run --quiet --with 'mkdocs-material==9.8.1' mkdocs build -d /tmp/docs-build`
Expected: exit 0. Warnings naming a link that resolves on disk outside `docs/` are accepted; a warning
naming a missing page is a failure. Record the accepted warnings in the commit body.

- [ ] **Step 6: Commit**

```bash
git add hack/check_docs_links.py mkdocs.yml cluster/helm/media-stack/calibre-web-automated/README.md docs/how-it-works/the-shape-and-why.md \
  docs/superpowers/specs/2026-11-17-docs-for-the-operator-design.md \
  docs/superpowers/plans/2026-11-17-docs-for-the-operator.md
git commit -m "📝 docs: site frame, link checker and the shape of the homelab"
```

---

### Task 2: Nodes and GitOps

**Files:**
- Create: `docs/how-it-works/nodes.md`
- Create: `docs/how-it-works/gitops.md`
- Modify: `mkdocs.yml` (nav: add both pages under How it works)

**Interfaces:** none.

- [ ] **Step 1: Write `nodes.md`**

Sections:

```text
## The machines                      four cluster nodes + two edge Pis, a table: name, role, CPU/arch,
                                     memory, what runs there
## Placement is a rule, not a hint   nodeSelector by hostname; a local-path claim lands on that node
## What "no failover" costs          node disk loss = app loss; the backup that would cover it
## Heat                               streaming at its ceiling, nuc0 throttling: links to the two
                                     decision records, no numbers restated
## Where to look next                 links to storage.md, applications.md, observability.md
```

Sources: design §8.1, the two decision records, the live cluster (`kubectl get nodes`, node labels and
capacity). Do not repeat the status page's pod counts. Gloss "edge Pis" once: the two Raspberry Pis
outside the cluster, running Pi-hole and WireGuard.

- [ ] **Step 2: Write `gitops.md`**

Sections:

```text
## A commit's journey                develop -> root Application -> each Application -> chart
## The repository in one screen      cluster/argocd, cluster/helm, cluster/infrastructure, kubectl,
                                     ansible, hack
## Four ways a chart is consumed      patterns A-D from AGENTS.md §3, one line each, with examples
## Why auto-sync is not in git        the UI switch, the root-app rewrite, what to do when a sync
                                     stops
## What to edit for a change          decision table: image, config, route, secret, new app
## Validate before merging            helm lint/template, kubeconform, hack/check-exposure.sh
```

Source: `AGENTS.md` §2–§3, design §8.3–§8.4. Keep the agent protocol out of this page; link it.

- [ ] **Step 3: Add both to the nav** under `How it works`, in this order: The shape and why, Nodes,
GitOps.

- [ ] **Step 4: Check and commit**

Run: `python3 hack/check_docs_links.py` → `0 broken relative links`.
Run the build from Task 1, Step 5 → exit 0.

```bash
git add docs/how-it-works/nodes.md docs/how-it-works/gitops.md mkdocs.yml
git commit -m "📝 docs: nodes, placement and the GitOps path"
```

---

### Task 3: Storage and network

**Files:**
- Create: `docs/how-it-works/storage.md`
- Create: `docs/how-it-works/network.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write `storage.md`**

Sections:

```text
## Two storage worlds                 config on local-path, media on NFS classes; why
## Claims and names                   <app>-config, 128Mi default, the class table
## The binding trap                   WaitForFirstConsumer + a moved pod; the recovery
## Longhorn's corpse                   what is stuck, why it cannot clear, why it is inert
## Backups                             the honest state: not working; the design that owns it
```

Sources: design §8.2, `charts/README.md` UID and binding sections, `audit/observability-storage.md`.
Link the UID trap rather than restating its procedure.

- [ ] **Step 2: Write `network.md`**

Sections:

```text
## How a request arrives              router -> the Traefik DaemonSet -> router rule -> service
## Names                               *.kub.local, the three public names, DNS and the LAN gate
## Three layers that look alike        lan-only, authentik-forwardauth, OIDC: what each proves
## TLS on kub.local                    the default local-cert, internal-tls option, redirect routers,
                                      the root every client installs
## API exemptions                      why /api can carry lan-only alone, and what that costs
## Public exposure                     the allowlist and hack/check-exposure.sh
```

Sources: design §8, `charts/README.md` requirements 12 and 19, `runbooks/internal-ca.md`, the
authentik runbook's trap list, the live IngressRoutes. Link the per-app matrix in
`reference/sso-applications.md` once stage 3 writes it; until then link the runbook.

- [ ] **Step 3: Add both to the nav** after GitOps.

- [ ] **Step 4: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/how-it-works/storage.md docs/how-it-works/network.md mkdocs.yml
git commit -m "📝 docs: storage, ingress and the three auth layers"
```

---

### Task 4: Identity and observability

**Files:**
- Create: `docs/how-it-works/identity.md`
- Create: `docs/how-it-works/observability.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write `identity.md`**

Sections:

```text
## authentik's job                    one identity provider; the two sign-in sources
## Three levels                       Admins, users, guests; capability groups; what each reaches
## Choosing how an app is protected   the order: native OIDC, proxy auth, forward-auth, lan-only
## Break-glass                        the local account or API key each app keeps, and where it lives
## MFA                                optional TOTP/WebAuthn, applied to source logins too
## Where the per-app detail is        link to runbooks/authentik-apps.md now and
                                      reference/sso-applications.md after stage 3
```

Sources: `runbooks/authentik-apps.md`, the SSO access-model design. Do not repeat the per-app table;
summarise the rule and link.

- [ ] **Step 2: Write `observability.md`**

Sections:

```text
## What watches what                  Prometheus, Loki, Fluent Bit, Grafana: placement and retention
## How metrics arrive                 annotation scraping; no ServiceMonitor
## How logs arrive                    Fluent Bit on every node, one JSON envelope per line
## What you look at                   the seven dashboards, one line each
## Where alerts go                    Grafana evaluates, Discord delivers, the board receives
## What it misses                     the known gaps, one line each, linking the reference
```

Source: `observability-conventions.md` and its split pages after stage 3. In stage 1, link the
existing file for detail; Task 13 rewires the links.

- [ ] **Step 3: Add both to the nav** after Network.

- [ ] **Step 4: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/how-it-works/identity.md docs/how-it-works/observability.md mkdocs.yml
git commit -m "📝 docs: identity and the monitoring stack"
```

---

### Task 5: Media and the application catalogue

**Files:**
- Create: `docs/how-it-works/media.md`
- Create: `docs/how-it-works/applications.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write `media.md`**

Sections:

```text
## The pipeline, one diagram          indexer -> *arr -> download client -> library -> Plex
## Who does what                      one line per app: Prowlarr, Sonarr/Radarr/Lidarr, Bazarr,
                                      qBittorrent, cross-seed, autobrr, unpackerr, maintainerr,
                                      recyclarr, Seerr, Tautulli, Plex
## The books pair                     Calibre-Web Automated writes, Kavita reads; the single-writer rule
## Where the files live               the NFS exports and which app mounts which
## What protects each one             OIDC, gate, or LAN only; link the matrix, do not rebuild it
```

Gloss "*arr apps", "indexer", "usenet/torrent" and "cross-seed" once, in plain words. Sources: the
charts' `values.yaml` and READMEs, the books design, the authentik runbook. Do not copy chart README
text.

- [ ] **Step 2: Write `applications.md`**

One table, grouped by namespace. Columns: Application, namespace, node, route, protection, chart
pattern (A/B/C/D), note. Every Application in `cluster/argocd/projects/` gets a row; group the four
NFS provisioners and the monitoring DaemonSets below the main table. Add a header line: "Snapshot of
the repo and the cluster on <date>; the live answer is `status.md`."

Source: `cluster/argocd/projects/**/*.yaml`, each chart's `values.yaml` (`nodeSelector`, `image.tag`,
`ingressRoute`), the live cluster for what is actually running. Do not put replica counts here; they
belong to `status.md`.

- [ ] **Step 3: Add both to the nav** after Observability.

- [ ] **Step 4: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/how-it-works/media.md docs/how-it-works/applications.md mkdocs.yml
git commit -m "📝 docs: the media pipeline and the application catalogue"
```

---

### Task 6: The one-page model, the home page and the stage handover

**Files:**
- Create: `docs/how-it-works/README.md`
- Modify: `docs/README.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write `how-it-works/README.md`**

Sections:

```text
## Four nodes, two edge Pis           one sentence each, link nodes.md
## The path a request takes           Mermaid: browser -> router -> Traefik -> app -> postgres/NFS
## The path a commit takes            Mermaid: edit -> branch -> PR -> develop -> ArgoCD -> pod
## Five things to remember            pinning, local-path, GitOps, lan-only, native SSO first
## Where to go next                   the nine topic pages, one line each
```

The diagrams are Mermaid fences. Keep the page under 800 words.

- [ ] **Step 2: Rewrite `docs/README.md` as the home page**

Sections:

```text
## What this is                       three sentences, the docs site URL, the source of truth
## Five things worth remembering      the same five as the model page, one line each
## Choose a path                      Understand (how-it-works/README.md), Fix something
                                      (status.md for now; running-it/ arrives in stage 2),
                                      Read a plan (agent-work.md arrives in stage 4)
## The map                            a short table: nodes, storage, identity, monitoring, media
## Where each kind of knowledge lives docs, the board, docs/superpowers, OpenMemory
## Freshness                          living pages carry a date; snapshots say so
```

Do not link `running-it/` or `agent-work.md` in this stage; the paths are added by Task 11 and
Task 22. Link `status.md`, the how-it-works pages and the board.

- [ ] **Step 3: Complete the stage 1 nav**

```yaml
nav:
  - Start here: README.md
  - How it works:
      - how-it-works/README.md
      - The shape and why: how-it-works/the-shape-and-why.md
      - Nodes: how-it-works/nodes.md
      - GitOps: how-it-works/gitops.md
      - Storage: how-it-works/storage.md
      - Network: how-it-works/network.md
      - Identity: how-it-works/identity.md
      - Observability: how-it-works/observability.md
      - Media: how-it-works/media.md
      - Applications: how-it-works/applications.md
```

The bare `how-it-works/README.md` first entry is what makes the section index work with
`navigation.indexes`.

- [ ] **Step 4: Run the full stage 1 check**

- `python3 hack/check_docs_links.py` → `0 broken relative links`.
- `uv run --quiet --with 'mkdocs-material==9.8.1' mkdocs build -d /tmp/docs-build` → exit 0.
- Open `/tmp/docs-build/index.html` and follow each link in the home page's three paths; each target
  exists in this stage.
- `git grep -n 'running-it\|agent-work' docs/README.md` → no match.

- [ ] **Step 5: Commit, hand over and merge**

```bash
git add docs/README.md docs/how-it-works/README.md mkdocs.yml
git commit -m "📝 docs: the one-page model and a start-here home page"
git fetch origin && git rebase origin/develop
git push -u origin feature/docs-stage-1
```

Open a PR against `develop` with the design and plan linked. Update the stage 1 task card: branch, PR,
the two checks above, then move it to `Validation`. After the operator merges, wait for the docs-site
CronJob (a minute or two) and confirm the seven-tab site opens at
`https://docs.kub.local/homelab/develop/` with only Start here and How it works populated.

---

## Stage 2 — Running it and a current status page (PR 2)

After stage 1 merges: `git fetch origin && git switch -c feature/docs-stage-2 origin/develop`.

### Task 7: The status page and the five-minute check

**Files:**
- Modify: `docs/status.md`
- Create: `docs/running-it/every-day.md`
- Modify: `mkdocs.yml` (add the Running it tab)

- [ ] **Step 1: Refresh `status.md` from the live cluster**

Title it **What is broken right now**. Summary block: `Kind: snapshot`, the date and time of the read,
and the commands that produced it. Keep the existing structure (summary, broken, repaired, not yet
rebuilt, borrowed volumes, cannot compare, drift) and update every table from
`kubectl get applications -n argocd`, `kubectl get pods -A`, `kubectl get deploy,statefulset,daemonset
-A`, `kubectl get pvc -A`. Add `## How to check right now` at the top with those commands and the
dashboard names. Do not keep a claim that is no longer true; if a section is empty, say so and remove
the table.

- [ ] **Step 2: Write `running-it/every-day.md`**

Sections:

```text
## Five minutes                      the commands and the three dashboard links; what order
## What normal looks like            the counts and colours that mean nothing is on fire
## The accepted faults               heat on streaming and nuc0, the dead nas SSD, qBittorrent's UI
## When something is red             the path to the alert, the dashboard, the board card
## What not to do                    no kubectl edit/apply on a GitOps object; no restarting a
                                     borrowed-volume pod
```

Link `status.md` for the counts instead of repeating them. Link `observability-conventions.md` for the
dashboard detail.

- [ ] **Step 3: Add the Running it nav**

```yaml
  - Running it:
      - What is broken right now: status.md
      - Every day: running-it/every-day.md
```

- [ ] **Step 4: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0. The refreshed numbers match the commands run
in the same session.

```bash
git add docs/status.md docs/running-it/every-day.md mkdocs.yml
git commit -m "📝 docs: a dated status page and the daily check"
```

---

### Task 8: Ship a change, and undo it

**Files:**
- Create: `docs/running-it/ship-a-change.md`
- Create: `docs/running-it/roll-back.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write `ship-a-change.md`**

Sections:

```text
## Before you edit                   the four patterns, where the value lives
## The local checks                   helm lint, helm template, kubeconform, check-exposure, the docs
                                      link checker when docs change
## Branch, commit, PR                the repo's branch model and emoji prefixes
## Merge and sync                    what happens automatically, what needs a UI click
## Verify                            the commands and the dashboard row that prove it
## If the sync stops                  the syncPolicy.automated trap and its fix
```

- [ ] **Step 2: Write `roll-back.md`**

Sections:

```text
## Three different undo buttons       git revert, ArgoCD history, pod restart
## Which one to use                   a decision table by situation
## What a rollback does to a claim    Recreate, the config volume, the UID trap
## The one that destroys data         deleting a claim or a namespace; what to do instead
```

- [ ] **Step 3: Add both to the nav** under Running it.

- [ ] **Step 4: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/running-it/ship-a-change.md docs/running-it/roll-back.md mkdocs.yml
git commit -m "📝 docs: shipping a change and rolling one back"
```

---

### Task 9: Add an application, and fix one that will not start

**Files:**
- Create: `docs/running-it/add-an-application.md`
- Create: `docs/running-it/fix-a-pending-pod.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write `add-an-application.md`**

A checklist, in order: chart under `cluster/helm/<area>/<app>`, pinned image tag, `nodeSelector`,
`Recreate` and a config claim, inline env, route with `lan-only` and the right gate, secret reference,
`Application` file (CRLF, no `automated`), the docs row and page, then verification. Each item names
the existing chart to copy from (autobrr for a hand-built app, a wrapper for an upstream chart).
Finish with `## Verify` and `## Common failures`.

- [ ] **Step 2: Write `fix-a-pending-pod.md`**

Sections, one per cause, each with the command that tells it apart and the fix:

```text
## First, read the event              kubectl describe pod | tail; the event names the cause
## No claim to bind                   PVC missing, class missing, provisioner down
## The claim bound to the wrong node  the lidarr case; delete the claim, then the pod
## A missing Secret                   CreateContainerConfigError; where the inventory is
## The image will not pull            tag, registry, credentials
## Not enough room                    request vs node capacity; the placement rule
## It is starting, not Pending         CrashLoopBackOff is a different page
```

- [ ] **Step 3: Add both to the nav.**

- [ ] **Step 4: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/running-it/add-an-application.md docs/running-it/fix-a-pending-pod.md mkdocs.yml
git commit -m "📝 docs: adding an app and unblocking a pending pod"
```

---

### Task 10: Secrets and the board

**Files:**
- Create: `docs/running-it/rotate-a-secret.md`
- Create: `docs/running-it/use-the-board.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write `rotate-a-secret.md`**

Sections: what a rotation touches (the instance Secret, the app Secret, the role password, the pod);
the `ALTER ROLE` command; the two-namespace copy rule; the restart; the verification; the failure mode
where only one copy changes. Link `docs/secrets.md` for the inventory and
`runbooks/postgres-add-database.md` for the role creation.

- [ ] **Step 2: Write `use-the-board.md`**

Sections: the six columns and who may move what; the three card kinds; filing a card; validating one;
the fixed description block and the new `site:` line; the two saved filters; what the board is not (no
subtask cards, no second todo list). Link `agent-protocol.md` for the long form. Keep it to one
screen.

- [ ] **Step 3: Add both to the nav.**

- [ ] **Step 4: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/running-it/rotate-a-secret.md docs/running-it/use-the-board.md mkdocs.yml
git commit -m "📝 docs: rotating a secret and using the board"
```

---

### Task 11: The docs site and disaster recovery

**Files:**
- Create: `docs/running-it/rebuild-the-docs-site.md`
- Create: `docs/running-it/disaster-recovery.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write `rebuild-the-docs-site.md`**

Sections: what the builder does each minute; the manual Job; where the state and the index live; why a
page is missing (branch not built, build failed, page not in nav, token cannot see the repo); how to
read a failure from the index and the Job logs; that the site is gated. Link
`runbooks/docs-site-first-run.md` for the first-run record and `cluster/helm/docs-site/README.md` for
the chart.

- [ ] **Step 2: Write `disaster-recovery.md`**

Sections: what would be lost today (config volumes, databases, Grafana, the board, secrets); what
exists (nothing offsite); what does not exist yet (the restic backup, the Ansible inventory, the
restore drill); which designs and workstreams own each; the one manual thing that can be done today (a
`pg_dump` and a copy of the claims) with its limits. No invented procedure.

- [ ] **Step 3: Write `running-it/README.md`**

An "I want to…" table: understand a symptom, change an app, add one, roll back, rotate a secret, give
someone access, use the board, rebuild the docs, recover from a loss. Each row links a page or an
existing runbook. This page is written last in the stage so every link exists.

- [ ] **Step 4: Update the home page**

Add the second path: "Fix something" → `running-it/README.md` and `status.md`. Leave "Read a plan"
pointing at the board until stage 4.

- [ ] **Step 5: Complete the Running it nav**

Replace the two-entry section from Task 7 with this one:

```yaml
  - Running it:
      - running-it/README.md
      - What is broken right now: status.md
      - Every day: running-it/every-day.md
      - Ship a change: running-it/ship-a-change.md
      - Roll back: running-it/roll-back.md
      - Add an application: running-it/add-an-application.md
      - Fix a pending pod: running-it/fix-a-pending-pod.md
      - Rotate a secret: running-it/rotate-a-secret.md
      - Use the board: running-it/use-the-board.md
      - Rebuild the docs site: running-it/rebuild-the-docs-site.md
      - Disaster recovery: running-it/disaster-recovery.md
```

- [ ] **Step 6: Full stage 2 check and handover**

`python3 hack/check_docs_links.py` → 0; build → exit 0; follow every home-page path.
Commit, rebase, push, open the PR against `develop`, update the stage 2 card to `Validation`, and
after the merge confirm the Running it tab on the `develop` site.

```bash
git add docs/running-it docs/README.md mkdocs.yml
git commit -m "📝 docs: running it end to end, and an honest recovery page"
```

---

## Stage 3 — Reference splits, secrets and runbooks (PR 3)

After stage 2 merges: `git fetch origin && git switch -c feature/docs-stage-3 origin/develop`.

### Task 12: Split the chart page

**Files:**
- Create: `docs/reference/chart-notes.md`
- Create: `docs/reference/chart-sizing.md`
- Modify: `docs/charts/README.md`
- Modify: `docs/observability-conventions.md` (the sizing anchor)
- Modify: `mkdocs.yml`

- [ ] **Step 1: Move the sizing section**

Cut "Resource sizing: the reviewed set" (the intro, the two tables and the re-measure rule) from
`docs/charts/README.md` into `reference/chart-sizing.md`, verbatim. Add the summary block
(`Kind: living page`, `Verified` today). Keep the date of the measurement.

- [ ] **Step 2: Move the per-app notes**

Cut "What good looks like" and "Charts still to align" into `reference/chart-notes.md`, verbatim. Turn
each app name that starts a paragraph into an `###` heading (`autobrr`, `cross-seed`, `flaresolverr`,
`prowlarr`, `sonarr`, `sonarr-anime`, `recyclarr`, `tautulli`, `radarr`, `radarr-anime`, `lidarr`,
`bazarr`, `bazarr-anime`, `unpackerr`, `maintainerr`, `calibre-web-automated`, `kavita`). Add the
summary block.

- [ ] **Step 3: Make `charts/README.md` an overview**

It keeps "Why a reference chart and not a shared library", "The contract", "How configuration reaches
the app", the UID trap, "Checking a chart against this list". Add the summary block and, after the
contract, two links: sizing and per-app notes. It no longer contains either.

- [ ] **Step 4: Fix the cross-links**

- `observability-conventions.md` line linking `charts/README.md#resource-sizing-the-reviewed-set` now
  links `reference/chart-sizing.md`.
- `grep -rn 'charts/README.md#' --include='*.md' .` must return nothing.
- `grep -rn 'docs/charts/README.md' cluster/helm` still resolves; the file still exists.

Run: `python3 hack/check_docs_links.py` → 0.

- [ ] **Step 5: Add the Reference nav shell**

```yaml
  - Reference:
      - Chart notes: reference/chart-notes.md
      - Chart sizing: reference/chart-sizing.md
```

No section index yet; `reference/README.md` is written in Task 14 and added to the nav there.

- [ ] **Step 6: Build and commit**

Build → exit 0. Commit:

```bash
git add docs/reference/chart-notes.md docs/reference/chart-sizing.md docs/charts/README.md docs/observability-conventions.md mkdocs.yml
git commit -m "📝 docs: split the chart contract from the per-app notes and sizing"
```

---

### Task 13: Split the observability page

**Files:**
- Create: `docs/reference/metrics.md`
- Create: `docs/reference/alerts.md`
- Create: `docs/reference/dashboards.md`
- Modify: `docs/observability-conventions.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Move metrics detail**

Cut "Exporter quirks", "Storage and retention" and "Current health facts" into
`reference/metrics.md`, verbatim, with the summary block and the measurement dates. Keep "How metrics
get in" in the overview.

- [ ] **Step 2: Move dashboards**

Cut "Dashboards" into `reference/dashboards.md`; add the dashboard-changing API calls from "Changing
things". Summary block.

- [ ] **Step 3: Move alerts**

Cut "Alerting", "Known gaps" and the rule/contact-point half of "Changing things" into
`reference/alerts.md`. Summary block. Keep the silence table and the pattern rules intact.

- [ ] **Step 4: Leave an overview behind**

`observability-conventions.md` keeps "What runs where", "How to query the stack" and "How metrics get
in", adds the summary block and a "Where the detail lives" table linking the three pages. No fact
appears in both the overview and a reference page.

- [ ] **Step 5: Update links and nav**

`grep -rn 'observability-conventions.md#' --include='*.md' .` → nothing. Add the three pages to the
Reference nav. Check and build:

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/reference/metrics.md docs/reference/alerts.md docs/reference/dashboards.md docs/observability-conventions.md mkdocs.yml
git commit -m "📝 docs: split observability into metrics, dashboards and alerts"
```

---

### Task 14: SSO matrix, routes and the reference index

**Files:**
- Create: `docs/reference/sso-applications.md`
- Create: `docs/reference/routes.md`
- Create: `docs/reference/README.md`
- Modify: `docs/runbooks/authentik-apps.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Move the per-app SSO detail**

From `runbooks/authentik-apps.md`, move to `reference/sso-applications.md`: the top matrix ("State" and
the three traps), "Media tools behind the gate" (including every `###` inside it), "Still open". Keep
in the runbook: the four client secrets, the Grafana/pgAdmin/Kavita/Argo CD procedures, "Granting
access", "FileBrowser", "MFA", "Verification". Add to the runbook's top a link to the matrix and keep
the runbook's opening rule (native SSO wins).

- [ ] **Step 2: Rebuild the matrix as a table**

Columns: Application, mechanism (OIDC / proxy / forward-auth / LAN only), state, route. One row per
app, including the ones with no gate and the reason. Keep the trap list and the "adding another tool"
procedure in the same page, under `## Traps` and `## Adding a tool`.

- [ ] **Step 3: Write `routes.md`**

One table from the live IngressRoutes and chart values: hostname, app, namespace, entrypoint, gate or
none, TLS, public yes/no. Group by `## Public`, `## Internal over TLS`, `## Internal without TLS`.
State the date and the command to regenerate the list.

- [ ] **Step 4: Write `reference/README.md`**

A table: page, what it answers, why it is long-form. Links to chart notes, chart sizing, metrics,
alerts, dashboards, SSO, routes, plus `../secrets.md` and `../qbittorrent-webui-stalls.md`.

- [ ] **Step 5: Add to the nav**

`Overview` (now exists), `Routes`, `SSO applications`, and the existing chart pages. Leave `secrets`
and the qBittorrent fault for Task 15's nav pass.

- [ ] **Step 6: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0. Every link from the trimmed runbook resolves.

```bash
git add docs/reference/sso-applications.md docs/reference/routes.md docs/reference/README.md docs/runbooks/authentik-apps.md mkdocs.yml
git commit -m "📝 docs: the SSO matrix and the route table, out of the runbook"
```

---

### Task 15: Complete the secret inventory and point `AGENTS.md` at it

**Files:**
- Modify: `docs/secrets.md`
- Modify: `AGENTS.md` §5
- Modify: `mkdocs.yml`

- [ ] **Step 1: Absorb the application inventory**

Add a section **Application secrets expected by the charts** to `docs/secrets.md`. Copy the rows of
`AGENTS.md` §5 into it unchanged (Secret, namespace, keys, consumer), and mark the section: "Compiled
from the charts on 2026-11-17; not verified against the cluster, because the read-only account cannot
read Secrets. The chart that mounts or references a Secret is the authority on its keys." Reconcile
the known `discourse` correction. Remove nothing from the PostgreSQL sections.

- [ ] **Step 2: Replace the table in `AGENTS.md`**

Keep the §5 heading, the rule that secrets are never committed, and the symptom sentence. Replace the
table and its intro with three lines: the inventory is `docs/secrets.md`; the charts are the authority
on keys; a missing Secret shows as `CreateContainerConfigError`. Change nothing else in the file.

- [ ] **Step 3: Label the open fault and add both pages to the nav**

Give `docs/qbittorrent-webui-stalls.md` the summary block with `Kind: living page` (it is an open
fault with no fix applied) and a link to `how-it-works/media.md`. Then add to the Reference nav:
`Secrets: secrets.md` and `qBittorrent Web UI stalls: qbittorrent-webui-stalls.md`.

- [ ] **Step 4: Check the brief and the docs**

`git diff --stat -- AGENTS.md` → one file, a small deletion and a few added lines.
`grep -c '^| media-stack |' AGENTS.md` → 0 (the table is gone).
`python3 hack/check_docs_links.py` → 0; build → exit 0.

- [ ] **Step 5: Commit**

```bash
git add docs/secrets.md AGENTS.md mkdocs.yml
git commit -m "📝 docs: one secret inventory, and a pointer from the agent brief"
```

---

### Task 16: The runbook index and its statuses

**Files:**
- Modify: `docs/runbooks/README.md`
- Modify: `docs/runbooks/books-cutover.md`
- Modify: `docs/runbooks/docs-site-first-run.md`
- Modify: `docs/runbooks/vikunja-database.md`
- Modify: `docs/runbooks/vikunja-first-start.md`
- Modify: `docs/runbooks/vikunja-board-seed.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Rewrite the index**

Two tables. **Living procedures**: every file that exists and is still used, with its status (`proved
<date>`, `pending`) and one line on when to use it. **Not written yet**: the six dead rows
(`restore-etcd`, `traefik-cutover`, `k3s-upgrade`, `restore`, `add-a-node`, `rotate-a-secret`) as plain
text, no links, each with the workstream that owns it. Add the conventions paragraph.

- [ ] **Step 2: Mark the completed records**

Add the summary block with `Kind: completed record <date>` to `books-cutover.md`,
`docs-site-first-run.md`, `vikunja-database.md`, `vikunja-first-start.md` and
`vikunja-board-seed.md`. The body does not change.

- [ ] **Step 3: Add the Runbooks nav**

All existing files, README first, ordered: authentik, internal-ca, postgres add/restore, alert-relay,
vikunja database/first-start/seed, books cutover, docs site. (The completed records stay listed;
their status says what they are.)

- [ ] **Step 4: Full stage 3 check and handover**

`python3 hack/check_docs_links.py` → 0; build → exit 0. Every entry in the runbook index is a file that
exists (`for f in ...; do test -f`).
Commit, rebase, push, open the PR, move the stage 3 card to `Validation`, and after the merge confirm
the Reference and Runbooks tabs on the `develop` site.

```bash
git add docs/runbooks mkdocs.yml
git commit -m "📝 docs: an honest runbook index and completed-record labels"
```

---

## Stage 4 — Agent work, history and ticket links (PR 4)

After stage 3 merges: `git fetch origin && git switch -c feature/docs-stage-4 origin/develop`.

### Task 17: The agent-work index, first half

**Files:**
- Create: `docs/agent-work.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write the introduction**

Sections: what `docs/superpowers/` is; why the paths are fixed (the skills find files by path); how a
spec and a plan pair up; how to read a plan (the instruction block at the top, the checkboxes are
planned-and-executed, the board is the state); that the files are written for agents and the prose is
the reasoning.

- [ ] **Step 2: Index the stabilisation programme**

A table: Document (link), what it settled, date. Every file in `docs/superpowers/plans/` and
`docs/superpowers/specs/` that belongs to the programme: the programme index, W0, W5, the
stabilisation design, and the three audits (link them under History instead, from Task 19). One line
per document, taken from its own H1 and first paragraphs. Do not copy text.

- [ ] **Step 3: Index the media charts**

One table for the chart refactors: sonarr, radarr, lidarr, bazarr, seerr, tautulli, unpackerr,
recyclarr, maintainerr, cross-seed, plus the media-dashboard and books entries. Each row links its plan
and its design where one exists.

- [ ] **Step 4: Add the Agent work nav**

```yaml
  - Agent work:
      - How to read a plan: agent-work.md
      - The board protocol: agent-protocol.md
```

- [ ] **Step 5: Check and commit**

Every plan/spec link resolves (`python3 hack/check_docs_links.py` → 0); build → exit 0.

```bash
git add docs/agent-work.md mkdocs.yml
git commit -m "📝 docs: how to read a plan, and the first half of the index"
```

---

### Task 18: The agent-work index, second half

**Files:**
- Modify: `docs/agent-work.md`

- [ ] **Step 1: Index identity, observability, the board and the one-offs**

Four more tables, same shape: identity (authentik phases, media tools, access model, filebrowser),
observability (stack design, alerting, dashboards, alert link base URL, post-repair, vulnerability),
board and docs (agent tracking/docs site, Vikunja board, board workflow, alerts to board, docs site
plan), and the smaller one-offs (logo, media dashboard, filebrowser namespace).

- [ ] **Step 2: Check completeness**

Run: `ls docs/superpowers/plans/*.md docs/superpowers/specs/*.md | wc -l` and count the links in
`agent-work.md`. Every file appears once. Add a final line naming the count and the date.

- [ ] **Step 3: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/agent-work.md
git commit -m "📝 docs: the rest of the agent-work index"
```

---

### Task 19: History — audits and decisions

**Files:**
- Create: `docs/audit/README.md`
- Modify: `docs/audit/ansible-hosts.md`
- Modify: `docs/audit/argocd-helm.md`
- Modify: `docs/audit/observability-storage.md`
- Modify: `docs/decisions/README.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write the audit index and banners**

`audit/README.md`: what the audits are (a snapshot of 2026-11-10, not current state), the three files
and their scope, and a link to `status.md`. Add a three-line banner to each audit under its H1: the
date, "historical snapshot, not maintained", and the link to `status.md`. Change no finding.

- [ ] **Step 2: Rewrite the decisions index**

`decisions/README.md`: what a decision record is, the four headings it uses, and a table of the two
records with their one-line decision and date. Keep the template section.

- [ ] **Step 3: Add the History nav**

```yaml
  - History:
      - Decisions: decisions/README.md
      - nuc0 throttling: decisions/2026-11-13-nuc0-throttling-accepted.md
      - streaming overheating: decisions/2026-11-13-streaming-overheating-deferred.md
      - Audits: audit/README.md
      - Ansible and hosts: audit/ansible-hosts.md
      - ArgoCD and Helm: audit/argocd-helm.md
      - Observability and storage: audit/observability-storage.md
```

- [ ] **Step 4: Check and commit**

`python3 hack/check_docs_links.py` → 0; build → exit 0.

```bash
git add docs/audit docs/decisions mkdocs.yml
git commit -m "📝 docs: decisions and audits as history, with a way back to today"
```

---

### Task 20: The `site:` line in the protocol and its generator

**Files:**
- Modify: `docs/agent-protocol.md`
- Modify: `hack/vikunja_migrate.py`
- Create: `tests/test_vikunja_migrate.py`
- Modify: `docs/running-it/use-the-board.md`

**Interfaces:**
- Produces: `site_url(docs: str) -> str | None` in `hack/vikunja_migrate.py`, returning
  `https://docs.kub.local/homelab/develop/<path>/` for a `docs/....md` path and `None` otherwise.
- Produces: `render_description(docs, branch, pr, body, commit="none")` emitting `site:` directly
  after `docs:` when `site_url` is not `None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_vikunja_migrate.py`, pytest:

```python
from hack.vikunja_migrate import render_description, site_url

def test_site_url_for_a_plan():
    assert site_url("docs/superpowers/plans/x.md") == \
        "https://docs.kub.local/homelab/develop/superpowers/plans/x/"

def test_site_url_for_a_page():
    assert site_url("docs/agent-protocol.md") == \
        "https://docs.kub.local/homelab/develop/agent-protocol/"

def test_site_url_none_for_a_non_docs_path():
    assert site_url("cluster/helm/sonarr") is None

def test_render_description_puts_site_after_docs():
    text = render_description("docs/status.md", "none", "none", "")
    assert text.splitlines()[:3] == [
        "repo: vyrtualsynthese/homelab",
        "docs: https://github.com/vyrtualsynthese/homelab/blob/develop/docs/status.md",
        "site: [Rendered page](https://docs.kub.local/homelab/develop/status/)",
    ]

def test_render_description_without_docs_has_no_site_line():
    text = render_description("none", "none", "none", "")
    assert "site:" not in text
```

- [ ] **Step 2: Run and watch them fail**

`uv run --quiet --with pytest python -m pytest -q tests/test_vikunja_migrate.py`
Expected: import error for `site_url`.

- [ ] **Step 3: Implement `site_url` and the renderer change**

In `hack/vikunja_migrate.py`: `site_url` returns `None` unless `docs` starts with `docs/` and ends
with `.md`; strip the prefix and suffix, append `/`, and prefix the site base. `render_description`
inserts `site: [Rendered page](<url>)` after `docs:` only when `site_url` is not `None` — a labelled
Markdown link, never a bare URL, because the protocol treats a bare URL as a card defect.

- [ ] **Step 4: Run the tests**

Expected: 5 passed.

- [ ] **Step 5: Verify the protocol and the board how-to**

The protocol and the board page already carry the `site:` line and the labelled-link rule: stage 2
added them with `docs/running-it/use-the-board.md`. Re-read both and change them only if the renderer's
output shape (above) differs from what they say. Add the summary block under the protocol's H1 if it
is still missing.

- [ ] **Step 6: Commit**

```bash
git add hack/vikunja_migrate.py tests/test_vikunja_migrate.py docs/agent-protocol.md docs/running-it/use-the-board.md
git commit -m "📝 docs: point every ticket at the rendered page"
```

---

### Task 21: The one-off `site:` backfill

**Files:**
- Create: `hack/vikunja_site_backfill.py`
- Create: `tests/test_vikunja_site_backfill.py`

**Interfaces:**
- Consumes: `site_url` from Task 20.
- Produces: `add_site_line(description: str) -> str`, adding `site: [Rendered page](<url>)` after the
  `docs:` line when a site URL applies and no `site:` line exists; otherwise returning the description
  unchanged. A labelled link, matching the renderer.
- Produces: `--dry-run` (default) and `--apply`; open columns only.

- [ ] **Step 1: Write the failing tests**

Cases: a description with `docs:` and no `site:` gains the line after `docs:`; a description that
already has `site:` is unchanged; a description with `docs: none` is unchanged; the body after the
block is preserved byte for byte; running the function twice equals running it once.

- [ ] **Step 2: Run and watch them fail**

`uv run --quiet --with pytest python -m pytest -q tests/test_vikunja_site_backfill.py`

- [ ] **Step 3: Implement the script**

Use `VIKUNJA_API_TOKEN` and the same API base as `vikunja_migrate.py`. Read the board's open columns
(`Backlog`, `Doing`, `Validation`) through the project view buckets endpoint. For each card, parse the
description's first block, apply `add_site_line`, and report `would update <id> <title>` or
`unchanged`. `--apply` sends the new description. Never touch `Idea` or `Done`.

- [ ] **Step 4: Run the tests**

Expected: all passed.

- [ ] **Step 5: Dry run, review, apply**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a   # or export VIKUNJA_API_TOKEN
python3 hack/vikunja_site_backfill.py --dry-run
```

Read the count and a sample. Then `--apply`, and spot-check three cards in the browser: `docs:`
unchanged, `site:` after it, click it and land on the rendered plan.

- [ ] **Step 6: Commit**

```bash
git add hack/vikunja_site_backfill.py tests/test_vikunja_site_backfill.py
git commit -m "📝 docs: backfill the rendered-page link on open tickets"
```

---

### Task 22: Final nav, the home page and acceptance

**Files:**
- Modify: `mkdocs.yml`
- Modify: `docs/README.md`
- Modify: `docs/agent-work.md` (only if a count line moves)

- [ ] **Step 1: Add the last home path**

"Read a plan" now points at `agent-work.md`; the board link stays as the state. Confirm the three
paths exist and every link resolves.

- [ ] **Step 2: Finalize the nav**

The seven tabs in this order: Start here, How it works, Running it, Reference, Runbooks, History,
Agent work. Check that no tab is empty and no page is listed twice.

- [ ] **Step 3: Run the complete verification**

- `python3 hack/check_docs_links.py` → 0.
- `uv run --quiet --with 'mkdocs-material==9.8.1' mkdocs build -d /tmp/docs-build` → exit 0; every
  warning read and accepted explicitly.
- `uv run --quiet --with pytest python -m pytest -q tests/` → all passed.
- `git diff --name-status origin/develop -- docs/superpowers | grep -v '^A'` → empty: nothing but
  additions under the superpowers folder.

- [ ] **Step 4: Hand over the stage**

Commit, rebase, push, open the PR against `develop`, move the stage 4 card to `Validation` with the
five outputs above. After the merge, verify on the `develop` site: seven tabs, the three home paths,
one ticket's `site:` link, and one running-it page followed end to end.

```bash
git add mkdocs.yml docs/README.md
git commit -m "📝 docs: seven tabs, three paths and the plan link"
```

- [ ] **Step 5: Operator acceptance**

The operator confirms the three intents from the design §1: explain the shape from the docs, open a
plan from a ticket, and change or fix one thing using only a page. Only the operator moves the
feature card to `Done`.
