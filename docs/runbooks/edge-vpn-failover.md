# The edge VPN pair: who holds the address, and what happens when it moves

> **In one line.** `rbp0` and `rbp1` run one wg-easy database, and keepalived moves one LAN address
> between them, so a client config dialling `80.14.57.209:51821` keeps working when either Pi dies.
>
> **Use it when** a VPN client stops reconnecting, nobody seems to hold `192.168.1.9`, the standby's
> copy of the panel refuses to update, or the router's forward has to be pointed or read back ·
> **Verified** 2026-11-27 for the rendering, the rules and the repository checks; the acceptance
> numbers are filled in by the first live run · **Kind:** how-to
>
> **Sources** `ansible/pi/hosts-pi.sh`,
> `ansible/playbooks/templates/runners/keepalived.conf`,
> `ansible/playbooks/templates/runners/wg-vrrp-notify.sh`,
> `ansible/playbooks/templates/runners/wg-snapshot.sh`,
> `ansible/playbooks/templates/runners/wg-sync.sh`,
> `ansible/playbooks/templates/runners/docker-compose.yml`,
> [`pi-reflash.md`](pi-reflash.md),
> [`../reference/alerts.md`](../reference/alerts.md), [`../reference/metrics.md`](../reference/metrics.md),
> [`../agent-access.md`](../agent-access.md)

## The pair

The two edge Pis serve one VPN, not two look-alikes.

| | `rbp0` | `rbp1` |
|---|---|---|
| Role | authority, `MASTER` at priority `150` | standby, `BACKUP` at priority `100` |
| Address | `192.168.1.22` | `192.168.1.38` |
| Panel | `https://vpn-rbp0.kub.local:8443` | `https://vpn-rbp1.kub.local:8443` |
| Tunnel port | `51821` | `51821` |
| Snapshot timer | `wg-vpn-snapshot.timer` | — |
| Pull timer | — | `wg-vpn-sync.timer` |

- **One database.** `rbp0` makes a consistent SQLite copy (`sqlite3 .backup`) of
  `/home/vronteix/dockers/config/wg-easy.db` every minute and leaves it at
  `/var/lib/wg-easy-sync/wg-easy.db`. `rbp1` pulls it over SSH every minute and replaces its own copy
  only when something changed.
- **One address.** keepalived moves `192.168.1.9/24` on `eth0` from `rbp0` to `rbp1` when `rbp0` stops
  advertising. The router's only WireGuard forward, `51821/udp`, points at that address, so the public
  endpoint never changes. The WireGuard listener is published on `0.0.0.0:51821` by both hosts, so the
  address can arrive after the container starts.
- **One authority.** When the two panels disagree, `rbp0` is right and `rbp1` moves. A change made on
  the standby is refused, never erased silently.

The full reasoning, the alternatives and the rollout order are in
[the design](../superpowers/specs/2026-11-26-edge-vpn-failover-design.md).

## Who holds the address

Run these on the Pi you are asking about:

```bash
cat /run/wg-vpn/vrrp.state          # master | backup | fault | stop
ip -4 addr show eth0                # the holder carries 192.168.1.9/24
systemctl status keepalived         # active (running) on both, whatever the state
```

`vrrp.state` is written by keepalived's notify script on every transition, so it is the fast answer.
`ip -4 addr show` is the proof: the state file can say `master` for a few seconds before the address
appears, and it can be stale if the notify script never ran.

From Prometheus, or through a port-forward, the same fact is one series:

```promql
wg_vpn_vrrp_master
```

It is `1` on the holder and `0` on the other host, with the Pi's `instance` label. A missing series
means node-exporter is not reading the textfile directory, not that the address is free. The
expected state is **exactly one** host at `1`; two at `1` is the split brain described below.

## The acceptance test

The test stops keepalived on the holder, which is what a dead Pi does, and times how long a client
takes to come back. Run it from a client that dials `80.14.57.209:51821` through the router; a client
on the LAN can use the same address (the router's hairpin works).

1. Note the holder and its recovery number before you start. On the holder:

   ```bash
   sudo systemctl stop keepalived
   ```

2. Time a **busy** client — one with a terminal session or traffic flowing — from the stop until it
   answers again. Then time an **idle** client, one that is connected but has sent nothing for a
   while. The idle client rehandshakes at its next keepalive, so it is always the slower number.
3. Put the pair back on the authority:

   ```bash
   sudo systemctl start keepalived
   ```

   `preempt_delay 300` means the old holder waits up to five minutes before taking the address back.
   That is the design: a Pi coming out of a reboot must not steal the address from a healthy
   standby. The address staying on the other host for a few minutes is not a fault.
4. Record both numbers here, with the date and the panel's `PersistentKeepalive` value. The target is
   **in use ≤ 15 s, idle ≤ 30 s**. The protocol retries a handshake 15 s after data goes unanswered,
   and an idle client waits for its own keepalive first, which the panel sets to 10 s.

| Date | Holder stopped | Busy client | Idle client | Keepalive | Notes |
|---|---|---|---|---|---|
| 2026-11-27 | — | not measured yet | not measured yet | 10 s | first live run fills this row |

A number outside the target is written down as measured, never rounded to it. The session cannot
move with the address: the new host has not seen the old session, so recovery always costs one
handshake.

## When the two databases disagree

The pull refuses to overwrite a database that was edited on `rbp1`. The service fails, writes
`wg_vpn_sync_diverged 1`, and names the tables whose rows differ:

```
sync: /home/vronteix/dockers/config/wg-easy.db differs from the stamp at
/var/lib/wg-easy-sync/last-installed; these tables moved:
clients_table
```

The five tables are `interfaces_table`, `clients_table`, `user_configs_table`, `hooks_table` and the
non-session columns of `users_table`. A panel login does not count as an edit: `users_table`'s
`updated_at` column is deliberately excluded.

Decide first, then run one of these:

- **Keep the change.** Apply the same edit in `rbp0`'s panel (for a peer, at the next free tunnel
  address), wait for the next snapshot, then force the copy on `rbp1`.
