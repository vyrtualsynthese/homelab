# Retire the `node-type` label for good — design

Card: #638 (Vikunja `homelab`, task, `priority/low`).

## Intent

`AGENTS.md` §4 says the `node-type` label is retired and must not be reintroduced. The rule is
about charts, and it has held; the machines are the loose end. All four nodes still carry the label
(`nas=nas`, `nuc0=general`, `rbp2=small`, `streaming=computing`), and one chart still depends on it,
so several pages say "retired" while a copied affinity would still schedule somewhere real.

The goal is one meaning for "retired": the label does not exist, no chart asks for it, and a chart
that asks for it fails at scheduling instead of quietly landing on `nuc0`.

## Goals

- Remove `node-type` from the four nodes.
- Remove the last in-repo consumer, `cluster/helm/monitoring/pihole-exporter`, without moving its pod
  off `nuc0`.
- Make the pages state what is true: the label existed on the machines until 2026-11-22, some charts
  asked for it, and it was removed rather than merely discouraged.

## Non-goals

- Rebuilding `pihole-exporter` against the chart contract (`docs/charts/README.md`). Its boilerplate
  `Chart.yaml` and other defects stay; this change touches placement only.
- Adding anything to git that manages node labels. Nothing in the repo ever created them, and a
  label nobody should use does not deserve a reconciler.
- Re-litigating `docs/charts/README.md:231` (the lidarr claim that bound to `nuc0` because the old pod
  made it eligible). That sentence is accurate history and stays.
- Touching `AGENTS.md` §4. The rule already says what this change makes true.

## Constraints

- `pihole-exporter` must be fixed **and deployed** before the labels are removed, or its pod goes
  `Pending` on the next restart: its affinity is `requiredDuringSchedulingIgnoredDuringExecution`.
- Node labels are not in git and cannot be; the removal is four `kubectl label node <n> node-type-`
  calls with the admin kubeconfig. It is recorded in the card and the report, not in a manifest.
- ArgoCD auto-sync is enabled per Application in the UI, not in the Application file. The exporter
  syncs on merge if that flag is still set, and the removal waits on the pod reading `nuc0` again
  through a `nodeSelector`.

## Approaches considered

1. **Remove the label, fix the chart, correct the pages.** (chosen) Leaves no trap: the label cannot
   be selected because it does not exist, and the pages describe one state rather than two.
   Costs a manual cluster step that git cannot record.
2. **Keep the label, correct the pages.** No cluster change, but every future reader of those pages
   needs the caveat "it exists, do not use it", and the exporter keeps violating the rule.
3. **Keep the label, fix only the exporter.** Docs still carry the contradiction the card describes;
   the trap survives for anyone copying an old chart.

## The change

1. `cluster/helm/monitoring/pihole-exporter/values.yaml`: drop the `affinity` block, set
   `nodeSelector: kubernetes.io/hostname: nuc0`. The deployment template already renders both, so no
   template edit is needed.
2. `docs/how-it-works/nodes.md`: the paragraph that says the label still constrains scheduling is
   rewritten to record the removal and the date.
3. `docs/how-it-works/applications.md`: the "older charts still select the retired label" section
   becomes a statement that none does, with `pihole-exporter` named as the last one, fixed on
   2026-11-22.
4. `docs/reference/chart-notes.md`: the two "matched no node at all" sentences (koillection,
   syncthing) are corrected — the affinity asked for `general`, `nuc0` answered it, and the pods
   those notes describe were stuck on claims their charts never created. The thelounge mention was
   already neutral (`is replaced by nodeSelector on nuc0`) and stays; the freshrss and dashy mentions
   are read and corrected only where they repeat the false claim.
5. Chart comments and READMEs that repeat "matched no node" (`syncthing`, `koillection`) are corrected
   the same way. Comments that only say "the old affinity is gone" stay.
6. After the merge and the exporter's resync: remove the label from the four nodes, then verify
   `kubectl get nodes -L node-type` prints nothing and the exporter pod is `Running` on `nuc0`.

## What is left to the builder

- Whether the corrected sentences keep the historical detail (which pods were stuck, for how long) or
  compress to one line. Keep the detail: it is the evidence that the affinity was never the cause.
- Whether `pihole-exporter` also gets its memory limit raised or its `pullPolicy` changed. Not this
  card; file separately if the values look wrong on the way past.
