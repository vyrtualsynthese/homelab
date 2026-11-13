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

Conventions: each runbook states its preconditions, the exact commands, the output that means
success, and the rollback. Anything destructive says so in the first line.
