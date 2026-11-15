# Design — agent work tracking, and one documentation site

- **Date:** 2026-11-15
- **Status:** awaiting operator approval.
- **Owner:** operator. Every decision marked **(operator)** was taken in conversation on 2026-11-15;
  every decision marked **(design)** is this document's call and can be overruled.
- **Relates to:** `2026-11-10-homelab-stabilisation-design.md` §8.6 (documentation surface), §8.8
  (where each kind of knowledge lives) and W7 task 7 (Vikunja); `2026-11-13-postgresql-design.md`
  §2 (Vikunja is a planned tenant).
- **Builds on:** nothing. Neither the board nor the docs site exists yet.

## 1. Intent

The operator runs several OpenCode sessions at once, across seven project directories, with
subagents inside each one. Two gaps come out of that:

1. **No view of the work.** Agents write plans, tick checkboxes and edit docs, but the state of a
   task lives in the session that is doing it. To answer "what is waiting on me" the operator has to
   open each session and read. The 2026-11-10 design already decided the answer: Vikunja is the task
   hub. It was never built.
2. **No reading surface.** `docs/` across the projects is 600 KiB of Markdown in the homelab repo
   alone, plus a `docs/` folder in five other repos. Reading it means `nvim` and remembering paths.
   The 2026-11-10 design already decided the answer: Material for MkDocs. It was never built either.

This document fills in both, for the operator alone, and leaves a documented path to the two later
audiences (a public presentation, and a user guide for the people who use the public services).

## 2. What exists today, and what does not

| Thing | State | Evidence |
|---|---|---|
| Vikunja | Not deployed | No chart under `cluster/helm/`, no Application referencing it; the only hits in the repo are the two design documents |
| Task state | Plan files with `- [ ]` checkboxes, one per workstream | `docs/superpowers/plans/`, 30 files |
| Session state | SQLite, not human-readable | `~/.local/share/opencode/opencode.db` |
| MkDocs | Not deployed, not configured | No `mkdocs.yml` anywhere in any project |
| Docs spread | 7 project dirs, 5 of them with a `docs/` folder | `~/projets/*` |
| `main` vs `develop` | `main` carries **0 of the 70** doc files; `develop` carries all 70 | `git ls-tree -r --name-only origin/main -- docs` |
| Authentik forward-auth | Middleware written, applied by hand | `cluster/infrastructure/traefik/middleware/authentik-forwardauth.yaml`; already used by `prowlarr`, `tautulli`, `bazarr-anime` |
| Vikunja packaging | Official Helm chart exists | `github.com/go-vikunja/helm-chart`, per the project's own k8s page |
| Vikunja agent access | Built in from 2.7.0 | `POST /api/v2/mcp`, Streamable HTTP, bearer token |

Two consequences worth stating before the design: the `main` half of the docs site renders nothing
until a release PR ships documentation to `main`, and that is a repository gap, not a builder bug.
And of the 54 branches on the homelab remote, 44 are `renovate/*` — which is moot now that only
`main` and `develop` are in scope.

## 3. Goals

1. One screen answers, for every repo at once: which tasks are ready, being worked, waiting on the
   operator, and done.
2. The operator's approval is the only route to *done*. An agent cannot close its own ticket.
3. Every repo that has a `docs/` folder appears on one internal site, under one name, with no commit
   to a list and without spending a GitHub Actions minute.
4. Reading documentation is a browser and a search box, not `nvim` and a path.
5. Nothing unmerged, internal or operator-facing is reachable from the internet.

## 4. Non-goals

- Watching a session live, per step or per tool call. The board shows task state, not activity
  **(operator, 2026-11-15)**.
- Feature branches, worktrees and drafts on the docs site; `main` and `develop` only **(operator,
  2026-11-15)**.
- Public access in this project. The public presentation and the user guide are phase 2, and this
  document only fixes their hooks **(operator, 2026-11-15)**.
