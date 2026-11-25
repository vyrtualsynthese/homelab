# The two Pis

`hosts-pi.sh` is the only thing that configures `rbp0` and `rbp1`. They are not patched, they are
reflashed: the image goes onto the USB disk, the Pi boots, and this script puts back what the image
does not know. A fresh Image has no Ansible and no key, which is why it is bash.

## The verbs

```bash
./hosts-pi.sh render rbp1          # print every file this script owns; changes nothing
./hosts-pi.sh setup  rbp1          # install the engine, write them, start the stack, print changed=N
./hosts-pi.sh verify rbp1          # name every difference; exit 1 if one needs an apply
./hosts-pi.sh backup /mnt/usb/pi rbp1   # copy the state a reflash destroys
./hosts-pi.sh restore /mnt/usb/pi rbp1  # copy it back, then verify
```

`render` is what to read before a change. `verify` is what to run after one: a host that matches the
repository prints one line and exits 0.

`PI_ROOT` points every verb at another directory instead of `/`, which is how `hack/tests/test_pi_script.py`
runs them without a Pi:

```bash
PI_ROOT=/tmp/fake-pi ./hosts-pi.sh setup rbp0
```

## What it owns, and what it refuses to touch

| Path | Owned | Why |
|---|---|---|
| `/home/vronteix/dockers/docker-compose.yml` | yes | one template in `playbooks/templates/runners/`, rendered per host; the WireGuard port is the only per-host value |
| `etc-pihole-updatelists/pihole-updatelists.conf` | yes | the file Pi-hole's list updater reads; without it Docker makes the source a directory |
| `etc-dnsmasq.d/02-my-wildcard-dns.conf` | yes | the wildcard names the LAN resolves through this Pi-hole |
| `/etc/hostname`, `/etc/hosts` | yes | the name Pi-hole's DNS and the compose file depend on |
| Docker's repository and the four packages | yes | a fresh image has no runtime; `setup` installs the pinned set from `download.docker.com` |
| `pihole.env`, `wg-easy.env`, `.env` | **no** | they hold the Pi-hole password and the WireGuard key, and they are gitignored. `setup` requires them and names the one that is missing |
| `/boot/firmware/config.txt`, `cmdline.txt` | **no** | see below |

Both config files live inside the stack directory, so `backup` and `restore` carry them with the
rest of it, and `verify` names a difference in either.

### The engine, which `setup` installs

A reflashed image has no container runtime, and the stack is four containers: `setup` installs
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
cloud-init wrote, and `pihole.env`/`wg-easy.env` do not exist — `setup` refuses both, correctly, since
it will not configure a host as something else and will not invent a secret. `restore` puts the
backup back, and `setup` then finds a host that already calls itself `rbp1`.

`rbp1` goes first. `rbp0` answers LAN DNS and WireGuard while it is down, and the reverse order takes
the house's resolver offline with nothing to fall back to.

`restore` does not write `cmdline.txt` back: it names the `PARTUUID` of the disk the image was
written to, which is not the disk it is being restored onto. The copy stays in the backup and the
script says so; compare it by hand if the boot arguments need changing.

### The backup is only as good as its size

`etc-pihole/gravity.db` alone is tens of megabytes, and the blocklists with it. A backup that reports
a few hundred kilobytes skipped the database — the check is in the script's last line, and it is the
one to read.
