# Moving a MikroTik switch to RouterOS 7

> **In one line.** Each switch takes its last RouterOS 6 through the device's own updater, then jumps
> to `7.24.7` long-term in one more install; the v6 configuration is converted once, on that first
> boot on v7, and the binary backup and the export are off the device before either reboot.
>
> **Use it when** a switch still runs RouterOS 6 and has to follow the maintained line, or a new
> long-term release has to land on one · **Verified** 2026-11-26: `crs326` 6.49.8 → 6.49.23 → 7.24.7
> and `crs309` 6.49.7 → 6.49.23 → 7.24.7, both then on RouterBOOT `7.24.7` · **Kind:** how-to
>
> **Sources** `/system package update` on both devices, MikroTik's *Upgrading to v7* and *Packages*
> manual pages, and the runs recorded under "Measured" · the accounts are
> [`switch-access.md`](switch-access.md), the change procedure with its revert timer is
> [`switch-change.md`](switch-change.md)

## What this touches

| Device | Model | Address | Before | After |
|---|---|---|---|---|
| `crs326` | CRS326-24G-2S+ | `192.168.1.11` | 6.49.8 long-term | 7.24.7 long-term |
| `crs309` | CRS309-1G-8S+ | `192.168.1.43` | 6.49.7 stable | 7.24.7 long-term |

Both are 16 MB of flash and 512 MB of RAM, and both are plain Layer 2 switches: one bridge, every
port in it, a DHCP client for the management address, the two key accounts, and no VLANs, routes or
firewall rules. That is why the conversion is uneventful here; a switch carrying routing, VLANs or
switch rules is a different job.

The `admin` password is the credential: a firmware install is `/system package` plus `/system
reboot`, and `deploy` holds neither `reboot` nor `policy`. Read them from
`~/.config/secrets/homelab.env` (`MIKROTIK_CRS326_PASSWORD`, `MIKROTIK_CRS309_PASSWORD`).

**One switch at a time.** A reboot drops every port on the device it reboots, and each install
reboots it twice (once for the v6 update, once for the v7 conversion).

## Before you start

Four files, two per switch: a binary backup, an export, and a copy of each off the device.

```bash
/system backup save name=pre-v7-<date>
/export file=pre-v7-<date>
```

then, from the workstation:

```bash
scp admin@192.168.1.11:pre-v7-<date>.backup .
scp admin@192.168.1.11:pre-v7-<date>.rsc .
```

The binary backup is loadable only on the same RouterOS version, and it is what a netinstall would
be restored from. The export is the readable half, and it is the one to diff the result against.

**Have a laptop, a cable and the reset button on hand.** The one outcome no command fixes is a switch
that stops booting, and the way out of it is netinstall from a machine plugged into the device.

## The path, and why the last 6 comes first

Measured on these two devices: a 6.49.8 or 6.49.7 switch is offered **7.12.1** on the upgrade
channel, while a 6.49.23 switch is offered **7.24.7** long-term directly. So there is no need for the
intermediate v7 stop — one v6 update, then one v7 conversion.

```bash
# 1. the last v6
/system package update set channel=stable
/system package update check-for-updates
/system package update install

# 2. the v7 conversion, once the switch answers again
/system package update set channel=upgrade
/system package update check-for-updates
/system package update install
```

`check-for-updates` has to settle before `install` is called, or the install has nothing to apply;
ten seconds is enough on these two. `install` starts the download and reboots at the end of it — the
SSH session drops with `Received disconnect … shutdown/reboot`, which is the expected line, not a
failure.

Once the switch runs 7.24.7, put it back on the channel it should follow, in v7's own syntax:

```bash
/system/package/update/set channel=long-term
```

**The 16 MB of flash is not the blocker it looks like.** An arm v7 package is 12.0–13.2 MiB, the
switch has about 2.1 MB free, and the install still works: free flash read 2132 KiB before the v6
update and 2132 KiB after it, while the download status climbed to 12.0 MiB, so the file is staged
outside flash and never lands in it. No netinstall is needed for space on these two models.

## The RouterBOOT firmware rides the same reboot

`/system routerboard print` reports the bootloader separately from RouterOS. After the conversion it
read `upgrade-firmware: 7.24.7` against `current-firmware: 6.48.6` on `crs326`, so:

```bash
/system package uninstall wireless
/system routerboard upgrade
/system reboot
```

The firmware is written during that reboot and `current-firmware` reads `7.24.7` afterwards. Do not
skip it: the v7 system on a v6 bootloader is the pairing MikroTik documents as unsupported.

## What the conversion did to the configuration

Read these back on the switch, not from the export — v7 writes every default into `/export`, so the
export is full of lines that were never configured (`/routing bgp template`, `/routing ospf instance`,
`/interface lte apn`). The objects that matter, and the values measured on both devices after the
conversion:

| Check | `crs326` | `crs309` |
|---|---|---|
| `/interface bridge port print count-only` | 26 (24 ether + 2 SFP+) | 9 (1 ether + 8 SFP+) |
| `/ip address print` | `192.168.1.11/24` from DHCP | `192.168.1.43/24` from DHCP, plus the stock `192.168.88.1/24` |
| `/system identity print` | `CR326` | `CRS309` |
| `/user print` | `admin`, `monitoring` pinned to `192.168.1.27/32`, `deploy` pinned to `192.168.1.0/24` | same |
| `/user group print` | `deploy` = `ssh,ftp,read,write,test` with every other policy denied | same |
| `/ip service print` | `ssh`, `api`, `winbox` open; `ftp`, `telnet`, `www`, `www-ssl` disabled | same |
| `/system swos print` | identity and `allow-from-ports` kept | n/a in use |
| `/system routerboard settings print` | `boot-os: router-os` | `boot-os: router-os` |

The `deploy` key survived the conversion, still listed by `/user ssh-keys print` (`key-type=rsa
bits=4096 info="switch-deploy"` at read-back); card #806 replaced it with ed25519 the same day, and
`/user ssh-keys import` on 7.24.7 removes the uploaded public key itself. The SSH host key is
unchanged — a client that trusted the switch before the upgrade trusts it afterwards, so
`~/.ssh/known_hosts` needs no edit — and `mktxp` reconnects on its own through the `monitoring`
account's API login.

## What is new in v7, and what to do about it

- **A `wireless` package appears.** v6 had it installed, so the conversion installs the v7 twin.
  Neither switch has a radio. `/system package uninstall wireless` then a reboot freed `1832.1KiB`
  on each: free flash went from 1128 to 2960 KiB on `crs309`, 1136 to 2968 KiB on `crs326`.
- **`reverse-proxy` shows up in `/ip service` on 443**, without a disabled flag. It is the listener
  MikroTik's Back-To-Home VPN uses; with no certificate and `back-to-home-vpn: revoked-and-disabled`,
  nothing answers — measured `Connection refused` on 443, and `www` on 80 stays disabled.
- **`/ip cloud` reads `ddns-enabled: auto`**, which is how the converter writes v6's `no`. No
  `dns-name` is published while Back-To-Home is revoked; only the public address is discovered.
- **`/ip ssh` keeps the same rule under a new name.** v7 calls v6's `always-allow-password-login=no`
  `password-authentication`, and its `yes-if-no-key` default still refuses a password to a user
  holding a key.
- **A disabled `ovpn-server1` object exists.** It is v7's stock object, disabled, and answers nothing
  on 1194 — measured `Connection refused`.

## The way back

Three facts decide it, and only the last one is a way out of a switch that will not boot.

- A downgrade to `6.49.23` is allowed by version, but the configuration was converted to v7 once. v7
  converts only on its first boot, so a downgraded switch runs the v6 configuration it had before the
  upgrade, not the one it has now.
- The binary backup can be converted again on v7 with
  `/system backup load name=pre-v7-<date> force-v6-to-v7-configuration-upgrade=yes`, which restores
  the v6 configuration, reboots, and converts it.
- Netinstall is the only recovery from a switch that does not boot. It wipes flash, so the export is
  what puts the configuration back, and it needs the laptop and cable named above.

## Measured

Four installs, each from the `install` call to the first answer from the switch afterwards:

| Switch | Step | Install | Answering again | Elapsed |
|---|---|---|---|---|
| `crs326` | 6.49.8 → 6.49.23 | 02:02:47 | 02:07:21 | 4 min 34 s |
| `crs326` | 6.49.23 → 7.24.7 | 03:25:54 | 03:30:28 | 4 min 34 s |
| `crs309` | 6.49.7 → 6.49.23 | 03:33:52 | 03:37:52 | 4 min 00 s |
| `crs309` | 6.49.23 → 7.24.7 | 03:38:18 | 03:42:50 | 4 min 32 s |

The `wireless` uninstall and the RouterBOOT write cost one more reboot of about a minute each.

## Verified

- 2026-11-26, both devices end to end: the four installs above, the RouterBOOT write on both
  (`current-firmware: 7.24.7`), the `wireless` uninstall on both, and the read-back table above.
- Both switches answer `ping` afterwards, only `ssh` (22), `api` (8728) and `winbox` (8291) accept a
  connection, and `mktxp` reports a fresh uptime for each from Prometheus
  (`mktxp_system_uptime{routerboard_address="192.168.1.11"}` and `…="192.168.1.43"`).
- The link rates survive the conversion: `/interface ethernet monitor [find running=yes] once` reads
  `10Gbps` on every SFP+ port of both switches and `1Gbps` on the gigabit ports that are up.
- The neighbor tables still show the SFP+ link between the two switches, and the collection of
  `switch-deploy` still authenticates in the change script's own account model (key listed, group
  unchanged).
- The change script's first run against a v7 switch is the gate's dry run, `switch-apply --target
  crs326 --change … --dry-run`: measured 2026-11-26, exit 0, the undo rendered from the device's own
  exports, "nothing was written". It ran with the v6-era script, then again with the ed25519 key and
  the RSA one removed.
