# An agent account on the switches, and the key it cannot read

> **In one line.** Each MikroTik switch carries a `deploy` account that may sign in, read and change
> configuration but cannot create a user or change a group, and whose private key sits under a UNIX
> user the agent's shell cannot read — so a prompt injected into an agent can run the apply script and
> cannot walk away with the key.
>
> **Use it when** you are creating that account, rotating its key, or checking what a switch accepts ·
> **Verified** 2026-11-25: the starting state, read from both switches over the API — `admin` and
> `monitoring` only, no SSH key on either, `/ip ssh` password login already off · **Kind:** how-to
>
> **Status:** pending. Nothing below has been run: on 2026-11-25 both switches still held the two
> accounts they had before this page existed. The first run is what turns it into a proved runbook.
>
> **Sources** `ansible/collectors/network-audit.sh`,
> [`network state collector`](../superpowers/specs/2026-11-24-network-state-collector-design.md),
> [`declared host access`](../decisions/2026-11-25-declared-host-access.md)

## What the two accounts are for

One reads and one writes, and neither can do the other's job.

| Account | Group, and what it carries | Signs in with | May |
|---|---|---|---|
| `audit` | `audit` = `read,ssh`; no source-address limit | key only | print, export, monitor — nothing that writes |
| `deploy` | `deploy` = `read,write,ssh,test`; `address=192.168.1.0/24` | key only | change configuration, and nothing about users |
| `admin` | built-in `full` | password | everything; this page does not touch it |
| `monitoring` | built-in `read`, `address=0.0.0.0/0` | password, over the API | what mktxp collects — and it carries `sensitive` and `reboot`, which [the gap below](#the-gap-monitoring-sits-in-the-built-in-read-group) names |

A RouterOS *policy* is one permission badge: `read` is "run a print or an export", `write` is "change
the configuration", `ssh` is "open a session over SSH", and `policy` is "create an account or change
what a group may do".

**`deploy` holds no `policy`.** That is the whole fence: an account that passes the gate can change a
firewall rule, and cannot make itself an administrator. `admin` keeps the password login it already
has, so this account is never the only way in.

**The group is not the built-in `read`.** Measured on `192.168.1.11` on 2026-11-25, that group carries
`local,telnet,ssh,reboot,read,test,winbox,password,web,sniff,sensitive,api,romon,tikapp` — so `sensitive`,
which reads every secret the configuration holds, and `reboot`, which restarts the switch. `deploy`
holds neither, and `test` is there because the apply script pings the switch it just changed.

## Where the key lives

The chain is the point of the card: the agent may *run* a change, and may never *hold* the credential.

```mermaid
flowchart LR
  card["a card"] --> git["git: the configuration, reviewed"]
  git --> script["switch-apply, run as netcfg"]
  script --> key["/home/netcfg/.ssh/switch-deploy<br/>0600, nologin account"]
  key --> sw["deploy@192.168.1.11 and .43"]
  agent["the agent's shell, as vsynthese"] -. "cannot read" .-> key
```

The custodian is the machine the agent's shell runs on, under a UNIX account of its own, `netcfg`,
whose shell is `/usr/sbin/nologin`. It exists to hold one file and to run one command through a
sudoers rule that card 698 adds.

**Why not a cluster node.** The agent holds `~/.ssh/homelab_live`, which is root on the four hosts, so
any key placed on a node is one `sudo cat` away from the agent. On the machine it runs on, `sudo`
asks for a password no prompt can type, and that is the only boundary in the estate that the agent's
own compromise does not cross.

## Before you start

- **An admin session on the switch** — `admin`, or any account in `full`. The commands below run in
  that session, not in the agent's shell.
- **The measured versions:** `192.168.1.11` runs RouterOS `6.49.8` (long-term) and `192.168.1.43`
  `6.49.7` (stable). Both are version 6, where the setting is `always-allow-password-login`; on 7.21
  and later it is `password-authentication`, and its default, `yes-if-no-key`, already refuses a
  password to any user holding a key. Card 696 is what moves them there, and it has to correct this
  page on the way.
- **The key's public half** on the switch, so the import can read it.

## 1. Make the key where the agent cannot read it

On the machine the agent's shell runs on:

```bash
sudo useradd --create-home --home-dir /home/netcfg --shell /usr/sbin/nologin netcfg
sudo install -d -o netcfg -g netcfg -m 700 /home/netcfg/.ssh
sudo -u netcfg ssh-keygen -t ed25519 -N '' -C "switch-deploy" \
  -f /home/netcfg/.ssh/switch-deploy
```

Expected: two files. `ssh-keygen` prints the fingerprint, which is the one thing worth pasting into
the card. The private half is `0600`, owned by `netcfg`; a directory that lets anyone list it or read
it is the defect this step exists to avoid.

## 2. Create the account, on each switch

Copy the public half to the switch, then in the admin session:

```
/file print
/user group add name=deploy policy=read,write,ssh,test
/user add name=deploy group=deploy address=192.168.1.0/24 password="<paste a random value>"
/user ssh-keys import public-key-file=switch-deploy.pub user=deploy
/ip ssh set always-allow-password-login=no
/file remove switch-deploy.pub
```

Generate the password on the laptop with `openssl rand -hex 24` and paste it. It is never recorded,
and it cannot be typed anywhere useful: a user holding a key is refused a password login, so the
account has no password path at all. It exists so that an edit which turns password login back on
does not hand out a guessable account — an empty password is a blank login, a random one is not.

`/ip ssh` already reads `always-allow-password-login: no` on both switches, measured on 2026-11-25;
the command above declares that state instead of changing it.

`address=192.168.1.0/24` is a prefix the switch enforces: the account logs in from a matching source
address and from nowhere else. Narrowing it to the one address the custodian appears from is tighter
still, and breaks the day that address moves; the card chose the LAN.

Do it on the second switch too, with its own copy of the import.

## 3. Prove it

**The agent cannot read the key.** In the agent's shell:

```bash
cat /home/netcfg/.ssh/switch-deploy
test -r /home/netcfg/.ssh/switch-deploy && echo READABLE || echo "not readable"
```

Expected: `Permission denied`, then `not readable`. A `READABLE` here is the whole card failed, not a
detail: stop and fix the ownership.

**The key works, and the group is the fence.** As the operator — the only account that may use the
sudo rule, because only it can type the password sudo asks for:

```bash
sudo -H -u netcfg ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  -i /home/netcfg/.ssh/switch-deploy deploy@192.168.1.11 '/system identity print'
sudo -H -u netcfg ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  -i /home/netcfg/.ssh/switch-deploy deploy@192.168.1.11 '/user add name=probe group=full'
```

Expected: the identity prints, and the second answers `not enough permissions` and creates nothing.
`BatchMode=yes` is what keeps a refused key from turning into a password prompt, and `accept-new`
settles the switch's host key on the first contact, in `netcfg`'s `known_hosts` rather than the
operator's.

**Another subnet is refused.** The key must not move to the other machine, so the source filter is
tested with a throwaway account that has a password both machines can type. In the admin session:

```
/user add name=deploy-probe group=deploy address=192.168.1.0/24 password="<temporary, known>"
```

From a laptop on the LAN, then from a WireGuard client or any address outside `192.168.1.0/24` that
routes to the switch:

```bash
ssh deploy-probe@192.168.1.43 '/system identity print'
```

Expected: the LAN attempt prints the identity; the outside attempt is refused. The evidence is the
switch's log — `/log print where message~"deploy-probe"` shows a `logged in` line for the LAN address
and a `login failure` for the other one. A source that cannot route to the switch proves nothing; it
has to arrive and be turned away there.

Then remove it, in the same session:

```
/user remove deploy-probe
```

The agent's own shell is *inside* the prefix as the switch sees it — WSL reaches the LAN through its
host's address — so the agent is never the "other subnet" for this test.

## The gap: `monitoring` sits in the built-in `read` group

Measured on 2026-11-25, both switches carry `monitoring` with `group=read` and
`address=0.0.0.0/0`. It therefore holds `sensitive` and `reboot` from any address, and its password
lives in the `monitoring/mktxp-conf` Secret. That is the same defect this page exists to avoid for
`deploy`, on the account that predates it; it is filed as its own card rather than changed here,
because mktxp is a running service and the move needs its egress address pinned.

## Rotate a key, or retire the account

1. **Rotate:** replace `/home/netcfg/.ssh/switch-deploy`, then `/user ssh-keys remove [find user=deploy]`
   and import the new public half. The old key stops working when the import lands.
2. **Retire:** `/user remove deploy`, then `/user group remove deploy`, then delete the two files under
   `/home/netcfg/.ssh` and the `netcfg` account if nothing else uses it.
3. **A session already open stays open** until it ends. The device removes the credential; it does not
   close a connection that has already authenticated.

## What this page does not cover

- **The apply script and its revert timer** are card 698: this page creates the account it runs as, and
  nothing that applies a change.
- **The `audit` account and the collector.** Neither switch held an `audit` user on 2026-11-25, so
  `ansible/collectors/network-audit.sh` cannot run against a device yet. Creating it is the same
  procedure with `policy=read,ssh`, and its commands are in `ansible/README.MD`.
- **The `admin` password and the Livebox** are card 695.
