# Runbooks

Procedures to execute, one file per routine operation. A runbook exists only after it has been
performed at least once — a procedure nobody has run is a guess with a title.

| Runbook | Written by | Status |
|---|---|---|
| `restore-etcd.md` | W5 Task 3 | pending — proved on the test cluster first |
| `traefik-cutover.md` | W5 Task 5 | pending |
| `k3s-upgrade.md` | W5 Task 13 | pending |
| `restore.md` | W4 | pending — the offsite restore drill |
| `add-a-node.md` | W8 | pending |
| `rotate-a-secret.md` | W8 | pending |
| `books-cutover.md` | W6 Task 6 | pending — first run is the cutover it describes |
| `postgres-add-database.md` | W2 Task 7 | proved 2026-11-13 (acceptance database) |
| `postgres-restore.md` | W2 Task 7 | pending — dumps verified 2026-11-13, restore not yet run |
| `authentik-apps.md` | media-tools SSO | proved 2026-11-15 — ten tools gated, calibre over OIDC |
| `internal-ca.md` | media-tools SSO | proved 2026-11-15 — root minted, leaf signed; the restore drill is still pending |
| `vikunja-database.md` | vikunja board | pending — run before the board's first start |
| `vikunja-first-start.md` | vikunja board | pending — the first run is its own proof |
| `vikunja-board-seed.md` | vikunja board | proved 2026-11-16 — eight boards shaped, `--verify` all PASS |
| `alert-relay.md` | alerts to board | pending — the synthetic alert through Grafana is its proof |

Conventions: each runbook states its preconditions, the exact commands, the output that means
success, and the rollback. Anything destructive says so in the first line.