- Replacing plan files. They remain the plan; the board carries state.
- Replacing git as the source of documentation.
- GitHub Actions, in any form **(operator, 2026-11-15)**.

## 5. Anti-goals

- Two sources of truth for task state, so that "is it done?" has two answers.
- A board that only exists in a web UI with no protocol behind it — it drifts the first week.
- A docs site that serves a half-written tree, or shows yesterday's build as today's.
- A public route for operator documentation, or an internal-only route for the guide written for
  end users.
- An agent moving its own ticket to *done*.

## 6. Constraints

Inherited from `2026-11-10-homelab-stabilisation-design.md` §6 and `AGENTS.md`:

- Everything reaches the cluster through git and ArgoCD. No `kubectl apply` against a managed
  resource; no `syncPolicy.automated` in an Application file (§7.1 of `AGENTS.md`).
- Ingress is Traefik v2 CRDs only. Internal and untrusted: `entryPoints: [web]`, host `*.kub.local`,
  plus `kube-system-lan-only@kubernetescrd`. `hack/check-exposure.sh` must pass.
- Free and open source only, no Bitnami, no vendored upstream charts, official chart where one
  exists (pattern D), official or LinuxServer images, Docker Hub only as a recorded exception.
- Every workload pinned with `nodeSelector: kubernetes.io/hostname: <node>`, one `local-path` claim
  per stateful concern, `strategy: Recreate` for stateful apps, memory requests and limits on every
  container, no workload on `rbp2`.
- Secrets are created by hand and never committed.
- PostgreSQL: one role per application, database created on demand when the app is rebuilt
  (`docs/runbooks/postgres-add-database.md`).

## 7. Approaches considered

### 7.1 Where task state lives

| Option | What it costs | Verdict |
|---|---|---|
| **A. Vikunja board, agents write to it over its own MCP endpoint** | One new service, PostgreSQL database, a protocol agents must follow | **Chosen** **(operator)**. The only option with a single cross-repo view, a human approval step, and an API |
| B. Keep the plan checkboxes as the ledger | Nothing new; but no cross-repo view, no approval step, and two sessions editing one plan collide | Rejected — it is today's state, and today's problem |
| C. Build a dashboard from OpenCode's session database | Live and accurate, no protocol to follow | Rejected — custom software with no human input path, and the operator does not need live activity |

### 7.2 How agents reach the board

| Option | What it costs | Verdict |
|---|---|---|
| **A. Vikunja's built-in MCP endpoint** | One MCP server entry; tools sit behind Code Mode's catalog rather than in every prompt; official, so no code to maintain | **Chosen** |
| B. A CLI wrapper plus a skill | Zero tools in context, full control of the verbs | Rejected — code we own forever for a marginal context saving |
| C. Direct REST calls from shell | No tools at all | Rejected — every agent writes its own `curl` and they diverge |

### 7.3 The documentation surface

| Option | What it costs | Verdict |
|---|---|---|
| **A. One builder in the cluster, one site per (repo, ref), one hostname** | A CronJob, a static web server, a read-only GitHub token | **Chosen** — one index, one hostname, no commit to maintain, no Actions minutes |
| B. One pipeline per project, published under path prefixes | Seven pipelines, seven search indexes, no shared navigation | Rejected |
| C. Move every project's docs into the homelab repo | Simplest possible site | Rejected — docs stop travelling with the code they describe |

### 7.4 How a merge reaches the site

| Option | What it costs | Verdict |
|---|---|---|
| **A. Poll the branch head, rebuild what changed** | One conditional GitHub request per (repo, ref) per minute; up to ~60 s of lag | **Chosen** **(operator, 2026-11-15)** — no public surface, no service to write, no Actions minute, and a `304 Not Modified` is not counted against the API rate limit |
| B. GitHub webhook into a receiver that creates the Job | Instant, but a public hostname amid a LAN-only rule, a new entry in the exposure allow-list, an HMAC secret, and a receiver service that exists only to turn a POST into a Job | Rejected — the largest attack surface in the project, protecting a documentation rebuild |
| C. Build on a fixed schedule | Simple, always stale by up to the interval | Rejected — it rebuilds unchanged sites and still lags |

