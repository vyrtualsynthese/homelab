# Changing a switch: export, undo, apply, verify — design

**Status:** draft, awaiting approval · **Date:** 2026-11-25 · **Board:** homelab, card #486
**Waits on:** #485, the restricted write account — no key for it exists on this workstation yet, so
nothing here has been run against a switch.

## Intent

One script that carries a configuration change onto a MikroTik switch and puts the switch back on
its feet even when the change took the switch's management address away. The agent writes the change
in git and never holds the key; the script is the only thing that talks to the device, and the person
running it is the one whose key it uses.

Junos calls this shape commit-confirmed. RouterOS has no such feature, so the timer that undoes the
change is a scheduled entry on the device itself: it fires whether or not anyone is still connected.

## What the card assumed, and what RouterOS actually does

The card says: export the configuration, schedule `/import file=before-change.rsc`, apply, verify,
cancel. Two documented facts make that literal recipe fail:

- `/import` **adds** the file's objects to the configuration and **stops at the first line that
  fails**, keeping everything before it (`manual.mikrotik.com/docs/cli-reference/import`). Importing
  the whole pre-change export onto a switch that still holds those objects fails on the first `add`
  that already exists and reverts nothing.
- A scheduled entry with `interval=0s` runs **once** at `start-date`/`start-time`
  (`manual.mikrotik.com/docs/cli-reference/system/scheduler`), which is the one-shot the timer needs.

So the timer must run an **undo file**: only the operations that put back what the change touched,
one guarded line per operation, so that a change that only half-landed still reverts.

## Current state

| | |
|---|---|
| Read side | `ansible/collectors/network-audit.sh` — `/export`, status block, RouterOS redactor, tests in `hack/tests/test_network_audit.py`, described in `ansible/README.MD` |
| Write account | card #485: group `policy=read,write,ssh,test`, no `policy`, key under a UNIX user the agent shell cannot read |
| Devices | `192.168.1.11` CRS326 on 7.16.1, `192.168.1.43` CRS309 on 6.49.10 until card #484 moves them to 7 |
| Keys here | none — no `~/.ssh/network-audit`, no `NET_AUDIT_*` in `homelab.env` |

## Goals

1. `ansible/switches/switch-change.sh` applies one `.rsc` change file from `ansible/switches/changes/`
   to one named switch, with an undo armed before the change and cancelled after it.
2. The undo is armed **before** the change is applied, so a change that removes the management
   address is still reverted with nobody at a console.
3. The person running it sees the undo it will arm, line by line, before anything is armed.
4. A change the script cannot invert is refused before the device is written to, and the refusal
   names the line.
5. Applied cleanly: the switch answers, the timer is cancelled, both uploaded files are removed, and
   the script prints what changed between the export before and the export after.
6. Reverted by the timer: the script reports it, prints the export-after against the export-before,
   and says whether they are identical.
7. Testable with no switch: rendering the undo is a pure function of two files, and the whole run is
   driven against a fake device on `PATH`.

## Non-goals

- Validating what the change *means*. Reachability is the check; the export diff is the evidence.
- A GitOps equivalent for switches, or a nightly drift check — the collector is the read side and
  stays the read side.
- The Livebox, and anything on a host.
- `show-sensitive` exports. The undo file contains no secrets; the export keeps RouterOS's default
  hiding.

## Anti-goals

- An undo armed from a file that was never verified to be on the device.
- An undo that silently skips a line because a previous line failed. Every line carries its own
  guard.
- A revert that depends on the workstation still being reachable, or on the script still running.
- A `latest`/floating anything.

## Approaches

**A — undo file rendered from the change, armed as a one-shot scheduler entry** (chosen).
The script reads the pre-change export and the change file, writes the inverse operations as guarded
one-liners, uploads that file, arms it, applies, verifies, cancels. No reboot, no binary artefact,
and the undo is readable before it is armed. Trade: the renderer must understand every operation the
change uses, and refuse the rest; RouterOS's `[find …]` selectors are its vocabulary.

**B — `/system backup save` and `/system backup load` on the timer.**
Native, exact, needs no parser: a binary backup restored by the entry. Trade: `load` **reboots** the
switch, so every port on it drops for a minute or more, and the entry that runs it needs the `policy`
right the #485 account deliberately does not have — the account could not arm its own timer. It also
cannot be reviewed before it fires, being binary. Rejected for those two reasons; the script takes a
backup before the change anyway, as a manual last resort.

**C — import the whole pre-change export on the timer.**
What the card literally says. Trade: contrary to the documented behaviour above, it either reverts
nothing or stops halfway. Rejected.

## Decisions

**Home and name.** `ansible/switches/switch-change.sh`, beside `changes/` holding the `.rsc` files
the agent commits. Tests in `hack/tests/test_switch_change.py`. A `switches` directory rather than
`collectors` because this one writes.

