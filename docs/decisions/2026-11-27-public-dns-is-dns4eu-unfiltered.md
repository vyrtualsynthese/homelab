# Public DNS is DNS4EU's unfiltered service, and Cloudflare is out

> **In one line.** Every public name this homelab resolves is asked of DNS4EU's unfiltered resolvers,
> `86.54.11.100` and `86.54.11.200`; Cloudflare is never used, and neither is a filtered DNS4EU
> variant, because Pi-hole is the filter.
>
> **Use it when** you are about to write a `nameserver`, a `dnsConfig`, an upstream in a template, or a
> `dig @` in a runbook · **Verified** 2026-11-27: `ansible/playbooks/templates/runners/pihole.settings`,
> both charts that carry their own resolvers, and every mention of the four addresses in the tree ·
> **Kind:** record
>
> **Sources** [`2026-11-21-sovereign-eu-providers-only.md`](2026-11-21-sovereign-eu-providers-only.md),
> [DNS4EU for the public](https://joindns4.eu/for-public), [how a request arrives](../how-it-works/network.md),
> `cluster/helm/media-stack/qbittorrent/values.yaml`,
> `cluster/helm/monitoring/blackbox-exporter/values.yaml`
>
> **See also** [the public resolver's numbers](../runbooks/cloud-monitoring.md) for the probes that
> depend on this path, and [how a request arrives](../how-it-works/network.md) for where the DNS step
> sits. The node `resolv.conf` that still names a public resolver on `nuc0` is open work, tracked on
> the board as card 889.

Date: 2026-11-27
Decided by: operator
Status: active

## The decision

Every resolver this homelab asks a public question of is DNS4EU's **unfiltered** pair:

- `86.54.11.100`
- `86.54.11.200`

That covers the three places a public resolver is configured. Pi-hole forwards there, for every
client on the LAN and every pod that resolves through the cluster. `qbittorrent` carries the pair in
its own `dnsConfig`, because the torrent client's lookups must not depend on the cluster.
`blackbox-exporter` carries it too, because its probes must see the public zone rather than Pi-hole's
LAN override.

Cloudflare is never used: not `1.1.1.1`, not `1.0.0.1`, not its DoH or DoT endpoints, and not in a
runbook example that someone will paste. Google's `8.8.8.8` and Quad9's `9.9.9.9` are out for the same
reason. The filtered DNS4EU variants — `86.54.11.1`, `.11`, `.12`, `.13` and their IPv6 forms — are out
too.

## Why

**The jurisdiction rule already decided it.** [`2026-11-21-sovereign-eu-providers-only.md`](2026-11-21-sovereign-eu-providers-only.md)
says every service in the path of the operator's data or the cluster's identity runs on a company
headquartered in an EU member state, with the data inside the EU, and names Switzerland as out.
Cloudflare and Google are US companies; Quad9 is a Swiss foundation. DNS4EU is operated by a
consortium of EU member states' providers, and its answering addresses sit in the EU. A resolver sees
every name the house asks for, which is as much of the operator's data as a mail provider does, so the
rule applies without a carve-out.

**Two charts were the exception, and one of them caused a measured fault.** `qbittorrent` and
`blackbox-exporter` had `dnsPolicy: None` with `1.1.1.1` beside a second public resolver; both keep
the setting, on the same reasoning that put it there — neither may depend on cluster DNS — and only the
addresses change. Separately, `nuc0`'s `/etc/resolv.conf` carried `1.1.1.1` beside the Pi-hole on
2026-11-26, so CoreDNS sent about one `*.kub.local` query in three to Cloudflare and got NXDOMAIN
back; `pihole-exporter` warned once a minute until its two names were pinned in the pod's `/etc/hosts`.
A public resolver in the path of the cluster's own names is the failure this decision rules out.

**The unfiltered variant is the only one that composes with Pi-hole.** Pi-hole holds the blocklist,
the allowlist and the local records; a filtered upstream answers a second, invisible policy that no
page in this repository records and that a blocked-site report would have to explain twice.

Options rejected:

- **Keep Cloudflare for its latency.** It is fast and it is not in the EU.
- **Quad9**, on the argument that it is European. Switzerland is out by the same record.
- **A filtered DNS4EU variant**, on the argument that the house would get ad blocking at the resolver
  too. Pi-hole already blocks, so the second filter only makes a blocked name harder to explain.
- **One resolver instead of the pair.** Two answering addresses survive one being unreachable; the
  second is the reason both charts list two.

## What it costs if it is wrong

- **A DNS4EU outage stops resolution for the LAN and the cluster.** Both Pi-holes forward to the same
  pair, and so do the two pods. The mitigation is the second address; if both are down, the fallback
  is to name another EU resolver — OVHcloud or Scaleway run one — and not to reach for Cloudflare,
  which this record exists to prevent.
- **Latency is unmeasured.** No query-time comparison between DNS4EU, Cloudflare and the house router
  has been taken; if resolution ever looks slow, measure it before blaming the choice, and record the
  number here.
- **Reversing it is cheap**: two addresses in three files, a Pi `setup` run, and a sync for the two
  charts. Being wrong costs a change, not a migration.
