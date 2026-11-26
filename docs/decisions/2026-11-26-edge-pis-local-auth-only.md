# Nothing on the edge Pis goes through authentik

> **In one line.** `rbp0` and `rbp1` carry the LAN's DNS and the VPN; every panel on them signs in
> with its own local account, never with authentik, so a cluster that is down cannot lock the operator
> out of the machines that resolve names and carry the way back in.
>
> **Use it when** you wire a sign-in for anything on an edge host, or you wonder why no `wg-easy`
> provider exists · **Verified** 2026-11-26 · **Kind:** record
>
> **Sources** [`reference/sso-applications.md`](../reference/sso-applications.md),
> [`runbooks/pi-reflash.md`](../runbooks/pi-reflash.md),
> `cluster/helm/authentik/files/app-wg-easy.yaml`, the vault items `wg.rbp0.local` and `wg.rbp1.local`

Date: 2026-11-26
Decided by: operator
Status: active

## The decision

The two edge Pis sit outside the SSO boundary. Nothing they serve may ask authentik — no OIDC
provider, no forward-auth gate, no outpost, no cluster route — before it lets an administrator in.

- **The VPN panel (wg-easy)** signs in with its own account and password, kept in the vault as
  `wg.rbp0.local` and `wg.rbp1.local` and reset on the host with
  `docker compose exec wg-easy cli db:admin:reset`.
- **Pi-hole's admin** uses its own password, the `PIHOLE_PANEL_PASSWORD` both hosts share in
  `~/.config/secrets/homelab.env`, filed in the vault as a field of `homelab-env`.
- **The `wg-easy` entry in the authentik blueprint**,
  `cluster/helm/authentik/files/app-wg-easy.yaml`, is dead configuration under this rule: no `wg-easy`
  provider exists in authentik, and none is to be created. `OAUTH_OIDC_CLIENT_SECRET` in each host's
  `wg-easy.env` is unused for the same reason, and goes when the file is next touched.

authentik stays usable *from* the edge, in a browser on a Pi. The edge never asks it whether a person
may enter.

## Why

The VPN is the way back into the LAN and Pi-hole resolves every name in it. Both run on hosts that keep
running when `streaming` or the whole cluster does not, which is the property that makes them the
recovery path. A sign-in through authentik inverts it: the cluster is exactly what is broken on the
morning the VPN is needed, and a gate on the Pi would then be a door locked from the inside. DNS fails
the same way, and takes the house's names with it.

The panel was documented as authentik in front and its own password as the fallback. A fallback is not
independence: the form that offers it, the client it uses and the redirect it follows are all choices
the cluster makes. No provider was ever created, so in practice the panel already signs in locally;
this record makes that the intended state rather than a blueprint nobody applied.

## What it costs if it is wrong

Two local passwords instead of one identity, rotated by hand on the hosts, and no per-person audit
trail for the two most powerful admin panels on the LAN. Each is a single credential, which is why both
are vault items and why each Pi's `backup` stream carries `wg-easy.db` and `pihole.env`. Reversing the
rule costs a provider and a blueprint entry, not data — the price is the lockout it buys back.
