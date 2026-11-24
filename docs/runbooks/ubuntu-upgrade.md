# Moving Ubuntu to a new LTS

> **In one line.** Four nodes take two release hops each, in place, one node at a time, with the cluster
> watching each one come back before the next starts.
>
> **Use it when** a cluster node has to move to the next Ubuntu LTS. The two edge Pis do not take this
> route: they are reflashed — [reflashing an edge Pi](pi-reflash.md) · **Verified** 2026-11-24: the
> releases offered and the state of all six hosts read from the machines; no node has taken the path
> below · **Kind:** how-to
>
> **Status:** pending — written from the design, the measured hosts and the runbooks it links to, not
> from a run. The first node makes it `proved`.
>
> **Sources** [`docs/superpowers/specs/2026-11-24-hosts-stabilise-and-ubuntu-upgrade-design.md`](../superpowers/specs/2026-11-24-hosts-stabilise-and-ubuntu-upgrade-design.md),
> `ansible/playbooks/site.yml` and `ansible/inventory/`, [`docs/audit/hosts-2026-11-24.md`](../audit/hosts-2026-11-24.md),
> [moving K3s to a new minor](k3s-upgrade.md), [restore from a backup](restore.md)

## What this touches

Four machines, all on Ubuntu 22.04.5 LTS on 2026-11-24, each offered **24.04.5** by the release check:
`nuc0` and `streaming` (control plane with etcd), `rbp2` (control plane with etcd, ARM64) and `nas`
(worker, the NFS exports and the disk union). What each one runs is in
[the machines](../how-it-works/nodes.md).

**Ubuntu does not skip an LTS**, so each host runs `do-release-upgrade` twice: 22.04 → 24.04.5, then
24.04.5 → 26.04.1. There is no single command and no supported shortcut.

| Order | Node | Hops | Why here |
|---|---|---|---|
| 1 | `nuc0` | two | a server whose claims can be rebuilt from `nodes-nuc0`, and neither the GPU host nor the storage host |
| 2 | `streaming` | two | the driver is the open question: if the module will not build, this is the cheapest place to stop |
| 3 | `rbp2` | two | after the two x86 servers have proved the path, on the ARM64 member |
| 4 | `nas` | two | last, always: the exports, the 51 TB union and the parity above it |

## Before you start

Each node's gate, in this order, recorded in the card:

- **Zero pending upgrades**: `apt-get -s dist-upgrade` prints `0 upgraded`. A release upgrade on top of a
  hundred held-back packages is the upgrade you cannot read.
- **The layer agrees with the machine**: `ansible-playbook playbooks/site.yml --limit <node> --check`
  exits 0. It is the drift report, and a host that drifted before a release move drifts past it.
- **A collector run on that node**, kept for the comparison after each hop.
- **That node's backup stream green within 24 h.** A release upgrade is the night you want the previous
  night's copy; `BackupStreamStale` firing mid-hop is a stop.
- **etcd healthy before a server moves**: `kubectl get --raw /healthz/etcd` answers `ok`. Quorum is two of
  three, and no two control planes move in the same evening.
- **`rbp2`'s boot partition**: 26.04 writes a kernel and firmware into `/boot/firmware` before anything
  else, and 64 MB of 253 MB was free on 2026-11-24. At least 120 MB, measured, or the hop does not start.
- **`streaming`'s GPU**: driver 580.178.04 is the last branch NVIDIA ships for the Quadro P400, and its
  module is DKMS-built. The 26.04 hop on this host is gated by the experiment that builds that module
  against the new kernel; if it does not compile, `streaming` stops after hop 1 and stays on 24.04, which
  is supported to 2029. **That is a decision, and it is recorded before the node is touched, not at the
  reboot.**

## One node at a time

Never two. Between nodes: a collector run, the next node's gates above, and `kubectl get nodes` showing
every node `Ready` on the same K3s version.

**Nothing here moves K3s.** The binary is not a package the release upgrade owns; it follows its own
procedure, and 1.37 is a decision of its own — [moving K3s to a new minor](k3s-upgrade.md).

1. **Drain it, from another node.**

   ```bash
   kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=600s
   ```

   Draining `nas` stops Prometheus, Grafana, Loki and PostgreSQL for the length of the move; draining
   `streaming` stops the media stack and Traefik, which is every route into the cluster. Do it when that
   costs least.

2. **Open a tmux session on the host before the first command.** `do-release-upgrade` restarts sshd and
   can take half an hour; a dropped SSH session in the middle of it is the failure this step prevents.

   ```bash
   ssh <node>
   tmux new -s upgrade
   sudo do-release-upgrade
   ```

3. **Hop 1, to 24.04.5.** Confirm what is offered first — `do-release-upgrade -c` — and stop if it offers
   anything but an LTS. It asks, in this order: continue over SSH (yes, inside tmux), whether to start the
   upgrade, what to do with each configuration file, and whether to remove obsolete packages.

   **Keep the local file** at every configuration prompt. `/etc/ssh/sshd_config` and `/etc/default/ufw`
   carry what the `accounts` and `firewall` roles wrote, and on `nas` `/etc/exports` and `/etc/fstab` carry
   the eight narrowed exports and the mergerfs mount. The package's version re-opens password logins,
   re-defaults the firewall policy, and replaces the exports with a file the cluster does not mount.
   **Say no** to removing obsolete packages: the layer's own trim owns the kernel set, and an autoremove
   in the middle of a release move is how the running kernel leaves.

