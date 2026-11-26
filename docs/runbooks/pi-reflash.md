# Reflashing an edge Pi

> **In one line.** The two Pis are rebuilt rather than patched: one image write to the USB disk, one
> cloud-init file on its boot partition, and then the stack goes back from the copy taken before the
> disk was touched.
>
> **Use it when** `rbp0` or `rbp1` has to move to the next Ubuntu LTS, or a Pi has to come back after a
> disk or board failure. The four cluster nodes take a different route: [moving Ubuntu to a new
> LTS](ubuntu-upgrade.md) · **Verified** 2026-11-24: the 26.04.1 image downloaded and its partitions
> read; no Pi has been reflashed with it · **Kind:** how-to
>
> **Status:** pending — the procedure below has been written from the image and the repository, not
> from a run. The first run makes it `proved`.
>
> **Sources** `ansible/pi/hosts-pi.sh`, `ansible/pi/README.md`, `ansible/inventory/hosts.yml`, the `edge`
> and `all` group variables, `ansible/playbooks/templates/runners/`, the 26.04.1 image's `system-boot`
> partition read on 2026-11-24, and [`docs/audit/hosts-2026-11-24.md`](../audit/hosts-2026-11-24.md)

## What this touches

Two Raspberry Pi 4 boards outside the cluster. Each runs four containers from `/home/vronteix/dockers`:
Pi-hole, which is the LAN's DNS; wg-easy, which is the VPN; and two exporters. What each machine is for
is in [the machines](../how-it-works/nodes.md); what it runs today is in the record.

| Host | Today | After | Hops |
|---|---|---|---|
| `rbp1` | 22.04.5, 5.15.0-1105-raspi | 26.04.1, 7.0.0-1017-raspi | none: the disk is overwritten |
| `rbp0` | 24.04.4, 6.8.0-1060-raspi | 26.04.1, 7.0.0-1017-raspi | none: the same |

**`rbp1` goes first.** `rbp0` answers DNS and WireGuard for the house, so while `rbp1` is down it is the
only resolver left. The other order takes the LAN's DNS offline with nothing behind it.

**A reflash is not an upgrade.** Nothing is carried across. The image writes a fresh root, and the state
that matters — Pi-hole's blocklists and query database, wg-easy's database with its peer set and panel
account, and the Pi-hole panel password in `pihole.env` — comes back from the copy taken in step 1. That
is why the Pis skip the two release hops the cluster nodes take.

## The image

Measured on 2026-11-24 by downloading the file and reading its partitions, not from release notes:

| | |
|---|---|
| File | `ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz` |
| URL | `https://cdimage.ubuntu.com/ubuntu/releases/26.04.1/release/ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz` |
| SHA256 | `d59ef9c6e85b906995501f166d00726ab80b59be7b65cdeac0fa31b996053e13` |
| Size | 1,528,288,776 bytes compressed; 4,918,114,816 bytes as an image |
| Built | 2026-08-26 |
| Partitions | 1: `system-boot`, FAT32, 512 MiB, starting at sector 2048 · 2: ext4 `LABEL=writable`, 4.1 GiB |
| Kernel in it | `linux-image-7.0.0-1017-raspi` 7.0.0-1017.17 and its modules, from the image's manifest |
| Firmware | `rpi-eeprom` 28.14-0ubuntu1 |
| Boot line | `current/cmdline.txt` holds `root=LABEL=writable rootfstype=ext4 … rootwait fixrtc`, and `config.txt` carries `arm_64bit=1` |

Verify before writing:

```bash
sha256sum ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz
# d59ef9c6e85b906995501f166d00726ab80b59be7b65cdeac0fa31b996053e13
```

**The image boots to no key.** Its own `user-data` creates an account `ubuntu` with password `ubuntu` and
sets `ssh_pwauth: false`, so a fresh boot has nothing you can log in with over the network. You write your
own `user-data` on the boot partition before the first boot — step 4. The image ships no avahi either, so
`rbp1.local` does not resolve until one is installed; the address comes from the router's DHCP lease.

