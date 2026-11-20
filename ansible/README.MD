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

This directory holds no backup code any more. `backup-setup.yaml`, `restic_env.yaml.dist` and the
three `backup-scripts/` were removed on 2026-11-20: they wrote AWS credentials in plaintext to
`/etc/environment`, dumped a SQLite `state.db` that an etcd cluster does not have, and backed up a
MinIO path deleted in July 2024.

Backups are `cluster/helm/backup` in the homelab repository: one OVH bucket in `GRA`, four restic
repositories, a nightly job per node plus one for the NFS exports. Its coverage matrix is
`cluster/helm/backup/values.yaml`, and [`docs/how-it-works/backups.md`](../docs/how-it-works/backups.md)
explains it.

What this playbook used to cover on the edge, and still does not: the two Pis' data. Their compose
stacks are in `playbooks/templates/runners/`, so the configuration is in git; anything they keep
outside those files is not backed up anywhere, by decision, until an edge stream exists.

## Intended use

```bash
cd ansible
make init          # install the required collections
make setup-node    # prompts for SSH and become passwords
```

Treat both commands as broken until W8 lands. If you need to touch a host, do it by hand and record
what you did in `docs/`.
