# Documentation

> **In one line.** The start-here page for this homelab: what the machines are, how a change reaches
> them, and where the answer to your question already lives.
>
> **Use it when** you are new to the cluster, or you cannot remember which page owns a subject ·
> **Verified** 2026-11-17 · **Kind:** living page

## What this is

This is the documentation of one K3s homelab: four cluster nodes, two edge Pis, and the applications
they run. Everything authoritative about it lives here in `docs/`, and where a file elsewhere in the
repository disagrees with one of these pages, these pages win. The same files render as a site with
navigation and search at
[https://docs.kub.local/homelab/develop/](https://docs.kub.local/homelab/develop/).

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

## Choose a path

| I want to… | Start here |
|---|---|
| **Understand** the place | [The homelab in one page](how-it-works/README.md), then the topic pages it lists |
| **Fix something**, or see what is broken | [Running it](running-it/README.md) opens the page for each intention; [application status](status.md) is the dated snapshot of what is red |
| **Read a plan**, or file work of my own | [How to read a plan](agent-work.md) explains the folder and indexes it; the board at [vikunja.kub.local](https://vikunja.kub.local) is the state, and [the operator↔agent protocol](agent-protocol.md) says how a card is written |

The board is the state of the work: when a plan's checkboxes and the board disagree, the board wins.

## The map

| Subject | Page | It answers |
|---|---|---|
| Nodes | [The machines](how-it-works/nodes.md) | which machines exist, what each is for, what a dead node takes with it |
| Storage | [Where the data lives](how-it-works/storage.md) | `local-path` against NFS, claim naming, the honest state of backups |
| Identity | [Who gets in](how-it-works/identity.md) | authentik's job, the three groups, and how a route is protected |
| Monitoring | [What watches the cluster](how-it-works/observability.md) | metrics and logs, the dashboards, where alerts go, the known holes |
| Media | [The media pipeline](how-it-works/media.md) | from finding releases to playback, the storage paths, the two book apps |

Two pages finish the set: [Applications](how-it-works/applications.md) is the catalogue — every
application with its node, route and protection — and [The shape and why](how-it-works/the-shape-and-why.md)
explains what failed in 2026 and which constraints became the five rules above.

## Where each kind of knowledge lives

| Kind | Home |
|---|---|
| Evergreen knowledge: architecture, runbooks, secret inventory | `docs/` in this repository, and nowhere else |
| Work state: ideas, plans, what is being done, what awaits validation | the board, and nothing else — no second todo list |
| Designs and plans | `docs/superpowers/`: a spec argues, a plan executes, the pair carries its date |
| Agent recall: facts learned, preferences, past mistakes | OpenMemory |

Documentation is files in git, never a wiki as the source of truth. A task links to a decision; it does
not contain one.

## Freshness

A living page carries the date it was last verified in its first blockquote, and is re-verified rather
than guessed at. A snapshot says so in its opening line and is not edited afterwards: the audit set and
`status.md` are snapshots, so read their date before trusting a claim about what is running.