## Before you start

- **One Pi at a time, `rbp1` first**, with the other answering `dig @192.168.1.22 <a LAN name>`.
- **A copy of the stack off the Pi**, taken in step 1, before the disk is overwritten.
- **`pihole.env` is yours.** It holds the Pi-hole panel password, it is gitignored, and `hosts-pi.sh`
  refuses to run without it rather than inventing one. Every other Pi-hole setting lives in
  `pihole.settings`, which the script renders from one template, so the two hosts cannot drift. wg-easy
  has no environment file since 15: its panel account, host, port, DNS and peers live in
  `config/wg-easy.db`, which `backup` copies.
- **Record what a reflash must not change**: `rpi-eeprom-update` (26.3 on `rbp0`, 13.12 on `rbp1` on
  2026-11-24) and the USB disk's model and serial. The boot order lives in the EEPROM and survives an
  image write, so a version that moves afterwards is a firmware change nobody would notice for a month.
- **Docker is not in the image**, and `setup` installs it (step 6): nothing on this host is installed by
  hand.

## Steps

**1. Back up on the Pi.** The script reads the compose templates from beside itself, so the `ansible/`
tree travels with it. `backup` refuses a directory on the same filesystem as the stack, and the Pi has
only the FAT beside its root, so use a USB stick:

```bash
scp -r ansible vronteix@<host>:~/hosts
ssh vronteix@<host> lsblk -o NAME,SIZE,MODEL,TRAN   # the stick, not the SSD the Pi boots from
ssh vronteix@<host> sudo mount /dev/sdb1 /mnt
ssh vronteix@<host> sudo '~/hosts/pi/hosts-pi.sh backup /mnt <host>'
ssh vronteix@<host> sudo umount /mnt
```

The byte count it prints must be above 100 MB: anything smaller means `etc-pihole/gravity.db` was skipped.
Copy the stick's contents to the workstation before going further.

**2. Write the image.** With the SSD in a USB adapter on the workstation:

```bash
lsblk -o NAME,SIZE,MODEL,SERIAL,TRAN   # the 1 TB Samsung SSD; never a name you assumed
sudo xzcat ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz |
  sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
sync
```

**This destroys the disk it is pointed at.** A wrong letter destroys another one.

**3. Grow nothing yet.** The image is 4.6 GiB and the first boot expands the root partition to the disk.
Leave the partition table alone; step 5 checks the result, and `growpart` plus `resize2fs` is the manual
step if it did not happen.

**4. Put a key on it, or there is no way in.** Mount the boot partition and replace `user-data`:

```bash
sudo mount /dev/sdX1 /mnt
sudo tee /mnt/user-data >/dev/null <<'YAML'
#cloud-config
hostname: rbp1
ssh_pwauth: false
packages: [avahi-daemon]
users:
  - name: vronteix
    groups: [adm, sudo]
    shell: /bin/bash
    sudo: "ALL=(ALL) PASSWD:ALL"
    lock_passwd: true
    ssh_authorized_keys:
      - <one public key, pasted from ~/.ssh/*.pub on this machine>
YAML
sudo umount /mnt
```

A key in the repository is a name, not a credential: `trusted_ssh_keys` is committed on purpose, and a
public half cannot log in without the private half that stays on the laptop that made it. The line
pasted here is one of those keys — **paste one the list already declares**, because the layer's second
run replaces `vronteix`'s file with the declaration and removes a key outside it. It goes in at the
keyboard because a fresh image has no other way in. `PASSWD:ALL` keeps the human account's sudo asking
for a password, which is the layer's rule
([the automation account](../decisions/2026-11-24-automation-sudo-account.md)). Naming `users:` without
`- default` replaces the image's `ubuntu` account rather than adding to it.

**5. First boot, and the checks that say it is the right image.** Power the Pi with the disk attached, find
its address in the router's leases, then:

