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
that matters — Pi-hole's blocklists and query database, the WireGuard peer set, the two env files — comes
back from the copy taken in step 1. That is why the Pis skip the two release hops the cluster nodes take.

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
- **The two env files are yours.** `pihole.env` holds the Pi-hole password and `wg-easy.env` holds the
  WireGuard key. Both are gitignored, and `hosts-pi.sh` refuses to run without them rather than inventing
  one.
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

A public key in the repository is a committed identity, so that line is pasted at the keyboard and never
committed. `PASSWD:ALL` keeps the human account's sudo asking for a password, which is the layer's rule
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

`restore` copies the tree and then verifies, so on a fresh image it exits 1 twice over: the copy's
`docker-compose.yml` may predate the template, and the engine is not installed yet. Both are `setup`'s job,
and the `verify` that follows `setup` is the one whose exit code counts. `setup` writes the two config files
the stack mounts, `/etc/hostname` and `/etc/hosts`, and leaves `/boot/firmware/config.txt` and
`cmdline.txt` alone: the image's copies are right for the disk they were written to, and `restore` puts
them back only when the backup's `cmdline.txt` names this disk. It never
writes the env files. Check the tree is readable by `vronteix` after the copy: a `cp -a` from a stick keeps
the owner it was written with.

A fresh image also has no container runtime, and `setup` installs that too: `docker-ce`, `docker-ce-cli`,
`containerd.io` and `docker-compose-plugin` from Docker's own repository, then `vronteix` into the `docker`
group. The suite (the release name apt matches on) is read from the image's `/etc/os-release`; measured on
2026-11-25, Docker's repository carried the 26.04 suite (`resolute`) for arm64 with `docker-ce` 29.10.1 and
`docker-compose-plugin` 5.8.0, so Ubuntu's `docker.io` is never used. The packages are pinned by band, and
the pins live in the script's header. This step therefore needs the network, and it runs before the stack
starts. An engine already present outside its band is named and left alone: the script never upgrades a
runtime in place, so a Pi already running 28.3.2 moves on its next reflash, not through `setup`.

**7. Bootstrap the layer.** Two runs, in this order: the first creates the `ansible` account from the keys
`vronteix` already accepts, the second connects as it. `-K` is needed on the first because a fresh image's
sudo asks for a password.

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
docker exec wg-easy wg show                                      # the peers are back
curl -s localhost:9902/metrics | head -3                         # the smartctl exporter
```

Then the same steps are repeated for `rbp0`, once `rbp1` answers DNS. After both: run
`ansible/collectors/host-audit.sh` on each host and keep the report beside the 2026-11-24 record, and
update the measured line in [the machines](../how-it-works/nodes.md).

## Rolling back

There is no downgrade here either: the way back is the outgoing release's image on the same disk, then
`restore` from the copy taken in step 1. Keep that image file until the new host has answered DNS and
served its stack for a day — the env files and the state come back from the backup, and nothing on the Pi
writes outside `dockers` except the boot files, which `backup` also copies.

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
