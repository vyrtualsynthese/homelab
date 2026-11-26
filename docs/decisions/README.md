# Decisions: the choices that outlived the work

> **In one line.** One short record per decision a future reader would otherwise have to
> reconstruct: what was decided, who decided it, why, and what it costs if the decision is
> wrong.
>
> **Use it when** you are about to reverse something and want to know why it was done that
> way, or you are writing a new record · **Verified** 2026-11-23 · **Kind:** reference
>
> **Sources** the records in this folder, each read on the day it was written

A decision record is written after the choice is made, by the person who made it, and it stays
when the work around it is finished. It is not a plan: the plan says what to build, the record
says what was settled and what it would cost to be wrong. Nothing here is deleted when it stops
being true — a record that has been overtaken is marked `Status: superseded by <link>` and left
in place.

The decisions taken during the stabilisation design are in §7 of
[that design](../superpowers/specs/2026-11-10-homelab-stabilisation-design.md). Records in this
folder are the ones made afterwards.

## The shape of a record

Every record carries the same four headings, in this order:

| Heading | What goes in it |
|---|---|
| The title | The decision itself, not its subject: "`nuc0` overheats and throttles; accepted, no alert", followed by `Date:`, `Decided by:` and `Status:` |
| `## The decision` | What was settled, in the present tense, and how it is tracked |
| `## Why` | The evidence: the numbers measured, not guessed, and the options rejected |
| `## What it costs if it is wrong` | The price in money, work or risk, so a later reader can tell what is worth revisiting |

A record may add a section for its own subject — how a fault is tracked, what the next steps
would cost — but the four above are what every record has.

## The records

| Record | The decision, in one line | Date |
|---|---|---|
| [`nuc0` overheats and throttles; accepted, no alert](2026-11-13-nuc0-throttling-accepted.md) | The throttling is accepted and tracked on the Platform dashboard with no alert, because an alert would fire often and ask for an action that does not exist | 2026-11-13 |
| [`streaming` runs at its thermal ceiling; the hardware fix is deferred](2026-11-13-streaming-overheating-deferred.md) | The node stays in service with its cooling fault, tracked against a soak test, until a cooler or a replacement platform is affordable | 2026-11-13 |
| [Seven applications are retired, not repaired](2026-11-18-retire-seven-applications.md) | `firefly`, `ghostfolio`, `guacamole`, `linkstack`, `monitorss`, `postiz` and `wiki` leave the repo and the cluster: their data was already gone and no chart of the seven met the contract | 2026-11-18 |
| [Discourse is retired, not repaired](2026-11-19-retire-discourse.md) | `discourse` leaves the repo and the cluster along with its public hostname `forum.ashuvidz.com`: it had been `Pending` since 2026-08-02 and its database no longer existed | 2026-11-19 |
| [Every provider is in an EU member state](2026-11-21-sovereign-eu-providers-only.md) | Every external service holding the operator's data or the cluster's identity runs on a company headquartered in an EU member state with the data inside the EU: Switzerland, the UK and Norway are out, and so is an EU region sold by a non-EU company | 2026-11-21 |
| [Agent shells are read-only](2026-11-23-agent-shells-read-only.md) | Every shell OpenCode spawns authenticates as `kube-system/opencode-mcp` by default; the operator's terminal keeps admin, and a write reaches the cluster only through git and Argo CD | 2026-11-23 |
| [Passwordless sudo belongs to one named account](2026-11-24-automation-sudo-account.md) | `ansible` holds the layer's only passwordless grant and the inventory connects as it; `vronteix` keeps `%sudo` with a password, so the drift report stays runnable unattended | 2026-11-24 |
| [Host access is a declared list](2026-11-25-declared-host-access.md) | `trusted_ssh_keys` in `group_vars` is the whole list of keys that may log in, written as the whole file to both accounts: adopting a laptop adds a line, retiring one deletes it, and an undeclared key is removed by the next run | 2026-11-25 |
| [No host is attached to Ubuntu Pro](2026-11-25-no-ubuntu-pro.md) | The six hosts stay un-attached: the security cover is the release they land on, 26.04 LTS to May 2031, and Canonical is a UK company, which the EU-only provider rule puts out | 2026-11-25 |
| [Nothing on the edge Pis goes through authentik](2026-11-26-edge-pis-local-auth-only.md) | `rbp0` and `rbp1` carry DNS and the VPN, so their panels sign in with local accounts only: a cluster that is down must not lock the operator out of the way back in | 2026-11-26 |
| [Public DNS is DNS4EU's unfiltered service, and Cloudflare is out](2026-11-27-public-dns-is-dns4eu-unfiltered.md) | Pi-hole, `qbittorrent` and `blackbox-exporter` all forward to `86.54.11.100` and `86.54.11.200`: Cloudflare, Google and Quad9 are out by the EU-only provider rule, and the filtered DNS4EU variants are out because Pi-hole is the filter | 2026-11-27 |

Below 80 °C on a one-hour soak at 50 % CPU is how the `streaming` record ends; a replacement
node is how the `nuc0` one does. Until then both are facts about the hardware, not open tickets.

## Template

```markdown
# <decision>

Date:
Decided by:
Status: active | superseded by <link>

## The decision

## Why

## What it costs if it is wrong
```
