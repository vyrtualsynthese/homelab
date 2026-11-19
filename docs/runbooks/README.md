# Runbooks

> **In one line.** The procedures that exist, what each one has proved, and the ones still unwritten
> — so that nobody runs a guess with a title.
>
> **Use it when** a routine operation needs its procedure, or you want to know whether a restore has
> ever been rehearsed · **Verified** 2026-11-17 · **Kind:** living page

A runbook exists only after it has been performed at least once; a procedure nobody has run is a
guess with a title. This page lists only files that exist. Seven are procedures — four of them
pending their first run — and five are completed records, kept because each is the only write-up of
something that happened once; their status says so.

## Living procedures

| Runbook | Status | Use it when |
|---|---|---|
| [`authentik-apps.md`](authentik-apps.md) | proved 2026-11-15 | an application needs signing in through authentik: native OIDC where the app speaks it, the forward-auth gate where it does not. The matrix and the traps are in [`reference/sso-applications.md`](../reference/sso-applications.md). |
| [`internal-ca.md`](internal-ca.md) | proved 2026-11-15; the restore drill is pending | a `*.kub.local` certificate fails to validate, the root must be minted again, or a leaf signed. |
| [`argocd.md`](argocd.md) | pending — the rebuild has not been run | the controller is down, or Argo CD must be rebuilt from nothing since it became self-managed on 2026-11-19. |
| [`postgres-add-database.md`](postgres-add-database.md) | proved 2026-11-13 (acceptance database) | an application is rebuilt and needs its own role, database and Secrets. |
| [`postgres-restore.md`](postgres-restore.md) | pending — the dumps were verified 2026-11-13, the restore itself has not been run | a database is lost, or the drill that proves the dumps restore is due. |
| [`alert-relay.md`](alert-relay.md) | pending — one synthetic alert through Grafana is its proof | Grafana alerts must reach the board, or the relay needs rebuilding. |
| [`renovate.md`](renovate.md) | pending — one hand-triggered run is its proof | Renovate stopped opening pull requests, its token has to be rotated, or the nightly slot has to move. |
| [`vikunja-database.md`](vikunja-database.md) | completed record 2026-11-16 | the board's database is rebuilt. |
| [`vikunja-first-start.md`](vikunja-first-start.md) | completed record 2026-11-16 | the board is built again from nothing, or you need what its first start checked. |
| [`vikunja-board-seed.md`](vikunja-board-seed.md) | completed record 2026-11-16 | the boards' shape must be recreated from the tracked seed, after a loss. |
| [`books-cutover.md`](books-cutover.md) | completed record 2026-11-13 | the books stack is rebuilt from nothing; it is the only write-up of the move to Calibre-Web Automated. |
| [`docs-site-first-run.md`](docs-site-first-run.md) | completed record 2026-11-17 | the docs site is rebuilt from nothing; it holds the measured first run. |

## Not written yet

No file exists for these, so there is no procedure to follow — do not run one from memory.

- `restore-etcd.md` — W5 (Platform) Task 3. It is proved on the test cluster before it is written
  down.
- `traefik-cutover.md` — W5 (Platform) Task 5.
- `k3s-upgrade.md` — W5 (Platform) Task 13.
- `restore.md` — W4 (Offsite). The offsite restore drill.
- `add-a-node.md` — W8 (Ansible and docs).
- `rotate-a-secret.md` — W8 (Ansible and docs). No longer a gap: the procedure lives at
  `docs/running-it/rotate-a-secret.md`, which is the page the Running it tab lists.

## Conventions

Each runbook states its preconditions, the exact commands, the output that means success, and the
rollback. Anything destructive says so in the first line. When a procedure's first run has happened,
its status here becomes `proved <date>`; when it will not be run again, it keeps its history as a
completed record with the date, its body untouched, and a line saying when it is still the
reference.