## 8. Decisions taken (operator, 2026-11-15)

1. Vikunja is the board, and the only place task state lives.
2. The states are exactly: **ready**, **doing**, **waiting for validation**, **done**.
3. Agents create tickets. The operator validates or refuses them.
4. The board carries one Vikunja project per GitHub repository.
5. The docs site shows `main` and `develop` per repo, and nothing else.
6. The site is internal only, on `kub.local`, for now.
7. Phase 2, not built here: a public presentation of the homelab, and a user guide reachable only by
   the operator's users.
8. Vikunja is served over TLS at `https://vikunja.kub.local` with the internal certificate, HTTP
   redirects to HTTPS, the API path skips the Authentik gate, and it exposes Prometheus metrics and
   INFO-level logs **(operator, 2026-11-15)**.
9. Only the Vikunja part is built now; the documentation site plan stands unexecuted
   **(operator, 2026-11-15)**.

## 9. Part A — the board

### 9.1 Deployment

- Wrapper chart at `cluster/helm/vikunja/` (pattern D), pinning `go-vikunja/helm-chart` at an exact
  version in `Chart.yaml`, with every image tag pinned in `values.yaml`.
- Namespace `vikunja`, created by the Application's `CreateNamespace=true`.
- Database: one role and one database on the shared PostgreSQL, created by hand through
  `docs/runbooks/postgres-add-database.md` before first start. Credentials in Secret `vikunja-db`
  (namespace `vikunja`), consumed as `envFrom`/`env` by the chart.
- Storage: one `local-path` claim for attachments and the config file, 5 GiB, `Recreate`.
- Node: `nuc0`, per the placement map in §8.1 of the parent design. **This is the one placement risk
  in this document** — `nuc0` was at ~28.5 GB of 32 GB before this work. Measure before adding, and
  if there is no headroom, move Vikunja to `nas` (see §13).
- Ingress: `vikunja.kub.local` over TLS, the way every `kub.local` app works since 2026-11-15. One
  `IngressRoute` on `websecure` with `tls.options.name: internal-tls` (namespace `kube-system`, from
  the `traefik-gate` chart) and neither `secretName` nor `certResolver`, so Traefik serves the
  cluster's default internal certificate `kube-system/local-cert` (`*.kub.local`, see
  `docs/runbooks/internal-ca.md`). A second `IngressRoute` on `web` carries `kube-system-lan-only` then
  `kube-system-redirect-to-https`, so plain HTTP becomes HTTPS without ever reaching Authentik.
  Two rules on the TLS router, because they must not share a gate:
  - `/` with `kube-system-lan-only` then `kube-system-authentik-forwardauth` — the browser UI.
  - `/api` at `priority: 100` with `kube-system-lan-only` only — API clients (the MCP endpoint is
    `/api/v2/mcp`) send a token, not a session, and cannot complete a sign-in redirect. This is the
    pattern `bazarr` and `prowlarr` already use.
- Monitoring: `metrics.enabled: true` in `config.yml`, scraped from
  `prometheus.io/scrape: "true"`, `prometheus.io/port: "3456"`, `prometheus.io/path: /api/v1/metrics`
  pod annotations. No metrics credentials: the endpoint stays inside the cluster and the router's
  `/api` rule is LAN-only.
- Logging: `log.level: INFO`, `log.format: text`, `log.http: stdout`, `log.database: off`,
  `log.events: off` — verbose enough to explain a failed request, quiet enough that Fluent Bit is not
  ingesting HTTP noise on every poll.
- Resources: 100m / 256Mi requests, 512Mi limit for the app. The chart's own bundled PostgreSQL is
  disabled in favour of §6.

### 9.2 Board shape

