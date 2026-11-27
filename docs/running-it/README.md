# Running it

> **In one line.** The day-2 index: the intention you arrive with, the one page that answers it, and
> the runbook behind it when the answer is a procedure rather than an explanation.
>
> **Use it when** something needs doing to the cluster and you want the shortest route to the page ·
> **Verified** 2026-11-22 · **Kind:** explanation
>
> **Sources** `mkdocs.yml`, the pages this one indexes, `docs/runbooks/README.md`

## I want to…

| I want to… | Start here |
|---|---|
| see what is broken right now | the cluster: `kubectl get pods -A` and ArgoCD's application list; [Every day](every-day.md) is the sweep that reads them |
| do the daily check | [Every day: the five-minute check](every-day.md) — four commands, three dashboards, one comparison |
| ship a change | [Ship a change](ship-a-change.md) — find the value, run the checks, branch, PR, watch the sync |
| roll back | [Roll back a change](roll-back.md) — a revert in git; the cluster-side "rollbacks" do not hold |
| add an application | [Add an application](add-an-application.md) — chart, Application file, gated route, Secret, catalogue row |
| fix a pending pod | [A pod that will not start](fix-a-pending-pod.md) — the event at the end of `describe pod` names the fault |
| rotate a secret | [Rotate a secret](rotate-a-secret.md) — every copy of the value, then the restart that makes it take |
| use the board | [Use the board](use-the-board.md) — columns, kinds, and which moves are yours alone |
| rebuild the docs site | [Rebuild the docs site](rebuild-the-docs-site.md) — the builder, the manual Job, and why a page is missing |
| cut a release | [Cutting a release](../runbooks/release.md) — the release pull request, the tag and its body, then `main` back into `develop` |
| recover from a loss | not this tab: [Backups](../backup/README.md) takes a dead disk, a broken database, a node and a total loss, one page each |

Each page opens with a summary blockquote and the date it was last checked against the cluster; read
that date before trusting a claim. A page whose date is older than the last merge on `develop`
describes an older cluster.

## When the question is not on this list

- **How the thing works** — nodes, storage, network, identity, observability, media, and the catalogue
  of applications: start at [How it works](../how-it-works/README.md).
- **Where the work stands** — the board at [vikunja.kub.internal](https://vikunja.kub.internal), LAN only.
  How work moves is the contract in `~/.config/opencode/AGENTS.md` §Task board, with the long form on
  [The board and worktrees](https://docs.kub.internal/config/develop/agent/board-and-worktrees/).
- **Why a decision was made** — the dated records: [the decisions](../decisions/README.md) and the
  audit set ([hosts](../audit/ansible-hosts.md), [ArgoCD](../audit/argocd-helm.md),
  [storage](../audit/observability-storage.md)) argue; they do not instruct.

## The procedures

The pages above stop at the application: a chart, an Application, a route, a Secret. A procedure that
touches a host, a data directory or a credential is a runbook, and one exists only after it has been
run at least once. Each one's status — proved on a date, or still pending its first run — is
[the shelf](../runbooks/README.md), which is also where the unwritten ones are named.

| Procedure | Use it when |
|---|---|
| [The GitOps controller](../runbooks/argocd.md) | Argo CD is down, or must be rebuilt from nothing |
| [Moving the Traefik release](../runbooks/traefik-release.md) | the router's chart has to move, be inspected or be rolled back — by `helm`, because K3s's helm-controller runs nothing here |
| [Moving K3s to a new minor](../runbooks/k3s-upgrade.md) | a node has to follow the cluster to a new version, one hostname at a time |
| [Renovate](../runbooks/renovate.md) | the dependency pull requests stopped, or its token has to be rotated |
| [The GPU stack](../runbooks/gpu-operator.md) | the GPU disappears from a pod, or Plex stops transcoding |
| [Cloud monitoring](../runbooks/cloud-monitoring.md) | a `Cloud*` alert fires, or a public name or DNS record changes |
| [The alert relay](../runbooks/alert-relay.md) | Grafana alerts stop reaching the board, or the relay needs rebuilding |
| [Add a database](../runbooks/postgres-add-database.md) | an application is rebuilt and needs its own role, database and Secrets |
| [Shape the board](../runbooks/vikunja-board-seed.md) | the board's columns have to be recreated from the tracked seed |

Two procedures are not here because the situation picks the tab: [a restore](../backup/README.md)
belongs to Backups, and so does PostgreSQL's; [wiring an app to authentik](../security/README.md) and
[the internal CA](../runbooks/internal-ca.md) belong to Security.

## The rule that outranks the index

GitOps is the only door for a change to an application: edit the chart, commit, open the pull
request. A `kubectl edit` or `kubectl apply` against a managed object is reverted by self-heal or the
next sync, and the change leaves no record. If a page here and the cluster disagree, the page
describes what the next sync restores; the cluster shows what drifted away from it.
