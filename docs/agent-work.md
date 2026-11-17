# Agent work: the plans and the specs

> **In one line.** `docs/superpowers/` holds the design that argues for a change and the plan that
> executes it, both written by agents for agents: this page explains how to read them and indexes
> them by theme, starting with the stabilisation programme and the media charts.
>
> **Use it when** you are picking up a plan, wondering why a past decision was made, or looking for
> the argument behind a chart that looks odd · **Verified** 2026-11-17 · **Kind:** living page

## What this folder is

A sizeable change usually starts as a **spec** and is cut into a **plan**. The spec argues — what is
wrong, the options, what was chosen and what it costs; the plan executes — numbered tasks, the files
to touch, the check that proves it landed. They pair by filename,
`specs/<date>-<slug>-design.md` with `plans/<date>-<slug>.md`, and the plan names its spec in a
`**Spec:**` line.

The paths are fixed on purpose. The writing-plans and executing-plans skills find a plan at
`docs/superpowers/plans/` and its design at `docs/superpowers/specs/`, so this project indexes the
folder instead of tidying it: nothing under `docs/superpowers/` is edited, moved or renamed. A new
plan or design gets one row on this page when it is written, and that row is the whole of its
maintenance here.

The prose is the reasoning, written for whoever picks the work up next: the rejected options, the
trap that cost a day, the number measured rather than guessed. Its commands and their output are the
evidence, not the plan's summary.

## How to read a plan

A plan opens with a fixed block: `**For agentic workers:**` names the skill, **Goal** the outcome,
**Architecture** the shape, `**Spec:**` the argument. Then **Global Constraints**, which hold for
every task in it — no failover, CRLF under `cluster/argocd/**`, the builder cannot write to the
cluster.

Every task is a checkbox list. **A tick means planned and executed by an agent, not done.** The plan
knows nothing about review: the state of the work is the board, and when the two disagree the plan is
the one that gets corrected. See [The board protocol](agent-protocol.md).

Read a plan beside the board, because tasks were reordered as reality intervened. And where the
deliverable is a runbook, execute the runbook rather than the tick: several plans hand steps to the
operator.

## Stabilisation programme

The rebuild after the 2026 storage failure. The design is the argument; the programme index sequences
the workstreams and hands each to its own plan.

| Document | What it settled | Date |
|---|---|---|
| [Stabilisation and rebuild design](superpowers/specs/2026-11-10-homelab-stabilisation-design.md) | why the cluster is rebuilt, not patched: what failed, the constraints, the order | 2026-11-10 |
| [Rebuild programme index](superpowers/plans/2026-11-10-homelab-programme-index.md) | how the workstreams are sequenced so each is planned, executed and verified alone | 2026-11-10 |
| [Longhorn cleanup](superpowers/plans/2026-11-10-w0-cleanup.md) | removing the dead storage layer; the held data is abandoned by decision | 2026-11-10 |
| [Platform upgrades](superpowers/plans/2026-11-11-w5-platform.md) | K3s and Traefik off the bundled versions, rehearsed, certificate kept | 2026-11-11 |
| [Shared PostgreSQL design](superpowers/specs/2026-11-13-postgresql-design.md) | one instance for every application, and no database created ahead of need | 2026-11-13 |
| [PostgreSQL W2](superpowers/plans/2026-11-13-postgresql-w2.md) | the dead Bitnami instance and broken pgAdmin replaced by an owned chart on `local-path`, a network policy and nightly dumps | 2026-11-13 |

## Media charts

One chart per application, rebuilt to the contract in [Chart notes](reference/chart-notes.md) and the
shape of `autobrr` or `prowlarr`. The design argues for that app's divergences.