```bash
ssh vronteix@<address>
lsb_release -ds        # Ubuntu 26.04.1 LTS
uname -r               # 7.0.0-1017-raspi
df -h / /boot/firmware # root grown to the SSD; 497 M on /boot/firmware
findmnt -no SOURCE /   # the USB disk, not an SD card
```

**6. Put the stack back, then let the script install the engine and own its files.**

```bash
scp -r ansible vronteix@<host>:~/hosts          # a fresh image has no checkout
sudo mkdir -p /home/vronteix/dockers
sudo mount /dev/sdb1 /mnt
sudo ~/hosts/pi/hosts-pi.sh restore /mnt/dockers <host>
sudo ~/hosts/pi/hosts-pi.sh setup <host>
sudo ~/hosts/pi/hosts-pi.sh verify <host>
```

`restore` copies the tree and then verifies, so on a fresh image it exits 1 for more than one reason: a
file the copy predates or the image does not have yet, and an engine that is not installed. Both are
`setup`'s job, and the `verify` that follows `setup` is the one whose exit code counts. `setup` writes the
files the stack mounts (`docker-compose.yml`, `pihole.settings`, `etc-dnsmasq.d/02-my-wildcard-dns.conf`),
the resolver list in `/etc/netplan/50-cloud-init.yaml`, the resolved drop-in
(`/etc/systemd/resolved.conf.d/90-pihole-resolvers.conf`), `/etc/hostname`, and the SSH and sudo policy:
the `00-homelab.conf` drop-in that closes password logins, the two directives in the main sshd config,
and the `%admins` grant and its group gone. It leaves the boot files alone: the image's copies are right
for the disk they were written to, and `restore` puts them back only when the backup's `cmdline.txt` names
this disk. It never writes `pihole.env`: after a reflash without the backup, the panel has no password
until one is set again, and the stack will not start without the file. `/etc/hosts` is cloud-init's: the
image rewrites it from its own template on every boot, so a script that owned it would be overwritten and
then report a difference no run could settle. Check the tree is readable by `vronteix` after the copy: a
`cp -a` from a stick keeps the owner it was written with.