- **Lose the change.** Force the copy. The standby's edit is what disappears; `rbp0` is untouched.

On `rbp1`, with a checkout of `ansible/` beside the script:

```bash
sudo ~/hosts/pi/hosts-pi.sh vpn-sync rbp1 --force
sudo ~/hosts/pi/hosts-pi.sh verify rbp1
```

`vpn-sync` runs the installed `/usr/local/lib/wg-vpn/wg-sync.sh` with `WG_SYNC_FORCE=1`, so what runs
by hand is what the timer runs a minute later. It stops `wg-easy`, installs the authority's copy,
starts the panel again and writes the new fingerprint to `/var/lib/wg-easy-sync/last-installed`.
The panel restart resets the standby's login sessions: sign in again.

A host that has never installed a copy — a freshly restored one, with no stamp — is refused the same
way. The reflash procedure puts `restore` → `setup` → `vpn-sync --force` → `verify` in that order for
exactly this reason.

## Before the first `setup` on a Pi

The firewall rule for VRRP has no port. VRRP is IP protocol `112`, and `ufw` takes the protocol by
name; a `112/udp` rule would match nothing and still read as protection. Prove this `ufw` build
accepts the name before `setup` applies it:

```bash
# on rbp0, allowing the other Pi's advertisements
sudo ufw --dry-run allow from 192.168.1.38 proto vrrp

# on rbp1, the other direction
sudo ufw --dry-run allow from 192.168.1.22 proto vrrp
```

The dry run prints the rule it would add and changes nothing. `setup` then writes one rule per peer
address in each direction, so an advertisement from a host's own address can never be accepted as a
second master.

## The router

The forward table is edited through the Livebox's own sysbus at `http://192.168.1.1/ws`. The login,
the `createContext` call and the two headers every later call carries are in
[the agent access page](../agent-access.md#the-router-and-the-login-it-takes); do not repeat the
password here.

Two writes, in this order, **after** keepalived holds the address:

1. `Firewall/setPortForwarding` on the existing rule `webui_wireguard0`, changing its destination to
   `192.168.1.9`. Its external port stays `51821/udp`.
2. `Firewall/deletePortForwarding` on `webui_wireguard1` (the `51822` rule). The call needs both `id`
   and `origin`; the rule is not removed with `id` alone.

Then read the table back. This, not the `status 0` answers, is the proof:

```
webui_wireguard0   51821/udp → 192.168.1.9
```

There must be no `webui_wireguard1` row, and no other rule pointing at `192.168.1.22` or
`192.168.1.38`. Applying the first call before keepalived holds `192.168.1.9` points the public port
at an address nothing answers, which is the outage this pair exists to remove.

To undo it, point `webui_wireguard0` back at `192.168.1.22` and recreate a `51822/udp` rule to
`192.168.1.38`. A deleted rule is created again; it does not come back from a backup.

## What this does not cover

- **Sessions do not move with the address.** A client must handshake again, and the number in the
  table above is that cost. The panel login is local to the host that served it: a session on
  `vpn-rbp1` does not become a session on `vpn-rbp0`, and installing a database on the standby ends
  its sessions.
- **The two hosts are not a cluster.** There is no quorum and no shared lock: both can hold
  `192.168.1.9` if the LAN ever lets them answer each other's advertisements, which is the split
  brain. On one switched segment this cannot happen — a host that cannot hear its peer cannot reach
  the router either — but a segmented LAN or a second path between them would need a different
  design. Look for two hosts at `wg_vpn_vrrp_master 1` before assuming a single holder.
- **Only the shared address is supported.** Both hosts use the same server key, so a LAN client that
  dials a Pi's own address (`192.168.1.22` or `192.168.1.38`) bypasses the pair and gets that host's
  answer. Hand out configs that name `80.14.57.209:51821` only.
- **DNS is not part of this.** The two Pi-holes already serve both addresses, and no name follows the
  address.
- **The four alert rules live in Grafana's database, not in git.** They are re-created from
  [`../reference/alerts.md`](../reference/alerts.md), which is the record.