| Application or feature | What it settled | Plan | Design |
|---|---|---|---|
| cross-seed | the image's real needs: read-only config, torrents, liveness only, no route | [Plan](superpowers/plans/2026-11-12-cross-seed-chart-refactor.md) | [Design](superpowers/specs/2026-11-12-cross-seed-chart-refactor-design.md) |
| sonarr | both instances on the contract, measured memory, exporter port fixed | [Plan](superpowers/plans/2026-11-12-sonarr-chart-refactor.md) | [Design](superpowers/specs/2026-11-12-sonarr-chart-refactor-design.md) |
| radarr | the `prowlarr` shape, and `radarr-anime` off root | [Plan](superpowers/plans/2026-11-12-radarr-chart-refactor.md) | [Design](superpowers/specs/2026-11-12-radarr-chart-refactor-design.md) |
| bazarr | the stalled remote chart replaced, exporter wired but off | [Plan](superpowers/plans/2026-11-13-bazarr-chart-refactor.md) | [Design](superpowers/specs/2026-11-13-bazarr-chart-refactor-design.md) |
| lidarr | a claim it owns, metrics exported, LAN-only route kept | [Plan](superpowers/plans/2026-11-13-lidarr-chart-refactor.md) | [Design](superpowers/specs/2026-11-13-lidarr-chart-refactor-design.md) |
| maintainerr | a claim the chart creates, LAN only, honest probes | [Plan](superpowers/plans/2026-11-13-maintainerr-chart-refactor.md) | [Design](superpowers/specs/2026-11-13-maintainerr-chart-refactor-design.md) |
| recyclarr | the scaffold turned into a contract CronJob | [Plan](superpowers/plans/2026-11-13-recyclarr-chart-refactor.md) | [Design](superpowers/specs/2026-11-13-recyclarr-chart-refactor-design.md) |
| seerr | the public-route shape: redirect, headers, rate limit, TLS | [Plan](superpowers/plans/2026-11-13-seerr-chart-refactor.md) | [Design](superpowers/specs/2026-11-13-seerr-chart-refactor-design.md) |
| tautulli | a config claim it actually owns, LAN only | [Plan](superpowers/plans/2026-11-13-tautulli-chart-refactor.md) | [Design](superpowers/specs/2026-11-13-tautulli-chart-refactor-design.md) |
| unpackerr | a new image, API keys out of git and read from Secrets | [Plan](superpowers/plans/2026-11-13-unpackerr-chart-refactor.md) | [Design](superpowers/specs/2026-11-13-unpackerr-chart-refactor-design.md) |
| media dashboard | a Grafana screen for what the stack is doing and where it sticks | [Plan](superpowers/plans/2026-11-13-media-dashboard.md) | [Design](superpowers/specs/2026-11-13-media-dashboard-design.md) |
| books library | Calibre desktop out, Calibre-Web Automated in, Kavita rebuilt | [Plan](superpowers/plans/2026-11-13-books-library.md) | [Design](superpowers/specs/2026-11-13-books-library-design.md) |

The running pipeline is [Media](how-it-works/media.md).

## Identity

One login service replaces the per-application logins. The phases are ordered: the instance is
repaired before any application is told to trust it, the sign-in sources come next, and the
applications follow one at a time.

| Document | What it settled | Date |
|---|---|---|
| [Authentik SSO design](superpowers/specs/2026-11-14-authentik-sso-design.md) | the programme as three phases, and why repair precedes sources and sources precede applications | 2026-11-14 |
| [Authentik phase 1 — repave the instance](superpowers/plans/2026-11-14-authentik-phase1-repave.md) | the instance on a current chart, configuration applied from blueprints in git, a valid certificate | 2026-11-14 |
| [Authentik phase 2 — Discord and Plex](superpowers/plans/2026-11-14-authentik-phase2-sources.md) | Discord guild membership and Plex shares as the two ways in, both landing in one group | 2026-11-14 |
| [Authentik phase 3 — four applications](superpowers/plans/2026-11-14-authentik-phase3-apps.md) | Argo CD, Grafana, pgAdmin and Kavita on OIDC, one provider and one binding per application | 2026-11-14 |
| [Authentik in front of the media tools](superpowers/specs/2026-11-14-media-tools-authentik-design.md) | what to do where native OIDC does not exist: one gate in front of nine tools, their own logins switched off | 2026-11-14 |
| [Media tools behind authentik](superpowers/plans/2026-11-14-media-tools-authentik.md) | the gate applied one tool at a time, starting with Sonarr, with the UIs left to the operator | 2026-11-14 |
| [SSO access model design](superpowers/specs/2026-11-14-sso-access-model-design.md) | three levels — admin, user, guest — bound to groups, replacing a single access policy, plus optional MFA | 2026-11-14 |
| [SSO access model](superpowers/plans/2026-11-14-sso-access-model.md) | the bindings, FileBrowser's folder scopes, and where MFA actually applies | 2026-11-14 |
| [FileBrowser in its own namespace](superpowers/specs/2026-11-14-filebrowser-namespace-design.md) | why a media server with its own login does not belong in the media-stack namespace | 2026-11-14 |
| [FileBrowser namespace move](superpowers/plans/2026-11-14-filebrowser-namespace.md) | the move itself: wrapper chart, fresh config claims, the same three NFS exports, its own login | 2026-11-14 |

The rule this programme follows — native OIDC first, proxy auth where the application is built for
it, the forward-auth middleware last — is in [Who gets in](how-it-works/identity.md).

## Observability

Prometheus, Grafana and Loki as owned wrapper charts, then the rules and the dashboards that make
them worth reading. An alert that cannot fire and a panel that flatters are both defects, and the
plans below treat them that way.

