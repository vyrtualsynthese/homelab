# The two Pis

`hosts-pi.sh` is the only thing that configures `rbp0` and `rbp1`. They are not patched, they are
reflashed: the image goes onto the USB disk, the Pi boots, and this script puts back what the image
does not know. A fresh Image has no Ansible and no key, which is why it is bash.

## The verbs

```bash
./hosts-pi.sh render rbp1          # print every file this script owns; changes nothing
./hosts-pi.sh setup  rbp1          # write them, start the stack, print changed=N
./hosts-pi.sh verify rbp1          # name every difference; exit 1 if there is one
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
| `/home/vronteix/dockers/docker-compose.yml` | yes | rendered from `playbooks/templates/runners/<host>/` |
| `/etc/hostname`, `/etc/hosts` | yes | the name Pi-hole's DNS and the compose file depend on |
| `pihole.env`, `wg-easy.env`, `.env` | **no** | they hold the Pi-hole password and the WireGuard key, and they are gitignored. `setup` requires them and names the one that is missing |
| `/boot/firmware/config.txt`, `cmdline.txt` | **no** | see below |

### Why the boot files are backed up and not written

After a reflash the image writes its own `cmdline.txt`, with the `PARTUUID` of the disk it was
written to. A remembered `cmdline.txt` restored onto a differently-enumerated disk makes the Pi
unbootable, and the machine that would fix it is the one that will not start. So `backup` saves the
two files and `restore` puts them back when they are needed; `setup` never invents them.

## The reflash sequence

```bash
# on the Pi, before anything
sudo ./hosts-pi.sh backup /mnt/backup/$(hostname) $(hostname)

# write the image to the USB disk, boot it, then
sudo ./hosts-pi.sh setup  rbp1
sudo ./hosts-pi.sh verify rbp1
```

`rbp1` goes first. `rbp0` answers LAN DNS and WireGuard while it is down, and the reverse order takes
the house's resolver offline with nothing to fall back to.

### The backup is only as good as its size

`etc-pihole/gravity.db` alone is tens of megabytes, and the blocklists with it. A backup that reports
a few hundred kilobytes skipped the database — the check is in the script's last line, and it is the
one to read.