- **One Vikunja project per GitHub repository, and only for repos that are real and active:**
  `homelab`, `config`, `ashuvidz-website`, `ashudev-website`, `selfhosted-helmcharts`, `ashuworld`,
  `ashuvidz-creator`. `scripts` is **not** a GitHub repository — it has no remote at all — so it gets
  no board, and its active sub-projects appear under the repository they belong to (`ashuworld` for
  the bank, `ashuvidz-creator` for the creator pipeline). Verified against `GET /user/repos`.
  A project is added when a repo first needs a ticket; nothing enumerates repos automatically here
  (unlike §10.2).
- Each project has one kanban view whose buckets are, in order: `Ready`, `Doing`,
  `Waiting for validation`, `Done`. `Done` is the view's done-bucket, so a task in it is marked done
  by Vikunja itself.
- **One saved filter, favourited, is the operator's screen:** every task whose bucket is
  `Waiting for validation`, across all projects, oldest first. The filter API takes queries without a
  project constraint, so this is one query, not six.
- A second saved filter, not favourited: every non-done task per repo, for the "what is in flight"
  question.

### 9.3 What a ticket is

**(design)** One ticket per plan task — the unit that already exists as a numbered step in a plan
file. Rationale: it is the largest unit a session finishes and a human can validate in one look, it
maps 1:1 onto something the plan already names, and it keeps the board around 10–30 cards per repo
rather than hundreds. Sessions do not get tickets, so exploratory work creates none.

A ticket carries:

| Field | Content |
|---|---|
| Title | The plan task's title |
| Description | `repo` · `plan path` · task number · what was done · the verification evidence (the command and its output, or the runbook step) · the PR link once one exists |
| Bucket | The state (§9.4) |
| Assignee | The agent role that did the work (`implementer`, `reviewer`), never the operator |
| Label | `agent` for agent-created, `operator` for the operator's own; plus the workstream ID (`W2`) where the plan has one |
| Priority | Copies the plan's ordering, and nothing else — it is not a second judgement |

### 9.4 State machine

| From | To | Who | When |
|---|---|---|---|
| — | `Ready` | operator or agent | The task exists in a plan and nobody has started it |
| `Ready` | `Doing` | agent | The session starts that task and assigns it to the account its token acts as |
| `Doing` | `Waiting for validation` | agent | The branch is pushed, the PR is open, the verification has been run and pasted into the description |
| `Waiting for validation` | `Done` | **operator only** | The operator accepts the work |
| `Waiting for validation` | `Doing` | operator | The operator refuses it; the comment says why, and the agent's next session reads it |

Nothing else moves a ticket. An agent that cannot finish leaves it in `Doing` with a comment saying
what blocks it — that comment is the "blocked" signal, so no fifth state is invented.

### 9.5 Agent transport

- Server entry in `~/.config/opencode/opencode.jsonc`:

  ```jsonc
  "mcp": {
    "servers": {
      "vikunja": {
        "type": "remote",
        "url": "http://vikunja.kub.local/api/v2/mcp",
        "oauth": false,
        "headers": { "Authorization": "Bearer {env:VIKUNJA_MCP_TOKEN}" }
      }
    }
  }
  ```

- The token is a Vikunja API token on the **Typed tools read + write** preset, not Full access, held
  in `~/.config/secrets/homelab.env` as `VIKUNJA_MCP_TOKEN` and loaded by the `opencode` wrapper — the same mechanism as
  `GITHUB_PERSONAL_ACCESS_TOKEN`.
- Scoping: a `vikunja_*` deny rule for `explore` (it is a read-only search agent and has no business
  writing tickets), allowed for the primary agent, `implementer` and `reviewer`. OpenCode drops a
  server's instructions from an agent's prompt when every one of its tools is denied, so the other
  agents pay nothing.
- Context cost: with Code Mode on by default, Vikunja's tools are reached through `execute` and cost
  a catalog line, the same way the 46 GitHub tools already do.

### 9.6 Where the protocol lives

