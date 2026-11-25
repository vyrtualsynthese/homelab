# A file, or a whole claim

> **In one line.** If what is missing is a file or a directory, the snapshot has it; if what is missing
> is a claim's contents, the snapshot has that too, and putting it back means stopping the writer first.
>
> **Use it when** a file was deleted, a directory was emptied, or a claim came back empty after a pod
> was rebuilt · **Verified** 2026-11-22 · **Kind:** how-to
>
> **Sources** `docs/runbooks/restore.md` §2 and §3, `cluster/helm/backup/values.yaml`

**Preconditions.** The restic password and the bucket's key pair, from the Bitwarden item
`homelab-backup-last-resort`. The repository name: `nfs` for the exports (traefik, koillection,
syncthing), `nodes-<node>` for a claim that lives on a node's own disk. Step 0 of
[restore.md](../runbooks/restore.md#step-0-a-restic-shell-in-the-cluster) opens the restic shell every
command below runs in.

## A file

The exports are backed up as they are, so the file is at its own path inside the snapshot:

```sh
restic -r "$NFS" ls latest | grep 'nfs/syncthing/Photos/2026'
restic -r "$NFS" restore latest --target /out --include '/mnt/storage/nfs/syncthing/Photos/2026/IMG_0421.jpg'
sha256sum /out/mnt/storage/nfs/syncthing/Photos/2026/IMG_0421.jpg
sha256sum /mnt/storage/nfs/syncthing/Photos/2026/IMG_0421.jpg   # on the NAS itself
```

**Success** is two identical hashes, or a file whose size and first bytes match what the application
shows. **Rollback** is nothing: the restore wrote into `/out` inside a scratch pod, and the live file
was never opened. Copy it into place only once you are satisfied, and copy the old one aside first if
it still exists.

## A claim

A claim's backup depends on its row's `kind`, and a `sqlite` claim is written in two places — the
database under `/staging/`, the rest of the configuration at its host path. The table in
[restore.md §2](../runbooks/restore.md#section-2-an-applications-configuration) is the authority on
which half you are missing; a `files` row has only the second.

1. **Restore into the writer pod, not onto the live claim.** The runbook's sequence creates a pod
   pinned to the node that holds the claim, restores the tree into it, and leaves the application
   running until the tree is ready.
2. **Stop the writer.** Pause the Application's automation first, then scale to zero and wait on the
   pod. Two traps are recorded there and both are real: the pods carry
   `app.kubernetes.io/instance=<app>`, so a `kubectl wait` on `app=<app>` returns immediately; and
   ArgoCD's self-heal reverts a scaled-down Deployment mid-swap because the automation lives in the UI,
   not in git.
3. **Move the old tree aside inside the claim**, never out of it, never deleted. That directory is the
   rollback.
4. **Restart and look.** The application answers, its own pages list what the database holds, and
   ArgoCD reads `Synced` and `Healthy`.

**Rollback**: stop the application again, move the old tree back, restore the saved
`/tmp/<app>-syncpolicy.json` onto the Application, and let the pod start. The claim keeps both trees
until you delete one by hand, and nothing in the cluster deletes them for you.

The worked example — Sonarr, 32 seconds down, 103 series and 566 episode files back — is in the drill
log at the foot of [restore.md](../runbooks/restore.md#the-drill-log).
