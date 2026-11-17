# Audits: three reads of the cluster, taken on 2026-11-10

> **In one line.** On 2026-11-10 three read-only passes went over this homelab and wrote down
> what was true that day — the host layer, ArgoCD and Helm, and observability with storage. The
> findings are kept because they are the evidence behind the plans that fixed them.
>
> **Use it when** a plan or chart cites an audit, or you wonder why something was rebuilt ·
> **Read** 2026-11-10 · **Kind:** snapshot (2026-11-10)

These pages are **history**. Each one describes the cluster and the repository on a single day,
before the rebuild work started. None of them is maintained, and none of them is a statement
about the cluster now. Where an audit and the cluster disagree, the cluster is right.

For what is true today, read [What is broken right now](../status.md). That page carries the
date it was read and is refreshed when a workstream lands; these are the receipts it no longer
repeats.

A finding is never edited to record that it was fixed. The plans under `docs/superpowers/plans/`
are what change the cluster, and the audits stay as the record of what those plans were arguing
with. Some defects were fixed, some are still open, and some were overtaken by a different
decision — the audit reads the same in all three cases.

| Audit | What it covered |
|---|---|
| [Ansible and the host layer](ansible-hosts.md) | The playbooks and what they cannot rebuild from this repository: the missing inventory, the backup scripts that have not run since 2024, the runner templates, and the staleness of the host configuration |
| [ArgoCD and Helm drift](argocd-helm.md) | What ArgoCD actually manages, broken `path:` and `$values` references, the chart patterns and their versions, ingress, the secrets the charts expect, and the known chart defects |
| [Observability and storage](observability-storage.md) | The Prometheus, Grafana, Loki and exporter inventory as it stood, the abandoned Longhorn layer and what the data loss took, the NFS provisioners, and the backup story |

## How to read one

Each audit opens with a three-line banner making the same claim this page does, then numbers its
sections and ends with verdicts or decisions. The verdicts are the part worth reading twice:
they say which defect mattered, not only that it existed.

Findings quote `REBUILD.md` and `TODO.MD`, two files deleted from the repository on 2026-11-11.
Every audit's banner carries the commands that recover them: `git show 6be598ab:REBUILD.md` and
`git show 6be598ab:TODO.MD`.
Decisions taken after an audit are separate, dated records, in
[Decisions](../decisions/README.md).