A reflashed image is already close to that state, and the script is what settles it either way: this
`user-data` sets `ssh_pwauth: false` and `sudo: ALL=(ALL) PASSWD:ALL`, so it has no password login and no
grant. An image written before this record still carries cloud-init's `50-cloud-init.conf` with
`PasswordAuthentication yes` and the hand-added `%admins` line, and `setup` is what removes them — `verify`
names any of it that comes back. **After `setup`, `sudo` on a Pi asks for `vronteix`'s password**: the
grant is gone, and there is no automation account there until the Pis are reflashed (card #481), so the
operator is the only one who runs root commands on these hosts.

A fresh image also has no container runtime, and `setup` installs that too: `docker-ce`, `docker-ce-cli`,
`containerd.io` and `docker-compose-plugin` from Docker's own repository, then `vronteix` into the `docker`
group. The suite (the release name apt matches on) is read from the image's `/etc/os-release`; measured on
2026-11-25, Docker's repository carried the 26.04 suite (`resolute`) for arm64 with `docker-ce` 29.10.1 and
`docker-compose-plugin` 5.8.0, so Ubuntu's `docker.io` is never used. The packages are pinned by band, and
the pins live in the script's header. This step therefore needs the network, and it runs before the stack
starts. An engine already present outside its band is named and left alone: the script never upgrades a
runtime in place, so a Pi already running 28.3.2 moves on its next reflash, not through `setup`.

**7. Bootstrap the layer.** Two runs, in this order: the first creates the `ansible` account holding the
keys `trusted_ssh_keys` declares — the same list `roles/accounts` writes into `vronteix`, so a reflashed
Pi is adopted by the line that is already there — and the second connects as it. `-K` is needed on the
first because a fresh image's sudo asks for a password.

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit rbp1 -e ansible_user=vronteix --tags automation -K
ansible-playbook playbooks/site.yml --limit rbp1
ansible-playbook playbooks/site.yml --limit rbp1 --check   # expected: changed=0
```

**8. Prove it.**

```bash
docker compose -f /home/vronteix/dockers/docker-compose.yml ps   # four services running
dig @192.168.1.38 <a LAN name> +short                            # Pi-hole answers
dig @192.168.1.38 example.com +short                             # and resolution works
resolvectl query example.com                                     # the host asks Pi-hole, not 1.1.1.1
docker exec wg-easy wg show                                      # the peers are back
curl -s localhost:9902/metrics | head -3                         # the smartctl exporter
```

`resolvectl` is the line that catches the old netplan: an answer that does not come from `192.168.1.22`
or `192.168.1.38` means the host is resolving past its own blocklist.

Then the same steps are repeated for `rbp0`, once `rbp1` answers DNS. After both: run
`ansible/collectors/host-audit.sh` on each host and keep the report beside the 2026-11-24 record, update
the measured line in [the machines](../how-it-works/nodes.md), and prove the pair still agrees:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
python3 hack/check_pihole_parity.py    # result: OK, or the settings that differ
```

The check reads both panels through their own API. It is the one thing `hosts-pi.sh verify` cannot see: a
setting changed in a panel, or a blocklist added to one host, lives in that host's `pihole.toml` and in no
file in this repository.

## The wg-easy panel

Since wg-easy 15 the VPN is configured in a web panel on `http://<host>:5000`, not in files. The panel
keeps everything in `config/wg-easy.db`: the admin account, the address and port clients dial, the DNS
servers and allowed IPs handed to new clients, and the peer list itself. `backup` copies that file with
the rest of the stack, so a reflash brings the panel back as it was, peers included.

- **The panel is plain HTTP, and the compose says so.** `INSECURE=true` is what makes it accept its own
  login cookie over HTTP; drop the variable and the panel answers on HTTPS and refuses to log in. Port
  5000 is not forwarded from the router, so the panel is reachable from the LAN only.
- **The published UDP port must equal the panel's port.** The interface listens inside the container on
  the port the panel names, and the stack maps the same number to the host (`51821` on `rbp0`, `51822` on
  `rbp1`). Changing one without the other leaves clients dialling a closed port: the tunnel is up in the
  panel and nothing connects.
- **A v14 environment file in front of this container stops it.** v15 exits on startup when `PASSWORD`
  or `PASSWORD_HASH` is in its environment, naming the migration page. That is why the service takes
  `environment:` and no `env_file:`; the `wg-easy.env` the Pis used to hold was deleted when they moved.
- **The admin password is reset from the host**, not by editing a file:
  `docker compose exec wg-easy cli db:admin:reset` (or `--password <new>`).

## Rolling back

There is no downgrade here either: the way back is the outgoing release's image on the same disk, then
`restore` from the copy taken in step 1. Keep that image file until the new host has answered DNS and
served its stack for a day — `pihole.env` and the state come back from the backup, and nothing on the Pi
writes outside `dockers` except the boot files, which `backup` copies, and the netplan file, which `setup`
rewrites from the template.

If the new image will not boot, the EEPROM's boot order is intact, so the fix is another image write
rather than a firmware change. That is the one failure this procedure cannot walk back one step at a
time, and why the copy in step 1 leaves the Pi first.

## What the first run has to answer

- **Whether `hosts-pi.sh setup` accepts the image's boot files** as they are. The image's `config.txt` uses
  `os_prefix=current/` and a `[tryboot]` section the old disk's copy may not have; the script reproduces a
  file that exists rather than inventing one, and the first run is where that is proved.
- **The uid `vronteix` gets** from cloud-init (1000 unless the file says otherwise) against the one the old
  disk had. A container that writes into a directory owned by another id shows it as a permission error, not
  as a uid mismatch.
