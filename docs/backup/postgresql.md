# PostgreSQL

> **In one line.** The shared instance is restored in two parts — `globals.sql` for the roles and
> grants, then the database's dump — and `globals.sql` goes first because a dump does not carry them.
>
> **Use it when** a database on the shared instance is damaged, or the whole instance is being rebuilt
> after `nas` lost its disk · **Verified** 2026-11-22 · **Kind:** how-to
>
> **Sources** `docs/runbooks/postgres-restore.md`, `docs/runbooks/restore.md` §1,
> `cluster/helm/backup/values.yaml`

**Destructive.** A restore replaces the target database: every table, row and index in it ends up as
the dump has it, and anything written since the dump is gone. Read the whole page before the first
command.

## Preconditions

- A dump no older than 14 days, from the `postgres-dumps` claim on `nas`, or from the `nodes-nas`
  repository when that claim is gone ([restore.md §1](../runbooks/restore.md#section-1-postgresql)).
- `kubectl` access to `databases`, and no dump job running — the claim is ReadWriteOnce, so a second
  writer on the same volume is the one thing that corrupts the process.
- The database name and its role: they are in the log of
  [postgres-add-database.md](../runbooks/postgres-add-database.md), which is the authority on the pair.
- `postgres:18.7` already on `nas`; the pods are pinned there because the claim is.

**If the dump is not there.** The producer is watched twice: `PostgresDumpStale` fires when
`databases/postgres-dump` has not succeeded in 26 hours, `PostgresDumpAbsent` when the CronJob is gone
or has never succeeded, and the nightly verifier fails naming the `postgres-dumps` row when the newest
`nodes-nas` snapshot holds no `*.dump` under it. Receive one of those and fix the producer before
restoring anything: a dump no older than 14 days is the precondition above, and the retention clock is
already running.

## The order

1. **`globals.sql`.** Roles and grants are cluster-wide objects, not part of a database dump. On a
   rebuilt instance they are missing, and every restore after this step fails on ownership until they
   exist.
2. **The database's dump**, with `pg_restore` pointed at the target database — a scratch instance for a
   drill, the live instance for a real restore.
3. **The check.** Row counts against the numbers in the drill log, and the application's own page
   listing what the database should hold.

**Rollback.** There is none for the target database: once `pg_restore` runs against it, what it held
is gone. That is why the drill restores into a scratch instance and reads production without writing
to it. On a rebuilt instance the rollback is the claim itself — the old data directory, still on
`nas` until something deletes it.

The commands for each step, the scratch instance and its teardown are
[postgres-restore.md](../runbooks/postgres-restore.md). This page is the order and the reasoning; that
runbook is the sequence, and it is the one to follow with a terminal open.

## authentik is the exception to routine

Restoring authentik's database is not a routine restore: it is the identity provider, so the sign-in
page everyone uses is inside the database you are replacing. Do it from a session that does not need
authentik to start, expect every session to end, and check the groups after the restore — membership is
an access decision, and a dump restores the membership of the dump's date.

What must be true before it starts is [the critical points](../security/critical-points.md); the
recovery of the whole cluster when this is not enough is
[when the cluster is gone](../runbooks/last-resort.md).
