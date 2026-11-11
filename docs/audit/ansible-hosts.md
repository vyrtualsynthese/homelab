# Audit — Ansible and host layer

> **Snapshot, not a live view.** Captured 2026-11-10 against the cluster and the repo at that
> moment. Findings are fixed by the plans in `docs/superpowers/plans/`, not by editing this file.
> References to `REBUILD.md` or `TODO.MD` point at files deleted on 2026-11-11; recover them
> with `git show 6be598ab:REBUILD.md`.

Read-only audit, 2026-11-10. Repo root `.../worktree/117be0/agent1`.

## 1. The playbooks cannot run from this repository

There is **no inventory file, no `group_vars`, no `host_vars`, no `ansible.cfg`** anywhere in the
repo or in its git history. `ansible/Makefile` invokes `ansible-playbook playbooks/<x>.yaml -kK`
with no `-i`, so hosts must come from a default inventory that lives outside the repo.

Playbooks target these groups, all of which exist only out-of-band: `all`, `k8s_agent`, `rbp0`,
`runner`, `backup`.

Consequence: the repository does not describe the machines it manages. A rebuild from git is
impossible without reconstructing the inventory by hand.

Connection settings: `remote_user: vronteix`, `become: yes` on every play; control-node keys
`/home/vyrtualsynthese/.ssh/homelab_rsa.pub` and `homelab_live.pub` (`setup-node.yaml:38-39`).

## 2. `setup-node.yaml` (216 lines)

Does: admins group plus NOPASSWD sudoers, full `apt upgrade`, `authorized_key`, SSH hardening
(`PermitRootLogin no`, `PasswordAuthentication no`), journald 1G config, packages (`open-iscsi`,
`nfs-common`, `linux-generic`, `linux-modules-extra`), a Longhorn multipath blacklist for
`k8s_agent`, Docker install for runners, Pi-hole and wg-easy compose deployment, netplan and
systemd-resolved stub disable.

Does **not** do: timezone, NTP, firewall, swap, sysctl tuning, kernel modules, containerd,
unattended-upgrades, or any GPU setup.

Defects:

- `apt_key` at `:125` — deprecated, removed in modern Ansible.
- Docker apt repository hardcoded to `focal` at `:131` — the nodes run Ubuntu 22.04 (jammy).
- `command: sed`, `rm && ln`, `systemctl restart docker` where modules exist (`:170`, `:180`,
  `:183`).
- A journald handler that never fires, and a whole-file journald overwrite instead of a drop-in.
- **Missing source files**: the Pi-hole and wg-easy tasks copy `pihole.env` and `wg-easy.env`,
  which are gitignored (`.gitignore:21`). The playbook fails as written.
- The `k8s_agent` multipath task fails if `/etc/multipath.conf` does not exist (inference).

## 3. `upgrade-all.yaml` (40 lines)

`apt upgrade: full` across **all six hosts in parallel**, autoclean, autoremove, `restic
self-update`, then `docker compose pull` through the **legacy v1 `community.docker.docker_compose`
module** while `setup-node.yaml` uses `docker_compose_v2`.

No reboot, no drain, no control-plane ordering, no K3s or PVC health check. Running it as written
restarts the runners' Docker daemon (Pi-hole and WireGuard blip for the whole LAN) and upgrades
the three control-plane hosts simultaneously, which is how you lose etcd quorum.

The K3s binary is untouched — it is not apt-managed.

## 4. Backups: broken since 2024, provably

- `backup-setup.yaml` installs restic, then writes `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID` and
  `AWS_SECRET_ACCESS_KEY` **in plaintext into `/etc/environment`**, and points every host at
  `s3:s3.amazonaws.com/ashudev-homelab-<host>-backup` (`:44-45`).
- It copies **only** `runner/backup.sh`, and only for `rbp0` and `rbp1` (`:56-63`).
- It then installs a **daily 04:00 root cron for the whole `backup` group** (`:65-71`). The k3s and
  NAS hosts get a cron entry pointing at a script that was never deployed to them.
- Git history shows the `k3s/backup.sh` copy task was **removed on 2024-01-31** and the `nas/`
  task on **2024-07-07**. The scripts still in `playbooks/scripts/backup-scripts/` are therefore
  dead code.
- `k3s/backup.sh` dumps `sqlite3 /var/lib/rancher/k3s/server/db/state.db`. This cluster is
  three-server HA, i.e. embedded etcd; there is no `state.db`. **This script has never produced a
  usable control-plane backup.**
- `nas/backup.sh` backs up exactly one path: `/mnt/storage/minio/.minio.sys`. MinIO was removed
  in July 2024. **It now backs up nothing.**
- No Longhorn volume, no PVC data, no database dump is in any script.
- `restic_env.yaml.dist` documents `rbp0` and `rbp1` only. The k3s and NAS hosts have no entry
  and no documented procedure.

Net: the only working offsite path is runner config from two Pis, into an AWS S3 bucket.

## 5. Runner templates

`rbp0` and `rbp1` compose files define Pi-hole (`jacklul/pihole:2024.07.0`), wg-easy v14 (ports
51821/51822); `rbp0` additionally runs Traefik v3.3.3, node-exporter and smartctl-exporter.

Wildcard DNS is in code: `templates/runners/02-my-wildcard-dns.conf` maps `livebox.local`,
`kub.local` → 192.168.1.46, `rbp0.local` → .22, `rbp1.local` → .38,
`kub.home.ashudev.com` → 192.168.1.46, `local-ttl=86400`.

**Not in code**: DNS upstreams, WireGuard peers and keys, Pi-hole admin password. They live in
gitignored `.env` files and host-local `./config` directories.

## 6. Staleness

Git history: playbooks created November 2021, reworked January 2024, last substantive change
June–July 2024, minor touches January/February 2025. Effectively **18–24 months stale**.

Evidence: Ubuntu `focal` (20.04) repository, `apt_key`, legacy `docker_compose` module, MinIO
backup target, historical hostnames `xps13` and `backup_slave`, pinned 2024 image tags,
unpinned `ansible-galaxy collection install` with no `requirements.yml`.

## 7. What a rebuild would not reproduce

Nothing in this repository can rebuild the platform. Missing:

1. K3s: install method, `server`/`agent` role assignment, cluster token, `--cluster-init`,
   `/etc/rancher/k3s/config.yaml`, `disable: [traefik]`.
2. High availability: no kube-vip or any VIP; grep returns zero hits.
3. Node labels and taints (`node-type=general` is required by several charts and exists only on
   the live cluster).
4. NFS exports: `/etc/exports` is not in the repo.
5. GPU host preparation — only `kubectl/gpu-operator-values.yaml` exists, no driver install.
6. WireGuard keys and Pi-hole configuration.
7. A firewall rule set of any kind.
8. System Upgrade Controller plans (cluster-only, per `REBUILD.md:23`).

## 8. Decisions this forces

- The inventory must enter the repo (or be documented as deliberately external with a template).
- `upgrade-all.yaml` cannot be used as-is on the control plane; it needs serial ordering, drain,
  reboot and version pinning, or it must be replaced by the System Upgrade Controller path.
- The backup workstream is a **greenfield build**, not a repair: no existing script covers the
  control plane, PVC data or databases, and the destination is AWS.
