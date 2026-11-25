# Rebuild a lost data disk from parity

> **In one line.** Replace one failed disk of `nas`'s six-disk array, mount the replacement where the
> array expects it, and let SnapRAID compute back what the disk held — the array as the last sync
> recorded it, not as it was when the disk died.
>
> **Use it when** a disk behind `/mnt/storage` has failed or is failing, `snapraid status` on `nas`
> names a disk as missing, or a replacement disk is going in · **Verified** 2026-11-25: every command
> checked against `man snapraid fix` (12.1, the version installed on `nas`) and the mount step against
> the by-UUID `/etc/fstab` measured in [`../audit/hosts-2026-11-24.md`](../audit/hosts-2026-11-24.md);
> **never run** — no spare disk exists, see [the drill](#the-drill-cannot-run-yet) · **Kind:** how-to
>
> **Sources** `ansible/roles/snapraid/`, `docs/audit/hosts-2026-11-24.md`,
> [`../how-it-works/storage.md`](../how-it-works/storage.md), `man snapraid` on `nas`

**Destructive, and the only copy.** `snapraid fix` rewrites the failed disk's files to their
last-synced state and there is no undo. The film and television library, the music and the stash
library are not backed up — one copy on `nas`, by decision
([storage.md](../how-it-works/storage.md#backups)) — so whatever the parity cannot carry is gone.

Parity is spare data calculated from the six disks, not a second copy of them: it can rebuild **one**
disk at a time, and it rebuilds the array as of the last `snapraid sync`, not as of the moment the
disk died. Both limits are what this page is careful about.

## What the rebuild brings back, and what it does not

`snapraid fix` reverts the disk to the state recorded in the content file — the index of every path
and its checksum that the last sync wrote. The command does not tell an error from an intentional
change, so it puts back what it knows and nothing else:

| The file, at the moment the disk was lost | After the rebuild |
|---|---|
| written or last changed **before** the last sync | back, byte-identical |
| changed or rewritten **after** the last sync | the version the last sync recorded — the newer one is lost |
| created **after** the last sync | absent: it is not in the content file, so the array never knew it |
| deleted **after** the last sync | back, because the parity still has it |
| in the content file but not recoverable | left renamed with a `.unrecoverable` suffix, never disguised as good data |

So the date of the last sync decides everything. Read it before anything else:

```bash
sudo snapraid status
```

Three strings in that report are the gate:

- **`NOT fully synced`** — the parity does not describe the array as it is. Whatever is unsynced is
  not in it, and a rebuild returns the older version of those files.
- **`sync in progress`** — a run is going right now, and a second one started on top of it will fail
  on snapraid's own lock. Wait for it to end.
- **`days ago of the last scrub/sync`** — the age of the array the parity knows. Zero is current;
  anything above zero is that many days of writes the rebuild will not have.

On 2026-11-24 the answer was 1,018 days, and the parity and content files all carried 2024-02-10: the
runner had been dead since `python2` was dropped from the host, so nothing written since had ever
reached the parity. `ansible/roles/snapraid` replaced the runner that evening and put the first real
sync at 03:17, which card #710 is what proves ran. **A disk lost while the parity is stale rebuilds
2024**, and files the sync has never seen are the ones that do not come back.

## Step 0 — read the failed disk first, if it still answers

A stale parity makes the failed disk the newer copy. If it still mounts, read it before you replace
it, and copy out what the parity does not know — the files with a modification time after the last
sync:

```bash
sudo mkdir -p /mnt/recover
sudo mount -o ro /dev/disk/by-uuid/<the old uuid> /mnt/recover
sudo rsync -a --ignore-existing /mnt/recover/ /mnt/hdd<M>/recovered/
```

`/mnt/hdd<M>` is another disk of the same array, named directly so the copy does not land back on the
failing branch; `sudo snapraid list` prints every path the parity holds, which is what the copy is
measured against. A disk that will not mount read-only is not a reason to delay the rebuild — it is
the reason the parity is the only source left.

## Step 1 — stop what writes, and the nightly run

Nothing may write into the union while `fix` runs: the union is the six disks seen as one path, so a
write lands on a disk `fix` is rewriting and comes back as the older version. **Reading is safe** —
the other five disks keep serving the library.

Stop the nightly job first, since 03:17 is inside every rebuild that takes hours:

```bash
sudo mv /etc/cron.d/snapraid /root/snapraid.cron
```

Then stop the writers. They are the applications that move, rename, delete or fetch files into a
`nas` export — the importers, the library managers, the two book servers, the uploads app, Syncthing
and FileBrowser:

```bash
kubectl -n media-stack scale deployment --replicas=0 \
  sonarr sonarr-anime radarr radarr-anime lidarr bazarr bazarr-anime \
  calibre-web-automated kavita maintainerr
kubectl -n koillection scale deployment --replicas=0 koillection
kubectl -n syncthing scale deployment --replicas=0 syncthing
kubectl -n filebrowser scale deployment --replicas=0 filebrowser
```

`kubectl -n <namespace> get deploy` names the real Deployments if a release is not called after its
Application. `plex`, `seerr` and `tautulli` only read the library and can stay up.

One writer is easy to miss: `/mnt/storage/traefik` holds the ACME certificate store, so Traefik is a
writer on the union too. It writes only when a certificate is issued, and a certificate issued during
the rebuild is reverted by it — a narrow window, and the reason to run a rebuild outside the weekly
issuance hour rather than to stop the router.

## Step 2 — name the disk inside the array

The array knows disks as `d1`, `d2` and so on, not as `/dev/sdX`, and `-d` takes that name:

```bash
sudo snapraid devices                 # mount point → array name
sudo sed -n '1,40p' /etc/snapraid.conf
lsblk -o NAME,SIZE,FSTYPE,UUID,MOUNTPOINT
grep -n hdd /etc/fstab
sudo smartctl -a /dev/disk/by-uuid/<the failing uuid>   # while the disk still answers
```

`snapraid devices` prints one line per disk: the device it resolves to, the configured path, and the
array name. The config's `data dN /mnt/hddN` line is the same pairing, and `parity` and `content`
name the files that must be reachable — the parity file sits on a disk outside the six data mounts,
and its own mount has to be up before anything reads it.

Success: one mount point maps to exactly one array name, and `snapraid status` lists that disk as
missing. Any other disk in that list is a second loss, and [one parity cannot rebuild two](#when-it-does-not-work).

## Step 3 — take the map aside

The content file is the index the recovery reads; `fix` reads it and never writes it. `nas` carries
six of them, and one of those copies may sit on the disk being replaced — which is the point of
having several. Copy them all the same, because the copy in `/root` is the only way back if a run has
to be undone by hand:

```bash
sudo cp -a /etc/snapraid.conf /root/snapraid.conf.$(date +%F)
sudo awk '$1 == "content" { print $2 }' /etc/snapraid.conf \
  | xargs -r -I{} sudo cp -a {} /root/
```

Success: `ls -l /root/snapraid*` shows the config and one copy per `content` line.

## Step 4 — replace the disk and mount it in the same place

`/etc/fstab` mounts each data disk by the filesystem's UUID rather than by its device name, so the
replacement has to be formatted and recorded in `/etc/fstab` under the same mount point. A hot-swap
bay takes the disk with the machine up; otherwise:

```bash
sudo umount /mnt/hdd<N>          # the failing branch, if it still mounts
sudo poweroff                    # swap the disk with the machine off
```

`nas` serving the exports means the whole cluster notices that power-off: the NFS clients keep the
mount, and the pods that read the library fail reads until the host is back. Expect I/O errors in the
media pods' logs, and restart any that stay broken after the exports return.

Back up, with the new disk in place:

```bash
sudo lsblk -o NAME,SIZE,MODEL,SERIAL,FSTYPE    # the new device: match size and serial, not sdX
sudo wipefs -a /dev/sdX
sudo mkfs.ext4 /dev/sdX
sudo blkid /dev/sdX                            # the new UUID
sudoedit /etc/fstab                            # same mount point, new UUID, same options
sudo systemctl daemon-reload
sudo mount /mnt/hdd<N>
findmnt /mnt/hdd<N>
```

Success: `findmnt` prints the mount point with the **new** UUID, `df -h /mnt/hdd<N>` shows its size,
and `ls -A /mnt/hdd<N>` is empty.

Then check that the union carries all six branches:

```bash
findmnt -o OPTIONS /mnt/storage
```

A branch missing from that list means `/mnt/storage` was mounted before the disk came back. With the
writers still stopped, remount it — `sudo umount /mnt/storage && sudo mount /mnt/storage` — before
they return.

**The mount is the trap of this page.** `snapraid fix` writes to the configured path, not to a disk:
with `/mnt/hdd<N>` unmounted, the recovered files land in the empty directory on the root filesystem.
Root is 457 GB and the disk held terabytes, so the run fills root and stops, leaving a partial copy
where the disk should be. Run `findmnt /mnt/hdd<N>` immediately before `fix` — every time, on the
same line of thinking as the `-E, --force-empty` guard the man page describes for `sync`.

A replacement larger than the parity can cover is not protected: the parity files together must be at
least as large as the largest data disk, so a bigger disk is only fine while that stays true.

## Step 5 — prove the parity can do it, without writing

`check` runs the same recovery as `fix` and writes nothing, which is exactly the pre-flight wanted:

```bash
sudo snapraid -d d<N> -U check
```

`-U` is required because the replacement filesystem carries a new UUID while the content file still
holds the old one; a single UUID change is the normal case after a recovery, and snapraid stops
without it rather than guessing.

Success: the run ends with no errors and no `unrecoverable` files. A number above zero names files
the parity cannot rebuild — stop here and read [what the rebuild cannot bring back](#what-the-rebuild-brings-back-and-what-it-does-not),
because those files were written after the last sync or belong to a disk that is also missing.

## Step 6 — rebuild

```bash
sudo snapraid -d d<N> -U -l /root/snapraid-fix-$(date +%F).log fix
```

- `-d d<N>` filters to that disk: every other disk in the array is untouched.
- `-U` accepts the new UUID, as in the `check` above.
- `-l` writes the detail to a file. Without it, unexpected errors go to the screen, and a run of hours
  scrolls them out of reach.

It reads the parity and writes everything the disk held, at disk speed: hours, not minutes. Ctrl+C
stops it; a second run starts over from the beginning, and neither the content file nor the parity is
left half-written by the interruption.

Success: exit 0, and the log's summary reports no errors.

## Step 7 — read the result before trusting it

```bash
sudo snapraid status
sudo find /mnt/hdd<N> -name '*.unrecoverable' -print
```

`status` no longer names the disk as missing. Every `.unrecoverable` file is one the parity could not
rebuild: snapraid leaves the name visibly marked so nothing mistakes it for good data, and that list
is what to accept or replace by hand. `status` still reports the old sync date, because `fix` does not
update the content file — the array is at the last sync, and the sync is the next step.

## Step 8 — hand it back

Prove the rebuilt disk against the parity, put the writers back, then let the array carry the new
state:

```bash
sudo snapraid -d d<N> -U check        # the rebuilt disk, read against parity: no errors
kubectl -n media-stack scale deployment --replicas=1 \
  sonarr sonarr-anime radarr radarr-anime lidarr bazarr bazarr-anime \
  calibre-web-automated kavita maintainerr
kubectl -n koillection scale deployment --replicas=1 koillection
kubectl -n syncthing scale deployment --replicas=1 syncthing
kubectl -n filebrowser scale deployment --replicas=1 filebrowser
sudo mv /root/snapraid.cron /etc/cron.d/snapraid
sudo /usr/local/sbin/snapraid-sync    # sync, then status, then a scrub on the first Sunday
sudo snapraid scrub -p bad            # recheck the blocks marked bad and clear the marks
```

`/usr/local/sbin/snapraid-sync` is the runner from `ansible/roles/snapraid`: it does `sync`, then
`status`, then one scrub slice on the first Sunday of the month. Running it by hand moves the parity
onto the state you have just accepted instead of waiting for 03:17 — until that sync, the array is one
disk loss away from the 2024 state again.

Success: `journalctl -t snapraid -n 20` holds `sync finished`; `snapraid status` names no bad blocks
on `d<N>`; and a final `sudo snapraid -d d<N> -U check` reports nothing. The scrub rechecks the blocks
the earlier failure marked bad and clears the mark when they read back, which is the last proof that
the rebuilt data agrees with the parity.

## When it does not work

- **Two disks at once.** One parity file rebuilds one disk. With two missing, snapraid has nothing to
  compute the second from, and the library on those two disks is gone — there is no backup of the
  union. Take the list of what was on them from `snapraid list` and rebuild what can be fetched again.
- **`check` names unrecoverable files.** Do not run `fix` expecting them back. Their blocks were never
  synced, or the parity is inconsistent; the only copy is the failed disk, if it still answers
  (Step 0).
- **`fix` filled the root filesystem.** The mount was not there. Stop the run, unmount `/mnt/hdd<N>`,
  and the misplaced tree is visible again under the mount point on `/`: delete that tree, mount the
  disk, and start over from Step 5.
- **`snapraid` stops on a UUID it does not know.** Expected once, with `-U`. A second UUID change in
  the same repair means a mount point is wrong — find it before forcing anything: `-U` on the wrong
  path rebuilds onto the wrong disk.

## The drill cannot run yet

No spare disk of 7.3 TB sits on the shelf. A drill needs one disk that can act as the failed disk and
be replaced — the failure, the format and the by-UUID mount are the half of this procedure that a
small rehearsal cannot show — so a full drill waits for that disk, and this page says so rather than
claiming a proof it does not have.

What can be rehearsed today is the command sequence: a loop-mounted pair of small files with a
throwaway config gives a real `sync`, a deleted file and a real `fix`, and proves the flags and the
output. It proves snapraid's behaviour, not this array's parity.
