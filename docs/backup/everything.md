# Everything is gone

> **In one line.** The rebuild starts outside the cluster — domain, tenant, bucket, vault, certificate
> root, repository — and this is that order, with where each thing is held and who can produce it.
>
> **Use it when** the total loss is real, or you are checking that the chain would hold if it were ·
> **Verified** 2026-11-22 · **Kind:** how-to
>
> **Sources** `docs/runbooks/last-resort.md`, `docs/security/critical-points.md`,
> `docs/runbooks/internal-ca.md`, `docs/backup/what-a-loss-costs.md`

Each row is needed before the next, which is why the order is the page. Where the repository has no
answer the cell says **not recorded** instead of guessing: those are the three rows that make a real
total loss harder than this table looks, and each one has a card against it.

| Order | What | Where it is held | Who can produce it |
|---|---|---|---|
| 1 | The registrar account for `ashudev.com` and `ashuvidz.com` | **not recorded** | **not recorded** — a gap, see the card |
| 2 | DNS for those names, pointing at the new public address | the OVH tenant that holds the zone | the operator, with the OVH account |
| 3 | The OVH tenant: the zone and the `GRA` bucket | the OVH account's own sign-in, with its recovery path | the operator; the recovery path is **not recorded** |
| 4 | The bucket's S3 key pair | the Bitwarden item `homelab-backup-last-resort`, and the `backup-restic` Secret while a cluster exists | the vault, or `infra-main`'s `ovh/backup` stack: `tofu output -raw access_key_id` |
| 5 | The restic repository password | the same vault item, and nowhere else | the vault only; it is generated once and derived from nothing |
| 6 | The vault itself: master password, 2FA, recovery codes | the operator's head and a piece of paper | **not recorded** — a gap, see the card |
| 7 | The internal CA's root key and certificate | a secure note in the vault, plus an encrypted copy on an offline USB stick | the vault or the stick — the stick's second copy is still owed |
| 8 | The GitHub account that owns this repository | the account's sign-in and its recovery codes | **not recorded** — a gap, see the card |
| 9 | The K3s server token, when a new etcd is built from a snapshot | the vault item, field `K3S_SERVER_TOKEN` | the vault |

Rows 1, 6 and 8 are the gaps. Each is a thing a person can produce today and no file can, and each is
worth fixing before the morning it matters: [the critical points](../security/critical-points.md) is
where they are tracked, and the operator session that writes them down replaces the cell.

## Then the rebuild

The sequence inside the cluster — hosts, then cluster from git, then data — is
[the order to rebuild in](what-a-loss-costs.md#the-order-to-rebuild-in) and is not repeated here. Two
things about it that this page's order decides:

- The repository cannot be cloned without row 8, which is why the chain starts at GitHub and not at
  `helm`.
- The data cannot be read without rows 4, 5 and 6, which is why the bucket is opened and proved
  readable before a single node is installed. That is [no cluster at all](no-cluster.md).

What is not backed up at all, and never will be, is in [what a loss costs](what-a-loss-costs.md): the
media libraries are one copy by decision, and a fire ends them.
