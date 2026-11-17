# Running it

> **In one line.** The day-2 index: the intention you arrive with, the one page that answers it, and
> the runbook behind it when the answer is a procedure rather than an explanation.
>
> **Use it when** something needs doing to the cluster and you want the shortest route to the page ·
> **Verified** 2026-11-17 · **Kind:** living page

## I want to…

| I want to… | Start here |
|---|---|
| see what is broken right now | [Application status](../status.md) — a dated snapshot: what runs, what is `Pending`, what is broken, and the faults already owned |
| do the daily check | [Every day: the five-minute check](every-day.md) — four commands, three dashboards, one comparison |
| ship a change | [Ship a change](ship-a-change.md) — find the value, run the checks, branch, PR, watch the sync |
| roll back | [Roll back a change](roll-back.md) — a revert in git; the cluster-side "rollbacks" do not hold |
| add an application | [Add an application](add-an-application.md) — chart, Application file, gated route, Secret, catalogue row |
| fix a pending pod | [A pod that will not start](fix-a-pending-pod.md) — the event at the end of `describe pod` names the fault |
| rotate a secret | [Rotate a secret](rotate-a-secret.md) — every copy of the value, then the restart that makes it take |
| use the board | [Use the board](use-the-board.md) — columns, kinds, and which moves are yours alone |
| rebuild the docs site | [Rebuild the docs site](rebuild-the-docs-site.md) — the builder, the manual Job, and why a page is missing |
| recover from a loss | [Disaster recovery](disaster-recovery.md) — what a dead disk takes, what exists, what does not |

Each page opens with a summary blockquote and the date it was last checked against the cluster; read
that date before trusting a claim. A page whose date is older than the last merge on `develop`
describes an older cluster.

## When the question is not on this list

- **How the thing works** — nodes, storage, network, identity, observability, media, and the catalogue
  of applications: start at [How it works](../how-it-works/README.md).
- **Where the work stands** — the board at [vikunja.kub.local](https://vikunja.kub.local), LAN only.
  The long form of how work moves is [the operator↔agent protocol](../agent-protocol.md).
- **Why a decision was made** — the dated records: [the decisions](../decisions/README.md) and the
  audit set ([hosts](../audit/ansible-hosts.md), [ArgoCD](../audit/argocd-helm.md),
  [storage](../audit/observability-storage.md)) argue; they do not instruct.

## Runbooks

The pages above stop at the application: a chart, an Application, a route, a Secret. A procedure that
touches a host, a data directory or a credential is a runbook, and one exists only after it has been
run at least once. The shelf, with each runbook's status, is
[`runbooks/README.md`](../runbooks/README.md).

The ones this index sends you to directly:

- [postgres-restore.md](../runbooks/postgres-restore.md) — a database from the nightly dump; the only
  restore with a written procedure, and it has not been run.
- [internal-ca.md](../runbooks/internal-ca.md) — mint the root, sign a leaf, re-trust after a loss.
- [authentik-apps.md](../runbooks/authentik-apps.md) — put an application behind SSO.
- [vikunja-board-seed.md](../runbooks/vikunja-board-seed.md) — recreate the board's columns and
  verify them after a loss; its cards need a restored dump.
- [docs-site-first-run.md](../runbooks/docs-site-first-run.md) — the docs site's first start, written
  ahead of the build that proves it.
- [postgres-add-database.md](../runbooks/postgres-add-database.md) — a new role and database, proved
  on the acceptance instance.

Pending runbooks — etcd restore, the offsite restore drill, the K3s upgrade, adding a node, rotating
a secret at the host level — are listed in [`runbooks/README.md`](../runbooks/README.md) with the
workstream that will write them. Until then, the page above is all the advice there is.

## The rule that outranks the index

GitOps is the only door for a change to an application: edit the chart, commit, open the pull
request. A `kubectl edit` or `kubectl apply` against a managed object is reverted by self-heal or the
next sync, and the change leaves no record. If a page here and the cluster disagree, the page
describes what the next sync restores; the cluster shows what drifted away from it.