- **Short form, five lines, in `~/.config/opencode/AGENTS.md`** — the file every session reads. It
  states the four states, the one-ticket-per-plan-task rule, and that only the operator moves a
  ticket to `Done`. Without this, agents will not do it.
- **Long form, `docs/agent-protocol.md`** in this repo: the table in §9.4, the ticket fields, worked
  examples of a healthy handover and of a refusal. The AGENTS.md lines link to it.

### 9.7 The "no second todo list" rule

`docs/README.md` says work state lives in "Vikunja only, once it exists" and that there is no second
todo list. Vikunja now exists, so the rule is applied, with one clarification:

- **The board is state.** Whether a task is ready, being worked, awaiting review or done is answered
  there and nowhere else.
- **Plan checkboxes are the plan.** Agents keep ticking them as they go, because the plans' own
  skills read them. They are a record of what was planned and executed, not a state tracker, and they
  are never consulted to answer "is this done".
- `docs/README.md` §"Where each kind of knowledge lives" gains that sentence, replacing "once it
  exists" with a link to `vikunja.kub.local`.

## 10. Part B — the documentation site

### 10.1 Shape

One hostname, `docs.kub.local` on the `web` entrypoint, LAN-only. Under it, one site per (repo, ref):

```
/                          index: every repo × ref present, with the build time
/homelab/develop/          Material site, own nav, own search
/homelab/main/             idem (empty today, see §2)
/scripts/develop/
...
```

Separate builds rather than one merged tree because MkDocs' search index is per build: merging refs
into one site returns the same page three times, once per ref. This also means a broken docs tree in
one repo cannot take the whole site down.

### 10.2 Discovery and fetch

A CronJob on a one-minute schedule — a **(design)** call over a longer interval, because it now costs
almost nothing: each run only asks whether a branch moved, and rebuilds only what moved. Each run:

1. `GET https://api.github.com/users/vyrtualsynthese/repos?per_page=100&type=owner` with a
   fine-grained, read-only token (`Contents: read`, `Metadata: read`) from Secret `github-docs-ro` in
   namespace `docs`. New repositories appear here with no commit anywhere, which is the requirement.
2. For each repo, for each ref in `main`, `develop`: `GET /repos/vyrtualsynthese/<repo>/commits/<ref>`
   as a conditional request, and compare the returned head SHA with the SHA the last successful build
   recorded for that (repo, ref). Unchanged pairs stop here — a `304 Not Modified` costs no rate-limit
   budget, so the steady-state cost of polling is one round trip per pair.
3. For a changed pair, fetch `GET /repos/vyrtualsynthese/<repo>/tarball/<ref>` and unpack. A tarball,
   not a `git clone` — no git binary in the builder image, and no `.git` to store.
4. Skip the (repo, ref) pair when there is no `docs/` folder, or when it contains no Markdown. Today
   that means `main` is absent for every repo whose docs have never been released, and the `main`
   column of the index is honestly empty instead of six empty sites.
5. A separate daily run rebuilds every (repo, ref) whether or not its head moved, so a change to the
   builder's own templates or to a generated `mkdocs.yml` still reaches the site.


### 10.3 Build

- If the repo ships its own `mkdocs.yml`, use it verbatim — a repo owns its own docs.
- Otherwise generate one: Material theme, `search`, `pymdownx.superfences` for Mermaid, a nav derived
  from the directory tree (so an agent adding a file needs no nav edit), `site_name` = repo name and
  ref.
- Build with `mkdocs build`; strict mode is never passed on the command line. A repository that wants
  it sets `strict: true` in its own `mkdocs.yml`, because repositories link to files that live outside
  `docs/` and those links would otherwise fail every build. A failing (repo, ref) is marked failed on
  the index page and keeps its previous build serving.
- Builder image `squidfunk/mkdocs-material`, the project's own, pinned by tag; fetcher image `alpine`
  with `curl` and `tar`. Both are Docker Hub, so both go in the §8.7 exception table (§11.2).

### 10.4 Serving, and never serving a half-build

