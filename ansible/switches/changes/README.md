# Change files

A change file is a RouterOS script fragment that `../switch-change.sh` carries onto a switch: the
section header its lines belong to, then the commands, exactly as `/import` would read them.

```
/interface bridge port
set [ find interface="sfp-sfpplus1" ] pvid=20
```

It is not a diff of two exports. The undo is built from these lines, so every line has to be one the
renderer can invert: `add`, `set`, `remove`, `enable`, `disable`. A `move`, an `add` with no
identifying attribute, or a `set` whose old value is in neither export is refused before anything is
written, with the change file and the line named. The runbook,
[`docs/runbooks/switch-change.md`](../../../docs/runbooks/switch-change.md), says why and what the
undo looks like.

A change file holds **no secret**. If a line needs a password or a key, it does not belong here.

Three paths may not appear in one change file, in any form:

- `/system scheduler` — a line under it could remove `revert-<id>`, the timer that cancels the run
  itself;
- `/user` — a line under it could remove the account that cancels it;
- `/file` — a line under it could rewrite or delete the undo file itself.

`/import`, `/system reset`, `/system backup` and `/system reboot` are refused for the same reason. So
is a line the renderer does not recognise as one of `add`, `set`, `remove`, `enable` and `disable`,
and so is a value holding a `}`: it would close the guard the undo line is written in.

Nothing in this directory runs on its own. `switch-change.sh` is the only caller, and it refuses a
change that cannot be undone before it touches a switch.
