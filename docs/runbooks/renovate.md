# Renovate: its token, a run, and the schedule

> **In one line.** Renovate is a nightly CronJob whose only credential is one GitHub token in Secret
> `renovate`; this page records what that token is, how to run the job by hand and read its log, and
> how to change the schedule.
>
> **Use it when** Renovate has not opened its usual pull requests, its token has to be rotated, or
> the nightly slot has to move · **Verified** 2026-11-19, application count re-checked 2026-11-23 ·
> **Kind:** how-to · **Sources** `.github/renovate.json`, `cluster/helm/renovate/`,
> `cluster/argocd/projects/`.

Status: **proved 2026-11-19** — the hand-triggered run described below is its first execution: Job
`renovate-manual-20261119-0114`, 18 minutes, exit code 0, no error line in 347.

## What it updates

Renovate reads `.github/renovate.json` and looks for pins with a set of managers. Each one owns a
different place where a version is written:

| Manager | What it reads | Scope, 2026-11-19 |
|---|---|---|
| `helm-values` | every `cluster/helm/**/values.yaml`: an `image:` block, and a single-string `image: repo:tag` | 79 images |
| custom regex, values | a `# renovate: datasource=… depName=…` line above a `version`, `appVersion`, `initTag`, `dockerTag` or `tag` key | qBittorrent's VueTorrent release, the Alpine base of its installer, and n8n's tag |
| custom regex, templates | a literal `image: repo:tag` under `cluster/helm/**/templates/**` | five init containers and Authentik's kubectl hook |
| `helmv3` | `dependencies:` in every `Chart.yaml` | 11 chart versions |
| `argocd` | `targetRevision:` on a `chart:` in the Applications | the NFS provisioner chart, in two Applications |
| `docker-compose` | the edge Pis' `docker-compose.yml` under `ansible/` | 9 images |
| `kubernetes` | `image:` in the hand-applied manifests under `kubectl/` and `cluster/infrastructure/` | one `latest` scratch pod, nothing to update; the Traefik snapshot is excluded |

Three of the rules in that file exist because of a failure, and they read as arbitrary without it:

- **LinuxServer tags.** The publisher writes the image build after the app version (`1.30.4-ls332`),
  and Docker's default versioning reads `-ls332` as a compatibility suffix. Renovate never crosses
  from one suffix to another, because that is how it avoids moving an `-alpine` image onto `-debian`.
  The effect was that every LinuxServer pin ending in `-ls<N>` was frozen at its release: Freshrss
  1.25 was blind to 1.30, and a Sonarr pin on `-ls4` was blind to `-ls329`. The `versioning` regex on those
  packages folds the build into the number (`1.30.4-ls332` is read as `1.30.4.332`), which is what
  makes those updates visible at all. The `-ls<N>` suffix is required rather than optional: the
  publisher keeps legacy base tags beside each release — `qbittorrent:20.04.1`, `lidarr:8.1.2135` —
  and the regex read those as majors 20 and 8, so a nightly pull request would have replaced 5.3.0
  with a 2020 Ubuntu image. Every real release carries the build suffix, so requiring it keeps the
  releases and drops the base tags, which is what the dashboard gate of 2026-11-19 used to hold back
  one tag at a time.
- **The repository's own `develop` reference is disabled.** All 60 Applications pin
  `targetRevision: develop` on this repository. There is no version to move to, and a tag written
  there would stop the GitOps sync, so renaming it a dependency only invented 60 rows on the
  dashboard.
