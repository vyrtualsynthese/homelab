# Carrying a switch change as netcfg, and undoing a change that moves its own selector — design

**Status:** draft, awaiting approval · **Date:** 2026-11-25 · **Board:** homelab, card #486
**Builds on:** `2026-11-25-switch-change-revert-design.md`, merged in PR #1535, and card #485 (the
`deploy` account), which is done.

## Intent

Card #486 stays in `Doing` until a timer has fired on a real switch. Reaching that point needs two
things that do not exist yet, and this design covers both:

1. **A way for the agent to run the change script with the key it may not read.** The key belongs to
   the UNIX account `netcfg`, whose home is `0700`; `~/.ssh/switches-deploy` as the runbook describes
   does not exist for the agent, and `/home/vsynthese` is `0750`, so `netcfg` cannot read a change
   file there either. `docs/runbooks/switch-access.md` promises "a sudoers rule that card 698 adds".
   There is no such rule.

2. **An undo for a change that rewrites the attribute its own selector matches on.** The card's
   acceptance is "a change that moves the management address to a wrong one and is put back by the
   timer". Today the script arms this undo for it:

   ```
   :if ([:len [/ip address find address="192.168.1.11/24"]] > 0) do={ /ip address set [find address="192.168.1.11/24"] address=192.168.1.11/24 }
   ```

   The change sets `address=192.168.1.211/24`, so `find address="192.168.1.11/24"` matches nothing
   afterwards: the guard is false, the timer fires, and it puts nothing back. Reproduced against
   `hack/fixtures/switch-change/exports/v7-*` on 2026-11-25. That is worse than having no timer: the
   run reports an armed way back that cannot work.

## Goals

1. One narrow, reviewable gate through which the agent runs a change as `netcfg`, without ever
   holding or reading the key, and without being able to run code as `netcfg`.
2. The installed artefacts are installable by one command the operator runs with `sudo`, are
   idempotent, and refuse an unknown or looser state.
3. A change that would leave an undo which cannot match its object is **refused before anything is
   written**, naming the line and the reason.
4. The live proof the card asks for: a timer fires on a real switch and the switch comes back without
   a session, with the export before and after compared.

## Non-goals

- Carrying a change made in Winbox through the gate. `--arm-only` stays a manual tool.
- Undoing a change that removes its selector's object by `remove` — that path already re-adds from the
  plain export and is unaffected.
- A general sudo grant for the agent. The gate authorises one wrapper, one invocation shape.
- Making the router's configuration declarative, or a drift check. Unchanged from the earlier design.

## Constraints

- The script that runs as `netcfg` must be **root-owned and not writable by `vsynthese`**. A sudo rule
  naming the repository copy would let the agent edit the code that can read the key.
- `sudo` resets the environment on this host, so nothing may come from an environment variable the
  agent sets. The wrapper sets what the script needs.
- `netcfg` cannot traverse `/home/vsynthese` or write under `/tmp/opencode`; both paths have to change
  for the gate, and neither can be widened.
- `crs309` (`192.168.1.43`) still runs RouterOS 6.49, which signs with SHA-1 RSA; #485's proof needed
  `PubkeyAcceptedAlgorithms=+ssh-rsa`. **Measured on 2026-11-25 by the first `--dry-run`: it is still
  needed**, the login ended at `Permission denied (publickey,password)`, so one line was added to
  `ros()` and `put()` with a comment naming card #696, which removes it.
- Nothing under `cluster/` is touched.

Measured on the devices on 2026-11-25, both added after the first live runs:

- RouterOS serves SCP only to an account holding the `ftp` policy. Without it the key authenticates
  and the file transfer's **exec channel** is refused (`exec request failed on channel 0`). `deploy`
  is a user in the group `deploy`, and that group's policy gained `ftp` with the other four rights
  unchanged; the user record carries no policy of its own.
- A plain `/file print` shortens a name to nineteen characters and `...`. Every name here is longer,
  so every check that looks a file up by name reads `print detail`, which prints it whole.

## Approaches considered

### The gate

| | What it trades |
|---|---|
| **A. Narrow wrapper, root-owned (chosen)** | ~70 lines of bash to review and test. The agent gets `--target`, `--change`, `--window`, `--expect`, `--yes`, `--dry-run`, `--arm-only`; the script's `--render-undo` mode, which echoes values from whichever file it is pointed at, is unreachable from the agent's side. |
| B. Sudo rule on the installed script | Four install lines and no new code, but it hands the agent the script's whole CLI as `netcfg`. `--render-undo --export <path>` turns any file `netcfg` can read into a slow oracle. Not exploitable against the key today, because the key does not parse as a RouterOS export — but the boundary stops being narrow by construction. |
| C. Operator runs every change by hand | The key never leaves a human session, and needs no privileged artefact. Rejected by the operator: every switch change would wait for a terminal. |

### The selector-destroying change

| | What it trades |
|---|---|
| **A. Refuse it, naming the line (chosen)** | The operator is told the change cannot be undone as written and picks a selector the change does not touch. A capability gap, never a silent no-op. |
| B. Fall back to the RouterOS `.id` (`find .id="*3"`) | Undoes the change as written. Makes the way back depend on a device-internal handle that is only meaningful while the object lives, and that the export does not show, so the operator cannot review the undo line they are trusting. |
| C. Prefer another unique attribute from the same export row | Works when the row happens to hold one; `/ip address` rows often hold none that the change leaves alone, so the common case still needs A. |

