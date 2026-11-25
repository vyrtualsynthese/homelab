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
> · **Verified** 2026-11-25: the undo renderer, the refusals and the run loop are green against
> `hack/fixtures/switch-change/`, a fake switch that answers `ssh` and `scp` from a state directory;
> the gate and the installer are green against their own stubs, in `hack/tests/test_switch_apply.py`
> and `hack/tests/test_switch_install.py`. **No timer has fired on a real switch yet** — the runs
> that will prove it are in "The first live runs" below, and their output is not yet in this page.
> · **Kind:** how-to
>
> **Sources** `ansible/switches/switch-change.sh`, `ansible/switches/switch-apply.sh`,
> `ansible/switches/install-switch-change.sh`, `hack/tests/test_switch_change.py`,
> `hack/fixtures/switch-change/`, and the design in
> [`../superpowers/specs/2026-11-25-switch-change-revert-design.md`](../superpowers/specs/2026-11-25-switch-change-revert-design.md)

## What this touches

| Device | Default address | What is written on it |
|---|---|---|
| `crs326` | `192.168.1.11` | `undo-<id>.rsc`, `change-<id>.rsc`, a scheduler entry `revert-<id>`, and `pre-<id>.backup` if the account may take one |
| `crs309` | `192.168.1.43` | the same |

The script signs in as `deploy` with the key at `/home/netcfg/.ssh/switch-deploy` — `switch-deploy`,
not `switches-deploy`. The gate runs it as `netcfg` with `HOME=/home/netcfg`, which is the account the
key belongs to. The account is `deploy`, holding `read`, `write`, `ssh` and `test` — card #485's,
proved in [`switch-access.md`](switch-access.md). The binary backup needs `policy` on top of those,
and its refusal is why that one step is allowed to fail.

Both switches run RouterOS 6.49, which signs a key login with the SHA-1 `ssh-rsa` algorithm that
OpenSSH 8.8 and later dropped from the default list. Every `ssh` and `scp` call the script makes
therefore carries `PubkeyAcceptedAlgorithms=+ssh-rsa`, or the login ends at `Permission denied
(publickey,password)` on a key the device already trusts. Card #696 moves the switches to RouterOS 7,
whose ed25519 key needs no allowance, and the line goes with that move.

`SWITCH_CHANGE_TARGETS`, `SWITCH_CHANGE_USER` and `SWITCH_CHANGE_KEY` override the three for a direct
run, and `./switches/switch-change.sh --list-targets` prints the pairs. Through the gate none of them
is in reach: the gate clears them before it hands over, and it reads its own paths and the account
from constants in the installed file, never from the environment, so `sudo`'s environment policy is
not what keeps a run on the reviewed code. The targets are `crs326` and `crs309`, the account and key
are the two above.

## The command

A change is a `.rsc` fragment, and it is staged in the inbox before anything reads it. From the
repository root:

```bash
install -m 0640 ansible/switches/changes/<your-change>.rsc \
  /var/lib/switch-change/inbox/<your-change>.rsc
```

Then it runs as `netcfg`, through the gate — the one command the sudoers drop-in allows the agent to
say as the account that holds the switch key:

```bash
sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
  --change /var/lib/switch-change/inbox/<your-change>.rsc --dry-run
```

The gate takes `--target` and `--change`, and passes `--window`, `--expect`, `--yes`, `--dry-run` and
`--arm-only` through unchanged. It checks the change and the installed script first, copies the change
to `/var/lib/switch-change/work/staged/stage-<epoch>-<pid>.rsc`, and hands the script that copy. The
path matters when a message quotes one: a refusal from the script names the staged file, not the
inbox file you wrote. The install, the digest it checks and what it refuses are
[`switch-access.md`](switch-access.md).

It reads the switch, renders the undo, prints it, and asks once before writing anything. `--yes`
skips the question for an unattended run; with no terminal to ask on — an agent's shell is one — a run
without `--yes` exits 2 and touches nothing. So does a `--dry-run`: it prints the undo and names any
timer an earlier run left armed, and cancels none of them.

```bash
sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
  --change /var/lib/switch-change/inbox/<change>.rsc --dry-run    # print the undo, write nothing
sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
  --change /var/lib/switch-change/inbox/<change>.rsc --arm-only   # arm the undo, do not apply
sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
  --change /var/lib/switch-change/inbox/<change>.rsc --window 20m # the default is 10m, the ceiling 1h
```

