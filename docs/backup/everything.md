# Everything is gone

> **In one line.** The rebuild starts outside the cluster — domain, tenant, bucket, vault, certificate
> root, repository — and this is that order, with where each thing is held and who can produce it.
>
> **Use it when** the total loss is real, or you are checking that the chain would hold if it were ·
> **Verified** 2026-11-22 · **Kind:** how-to
>
> **Sources** `docs/runbooks/last-resort.md`, `docs/security/critical-points.md`,
> `docs/runbooks/internal-ca.md`, `docs/backup/what-a-loss-costs.md`

Each row is needed before the next, which is why the order is the page. Every cell is filled on
2026-11-22 (card #461): a row that used to say **not recorded** now names the vault item or the paper
copy, and the person who can produce it.

| Order | What | Where it is held | Who can produce it |
|---|---|---|---|
| 1 | The registrar account for `ashudev.com` and `ashuvidz.com` | OVH SAS, in the same account as the zone and the bucket; `ashudev.com` renews on 2027-03-29, `ashuvidz.com` on 2028-04-25 | the operator, with the OVH sign-in kept in the vault |
| 2 | DNS for those names, pointing at the new public address | the OVH tenant that holds the zone | the operator, with the OVH account |
| 3 | The OVH tenant: the zone and the `GRA` bucket | the OVH account's sign-in, recovery codes and two-step login backup, in the vault | the operator, with the vault |
| 4 | The bucket's S3 key pair | the Bitwarden item `homelab-backup-last-resort`, and the `backup-restic` Secret while a cluster exists | the vault, or `infra-main`'s `ovh/backup` stack: `tofu output -raw access_key_id` |
| 5 | The restic repository password | the same vault item, and nowhere else | the vault only; it is generated once and derived from nothing |
| 6 | The vault itself: master password, 2FA, recovery codes | the master password and the two-step login recovery code on paper, kept offline | the operator, from that paper — it is the only copy |
| 7 | The internal CA's root key and certificate | a secure note in the vault, plus an encrypted copy on an offline USB stick | the vault or the stick — the stick's second copy is still owed |
| 8 | The GitHub account that owns this repository | the account's sign-in and its recovery codes, in the vault | the operator, with the vault |
| 9 | The K3s server token, when a new etcd is built from a snapshot | the vault item, field `K3S_SERVER_TOKEN` | the vault |

No row is a gap any more. The chain ends on row 6: the vault opens the tenant and the repository
account, and the paper copy of the vault's own master password and recovery code is what opens the
vault. That copy is one page in one place, and the second copy is still owed — the same debt the CA
row carries. [The critical points](../security/critical-points.md) is the full list.

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
