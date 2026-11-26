# The two Pis

`hosts-pi.sh` is the only thing that configures `rbp0` and `rbp1`. They are not patched, they are
reflashed: the image goes onto the USB disk, the Pi boots, and this script puts back what the image
does not know. A fresh Image has no Ansible and no key, which is why it is bash.

## The verbs

```bash
./hosts-pi.sh render rbp1          # print every file this script owns; changes nothing
sudo ./hosts-pi.sh setup  rbp1     # install the engine, write them, start the stack, print changed=N
sudo ./hosts-pi.sh verify rbp1     # name every difference; exit 1 if one needs an apply
sudo ./hosts-pi.sh backup /mnt/usb/pi rbp1   # copy the state a reflash destroys
sudo ./hosts-pi.sh restore /mnt/usb/pi rbp1  # copy it back, then verify
```

`render` is what to read before a change. `verify` is what to run after one: a host that matches the
repository prints one line and exits 0.

`setup` and `verify` both need root, and now refuse early without it: they read `/etc/sudoers`, which
is `0440 root:root`, and they write the SSH policy. `render` needs nothing.

`PI_ROOT` points every verb at another directory instead of `/`, which is how `hack/tests/test_pi_script.py`
runs them without a Pi:

```bash
PI_ROOT=/tmp/fake-pi ./hosts-pi.sh setup rbp0
```

## What it owns, and what it refuses to touch

| Path | Owned | Why |
|---|---|---|
| `/home/vronteix/dockers/docker-compose.yml` | yes | one template in `playbooks/templates/runners/`, rendered per host; the WireGuard port is the only per-host value |
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
| `/home/vronteix/.ssh/authorized_keys` | **no** | the operator's keys: written by the image's `user-data` and, on the cluster nodes, by `roles/accounts`. `setup` reads it and refuses to close password logins when it holds no key |
| `/etc/hosts` | **no** | cloud-init rewrites it from its own template on every boot (`manage_etc_hosts: true`); its copy already names the host and localhost |
| Docker's repository and the four packages | yes | a fresh image has no runtime; `setup` installs the pinned set from `download.docker.com` |
| `pihole.env` | **no** | it holds the Pi-hole panel password and nothing else: `FTLCONF_webserver_api_password`, `0600`, owned by the account that runs compose, gitignored. `setup` requires it and names it when it is missing. The v5 name `WEBPASSWORD` is ignored by this image |
| `wg-easy.env` | **no** | one key, `OAUTH_OIDC_CLIENT_SECRET`, the VPN panel's own client secret in authentik. Gitignored, `0600`, required by `setup` and never written by it; the same value is `WG_EASY_CLIENT_SECRET` in the cluster's `authentik-sso` Secret, because Secrets do not leave the cluster |
| `certs/` | **no** | `tls.pem` and `tls-key.pem`, this host's own leaf from the internal CA (`hack/internal-cert.sh --host <name>`) and the key Caddy serves on 8443. Required by `setup`, never written, and carried off the host by `backup` |
| `config/` (wg-easy's database) | **no** | since wg-easy 15 the panel account, host, port, DNS and peers live in `config/wg-easy.db`, not in an environment file. `backup` copies it with the rest of the stack |
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
  `127.0.0.1`: a container cannot reach the host's loopback. Never `1.1.1.1`: it answers around the
  blocklist.
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
  `hack/pihole/`, so the app password never crosses the LAN in the clear; a reflash mints a new
  certificate and the check fails until that pair is re-exported and committed.

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
- **The gate is the panel's own OIDC client in authentik**, Administrators only. The panel's password
  stays next to it because a cluster that is down cannot answer an OIDC challenge, and the reason this
  arrangement exists is that the VPN panel must work when the cluster does not.
- **Port 8443, not 443**: Pi-hole owns 80 and 443 on these hosts.

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
