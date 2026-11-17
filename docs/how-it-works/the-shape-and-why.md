# Why the homelab looks like this

> **In one line.** The cluster is deliberately one machine deep: in 2026 it lost its storage layer,
> and the shape it has now is the answer to that failure, not a temporary state.
>
> **Use it when** a choice looks odd and you want the reason for it, or before you change something
> decided on purpose · **Verified** 2026-11-17 · **Kind:** living page

## What failed, in one paragraph

In 2026 the cluster's storage layer, Longhorn, failed. It provided **replicated storage**: the same
volume on more than one machine, copied by software so that losing a node would not lose data. The
failure took every volume it held and left claims that cannot be deleted, because the software to
clear them is gone. Three gaps around the storage turned one failure into a lost cluster: no backup
anyone had restored, no alerting that would have noticed stuck pods, and no document saying what the
machines were or what should run on them. The inventory, the alerting and the placement rule wrote
down what was missing; the backup gap is still open. The dated evidence is in
[`audit/observability-storage.md`](../audit/observability-storage.md); current counts are in
[`status.md`](../status.md).

## The four rules that follow

### No replicated storage

Configuration volumes use `local-path`, a directory on one node's own disk; media lives on NFS from
the NAS, so there is one copy. Replication added a component that could fail on its own, and it did.
Copies are the backup's job, not yet done — see below.

### Every pod pinned to a named node

Every workload declares `nodeSelector: kubernetes.io/hostname: <node>`. A `local-path` volume sits on
one node's disk, so the pod that reads it must run there; otherwise a pod would pair with a volume on
another machine; placement is a written rule. The map of which node does what is the design's
[§8.1](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#81-placement-map).

### Applications arrive through GitOps

Every application the cluster runs is described here and reconciled by ArgoCD. A change is a commit,
a pull request and a merge; `kubectl edit` or `apply` is undone by the next sync, so it is not a fix.
One exception is deliberate: auto-sync is a switch in the ArgoCD interface, not a line in git, so a
pull request that adds `syncPolicy.automated` is a regression — see
[§8.4](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#84-sync-policy).

### Free software and official images only

No proprietary licence, no Bitnami chart, no source-available-with-restrictions product. Images come
from the upstream project or LinuxServer; Docker Hub is avoided, with the official image kept where
no alternative exists. Nothing is built or maintained here: a licence that changes under you, or an
image nobody upstream will rebuild, is a dependency you cannot replace.

## What is accepted, not fixed

Known, written down, left alone: do not change one without a decision that replaces it.

- **No failover.** Losing a node's disk means losing every application pinned to it; the mitigation
  is a backup, not a second copy in the storage layer.
- **No backups yet.** The [audit's backup table](../audit/observability-storage.md#3-backups) shows
  none work. The design's offsite workstream (W4) owns the fix.
- **Alerting has one point of failure.** Grafana evaluates the rules and delivers them to Discord; if
  Grafana, Prometheus or `nas` is down, nothing is sent. The other holes are in
  [`observability-conventions.md`](../observability-conventions.md#known-gaps).
- **Heat.** `streaming` and `nuc0` both run at their thermal limit; the fix for each is hardware not
  yet bought. Both carry a silence on the shared temperature rule, so the condition stays visible without
  notifying: see
  [`streaming` overheating deferred](../decisions/2026-11-13-streaming-overheating-deferred.md) and
  [`nuc0` throttling accepted](../decisions/2026-11-13-nuc0-throttling-accepted.md).

## What was rejected

- **Replicated storage, including rebuilding Longhorn.** The recovery path was worse than no copies:
  nothing could be restored, and the leftovers cannot be deleted.
- **A wiki as the source of truth.** Markdown in git is what pull requests review and agents search;
  a wiki is at most a read-only mirror.
- **SOPS now.** Encrypted secrets in git need a decryption plugin in ArgoCD's render path, a change
  for after the upgrades; the accepted step is a secret inventory plus one encrypted dump kept with
  the backups.
- **A shared library chart.** Charts stay one per application with one agreed shape; a shared library
  ties every application's next upgrade to one template.

## Where the full argument lives

This page summarises; it is not the record. The argument, the alternatives and the risks are in the
design:

- [§6 Constraints](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#6-constraints):
  what the hardware and the licence policy rule out.
- [§7 Decisions taken](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#7-decisions-taken):
  every decision, with who made it.
- [§8 Cross-cutting conventions](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#8-cross-cutting-conventions):
  placement, storage, charts, sync policy, secrets, images, knowledge homes.
- [§13 Risks](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md#13-risks): each
  accepted risk with its mitigation.

The measurements behind them, dated 2026-11-10, are in the
[audit](../audit/observability-storage.md); audits are snapshots, not current state.
