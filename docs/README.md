# Documentation

> **In one line.** The start-here page for this homelab: what the machines are, how a change reaches
> them, and where the answer to your question already lives.
>
> **Use it when** you are new to the cluster, or you cannot remember which page owns a subject ·
> **Verified** 2026-11-22 · **Kind:** explanation
>
> **Sources** `mkdocs.yml`, `AGENTS.md`, the pages under `docs/`

## What this is

This is the documentation of one K3s homelab: four cluster nodes, two edge Pis, and the applications
they run. Everything authoritative about it lives here in `docs/`, and where a file elsewhere in the
repository disagrees with one of these pages, these pages win. The same files render as a site with
navigation and search at
[https://docs.kub.internal/homelab/develop/](https://docs.kub.internal/homelab/develop/).

## Five things worth remembering

Most of what looks strange at first follows from five rules. Each links the page that argues it.

1. **Every rebuilt workload is pinned** to a named machine, and a `local-path` volume is created on
   the machine its first pod landed on. [The machines](how-it-works/nodes.md)
2. **Configuration lives on `local-path`**: a directory on one machine's own disk. There is no
   replicated volume and no failover, so losing that disk loses the data.
   [Where the data lives](how-it-works/storage.md)
3. **GitOps is the only door for applications.** A merge to `develop` is a deployment; a hand edit is
   drift that gets reverted. [GitOps](how-it-works/gitops.md)
4. **Every non-public route carries `lan-only`.** The house router forwards 80 and 443 to the cluster
   and Traefik routes on the `Host` header alone, so a route without the gate answers the internet.
   [Network](how-it-works/network.md)
5. **Native SSO first.** An app that speaks OIDC uses it. The forward-auth gate treats everyone who
   passed as the same person, so it is the fallback, not the default. [Identity](how-it-works/identity.md)

## Four ways in

| I want to… | Start here |
|---|---|
| **Understand** the place | [The homelab in one page](how-it-works/README.md), then the topic pages it lists |
| **Operate** it: a change, a rollback, a pod that will not start, a procedure | [Running it](running-it/README.md) is the intention table, and [every procedure](runbooks/README.md) is the shelf behind it |
| **Face a loss**: a file, a database, a node, the whole cluster | [Backups](backup/README.md) — what leaves the house every night, what it would cost to lose the rest, and the page per scenario |
| **Check a fact**: is this hostname public, which chart do I copy, who can sign in where | [Reference](reference/README.md), where the tables are, each one dated and regenerable |

Two subjects are read before touching them rather than after: [Security](security/README.md) for what
keeps the place closed, and [the rules these pages obey](documentation-rules.md) for how a page is
written and edited.

Work is not on this list because it is not a page: the board at
[vikunja.kub.internal](https://vikunja.kub.internal) is the state of the work, and when a plan's checkboxes
and the board disagree, the board wins. [How to read a plan](agent-work.md) explains the folder, and
[the operator↔agent protocol](agent-protocol.md) how a card is written.

## The map

| Subject | Page | It answers |
|---|---|---|
| Nodes | [The machines](how-it-works/nodes.md) | which machines exist, what each is for, what a dead node takes with it |
| Storage | [Where the data lives](how-it-works/storage.md) | `local-path` against NFS, claim naming, what a lost disk costs |
| Backups | [Backups](backup/README.md) | what leaves the house nightly, what it would cost to lose, and the page for each loss |
| Identity | [Who gets in](how-it-works/identity.md) | authentik's job, the three groups, and how a route is protected |
| Monitoring | [What watches the cluster](how-it-works/observability.md) | metrics and logs, the dashboards, where alerts go, the known holes |
| Media | [The media pipeline](how-it-works/media.md) | from finding releases to playback, the storage paths, the two book apps |

Two pages finish the set: [Applications](how-it-works/applications.md) is the catalogue — every
application with its node, route and protection — and [The shape and why](how-it-works/the-shape-and-why.md)
explains what failed in 2026 and which constraints became the five rules above.

## The four kinds of page

Every page under `docs/` is one of four things, and the kind is in its opening blockquote. The kind
decides who may edit it and when — the rules are [documentation-rules.md](documentation-rules.md).

| Kind | What it is | How it changes |
|---|---|---|
| **Explanation** | how a part of the place works and why it is shaped that way — How it works, the two section openings | rewritten when the understanding changes, with its `Verified` date moved |
| **How-to** | a procedure to run: preconditions, commands, what success looks like, the rollback | proved by running it, then edited only by the change that alters the procedure |
| **Reference** | a table of current fact: hostnames, charts, metrics, Secrets, what is covered | regenerated from its source where it can be, re-read where it cannot |
| **Record** | what was true on a date: an audit, a decision, a completed cutover | never edited after the fact; a new record supersedes it |

A page that is none of the four is a page that has not decided what it is, and the check in
`hack/check-docs.sh` says so.

## Freshness

A page carries the date it was last verified and the files it was written from, and is re-verified
rather than guessed at. A record says so in its kind and is not edited afterwards: the audit set is
records, so read their date before trusting a claim about what is running. No page lists counts —
[Every day](running-it/every-day.md) asks the cluster instead.
