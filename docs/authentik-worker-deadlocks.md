# authentik's worker stalls when two tasks touch the permission tables at once

Status: **fixed in `cluster/helm/authentik/values.yaml` on 2026-11-20**, not yet re-observed under a
rollout. Recorded from the incident of 2026-11-19 20:53–21:28 UTC, during the FreshRSS sign-in
cutover ([PR #1287](https://github.com/vyrtualsynthese/homelab/pull/1287)).

## What was seen

- Blueprint applies stopped finishing. `apply_blueprint` for `ashulab-app-freshrss` sat `running`
  from 21:18:07Z, and the queued count grew (225 → 231 in 25 s) instead of draining.
- `outpost_send_update` tasks also stuck on the two slowest steps, `build_user_permissions` and
  `remove_all_perms_from_managed_role`. Four task slots were `running` at once.
- Postgres logged `deadlock detected` on `guardian_roleobjectpermission` every one or two minutes,
  one backend blocking others for minutes.
- Restarting the worker pod cleared it: the first card was written around 22:25 CET and the queue
  processed normally again after the restart.

## The measurement

Prometheus (pod count, memory), Loki (postgres log, worker log), 2026-11-19 UTC:

| Observation | Value |
|---|---|
| Deadlocks in the `authentik` database | **19**, all between 20:53:51 and 21:28:11 |
| Statement pairs in each deadlock | `DELETE … guardian_roleobjectpermission` against `DELETE … guardian_rolemodelpermission`, each waiting on a row the other held |
| Statements slower than 10 s that day | 49: 28 `DELETE guardian_rolemodelpermission`, 10 `UPDATE authentik_core_group`, 8 `SELECT authentik_core_provider` (and 3 cache rows) |
| Longest single statement | **133 s**, `UPDATE "authentik_core_group" …`, at 21:00:11 |
| Worker pods | 2 — and once 3 — during **every** rollout: 20:49–20:54, 21:06–21:10, 21:19–21:24, 22:03–22:08, 22:09–22:13, 22:44–22:50, 23:16–23:20, 23:22–23:26, 23:31–23:35 |
| Worker memory | peak working set 313 MiB against a 1 GiB limit, no OOM kill, no crash loop |
| Longest task at the time | `apply_blueprint` on `ashulab-app-freshrss` 538 s, on `ashulab-outpost-embedded` 1679 s |

The count of worker pods is the important row: the two-pod windows line up with the deadlock
minutes, including the one the card was written in (21:19–21:24, when four task slots were `running`
because two pods × two threads each).

## What it is not

- **Not memory.** The peak was under a third of the limit, and no container was ever OOM-killed.
- **Not a crash loop.** Those pods were replacements created by a rollout, not restarts of one pod.
- **Not the database's health.** Postgres answered throughout; it was the deadlock detector doing
  its job of aborting one of two transactions that each held a row the other wanted.

## Why it happens

`guardian` is authentik's permission table: one row per role-on-object grant, and one per
role-on-model. Two different tasks write it, and each reconciliation ends by deleting the rows it no
longer wants (`DELETE … WHERE id IN (…)`) and rewriting the group that owns them. Two of those at the
same time take the same rows in opposite order, and Postgres resolves the cycle by killing one.

The killed task fails with `deadlock detected`, the task queue (dramatiq) retries it twenty-some
seconds later, and the retry collides with whatever else is writing — so the storm feeds itself. Every
`outpost_send_update` rebuilds the permissions of every user, which is why that task, not the
blueprint, is the one that usually loses.

The queue then grows rather than drains for a second reason, in upstream code this repository cannot
reach: blueprint discovery re-runs every minute and re-enqueues any blueprint whose
`last_applied_hash` differs from the file. That hash is written only **after a successful apply**
(`authentik/blueprints/v1/tasks.py`, `check_blueprint_v1_file`), so a blueprint that is running,
queued or failing is enqueued again on every pass. There is no "already pending" guard.

Two workers each running two tasks made both halves of that worse. On its own, one worker with two
threads was enough — the deadlocks at 21:12, 21:13 and 21:16 happened while only one pod was up.

## What changed

Two settings in `cluster/helm/authentik/values.yaml`, nothing else:

- `authentik.worker.env`: `AUTHENTIK_WORKER__THREADS: "1"`, so the worker runs one task at a time
  instead of the upstream default of two.
- `authentik.worker.deploymentStrategy: {type: Recreate}`, so a rollout stops the old worker before
  starting the new one. Rolling upgrades kept both alive for four or five minutes, which put a second
  writer on the same tables for exactly the length of every rollout.

Together the worker is the only writer on those tables, and it writes one task at a time. The cost is
that a slow task now blocks every other task instead of half of them; the queue is short enough
(a few seconds per task when idle) that this has not mattered, and `System → Tasks` in authentik
shows the backlog if it ever does.

## If it happens again

1. Restart the worker: `kubectl -n authentik rollout restart deployment/authentik-worker`. Stuck
   `running` rows belong to the process that died; they are retried or cleared as the queue drains.
2. Watch the queue settle in authentik under **System → Tasks**, or read it with the API:
   `GET /api/v3/tasks/tasks/?search=apply_blueprint` (`AUTHENTIK_TOKEN` in
   `~/.config/secrets/homelab.env`).
3. If the deadlocks come back with `AUTHENTIK_WORKER__THREADS=1` in force, the second writer is not
   the worker. Look at who issued the statement in the postgres log before changing anything else —
   a permission rebuild started from the web UI or the API runs in the server, not the worker.

Do not scale the worker up, and do not put the strategy back to `RollingUpdate`: both restore the
second writer.

## What is still open

- **The re-enqueue on every pass is upstream.** A blueprint whose apply is slow will still queue a
  duplicate every minute until one succeeds. Serialising the worker makes the duplicates harmless
  (they apply against an unchanged state and finish in seconds) rather than fixing the cause.
- **`UPDATE authentik_core_group` took 133 s under the old concurrency.** It no longer has a second
  writer to wait on at worker level, but a permission change made from the UI while a task rebuilds
  permissions is still two writers. Nothing in this repository prevents that; it is worth watching
  the same log line if the stall ever returns.
