# Decisions: the choices that outlived the work

> **In one line.** One short record per decision a future reader would otherwise have to
> reconstruct: what was decided, who decided it, why, and what it costs if the decision is
> wrong.
>
> **Use it when** you are about to reverse something and want to know why it was done that
> way, or you are writing a new record · **Verified** 2026-11-17 · **Kind:** living page

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
