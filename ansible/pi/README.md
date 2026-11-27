# The two Pis

`hosts-pi.sh` is the only thing that configures `rbp0` and `rbp1`. They are not patched, they are
reflashed: the image goes onto the USB disk, the Pi boots, and this script puts back what the image
does not know. A fresh Image has no Ansible and no key, which is why it is bash.

## The verbs

```bash
./hosts-pi.sh render rbp1          # print every file this script owns; changes nothing
sudo ./hosts-pi.sh setup  rbp1     # install the engine, write them, start the stack, print changed=N
sudo ./hosts-pi.sh verify rbp1     # name every difference; exit 1 if one needs an apply
sudo ./hosts-pi.sh vpn-sync rbp1 [--force]   # the standby's pull, run by hand
sudo ./hosts-pi.sh backup /mnt/usb/pi rbp1   # copy the state a reflash destroys
sudo ./hosts-pi.sh restore /mnt/usb/pi rbp1  # copy it back, then verify
```

`render` is what to read before a change. `verify` is what to run after one: a host that matches the
repository prints one line and exits 0.

`setup` and `verify` both need root, and now refuse early without it: they read `/etc/sudoers`, which
is `0440 root:root`, they write the SSH policy, and they read and apply `ufw`. `render` needs nothing.

`PI_ROOT` points every verb at another directory instead of `/`, which is how `hack/tests/test_pi_script.py`
runs them without a Pi:

```bash
PI_ROOT=/tmp/fake-pi ./hosts-pi.sh setup rbp0
```

## What it owns, and what it refuses to touch

