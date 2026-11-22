# Runbooks

> **In one line.** The procedures that exist, what each one has proved, and the ones still unwritten
> — so that nobody runs a guess with a title.
>
> **Use it when** a routine operation needs its procedure, or you want to know whether a restore has
> ever been rehearsed · **Verified** 2026-11-20 · **Kind:** living page

A runbook exists only after it has been performed at least once; a procedure nobody has run is a
guess with a title. This page lists only files that exist. Thirteen are procedures — several of them
pending their first run — and five are completed records, kept because each is the only write-up of
something that happened once; their status says so.

## Living procedures

| Runbook | Status | Use it when |
|---|---|---|
| [`authentik-apps.md`](authentik-apps.md) | proved 2026-11-15 | an application needs signing in through authentik: native OIDC where the app speaks it, the forward-auth gate where it does not. The matrix and the traps are in [`reference/sso-applications.md`](../reference/sso-applications.md). |
| [`internal-ca.md`](internal-ca.md) | proved 2026-11-15; the restore drill is pending | a `*.kub.local` certificate fails to validate, the root must be minted again, or a leaf signed. |
| [`acme-store.md`](acme-store.md) | proved 2026-11-22 (34 entries → 16) | a Let's Encrypt certificate exists for a hostname no route serves, or a retired name must stop consuming the weekly issuance limit. The store is one file on an NFS claim, edited with the router stopped. |
| [`traefik-release.md`](traefik-release.md) | proved 2026-11-21 (chart `20.3.1` → `25.0.3` → `27.0.2` → `42.4.1`, Traefik `2.9.4` → `3.7.19`) | the Traefik release has to move to a newer chart, or be inspected or rolled back. K3s's helm-controller runs nothing in this cluster, so the release is moved with `helm` by hand. |
| [`argocd.md`](argocd.md) | pending — the rebuild has not been run | the controller is down, or Argo CD must be rebuilt from nothing since it became self-managed on 2026-11-19. |
| [`postgres-add-database.md`](postgres-add-database.md) | proved 2026-11-13 (acceptance database) | an application is rebuilt and needs its own role, database and Secrets. |
| [`postgres-restore.md`](postgres-restore.md) | pending — the dumps were verified 2026-11-13, the restore itself has not been run | a database is lost, or the drill that proves the dumps restore is due. |
| [`restore.md`](restore.md) | pending — no restore has been recorded; the bucket's layout is checked against the cluster | a file, a claim, a database or the cluster state has to come back out of the OVH bucket, or the monthly restore drill is due. |
| [`alert-relay.md`](alert-relay.md) | pending — one synthetic alert through Grafana is its proof | Grafana alerts must reach the board, or the relay needs rebuilding. |
| [`renovate.md`](renovate.md) | proved 2026-11-19 | Renovate stopped opening pull requests, its token has to be rotated, or the nightly slot has to move. |
| [`gpu-operator.md`](gpu-operator.md) | pending — the chart is in git; the host driver step has not been run since | the GPU disappears from a pod, Plex stops transcoding, the DCGM GPU panels go empty, or someone asks about `nvidia.com/gpu-driver-upgrade-state`. |
| [`vikunja-database.md`](vikunja-database.md) | completed record 2026-11-16 | the board's database is rebuilt. |
| [`vikunja-first-start.md`](vikunja-first-start.md) | completed record 2026-11-16 | the board is built again from nothing, or you need what its first start checked. |
| [`vikunja-board-seed.md`](vikunja-board-seed.md) | completed record 2026-11-16 | the boards' shape must be recreated from the tracked seed, after a loss. |
| [`books-cutover.md`](books-cutover.md) | completed record 2026-11-13 | the books stack is rebuilt from nothing; it is the only write-up of the move to Calibre-Web Automated. |
| [`docs-site-first-run.md`](docs-site-first-run.md) | completed record 2026-11-17 | the docs site is rebuilt from nothing; it holds the measured first run. |
| [`cloud-monitoring.md`](cloud-monitoring.md) | proved 2026-11-20 for the probes, the alerts and the OVH configuration; the `ashuvidz.com` delegation flip has not been run | a `Cloud*` alert fires, a public name or DNS record changes, or a zone's delegation moves. |
| [`k3s-upgrade.md`](k3s-upgrade.md) | proved 2026-11-21/22, four nodes to `v1.31.14+k3s1` | K3s has to move to a new minor version, or one node is stuck on an older one. Includes the per-node timings, the 15-second ingress blip on `streaming`, and why the plan is pinned to one hostname at a time. |

## Not written yet

No file exists for these, so there is no procedure to follow — do not run one from memory.

- `restore-etcd.md` — W5 (Platform) Task 3. It is proved on the test cluster before it is written
  down.
- `add-a-node.md` — W8 (Ansible and docs).
- `rotate-a-secret.md` — W8 (Ansible and docs). No longer a gap: the procedure lives at
  `docs/running-it/rotate-a-secret.md`, which is the page the Running it tab lists.

## Conventions

Each runbook states its preconditions, the exact commands, the output that means success, and the
rollback. Anything destructive says so in the first line. When a procedure's first run has happened,
its status here becomes `proved <date>`; when it will not be run again, it keeps its history as a
completed record with the date, its body untouched, and a line saying when it is still the
reference.
