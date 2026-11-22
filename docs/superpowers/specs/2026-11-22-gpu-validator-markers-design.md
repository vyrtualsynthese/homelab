# GPU allocatable drops to 0 after a `streaming` restart — design

Date: 2026-11-22 · Card: homelab #575 (`#417`) · Kind: small change, short design

## Intent

Make the state visible and the recovery findable: when `streaming` restarts, the node silently stops
advertising `nvidia.com/gpu`, and nothing notices because no workload asks for it and Plex uses the
card through `runtimeClassName`.

## Why this and not a fix

The GPU Operator validator writes its readiness markers to `/run/nvidia/validations`, a tmpfs. A k3s
restart clears it; the validator pod is not recreated, so its init containers do not run again, and the
device plugin's gate waits forever for `toolkit-ready`. The node keeps reporting `nvidia.com/gpu: 0`
while every GPU pod reads `Running`.

The marker path is hardcoded: `/run/nvidia/validations` is a constant in the validator, and the device
plugin's gate hardcodes the same path. The validator reads an `OUTPUT_DIR` environment override, but
moving only the writer would strand the gate, so it is unusable. There is no chart setting for either
side.

## Approaches considered

| Approach | Trade-off | Verdict |
|---|---|---|
| **A — durable markers**: host bind mount `/run/nvidia/validations` → `/var/lib/nvidia/validations` in a systemd unit | removes the cause, but adds host-side boot ordering that exists on one machine and not in git, next to the hand-installed driver | rejected |
| **B — accept the workaround, add the alert** | GPU stays 0 after a restart until someone runs the documented pod delete; the alert says so within ten minutes | **chosen** |
| **C — B plus a watchdog CronJob** that deletes the validator pod when allocatable reads 0 | automatic recovery, but adds a ServiceAccount with pod-delete rights, another pinned image, and a loop that can fight a genuine driver repair | rejected |

The operator picked B on 2026-11-22.

## The rule

Grafana-managed, as every rule in this cluster is (Prometheus here holds no rules). Name
`GpuNotAllocatable`, group `homelab-core`, `for: 10m`, severity `warning`.

```promql
1 - kube_node_status_allocatable{resource="nvidia_com_gpu"}
```

- The expression returns `1` when the node advertises zero, `0` when healthy, and no series on a node
  that never had the resource. The condition is "above 0" like every other rule here; writing the
  metric as `== 0` would return the value `0` and could never fire — the trap recorded in
  `docs/reference/alerts.md`.
- The resource label is sanitized (`nvidia_com_gpu`), not the `nvidia.com/gpu` printed by `kubectl`.
- The series exists only on a node whose device plugin has registered the card, so the expression is
  self-gating; no join is needed. `kube_node_labels` is dropped by Prometheus's drop rules, and
  `kube_node_info` carries no custom node labels, so the `nvidia.com/gpu.present` join in the card is
  not available.
- Evidence: during the 2026-11-22 failure the series stayed present and read `0` from 00:31 to 01:06
  in Prometheus, so the shape catches the state rather than the series disappearing.
- `noDataState: OK` and `execErrState: OK`: a healthy cluster still returns the series (value 1), and a
  dead Prometheus is `MonitoringDown`'s job, not this rule's.
- The annotation names the node and the command:
  `kubectl -n gpu-operator delete pod -l app=nvidia-operator-validator`, then the allocatable check.

## Documentation

- `docs/runbooks/gpu-operator.md`: a section for the state — cause, detection, the workaround, and why
  it is accepted; correct the "what a node restart does" paragraph, which currently implies every GPU
  pod coming back is enough.
- `docs/runbooks/k3s-upgrade.md`: the `streaming` verification gains
  `kubectl get node streaming -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'` with the expected
  `1`, and the "GPU survived" paragraph says what it really showed.
- `docs/reference/alerts.md`: one row, and the counts move from 42 rules / 23 in `homelab-core` to
  43 / 24.
- `CHANGELOG.md`: one `Added` entry.

## Non-goals

No host change, no chart change, no kube-state-metrics label allowlist, no watchdog, no change to the
GPU Operator pin.

## What is left to the builder

The exact wording of the runbook section, and whether the alert's description repeats the whole
workaround or points at the runbook — it will point at the runbook and name the command.

## Verification

- Datasource query returns one series with value `1` now, and the rule reads back Normal with no error.
- `POST /api/v1/eval` with the rule's payload proves the condition pipeline evaluates without sending a
  notification.
- The docs pages render, `mkdocs.yml` needs no change (no new page), and the repository's checker suite
  passes.
