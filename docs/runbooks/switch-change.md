# Changing a switch configuration with an armed undo

Date: 2026-11-25 · **Pending** — the renderer and the fake-device runs are green; no switch has run
this yet

> **In one line.** One command carries a `.rsc` change onto a MikroTik switch, and before the change
> lands it arms a one-shot timer on the switch that puts the old configuration back if the switch
> stops answering.
>
> **Use it when** a switch's configuration has to change and a mistake would cut the way in — a
> bridge port's VLAN, an address, a route, a port that must be disabled and enabled again — and you
> want the way back to be on the device rather than in your session.
> · **Verified** 2026-11-25: the undo renderer, the refusals and the whole run are green against
> `hack/fixtures/switch-change/`, a fake switch that answers `ssh` and `scp` from a state directory.
> No switch has run this yet: the deploy account and its key are card #485, which is not done.
> · **Kind:** how-to
>
> **Sources** `ansible/switches/switch-change.sh`, `hack/tests/test_switch_change.py`,
> `hack/fixtures/switch-change/`, and the design in
> [`../superpowers/specs/2026-11-25-switch-change-revert-design.md`](../superpowers/specs/2026-11-25-switch-change-revert-design.md)

## What this touches

| Device | Default address | What is written on it |
|---|---|---|
| `crs326` | `192.168.1.11` | `undo-<id>.rsc`, `change-<id>.rsc`, a scheduler entry `revert-<id>`, and `pre-<id>.backup` if the account may take one |
| `crs309` | `192.168.1.43` | the same |

The account is `deploy`, with `~/.ssh/switches-deploy` as its key. `SWITCH_CHANGE_TARGETS`,
`SWITCH_CHANGE_USER` and `SWITCH_CHANGE_KEY` override the three; `./switches/switch-change.sh
--list-targets` prints the pairs. **This is the account card #485 creates**: it needs `read`, `write`,
`ssh` and `test`, and the binary backup needs `policy` as well, which is why that one step is allowed
to fail.

## The command

From `ansible/`:

```bash
./switches/switch-change.sh --target crs326 --change switches/changes/<your-change>.rsc
```

It reads the switch, renders the undo, prints it, and asks once before writing anything. `--yes`
skips the question for an unattended run; without either, and with no terminal to ask on, it exits 2
and touches nothing.

```bash
./switches/switch-change.sh --target crs326 --change … --dry-run    # print the undo, write nothing
./switches/switch-change.sh --target crs326 --change … --arm-only   # arm the undo, do not apply
./switches/switch-change.sh --target crs326 --change … --window 20m # the default is 10m, the ceiling 1h
```

`--arm-only` is for a change applied by hand from Winbox: the undo and its timer are the same, and
the operator applies the change. `--expect` is the address to keep polling once the change has
landed; it defaults to the target's address and matters when the change is the management address
itself.

## What you see, phase by phase

| Phase | What it does | The line it leaves |
|---|---|---|
| Read | `/system resource print` for the major version, `/export` and the verbose export, `/system clock print`, and the `revert-*` listing. Any timer an earlier run left armed is cancelled here. | `cancelled the timer an earlier run left armed: revert-…` |
| Arm | Shows the undo, uploads `undo-<id>.rsc` and `change-<id>.rsc`, reads each back, takes the binary backup, adds the one-shot entry and reads its `start-time` back. | `armed revert-<id> on crs326 for 09:30:00 device time (window 10m)` |
| Apply | `/import file=change-<id>.rsc verbose=yes`. A dropped session here is expected: the import runs on the device once it has started. | `verifying: applying … and watching 192.168.1.11 until the switch answers` |
| Verify | Reads the timer's `run-count` first, then polls the expected address until the switch answers or the window plus a reboot allowance runs out. | `applied: …` · `reverted: …` · `the switch did not answer …` |

If the import prints a RouterOS error, the undo runs **on demand** — imported at once, not waited for
— so a change that landed halfway is put back in seconds. The line says `reverted on demand`.

## The undo it arms

One line per inverse operation, each wrapped in a guard so a half-applied change still reverts and a
line with nothing to do does nothing:

