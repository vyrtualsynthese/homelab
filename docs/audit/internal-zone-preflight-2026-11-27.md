# Internal zone pre-flight, measured — resolvers, phone and DHCP

> **In one line.** Which resolvers answer `*.kub.local` on 2026-11-27, what `kub.internal` returns
> before the rename, and which readings the phone and the nodes could not supply — the facts the
> `.kub.internal` change has to be judged against.
>
> **Use it when** the phone cannot open an internal name, or a LAN client resolves one name and not
> another · **Verified** 2026-11-27 · **Kind:** record
>
> **Sources** `dig` against `192.168.1.22`, `.38` and `.1`; `/etc/resolv.conf` on the four nodes and
> the phone's Settings screens — both attempted, neither read, see below.

Renaming the zone is not the same as fixing the phone. `kub.internal` becomes resolvable the day a
record for it exists, but a name only answers on a device that asks a server holding it. This page
records the second half — who asks whom — before anything moves, so the change can be judged against
a measurement. The chain itself is described in
[the network page](../how-it-works/network.md).

| Where | Resolver | `grafana.kub.local` | `kub.internal` |
|---|---|---|---|
| `rbp0` Pi-hole | itself | `192.168.1.46` | NXDOMAIN (no record) |
| `rbp1` Pi-hole | itself | `192.168.1.46` | NXDOMAIN (no record) |
| Livebox `192.168.1.1` | upstream | NXDOMAIN | NXDOMAIN |
| Phone, Wi-Fi | not measured — operator | not measured — operator | n/a |

The commands behind the table, run from this session on 2026-11-27:

```bash
dig +short grafana.kub.local @192.168.1.22   # 192.168.1.46   status: NOERROR
dig +short grafana.kub.local @192.168.1.38   # 192.168.1.46   status: NOERROR
dig +short grafana.kub.local @192.168.1.1    # (empty)        status: NXDOMAIN
dig +short kub.internal @192.168.1.22        # (empty)        status: NXDOMAIN
dig +short kub.internal @192.168.1.38        # (empty)        status: NXDOMAIN
dig +short kub.internal @192.168.1.1         # (empty)        status: NXDOMAIN
```

`192.168.1.22` is `rbp0` and `192.168.1.38` is `rbp1`; the lease list that ties the addresses to the
two Pis is in [the Livebox record](livebox-2026-11-26.md#the-addresses-the-box-hands-out).

Three things the table says besides the empty cells:

- **The Pis do not serve a zone; they hold records.** A name under `kub.local` that has no record,
  `random.kub.local` for example, comes back `NOERROR` with no answer, and a recursive lookup of
  `kub.local` itself ends at the root servers' SOA. The Pi-holes forward the name upstream like any
  other, so an internal name answers only because a local DNS record was written for it.
- **`.internal` will answer differently, and that is fine.** `kub.internal` sits under a reserved TLD
  that no registry operates, so a query for it returns `NXDOMAIN` — no upstream can ever shadow the
  name later the way a real delegation could. The rename changes a not-there answer from `NOERROR` to
  `NXDOMAIN`; both mean "no name".
- **The Livebox forwards but knows nothing local.** It resolves `example.com` normally
  (`104.20.23.154`, `172.66.147.243`), so it is a working resolver that simply has no view of the
  internal names. A client pointed at the box gets a fast `NXDOMAIN`, not the mDNS timeout the phone
  is reported to show — which is the distinction the phone rows exist to settle.

## The phone

- IP settings: not measured — operator
- Private DNS: not measured — operator
- `grafana.kub.local`, tunnel off: not measured — operator

The phone is the device this change exists for and it cannot be reached from a session. The three
values are read from its Settings screens by the operator, on the house Wi-Fi with the WireGuard
tunnel off, and stay marked until they are:

1. IP settings and, when static, DNS 1 and DNS 2, from the network's gear icon.
2. Private DNS: `Off`, `Automatic`, or a hostname.
3. `https://grafana.kub.local`, recording a timeout (the mDNS failure) or a certificate warning (the
   name resolved).

## The nodes

| Node | `/etc/resolv.conf` nameservers |
|---|---|
| `nuc0` | not measured — ssh permission denied (publickey) |
| `nas` | not measured — ssh permission denied (publickey) |
| `streaming` | not measured — ssh permission denied (publickey) |
| `rbp2` | not measured — ssh permission denied (publickey) |

The read is `cat /etc/resolv.conf` over `ssh` to each node. All four refused the key
(`vsynthese@<node>: Permission denied (publickey).`, exit 255), so no node's file was read and
nothing was guessed in its place. The resolver lines card 889 was to cite are therefore absent, and
the same four commands have to run from a shell whose key the nodes accept.

## What this decides

- Phone resolver is a Pi-hole, Private DNS off or automatic → the phone is fixed by this change.
- Phone resolver is the Livebox → the rename still ships (decided 2026-11-27); the phone stays
  unverified until card 894 hands out the Pi-holes, or the operator sets a static DNS on the phone.
- Private DNS names a hostname → it overrides the network and must be turned off for the gate to
  pass.
- A node still names a public resolver → `ansible/roles/resolver` runs against it in Task 10.

None of the four is chosen yet: the phone rows are empty, so the page says the Pi-holes answer and
the box does not, and stops there.

## What this record does not cover

- **The phone.** Every phone field above is unfilled by design, not by oversight.
- **The four nodes' resolvers.** `ssh` was refused; the files were not read.
- **What the DHCP lease hands out.** That the Livebox's own resolver has no internal names does not
  say which DNS server it puts in the lease. A client that resolves `kub.local` is evidence that the
  lease or that client's static setting reaches a Pi-hole, but the lease option itself was not read
  on 2026-11-27.
- **Any other name.** `grafana.kub.local` and the bare `kub.internal` are the two that were asked;
  the rest of the zone is assumed to behave alike because it is written the same way, not because
  it was queried.