**The four phases, in this order.** Each prints one line as it starts, so a terminal that hangs shows
where:

1. **Read.** Sign in, read `/system resource print` for the major version, and take **two** exports:
   the plain one, and a verbose one. Any `revert-*` entry left by an earlier run is cancelled here
   and named in the output: that run's session is over, and a second timer must never sit beside an
   old one. Nothing else is written. `--dry-run` stops here after printing the undo it would arm.

   Why two: `/export` omits every value that sits at its RouterOS default, so a change that sets an
   attribute whose old value *was* the default has nothing to restore in the plain export — and that
   is most changes. The verbose export holds the old value; the plain one holds the arguments that
   can be fed back to `add`, since a verbose line also carries read-only properties such as
   `dynamic` and `invalid`, which `add` refuses. So verbose answers "what was this set to", plain
   answers "how is this object written", and the comparison in phase 4 uses the plain one alone.
   On RouterOS 6 the verbose export is `/export hide-sensitive verbose`, because `verbose` would
   otherwise print the secrets that `hide-sensitive` exists to hide. Neither copy is committed.
2. **Arm.** Render the undo, refuse if any line is unmodelled, show it, ask once. Upload
   `undo-<id>.rsc` and `change-<id>.rsc` with `scp -O`, read `/file print` back to prove both are
   there, take `/system backup save name=pre-<id>`, then add the one-shot entry
   `revert-<id>` at the device clock's now + window. Read `/system scheduler print detail` back and
   fail if its `start-time` is not the one asked for.

   The backup is the only step that is allowed to fail: taking one needs the `policy` right that
   #485 withholds, and the undo file does not depend on it. A refusal prints "no binary fallback for
   this run" and the run carries on armed. It never blocks the undo.
3. **Apply.** `/import file=change-<id>.rsc verbose=yes`. A dropped session here is expected, not a
   failure: the import runs on the device once started. When the session survives and RouterOS
   reports a failed line, the undo runs **on demand** — re-upload nothing, just import
   `undo-<id>.rsc`, remove the entry, remove the files — so a change that lands halfway is put back
   in seconds rather than after the whole window. The undo lines are guarded and idempotent, which
   is what makes that safe.
4. **Verify.** Read the entry's `run-count` first: anything but `0` means the timer already fired,
   and the run is reported as reverted (exit 3 or 4) rather than as applied. Then poll `--expect`
   until the switch answers or the window runs out. Answer: export again, diff, remove
   `revert-<id>` over whichever address answered (the change may have moved the target), remove both
   uploaded files, exit 0. No answer: say so, keep polling for the revert plus a reboot allowance,
   then export again and compare with the export before — exit 3 when identical, 4 when not, 5 when
   it never came back.

   "Identical" and the printed diff both drop `#` comment lines and blank lines: every export opens
   with a dated RouterOS comment, so two of them are never byte-equal.

**The undo file.** One line per inverse operation, each wrapped in a RouterOS guard so a half-applied
change still reverts and a line that has nothing to do does nothing:

```
:if ([:len [/ip address find address="192.168.1.11/24"]] > 0) do={ /ip address remove [find address="192.168.1.11/24"] }
:if ([:len [/interface bridge vlan find vlan-ids="10"]] = 0) do={ /interface bridge vlan add vlan-ids=10 bridge=bridge tagged=bridge }
:if ([:len [/interface bridge port find interface="sfp-sfpplus1"]] > 0) do={ /interface bridge port set [find interface="sfp-sfpplus1"] pvid=1 }
```

Rendering works off the change file's operations, not a diff of two exports, because the export after
the change may not exist:

| Change line | Undo line |
|---|---|
| `/path add … name=X` (or `comment=`, else refused) | remove the object found by that key |
| `/path remove [find <selector>]` | re-add the object the pre-change export defines for that selector |
| `/path set [find <selector>] k=v` | set `k` back to the value the verbose export holds for that object |
| `/path enable`/`disable [find …]` | the other one |
| anything else, including `move`, `/system reset`, `/import`, `/system scheduler`, `/user` | refused |

**Refusals, and what they protect.** A change line that cannot be inverted stops the run before
anything is written: an `add` with no key to find it by afterwards, a `set` whose old value is in
neither export, a command outside the table. A change that touches `/system scheduler` or `/user`
is refused too, because it could remove the timer or the account that cancels it.

