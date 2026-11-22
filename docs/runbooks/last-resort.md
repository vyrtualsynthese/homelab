# When the cluster is gone

> **In one line.** The morning after a fire or a ransomware run: a laptop, the password manager and
> one OVH bucket in `GRA` are all that is left, and this page gets the data out of the bucket and
> readable before anything is rebuilt.
>
> **Use it when** the cluster does not exist any more, the house is not available, or the backups
> need proving from outside the cluster · **Verified** 2026-11-22: four repositories read from this
> laptop with no cluster, values taken from Bitwarden · **Kind:** living page

Every other restore page assumes a cluster that still stands: a restic pod in `backup`, the
`backup-restic` Secret, `kubectl` answering. [`restore.md`](restore.md) is that page. This one
assumes none of it — no cluster, no Secret, no `kubectl`, possibly no house.

What survives both cases is the bucket and the keys, and only if the keys were somewhere else. That
is the whole subject.

## The chain of keys

Four secrets stand between a dead cluster and readable data. Each has a failure domain: where a copy
exists that the house cannot take with it.

| Secret | Where a copy lives today | Failure domain | If it is lost |
|---|---|---|---|
| restic repository password | Bitwarden, item `homelab-backup-last-resort` (Homelab folder, re-prompt on) | cloud vault | the bucket is ciphertext; nothing on this page works |
| OVH S3 key pair for the bucket | the same vault item, and the `backup-restic` Secret in the cluster | cloud vault | without it the bucket cannot be opened, though the data is intact |
| K3s server token (`/var/lib/rancher/k3s/server/token`) | the same vault item, field `K3S_SERVER_TOKEN` | cloud vault | an etcd snapshot opens to garbage: it is encrypted under a key derived from the token |
| Bitwarden master password and 2FA recovery codes | nowhere written down | — | every row above is sealed behind it |

Two honest statements about that table.

**The vault copy is hours old.** Until 2026-11-22 Bitwarden held neither the repository password nor
a K3s token: the note that reads like a backup note ("Kluster Backup codes") is the retired AWS and
syncthing setup, and its passwords do not open this bucket. `secrets.md` promised "the password
manager, plus one sealed paper copy" — the first half was false, and nothing in this repository can
check the second.

**There is one vault, and the vault is the whole chain.** Losing access to Bitwarden loses the
backups as completely as losing the bucket. That is why the four rows above have the same failure
domain and why the yearly proof in the last section exists.

The S3 key pair is the one secret that can be rebuilt: it is an output of `infra-main`'s
`ovh/backup` stack, so with the OVH API key and the state bucket's own pair,

```bash
cd infra-main/infra-main/ovh
tofu output -raw access_key_id
tofu output -raw secret_access_key
```

prints it again. The restic password has no such path — it is the one value generated once and never
derived from anything.

## What a compromised cluster can still do

The cluster holds the bucket's S3 pair, because the backup jobs need it. Whoever reads the
`backup-restic` Secret can run `restic forget --prune` and delete the history the bucket holds. No
monitoring on this page repairs that.

The decision on 2026-11-22 is **object versioning with a 30-day window on previous versions**: a
delete becomes a marker, the bytes live on for thirty days, and the backup user cannot delete a
version permanently. **It is in force:** versioning was switched on at 01:29Z and two lifecycle rules
were applied at 02:43Z — `expire-noncurrent-after-30-days` (`NoncurrentVersionExpiration: 30`) and
`expire-delete-markers` (`ExpiredObjectDeleteMarker: true`) — and read back through the OVH API. The
cluster's credential still cannot purge history inside that window; the account owner can.

The second copy — one that survives losing the OVH account, not just the cluster — waits on a second
EU provider and is card #535, frozen.

## Before you start

- A laptop, any operating system. Nothing else in this section needs the homelab.
- `restic`, any recent version. The jobs run `0.19.1` and the 2026-11-22 proof used `0.18.0`; both
  read these repositories. It is one static binary: take it from
  `https://github.com/restic/restic/releases` and put it on the `PATH`.
- The Bitwarden item `homelab-backup-last-resort`, which needs the master password and the 2FA
  device.
- About 30 GB free disk. The repositories settle between 15 and 25 GB, and one repository at a time
  is enough to start with.

## Step 1 — prove the password before pulling anything

Set the four values from the vault. Nothing below reads a file on the homelab, and nothing needs
`kubectl`.