4. **Reboot when it asks, and verify the hop before starting the next one.**

   ```bash
   lsb_release -ds                 # Ubuntu 24.04.5 LTS
   uname -r                        # the 24.04 kernel
   systemctl is-active k3s || systemctl is-active k3s-agent   # the worker runs the agent
   kubectl get nodes -o wide       # this node Ready, same version as the rest
   sudo ufw status verbose         # still active, still the layer's rules
   timedatectl | grep 'Time zone'  # Europe/Paris, not UTC
   ```

   On `streaming` add: `nvidia-smi -L` names the Quadro P400 and `dkms status` shows the 580 module built
   for the new kernel. On `nas` add: `snapraid status` reports `786,116` files with no out-of-date parity,
   and a file written to `/mnt/storage` reads back with the right content.

5. **Hop 2, to 26.04.1.** Check that the release check now offers it — `do-release-upgrade -c` — then the
   same command, from the same tmux session, with the same answers. Then the same verification, plus the one
   check that matters most on `streaming`:

   ```bash
   kubectl get node streaming -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'   # expect 1
   ```

   A GPU pod can be `Running` and `nvidia-smi` can answer while the scheduler's resource reads 0 — that
   fault has a recovery of its own in [the GPU stack](gpu-operator.md).

6. **Uncordon and watch it take work back.**

   ```bash
   kubectl uncordon <node>
   kubectl get pods -A --field-selector spec.nodeName=<node> --no-headers | grep -v -E 'Running|Completed'
   kubectl get --raw /healthz/etcd                    # ok
   kubectl get nodes -o wide                          # the node Ready
   ```

   Pods that held NFS mounts to `nas` through its reboot can come back with stale handles; a pod that stays
   `CreateContainerConfigError` or whose volume mount fails is deleted once so the kubelet mounts it
   again, and the failure is written down rather than worked around.

7. **The host, not just the cluster.** Run `ansible/collectors/host-audit.sh` on the node and diff it
   against the run taken before, then:

   ```bash
   cd ansible && ansible-playbook playbooks/site.yml --limit <node> --check   # changed=0
   ```

   Expect drift the first time: the release published its own `sshd_config`, ufw defaults and apt
   configuration, and the layer reports what it would put back. Apply it with `--limit <node>`, re-run to
   `changed=0`, and add the drift to the node's card as a finding rather than an accident.

## After every node

- The collector report beside the 2026-11-24 record, and the measured line in
  [the machines](../how-it-works/nodes.md) updated in the same pull request.
- The next node's gates from [Before you start](#before-you-start).
- That node's backup job re-run once by hand, its log read for a saved snapshot, then the job deleted. The
  release changed the host under the backup, and a re-run is the cheap proof that the mounts, the SQLite
  staging and restic still work there.

## Rolling back

**There is no downgrade.** Ubuntu has no supported path back one release, `do-release-upgrade` keeps no
copy of what it replaced, and a host that took the hop has taken it. Everything below limits the damage;
none of it undoes the hop.

- **The copy is the rollback.** The gate is that node's restic stream being green within 24 h. A claim that
  did not survive comes back with [restore from a backup](restore.md), onto the same volume.
- **A half-finished upgrade** is a host that is up with packages `half-configured`, not a broken one:
  `sudo dpkg --configure -a`, then `sudo apt-get -f install`, then re-run the upgrade.
- **A kernel that will not boot** is the one step back that exists, and only for the kernel: the layer
  keeps the running kernel and the previous one, so GRUB's older entry boots the same release.
- **A host that does not come back at all** is a rebuild: the 26.04 image, `site.yml` for the host layer,
  and that node's claims from `nodes-<host>`. This has not been rehearsed on a cluster node, and it is
  the reason `nas` is last.

## Traps

| Symptom | What it is |
|---|---|
| the upgrade stops at a configuration prompt nobody answered | the prompt is answered at the keyboard, not from scrollback: keep the local file, on every host, every time |
| `ufw` reports the default policy back to `allow` after the reboot | the package's `/etc/default/ufw` won the diff; re-apply `--limit <node> --tags firewall` and check the read-back in the role |
| `kubectl` cannot reach the API mid-hop | the kubeconfig names the node being moved; use `--server` against another control plane, as in [the K3s runbook](k3s-upgrade.md) |
| a node comes back `Ready` on the old kernel | the upgrade installed 26.04 and the firmware still boots the old entry: `uname -r` before `uncordon`, not after |
| `nas` serves an empty `/mnt/storage` after the hop | mergerfs did not mount; the disks are fine and the union is missing — `mount -a`, then `snapraid status` |
| `nvidia.com/gpu` reads 0 with the driver answering | the device plugin has not re-advertised; [the GPU stack](gpu-operator.md) has the one-pod recovery |