- Builder writes to a staging directory on the PVC; on success it swaps `<repo>/<ref>` into place.
  A failed or partial run leaves the previous build serving, and the index page records the last
  good build time per section.
- Server: `nginx-unprivileged` pinned by tag, read-only root filesystem, the PVC mounted read-only at
  the web root, `strategy: Recreate`, 32Mi / 64Mi memory.
- One IngressRoute: `docs.kub.local`, `entryPoints: [web]`, `kube-system-lan-only@kubernetescrd`.
  No TLS, following the `grafana.kub.local` / `prometheus.kub.local` convention.

### 10.5 Triggering by hand

The chart ships the CronJob and a `Job` template of the same pod, so a fresh build is
`kubectl create job --from=cronjob/docs-site-build docs-manual-1 -n docs`. Agents cannot do it — they
have no cluster writes — which is intended: the site is not an agent's output.

### 10.6 Phase 2 hooks, not built here

- **Audience split by folder.** `docs/user/**` leaves the operator site and becomes its own build with
  its own search; the operator site stops rendering it. One source tree, two outputs, no duplicated
  text.
- **User guide route.** Served at a public hostname behind
  `kube-system-authentik-forwardauth@kubernetescrd`, so only the operator's users get in — the
  middleware already exists and is already in use on three apps. This is the one public-facing part
  of the docs work, and it is phase 2.
- **Public presentation.** Not documentation and not this document: the 2026-11-12 showcase-dashboard
  design already covers explaining the lab to a guest. A public landing page would be its own spec.

## 11. Part C — operations

### 11.1 New secrets, created by hand, never committed

| Namespace | Secret | Keys |
|---|---|---|
| `vikunja` | `vikunja-db` | `VIKUNJA_DATABASE_PASSWORD` |
| `docs` | `github-docs-ro` | `token` |

The MCP token is not a cluster Secret: it lives in `~/.config/secrets/homelab.env` on the operator's
machine, and the `opencode` wrapper sources that file so MCP servers see it.

### 11.2 Image policy additions

`squidfunk/mkdocs-material`, `nginxinc/nginx-unprivileged` and `alpine` are Docker Hub. For the first
two, upstream publishes nothing else, so they belong in the §8.7 table with that reason. `alpine` is
already used by `qbittorrent` and already recorded as a floating-tag defect (§7.12 of `AGENTS.md`);
this chart pins a version instead of reproducing it.

### 11.3 Placement

Both parts are small: the docs pods are tens of MiB, Vikunja a few hundred. Only Vikunja is stateful.
Both are pinned to `nuc0` per §8.1 of the parent design. The RAM on `nuc0` is the constraint that
matters (§13).

### 11.4 Verification

| Part | Test |
|---|---|
| Vikunja chart | `helm lint`, `helm template -n vikunja`, `kubeconform -strict` |
| Vikunja running | `Synced`/`Healthy`, a kanban card moves between all four buckets, and `GET /api/v2/mcp` answers a tool call with the agent token |
| Agent protocol | one real plan task taken from `Ready` to `Waiting for validation` by an agent, and refused once by the operator, with the comment read back by the next session |
| Docs builder | a run produces `/homelab/develop/`; a repo with no `docs/` on `develop` produces no section; a deliberately broken `--strict` build leaves the last good build serving |
| Change detection | a merge to `develop` appears on the site within two runs; a second run with no merge rebuilds nothing and logs the unchanged head SHAs; the daily full rebuild still runs |
| Auto-discovery | a throwaway private repo containing only `docs/index.md` appears on the index within two runs, and disappears when the token loses access |
| Exposure | `hack/check-exposure.sh` passes; `docs.kub.local` and `vikunja.kub.local` both refuse a request from off-LAN |

### 11.5 Rollback

Delete the two Applications. The docs site holds no state beyond a rebuildable PVC. Vikunja holds the
board, so its export is a `vikunja` dump in the same PostgreSQL nightly backup; refusing to lose the
board is why it is on PostgreSQL rather than SQLite in a pod.