`--arm-only` is for a change applied by hand from Winbox (MikroTik's Windows management tool): the undo and its timer are the same, and
the operator applies the change. `--expect` is the address to keep polling once the change has
landed; it defaults to the target's address and matters when the change is the management address
itself.

## What you see, phase by phase

| Phase | What it does | The line it leaves |
|---|---|---|
| Read | `/system resource print` for the major version, `/export` and the verbose export, `/system clock print`, and the `revert-*` listing. | — |
| Arm | Shows the undo, cancels the timers an earlier run left armed, uploads `undo-<id>.rsc` and `change-<id>.rsc`, checks each is listed on the device, takes the binary backup, adds the one-shot entry and reads its `start-date`, `start-time` and name back. | `cancelled the timer an earlier run left armed: revert-…` · `armed revert-<id> on crs326 for 09:30:00 device time (window 10m)` |
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
move away from. The work directory `/var/lib/switch-change/work/<id>/` keeps the two exports, the
rendered undo and a copy of the change. Its parent `/var/lib/switch-change/work` is
`netcfg:vsynthese 0750`, so a `vsynthese` shell can step through it and list it; the run's own `<id>`
directory is `netcfg` `0700` — the gate sets `umask 077` — so nothing inside is readable by the agent
or by another account. The run prints the diff to its own output, and the operator reads the files
with `sudo`. The staged copy sits one level up in `work/staged/`, which the gate makes `netcfg`
`0700`. None of it is committed and none of it contains a secret.

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
| 0 | the change landed, the timer was removed, and the switch answered. When the switch does not confirm the timer's removal, the last line says so and stderr names the entry: it may still be armed, and it fires at its start time unless it is removed by hand |
| 1 | no change was applied, and nothing is left on the switch: a refusal, an unreadable change, a failed upload (whatever it had uploaded is removed), a timer the device refused or would not show back, or an interrupt, which leaves the timer armed on purpose |
| 2 | usage error, including a run with no `--yes` and no terminal to ask on, a window below a minute or above an hour, and an option given with no value |
| 3 | the export is identical to the one taken before the change — reverted, or the change was a no-op; with an unreadable `run-count` the line says which of the two is unknown |
| 4 | the export **differs** from the one taken before; the diff is printed |
| 5 | the switch never answered again; nothing was removed and nothing is claimed about it |

Codes 3, 4 and 5 are not failures of the script: they are the switch telling you what happened. Read
the diff on 4 before running anything else. The gate adds no code of its own: a refusal there — a
change outside the inbox, an installed script that does not match its digest, a missing work directory
— is 1, and a bad argument is 2.

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
- an `add` whose key the export already holds: the undo would remove an object the change never made;
- a selector matching more than one object, or none — undoing one of several is a guess;
- a `set` whose old value is in neither export, or an argument with no `=` in it;
- a `set` that rewrites an attribute its own selector matches on: after the change lands, the undo
  looks the object up by the value the change has just replaced, finds nothing, and the timer fires
  into nothing. The address move is exactly that case, because `/ip address` rows are found by their
  address:

  ```
  /ip address
  set [ find address="192.168.1.11/24" ] address=192.168.1.211/24
  ```

  Select the object by an attribute the change leaves alone — `interface`, a comment, a name — and the
  same change renders, as long as that selector matches exactly one object. A `set` that rewrites one
  of two selector attributes is refused for the same reason. So a management address cannot be moved
  through this script: the way back to it is a card of its own, not a change that slips past this
  check.
- a value or old value holding a `}`, which would close the guard the undo is written in;
- anything but `add`, `set`, `remove`, `enable` and `disable` — `move` included, whether it is
  written under a section header or on a line of its own;
- a change that renders no undo line at all, such as a file holding only a section header;
- `/import`, and any line under `/system scheduler`, `/user`, `/file` or `/system reset`,
  `/system backup`, `/system reboot`: those could remove the timer, the account that cancels it, or
  the undo file itself.

The change file itself is a `.rsc` fragment and holds no secret. `switches/changes/README.md` says
what one looks like.

## The first live runs

These three runs are the proof this page is waiting on, and the runs are where its banner's
**Verified** line comes from: two rehearsals on `crs309`, then a full run on `crs326` whose own timer
puts the switch back. Their output is pasted in this section by the runs themselves; the commands and
the lines to expect are below.

The change is deliberately the one that cuts the way in: it disables `ssh`, the service this script
itself uses to reach the switch. That is the whole point. Once the change lands, the session that made
it is gone, and nothing on the agent's side can open another — the switch can only come back by
running the undo its own timer holds, with nobody connected. A change that leaves the way in open
would prove nothing about the timer.

All three need the gate installed first; its install, digest and sudoers rule are
[`switch-access.md`](switch-access.md). The change file is staged in the inbox the way the command
section stages one.

**1. The dry run on `crs309`.** Read the switch, render the undo, write nothing:

```bash
install -m 0640 /dev/stdin /var/lib/switch-change/inbox/arm-only.rsc <<'EOF'
/ip service
set [ find name=ssh ] disabled=yes
EOF
sudo -u netcfg /usr/local/sbin/switch-apply --target crs309 \
  --change /var/lib/switch-change/inbox/arm-only.rsc --dry-run
```

Expected, and exit 0:

```
the undo to arm on crs309, at <hh:mm:ss> device time (window 10m):
:if ([:len [/ip service find name="ssh"]] > 0) do={ /ip service set [find name="ssh"] disabled=no }
nothing was written: this was --dry-run
```

The undo is `set [find name="ssh"] disabled=no`, wrapped in the guard that makes it do nothing when
the service is already back: `/ip service` is the menu, `[find name="ssh"]` picks the one row in it,
and `no` is what the run read from the verbose export as the value before the change. That value is
what the dry run below is for: it is the check that the real export carries the row at all.
`<hh:mm:ss>` is the device's own clock plus the window, never this workstation's. A run that refuses
instead prints the refusal on stderr — one line per refused line of the change,
`switch-change.sh: <the staged file> line 2: …` — then `nothing was changed: the change cannot be
undone`, prints no undo at all, and exits 1.

**2. The arm-only run on `crs309`.** Upload the undo and the change, add the timer, and leave the
configuration alone:

```bash
sudo -u netcfg /usr/local/sbin/switch-apply --target crs309 \
  --change /var/lib/switch-change/inbox/arm-only.rsc --arm-only --window 3m --yes
```

Arming is a write, so the run asks once first; `--yes` answers it. The flag is not optional from an
agent's shell, which has no terminal to ask on: without it the run exits 2 and touches nothing.

Expected, and exit 0:

```
the undo to arm on crs309, at <hh:mm:ss> device time (window 3m):
:if ([:len [/ip service find name="ssh"]] > 0) do={ /ip service set [find name="ssh"] disabled=no }
armed revert-crs309-<date>-<time> on crs309 for <hh:mm:ss> device time (window 3m)
the entry runs /import file=undo-crs309-<date>-<time>.rsc once; the change has not been applied
```

`no binary fallback for this run: …` may sit between the undo and the `armed` line: `deploy` carries
no `policy` right, so the backup is refused on purpose and the run carries on armed.

Three minutes later the entry has run once, which `/system scheduler print detail` shows as
`run-count=1`, and `/log print` on the switch shows the `/import file=undo-crs309-<date>-<time>.rsc`.
The configuration was never changed, so the entry and both files stay until they are removed by hand:

```
/system scheduler remove [find name="revert-crs309-<date>-<time>"]
/file remove [find name="undo-crs309-<date>-<time>.rsc"]
/file remove [find name="change-crs309-<date>-<time>.rsc"]
```

**3. The full revert on `crs326`.** This is the card's acceptance. The same change file under another
name, with no `--arm-only`: arm it, apply it, then watch the management address until it answers
again.

```bash
install -m 0640 /dev/stdin /var/lib/switch-change/inbox/ssh-off.rsc <<'EOF'
/ip service
set [ find name=ssh ] disabled=yes
EOF
sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
  --change /var/lib/switch-change/inbox/ssh-off.rsc --yes --window 5m
```

Expected, in this order, and exit 3:

```
the undo to arm on crs326, at <hh:mm:ss> device time (window 5m):
:if ([:len [/ip service find name="ssh"]] > 0) do={ /ip service set [find name="ssh"] disabled=no }
armed revert-crs326-<date>-<time> on crs326 for <hh:mm:ss> device time (window 5m)
verifying: applying /var/lib/switch-change/work/staged/stage-<epoch>-<pid>.rsc and watching 192.168.1.11 until the switch answers
the difference against the export taken before the change:
reverted: the timer revert-crs326-<date>-<time> fired and the export is identical to the one taken before the change
```

After `verifying`, the import lands and SSH stops answering, so the poll at `192.168.1.11` fails for
the rest of the window; that is expected, not a lost run. Five minutes later the timer imports the undo
on the device, SSH answers again, and the poll succeeds. The script then reads the entry's `run-count`,
removes the timer and both files, and compares the export with the one taken before the change.

**Exit 3 is the right answer here**, and exit 0 would not be: 0 means the timer's `run-count` was 0 and
the change simply landed — the switch never stopped answering, so the way in was never cut and nothing
about the timer was proved. Codes 4 and 5 are in the table above: 4 is an export that came back
different, 5 is a switch that never answered again, and on either one the next step is `/log print` on
the switch.

When the three runs have happened, their real lines replace the placeholders in these blocks, and this
section is what the banner's **Verified** line and the **Verified** list below are updated from.

## Verified

- `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py`
  — 66 tests, the undo renderer on RouterOS 6 and 7, every refusal, and every exit path driven
  through the fake switch, whose `/export` carries its scheduler table as a real one does.
- `hack/tests/test_switch_apply.py` — 21 tests, the gate's target list, its argument set, the pinned
  descriptor, the staged copy, the digest and the work directory.
- `hack/tests/test_switch_install.py` — 16 tests, the installer against a temporary `--destdir`.
- `bash hack/check-docs.sh` — this page's banner, links and nav entry.
- Not verified: a real switch, a real scheduler entry, and a real timer firing. "The first live runs"
  above is that proof, and its output is pasted there when the runs happen. Card #485 created the
  `deploy` account and its key; the card this page serves is #486.
