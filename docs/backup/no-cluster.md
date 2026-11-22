# No cluster at all

> **In one line.** A laptop, the password manager and one bucket: this is the page that says when that
> is the whole situation, what it takes, and what it can and cannot prove — the commands are elsewhere.
>
> **Use it when** the cluster does not exist, the house is not available, or you want to know whether
> the backups open without the thing that made them · **Verified** 2026-11-22 · **Kind:** how-to
>
> **Sources** `docs/runbooks/last-resort.md`, `docs/security/critical-points.md`,
> `docs/how-it-works/backups.md`

## When it applies

- The cluster is gone: the machines are gone, or the disks are, and no `kubectl` answers anywhere.
- The house is gone, and so is everything in it, including the NAS.
- Nothing is broken, and you are proving the chain: a scheduled drill is this page's cheapest use.

It does **not** apply when the cluster still stands and one application is broken — that is
[restore.md](../runbooks/restore.md), and it is faster. The dividing line is `kubectl get nodes`: if it
answers, this is the wrong page.

## What it needs

Four things, and three of them are the same thing — access to one vault:

- A machine with `restic` and nothing else from this homelab.
- The restic repository password, from the Bitwarden item `homelab-backup-last-resort`.
- The bucket's S3 key pair, from the same item — or re-derived from `infra-main`'s `ovh/backup` stack.
- The four repository names: `nodes-nas`, `nodes-nuc0`, `nodes-streaming`, `nfs`.
- Access to Bitwarden itself, which is why its master password and recovery codes are on
  [the critical points](../security/critical-points.md) with a card beside them: that row is not
  recorded.

## What it proves, and what it does not

**Proved on 2026-11-22**: all four repositories were listed and a tree from `nfs` was decrypted from a
laptop, using only the values in the vault, with the on-disk copies of the password moved aside. The
commands and the row are in [last-resort.md](../runbooks/last-resort.md) and the drill log of
[restore.md](../runbooks/restore.md#the-drill-log).

**Not proved**: that a cluster is then rebuilt from what was recovered. The sequence exists — hosts,
then cluster from git, then data — and it is in [what a loss costs](what-a-loss-costs.md#the-order-to-rebuild-in),
but nobody has walked it end to end.

**The one thing this cannot fix**: if the password is gone, the bucket is ciphertext. No page recovers
from that, and [what a loss costs](what-a-loss-costs.md) says so in the row that matters most.