- **n8n's tag is read from GitHub releases, not Docker Hub.** n8n publishes its prerelease builds to
  Docker Hub as ordinary version tags (`2.49.1`, `3.3.0`) and marks them as prereleases only in the
  GitHub release, so the docker datasource treated `3.3.0` as a normal major upgrade (pull request
  #1001). `cluster/helm/n8n/values.yaml` now carries a
  `# renovate: datasource=github-releases depName=n8n-io/n8n` annotation, the docker entry for
  `n8nio/n8n` is switched off in `packageRules` so the same pin is not read twice, and a second rule
  strips the `n8n@` prefix off the release tags (`extractVersion`). That last part is not optional:
  the default versioning reads the letters in `n8n@2.48.6` as the number 8, then judges every release
  a major jump and hides all of them. The lookup on 2026-11-19 offered `2.48.6`, the newest stable,
  and nothing from the `2.49.x` or `3.x` prerelease lines.

**`appVersion` does not move with the tag.** The docker manager owns `image.tag` in `values.yaml` and
reads nothing in `Chart.yaml` but `dependencies:`, so a version bump merges with `appVersion` behind
it. A second `# renovate:` annotation on `Chart.yaml` was rejected: it would extract the same pin
twice, and it would copy the packaging suffix (`-ls332`, `-distroless`) into `appVersion`, which the
[chart contract](../charts/README.md#how-appversion-relates-to-imagetag) says is the application's
own version. `hack/check-app-version.sh` is the guard instead: after merging an image bump, it fails
until `appVersion` names the version inside the new tag. Batch the repair rather than following each
pull request with a commit of its own: on 2026-11-25 twenty pull requests merged and eleven of them
had moved a tag, so one commit updated eleven `appVersion` lines and the guard went back to `OK`.

## The newest stable release wins, 2026-11-25

`separateMajorMinor` is off for the repository, so a package has one branch and that branch moves to
the newest stable release. When a major is out, the branch goes to that major and no patch branch for
the line it replaces opens beside it: with chart `43.0.0` and Traefik `3.8.0` published, the answer is
one pull request to `43.0.0`, not a `43` branch next to a `42.4.3` one. Prereleases stay out through
`ignoreUnstable`, which is on by default, so a major is only offered once it is a published stable
release.

Three rules narrow that for the packages where it does not fit:

- **PostgreSQL's major waits for a person.** A PostgreSQL major cannot run on the previous data
  directory: the image reads the version out of `PG_VERSION`, exits, and the pod crash-loops until
  the data has been migrated. A pull request to `19` is therefore a migration in a version bump's
  clothes — [`postgres-restore.md`](postgres-restore.md) is the procedure, and it is not something a
  merge button should start. Two `packageRules` entries on the `postgres` image do the work:
  `separateMajorMinor: true` keeps 18's patches flowing on their own branch, and
  `dependencyDashboardApproval: true` scoped to `major` puts 19 on the dependency dashboard under
  **Pending Approval** without creating a branch. A new major is learned from that entry, and a tick
  beside it is what creates the pull request; until then no PR exists to merge by accident.
- **Traefik groups the chart and the image it ships.** The wrapper chart in
  `cluster/helm/platform/traefik` pins both the chart dependency and the `image.tag` inside it, and
  each chart release carries the image that belongs with it: chart `43.0.0` ships Traefik `3.8.0`. A
  `groupName` puts the two on one branch, so the chart and the image move together instead of
  arriving as two pull requests that can drift apart.
- **The Traefik snapshot is outside Renovate.** `cluster/infrastructure/traefik/snapshot/` is a
  written record of what the old release looked like and nothing applies it, so an image bump there
  rewrites a record instead of changing the cluster. Pull requests #1514 and #1519 were exactly that.
  A `matchFileNames` rule disables every dependency under that path.


## Where the configuration lives, and when a change takes effect

`.github/renovate.json`, in this repository. Renovate reads it from the **default branch**, which is
`main`, not `develop`. An edit merged to `develop` changes nothing until the next `develop` → `main`
release lands; only then does the nightly run pick it up. The pull requests themselves still target
`develop`, because the file sets `baseBranchPatterns`.

Validate a change before committing it (this is the same Renovate version the cluster runs):

```bash
npx --yes --package renovate@44.252.7 renovate-config-validator .github/renovate.json
```

The validator catches a removed or renamed key and prints the migration for it. It does not catch a
`packageRules` entry that matches nothing, so a rule that stops working can fail silently: the
dependency dashboard issue is the place a coverage regression shows up as a pin that never moves.

To see what one pin would actually get, before the change reaches `main`, run a lookup against the
working copy. `newValue` in the output is what Renovate would write into the file:

```bash
RENOVATE_HOST_RULES="$(node -e 'console.log(JSON.stringify([{hostType:"github",matchHost:"api.github.com",token:process.env.GITHUB_PERSONAL_ACCESS_TOKEN}]))')" \
  npx --yes --package renovate@44.252.7 renovate --platform=local --dry-run=lookup \
  --include-paths='cluster/helm/n8n/values.yaml' --base-branch-patterns='.*'
```

The token is only needed for a dependency whose datasource is GitHub-hosted; the docker lookups run
without one.

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
`backoffLimit: 1` — a failed run is retried once, then left failed. A run takes 7 to 10 minutes on
the current image; runs on the previous one took up to 39.

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
| every open Renovate PR reads `dirty` with ~275 changed files, and Renovate does not rebase them | `develop` was force-pushed, so the branches still carry the history that no longer exists on the base | rebase each `renovate/*` branch onto `develop` by hand; see the 2026-11-25 note below |

## What a force-push does to the open branches, 2026-11-25

The history rewrite that let #1525 merge by hand (89 commits of `main` skipped as already applied,
113 replayed onto `main`) left every open Renovate branch based on the `develop` that no longer
existed. GitHub then reported each one `dirty` with about 275 changed files and 30 commits, and the
run that followed did not rebase them: `rebaseWhen: auto` acts on its own notion of a conflict, not
on GitHub's `mergeable_state`.

The repair is one local rebase per branch, which drops the dead history by patch identity and leaves
the single Renovate commit:

```bash
git fetch origin 'refs/heads/renovate/*:refs/remotes/origin/renovate/*'
for b in $(git branch -r --list 'origin/renovate/*' | sed 's#^ *origin/##'); do
  git switch --detach "origin/$b" -q &&
  git rebase origin/develop -q &&
  git push --force-with-lease="refs/heads/$b:origin/$b" origin "HEAD:refs/heads/$b"
done
```

Each branch ends as one commit over `develop` touching one file, and the pull requests go back to
`clean`. Six pull requests were closed instead of repaired, because the new configuration had already
replaced them: two against the Traefik snapshot, two Traefik branches the grouping subsumed, the
`42.4.3` chart patch and the `29.49.0` Prometheus minor, both of which the move to the newest stable
release had overtaken.

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

## The coverage rewrite, 2026-11-19

`.github/renovate.json` was rewritten that day and released to `main`; two hand-triggered runs then
proved it in the cluster:

```
job renovate-manual-20261119-0433: Complete, 1/1, exit 0, 7m27s — 16 pull requests opened (#1244–#1259)
job renovate-manual-20261119-0503: Complete, 1/1, exit 0, 9m38s — six blocked branches rebased, one commit each
```

The LinuxServer gate held: the dependency dashboard lists the major tags (lidarr v8, prowlarr v3,
qbittorrent v20) under **Pending Approval**, and no branch was created for them. `qbittorrent
20.04.1` also sits behind the closed pull request #615, so recreating that branch needs a checkbox
as well as an approval. On 2026-11-25 the gate was replaced by the required `-ls<N>` suffix, which
drops those base tags instead of parking them, so the table above is the record of what the gate did
while it existed.

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
housekeeping or the 04:30 database dump — a run takes 7 to 10 minutes.

**Expect one catch-up run when the slot moves.** The controller remembers the last scheduled time
and creates a Job for a slot it missed. Moving from `0 1 * * *` (UTC) to `0 3 * * *` (Europe/Paris)
on 2026-11-19 produced `renovate-29916120` with
`batch.kubernetes.io/cronjob-scheduled-timestamp: 2026-11-18T03:00:00+01:00` — yesterday's slot under
the new schedule. It is a one-off, but it runs beside whatever manual run you started, and two
Renovate processes race each other on the same branches: delete the catch-up and keep the run you
meant. [`secrets.md`](../secrets.md) and the chart's
[`README.md`](../../cluster/helm/renovate/README.md) describe the rest of the chart.