## What gets built

### Artefacts

| Path | Owner, mode | Why |
|---|---|---|
| `ansible/switches/install-switch-change.sh` | repo, `0755` | the one command the operator runs with `sudo` |
| `ansible/switches/switch-apply.sh` | repo, `0755` | the wrapper's source |
| `/usr/local/lib/homelab/switch-change.sh` | `root:root 0755` | the trusted copy the wrapper runs |
| `/usr/local/sbin/switch-apply` | `root:root 0755` | the gate |
| `/etc/sudoers.d/50-switch-apply` | `root:root 0440` | `vsynthese ALL=(netcfg) NOPASSWD: /usr/local/sbin/switch-apply` |
| `/var/lib/switch-change/inbox` | `vsynthese:netcfg 2750` | where the agent stages a change file; the setgid bit makes files group-`netcfg` |
| `/var/lib/switch-change/work` | `netcfg:vsynthese 0750` | the script's work directory. Each run makes its own `<id>` directory `0700`: the gate's `umask 077` is what the script runs under, so the exports are `netcfg`'s alone, the run prints the diff as the evidence, and the operator reads the files with `sudo` |

The wrapper, running as `netcfg`:

- refuses to run as anyone but `netcfg`;
- accepts only the seven flags above, and passes them through unchanged;
- requires `--target` to be `crs326` or `crs309`;
- requires `--change` to be an absolute path, a regular file, not a symlink, and — after `realpath` —
  inside the inbox;
- pins the change: opens it once, re-checks the descriptor's own target, and hands the script a
  `netcfg`-only copy under the work directory, so the agent-writable inbox cannot aim it elsewhere;
- refuses when the installed script's SHA-256 differs from `/usr/local/lib/homelab/switch-change.sh.sha256`,
  the digest the installer writes, naming the installer: a live change must run the code the operator
  installed, and `netcfg` cannot read the agent's checkout to compare against it;
- sets `HOME=/home/netcfg` and `SWITCH_CHANGE_WORKDIR=/var/lib/switch-change/work`, then `exec`s the
  installed script.

Exit codes: the script's own pass through; the wrapper adds `1` for "refused, nothing touched"
(not installed, drift, change file outside the inbox) and `2` for a bad argument.

The installer:

- refuses anything but `root`, and refuses a dirty tree for the two files it installs (`git diff
  --quiet -- ansible/switches/`), so an unmerged edit cannot become the thing that holds the key;
- creates or checks the two directories. It **refuses** a directory that is group- or
  world-writable, rather than tightening it behind the operator's back;
- installs both files with `install -m 0755 -o root -g root`, and writes
  `/usr/local/lib/homelab/switch-change.sh.sha256` beside the script on the same run;
- writes the sudoers drop-in, validates it with `visudo -cf`, and removes it again if validation
  fails;
- prints the SHA-256 of both installed files and the one command the agent will use.

### The renderer refusal

In the undo renderer, a `set` whose selector matches on an attribute the same line sets is refused:

```
/ip address
set [ find address="192.168.1.11/24" ] address=192.168.1.211/24
```

```
switch-change.sh: <change> line 2: the change sets address, which its own selector matches on; the
undo would find nothing to put back. Select the object by an attribute the change does not set.
```

A `set` that changes more than one attribute is refused when **any** of them is in the selector. The
check is on the selector's own tokens, so a quoted value holding `=` is compared as a value, not
split.

### The live proof

Two runs, both through the wrapper, both in the runbook with the change file quoted inline so it is
reviewable:

1. **`--dry-run` on `crs309`** — reads the switch, renders the undo against a real RouterOS 6 export,
   writes nothing. Proves the gate, authentication as `netcfg`, and the export parse.
2. **`--arm-only --window 3m` on `crs309`** — uploads both files, adds the timer, reads its name, date
   and time back, exits 0 without touching the configuration. Then `/log print` on the switch shows
   the entry firing three minutes later, and the two files are removed by hand.
3. **The revert, on `crs326`** — this is the card's acceptance, and it is *not* the address move: an
   undoable change that cuts the way in, then comes back without a session:

   ```
   /ip service
   set [ find name=ssh ] disabled=yes
   ```

   The undo is `set [find name=ssh] disabled=no`; the selector survives, the timer restores SSH, the
   script's poll at `192.168.1.11` starts answering again, and the export after matches the export
   before (exit 3). Bridging and routing are untouched, so traffic is unaffected while management is
   cut. A five-minute window leaves time to watch it.

   The address move stays refused, and the runbook says why. If the operator wants it undone, that is
   a card of its own (approach B above), not a silent widening of the renderer.

## What the card's acceptance becomes

"a change that moves the management address to a wrong one and is put back by the timer" becomes
"a change that turns SSH off on the switch and is put back by the timer", for the reason above. The
intent the card was written for — the way in is gone, and the device puts itself back with nobody
connected — is the same. The address move is the case the renderer now refuses.

## What stays open

- The `pre-<id>.backup` step still needs `policy`, which `deploy` does not hold; unchanged.
- Card #484 (RouterOS 7) still removes the `+ssh-rsa` allowance.
- The drift check compares the installed script against a root-owned digest, so the digest has to be
  written in the same run as the script. An install interrupted between the two refuses every run
  until it is repeated — loud, not silent.

## Verified

Filled in when the work lands: the test count, `hack/check-docs.sh`, and the three live runs — what
the switch printed, what the export diff was, and the `/log print` line the timer left.
