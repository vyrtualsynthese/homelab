# Renovate: its token, a run, and the schedule

> **In one line.** Renovate is a nightly CronJob whose only credential is one GitHub token in Secret
> `renovate`; this page records what that token is, how to run the job by hand and read its log, and
> how to change the schedule.
>
> **Use it when** Renovate has not opened its usual pull requests, its token has to be rotated, or
> the nightly slot has to move · **Verified** 2026-11-19 · **Kind:** living page

Status: **proved 2026-11-19** — the hand-triggered run described below is its first execution: Job
`renovate-manual-20261119-0114`, 18 minutes, exit code 0, no error line in 347.

## The token

| Property | Value |
|---|---|
| Secret | `renovate` in namespace `renovate` |
| Key | `RENOVATE_TOKEN` — the chart's `existingSecret` exports every key as an environment variable |
| Account | `vyrtualsynthese`; every pull request Renovate opens is authored by that account, not by a bot |
| Type | classic personal access token |
| Scopes | `repo` — the classic scope that covers branches, pull requests and issues. Nothing needs more: this repository has no `.github/workflows`, so the `workflow` scope buys nothing. The cluster cannot show whether extra scopes were also granted |
| Expiry | **none** — a classic token with no expiry date, confirmed by the operator on 2026-11-19 |
| Renewal date | none to record. Rotate it if the account is compromised or the operator next rotates their GitHub tokens |

The read-only cluster account cannot read Secrets, so the value is only ever seen by the operator.
[`secrets.md`](../secrets.md) lists the Secret; this table is the record of its type and scopes.

## A run, by hand

The scheduled run is `03:00 Europe/Paris` every day (`0 3 * * *`), under `Forbid` and with
`backoffLimit: 1` — a failed run is retried once, then left failed. A run has taken 18 to 39 minutes.

Trigger one now, without waiting for 03:00:

```bash
kubectl -n renovate create job --from=cronjob/renovate "renovate-manual-$(date +%Y%m%d-%H%M)"
kubectl -n renovate get jobs --sort-by=.metadata.creationTimestamp
```

Read the log of the pod the Job creates:

```bash
kubectl -n renovate logs "job/$(kubectl -n renovate get jobs --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{.items[-1].metadata.name}')" | tail -40
```

**What success looks like**, in this order:

1. The Job ends `Complete` and `1/1` — `kubectl -n renovate get jobs`.
2. The log shows `Repository started` once per repository and no line containing
   `Authentication failed`, `401` or `403`.
3. The log ends with `Renovate was run at log level "info"`; there is no stack trace.
4. In GitHub, the dependency dashboard issue of `vyrtualsynthese/homelab` has been refreshed, and any
   updatable pin has an open pull request from `vyrtualsynthese`.

Then remove the manual Job so the next nightly one is not confused with it:

```bash
kubectl -n renovate delete job "renovate-manual-<the stamp you used>"
```

An agent cannot do any of this: the `kubernetes` MCP account is read-only, so it can read the jobs
and the logs of past runs but cannot create or delete one. Ask the operator for the trigger and the
delete.

**When it fails:**

| Log signature | Cause | Fix |
|---|---|---|
| `Authentication failed`, `401`, `Bad credentials` | the token is wrong or was revoked | rotate it, below |
| `403` with `rate limit` | GitHub's hourly cap; a manual run after the nightly one can hit it | wait an hour; it is not a configuration fault |
| `Repository is disabled` for every repository | `renovate.json` is missing from that repository's default branch, or its `ignorePaths` no longer matches | check `.github/renovate.json` on `develop` |
| Job failed with no renovate output | the container never started — image pull, missing Secret | `kubectl -n renovate describe pod <pod>` |

## The first run, 2026-11-19

The procedure's first execution, run against the wrapper chart on image `44.251.0`:

```
job renovate-manual-20261119-0114: Complete, 1/1, exit 0, 18 minutes
15 repositories autodiscovered, 15 "Repository started" lines, 347 log lines
no "Authentication failed", 401, 403 or stack trace
homelab: argocd 56 files/59 deps, helm-values 56/79, helmv3 11/11
Dependency Dashboard refreshed at 00:30 UTC; four pull requests updated
```

`helmv3 11/11` is the check that matters for the change that preceded this run: the wrapper's
`Chart.yaml` dependency is now one of the eleven, so Renovate owns the chart version in the same file
the image tag lives in.

## Rotating the token

The pod reads environment variables at container start, so a patched Secret reaches the next run and
no running one.

1. On GitHub: Settings → Developer settings → Personal access tokens → the token with `repo` for
   `vyrtualsynthese`. Regenerate it and copy the value.
2. Replace the Secret; `create` fails if it exists, so go through `apply`:

   ```bash
   kubectl -n renovate create secret generic renovate \
     --from-literal=RENOVATE_TOKEN='<value>' --dry-run=client -o yaml | kubectl apply -f -
   ```

3. Trigger a run with the procedure above and confirm the four success conditions. A `200` from the
   apply proves nothing about the token.

Nothing else holds the value: there is no second copy in another namespace, and the Secret is
referenced by `existingSecret` only. If the token is a fine-grained one next time, the scopes become
`Contents: read and write`, `Pull requests: read and write` and `Issues: read and write` on the
repositories the account can see.

## Changing the schedule

The schedule, the timezone and the retry policy are values, not template edits:

```yaml
# cluster/helm/renovate/values.yaml
renovate:
  cronjob:
    schedule: "0 3 * * *"
    timeZone: Europe/Paris
```

Keep the timezone explicit: the CronJob controller runs in UTC, so an unqualified schedule silently
moves by an hour when the offset changes. Pick a slot that does not overlap the *arrs' midnight
housekeeping or the 04:30 database dump — a run has taken 18 to 39 minutes.

**Expect one catch-up run when the slot moves.** The controller remembers the last scheduled time
and creates a Job for a slot it missed. Moving from `0 1 * * *` (UTC) to `0 3 * * *` (Europe/Paris)
on 2026-11-19 produced `renovate-29916120` with
`batch.kubernetes.io/cronjob-scheduled-timestamp: 2026-11-18T03:00:00+01:00` — yesterday's slot under
the new schedule. It is a one-off, but it runs beside whatever manual run you started, and two
Renovate processes race each other on the same branches: delete the catch-up and keep the run you
meant. [`secrets.md`](../secrets.md) and the chart's
[`README.md`](../../cluster/helm/renovate/README.md) describe the rest of the chart.
