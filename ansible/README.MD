# Ansible

Host preparation for the six machines. **This directory does not currently work, and it could not
rebuild these hosts even if it ran.** Both statements are established in
[`docs/audit/ansible-hosts.md`](../docs/audit/ansible-hosts.md).

## Why it does not work

- **No inventory.** There is no `inventory`, no `group_vars`, no `host_vars` and no `ansible.cfg`
  anywhere in this repo or in its git history. The `Makefile` calls `ansible-playbook` without `-i`,
  so host names and connection details live outside version control and nobody can reconstruct them
  from here.
- **Missing source files.** The playbook copies `pihole.env` and `wg-easy.env`, which `.gitignore`
  excludes. It fails as written.
- **Stale for the platform.** The Docker apt repository is pinned to `focal` while the nodes run
  Ubuntu 22.04, `apt_key` is removed in current Ansible, and `upgrade-all.yaml` uses the legacy
  `docker_compose` v1 module while `setup-node.yaml` uses `docker_compose_v2`.
- **`upgrade-all.yaml` is unsafe as written.** It runs a full `apt upgrade` on all six hosts in
  parallel with no reboot, no drain and no control-plane ordering. On a three-server etcd cluster
  that is how quorum is lost.

## What it does not cover

A rebuild from this repository cannot reproduce the platform. Missing: K3s install, role assignment,
cluster token and `config.yaml`; containerd; node labels and taints; NFS exports; the GPU host stack;
a firewall; WireGuard keys and Pi-hole secrets; the System Upgrade Controller plans.

## Backups

`backup-setup.yaml` writes restic credentials in plaintext to `/etc/environment` and points every
host at an AWS S3 bucket. It copies only the runner script, and only to `rbp0` and `rbp1`, then
installs a daily cron for the whole `backup` group — so the k3s and NAS hosts have been running a
cron against a script that was never deployed to them since 2024. The k3s script dumps
`sqlite3 .../server/db/state.db`, which does not exist on an etcd cluster.

**Do not run `make backup-setup` expecting backups.** W4 replaces it entirely, against OVH.

## Intended use

```bash
cd ansible
make init          # install the required collections
make setup-node    # prompts for SSH and become passwords
```

Treat both commands as broken until W8 lands. If you need to touch a host, do it by hand and record
what you did in `docs/`.
