# Every procedure

> **In one line.** The shelf: every runbook this repository has, the tab that lists it, and what each
> one has proved — so that nobody runs a guess with a title, and a pending procedure says it is
> pending.
>
> **Use it when** a routine operation needs its procedure, or you want to know whether a restore has
> ever been rehearsed · **Verified** 2026-11-22 · **Kind:** reference
>
> **Sources** `mkdocs.yml`, the files in `docs/runbooks/`, `docs/backup/`, `docs/security/` and
> `docs/running-it/`

A runbook counts as proved only after it has been performed at least once; a procedure nobody has run
is a guess with a title. Every file below exists, and its status is the date it was proved or the fact
that it has not been. Five are completed records, kept because each is the only write-up of something
that happened once — four of them are listed in History, and the fifth, the board's shape, is a
procedure the Running it tab still sends you to.

Each procedure is read where it is used, so the tab column is the route: Backups for anything that
puts data back, Security for identity and certificates, Running it for the rest.

## The shelf

| Procedure | Tab | Status | Use it when |
|---|---|---|---|
| [`authentik-apps.md`](authentik-apps.md) | Security | proved 2026-11-15 | an application needs signing in through authentik: native OIDC where the app speaks it, the forward-auth gate where it does not. The matrix and the traps are in [`reference/sso-applications.md`](../reference/sso-applications.md). |
| [`internal-ca.md`](internal-ca.md) | Security | proved 2026-11-15; the restore drill is pending | a `*.kub.local` certificate fails to validate, the root must be minted again, or a leaf signed. |
| [`acme-store.md`](acme-store.md) | Security | proved 2026-11-22 (34 entries → 16) | a Let's Encrypt certificate exists for a hostname no route serves, or a retired name must stop consuming the weekly issuance limit. The store is one file on an NFS claim, edited with the router stopped. |
| [`restore.md`](restore.md) | Backups | proved 2026-11-21: sections 1–3 restored and read back; the destructive put-back and section 4's etcd restore are proved separately | a file, a claim, a database or the cluster state has to come back out of the OVH bucket, or the monthly restore drill is due. |
| [`restore-etcd.md`](restore-etcd.md) | Backups | proved 2026-11-22 on a throwaway single-node K3s: a production snapshot restored and its objects read back in 16 s | the control plane is unquorate, a server will not start, or the drill that proves the etcd copy restores is due. |
| [`postgres-restore.md`](postgres-restore.md) | Backups | pending — the dumps were verified 2026-11-13, the restore itself has not been run | a database is lost, or the drill that proves the dumps restore is due. |
| [`last-resort.md`](last-resort.md) | Backups | proved 2026-11-22 from a laptop with no cluster, values out of Bitwarden; not yet run from a second machine | the cluster is gone, the house is not available, or the bucket has to be proved from outside before a rebuild. |
| [`argocd.md`](argocd.md) | Running it | pending — the rebuild has not been run | the controller is down, or Argo CD must be rebuilt from nothing since it became self-managed on 2026-11-19. |
| [`traefik-release.md`](traefik-release.md) | Running it | proved 2026-11-21 (chart `20.3.1` → `25.0.3` → `27.0.2` → `42.4.1`, Traefik `2.9.4` → `3.7.19`); Application committed 2026-11-23, first sync pending | the Traefik release has to move to a newer chart, its values change, it has to be inspected or rolled back, or the `traefik` Application has to be synced by hand or taken back out of ArgoCD. The chart is `cluster/helm/platform/traefik`; the sync is manual on purpose. |
| [`k3s-upgrade.md`](k3s-upgrade.md) | Running it | proved 2026-11-21/22 and 2026-11-24, four nodes to `v1.36.6+k3s1` across five minors | K3s has to move to a new minor version, or one node is stuck on an older one. Includes the per-node timings, the ingress blip on `streaming`, and why the plan is pinned to one hostname at a time. |
| [`ubuntu-upgrade.md`](ubuntu-upgrade.md) | Running it | pending — no cluster node has taken the path | a cluster node has to move to the next Ubuntu LTS: two hops in place, one node at a time, with the boot partition, the GPU module and the disk union checked at each hop. |
| [`pi-reflash.md`](pi-reflash.md) | Running it | pending — no Pi has been reflashed with 26.04.1 | an edge Pi moves release or is rebuilt: the image, its checksum, the cloud-init file that puts a key on it, and the copy that has to leave the Pi first. |
| [`renovate.md`](renovate.md) | Running it | proved 2026-11-19 | Renovate stopped opening pull requests, its token has to be rotated, or the nightly slot has to move. |
| [`gpu-operator.md`](gpu-operator.md) | Running it | pending — the chart is in git; the host driver step has not been run since | the GPU disappears from a pod, Plex stops transcoding, the DCGM GPU panels go empty, or someone asks about `nvidia.com/gpu-driver-upgrade-state`. |
| [`cloud-monitoring.md`](cloud-monitoring.md) | Running it | proved 2026-11-20 for the probes, the alerts and the OVH configuration; the `ashuvidz.com` delegation flip has not been run | a `Cloud*` alert fires, a public name or DNS record changes, or a zone's delegation moves. |
| [`alert-relay.md`](alert-relay.md) | Running it | pending — one synthetic alert through Grafana is its proof | Grafana alerts must reach the board, or the relay needs rebuilding. |
| [`postgres-add-database.md`](postgres-add-database.md) | Running it | proved 2026-11-13 (acceptance database) | an application is rebuilt and needs its own role, database and Secrets. |
| [`vikunja-board-seed.md`](vikunja-board-seed.md) | Running it | completed record 2026-11-16, still the only write-up of the boards' shape | the boards' columns must be recreated from the tracked seed, after a loss. |
| [`vikunja-database.md`](vikunja-database.md) | History | completed record 2026-11-16 | the board's database is rebuilt. |
| [`vikunja-first-start.md`](vikunja-first-start.md) | History | completed record 2026-11-16 | the board is built again from nothing, or you need what its first start checked. |
| [`books-cutover.md`](books-cutover.md) | History | completed record 2026-11-13 | the books stack is rebuilt from nothing; it is the only write-up of the move to Calibre-Web Automated. |
| [`docs-site-first-run.md`](docs-site-first-run.md) | History | completed record 2026-11-17 | the docs site is rebuilt from nothing; it holds the measured first run. |

## Not written yet

No file exists for these, so there is no procedure to follow — do not run one from memory.

- `add-a-node.md` — W8 (Ansible and docs).
- `rotate-a-secret.md` — W8 (Ansible and docs). No longer a gap: the procedure lives at
  `docs/running-it/rotate-a-secret.md`, which the Running it tab lists.

## Conventions

Each runbook states its preconditions, the exact commands, the output that means success, and the
rollback. Anything destructive says so in the first line. When a procedure's first run has happened,
its status above becomes `proved <date>`; when it will not be run again, it keeps its history as a
completed record with the date, its body untouched, and a line saying when it is still the
reference.
