# Internal zone pre-flight, measured — resolvers, phone and DHCP

> **In one line.** Which resolvers answer `*.kub.local` on 2026-11-27, what `kub.internal` returns
> before the rename, and which readings the phone and the nodes could not supply — the facts the
> `.kub.internal` change has to be judged against.
>
> **Use it when** the phone cannot open an internal name, or a LAN client resolves one name and not
> another · **Verified** 2026-11-27 · **Kind:** record
>
> **Sources** `dig` against `192.168.1.22`, `.38` and `.1`; `adb` over wireless debugging on the
> phone; `/etc/resolv.conf` on the four nodes — refused, see below.

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
| Phone, Wi-Fi (`192.168.1.50`) | `rbp0` Pi-hole | `192.168.1.46` | `192.168.1.46` (`grafana.kub.internal`) |

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

Measured on 2026-11-27 over `adb` on the house Wi-Fi, tunnel down — `ip link` on the device shows no
`tun` or `wg` interface — with the phone at `192.168.1.50`.

- Private DNS: `Off` (`settings get global private_dns_mode` reads `off`; a leftover
  `private_dns_specifier` names `pihole.app.local` and does nothing while the mode is off).
- Resolver: the `rbp0` Pi-hole. Its query log carries the phone's own `A grafana.kub.internal`
  lookups from `192.168.1.50`, answered `192.168.1.46`.
- `https://grafana.kub.internal/`: resolves, connects, reaches the sign-in page.
  `http://sonarr.kub.local/` follows the one-hop redirect to `sonarr.kub.internal` and ends at the
  same page.
- IP settings: `192.168.1.50`; whether the lease is static was not read, and it no longer decides
  anything now that the resolver is proven.

**The blocker was not DNS.** The phone resolved the name for hours while every page failed, because
Android 17 (SDK 37) gates local network access per application. On Firefox,
`android.permission.ACCESS_LOCAL_NETWORK` was `granted=false` with `USER_SET|USER_FIXED`, a state
that suppresses the permission prompt as well as the access, so each request to a LAN address timed
out inside the browser while the same phone's shell pinged and connected. Chrome named it plainly:
`ERR_LOCAL_NETWORK_PERMISSION_MISSING`. Granting the permission turned "Unable to connect" into the
certificate page, and installing `local-ca.pem` as a user CA with
`security.enterprise_roots.enabled` at `true` finished it.

## A second device, and the traps it exposed

A Samsung A22 5G on Android 13 (`192.168.1.15`) was brought up the same day, and it needed three
decisions that are about the device, not about this zone. A third phone will need all three.

- **The platform gates the local network per application.** Android 17 refuses every request an app
  makes to a LAN address until `android.permission.ACCESS_LOCAL_NETWORK` is granted, while the same
  phone's shell pings that address without trouble. Firefox can hold the permission at
  `granted=false` with `USER_SET|USER_FIXED`, which suppresses the prompt as well as the access, so
  it never asks. Android 13 has no such permission and needs no grant.
- **Firefox's DNS-over-HTTPS bypasses the Pi-holes.** With DoH on, `*.kub.internal` never reaches a
  resolver that holds the record, and Firefox reports the result as a certificate failure rather
  than a lookup one. DoH has to be off, or the zone exempted, before the certificate question can be
  asked at all.
- **Firefox keeps its own trust store.** `about:config` is absent from Firefox for Android release —
  `about:about` lists no `about:config` and the page does not open — so
  `security.enterprise_roots.enabled` cannot be set there. The route that works is Settings →
  About Firefox → tap the logo five times → **Secret Settings** → *Accept third-party
  certificates*. Chrome reads the Android user store and needs the certificate alone.

With those settled, both Chrome and Firefox reach the authentik sign-in on
`https://grafana.kub.internal/` from that phone.

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

The first branch is the one that holds: the phone resolves through a Pi-hole with Private DNS off, so
the rename fixes it, and card 894 is about handing out both Pi-holes rather than about this device.

## What this record does not cover

- **Devices beyond these two.** The permission and the certificate were granted on both phones on
  2026-11-27; a third device repeats the same decisions, and every app that ships its own trust
  store needs its own copy of the root. Neither phone's static-or-DHCP lease was read.
- **The four nodes' resolvers.** `ssh` was refused; the files were not read.
- **What the DHCP lease hands out.** That the Livebox's own resolver has no internal names does not
  say which DNS server it puts in the lease. The phone resolving `kub.internal` is evidence that the
  lease or that client's static setting reaches a Pi-hole, but the lease option itself was not read
  on 2026-11-27.
- **Any other name.** `grafana.kub.local` and the bare `kub.internal` are the two that were asked;
  the rest of the zone is assumed to behave alike because it is written the same way, not because
  it was queried.