**Same parse shape on both generations.** Measured against real exports, RouterOS 6 and 7 both print a
`/section` header followed by `add …` and `set [ find … ] …` lines, wrapping long lines with a
trailing `\`. The renderer joins the wraps and reads that one shape; `--major` chooses the export
command (verbose on 7, `hide-sensitive verbose` on 6) and is cross-checked against the export's
`by RouterOS <major>.` header, because a mismatch means the two are talking about different files.

**Defaults.** `SWITCH_CHANGE_TARGETS` (`crs326=192.168.1.11 crs309=192.168.1.43`),
`SWITCH_CHANGE_USER` (`deploy`), `SWITCH_CHANGE_KEY` (`~/.ssh/switches-deploy`),
`SWITCH_CHANGE_WINDOW` (`10m`, ceiling one hour), `SWITCH_CHANGE_WORKDIR`
(`/tmp/opencode/switch-change`), connect and command timeouts as the collector has them. `--expect`
defaults to the target's address. `--arm-only` stops after the undo is armed, for a change applied by
hand from Winbox.

**The entry's start time is computed on the device's clock**, read from `/system clock print`, and a
window that would cross the device's midnight is refused with nothing armed: RouterOS dates the entry
when it is created, so a `start-time` earlier than that date's clock runs hours early or a day late,
and neither is a revert path. The device's clock and the workstation's are printed together when they
disagree by more than a minute, as a warning.

**A change that touches `/system scheduler` or `/user` is refused** rather than rendered: the first
can remove the timer, the second the account that cancels it.

**Exit codes.** 0 applied and timer cancelled · 1 nothing touched (refusal, preflight or upload
failure) · 2 usage · 3 reverted, by the timer or on demand, export identical to before · 4 reverted,
export differs · 5 never answered again.

**An interrupted run leaves the timer armed.** Ctrl-C, a closed terminal and a killed process all
take the same path: print the exact minute the entry fires and the one command that cancels it, then
stop without touching the device. A switch taken away by a change is an outage; a change reverted
while it was good costs one more run, and that is the cheaper mistake.

**The new script carries its own helpers.** `ros()`, the timeouts, the major-version read and the
CRLF strip are written again rather than lifted from the collector, whose test suite is the reason it
is worth leaving alone; about thirty lines are shared, and a third script would be the moment to
extract them.

**Tests.** `--render-revert` is the pure entry point for the renderer, tested against fixtures for
both export generations, quoting, every row of the table, and each refusal. The run itself is tested
against a fake device: two shell shims on `PATH`, `ssh` and `scp`, over a state directory holding the
configuration, the file list, the scheduler table and a fake clock the shims advance on each poll.
The fake applies the undo when its clock passes the entry's `start-time`, and a knob makes the
switch stop answering after the change until that happens. Cases: applied cleanly; reverted with an
identical export; reverted with a different one; never came back; refused before any write.

**Docs.** `docs/runbooks/switch-change.md` — the procedure, the undo's shape, what to do when the
revert fires, and the two RouterOS facts above — plus its `mkdocs.yml` entry under Running it, and
the `ansible/README.MD` row and section that name the second script beside the collector.

## What is left to the builder

- The renderer's parsing strategy (awk beside the collector's redactor, or bash), and the exact
  refusal messages.
- The wording of the four phase lines and of the diff between exports.
- Whether the guard is `:len [...] > 0` or a `:foreach` for selectors that can match several objects;
  a selector matching more than one object is a refusal unless the row says otherwise.
- The fake device's internals, and which of the five exit paths each test drives.
- How the undo ordering is decided when one line's object is another line's parent.

## Verification

1. `bash -n ansible/switches/switch-change.sh`, and `shellcheck` when present.
2. `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests` with the
   new renderer and fake-device cases green.
3. `hack/check-docs.sh --base origin/develop` with the new page in the nav and cited from
   `ansible/README.MD`.
4. **The proof this repository cannot run yet:** one real change on one switch — the management
   address moved to a wrong one — reverted by the timer on its own, the switch answering again, the
   export identical to the pre-change one. That needs #485's key and is the operator's step.

## Risks

- **The renderer misses a shape.** Mitigated by refusal instead of guesswork, and by the undo being
  shown before it is armed; a refusal costs a run, not a switch.
- **`scp -O` upload.** RouterOS accepts SCP, with `-O` forced, and the upload is verified by reading
  the file list back before the timer is armed; a failed upload stops the run with nothing on the
  device to undo.
- **A change that breaks the switch so badly the timer cannot run it** — a change to `/system
  scheduler` or `/user` is refused for that reason. Anything else: the binary backup taken at arm
  time is the last resort when it could be taken at all, and card #484's upgrade runbook already
  names netinstall plus the collector's export as the floor.
- **Reverting this switch drops every port's configuration mid-change.** A revert only happens after
  the change already took the switch away, so the alternative is worse; the runbook says so.
- **The window is a human deadline.** Too short and a slow verification cancels nothing; too long and
  a broken switch waits. Ten minutes by default, and the entry's start-time is read back so the
  person is told the exact minute it fires.