```
:if ([:len [/interface bridge port find interface="sfp-sfpplus1"]] > 0) do={ /interface bridge port set [find interface="sfp-sfpplus1"] pvid=1 }
```

The change was `set … pvid=20`, so the undo sets `pvid` back to the value the **verbose** export
holds — the plain export omits any value sitting at its default, and the default is what most changes
move away from. The work directory `/tmp/opencode/switch-change/<id>/` keeps the two exports, the
rendered undo and a copy of the change; none of it is committed and none of it contains a secret.

## Two RouterOS facts this rests on

- **`/import` is additive, and it stops at the first failing line.** Importing the export taken
  before the change would therefore remove nothing the change added: an `add` in the export creates a
  second object, and a `set` back to a value already in place does nothing. That is why the undo is
  built from inverse operations, one guarded line each, and not from a second import of the old
  export.
- **A scheduler entry with `interval=0s` runs once**, at its `start-time`, and RouterOS dates the
  entry on the day it is created. A window that would cross the device's midnight is therefore
  refused before anything is armed: a `start-time` earlier than today's clock would run hours early,
  and one on the next day runs a day late. Neither is a way back.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | the change landed, the timer was removed, and the switch answered |
| 1 | nothing was touched: a refusal, an unreadable change, a failed preflight or upload, or an interrupt with the timer left armed |
| 2 | usage error, including a run with no `--yes` and no terminal to ask on |
| 3 | reverted — by the timer or on demand — and the export is identical to the one taken before |
| 4 | reverted, and the export **differs** from the one taken before; the diff is printed |
| 5 | the switch never answered again; nothing was removed and nothing is claimed about it |

Codes 3, 4 and 5 are not failures of the script: they are the switch telling you what happened. Read
the diff on 4 before running anything else.

## When the timer fires

The entry runs `/import file=undo-<id>.rsc` once, at its start time, on the switch itself — it does
not need your session. Before it runs, the switch may be unreachable from here because the change cut
the path; that is what the timer is for. The undo puts back the attributes the change touched, so the
management address and the VLANs return.

Traffic on that switch can drop for the moment the undo runs: a port's VLAN moving back is
reconfiguration, not a packet-preserving operation. Treat a fired timer as an incident worth reading
in `/log print` on the switch, even when the run reports 3.

If the run is interrupted while the timer is armed, it prints the entry's start time and the command
that cancels it:

```
  /system scheduler remove [find name="revert-crs326-20261125-093000"]
```

The timer stays armed on purpose. Cancel it by hand only once you have checked the switch yourself.

## The binary backup

`/system backup save name=pre-<id>` writes a full binary snapshot, loadable with `/system backup load
name=pre-<id>` — which reboots the switch. The script never uses it: the textual undo is the way
back, and the backup is the lever of last resort. It needs a policy right the #485 account does not
carry, so a refusal prints `no binary fallback for this run` and the run carries on armed.

A backup written there stays on the switch's flash until it is removed by hand (`/file remove
[find name="pre-<id>.backup"]`). Remove the old ones when the run that made them is over.

## What it refuses

The renderer refuses, before anything is written, and names the line:

- an `add` with no attribute it can find the object by afterwards;
- a selector matching more than one object, or none — undoing one of several is a guess;
- a `set` whose old value is in neither export;
- anything but `add`, `set`, `remove`, `enable` and `disable` — `move` included;
- `/import` in a change, and any line under `/system scheduler`, `/user`, `/system reset` or `/system
  backup`: those could remove the timer or the account that cancels it.

The change file itself is a `.rsc` fragment and holds no secret. `switches/changes/README.md` says
what one looks like.

## Verified

- `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py`
  — 38 tests, the undo renderer on RouterOS 6 and 7, every refusal, and every exit path driven
  through the fake switch.
- `bash hack/check-docs.sh` — this page's banner, links and nav entry.
- Not verified: a real switch, a real scheduler entry, and a real timer firing. Card #485 creates the
  `deploy` account and its key; the card this page serves is #486.