| Document | What it settled | Date |
|---|---|---|
| [Observability stack design](superpowers/specs/2026-11-12-observability-stack-design.md) | why four wrapper charts on pinned upstream versions, and why every piece sits on `nas` | 2026-11-12 |
| [Observability stack](superpowers/plans/2026-11-12-observability-stack.md) | the two hand-edited charts and the never-deployed Loki directory replaced by one chart per component | 2026-11-12 |
| [Alerting redesign](superpowers/specs/2026-11-12-alerting-design.md) | a rule set where several rules could not fire, and what is worth waking someone for | 2026-11-12 |
| [Dashboards overhaul](superpowers/plans/2026-11-12-dashboards-overhaul.md) | the five dashboards made honest and quick to debug from, every query validated before its panel counted | 2026-11-12 |
| [Showcase dashboard design](superpowers/specs/2026-11-12-showcase-dashboard-design.md) | one screen that explains the lab to someone who has never seen a server, without flattering it | 2026-11-12 |
| [Grafana alert links (design)](superpowers/specs/2026-11-12-grafana-alert-link-base-url.md) | every absolute URL derives from `root_url`, so one unset key pointed every Source and Silence link at the reader's own device | 2026-11-12 |
| [Grafana alert link base URL (plan)](superpowers/plans/2026-11-12-grafana-alert-link-base-url.md) | the fix: `root_url` and `domain` set in the wrapper chart, nothing else touched | 2026-11-12 |
| [Observability after the media chart repairs](superpowers/plans/2026-11-13-observability-post-repair.md) | the four findings the rebuilt charts exposed: a temperature alert never verified, no workload-down rule, a DNS gap, app-side misconfiguration | 2026-11-13 |
| [Vulnerability alerting](superpowers/specs/2026-11-14-vulnerability-alerting-design.md) | what the data can and cannot support for a severity warning, deferred with the two approaches that survived | 2026-11-14 |

That table has one exception to the naming rule above: the design for the Grafana alert links is
`specs/2026-11-12-grafana-alert-link-base-url.md` with no `-design` suffix, so design and plan share
a filename under two folders. Those two rows say `(design)` and `(plan)` for that reason.

The live stack and its known holes are [Observability conventions](observability-conventions.md).

## Media stack security

The router forwards 80 and 443 to the cluster and Traefik routes on the `Host` header alone, so a
route without a gate answers the internet whether or not its name resolves publicly. Phase 0 closed
the door; the phases behind it are argued and unbuilt.

| Document | What it settled | Date |
|---|---|---|
| [Media stack security design](superpowers/specs/2026-11-14-media-stack-security-design.md) | what the media-stack namespace exposes, the phases that close it, and the order they go in | 2026-11-14 |
| [Media stack security — Phase 0](superpowers/plans/2026-11-14-media-stack-security-phase0.md) | one lan-only middleware on every non-public route, and a checker that fails a route that forgets it | 2026-11-14 |

The enforced rule is [Routes](reference/routes.md): the gate column, not the address, decides who
gets in.

## Board and docs

Where task state lives, where the knowledge lives, and the join between them: a board that is the
state, a site that is the reading, and alerts that become a queue instead of a scrolling message.

| Document | What it settled | Date |
|---|---|---|
| [Agent work tracking and one documentation site](superpowers/specs/2026-11-15-agent-work-tracking-and-docs-site-design.md) | the board as the single place task state lives, and one site rendering every repository's docs | 2026-11-15 |
| [Documentation site](superpowers/plans/2026-11-15-docs-site.md) | one internal hostname rebuilt within a minute of a merge, no commit to maintain, no CI minute spent | 2026-11-15 |
| [Vikunja task board](superpowers/plans/2026-11-15-vikunja-board.md) | Vikunja on the shared PostgreSQL, agents reaching it over its own MCP endpoint | 2026-11-15 |
| [The board workflow design](superpowers/specs/2026-11-16-board-workflow-design.md) | what a ticket is, which columns may hold a card, and the wording that goes on it | 2026-11-16 |
| [The board workflow](superpowers/plans/2026-11-16-board-workflow.md) | six columns, the existing tickets and merged pull requests filed correctly, and a protocol that keeps them there | 2026-11-16 |
| [Grafana alerts to the board](superpowers/specs/2026-11-16-alerts-to-board-design.md) | an alert as a queue item somebody looked at, rather than a message that wakes the same person forty times | 2026-11-16 |
| [Documentation for the operator design](superpowers/specs/2026-11-17-docs-for-the-operator-design.md) | the three jobs the documentation does for one reader, and the site shape that serves them | 2026-11-17 |
| [Documentation for the operator](superpowers/plans/2026-11-17-docs-for-the-operator.md) | the seven-tab site built in four pull requests, each merged before the next starts | 2026-11-17 |

The protocol itself is [The board protocol](agent-protocol.md); this page is the index it points at.

## One-offs

Plans that are neither a programme nor a chart refactor.

| Document | What it settled | Date |
|---|---|---|
| [AshuLab logo](superpowers/plans/2026-11-14-ashulab-logo.md) | the mark as a hand-drawn vector master — rasters to choose, SVG to keep — crisp at 32 px and safe under Discord's circular crop | 2026-11-14 |

## The count

**60 documents** — 32 plans and 28 designs — each linked exactly once on this page. Counted against
`docs/superpowers/` on 2026-11-17.
