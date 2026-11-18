# Seven applications are retired, not repaired

Date: 2026-11-18
Decided by: operator
Status: active

## The decision

`firefly`, `ghostfolio`, `guacamole`, `linkstack`, `monitorss`, `postiz` and `wiki` are gone: their
charts, their ArgoCD `Application` files and their namespaces. They were never rebuilt to the chart
contract, every one of them had lost either its claim or its database in the 2026 Longhorn failure,
and none held data that still had a home. Repairing them would have meant rebuilding seven charts,
seven databases and seven claim sets to recover seven applications the operator does not need.

What went with them, so it is not discovered later:

| Gone | Was | Where it is now |
|---|---|---|
| `cluster/helm/{firefly,ghostfolio,guacamole,linkstack,monitorss,postiz,wiki}` | the seven charts, pattern A except `postiz` (pattern C) | git history, at the commit before the retirement branch |
| the seven `cluster/argocd/projects/*.yaml` Application files | how each chart reached the cluster | git history |
| the `wiki.ashuvidz.com` public host | the only public hostname of the seven | removed from `hack/check_exposure.py`'s allowlist, from `AGENTS.md` and from `traefik-gate`'s README; four public hosts remain |
| pattern C, the vendored upstream chart | `postiz` was its last user | the pattern is documented as historical; new external charts are pattern D |
| the `firefly` and `guacamole` rows of the sizing pass, the `monitorss` and `linkstack` rows of the verified set | sized charts | [`reference/chart-sizing.md`](../reference/chart-sizing.md) keeps the totals and says which rows left |
| the Grafana silence over `pod=~"ghostfolio-.*\|wiki-app-.*\|monitorss-bot-.*"` | a notification mute for three crash-looping pods | deleted through the Grafana API, since the pods no longer exist |

The teardown itself, for the next one: the seven `Application` objects carried **no**
`resources-finalizer.argocd.argoproj.io`, so deleting them would have left every workload running.
The Applications were deleted first, then the namespaces by hand. Four namespaces emptied on their
own; three hung on a pod whose Longhorn mount could never unmount — those pods were force-deleted,
then the five stuck `PersistentVolume`s had their `external-attacher/driver-longhorn-io` finalizer
patched away, as `AGENTS.md` §7.4 describes.

## Why

What each one was on 2026-11-18, the day of the retirement:

| Application | State | Why it could not be repaired cheaply |
|---|---|---|
| `firefly` | running, 3/3 | its database `firefly` does not exist on the rebuilt PostgreSQL and its `DB_HOST` still names `postgres-postgresql.databases.svc.cluster.local`, the retired Bitnami Service; its upload claim was stuck `Terminating` and held only `lost+found` |
| `linkstack` | running, 1/1 | the only copy of its state was a Longhorn claim stuck `Terminating` since the disk failure; a `latest` image on top |
| `guacamole` | running, 2/2 | its connection data lived in the same missing database; nothing on its own claim |
| `ghostfolio` | crash-loop, more than ten thousand restarts | same retired Bitnami hostname, and no `ghostfolio` database on the new instance |
| `wiki` | crash-loop, more than ten thousand restarts | same, `getaddrinfo ENOTFOUND` |
| `monitorss` | 0/2 | its Mongo pod was Pending on a claim that no longer exists; the bot then had no database |
| `postiz` | 0/1 | its uploads claim was gone, and its Redis claim was stuck `Terminating` |

Two facts decided it. First, the PostgreSQL instance rebuilt on 2026-11-13 holds `authentik`,
`vikunja` and `acceptance` — none of the seven databases was recreated, so the application data was
already lost before this record. Second, what the running pods still mounted was a Longhorn volume
whose driver is gone: the mount survives the pod, not the next restart. Three of the seven had in any
case never been rebuilt against the chart contract, so "make it work" also meant "write the chart
again".

The alternative — keeping seven broken Applications in git so the cluster keeps matching the repo —
was rejected: a status board that lists seven dead apps as broken hides the faults that can still be
fixed, and the public `wiki.ashuvidz.com` route was an ungated door onto a crash-looping pod.

## What it costs if it is wrong

The data is not recoverable. What was lost is the finance history in `firefly`, the wiki pages, the
saved connections in `guacamole`, the link page in `linkstack`, the feed subscriptions in
`monitorss`, the scheduled posts in `postiz`, and whatever `ghostfolio` held. Most of that was lost
when its database was: this record buries it, it does not cause it. The Longhorn volumes were already
unattachable and their contents were abandoned by decision on 2026-11-13.

Reinstating one means taking its chart back out of git history and rebuilding everything it stood
on: a role and a database on the shared PostgreSQL by
[`runbooks/postgres-add-database.md`](../runbooks/postgres-add-database.md), a Secret, a claim on
`local-path` rather than Longhorn, and a rebuilt chart. For `wiki` it also means asking for the public
hostname back. That is a day's work per application, which is the price this record accepted.
