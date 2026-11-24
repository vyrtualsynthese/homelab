# Hosts: stabilise, then move to Ubuntu 26.04.1 LTS — design

**Status:** draft, awaiting approval · **Date:** 2026-11-24 · **Board:** homelab, cards #480 and #481
**Live basis (measured 2026-11-24):** six collector runs of `ansible/collectors/host-audit.sh`, recorded
in [`docs/audit/hosts-2026-11-24.md`](../../audit/hosts-2026-11-24.md). Every host fact below is from
there; nothing is inferred from the playbooks, which have not run in years.

## Intent

Six machines, one destination: **Ubuntu 26.04.1 LTS**, because only LTS is used here. Each carries
hand-made configuration that exists nowhere in git, so a reinstall loses it and an in-place move drags
it through two release hops where nobody is looking. The work therefore has two halves, and they are
two cards:

1. **Stabilise** (#480) — every host ends clean, known-good and *upgrade-ready* on the release it is on
   today. No host changes release. This is where the accumulated damage is repaired: the cron that
   fails silently, the packages nothing uses, the settings nobody chose.
2. **Move** (#481) — all six reach 26.04.1 LTS, by the route each half allows.

Both are blocked-and-blocking in that order on the board: #481 cannot start before #480 is `Done`.

The backlog this replaces is [the ansible audit](../../audit/ansible-hosts.md) of 2026-11-23, which
found the `ansible/` layer not runnable at all.

## The starting state

| Host | Release today | Offered | What it holds that a reinstall would lose |
|---|---|---|---|
| `nuc0` | 22.04.5 | 24.04.5 | an etcd member; ten `local-path` claims; the observability stack's Prometheus and Loki data |
| `streaming` | 22.04.5 | 24.04.5 | an etcd member; the NVIDIA DKMS build; the media stack's configuration claims; the GPU |
| `rbp2` | 22.04.5 | 24.04.5 | an etcd member, on ARM64, with no container runtime |
| `nas` | 22.04.5 | 24.04.5 | the storage: six ext4 disks, an LVM volume, a 51 TB mergerfs union, snapraid parity, nine NFS exports |
| `rbp0` | **24.04.4** | **26.04.1** | Pi-hole's blocklists and query database, the WireGuard peer set, a compose file |
| `rbp1` | 22.04.5 | 24.04.5 | the same, on the second Pi |

Two facts shape everything downstream:

- **`rbp0` is already a release ahead.** It is on noble and `do-release-upgrade -c` offers 26.04.1
  directly, so "upgrade everything to 24.04 first" is not one command on any two hosts.
- **The two Pis boot from USB.** `sda` is a 1 TB Samsung SSD on the USB bus, `sda1` is the 256 MB
  `system-boot` FAT mounted at `/boot/firmware`, and `sda2` is the root. The board's boot order and
  EEPROM live on the Pi itself and survive an image write, so a reflash is a write to that disk and
  nothing else.

## Decisions

**D1 — the destination is 26.04.1 LTS, reached in two hops.** One hop for `rbp0`; two for the other
five, 22.04 → 24.04.5 → 26.04.1. Ubuntu does not support skipping an LTS. The alternative — hold
everything on 24.04 — was rejected because the operator uses LTS and the newest LTS is the point.

**D2 — the four cluster nodes move in place, the two Pis are reflashed.** This is the whole design in
one line.

- The cluster nodes hold state an installer cannot be trusted with: three etcd members where quorum
  is two of three, six `local-path` claims holding Prometheus's ninety days, the NVIDIA module built
  by DKMS, and the entire storage host. In place is also the only route on which the *measured* state
  is what moves, which is what makes the collector worth running.
- The Pis hold a compose file and two env files. In place would take them through a release upgrade
  twice as often for no benefit, and leave the previous release's residue on a disk that exists to
  run four containers. A fresh image is smaller, faster to verify and easier to repeat — as long as
  the state is copied off first, which is what the script's `backup` verb is for.

**D3 — configuration as code is a thin Ansible layer over what is already there, not a rebuild.** The
existing `ansible/` is not a starting point: `setup-node.yaml` has no inventory, writes the
`%admins ALL=(ALL) NOPASSWD: ALL` grant this repository now wants gone, references two `.pub` paths
under another user's home, and sets no `ansible.cfg`. `upgrade-all.yaml` upgrades all six at once with
no drain or ordering. Both are replaced, not extended.

The layer's job is narrower than "provision the host". It is: **declare the settings a host must have,
prove with `--check` that it has them, and apply them when it does not.** Everything the apps do stays
in Kubernetes and git; everything the *host* does — SSH policy, sudo, time zone, the package floor, the
kernel it may keep — becomes a role.

**D4 — the two Pis get one script, not a role.** They are outside the cluster, they are reflashed rather
than patched, and they are the only machines where a human is physically present. A script with four
verbs is the whole interface:

```
hosts-pi.sh setup          # bring a freshly flashed Pi to the declared state
hosts-pi.sh verify         # exit non-zero if it differs; print every difference
hosts-pi.sh backup  <dir>  # copy the state a reflash destroys into <dir>
hosts-pi.sh restore <dir>  # put it back, then verify
```

`setup` writes the compose file and env files from `ansible/playbooks/templates/runners/<host>/`,
starts the stack, and refuses to run if the hostname does not match the directory it came from. It is
idempotent: a second `setup` changes nothing, and `verify` is the same code with the writes removed.
Since Ansible is not installed on a fresh Pi, the script is bash — and it is the same script the
operator runs today, made repeatable.

**D5 — what is deliberately not automated.** The release upgrade itself. `do-release-upgrade` asks
questions, restarts services and can stop halfway; wrapping it in a role hides the one moment a human
should be watching. The layer prepares the host and the runbook says what to do, and the operator runs
the command. Same for `rpi-eeprom-update` and the image write.

## The two cards, and the gate between them

**#480 opens with the storage export.** The nine NFS exports on `nas` are the one host-state change
the move cannot carry across two release hops: they are `rw,async,insecure,no_root_squash` to the whole
`/24`, one of them is `/mnt` itself, and nothing in git records them. So #480's first outcome is to
drop the `/mnt` export, keep the eight subdirectories the cluster already mounts, squash root, drop
`insecure` and name the clients — done before `ufw` is enabled, so the client list is settled while
nothing is filtering and a mistake is a failed mount rather than a silent one. Reference: §4 of
[the security baseline](../../security/security-baseline-2026-11-24.md).

**#480 ends when** a fresh collector run shows: the export list is the narrowed one and the clients
still mount, the snapraid cron executes, the kernel and `linux-modules-extra` package sets are one
version each, `ufw` is enabled on all six, the time zone is chosen rather than defaulted, both Pis
refuse password logins and run the images the templates pin, `apt-get dist-upgrade` is clean, and
`--check` on the new layer reports no drift.

**#481 starts when** that is true, and not before: every defect #480 repairs is one that a release
upgrade would otherwise carry across two hops, and two of them — the silent cron and the broken DKMS
entry — fail *harder* on a new kernel.

## Shape of the host layer

```
ansible/
  ansible.cfg                 # no host key checking surprises, one fork, explicit inventory
  inventory/hosts.yml         # six hosts, three groups: cluster, servers, edge
  group_vars/all.yml          # time zone, sudo policy, package floor
  group_vars/cluster.yml      # K3s: version, config keys, the disable list
  group_vars/edge.yml         # Docker, compose project path, the two Pi-only facts
  roles/ssh/                  # sshd policy, key-only, X11 off
  roles/sudo/                 # %sudo only; removes the %admins grant
  roles/time/                 # one time zone, timesyncd, and the RTC rule
  roles/packages/             # the floor, the removals, autoremove policy
  roles/kernel/               # keep N kernels; purge the rest; DKMS health
  roles/nfs/                  # on nas only: exports, with the client list
  collectors/host-audit.sh
  playbooks/site.yml          # the only entry point: --check for drift, apply for rebuild
  playbooks/templates/runners/…
```

Two rules hold it together. **`site.yml` is the only entry point** — a role that is not reached from it
is dead code, which is what `setup-node.yaml` became. And **`--check` is the contract**: the layer is
worth having only if `ansible-playbook site.yml --check` exits 0 on a correct host, so CI-less as this
repository is, that command is the drift report and belongs in the README.

Two things the layer must *not* do, both learned from `setup-node.yaml`:

- **No `NOPASSWD: ALL`.** The host layer's job is to remove it, not write it.
- **No key material in the repository.** `authorized_key` reads from `~/.ssh/*.pub` on the operator's
  machine, never from a file in git, and a key that is missing fails the play rather than skipping it
  (`with_file` already fails loudly; `lookup('file', …, errors='ignore')` does not).

## Shape of the Pi script

The script owns four files on the Pi and nothing else:

| Path | Why it is in the script |
|---|---|
| `/home/vronteix/dockers/docker-compose.yml` | the stack; rendered from the template per host |
| `/home/vronteix/dockers/pihole.env`, `wg-easy.env` | the two secrets files the templates reference |
| `/boot/firmware/cmdline.txt`, `config.txt` | the boot line and `arm_64bit=1`; a reflash must reproduce them |
| `/etc/hostname`, `/etc/hosts` | the name the compose file and Pi-hole's DNS depend on |

`backup` copies `/home/vronteix/dockers` whole — Pi-hole's `etc-pihole` is 57 MB of blocklists and the
query database, and wg-easy's peer set lives in the same tree — plus the current `config.txt` and
`cmdline.txt`, into one directory the operator keeps off the Pi. `restore` puts the tree back and
starts the stack; `verify` then proves the containers are up and Pi-hole answers.

The script also records the boot facts that are invisible otherwise: the EEPROM version
(`rpi-eeprom-update` reports 26.3 on `rbp0`, 13.12 on `rbp1`) and the fact that `sda` is the USB disk.
A reflash that changes either is a behaviour change nobody would notice for a month.

## The sequence

Ordered by what breaks if it is done at the wrong time, not by hostname.

**Phase 0 — stabilise (#480).** No release changes. It opens with the NFS export rewrite, because
that is the only host-state change the move must carry and the only one where a wrong client list
looks like a healthy export. Then the package and kernel trims, `ufw`, the Pis' SSH policy, and the
snapraid cron. Ends with a collector run that shows every defect closed.

**Phase 1 — `rbp1`.** Reflashed to 26.04.1. It goes first because `rbp0` still answers LAN DNS and
WireGuard while it is down; the reverse order takes the house's resolver offline with nothing to fall
back to.

**Phase 2 — `rbp0`.** Same, once `rbp1` is verified answering DNS. This is the host that changes
release most: 24.04.4 → 26.04.1 in one hop.

**Phase 3 — `nuc0`.** The first cluster node, because it is a server whose claims can be rebuilt from
`nodes-nuc0` in the backup bucket, and because it is not the GPU host and not the storage host. Two
hops, one node at a time, draining and uncordoning around each.

**Phase 4 — `streaming`.** After `nuc0`, because the driver is the open question. If the NVIDIA module
does not build, this is the host where a stop is cheapest to take: the media stack comes back.

**Phase 5 — `rbp2`.** The ARM64 control-plane member, after the two x86 servers have proved the path.
`rbp2` runs nothing but the control plane, so there is no claim to lose and no reason to go early.

**Phase 6 — `nas`.** Last, always. Two release hops on the host that serves the nine exports and the
snapraid parity, with a union filesystem in the middle of it. Nothing else in the estate is a
prerequisite for it, so it waits.

Between every phase: a collector run, and the backup job's last run confirmed green — a release
upgrade is the one night you want the previous night's copy.

## Risks

**The Quadro P400 and the 26.04 kernel.** The module is DKMS-built against five kernels today, the
newest being the 6.8.0-149 the host runs. 26.04 ships a newer kernel than that, and the 580 branch is
the last one NVIDIA's release notes list for Pascal cards — so the driver will not get a new major
release to build against. Either the 580 source compiles against the new kernel as it stands, or
`streaming` can never take 26.04 and the destination for that host becomes "stay on 24.04, which is
supported until 2029". **This is decided by an experiment, not by reading release notes**: build the
module against a 26.04 kernel in a throwaway VM before `streaming` is touched.

**The union filesystem across two hops.** `nas` runs mergerfs over six ext4 disks with snapraid parity
above it. mergerfs is a userspace FUSE mount whose behaviour follows the kernel it was built for, and
the snapraid runner is not running at all today (`python2` is missing, the cron fails silently). Both
must be re-verified at 24.04 *and* at 26.04, not just at the end — a parity operation on a fresh
kernel is exactly where an untested union costs the collection.

**The `nas` boot partition and the DKMS residue.** `intel-i915-dkms` is registered against two 5.15
kernels with a `dkms.conf` that does not exist, so every kernel install prints a failure. It is a
hand-built module for hardware `nas` does not have. Removing it is #480's job; leaving it is a release
upgrade that stalls on a prompt nobody reads.

**`/boot/firmware` has 62–64 MB free** on `rbp0` and `rbp2` of 253 MB. A release upgrade writes a new
kernel and firmware there first. The Pis that are reflashed are not affected; `rbp2` is.

**The third etcd member is an ARM64 Pi with no container runtime.** Quorum survives one node down, so
the four cluster nodes are moved strictly one at a time, each uncordoned and healthy before the next
starts. No plan runs two hosts in parallel, and no plan runs during a backup window.

**The upgrade path is not the same on two hosts.** `rbp0` is already on noble and gets one hop; the
five jammy hosts get two. A runbook that says "do-release-upgrade twice" is wrong on `rbp0` and right
five times, and the plan needs to say which host it is talking about at each step.

## What proves each step

Per host, after each phase, in this order:

1. `host-audit.sh` again, diffed against the previous run — the release, the kernel, the package set,
   the exports and the containers, all in one file.
2. `kubectl get nodes` (from outside the moved node) — `Ready`, and the same K3s version as the rest.
3. The app probes that host serves: Prometheus up, a claim read back, `snapraid status` clean on `nas`,
   Pi-hole answering `dig` on the Pis.
4. `site.yml --check` exit 0 — the host layer agrees with the machine.

## Out of scope

- **PostgreSQL, and every workload inside the cluster.** A node move must not require one, and if it
  does, the plan has failed, not the workload.
- **The rest of the cluster's security surface.** The wg-easy admin on plain HTTP, the absent host
  firewall, the `%admins` grant and the etcd snapshot schedule are real, and they are
  [the security baseline](../../security/security-baseline-2026-11-24.md)'s work rather than this one's.
  The NFS exports are the exception: they are in #480 because the move would otherwise carry them
  across two hops unmeasured, and because they change host state that the upgrade has to preserve.
- **K3s.** It moves with `system-upgrade-controller`, not with the OS; 1.37 is a decision of its own
  and has no card.
- **A second Ansible controller.** `site.yml` runs from the operator's workstation over SSH. A runner
  in the cluster is the old card #244's idea and is not needed to move a host.

## Open questions

1. **Does the P400 module build against the 26.04 kernel?** The experiment above settles it, and it
   can run before any host moves. If the answer is no, `streaming` stays on 24.04 and the design says
   so rather than discovering it at phase 4.
2. **Does an official Raspberry Pi Ubuntu 26.04.1 image exist**, or is the Pis' path a 26.04 base image
   plus `rpi-eeprom` and the `-raspi` kernel? `rbp0` being offered 26.04.1 proves the *release* exists
   for arm64; it does not prove an installable image does.
3. **Which time zone**, now that all six are `Etc/UTC` and nothing depends on it. One value in
   `group_vars/all.yml`, and `Europe/Paris` is the obvious one given `TZ=Europe/Paris` is already
   hard-coded in every LinuxServer container.
4. **Does `nas` keep exporting to the whole `/24`** after the hardening change narrows it? The design
   assumes the export list is rewritten in #480 and the upgrade only has to preserve what is left.
