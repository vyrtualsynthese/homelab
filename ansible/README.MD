# Ansible

Host preparation for the six machines: four K3s nodes upgraded in place, two edge Pis reflashed.
Everything a host needs that is not in a container is described here, and `make check` is what says
whether a host still matches.

```bash
cd ansible
make init                      # once: install the collections the roles use
make check                     # the drift report, --check --diff, changes nothing
make apply                     # converge a host
make adopt                     # print this laptop's key line for trusted_ssh_keys
make lint                      # ansible-lint over the whole layer
```

`make init` pins each collection to a range, not to "latest": `community.general` 14 needs
ansible-core 2.19, and this controller is 2.18, so an unpinned install prints a support warning on
every run and then uses a collection built against a newer core. Bump a range only after
`ansible --version` on the machine that runs it.

**The rule: `make check` is the drift report, and a host that is correct prints no `changed`.** A
`changed` line is either a host drifting from this repository or a task that is not idempotent; both
are defects to read, not noise to tolerate.

`make check` and `make apply` reach every host in the inventory. To work one host at a time — which is
how the first run of a role that touches a firewall or an export list must be done — pass a limit to
`ansible-playbook` directly: make has no `--limit`, refuses the argument, and exits before running the
play. The two commands below are the form.

The two edge Pis are the exception until they are reflashed: the 24.04 image has no `ansible`
account, so a run that includes them answers `unreachable` for those two hosts and exits non-zero.
Use `--limit cluster` for the drift report until the reflash has put the account on both (card #481),
and read an `unreachable` there as this state, not as drift.

```bash
ansible-playbook playbooks/site.yml --check --diff --limit nuc0
ansible-playbook playbooks/site.yml --limit nuc0
```

**A host's first run is two steps, in this order.** The layer removes
`%admins ALL=(ALL) NOPASSWD: ALL`, which is the only passwordless path the human account has, and the
`become` of the run that removes it then asks for a password no unattended run can type — the first
apply of this layer died there with `Missing sudo password`. So the grant moves to one named account,
`ansible` (`automation_user`), and that account has to exist before the human grant goes:

```bash
cd ansible
# 1. create the automation account, and touch nothing else
ansible-playbook playbooks/site.yml -e ansible_user=vronteix --limit nuc0 --tags automation
# 2. converge the host as that account; this is the run that removes the human grant
ansible-playbook playbooks/site.yml --limit nuc0
```

Step 1 is the only command in this directory that names the human account, and the only one an agent
cannot run on a host where the grant is already gone: that host needs `-K` and a typed password, so it
is the operator's. A reflashed Pi needs both steps again, because the image has neither account.

`roles/automation` and `roles/accounts` write the same declared list, `trusted_ssh_keys`, into
`ansible`'s and `vronteix`'s `authorized_keys`: one line per laptop, and a key that is not in the list is
removed by the next run. `make adopt` prints the line for the machine you are on. The procedure is
[`docs/runbooks/ansible-laptop-onboarding.md`](../docs/runbooks/ansible-laptop-onboarding.md).

The trade — passwordless root for one named identity instead of a group — is
[`docs/decisions/2026-11-24-automation-sudo-account.md`](../docs/decisions/2026-11-24-automation-sudo-account.md).

## What is here

| Path | What it is |
|---|---|
| `inventory/hosts.yml` | the six hosts, in four groups. An address is written here and nowhere else |
| `inventory/group_vars/`, `inventory/host_vars/` | the values per group and per host; beside the inventory, where Ansible reads them |
| `playbooks/site.yml` | the one entry point; every host takes every role, and a role skips what it does not own |
| `roles/` | one role per concern, below |
| `pi/hosts-pi.sh` | the two Pis, which are reflashed and so cannot be configured by Ansible — see `pi/README.md` |
| `collectors/host-audit.sh` | a read-only report of what a host actually is |

| Role | Hosts | What it changes |
|---|---|---|
| `baseline` | all | marks the host as managed from here |
| `accounts` | all | removes the `%admins ALL=(ALL) NOPASSWD: ALL` grant, the group, cloud-init's grant for the retired `ubuntu` account and that orphan home, closes password logins, turns X11 forwarding off |
| `packages` | all | removes what `inventory/host_vars/<host>.yml` names, after resolving each pattern against dpkg |
| `system` | all | one time zone, two kernels kept (the running one and the newest other), `apt autoremove --purge`, the `rc` residue purged |
| `firewall` | all | `ufw`, default deny in, the K3s and NFS ports opened to the addresses that use them |
| `union` | `storage` | the mergerfs pool's line in `/etc/fstab` and its two runtime options: `moveonenospc`, `minfreespace` |
| `nfs` | `storage` | the eight exports the cluster mounts, root squashed, `sync` |
| `snapraid` | `storage` | a nightly sync from cron, without the Python 2 runner it used to call |

The roles are ordered in `site.yml`: `packages` before `system`, because the removals create orphans
and the system role is the one that collects them after it has named the running kernel.

## What it deliberately does not do

- **No release upgrade.** Nothing here runs `do-release-upgrade`. Moving a host between Ubuntu
  releases is a decision with a reboot, a rollback and a node drain attached, and the runbooks are
  `docs/runbooks/k3s-upgrade.md` and (for the Pis) `pi/README.md`.
- **No K3s install, no cluster token, no node labels.** The cluster belongs to
  `cluster/argocd` and `cluster/helm`; this layer is the host under it.
- **No backups.** Those are `cluster/helm/backup`, one OVH bucket in `GRA`, four restic repositories
  and a nightly job per node. [`docs/how-it-works/backups.md`](../docs/how-it-works/backups.md) is the
  page.
- **No secrets.** The Pis' `pihole.env` and `wg-easy.env` are gitignored and the script requires them
  rather than writing them; every other secret is a Kubernetes Secret, listed in `docs/secrets.md`. The
  keys in `trusted_ssh_keys` are public halves: they name a laptop and cannot log in without the private
  half, which never leaves the machine that made it.
- **No rule for a published container port.** Docker's DNAT and the CNI's portmap are evaluated in NAT
  and FORWARD, where `ufw`'s INPUT never looks. The role says so where its rules are, and names
  `DOCKER-USER` as the place those are filtered.

## Seeing a host without touching it

`collectors/host-audit.sh` reads a host and prints a Markdown report — packages, kernel and sysctl,
services and timers, network, firewall, storage and exports, containers, K3s, GPU, Pi firmware, SSH and
sudoers, upgrade readiness, recent errors — and writes nothing, starts nothing, changes nothing:

```bash
mkdir -p /tmp/opencode/host-audit
sudo ./collectors/host-audit.sh > /tmp/opencode/host-audit/$(hostname).md
sudo AUDIT_NETWORK=1 ./collectors/host-audit.sh   # also asks apt and do-release-upgrade -c
```

Every value passes through a redactor before it is printed: a secret's value becomes `<redacted>` and
its key stays, so a report line reads `token: <redacted>`. `hack/tests/test_host_audit.py` holds the
proof in both directions — the shapes it must remove (K3s token, WireGuard keys, env files, URL
credentials, presigned URLs, JWTs, PEM blocks) and the settings it must leave alone
(`PasswordAuthentication no`, `vm.swappiness = 60`, a sudoers line). A non-root run still prints, marks
what it could not read, and is not worth trusting until it is rerun under `sudo`.

## What this replaced, and why

Until 2026-11-24 this directory was not runnable. There was no inventory and no `ansible.cfg` anywhere
in the repo or its history, so host names and connection details lived outside version control;
`setup-node.yaml` copied `pihole.env` and `wg-easy.env`, which `.gitignore` excludes, and wrote
`%admins ALL=(ALL) NOPASSWD: ALL`; the Docker apt repository was pinned to `focal`; `apt_key` was used
after Ansible removed it; and `upgrade-all.yaml` ran a full `apt upgrade` on all six hosts at once with
no reboot, no drain and no control-plane ordering, which on a three-server etcd cluster is how quorum
is lost. Both playbooks are deleted. The evidence for each statement is in
[`docs/audit/ansible-hosts.md`](../docs/audit/ansible-hosts.md), and the work that replaced them is
[`docs/superpowers/plans/2026-11-24-hosts-stabilise.md`](../docs/superpowers/plans/2026-11-24-hosts-stabilise.md).

Two earlier files went the same way on 2026-11-20: `backup-setup.yaml`, `restic_env.yaml.dist` and the
three `backup-scripts/` wrote AWS credentials in plaintext to `/etc/environment`, dumped a SQLite
`state.db` an etcd cluster does not have, and backed up a MinIO path deleted in July 2024.

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