## 12. Resolved in the plans

The two implementation plans settle everything this section used to leave open, so what remains is
the work itself:

| Was open | Now decided | Where |
|---|---|---|
| Upstream Vikunja chart version and image tags | `vikunja` 2.6.0 (OCI) and image `vikunja/vikunja:2.8.0`; `squidfunk/mkdocs-material:9.8.1`; `nginxinc/nginx-unprivileged:1.31-alpine` | `2026-11-15-vikunja-board.md` Task 2, `2026-11-15-docs-site.md` Task 4 |
| The builder's command and resources | `python3 /opt/builder/build.py`, 100m/256Mi request, 1Gi limit | `2026-11-15-docs-site.md` Task 4 |
| The index page's shape | One row per (repo, ref), linked only when the build is `ok` | `2026-11-15-docs-site.md` Task 3 |
| The short-form AGENTS.md wording | Five verbatim lines | `2026-11-15-vikunja-board.md` Task 6 |
| How the buckets are created | An idempotent API script, `hack/vikunja-seed.sh` | `2026-11-15-vikunja-board.md` Task 4 |
| `--strict` | Never on the command line; a repo opts in through its own `mkdocs.yml` | `2026-11-15-docs-site.md` Task 2 |

## 13. Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| `nuc0` has no RAM headroom for Vikunja | Pod OOM or eviction of a neighbour | Measure before deploy; the fallback is `nas`, which is where the database and the disks already are |
| Agents skip the protocol under context pressure | The board silently becomes a lie, which is worse than no board | The five AGENTS.md lines are the mechanism; verification in §11.4 is one real task end to end, refused and resumed |
| A Vikunja upgrade moves the MCP endpoint or the API | Agents break | The chart version is pinned; the upgrade is a deliberate act with the §11.4 MCP check re-run |
| The builder's GitHub token is broad | Read access to every repo leaks through one Secret | Fine-grained token, `Contents: read` only, one repo owner, rotated when the operator rotates the others |
| Polling hits the GitHub API limit | Builds stop silently | 7 repos × 2 refs = 14 conditional requests a minute; `304` responses are not counted against the 5000/hour budget, so only actual merges spend it |
| `main` stays empty forever | Half the site is a promise nobody keeps | Accepted by the operator (§2); a release PR that ships docs to `main` closes it |

## 14. Calls made without the operator asking

Each of these is this document's judgement, listed so it can be overruled in one line rather than
discovered later:

1. One ticket per plan task, not per session and not per PR (§9.3).
2. A blocked task is a comment in `Doing`, not a fifth bucket (§9.4).
3. Vikunja sits behind the Traefik forward-auth gate in phase 1, with its own OpenID deferred to
   phase 2 (§9.1).
4. One-minute polling with per-ref change detection instead of a webhook or a fixed rebuild interval
   (§7.4, §10.2).
5. One site per (repo, ref) rather than one merged site with duplicated search hits (§10.1).
6. Repos without `docs/` on a ref contribute nothing, rather than an empty site (§10.2).
7. Tarball fetch instead of `git clone` (§10.2).
8. `docs/agent-protocol.md` in this repo, with the short form in the global `AGENTS.md` (§9.6).
9. The board is seeded by an idempotent API script in `hack/`, not by hand in the UI, so a rebuilt
   cluster gets the same board (§9.2).
10. The upstream Vikunja chart is consumed as an OCI artifact pinned to 2.6.0, with bjw-s `common`
    1.5.1 arriving as its own dependency; if that subchart does not come with the artifact, our
    wrapper declares it as a second dependency rather than copying files (§9.1).
11. `vikunja-db` holds one key, the password. Host, database and user are not secrets and live in
    `values.yaml` where they can be reviewed (§11.1).
12. `--strict` is never passed to MkDocs; a repository that wants strict builds sets it in its own
    `mkdocs.yml` (§10.3).
