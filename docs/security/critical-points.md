# The critical points

> **In one line.** A handful of things carry the whole homelab: lose one and either the data is
> ciphertext, everyone is locked out, or the cluster has to be rebuilt from a repository you can no
> longer reach. This page names them and says where the copy is.
>
> **Use it when** you are deciding what to back up, auditing custody, or facing the morning after a
> loss · **Verified** 2026-11-22 · **Kind:** reference
>
> **Sources** `docs/how-it-works/backups.md`, `docs/runbooks/internal-ca.md`,
> `docs/runbooks/last-resort.md`, `docs/secrets.md`, `cluster/helm/backup/values.yaml`

Ordered by how much goes with it. The last column is the page that picks it up again; this page
never repeats the procedure.

| What it is | What stops without it | Where its copy is | The page that recovers it |
|---|---|---|---|
| **The restic password** and the bucket's key pair | every backup becomes ciphertext: the six nightly jobs run and write nothing anyone can read | the Bitwarden item `homelab-backup-last-resort`, and for the password nowhere else | [last resort](../runbooks/last-resort.md) |
| **The GitHub account** that owns this repository | the configuration of everything: charts, Application objects, routes, the backup matrix itself | not recorded — the account's recovery codes are not in the repository, and this row is why the card exists | [last resort](../runbooks/last-resort.md) |
| **The internal CA root** | every `*.kub.local` name serves a warning, and no client trusts the cluster again | the key and certificate as a secure note in Bitwarden, plus an encrypted copy on an offline USB stick — *the second copy is still owed, see [the CA runbook](../runbooks/internal-ca.md)* | [the internal CA](../runbooks/internal-ca.md) |
| **authentik and its database** | every sign-in: native OIDC, the gate, the dashboard, all of it | the `postgres` dump in the `nodes-nas` stream, and authentik's own claim in the same stream | [PostgreSQL restore](../runbooks/postgres-restore.md) |
| **The OVH tenant** (bucket and DNS zone) | the backup bucket and every `ashudev.com` name, including the public sites | not recorded — the tenant's recovery path is not in the repository, and this row is why the card exists | [last resort](../runbooks/last-resort.md) |
| **The registrar** of `ashudev.com` and `ashuvidz.com` | renewal lapses and every public name stops resolving, however healthy the cluster is | not recorded — no registrar, account or renewal date is written down here | [last resort](../runbooks/last-resort.md) |
| **The `lan-only` middleware and the route charts** | the boundary between the LAN and the internet: a rebuilt cluster without them answers every hostname | git, and the `nfs` stream carries nothing of it — it is configuration, not data | [network](../how-it-works/network.md), [route a request](../running-it/ship-a-change.md) |
| **The Traefik certificate store** (`acme.json` on `pv-traefik-nfs`) | public certificates until re-issued; losing it burns Let's Encrypt's weekly quota re-requesting every name | the `nfs` stream, tag `traefik` | [the certificate store](../runbooks/acme-store.md) |
| **Each node's `local-path` disk** | the claims that landed on that node: an application's configuration, and for some, its only database | the `nodes-<node>` stream, per claim | [a dead node](../backup/a-node.md) |
| **The password manager's own recovery code** | the restic password, the CA root and every other item in it, at once | not recorded — Bitwarden's recovery code is not written down in this repository | [last resort](../runbooks/last-resort.md) |

Three rows say **not recorded**, and they are the honest state on 2026-11-22: the registrar, the
GitHub account's recovery and the password manager's recovery code exist in someone's head or on a
piece of paper, not in a file a rebuild can read. The operator session that fills them replaces the
cell with the fact and cites the card.

## What is not on this list, and why

- **The bulk media.** The film, television and music libraries are one copy by decision. Their loss
  costs the collection, not the homelab, and no restore path pretends otherwise.
- **Observability history.** Prometheus and Loki hold thirty days at most; losing them costs the
  graphs, not the system.
- **Group membership in authentik.** It is an access decision, not configuration: a restore brings
  the groups of the backup's date, and the operator re-checks who is in them.

What is protected, and how, is [how backups work](../how-it-works/backups.md); what a loss costs is
[what a loss costs](../backup/what-a-loss-costs.md), and what is covered claim by claim is
[the coverage table](../backup/coverage.md).