```bash
export RESTIC_PASSWORD='<RESTIC_PASSWORD from the item>'
export AWS_ACCESS_KEY_ID='<S3_ACCESS_KEY_ID>'
export AWS_SECRET_ACCESS_KEY='<S3_SECRET_ACCESS_KEY>'
export AWS_DEFAULT_REGION=gra
export RESTIC_CACHE_DIR="$HOME/.cache/restic"

export NODES_NAS=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nodes-nas
export NODES_NUC0=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nodes-nuc0
export NODES_STREAMING=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nodes-streaming
export NFS=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/nfs

for r in "$NODES_NAS" "$NODES_NUC0" "$NODES_STREAMING" "$NFS"; do
  restic -r "$r" snapshots --latest 1
done
```

Success is one snapshot row per repository. What the 2026-11-22 run found, one line per repository,
reformatted from `restic snapshots --json`:

```
nodes-nas       2026-11-22T00:33:45  44e80ca9   108.2 MiB
nodes-nuc0      2026-11-22T00:01:57  80f5025f   811.2 MiB
nodes-streaming 2026-11-22T00:31:27  d434572d   652.0 MiB
nfs             2026-11-21T06:00:12  c517d884    91.0 MiB
```

`Fatal: unable to open config file … Access Denied` means the key pair is the wrong one, not that the
data is gone: the state bucket's pair is in `keys.env` and opens nothing here. Take the bucket's own
pair from the vault, or re-derive it as above.

`wrong password` means the vault value and the repository disagree. Stop and find the right one;
no command below can work around it.

## Step 2 — pull the data out, and check it

Restore onto the laptop's disk, one small piece at a time. This reads only; it writes nothing back to
the bucket.

```bash
# the SQLite copy lives in the node stream
restic -r "$NODES_STREAMING" restore latest --target ~/restore \
  --include '/staging/claims/media-stack/sonarr-config'

# the Syncthing file lives in the NFS stream
restic -r "$NFS" restore latest --target ~/restore \
  --include '/mnt/storage/nfs/syncthing/Photos/Argentique/IMG_20240121_0008.jpg'
```

Then verify **two** things, because a restore that has not been read is not a restore.

**A file, by hash.** The Syncthing photo the drill in `restore.md` used is a reference that outlives
the cluster:

```bash
sha256sum ~/restore/mnt/storage/nfs/syncthing/Photos/Argentique/IMG_20240121_0008.jpg
# 2026-11-21 recorded: a90eb14a…  — a different hash means this is not the file the drill saw
```

**A database, by SQLite's own check.** The staged copy is the consistent one; the live file was being
written while the job read it.

```bash
python3 - <<'EOF'
import sqlite3
db = sqlite3.connect("/home/<you>/restore/staging/claims/media-stack/sonarr-config/sonarr.db")
print("integrity:", db.execute("pragma integrity_check").fetchone()[0])
print("series:", db.execute("select count(*) from Series").fetchone()[0])
EOF
```

Success is `integrity: ok` and a series count that is plausible. On 2026-11-21 it was 103 series and
6 212 episodes; a count far below that means an older snapshot was restored, not a broken one.

The PostgreSQL dumps are the other half: `globals.sql` and one `<database>-<UTC>.dump` per database,
under `nodes-nas`. They are read by `pg_restore`, so nothing checks them on the laptop; their check
is the one in `postgres-restore.md`, once an instance exists.

## Step 3 — what the retention gives you, and what it does not

`backup-prune` runs on Sunday at noon with **7 daily, 4 weekly, 12 monthly**.

| The data you want | Where it is |
|---|---|
| the last week | a nightly snapshot |
| the last three months | a weekly snapshot |
| older than three months, inside a year | a monthly snapshot, and nothing finer |
| older than a year | gone, by the retention policy |

So a loss discovered three weeks later still has a daily snapshot; one discovered five weeks later
has a weekly; one discovered in March has January's monthly and nothing else. Nothing recovered from
this bucket is older than a year.

