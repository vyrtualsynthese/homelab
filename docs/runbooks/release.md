# Cutting a release

Date: 2026-11-26 · **Verified** — `v1.1.0`, the first release through the branch gate

> **In one line.** A release is two pull requests between protected branches — `develop` into `main`
> with a merge commit, then `main` back into `develop` the same way — and a tag with a written body
> on the first one.
>
> **Use it when** `develop` holds work that has to count as released, or the next release pull
> request shows hundreds of files it should not · **Verified** 2026-11-26 · **Kind:** how-to
>
> **Sources** pull requests #1623 and #1624, `docs/how-it-works/gitops.md`, the repository's branch
> protection and merge settings

## Why it is two pull requests

Neither permanent branch takes a push. Since 2026-11-26 `develop` requires a pull request, refuses a
force push and a deletion, and enforces that on administrators; `main` refused a force push and a
deletion already. The post-release step that used to run on a workstation —
`git checkout develop && git rebase main && git push --force` — is exactly what the gate rejects
(`GH006`), so the history `main` gains at a release has to come back to `develop` as a pull request.

| Step | Pull request or command | Merge method |
|---|---|---|
| 1. Release | `develop` → `main` | **Create a merge commit** |
| 2. Tag and body | `git tag -a vX.Y.Z`, GitHub release on that tag | — |
| 3. Sync back | `main` → `develop` | **Create a merge commit** |

`main` is therefore no longer a straight line: each release is one merge commit on it. Feature pull
requests still rebase onto `develop`, as [Ship a change](../running-it/ship-a-change.md) says; only
the two permanent branches meet by merge.

## 1. The release pull request

Run the checks on the tree that is about to ship, at the `develop` tip:

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
./hack/check-docs.sh --base origin/main
./hack/check-exposure.sh
./hack/check-charts.sh
./hack/check-app-version.sh
./hack/check-config-keys.sh
```

Then open `develop` → `main` and merge it with **Create a merge commit**.

- **Rebase merge is refused**: `405 This branch can't be rebased`. GitHub offers it only for a branch
  with no merge commits, and `develop` now carries the previous release's sync merge. Squash merge is
  off on the repository, and it would drop the individual commits, so merge commit is the only method.
- **`409 Head branch is out of date`** right after a sync merge is transient: GitHub recomputes the
  comparison for a few seconds. Re-read the pull request; it reads `clean` and merges.
- Check the release diff for a secret-shaped addition before merging:
  `git diff origin/main...origin/develop | grep -E '^\+.*(BEGIN .*PRIVATE KEY|ghp_|github_pat_|AKIA[0-9A-Z]{16})'`.

## 2. The tag and the release body

The tag and the release title share one version: `v1.1.0` ↔ `1.1.0`.

```bash
git tag -a vX.Y.Z -m "homelab vX.Y.Z: three or four things it changed" origin/main
git push origin vX.Y.Z
```

Create the GitHub release on that tag, let it generate the notes, **then rewrite the body**: group the
changes by area, drop the `Update …` dependency lines and the merge commits, and keep the sentences
human. The body is the release's only record — there is no `CHANGELOG.md`, by decision.

## 3. The sync back

Start with the local `main` current, or the comparison runs against the wrong base:

```bash
git fetch origin
git switch main && git merge --ff-only origin/main && git switch develop
```

That trap is not theoretical: on 2026-11-26 a checkout whose `main` still pointed at `7d21efa9` —
the `v1.0.0` tip, one release behind — replayed 190 commits instead of two, then stopped on five
Pi-hole conflicts that do not exist between the real branches.

```bash
git switch -c fix/release-sync origin/develop
git merge --no-ff origin/main
```

Resolve any conflict in `develop`'s favour, then prove that nothing moved before committing:

```bash
git cherry origin/develop origin/main     # every line marked '-' = already applied
git diff origin/develop HEAD              # empty = main added no content
```

Commit, push the branch, open the pull request against `develop`, and merge it with **Create a merge
commit** too — a rebase merge there would replay the release commits a second time.

A rebase merge does worse than replay: the sync merge has **no patch of its own**, so rebasing it
applies nothing. The pull request closes marked merged, `develop` does not move, and `main` stays out
of its history — which is what #1630 did on 2026-11-26, and how #1634 had to be reopened and merged
properly minutes later. Delete the branch afterwards: it is a normal feature branch, unlike the two
permanent ones.

Without this step the next release pull request carries every commit `main` holds that the root of
`develop` never saw, and reports hundreds of changed files as conflicting. That is the state that
forced #1624: 82 commits, 37 textual conflicts, and a merged tree identical to `develop`'s.

## The canonical checkout follows, it does not build

`~/projets/homelab` keeps `main` and `develop` checked out for review, and neither is a place to work:
a commit made there cannot be pushed, and a rebase there rewrites the branch into commits that no
longer match the remote. Keep both current by fast-forward only:

```bash
git fetch origin
git switch main && git merge --ff-only origin/main
git switch develop && git merge --ff-only origin/develop
```

If `develop` there reports itself ahead of the remote, the extra commits are replays, not work:
`git cherry origin/develop develop` marks each one `-` because its patch is already upstream, and
`git reset --hard origin/develop` discards them. On 2026-11-26 two rebases of that checkout left it
two commits ahead and eight behind while the content was already on the remote.

## What this depends on

Changing any row below breaks the flow, silently or at the next release.

| Setting | Value | Why |
|---|---|---|
| `allow_merge_commit` | `true` | the only method the flow uses |
| `allow_rebase_merge` | `true` | feature pull requests still rebase |
| `allow_squash_merge` | `false` | it would drop the individual commits |
| `delete_branch_on_merge` | `false` | a release pull request's head is `develop` itself; the setting would delete the integration branch when it merges |
| `develop` protection | pull request required, 0 approvals, no force push, no deletion, admins enforced | allows a self-merge and nothing else |
| `main` protection | no force push, no deletion, admins enforced | the tag has to stay reachable |

## History

- `v1.0.0`, 2026-11-25, tag on `7d21efa9` — cut before the gate existed, from #1540.
- `v1.1.0`, 2026-11-26, tag on `dc7ce8b8` — from #1623, after #1624 reconciled `main`'s history;
  152 files, +12,203 / −793.
