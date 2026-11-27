# The internal zone is `kub.internal`, and `*.kub.local` redirects until 2027-01-01

> **In one line.** Every Traefik-served internal name is `<app>.kub.internal`, answered by the two
> edge Pi-holes and served by the ashulab internal CA. The old `*.kub.local` names answer through one
> wildcard redirect for a 30-day soak, then that redirect, the old SAN and the old Pi-hole wildcard
> are removed together.
>
> **Use it when** you write an internal hostname in a chart, a blueprint, a runbook or a bookmark, or
> you ask why `CN = kub.local` is still on the leaf · **Verified** 2026-11-27: the preflight
> measurements, the Pi-hole and CoreDNS answer, and a rename across 23 names and 20 charts ·
> **Kind:** record
>
> **Sources** [`../audit/internal-zone-preflight-2026-11-27.md`](../audit/internal-zone-preflight-2026-11-27.md),
> [`../how-it-works/network.md`](../how-it-works/network.md),
> [`../runbooks/internal-ca.md`](../runbooks/internal-ca.md),
> `cluster/helm/platform/traefik-gate/templates/legacy-redirect.yaml`

Date: 2026-11-27
Decided by: the operator
Status: active

## The decision

Every Traefik-served internal name is `<app>.kub.internal`, served by the ashulab internal CA. The
Pi-holes answer the wildcard and CoreDNS forwards it. `*.kub.local` is answered by one wildcard
redirect for a 30-day soak, then removed with the old SAN and the old Pi-hole wildcard.

## Why

An Android phone on the house Wi-Fi could not open any `*.kub.local` name, while the same names
worked over WireGuard. Android sends `.local` to mDNS `224.0.0.251:5353` and never to the configured
DNS; VPN networks are excluded from that. No mDNS responder exists on the LAN, so the name timed out.
RFC 6762 reserves the suffix; `kub.home.arpa` was rejected as residential-CPE framing around a name
that must stay short, and the public `*.kub.home.ashudev.com` would make every internal app
internet-resolvable.

The measurements are in `docs/audit/internal-zone-preflight-2026-11-27.md`.

## What it costs if it is wrong

A rename across 23 names, 20 charts, the authentik blueprints and the internal leaf: the rollback is
`git revert` until the cleanup, so the cost is one branch, not a rebuild. The soak keeps every saved
URL alive; the cleanup is a separate change with its own review. If the phone still asks the Livebox
after the change, the zone is right and the resolver is wrong, which is card 894.
