# Edge DNS parity: rbp0 is the source of truth — design

**Status:** proposed 2026-11-25 · **Date:** 2026-11-25 · **Board:** homelab, card #748
**Live basis (measured 2026-11-25):** the two panels through their own API (FTL `v6.6`), the
rendered files on both hosts, `ansible/pi/hosts-pi.sh`, `ansible/playbooks/templates/runners/`, and
`/etc/netplan/50-cloud-init.yaml` plus `/etc/resolv.conf` on each Pi. Every number below was read
from the host or the API it names, not inferred.

## Intent

The two edge Pis must answer DNS the same way, from one file in git, with **rbp0 as the source of
truth**. A query must reach a Pi-hole whichever host a client happens to ask, and the Pis' own
traffic must go through Pi-hole rather than around it.

Why now: the two panels drifted in four settings, both hosts resolve around their own blocklists, and
the panel password that the two share was printed into an agent session's log.

## What exists today

| Where | What it does | What it costs |
|---|---|---|
| `pihole.env` on each host | hand-kept, gitignored, holds `WEBPASSWORD` and 12 `FTLCONF_`/legacy keys | the two copies differ in their commented lines, so "same config" is a coincidence, not a rule |
| the two panels | 167 settings, **4 differ**: `dns.blockTTL` 86400/2, `dns.rateLimit.count` 10000/1000, `dns.listeningMode` ALL/SINGLE, `dns.revServers` LAN zone/none | rbp0 caches a blocked answer for a day; rbp1 answers reverse lookups upstream |
| the two panels | 26 identical adlists, 418 deny and 418 allow domains each, both with `urlhaus` failing | list parity is already true; nothing enforces it |
| `pihole.env` upstream key | `PIHOLE_DNS_: 1.1.1.1;1.0.0.1` (v5 leftover) | dormant: `pihole.toml` holds DNS4EU (`86.54.11.100/200`), so the next start that reads the env moves the upstream to a US resolver |
| `/etc/netplan/50-cloud-init.yaml` | `use-dns: false`, `nameservers: addresses: [1.1.1.1]` | both Pis resolve around their own blocklist; that file is a plain 2023 file, cloud-init has no seed left to rewrite it |
| `/etc/resolv.conf` | rbp0 `127.0.0.1, 192.168.1.38, 1.1.1.1`; rbp1 `127.0.0.1, 192.168.1.22, 1.1.1.1` | a container cannot reach the host's loopback, so Docker's resolver falls through: **rbp0 sent 1,720 queries in 24 h to rbp1**, and its own panel counted none of the host's traffic |
| pihole web | `0.0.0.0:80`, plain HTTP, one password shared by both hosts, in clear text in `pihole.env` | any LAN device can open the panel that holds every client's DNS history; the security baseline flags the same class on port 5000 and misses this one |
| `FTLCONF_` mapping | verified in a throwaway container on rbp0: `FTLCONF_dns_blockTTL`, `_listeningMode`, `_reason`… set the running config, nested keys included (`FTLCONF_dns_rateLimit_count`) | the file can own every setting, so no dashboard edit is needed |

## Decisions

**D1 — rbp0 is the source of truth; rbp1 conforms.** When they disagree, rbp1 is wrong. Their query
histories are not copied: each panel keeps the traffic it served.

**D2 — one rendered file carries every setting.** `ansible/playbooks/templates/runners/pihole.conf.env`
holds all `FTLCONF_*` keys, rendered per host by `hosts-pi.sh` and written by `setup`, printed by
`render`, compared by `verify` — the same treatment `docker-compose.yml` already gets. The two hosts
end up byte-identical, and a setting that is not in that file is not a setting.

**D3 — `pihole.env` keeps exactly one key: `FTLCONF_webserver_api_password`.** It stays host-local,
gitignored and out of the render; the compose reads `pihole.settings` then `pihole.env`, so the secret
wins on a collision. Measured on 2026-11-25 while rolling this out: the file held `WEBPASSWORD`, which
is the v5 name and this image ignores it — the panel keeps `webserver.api.pwhash` in `pihole.toml`, and
only the v6 key moves it. The file is `0600` and owned by the account that runs compose, which has to
read it: `root` and world-readable was how it looked before.

