# The security baseline, 2026-11-24

> **In one line.** Six boundaries hold this homelab apart from the internet, each other and itself;
> this page grades what actually crosses each one today, what that costs, and the smallest change that
> closes it.
>
> **Use it when** you are deciding what to fix next, or you want the evidence behind "the cluster is
> secure enough" · **Verified** 2026-11-24 · **Kind:** record
>
> **Sources** the six collector reports of 2026-11-24 (see
> [the audit](../audit/hosts-2026-11-24.md)), `hack/check_exposure.py`, `cluster/helm/**/values.yaml`,
> `docs/how-it-works/network.md`, `kubectl/mcp-readonly-rbac.yaml`, and the two designs it continues:
> [media-stack security](../superpowers/specs/2026-11-14-media-stack-security-design.md) and
> [hosts: stabilise and upgrade](../superpowers/specs/2026-11-24-hosts-stabilise-and-ubuntu-upgrade-design.md)

This is a record: what was true on the date, graded. It is not the plan of work — the work is
[card #480 and #481](https://vikunja.kub.local/projects/2), and this page is what they were filed
from. A later measurement supersedes it; this one is not edited afterwards.

> **Superseded in part, 2026-11-24 — written, not yet applied.** The findings in §3 (no host firewall),
> §4 (nine exports of the whole LAN, unsquashed root) and §6 (the `%admins` grant, password logins on
> the two Pis) have a fix in `ansible/` that was written the same day and has not run on a host yet.
> The grades above stand as what was measured; they change when a collector run shows the state after
> the apply, not when the code lands.
>
> **Superseded in part, 2026-11-26 — the router's rules were read.** Two questions this record left
> open now have an answer: the Livebox does not forward `5000` or `9902`, so §2's exposure ends at
> the LAN, and the `/24` holds 30 addressed devices, eight of them the homelab. The LAPI's `30500` is
> not forwarded either, which keeps the security-detection plan's grade and corrects its stated
> reason. The measurements are [the Livebox record](../audit/livebox-2026-11-26.md); the grades here
> stand as what was measured on 2026-11-24.

## How a claim is graded here

A line in this plan is only a finding when it names four things: the principal that is not trusted,
the action it can take, the control that action is supposed to hit, and what actually happens. A gap
without that chain is written in [what is not established](#what-is-not-established) and carries no
severity.

Severity is the demonstrated consequence, not the size of the omission. *High* means a control was
defeated outright for an action with real damage; *medium* means the same with a narrower blast
radius or a precondition the attacker does not already have; *low* and *informational* are what they
say.

Nothing here comes from a live probe of a deployed service. Every line is read from configuration on
the machines or rendered from the charts.

## 1. The internet to Traefik

**This one holds.** 73 `IngressRoute` objects live against the cluster, 77 rendered from the
charts, and `hack/check-exposure.sh` passes: every hostname outside the nine-name allowlist carries
`kube-system-lan-only`, and the nine that do not are the ones meant to answer the world. The control
is one middleware per route, which is thin but checked on every commit.

The residual risk is where the check cannot see. It renders the charts; it does not read the cluster.
`cluster/infrastructure/**` is applied by hand, so a route created there was never in a render, and
the live count differs from the rendered count by four. That difference is not explained here and is
the first thing to look at before trusting the allowlist.

*Not a finding. See [what is not established](#what-is-not-established) for the four routes.*

## 2. The VPN, and the screen that holds it

**A finding, medium.** Both edge Pis publish the wg-easy admin interface — the one screen that holds
every peer's private key — on **`5000/tcp` over plain HTTP**, bound to `0.0.0.0`, on a machine with no
firewall. Every host on the flat `/24` can open it, and the login and the session cookie cross the
LAN in clear text. Any device that reads that traffic, or guesses the password, is handed the
credentials of every VPN client, which is the ability to come back in from outside.

The control it defeats is the session's confidentiality — a login form served over HTTP has none —
and the blast radius is the VPN itself, not the cluster.

Beside it, the same DNAT publishes `9902/tcp`, the smartctl exporter, unauthenticated, on both Pis.
Node metrics are not a secret; it is listed because it is the same fix.

*Smallest fix:* bind `5000` to `127.0.0.1:5000` in the compose file and reach it through an SSH
tunnel, or terminate TLS in front of it. Move `9902` behind `lan-only` in the cluster instead of
publishing it from the host firewall's blind spot. Neither needs a new component.

*Not established:* whether the house router also forwards `5000` from outside. The router's rules are
not in git; `docs/how-it-works/network.md` lists only 80, 443, 25565, 22000 and 21027.

## 3. The LAN to the control plane

**A finding, informational.** No host on any of the six runs a packet filter of its own. `ufw` is
installed and enabled on five and inactive on all five, and `streaming` does not have `ufw` at all.
The only chains present are Docker's and Kubernetes's, which describe port forwarding, not policy.

What that leaves open to every device on the same `/24`: the API server on `6443` of all four nodes,
the kubelet on `10250`, node-exporter on `9100`, rpcbind and NFS on `111`/`2049`, and each server's
etcd on `2379`/`2380` bound to its own LAN address.

None of those is unauthenticated — the API server wants a client certificate, kubelet and etcd want
theirs, and the K3s credential files are `0600 root:root`. The finding is not that the cluster is
open; it is that **the boundary between "a device on the LAN" and "the control plane" is each
service's own authentication and nothing else.** A compromised television and the operator's laptop
have the same reach, and both reach the next boundary, which is worse.

*Smallest fix:* turn `ufw` on where it already exists, with the K3s and Docker rules it needs, and
install it on `streaming`. Then narrow the etcd binds to the two peer addresses and the API server to
the LAN, rather than `*`. Do this after the cluster is stable, not during the release upgrade.

## 4. The LAN to the storage exports

**The worst finding, high — and already planned.** `nas` exports **nine** paths to the entire
`192.168.1.0/24`, and every one is `rw,async,insecure,no_root_squash`. One of the nine is **`/mnt`** —
not a share, the whole storage tree: the six data disks, the SSD, the LVM volume behind the downloads,
and the mergerfs union on top of them. The list is in `/etc/exports` on the host and nowhere in git.

`no_root_squash` means a client that mounts the export as root keeps root, so Unix ownership and mode
stop being a control. `insecure` removes the requirement for a privileged source port, which is what
makes the export usable from an unprivileged user or a container that can reach the LAN. Together
they defeat an explicit control — file permissions — over the 51 TB union, the parity data that can
rebuild one of its disks, and whatever sits underneath: `acme.json` on `/mnt/storage/traefik`, the
qBittorrent configuration, the Syncthing identity and its keys.

This is not a new discovery. Phase 3 item 1 of [media-stack security](../superpowers/specs/2026-11-14-media-stack-security-design.md)
says exactly this, and that design's own status line records it as unbuilt. What the 2026-11-24
measurement adds is the detail that was missing then: nine exports rather than a guess, `/mnt` among
them, and the exact flags.

*Smallest fix:* drop the `/mnt` export and keep the eight subdirectories the cluster already mounts,
squash root (`root_squash`, or `all_squash` with `anonuid`/`anongid` naming the consumer), drop
`insecure`, and list client addresses rather than the subnet. Expect to fix the ownership inside the
export once, on the server, rather than from the client.

*Precondition the attacker needs:* a foothold on any LAN device. That is the same precondition as
§3, which is why the two are one job.

## 5. The host to the container

**A finding, medium.** Four charts declare `privileged: true`: `qbittorrent`, `pihole-exporter`,
`prometheus-smartctl` and `mktxp`; 31 charts mount a host path. A privileged container is the host —
it can reach every device node, load a module and write `/etc`. The boundary is namespace isolation,
and a privileged pod has opted out of it.

The finding is not that these pods are privileged. `smartctl` and `mktxp` genuinely need it, and
qBittorrent's VPN mode does. It is that the exemption is invisible: nothing in the repository lists
which pods hold host authority, so the next privileged chart arrives unnoticed and the next review
starts from nothing.

*Smallest fix:* one line in [what enforces what](enforcement.md) naming the four, plus a check that
fails when a fifth appears. That is a `grep` over the rendered charts, the shape `check-exposure.sh`
already uses.

*Corrected 2026-11-26:* two of the four have left the list. `pihole-exporter` and `mktxp` only call
an API over TCP — Pi-hole's HTTP API and the RouterOS API — and mount no device, so neither ever
needed the host; the paragraph above took the flag at face value and was wrong about `mktxp`.
`qbittorrent` (the WireGuard interface) and `prometheus-smartctl` (the disk devices) genuinely need
it. The exempt list is two, and the check that fails on a third is still unbuilt.

*Holds today:* the Docker socket is `root:docker 0660` on every host that has one and the `docker`
group has no human member, so shell access to Docker is not an unprivileged action anywhere.

## 6. Patching, identity and the operator's own access

**A finding, medium.** `/etc/sudoers` on all six carries a hand-added
`%admins ALL=(ALL) NOPASSWD: ALL`, and the operator's account is in `admins` on every host. Passwordless
root is therefore granted by a group membership, with no drop-in file recording it: `vronteix` is not
merely a user with a key, it is root the moment it logs in.

The control defeated is re-authentication — the reason `sudo` asks for a password at all. What it
costs: any unattended session, any stolen key, or any password guessed on a Pi (both still accept
`PasswordAuthentication yes`) is root on that machine rather than a user on it.

*Smallest fix:* drop the `%admins` line, keep `%sudo` as it ships, and accept one password prompt per
sudo. If unattended automation needs it, give that one command a scoped `NOPASSWD` drop-in instead of
a group-wide grant.

*Status, 2026-11-24.* The line is removed by `ansible/roles/accounts`, and the grant an unattended run
needs sits on one named account instead of a group: `ansible`, key-only, in
`/etc/sudoers.d/00-homelab-automation`, which `ansible/roles/automation` writes and `visudo` validates.
Re-authentication is therefore still defeated for that one account, by design, while the operator's
own account needs its password and the sudo log tells the two apart.
[The record](../decisions/2026-11-24-automation-sudo-account.md) says why the alternative — no
passwordless grant anywhere — was rejected: `make check` would need a password typed at a terminal,
and that drift report is the layer's whole output.

*Status, 2026-11-26.* Both Pis carry it too. The layer cannot reach them until they are reflashed
(card #481), so `ansible/pi/hosts-pi.sh` owns the same state there: the grant and the `admins` group
gone, and `%sudo` left as Ubuntu ships it. Sudo on a Pi now asks for the account's password, and the
reflashed image's `user-data` already says `PASSWD:ALL`, so the two agree. Measured on both hosts
before and after:
[the record](../audit/hosts-2026-11-24.md#the-edge-pis-converged-2026-11-26).

**A finding, low.** Both Pis accept a password on port 22 while the four cluster nodes do not. They
are the DNS server and the VPN endpoint, so a guessed password does not merely reach a host.

*Smallest fix:* `PasswordAuthentication no` on both, in the same edit that fixes §2.

*Status, 2026-11-26.* Closed on both, and the main sshd config was not what was wrong: cloud-init's
`50-cloud-init.conf` said `yes` and sorts ahead of it. The Pis now carry the `00-homelab.conf` drop-in
`roles/accounts` writes on the nodes, and `setup` refuses to close the password while the human
account has no key.

**A finding, low.** Ubuntu Pro is attached on no host. The five jammy machines are offered
`24.04.5 LTS`, so ESM is the wrong lever — the release move is — but `streaming` carries 151 pending
updates and `rbp0` 100, and nothing schedules a patch window at all. The Pro half is settled: no host
attaches, the move is the cover —
[the record](../decisions/2026-11-25-no-ubuntu-pro.md).

*Decision, 2026-11-25 — it stays un-attached.* The free personal subscription covers five machines
and the estate has six, and all six move to 26.04.1 LTS, whose standard support runs to 2031: ESM
(Extended Security Maintenance, the paid extension after standard support ends) is what extends a
release past that point, and no host here will be past it. The backlog the finding measured came
from the `-updates` pocket, which the default configuration does not install; `unattended-upgrades`
is enabled on all six and does install `jammy-security`, and the `-updates` backlog was cleared by
hand that day — `apt-get -s dist-upgrade` prints `0 upgraded` on every host.

**A finding, low.** No server declares an etcd snapshot schedule. The five snapshot files on disk are
the ones the upgrade plan wrote before each restart, so the nightly backup uploads a copy that only
changes when somebody runs an upgrade. It restores a bad write; it does not restore a month.
Card #506 (`etcd: enable K3s S3 upload with the 1.31 upgrade`) is the fix and is still open.

> **Closed 2026-11-25.** The three servers now declare `etcd-snapshot-schedule-cron "0 */6 * * *"`,
> compress, and upload to the bucket themselves, keeping the newest 180 per prefix, and the restic
> copy the nightly jobs took is gone. Card #506 carries the evidence;
> [`../how-it-works/backups.md`](../how-it-works/backups.md) is the page.

## What is not established

Each line names the missing fact, not a guess about it.

- **The four rendered routes with no live counterpart.** 77 rendered, 73 live. Which four, and
  whether one of them was a gate, is not known.
- **Which devices share the `/24`.** Every finding in §3 and §4 assumes an untrusted device can be on
  the LAN. The router's DHCP leases and any guest or IoT VLAN are not in the repository.
- **Whether `5000` is forwarded from outside.** The router's port forwards are not in git, and the
  one page that lists them predates the Pis' current compose file.
- **The mode of the three env files** under `/home/vronteix/dockers` on both Pis. Their existence is
  measured; whether they are readable by a non-root local user is not.
- **What the Pis' `/home/vronteix/scripts/backup.sh` does.** It runs as root at 04:00 and was not
  read.
- **Whether the `traefik.kub.local` dashboard is reachable from the LAN only.** The route claims
  `lan-only`; the live object was not read, and the route count above says the two can differ.
- **Whether any container in the cluster is reached by a path outside Traefik**, for example a
  `LoadBalancer` Service with its own address. The charts were read; the live Services were not.

## The order the measured defects imply

Nothing here waits on the Ubuntu move, and the storage export is the one that cannot wait. The second
row is the same fix as the first in one respect — both stop a LAN host reaching something it should
not — so they are best done in the same sitting, before `ufw` goes on and makes the export's client
list the thing that has to be right.

| Work | Boundary | Grade | Waits on |
|---|---|---|---|
| Drop the `/mnt` export; squash root and drop `insecure` on the eight the cluster mounts; name the clients | §4 LAN → storage | high | nothing — this is Phase 3 item 1 of [media-stack security](../superpowers/specs/2026-11-14-media-stack-security-design.md), and #480's first outcome |
| Bind wg-easy's admin to `127.0.0.1`; move `9902` into the cluster | §2 the VPN | medium | nothing |
| Turn `ufw` on with the K3s and Docker rules; install it on `streaming` | §3 LAN → control plane | informational | the export's client list, or the NFS clients break |
| Drop `%admins ALL=(ALL) NOPASSWD: ALL`; key-only SSH on both Pis | §6 identity | medium, low | nothing |
| List the two privileged charts and check for a third | §5 host → container | medium | nothing |
| Schedule etcd snapshots and the S3 upload | §6 the control plane's copy | low | done 2026-11-25, card #506 |
| Reconcile the four rendered-but-not-live routes | §1 internet → Traefik | — | nothing |

The first four rows belong to the stabilisation card #480 and are why it exists; the Ubuntu move in
#481 would otherwise carry them silently across two release hops. The export row is the one that
changes host state before the move, so it is also the one that has to be finished and verified on
24.04 *and* on 26.04 rather than once.

## What this plan does not cover

The applications' own authentication — who may open Radarr, what authentik holds, break-glass — is
[identity](../how-it-works/identity.md). Recovery after a loss is
[the critical points](critical-points.md). The guarantee-by-guarantee command list is
[what enforces what](enforcement.md), and this page does not repeat it.

It also does not grade the cluster's *internet-facing* applications. `plex`, `seerr` and the two
websites are meant to be reachable, and whether they are safe is a per-application question that
needs their own source, not this record.
