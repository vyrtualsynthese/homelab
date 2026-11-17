# Every day: the five-minute check

> **In one line.** Four shell commands, three dashboards and a comparison against the dated status
> page: the whole daily sweep, short enough to repeat.
>
> **Use it when** you sit down to the cluster and want to know whether tonight needs work ·
> **Verified** 2026-11-17 · **Kind:** living page

## Five minutes

The commands answer "does the cluster agree with git, and is anything not running". Run them in this
order; each narrows the last.

```bash
kubectl get applications -n argocd \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'

kubectl get pods -A --no-headers | awk '$4!="Running" && $4!="Completed"'

kubectl get deploy,statefulset,daemonset -A
kubectl get pvc -A   # the `Terminating` claims are expected
```

Then the dashboards, from anything on fire, to whose logs are noisy, to the hardware underneath:

| Order | Dashboard | What you are looking for |
|---|---|---|
| 1 | [Homelab · Health](https://grafana.kub.local/d/homelab-health) | the four counts, then the firing list: only the accepted faults below, and nothing to do |
| 2 | [Homelab · Logs](https://grafana.kub.local/d/homelab-logs) | errors and warnings by container — an unfamiliar container name is the day's clue |
| 3 | [Homelab · Disks](https://grafana.kub.local/d/homelab-disks) | life and temperature per drive; one is at its end, and a second going is the day's news |

Read [the status page](../status.md) too: its date decides whether its lists still describe the
cluster.

## What normal looks like

Normal is not silence — this cluster has known faults, and they stay visible. Normal is that nothing
has **changed**:

- Every `Application` is `Synced` and `Healthy`, apart from the drift set, the chart-path errors and
  the broken list: all named on the status page, so the only question is whether the membership
  changed.
- The pod filter prints rows. A row whose application is already on the status page is a fault you
  already own, not one you just found.
- A few provisioners sit at `Progressing` while serving their claims: ArgoCD's reading of a
  provisioner, not a fault.
- On Health, everything firing is already known — an accepted fault below or a broken application the
  status page names. On Disks, every other drive has life left.

Watch the delta: a row that was not there last time. Compare against
[the status page](../status.md), never memory; when its date is older than the last merge on
`develop`, distrust the page before arguing with the cluster.

## The accepted faults

Each is decided, dated and written down. Meeting it again is not a finding; read the record first.

- **`streaming` runs hot.** Its CPU sits at its thermal ceiling, the temperature alert fires on
  purpose, and the cooling fix is deferred:
  [the decision](../decisions/2026-11-13-streaming-overheating-deferred.md).
- **`nuc0` throttles.** The case is too small for its CPU, the condition is accepted, and the rule
  is silenced for `nuc0` on purpose:
  [the decision](../decisions/2026-11-13-nuc0-throttling-accepted.md).
- **The `nas` SSD is out of life.** It holds the `nas`-pinned configuration claims, including the
  monitoring stack's, and waits on a replacement:
  [the readings](../observability-conventions.md#dashboards).
- **qBittorrent's Web UI stalls during downloads.** The UI and its API stop answering for an hour or
  two, then clear; the download itself never stops:
  [the evidence](../qbittorrent-webui-stalls.md).

## When something is red

Three steps, in this order:

1. **The alert.** It arrives in Discord; a critical also opens a card in the board's `alerts`
   project. The rule name and labels name the object, not the cause.
2. **The dashboard.** Health confirms the state,
   [Applications](https://grafana.kub.local/d/homelab-applications) narrows to one namespace, Logs
   carries the container's own words. A rule is coarser than its fault: `PodPending` covers a claim
   that cannot bind and a container that cannot start.
3. **The board.** One card for the fault, in the project the work belongs to, in `Backlog`: what the
   alert said, what the dashboard showed, what the logs said. Update an existing card rather than
   duplicating it; the relay's `alerts` card is not the investigation.

Under all three: the board is where state lives. A fault fixed in a shell with no card returns
unrecorded.

## What not to do

- **Never `kubectl edit` or `kubectl apply` a GitOps-managed object.** Self-heal or the next sync
  reverts it, and the change vanishes with no record of why. Edit the chart, commit, let the
  Application pick it up.
- **Never restart a pod whose volume is borrowed, nor clear a `Terminating` claim's finalizers.** The
  driver that holds the claim is gone, so a restart can lose the volume and its configuration, and
  clearing the finalizers is the same loss done on purpose.
  [The status page](../status.md) names the apps to leave alone.
- **Never silence a new alert to clear red.** A silence suppresses the notification only, with an
  expiry and a comment; the rule goes on firing. It is a decision, not a broom.