**D4 — the four drifting settings move into the rendered file, with corrected values**: `blockTTL 2`,
`rateLimit.count 1000`, `listeningMode ALL`, `revServers` on. The source-of-truth rule settles who
conforms, not whether a value is good: rbp0's `86400` and `10000` are the outliers, so they are fixed
on rbp0 rather than copied to a second host.

**D5 — upstreams are declared, not remembered.** `FTLCONF_dns_upstreams` carries DNS4EU in the
rendered file and `PIHOLE_DNS_` is deleted, so no start can move the upstream to Cloudflare.

**D6 — both hosts resolve through Pi-hole.** Netplan nameservers become `192.168.1.22` then
`192.168.1.38` on **both** hosts: no loopback (a container cannot use it), no `1.1.1.1` (it bypasses
the blocklist). `setup` writes the file and runs `netplan apply`; `verify` compares it to the
template. The file has been static since 2023 and cloud-init no longer holds a seed for it.

Measured while building this, and the reason a second file joins it: `systemd-resolved` keeps its
stub listener **on** by default, so `/etc/resolv.conf` is `127.0.0.53`; Docker drops that loopback
entry and falls back to `8.8.8.8`, which resolves around the blocklist from inside the host that
serves it. The two hosts had worked around it by hand, each naming the *other* Pi in
`/etc/systemd/resolved.conf`. A rendered drop-in,
`/etc/systemd/resolved.conf.d/90-pihole-resolvers.conf`, replaces that: `DNS=` empty (netplan is the
one record of the addresses) and `DNSStubListener=no`. `setup` restarts resolved and recreates the two
bridge services, because a container's `/etc/resolv.conf` is a copy taken when it is created.

**D7 — parity is checked, not assumed.** `hack/check-pihole-parity.py` reads
`PIHOLE_RBP0_API_KEY`/`PIHOLE_RBP1_API_KEY`, compares `/api/config` and `/api/lists` across the two
panels, and exits non-zero naming the keys that differ. The repository's habit is a check that
refuses a looser state; this is that check for DNS.

**D8 — the shared panel password is rotated on both hosts in this change**, because it is in a session
log. The two keep one password: the standby must answer the same login.

**D9 — panel exposure is left as it is here.** Binding Pi-hole's UI to loopback and wg-easy's to
loopback is one decision about two panels, and it belongs with the change that puts a moving address
in front of the Pis. The security baseline's finding stays open with that pointer.

## What this change does not do

- No moving address, keepalived, or wg-easy database replica — the next design.
- No metrics board — the design after that.
- **One measured correction, applied during the rollout.** Pi-hole v6 defaults `misc.etc_dnsmasq_d`
  to false, so `/etc/dnsmasq.d/02-my-wildcard-dns.conf` had been ignored since the v6 move: the six
  local overrides worked only through the `FTLCONF_misc_dnsmasq_lines` copy in `pihole.env`, which
  this change empties. The rendered settings file now carries `FTLCONF_misc_etc_dnsmasq_d=true`, and
  all six answer again from `127.0.0.1` on both hosts.

- No change to the blocklist strategy. The 26 lists and 418/418 domains are identical on both hosts,
  so parity does not depend on settling it; the overlap between the hand-added hagezi list and the
  firebog set stays a separate decision.

## Risks, and what the builder decides

- **Netplan is a live network change.** Both resolvers become the Pis themselves; if Pi-hole is down on
  both, a booted host cannot resolve. The second address is the mitigation, and the runbook needs the
  recovery line (`netplan apply` from the console).
- **A reflash without a restored volume has no panel password** (D3 removes it from the template's
  reach). The reflash runbook's restore step covers the volume; the builder should say so in it.
- Exact `FTLCONF_*` spelling for the keys D4 names, the test names in `hack/tests/test_pi_script.py`,
  and the wording in `docs/runbooks/pi-reflash.md` are the builder's calls.
