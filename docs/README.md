# Documentation

Everything authoritative about this homelab lives here. If a document elsewhere in the repo
disagrees with one of these, these win.

## Read in this order

| Document | What it answers |
|---|---|
| [`audit/`](audit/) | What is actually true about the cluster and the repo, with evidence. Captured 2026-11-10 — a snapshot, not a live view. |
| [`superpowers/specs/2026-11-10-homelab-stabilisation-design.md`](superpowers/specs/2026-11-10-homelab-stabilisation-design.md) | Why the rebuild looks the way it does: intent, constraints, conventions, the nine workstreams and the risks. |
| [`superpowers/plans/2026-11-10-homelab-programme-index.md`](superpowers/plans/2026-11-10-homelab-programme-index.md) | The workstreams with estimates, dependencies and the operator's priority order. |
| [`superpowers/plans/`](superpowers/plans/) | One implementation plan per workstream, written to the command. |
| [`status.md`](status.md) | Which applications are repaired, which are working but not rebuilt, and which are broken — with the date it was checked. Start here before trusting any other page's "done". |
| [`runbooks/`](runbooks/) | Procedures to execute, produced as workstreams land. |
| [`observability-conventions.md`](observability-conventions.md) | The live monitoring stack: what runs, how to query it, the dashboards, the alert set and its known holes. Read before touching Grafana, Prometheus or Loki. |

## The audit set

| File | Scope |
|---|---|
| [`audit/observability-storage.md`](audit/observability-storage.md) | Prometheus, Grafana, Loki, exporters, Longhorn, storage classes, what the data loss took |
| [`audit/argocd-helm.md`](audit/argocd-helm.md) | Application reference integrity, sync policy, pattern classification, versions, ingress, secrets, decommissioned software |
| [`audit/ansible-hosts.md`](audit/ansible-hosts.md) | Playbooks, inventory, backups, runner templates, staleness, what a rebuild would not reproduce |

These are dated on purpose. They describe the cluster at one moment and will drift; a later
workstream supersedes them by producing a runbook, not by editing the audit. For the current state
instead, read [`status.md`](status.md).

## Faults that are tracked, not fixed

Known conditions that are accepted for now, each with a record rather than an alert nobody can act
on. Read the record before opening a new investigation into one of them.

| Fault | Record | How it is tracked |
|---|---|---|
| `streaming` runs at its thermal ceiling; the cooler needs money, not configuration | [`decisions/2026-11-13-streaming-overheating-deferred.md`](decisions/2026-11-13-streaming-overheating-deferred.md) | `NodeTemperatureHigh` fires; a time-boxed silence keeps the state visible without messaging |
| `nuc0` overheats and throttles by design of its chassis; replacement node is the fix | [`decisions/2026-11-13-nuc0-throttling-accepted.md`](decisions/2026-11-13-nuc0-throttling-accepted.md) | Document only, deliberately no alert |
| qBittorrent's Web UI stops answering during downloads | [`qbittorrent-webui-stalls.md`](qbittorrent-webui-stalls.md) | Measurements and the decisive test; no fix applied |

## Where each kind of knowledge lives

Set out in the design at §8.8, and worth repeating because it is the rule that stops the drift:

| Kind | Home |
|---|---|
| Decisions and designs | `docs/superpowers/specs/`, `docs/decisions/` |
| Evergreen knowledge: architecture, runbooks, secret inventory | `docs/` |
| Work state: what is being done, by whom, blocked, done | Vikunja only, once it exists |
| Agent recall: facts learned, preferences, past mistakes | OpenMemory |

No second todo list, anywhere. A task links to a decision; it does not contain one. Documentation is
files in git, never a wiki as the source of truth.

## Reading it comfortably

Markdown in git is the source. Material for MkDocs renders the same files into a browsable site with
navigation and search, and Mermaid diagrams render both there and on GitHub, so the repo is readable
with no build step.

## The deletion of 2026-11-11

`TODO.MD` and `REBUILD.md` were removed. Both described a plan that had been overtaken: `TODO.MD` was
a 15-session checklist with nothing ticked, and `REBUILD.md` carried figures the cluster contradicted
(31 self-healing Applications when the real number is near 20; 17 Pending pods when there were more;
11 PVCs described as safely bound when they were mid-deletion). Keeping them would have left three
documents disagreeing about the same machines — the failure mode this directory exists to prevent.
Their useful reasoning was carried into the design before deletion.