| Path | Owned | Why |
|---|---|---|
| `/home/vronteix/dockers/docker-compose.yml` | yes | one template in `playbooks/templates/runners/`, the same bytes on both hosts: the tunnel port is the literal `51821`, because the pair shares one panel database and one client config |
| `Caddyfile` | yes | the VPN panel's TLS endpoint, rendered per host from `playbooks/templates/runners/Caddyfile`: the panel's name is the only per-host value. `setup` force-recreates `caddy` when it writes it, because a bind mount is resolved at container start |
| `etc-pihole-updatelists/pihole-updatelists.conf` | yes | the file Pi-hole's list updater reads; without it Docker makes the source a directory |
| `etc-dnsmasq.d/02-my-wildcard-dns.conf` | yes | the wildcard names the LAN resolves through this Pi-hole |
| `pihole.settings` | yes | every Pi-hole setting, rendered from one template so the two hosts are byte-identical. The name avoids `.gitignore`'s `*.env` |
| `/etc/netplan/50-cloud-init.yaml` | yes | the resolvers: both Pi-holes, so a host resolves through its own blocklist. `setup` applies it with `netplan apply` |
| `/etc/systemd/resolved.conf.d/90-pihole-resolvers.conf` | yes | a drop-in that keeps resolved's stub listener off, so `/etc/resolv.conf` lists the two Pi-holes and a container reads them instead of falling back to Docker's 8.8.8.8 |
| `/etc/hostname` | yes | the name Pi-hole's DNS and the compose file depend on |
| `/etc/ssh/sshd_config.d/00-homelab.conf` | yes | the drop-in that closes password logins. It is numbered `00` because sshd takes the first value it reads and cloud-init's `50-cloud-init.conf` says `yes` |
| `/etc/ssh/sshd_config` | yes | the same two directives, `PasswordAuthentication no` and `X11Forwarding no`, written as a second record. The commented examples are left alone |
| `/etc/sudoers` | yes, the grant only | removes `%admins ALL=(ALL) NOPASSWD: ALL`; `%sudo` stays as Ubuntu ships it, so the human account keeps a password on sudo. The edit passes `visudo` before it is installed |
| the `admins` group | yes, its removal | it exists only to carry that grant. Removed after the line, the order `roles/accounts` uses |
| `/etc/sudoers.d/90-cloud-init-users` | yes, its removal | cloud-init's grant for the `ubuntu` account the image's rename retired: a live passwordless root the moment anything creates that account again |
| `ufw` | yes | the edge rule set, the default deny incoming and the allow routed the containers need. Rules first, the defaults second, `enable` last |
| `/home/vronteix/.ssh/authorized_keys` | **no** | the operator's keys: written by the image's `user-data` and, on the cluster nodes, by `roles/accounts`. `setup` reads it and refuses to close password logins when it holds no key |
| `/etc/hosts` | **no** | cloud-init rewrites it from its own template on every boot (`manage_etc_hosts: true`); its copy already names the host and localhost |
| Docker's repository and the four packages | yes | a fresh image has no runtime; `setup` installs the pinned set from `download.docker.com` |
| `pihole.env` | **no** | it holds the Pi-hole panel password and nothing else: `FTLCONF_webserver_api_password`, `0600`, owned by the account that runs compose, gitignored. `setup` requires it and names it when it is missing. The v5 name `WEBPASSWORD` is ignored by this image |
| `certs/` | **no** | three files: `tls.pem` and `tls-key.pem`, this host's own leaf from the internal CA (`hack/internal-cert.sh --host vpn-rbp0.kub.local`) that Caddy serves on 8443, and `pihole.pem`, the same kind of leaf for the Pi-hole panel, combined certificate and key in one file. Required by `setup`, never written, and carried off the host by `backup` |
| `config/` (wg-easy's database) | **no** | since wg-easy 15 the panel account, host, port, DNS and peers live in `config/wg-easy.db`, not in an environment file. `backup` copies it with the rest of the stack. A fresh container writes it `0644`, so `setup` narrows it to `0600` root-owned once the panel has created it — every peer's private key and the panel's password hash are in that file — and `verify` reads the mode back |
| `/etc/keepalived/keepalived.conf`, `/usr/local/lib/wg-vpn/` | yes | the VRRP instance, the notify script, and the snapshot and sync scripts. `setup` writes them, keeps the timers armed and restarts keepalived only when the instance changed |
| `/etc/systemd/system/wg-vpn-{snapshot,sync}.{service,timer}` | yes | the snapshot timer on `rbp0` and the pull timer on `rbp1`. The authority carries no sync unit: `verify` names one left there as drift |
| `/var/lib/wg-easy-sync/` | **no** | the authority's snapshot `wg-easy.db` (`0640 root:wg-sync`), the standby's `last-installed` stamp and its transient `incoming.db`. `setup` makes the directory and the `wg-sync` group; the scripts write inside it |
| `/etc/wg-sync/id_ed25519` | **no** | the private half of the pair's sync key, on `rbp1` only, `0600 root:root`. The operator mints it; `setup` fixes its mode and names it when it is missing, and `verify` reads it back |
| `/var/lib/wg-sync/.ssh/authorized_keys` | **no** | the public half, on `rbp0` only: one line restricted to `from="192.168.1.38"` and a forced `cat` of `/var/lib/wg-easy-sync/wg-easy.db`. Operator-supplied; `backup` and `restore` carry both halves |
| `/boot/firmware/config.txt`, `cmdline.txt` | **no** | see below |

The stack's own files live inside the stack directory, so `backup` and `restore` carry them with the
rest of it. `verify` names a difference in any of them, the netplan file included.

### SSH and sudo: the same policy as the cluster nodes

`roles/accounts` owns this on the four cluster nodes. It cannot reach the Pis until they are reflashed
(card #481), so the same two settings live here, and `setup` is what applies them:

- **Password logins are off.** The 2026-11-24 audit, and a re-measurement on 2026-11-26, both read
  `passwordauthentication yes` on both Pis. The main `/etc/ssh/sshd_config` already said `no`; what won
  was cloud-init's own `/etc/ssh/sshd_config.d/50-cloud-init.conf`, which says `yes` and is included
  near the top of the file. `sshd` (the SSH server) takes the first value it reads, so the drop-in
  above it decides. `00-homelab.conf` is numbered to sort ahead of it, and cloud-init's file is left
  as it is: a later boot may rewrite it, and ours wins either way.
- **`X11Forwarding` is off**, the second directive in the same drop-in. It was `yes` on both Pis.
- **The `%admins` passwordless grant is gone**, and the `admins` group with it, so `sudo` asks for the
  account's password — the point of the change. `%sudo` stays as Ubuntu ships it. There is no
  automation account on a Pi yet, so no unattended run is root there.
- **`/etc/sudoers.d/90-cloud-init-users` is removed** when it is present: it grants the retired
  `ubuntu` account root without a password.

**The lockout guard.** Closing password logins is a lockout on a host with no key, and the machine that
would fix it is the one nobody can log in to. Before writing the drop-in, `setup` reads
`/home/vronteix/.ssh/authorized_keys` and stops with a message if it holds no public key. It does not
write that file: the keys are the operator's, and the image's `user-data` is what puts the first one
there.

The strings match `roles/accounts`, and `hack/tests/test_pi_script.py` compares them, so a change to
one record alone fails a test rather than leaving half the fleet on the other.

### The host firewall

`roles/firewall` owns this on the four cluster nodes. The Pis take the same edge rule set through
`setup`, because the layer cannot reach them. `ufw` had been installed and inactive on both since
2023: every LAN device reached 22, 53, 80, 443, 5000, 9100, 9323 and 9902, and nothing filtered.

- **Rules first, then the defaults, then `ufw --force enable`.** The reverse order closes the session
  that is still adding the rule which allows it. Run it from the LAN or the console: 22 is allowed
  from the LAN only, and `sudo` drops `SSH_CLIENT`, so no guard inside the script can see where you
  came from.
- **What is open:** 22, 80, 443 and 53 from the LAN; 53, 80 and 443 from the WireGuard tunnel
  `10.8.0.0/24`; 53 from the compose network `172.30.0.0/24`; 9100 and 9323 from the four cluster
  nodes. 443 is Pi-hole's own HTTPS panel: it runs with `network_mode: host`, so it reaches `INPUT`,
  and `hack/check_pihole_parity.py` reads it.
- **The tunnel's own address never reaches ufw.** wg-easy masquerades a peer's traffic to its own
  address on that compose network before it leaves, so the four `10.8.0.0/24` rules match nothing and
  the `172.30.0.0/24` pair is what gives a connected peer a resolver. Measured on `rbp0` on
  2026-11-27: `[UFW BLOCK]` in `/var/log/ufw.log` with `SRC=172.30.0.2` and `DPT=53`. Both sets stay:
  the tunnel pair is what a packet carrying the tunnel's own address would need.
- **`allow routed`, not deny.** Docker's containers egress through `FORWARD`; a deny there takes
  every one of them, Pi-hole included, off the network.
- **What ufw never sees:** the published container ports — `51821/udp` (wg-easy), `8443/tcp`
  (Caddy) and `9902/tcp` (smartctl-exporter). Docker's DNAT rewrites in `NAT` and `FORWARD` before
  `INPUT` is consulted, so a ufw rule for them would filter nothing.
- **VRRP is a protocol, not a port.** keepalived's advertisements are IP protocol `112`, so the rule
  reads `proto vrrp` with no port, one rule per peer address and one in each direction. A `112/udp`
  rule would match nothing; `setup` applies the rule and `verify` reads it back.
- **The record.** `roles/firewall/vars/main.yml` holds the rule set; this script carries a copy
  because it runs on the host, and `test_the_pi_and_the_ansible_layer_allow_the_same_ports` compares
  the commands they resolve to. `verify` reads `ufw status verbose` and `ufw show added`, and names a
  missing rule, a default that moved, or a firewall that is off.

### The two Pi-holes are one configuration

`rbp0` is the source of truth and `rbp1` conforms: when the panels disagree, `rbp1` is the one that
moves. Parity is not a habit, it is the file. `pihole.settings` is rendered from one template and
carried to both hosts unchanged, so `render rbp0` and `render rbp1` print the same bytes, and `setup`
prints `changed=0` on a host that already matches.

- **`pihole.env` holds the password, and nothing else.** Compose reads `pihole.settings` first and
  `pihole.env` after it, so on a collision the secret wins. Every other `FTLCONF_*` key belongs in the
  settings file: a key set in the panel rather than in git is drift the next `setup` cannot see.
- **The `address=` overrides live in `etc-dnsmasq.d/02-my-wildcard-dns.conf`**, rendered and compared
  like the settings. Six of them were also in each host's `FTLCONF_misc_dnsmasq_lines`, which the
  settings file now clears: the duplicate is gone and this file carries all six, `ashudev.com`
  included. `ADLISTS_URL` went with them, since no timer or entrypoint runs `pihole-updatelists` on
  these hosts — the blocklists in `gravity.db` are edited through the panel.
- **`FTLCONF_webserver_api_max_sessions=64`** is there because 16 is the default, a session lasts 30
  minutes, and an app password cannot close one — only the panel's own password can. The parity check
  takes a session per host, and the third run in half an hour answered `api_seats_exceeded`.
- **`FTLCONF_misc_etc_dnsmasq_d=true` is what makes that file read at all.** Pi-hole v6 defaults it
  to false, so `/etc/dnsmasq.d` was ignored on both hosts and the overrides survived only through the
  env duplicate. Measured on 2026-11-25: with the key on, all six answer from `127.0.0.1`.
- **The resolvers point at both Pis**, `192.168.1.22` then `192.168.1.38`, on both hosts. Never
  `127.0.0.1`: a container cannot reach the host's loopback. Never `1.1.1.1`, and never any other
  public resolver beside the Pi-holes: it answers around the blocklist. The two Pis forward to
  DNS4EU's unfiltered pair and to nothing else, per
  `docs/decisions/2026-11-27-public-dns-is-dns4eu-unfiltered.md`.
- **A resolver change is live.** `setup` runs `netplan apply` and restarts `systemd-resolved` when the
  bytes moved, and recreates the bridge services (`wg-easy`, `caddy`, `smartctl-exporter`), because a
  container's `/etc/resolv.conf` is a copy taken when it is created. If both Pi-holes are down, a host
  cannot resolve, so the second address in the list is the fallback rather than a comment.
- **The drop-in, not the packaged file.** `90-pihole-resolvers.conf` keeps resolved's stub listener
  off: with it on, `/etc/resolv.conf` is `127.0.0.53`, Docker drops that loopback entry and falls back
  to `8.8.8.8`, and a container resolves around the blocklist from inside the host that serves it. The
  file names no server at all — netplan is the one record of the two addresses.
- **Panel drift is checked from the workspace**, not from the host: `hack/check_pihole_parity.py`
  reads both `/api/config` and `/api/lists` through `PIHOLE_RBP0_API_KEY` and `PIHOLE_RBP1_API_KEY`
  and exits 1 naming every setting that differs. A list added in one panel is invisible to `verify`
  and visible to it. It dials HTTPS and trusts only each panel's own certificate, kept in
  `hack/pihole/`, so the app password never crosses the LAN in the clear; a changed leaf — a renewal,
  or a reflash that did not restore `certs/pihole.pem` — fails the check until the new pair is
  re-exported and committed.

### Pi-hole's panel and API certificate

Pi-hole v6 terminates TLS itself on 443, and the pair it minted at first start was the same on both
hosts: CN `pi.hole`, self-signed. A client reaching the address could not verify it against any name,
so the cluster's exporter read both Pis with verification off — encrypted, with an unauthenticated
peer.

- **One leaf per host**, `pihole-rbp0.kub.local` and `pihole-rbp1.kub.local`, from the same internal
  CA as the rest. The names are in `02-my-wildcard-dns.conf` above the `*.kub.local` wildcard, because
  the wildcard points at the router and dnsmasq serves the longest suffix.
- **One combined PEM per host** at `certs/pihole.pem`: certificate first, then the key. Pi-hole reads
  both from `webserver.tls.cert` — the key is not a separate setting — and `pihole.settings` names the
  path through `FTLCONF_webserver_tls_cert`. The compose mounts `./certs` read-only into the pihole
  container, the same directory Caddy reads.
- **The leaf belongs to the panel's own account.** Pi-hole's web server drops to the image's `pihole`
  user, uid 1000 — the same number as `vronteix` on a fresh image — and reads the certificate as that
  account. `setup` therefore hands `certs/` and `certs/pihole.pem` to it, and leaves the Caddy pair
  beside them root-only, because nothing but root needs the VPN key. A leaf installed `0600 root:root`,
  which is how the runbook copies one, is unreadable to the panel: FTL does not fall back to a
  self-signed pair, it refuses to start its web server, and 80 and 443 stop answering on that host.
  That was measured on both Pis on 2026-11-26, after the leaves went in.
- **The exporter trusts the root**, mounts it as a ConfigMap and dials the names. There is no
  `SKIP_TLS_VERIFICATION` left in its environment.
- **`hack/check_pihole_parity.py` pins each host's leaf.** Replacing the certificate changes the pin:
  re-export the two leaves after `setup` and commit them, or the check fails by design.

### The VPN panel is served by Caddy, on the host itself

wg-easy 15 has no TLS server of its own: it expects a proxy in front, and its `INSECURE=true` is only
for the case where there is none. Both Pis used to publish it as plain HTTP on `0.0.0.0:5000`, which
put the login, the session cookie and every peer's key on the LAN in the clear. The panel now listens
on the compose network and nothing else, and `caddy` holds the only socket the LAN can open: `8443`,
this host's own certificate, and no ACME.

- **The name is per host**: `vpn-rbp0.kub.local` on `rbp0`, `vpn-rbp1.kub.local` on `rbp1`. Pi-hole
  answers both from `02-my-wildcard-dns.conf`, above the `*.kub.local` wildcard, and the certificate
  is a per-host leaf — never the shared `*.kub.local` key, which would let either Pi impersonate every
  internal name.
- **The leaf is issued on the workstation**, with `hack/internal-cert.sh --host <name>`, and the pair
  is copied to `certs/tls.pem` and `certs/tls-key.pem` on the host. `setup` refuses to run without
  both and writes neither; `backup` is what carries them off the host.
- **The gate is the panel's own account**, not authentik: nothing on an edge host asks the cluster
  whether a person may enter, because the VPN is the way back in when the cluster is down
  ([the record](../../docs/decisions/2026-11-26-edge-pis-local-auth-only.md)). There is no `wg-easy.env` and no
  OAuth block: the compose file carries no `env_file` for this service, and the account lives in
  `config/wg-easy.db`.
- **Port 8443, not 443**: Pi-hole owns 80 and 443 on these hosts.

### The VPN is one pair

`rbp0` and `rbp1` are one VPN, not two that look alike.

- **`rbp0` is the authority, `rbp1` the standby.** The same rule as the Pi-holes: when the two panels
  disagree, the standby is the one that moves.
- **Both publish `51821/udp`.** One panel database is behind both, so a config minted on either host
  names the same port and a client keeps dialling it whichever Pi answers.
- **keepalived moves `192.168.1.9/24`.** `rbp0` advertises as `MASTER` at priority `150`, `rbp1` as
  `BACKUP` at `100`, unicast to each other's address, with `preempt_delay 300` so a returning `rbp0`
  does not take the address back mid-boot. The router's `51821` forward points at this address, not at
  a host.
- **The address also follows the panel.** keepalived runs `/usr/local/lib/wg-vpn/wg-easy-running.sh`
  every two seconds and drops to `FAULT` after three failures. The check is a file because keepalived
  hands `script` straight to `execve`: a pipeline written in `keepalived.conf` is arguments to
  `docker inspect`, fails every run, and leaves both hosts without the address.
- **The authority snapshots every minute.** `wg-vpn-snapshot.timer` runs
  `/usr/local/lib/wg-vpn/wg-snapshot.sh`, which uses `sqlite3 .backup` and an integrity check, and
  leaves `/var/lib/wg-easy-sync/wg-easy.db` (`0640 root:wg-sync`). A timer that stops writes
  `wg_vpn_snapshot_ok 0` rather than freezing a fresh timestamp.
- **The standby pulls every minute.** `wg-vpn-sync.timer` runs `/usr/local/lib/wg-vpn/wg-sync.sh` as
  root, over SSH as the `wg-sync` account. It stops without touching anything while this host holds
  the address, and it refuses to overwrite a copy edited here: the differing tables are named and
  `vpn-sync --force` is the operator accepting the loss.
- **The key is two files, both operator-supplied.** `/etc/wg-sync/id_ed25519` (`0600 root:root`) on
  `rbp1`; one line in `/var/lib/wg-sync/.ssh/authorized_keys` on `rbp0`, restricted with `from=` and a
  forced `cat` of the snapshot. `backup` and `restore` carry both halves; `setup` fixes their modes,
  refuses to configure a host whose own half is missing, and never mints one.
- **The test and the recovery are written down** in
  [`docs/runbooks/edge-vpn-failover.md`](../../docs/runbooks/edge-vpn-failover.md): who holds the
  address, the timed failover, a divergence, and the router's two calls.

### The engine, which `setup` installs

A reflashed image has no container runtime, and the stack is five containers: `setup` installs
`docker-ce`, `docker-ce-cli`, `containerd.io` and `docker-compose-plugin` from Docker's own
repository, and puts `vronteix` in the `docker` group.

- **The suite** (the release name apt matches on) is read from `/etc/os-release`, so a reflash onto
  the next release names its own suite instead of pointing apt at a codename that no longer exists.
- **The pins are bands** — `docker-ce` and `docker-ce-cli` at `5:29.*`, `containerd.io` at `2.4.*`,
  `docker-compose-plugin` at `5:8.*` — because nothing in this repository bumps an apt pin, so an exact
  version ages silently, which is what 28.3.2 on both Pis was. A band reproduces the same major on a
  rebuild and refuses a jump; the comment above the constants records the version measured when they
  were last moved.
- **An engine that is already there is named, never replaced.** When the packages are installed
  outside their band, `setup` prints a `NOTE` and leaves them alone: upgrading restarts the daemon
  that answers the house's DNS, and on these hosts a reflash is what moves it. `verify` prints the
  same note. A partial set — one package missing while another sits off its pin — is refused rather
  than installed over, and the message says so.
- **A converged host does no network work**: with the four packages in band and the repository in
  place, `setup` starts the stack and stops there. A repository line or keyring that went missing
  under a converged engine is rewritten, since that is what a later `apt-get update` reads; no
  package is touched by it.
- This step needs root, so `setup` is run with `sudo`; without it the script refuses before touching
  apt.

### Why the boot files are backed up and not written

After a reflash the image writes its own `cmdline.txt`, with the `PARTUUID` of the disk it was
written to. A remembered `cmdline.txt` restored onto a differently-enumerated disk makes the Pi
unbootable, and the machine that would fix it is the one that will not start. So `backup` saves the
two files and `restore` puts them back when they are needed; `setup` never invents them.

## The reflash sequence

```bash
# on the Pi, before anything
sudo ./hosts-pi.sh backup /mnt/backup/$(hostname) $(hostname)

# write the image to the USB disk, boot it, then — restore first, not setup
sudo ./hosts-pi.sh restore /mnt/backup/rbp1 rbp1
sudo ./hosts-pi.sh setup   rbp1
sudo ./hosts-pi.sh verify  rbp1
```

`restore` comes first because a fresh image is not this host yet: its `/etc/hostname` is whatever
cloud-init wrote, and `pihole.env` does not exist — `setup` refuses both, correctly, since it will
not configure a host as something else and will not invent a secret. `restore` puts the backup back,
and `setup` then finds a host that already calls itself `rbp1`.

`rbp1` goes first. `rbp0` answers LAN DNS and WireGuard while it is down, and the reverse order takes
the house's resolver offline with nothing to fall back to.

`restore` does not write `cmdline.txt` back: it names the `PARTUUID` of the disk the image was
written to, which is not the disk it is being restored onto. The copy stays in the backup and the
script says so; compare it by hand if the boot arguments need changing.

### The backup is only as good as its size

`etc-pihole/gravity.db` alone is tens of megabytes, and the blocklists with it. A backup that reports
a few hundred kilobytes skipped the database — the check is in the script's last line, and it is the
one to read.
