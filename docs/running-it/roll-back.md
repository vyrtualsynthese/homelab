# Roll back a change

> **In one line.** Three things get called an undo — a revert in git, a rollback in Argo CD, a pod
> restart — and only the first one holds: the other two act on the cluster, and the next sync undoes
> them. None of them puts data back.
>
> **Use it when** a change you merged went wrong, a pod is wedged, or you are about to delete a claim
> to make an application start · **Verified** 2026-11-17 · **Kind:** living page

## Three different undo buttons

**`git revert` of the commit.** A new commit undoing an old one, on a branch, through a pull request,
into `develop`. It changes git, then the cluster once the Application syncs
([gitops.md](../how-it-works/gitops.md)). The bad commit stays in the history with its undo beside
it, so the next reader sees what was withdrawn — reverting the revert restores it. It touches no
volume: what the newer version wrote into a claim stays.

**Argo CD's history and rollback.** Each Application records the revisions it synced, and
**History → Rollback** re-applies an earlier one to the cluster. It changes the cluster only: the
next sync, automatic or pressed, ships the bad state again, and it cannot reach a revision Argo CD
never deployed. It buys the minutes before the revert lands.

**A pod restart.** `kubectl -n <ns> delete pod <name>`, or **Restart** in the interface. Nothing is
rolled back: the pod comes back on the same image, the same template and the same claim, with a fresh
read of the Secret — which is what makes a rotated value take effect. Deleting the pod leaves no
diff; `kubectl rollout restart` stamps an annotation the next sync removes, rolling twice.

## Which one to use

| Situation | Do | Why |
|---|---|---|
| a bumped image crash-loops | revert the bump in git; an Argo CD rollback only stops the bleeding | rollback buys time, revert holds |
| a setting, memory limit or environment value is wrong | revert it in git | nothing at stake |
| a route, hostname or access gate broke | revert it in git | no pod rolls |
| the pod is wedged, git unchanged | delete the pod — unless its volume is borrowed ([status.md](../status.md)) | a restart is the whole fix |
| `OutOfSync` with no new revision | nothing here: the auto-sync switch is off ([ship-a-change.md](ship-a-change.md#if-the-sync-stops)) | no revision to undo |

The safe default is the first row: `git revert`, merge, sync. The others buy time for the revert, or
are not rollbacks.

## What a rollback does to a claim

A rollback rolls the template, never the data. The claim mounts again with every file the newer
version left on it, schema migrations included, and a step back across a major version often refuses
to start on its own database. Recovering the old configuration is not this tool's job; no copy
exists.

`strategy: Recreate` stops the old pod before the new one starts, so the volume is never open twice —
a rollback has a stop in it. On a borrowed volume that stop ends the application
([status.md](../status.md)).

Two timing traps ride with the template: a revision that ran as a different UID finds its
`/config` unreadable ([the UID trap](../charts/README.md#changing-the-uid-breaks-the-config-volume)),
and a claim that does not exist yet binds wherever the first pod lands, so an older pin can weld it
to the wrong machine ([the binding trap](../how-it-works/storage.md#the-binding-trap)).

A claim's size only grows: a rollback asking for less than it holds is rejected, and the sync fails.

## The one that destroys data

Four actions end an application's configuration for good: `kubectl delete pvc`, `kubectl delete
namespace`, `kubectl delete pv`, and clearing the finalizers on a claim stuck `Terminating`.

The first two leave the next pod on an empty directory: `local-path` uses the `Retain`
policy, so the directory survives on the node, but nothing rebinds it — and deleting that leftover
`Released` volume erases it. A namespace takes every claim inside it, a database's too.

Clearing a finalizer is worse: it looks like tidying. The driver that would clear those
claims was Longhorn and it is gone, so the finalizer alone holds the volume attached to a running
pod; clearing it destroys that volume under a serving application. [status.md](../status.md) names
the ones to leave alone.

Instead: leave it alone. Scale the application to zero if it must stop, fix the template in git, and
treat "an empty claim would let it start" as a data decision. When a claim really must go, retire the
application in git first, so the next sync does not recreate it. Copy the volume out with a throwaway
pod first — no backup of this cluster works
([storage.md](../how-it-works/storage.md#backups)).
