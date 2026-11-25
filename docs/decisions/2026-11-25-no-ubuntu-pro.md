# No host is attached to Ubuntu Pro; the release move is the coverage

> **In one line.** All six hosts stay un-attached to Ubuntu Pro: the extra years of security cover ESM
> sells are years this homelab will not spend on 22.04, and Canonical is a United Kingdom company,
> which the EU-only provider rule puts out.
>
> **Use it when** `pro status` on a host reads un-attached and you wonder whether that is drift, or you
> are about to run `pro attach` · **Verified** 2026-11-25: `pro status` on the four cluster nodes, and
> the six-host measurement of 2026-11-24 · **Kind:** record
>
> **Sources** [`audit/hosts-2026-11-24.md`](../audit/hosts-2026-11-24.md) §SSH and sudo and §Upgrade
> readiness, [`2026-11-21-sovereign-eu-providers-only.md`](2026-11-21-sovereign-eu-providers-only.md),
> `ubuntu.com/pro` for the subscription's terms, [moving Ubuntu to a new LTS](../runbooks/ubuntu-upgrade.md)

Date: 2026-11-25
Decided by: operator
Status: active

## The decision

No host in this homelab is attached to Ubuntu Pro, and the move to 26.04.1 does not attach one.
The security cover comes from the release the hosts land on: 26.04 LTS keeps standard security
maintenance until **May 2031**, five years from its April 2026 release, and every host is inside that
window the day its hops finish.

`pro status` reading `"attached": false` with an empty account is therefore the intended state, not
drift, on all six: `nas`, `nuc0`, `streaming`, `rbp2`, `rbp0` and `rbp1`.

It is recorded because card #706 asked for it — "In the same pass decide Ubuntu Pro" — and #708 gates
the whole move on that card being `Done`.

## Why

Measured 2026-11-24 on all six hosts and again on the four cluster nodes on 2026-11-25: nothing is
attached, no ESM service is enabled, and nothing in the clusters depends on one. So the choice costs
nothing to make now.

Three reasons, strongest first.

1. **The vendor is out by a decision already taken.** Canonical Ltd is headquartered in London.
   [`2026-11-21-sovereign-eu-providers-only.md`](2026-11-21-sovereign-eu-providers-only.md) says a
   service that holds data or authority for this cluster runs on a company headquartered in an EU
   member state, and names the UK as out: "European is not the test; EU is". Attaching puts a non-EU
   service in the path of every host's identity and its package inventory. The rule is written for
   exactly one kind of decision, and this is one — not an exception quietly carved out for
   convenience.
2. **ESM covers a gap this programme closes in days.** The five jammy hosts are offered `24.04.5 LTS`
   today and are scheduled to reach 26.04.1 in this move. 22.04's standard support runs to April 2027,
   which is after the move: there is no window in which a jammy host sits unsupported and waiting for
   ESM. The 2032 date ESM would add is a date nothing here is planned to need.
3. **The free subscription covers five machines; there are six.** Canonical's own terms give a
   personal subscription up to five physical machines. Covering all six means a paid tier or one host
   left out, and a partial attachment is the worse answer: `pro status` then means something different
   depending on which host you ask, and a later reader has to reconstruct which five and why.

Options rejected:

- **Attach the five jammy hosts and leave `rbp0` out**, since `rbp0` already runs noble and needs
  nothing ESM offers. Rejected because it leaves the count as a fact someone has to remember, and the
  two Pis are reflashed to 26.04.1 in the same programme anyway.
- **Attach under a paid tier.** Money for a gap the move closes, from a jurisdiction the rule excludes.
- **Attach `esm-apps` alone.** The services that matter — `esm-infra` — are the half not covered, so
  the cheaper half changes nothing about the exposure.

## What it costs if it is wrong

- Until its hops finish, each jammy host gets only what Ubuntu publishes to the main archive. That is
  what it has had since 2022; this record changes none of it.
- The real failure is the move slipping past **April 2027**, when 22.04's standard support ends. From
  that date a host still on jammy stops receiving security updates, and this record is the one to
  overturn — attach, or finish the move. It is written with the date for that reason.
- Reversing it is cheap: a token and `pro attach` per host, no rebuild, no downtime inside a
  maintenance window. The cost of being wrong is a delay, not a lock-in.