Cost is not a consideration at this point: egress from OVH is free, so a full restore costs time. At a
typical home line, 25 GB is under an hour. Start with `nodes-nas` (the dumps, 108 MiB) and
`nodes-nuc0` (the claims and the node's `/etc`, 811 MiB); `nodes-streaming` is 9.4 GB and the `nfs`
repository is the slow one.

## Step 4 — the rebuild order, and only after the data is out

The order is not negotiable, and every step assumes the previous one:

1. **Nothing.** Restore and verify everything first. A rebuild started early overwrites the disk the
   old data might still be on.
2. **One host, then K3s, with the token from the vault in place first.** The token is what makes an
   etcd snapshot readable; installing a new one and restoring afterwards produces a cluster whose
   Secrets are noise.
3. **The Secrets** from [`../secrets.md`](../secrets.md), before ArgoCD syncs anything. A missing
   Secret is a pod in `CreateContainerConfigError`, not a failed sync.
4. **etcd**, if it is being restored at all, into a test cluster first. This has never been rehearsed
   (card #8); treat it as unproven and prefer rebuilding the object state from git.
5. **`globals.sql` before any database dump.** The dump's objects belong to roles a fresh instance
   does not have.
6. **Claims before the applications that write to them**, then the applications, then ArgoCD syncing
   `develop`.
7. **Expect the last day of changes to be missing**, twice over: once because the last snapshot is up
   to 24 hours old, and once because the rebuild itself takes a day.

## Step 5 — the minimum hardware

Two different questions, two different answers.

**To make the data readable:** a laptop, 30 GB free, and the restic binary. That is all. Anything
that can run `restic restore` and open a SQLite file can read this bucket, including a borrowed
machine.

**To have a working cluster again:** one x86 machine, 32 GB of RAM, a 1 TB SSD, and the disks that
hold the data. The five-node split in this homelab is about *placement* — the GPU on `streaming`, the
disks on `nas`, the catalogue on `nuc0` — not about a floor. One node runs K3s, `local-path`, the NFS
export and the applications together, and it will be slower and will not transcode video without a
GPU. `nuc0`'s catalogue alone sits at about 28.5 GB of its 32, so a single node should carry the
applications that matter and not all of them; the rest wait.

## Step 6 — what is gone, and must not be chased

By the operator's decision, and each one was weighed against its size:

| Gone | Why it stayed out |
|---|---|
| the music, the media library, the stash library, downloads | bulk data a source can re-supply, and far larger than everything else combined |
| the books library (22 GB) | dropped on 2026-11-20: re-acquiring a book is possible, and it was the single largest thing in scope |
| Loki logs, Prometheus metrics | rebuilt by what writes them; the history was not worth the bill |
| the two edge Pis' own state | deferred; their restic path is in the Ansible audit |
| poster art, thumbnails, caches, application logs | rebuilt from the metadata providers on the next refresh |

What is left is the part a rebuild cannot invent: application configuration, the databases and their
roles, syncthing, koillection's uploads, the ACME certificate store and the cluster's object state.

## Step 7 — the yearly proof

The copies are only worth what has been read from them. Once a year, **from a machine that is not the
homelab and with nothing copied off the homelab**, fetch the item from Bitwarden and run Step 1 for
all four repositories. One dated line here, no more.

| Date | From | Result | By |
|---|---|---|---|
| 2026-11-22 | this workstation, cluster not consulted | four repositories listed (`nodes-nas` `44e80ca9`, `nodes-nuc0` `80f5025f`, `nodes-streaming` `d434572d`, `nfs` `c517d884`) with values read from Bitwarden; a tree listing from `nfs` decrypted. **Caveat:** the vault item was written from this machine the same morning, so this proves the procedure and the credentials, not yet a second machine | opencode (agent) |

The line to add every year is the same shape, and the rule is that it must be run somewhere else. A
proof from the machine that holds the working copies is not a proof about the copy that matters.

## When it goes wrong

- **`Access Denied` on every repository** — the pair is the state bucket's, not this bucket's. The
  two are different users in `infra-main`; see Step 1.
- **`wrong password`** — the vault value and the repository disagree, or the field was edited. The
  bucket is fine; the value is the problem.
- **A repository lists snapshots from months ago and nothing recent** — the stream stopped advancing
  before the cluster died. Restore what is there, and know that the gap is real: it is what
  `BackupStreamStale` would have said, if anything had been left to alert.
- **The bucket is empty** — check the region and the bucket name first (`gra`,
  `ashudev-homelab-backup`); then the account. Nothing else on this page helps.
- **No Bitwarden access at all** — this page stops here. That is the failure the frozen second-copy
  card exists for, and the reason the vault is the weakest link in the chain.
