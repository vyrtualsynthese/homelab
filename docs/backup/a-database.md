# A database inside an application

> **In one line.** A `sqlite` row is copied through SQLite's own backup API into `/staging/`, and that
> staged copy is the one to put back — the live file may have been half-written when the job read it.
>
> **Use it when** an application starts with an empty library, or its database file is corrupt, and its
> configuration otherwise still exists · **Verified** 2026-11-22 · **Kind:** how-to
>
> **Sources** `docs/runbooks/restore.md` §2, `cluster/helm/backup/values.yaml`

**Preconditions.** The restic password from `homelab-backup-last-resort`, the repository of the node
the claim lives on (`nodes-streaming` for the media stack, `nodes-nuc0` for the general catalogue), and
the restic shell from [Step 0](../runbooks/restore.md#step-0-a-restic-shell-in-the-cluster).

## The staged copy is the one

Restore both halves in one command — the database from `/staging/`, the rest of the configuration from
the claim's host path — because the row wrote them into the same snapshot:

```sh
restic -r "$NODES_STREAMING" restore latest --target /out \
  --include '/staging/claims/media-stack/sonarr-config' \
  --include '/mnt/ssd0/pvc-<id>_media-stack_sonarr-config'
find /out/staging/claims/media-stack/sonarr-config -name '*.db'
```

**Success** is the database file, and no `-wal` beside it: the staging step opens the database through
SQLite's backup API, which produces one clean file. Then prove the file before moving it:

```bash
python3 - <<'EOF'
import sqlite3
db = sqlite3.connect("sonarr.db")
print("integrity:", db.execute("pragma integrity_check").fetchone()[0])
print("rows:", db.execute("select count(*) from Series").fetchone()[0])
EOF
```

`integrity: ok` and a count that matches the last thing the application showed. `sqlite3 <file>
'.tables'` lists the tables when the application's main table is not the one named here.

## Putting it back

**Destructive.** Copy the database into the claim only with the writer stopped: pause the
Application's automation, scale the Deployment to zero, wait on the pod selector recorded in
[restore.md §2](../runbooks/restore.md#section-2-an-applications-configuration), then swap. Leave the
live database aside as `*.db.before-restore` inside the claim rather than deleting it — that file is
the rollback — and start the application again.

**Rollback**: stop the pod, move `*.db.before-restore` back over the restored file, restore the saved
sync policy, and start. Nothing outside the claim was touched, so there is nothing else to undo.

## When the row is a `files` row

There is no staged copy, and that is not a fault: the row says the claim is backed up as it is, and a
database inside it was copied while the application was writing. The answer is the claim restore in
[a file or a claim](a-file-or-a-claim.md), with the same stop-first rule — not an improvised copy of a
live database file.

Which kind a row is, and why, is in `cluster/helm/backup/values.yaml` beside the row, and in
[how backups work](../how-it-works/backups.md).
