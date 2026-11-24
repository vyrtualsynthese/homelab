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

## Seeing the network hardware without touching it

`collectors/network-audit.sh` reads the two MikroTik switches and prints one Markdown page. Each
switch gets the device's own `/export`, a status block with its identity, model, serial, RouterOS
version and uptime, and a table of the ports, with link state and negotiated speed. After the two
switches comes the Livebox: the status its login page answers with, the firmware build named in that
page, and the port forwards, marked as remembered rather than read because they need the admin
password. What looks like a credential inside an export comes out as `<redacted>`.

Nothing is written and nothing is changed: the switch half runs `/print`, `/export` and one port-state
query each, and the Livebox gets a single unauthenticated GET.

It signs in with a key, as a RouterOS user that can sign in, print and export, and change nothing else.
That is the `read` policy plus the `ssh` one, because RouterOS counts SSH access as a policy of its
own: a group holding `read` alone cannot open the session.

Make the pair on your machine with no passphrase, copy the public half to the switch with an account
that can still sign in, and open a session there:

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/network-audit
scp ~/.ssh/network-audit.pub admin@192.168.1.11:
ssh admin@192.168.1.11
```

Then in that session, once per switch:

```
/user group add name=audit policy=read,ssh
/user add name=audit group=audit
/user ssh-keys import public-key-file=network-audit.pub user=audit
```

The import comes last because it names the account, which has to exist by then. The script reads
`~/.ssh/network-audit` unless `NET_AUDIT_KEY` names another file, and a RouterOS build too old to
import an ed25519 key needs an RSA one: `ssh-keygen -t rsa -b 4096 -N '' -f ~/.ssh/network-audit`.

Then, from this directory:

```bash
./collectors/network-audit.sh > /tmp/opencode/network-audit.md
NET_AUDIT_FROM_FIXTURE=../hack/fixtures/network ./collectors/network-audit.sh   # no device touched
```

The default targets are `crs326=192.168.1.11` and `crs309=192.168.1.43`, which `NET_AUDIT_TARGETS`
overrides; `./collectors/network-audit.sh --help` lists the rest of the variables. The page goes to
stdout, and a device that does not answer is reported in it rather than on the terminal: the exit is 0
when at least one switch answered it, 1 when none did, 2 on a usage error.

<!-- Do not cite a snapshot page from here until one exists: `hack/check_docs.py` resolves every
`docs/*.md` path in the repository and fails on one that is not there yet. -->

