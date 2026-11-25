# Switch change with an armed undo — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ansible/switches/switch-change.sh` carries one `.rsc` change onto a MikroTik switch with an undo armed on the device before the change lands and cancelled only when the switch still answers.

**Architecture:** A bash script in the collector family (embedded awk, `ssh`/`scp` only) with four printed phases — read, arm, apply, verify. The undo is rendered locally from the change file against two pre-change exports and armed as a one-shot `/system scheduler` entry, so it fires with nobody connected. The renderer is a pure CLI entry (`--render-undo`) so it is tested without a device; the phases are tested against a fake `ssh`/`scp` on `PATH`.

**Tech Stack:** bash, awk, `ssh`, `scp -O`, python3 (`unittest`) for tests, RouterOS 6.x and 7.x CLI.

**Spec:** `docs/superpowers/specs/2026-11-25-switch-change-revert-design.md`

## Global Constraints

- Shell: `set -uo pipefail` (never `set -e`; the collector does not, and a failed device command is data, not an abort).
- `bash -n` clean, and `shellcheck` clean when `shellcheck` is installed.
- Every device read strips `\r` (RouterOS answers CRLF, ssh ends its errors with one).
- No secret in an argument, an environment variable or the repository: authentication is `ssh -i` with a key the person running the script holds.
- Nothing under `cluster/` is touched by this work; the repository's GitOps rules do not apply to this directory.
- Tests: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`.
- Docs: a page is in `mkdocs.yml`'s nav or nothing links to it; every page carries the five banner labels (`In one line`, `Use it when`, `Verified`, `Kind:`, `Sources`) that `hack/check-docs.sh` requires.
- Commit prefixes: `🚀` feature, `🐛` fix, `📝` docs, `♻️` refactor.
- Exit codes of `switch-change.sh`, fixed by the spec: `0` applied and timer cancelled · `1` nothing touched · `2` usage · `3` reverted, export identical · `4` reverted, export differs · `5` never answered again.

## File Structure

| Path | Responsibility |
|---|---|
| `ansible/switches/switch-change.sh` | the whole tool: argument parsing, the four phases, the embedded undo renderer |
| `ansible/switches/changes/README.md` | what a change file is, and the two commands that must not appear in one |
| `hack/tests/test_switch_change.py` | renderer cases, and the run cases against the fake device |
| `hack/fixtures/switch-change/exports/` | recorded exports: v7 plain, v7 verbose, v6 plain, v6 verbose |
| `hack/fixtures/switch-change/changes/` | change files, one per renderer case |
| `hack/fixtures/switch-change/fake/ssh`, `.../fake/scp` | the fake device on `PATH` |
| `docs/runbooks/switch-change.md` | the procedure, the undo's shape, what a fired revert means |
| `mkdocs.yml`, `docs/runbooks/README.md`, `ansible/README.MD` | the nav entry and the pointers |

## Review Focus

These are the inputs the spec implies and no task's tests would otherwise pin; each line is a test in the task named beside it.

1. A `set` whose old value is absent from **both** exports: must refuse with the line number, never emit `set k=` with an empty value (RouterOS clears the property instead of restoring it). — Task 1.
2. A value containing `"` or `=` inside quotes: the undo line must carry the value byte for byte, or the restore writes a different value than the one it read. — Task 1.
3. A selector that matches more than one object in the export, or none: refuse, because undoing one of several is a guess. — Task 1.
4. The device clock near midnight, so the window crosses it: refuse to arm and say why, rather than create an entry whose `start-time` is already past on the device's own date. — Task 4.
5. Ctrl-C, `SIGTERM` or a closed terminal during verify: the timer stays armed and the script prints the exact minute it fires and the one command that cancels it. — Task 6.
6. A `revert-*` entry left by an earlier interrupted run: cancelled in phase 1 and printed by name, so a second timer never sits beside an old one. — Task 4.

---

### Task 1: Argument parsing, targets, and the v7 undo renderer

**Files:**
- Create: `ansible/switches/switch-change.sh`
- Create: `hack/fixtures/switch-change/exports/v7-plain.rsc`, `.../v7-verbose.rsc`
- Create: `hack/fixtures/switch-change/changes/*.rsc` (the cases named below)
- Test: `hack/tests/test_switch_change.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the CLI `switch-change.sh --render-undo --major <6|7> --change <file> --export <plain> --verbose-export <verbose>` printing the undo file on stdout and refusals on stderr; `--noise-free` reading stdin and printing stdout with `#` lines and blank lines dropped; `--list-targets`; `--help`; `--target/--change/--window/--expect/--yes/--dry-run` parsed but only `--render-undo`, `--noise-free`, `--list-targets` and `--help` implemented. Internal function names every later task uses: `die`, `say`, `warn`, `usage`, `targets`, `host_of`, `routeros_major`, `render_undo`, `ros`, `put`, `noise_free`, `window_seconds`, `device_now`, `start_time_for`.

`--noise-free` exists as a test entry only, the way the collector exposes `--dump` and `--major`: it is the filter phase 4 compares with, and a filter that quietly dropped a configuration line would make every revert look identical.

- [ ] **Step 1: Record the fixtures**

Both generations print the same shape — a `/section` header, then `add …`, `set [ find … ] …`, `remove`, `enable`, `disable` lines under it — so a fixture that is a single absolute line models nothing a switch prints. Long lines wrap with a trailing `\` and continue on the next line.

`hack/fixtures/switch-change/exports/v7-plain.rsc`:

```
# 2026-11-25 09:14:02 by RouterOS 7.16.1
# software id = ABCD-1234
#
# model = CRS326-24G-2S+
/interface bridge
add name=bridge
/interface bridge port
add bridge=bridge interface=sfp-sfpplus1 pvid=10
add bridge=bridge interface=sfp-sfpplus2 comment="a \"b\" c=d"
/ip address
add address=192.168.1.11/24 interface=bridge network=192.168.1.0
add address=192.168.1.12/24 interface=bridge network=192.168.1.0
/ip route
add dst-address=0.0.0.0/0 gateway=192.168.1.1 \
    check-gateway=ping
```

`.../v7-verbose.rsc` — the same device with the defaults written out, which is what makes a `set` back to a default possible:

```
# 2026-11-25 09:14:02 by RouterOS 7.16.1
#
/interface bridge
add name=bridge mtu=1500 auto-mac=yes protocol-mode=rstp
/interface bridge port
add bridge=bridge interface=sfp-sfpplus1 pvid=10 hw=yes auto-isolate=no
add bridge=bridge interface=sfp-sfpplus2 pvid=1 hw=yes auto-isolate=no comment="a \"b\" c=d"
/ip address
add address=192.168.1.11/24 interface=bridge network=192.168.1.0 actual-interface=bridge
add address=192.168.1.12/24 interface=bridge network=192.168.1.0 actual-interface=bridge
/ip route
add dst-address=0.0.0.0/0 gateway=192.168.1.1 check-gateway=ping distance=1
```

Change files (`hack/fixtures/switch-change/changes/`), each a file whose name starts with the case. A change file is a `.rsc` fragment, so it carries the section header its lines belong to:

| file | content |
|---|---|
| `add-address.rsc` | `/ip address` + `add address=192.168.1.99/24 interface=bridge` |
| `absolute-add.rsc` | `/ip address add address=192.168.1.98/24 interface=bridge` |
| `set-pvid.rsc` | `/interface bridge port` + `set [ find interface="sfp-sfpplus2" ] pvid=20` |
| `remove-route.rsc` | `/ip route` + `remove [ find dst-address="0.0.0.0/0" ]` |
| `disable-port.rsc` | `/interface bridge port` + `disable [ find interface="sfp-sfpplus1" ]` |
| `comment-add.rsc` | `/ip route` + `add comment=uplink gateway=192.168.1.254` |
| `refuse-add-unkeyed.rsc` | `/interface bridge vlan` + `add vlan-ids=20` |
| `refuse-multi-match.rsc` | `/ip address` + `remove [ find interface="bridge" ]` (two addresses match) |
| `refuse-move.rsc` | `/interface bridge port` + `move [ find interface="sfp-sfpplus2" ] destination=1` |
| `refuse-set-unknown-attr.rsc` | `/interface bridge` + `set [ find name="bridge" ] nope=1` |
| `refuse-scheduler.rsc` | `/system scheduler` + `disable [ find name="revert" ]` |
| `refuse-import.rsc` | `/import file=other.rsc` |
| `quoted-value.rsc` | `/interface bridge port` + `set [ find interface="sfp-sfpplus2" ] comment="plain"` |

- [ ] **Step 2: Write the failing renderer tests**

In `hack/tests/test_switch_change.py`, with `ROOT` and `SCRIPT` as in `hack/tests/test_network_audit.py`, and a helper `run(args, env=None)` that merges the environment minus `SWITCH_CHANGE_*`:

```python
class RendererV7Tests(unittest.TestCase):
    def render(self, change, export="v7-plain.rsc", verbose="v7-verbose.rsc"):
        done = run(["--render-undo", "--major", "7",
                    "--change", str(CHANGES / change),
                    "--export", str(EXPORTS / export),
                    "--verbose-export", str(EXPORTS / verbose)])
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_an_added_object_is_removed_under_a_guard(self):
        self.assertEqual(
            self.render("add-address.rsc").strip(),
            ':if ([:len [/ip address find address="192.168.1.99/24"]] > 0) '
            'do={ /ip address remove [find address="192.168.1.99/24"] }')

    def test_an_absolute_change_line_is_read_too(self):
        self.assertIn('find address="192.168.1.98/24"', self.render("absolute-add.rsc"))

    def test_a_set_restores_the_verbose_value_not_the_plain_one(self):
        self.assertIn(
            ':if ([:len [/interface bridge port find interface="sfp-sfpplus2"]] > 0) '
            'do={ /interface bridge port set [find interface="sfp-sfpplus2"] pvid=1 }',
            self.render("set-pvid.rsc"))

    def test_a_removed_object_is_added_back_with_its_wrapped_line_joined(self):
        self.assertIn(
            ':if ([:len [/ip route find dst-address="0.0.0.0/0"]] = 0) '
            'do={ /ip route add dst-address=0.0.0.0/0 gateway=192.168.1.1 check-gateway=ping }',
            self.render("remove-route.rsc"))

    def test_a_disabled_object_is_enabled_again(self):
        self.assertIn(
            ':if ([:len [/interface bridge port find interface="sfp-sfpplus1"]] > 0) '
            'do={ /interface bridge port enable [find interface="sfp-sfpplus1"] }',
            self.render("disable-port.rsc"))

    def test_an_add_keyed_by_comment_uses_the_comment(self):
        self.assertIn('/ip route find comment=uplink', self.render("comment-add.rsc"))

    def test_a_quoted_value_survives_byte_for_byte(self):
        self.assertIn('comment="a \\"b\\" c=d"', self.render("quoted-value.rsc"))
```

And one test per refusal, asserting exit 1, empty stdout, and the line number on stderr:

```python
class RefusalTests(unittest.TestCase):
    def refused(self, change):
        done = run(["--render-undo", "--major", "7",
                    "--change", str(CHANGES / change),
                    "--export", str(EXPORTS / "v7-plain.rsc"),
                    "--verbose-export", str(EXPORTS / "v7-verbose.rsc")])
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stdout, "")
        return done.stderr

    def test_an_add_with_no_key_names_its_line(self):
        self.assertIn("line 2", self.refused("refuse-add-unkeyed.rsc"))

    def test_a_selector_matching_two_objects_is_refused(self):
        self.assertIn("matches 2 objects", self.refused("refuse-multi-match.rsc"))

    def test_a_move_is_refused(self):
        self.assertIn("move", self.refused("refuse-move.rsc"))

    def test_a_set_with_no_old_value_in_either_export_is_refused(self):
        self.assertIn("line 2", self.refused("refuse-set-unknown-attr.rsc"))

    def test_a_change_to_the_scheduler_is_refused(self):
        self.assertIn("/system scheduler", self.refused("refuse-scheduler.rsc"))

    def test_an_import_inside_a_change_is_refused(self):
        self.assertIn("/import", self.refused("refuse-import.rsc"))
```

and the filter's own case:

```python
class NoiseFreeTests(unittest.TestCase):
    def test_comments_and_blank_lines_go_and_every_other_line_stays(self):
        text = "# 2026-11-25 09:14:02 by RouterOS 7.16.1\n\n/ip route\nadd dst-address=0.0.0.0/0 gateway=1.1.1.1\n"
        done = run(["--noise-free"], stdin=text)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "/ip route\nadd dst-address=0.0.0.0/0 gateway=1.1.1.1\n")
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py`
Expected: FAIL — `switch-change.sh` does not exist.

- [ ] **Step 4: Implement the script's shell and the renderer**

`switch-change.sh` opens with the header comment block the collector uses (usage printed by `sed -n '2,Np' "${BASH_SOURCE[0]}"`, so `--help` prints comment lines only), then `set -uo pipefail` and the defaults:

```
SCRIPT_VERSION="2026-11-25.1"
SWITCH_CHANGE_TARGETS="${SWITCH_CHANGE_TARGETS:-crs326=192.168.1.11 crs309=192.168.1.43}"
SWITCH_CHANGE_USER="${SWITCH_CHANGE_USER:-deploy}"
SWITCH_CHANGE_KEY="${SWITCH_CHANGE_KEY:-$HOME/.ssh/switches-deploy}"
SWITCH_CHANGE_WINDOW="${SWITCH_CHANGE_WINDOW:-10m}"
SWITCH_CHANGE_WORKDIR="${SWITCH_CHANGE_WORKDIR:-/tmp/opencode/switch-change}"
SWITCH_CHANGE_CONNECT_TIMEOUT="${SWITCH_CHANGE_CONNECT_TIMEOUT:-5}"
SWITCH_CHANGE_CMD_TIMEOUT="${SWITCH_CHANGE_CMD_TIMEOUT:-20}"
SWITCH_CHANGE_POLL="${SWITCH_CHANGE_POLL:-5}"
```

`validate_targets`, `targets`, `host_of` are the collector's, unchanged in behaviour: a malformed `name=host` pair dies with exit 2 and prints no partial list.

`render_undo <major> <change> <plain> <verbose>` is one embedded awk program. Its contract:

- Join a trailing `\` with the next line first: a real export wraps long lines, and a half line would be parsed as a whole object.
- Skip blank lines and lines whose first non-space character is `#`. A bare `/section` line sets the current path and emits nothing.
- An object line under a section is `<verb> <args>`, where verb is `add`/`set`/`remove`/`enable`/`disable`. A line that starts with `/` may also carry its own path and verb (`/ip address add …`), which is how a change file written by hand reads.
- Refuse, before anything reaches stdout, any path of `/system scheduler`, `/user`, `/system reset`, `/system backup`, a bare `/import`, and any verb outside the five: stderr `switch-change.sh: <change>:<line>: <why>`, exit 1, nothing on stdout. Refusals are collected and all reported.
- The object index for a path is built from the export's `add` lines under that path, keyed by every `k=v` pair on the line; a `set [ find default-name=ether1 ] …` line indexes under `default-name=ether1` too.
- `add`: the key attribute is the first of `name`, `comment`, `address`, `dst-address`, `interface`, `mac-address` present in the args; none of them is a refusal naming the line. The undo is a guard on `find <key>=<value>` and a `remove` of it. The value is copied as written when it was quoted, and quoted when it holds a space, `/`, `=` or `"` (escaped), because RouterOS reads those unquoted as syntax.
- `set`/`disable`/`enable`/`remove`: the selector is the text inside `[ find … ]` (spaces inside the brackets are optional). Match it against the path's objects in the plain export — every `k=v` pair of the selector must appear on the object, values compared with quotes removed. Zero matches, or more than one, is a refusal: undoing one of several is a guess. For `set`, each `k` is restored from the **verbose** export's matching object, and a `k` absent there is a refusal. For `remove`, the object's args are taken from the plain export and re-added. Quotes are copied verbatim.
- Undo lines come out in **reverse** order of the change file.
- Each undo line has the exact shape the tests assert, with a single space after `do={` and one before `}`.

`ros` and `put` are **not** written in this task: nothing here calls them, and a function with no failing test is not TDD. They land in Task 4 with their first caller.

- [ ] **Step 5: Run the tests again**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py`
Expected: PASS, all renderer and refusal cases.

- [ ] **Step 6: Commit**

```bash
git add ansible/switches/switch-change.sh hack/tests/test_switch_change.py hack/fixtures/switch-change
git commit -m "🚀 feat(switches): render a guarded undo for a RouterOS 7 change"
```

---

### Task 2: The renderer reads a RouterOS 6 export

**Files:**
- Create: `hack/fixtures/switch-change/exports/v6-plain.rsc`, `.../v6-verbose.rsc`
- Create: `hack/fixtures/switch-change/changes/add-address-v6.rsc`, `remove-bridge-v6.rsc`, `set-mtu-v6.rsc`
- Modify: `ansible/switches/switch-change.sh` (the `render_undo` awk)
- Test: `hack/tests/test_switch_change.py`

**Interfaces:**
- Consumes: `render_undo` from Task 1.
- Produces: an object index that also carries objects introduced by a `set [ find default-name=… ] …` line, and a refusal when the export's `# … by RouterOS <major>.` header disagrees with `--major`.

- [ ] **Step 1: Record the v6 fixtures**

The parse shape is the same one Task 1 reads; what differs is the header, the export command the phases will use, and the way an object that exists by default is written.

`v6-plain.rsc`:

```
# 2026-11-25 09:14:02 by RouterOS 6.49.10
# software id = WXYZ-5678
#
/interface bridge
add admin-mac=AA:BB:CC:DD:EE:FF auto-mac=no name=bridge
/interface ethernet
set [ find default-name=sfp-sfpplus1 ] name=sfp-sfpplus1
/ip address
add address=192.168.1.43/24 interface=bridge network=192.168.1.0
```

`v6-verbose.rsc` — the same objects with their defaults written out:

```
# 2026-11-25 09:14:02 by RouterOS 6.49.10
#
/interface bridge
add admin-mac=AA:BB:CC:DD:EE:FF auto-mac=no mtu=1500 name=bridge protocol-mode=rstp
/interface ethernet
set [ find default-name=sfp-sfpplus1 ] mtu=1500 name=sfp-sfpplus1
/ip address
add address=192.168.1.43/24 interface=bridge network=192.168.1.0
```

- [ ] **Step 2: Write the failing v6 tests**

```python
class RendererV6Tests(unittest.TestCase):
    def render(self, change, major="6"):
        done = run(["--render-undo", "--major", major,
                    "--change", str(CHANGES / change),
                    "--export", str(EXPORTS / "v6-plain.rsc"),
                    "--verbose-export", str(EXPORTS / "v6-verbose.rsc")])
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_an_added_object_on_a_v6_device_is_removed_under_a_guard(self):
        self.assertIn(
            ':if ([:len [/ip address find address="192.168.1.44/24"]] > 0) '
            'do={ /ip address remove [find address="192.168.1.44/24"] }',
            self.render("add-address-v6.rsc"))

    def test_a_removed_v6_object_is_added_back_with_every_argument(self):
        self.assertIn(
            'do={ /interface bridge add admin-mac=AA:BB:CC:DD:EE:FF auto-mac=no name=bridge }',
            self.render("remove-bridge-v6.rsc"))

    def test_an_object_the_export_introduces_with_set_is_indexed(self):
        self.assertIn(
            'do={ /interface ethernet set [find default-name="sfp-sfpplus1"] mtu=1500 }',
            self.render("set-mtu-v6.rsc"))

    def test_an_export_whose_header_names_another_major_is_refused(self):
        done = run(["--render-undo", "--major", "7",
                    "--change", str(CHANGES / "add-address-v6.rsc"),
                    "--export", str(EXPORTS / "v6-plain.rsc"),
                    "--verbose-export", str(EXPORTS / "v6-verbose.rsc")])
        self.assertEqual(done.returncode, 1)
        self.assertIn("says RouterOS 6", done.stderr)
```

The three change files:

```
/ip address
add address=192.168.1.44/24 interface=bridge
```
```
/interface bridge
remove [ find name="bridge" ]
```
```
/interface ethernet
set [ find default-name="sfp-sfpplus1" ] mtu=1400
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py -k V6`
Expected: FAIL — the index has no `set`-introduced objects, and the header is not read.

- [ ] **Step 4: Index the objects a `set [ find … ]` line introduces, and read the header**

A `set [ find default-name=sfp-sfpplus1 ] mtu=1500` line in an export describes an object that exists on every device of that model; its `default-name` is its identity when it has no `name`. Add it to the index with the selector's pairs plus its own args, so a change that selects it resolves. Read the `by RouterOS <major>.` version out of the export's first comment line; a header naming another major than `--major` is a refusal (`says RouterOS 6`), because the export command and the parse would otherwise disagree.

- [ ] **Step 5: Run the whole file**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py`
Expected: PASS, v7 and v6 cases together.

- [ ] **Step 6: Commit**

```bash
git add ansible/switches/switch-change.sh hack/tests/test_switch_change.py hack/fixtures/switch-change
git commit -m "🚀 feat(switches): read a RouterOS 6 export and refuse a mismatched header"
```

---

### Task 3: A fake switch on `PATH`

**Files:**
- Create: `hack/fixtures/switch-change/fake/ssh`, `.../fake/scp`
- Test: `hack/tests/test_switch_change.py`

**Interfaces:**
- Consumes: `ros` and `put` from Task 1.
- Produces: a python3 fake invoked as `ssh`/`scp` that models a device from environment: `FAKE_STATE` (directory), `FAKE_NOW` (`YYYY-MM-DD HH:MM:SS`, advanced by `FAKE_STEP` seconds per call, default `60`), `FAKE_VERSION` (default `7.16.1`), `FAKE_CONFIG` (the text `/export` returns), `FAKE_CONFIG_AFTER_CHANGE`, `FAKE_CONFIG_AFTER_UNDO`, `FAKE_BREAK_AFTER_CHANGE` (`1` = stop answering after an import of a `change-*` file, until the timer fires), `FAKE_IMPORT_ERROR` (text the import prints before failing).
- Test helper `fake(tag, **env)` creating a state directory and a `PATH` with the two shims in front.

- [ ] **Step 1: Write the failing test**

```python
class FakeDeviceTests(unittest.TestCase):
    def test_the_fake_answers_a_routeros_read_and_an_export(self):
        with fake("read") as f:
            self.assertIn("version: 7.16.1", f.ros("/system resource print"))
            self.assertEqual(f.ros("/export").strip(),
                             (EXPORTS / "v7-plain.rsc").read_text().strip())
```

with `fake()` opening the state directory, writing `config` from the fixture, and exposing `ros(command)` that runs `["ssh", "-i", key, "deploy@192.168.1.11", command]` under the fake `PATH`.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py -k FakeDevice`
Expected: FAIL — no shims.

- [ ] **Step 3: Write the fake devices**

`hack/fixtures/switch-change/fake/ssh` is `#!/usr/bin/env python3`; `scp` execs `ssh` after turning its copy into a file write. The fake implements, and nothing else:

| command | answer |
|---|---|
| `/system resource print` | `version: <FAKE_VERSION> (stable)` plus an `uptime:` line |
| `/system clock print` | `date:` and `time:` from the fake clock, `time-zone-name: Europe/Paris` |
| `/export` / `/export verbose` / `/export hide-sensitive verbose` | the config file |
| `/file print where name="X"` | the entry if it exists, nothing otherwise |
| `/file remove [find name="X"]` | deletes it |
| `/system scheduler print detail where name="X"` | `name="X" … run-count=N`, `start-time=` and `start-date=`, or nothing |
| `/system scheduler add …` | stores name, start-time, on-event |
| `/system scheduler remove [find name="X"]` | deletes it |
| `/system backup save name=X` | creates the entry, or prints `not enough permissions (9)` when `FAKE_BACKUP_DENIED=1` |
| `/import file=X verbose=yes` | `change-*` → config becomes `FAKE_CONFIG_AFTER_CHANGE`; `undo-*` → `FAKE_CONFIG_AFTER_UNDO`; prints `FAKE_IMPORT_ERROR` when set |

Before answering anything, it advances its clock and fires any entry whose `start-time` has arrived: a fired entry with an `undo-*` on-event switches the config to `FAKE_CONFIG_AFTER_UNDO`, sets `run-count=1` and lifts `FAKE_BREAK_AFTER_CHANGE`.

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py -k FakeDevice`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hack/fixtures/switch-change/fake hack/tests/test_switch_change.py
git commit -m "🚀 feat(switches): a fake RouterOS device for the change script's tests"
```

---

### Task 4: Phases read and arm

**Files:**
- Modify: `ansible/switches/switch-change.sh`
- Test: `hack/tests/test_switch_change.py`

**Interfaces:**
- Consumes: `render_undo`, `ros`, `put`, the fake.
- Produces: `switch-change.sh --target <name> --change <file> [--window 10m] [--expect <addr>] [--yes] [--dry-run] [--arm-only]`, with phases 1 and 2 complete; `window_seconds <10m|600s|600>`; `device_now <host>` printing `YYYY-MM-DD HH:MM:SS`; `start_time_for "<date> <time>" <seconds>` printing `HH:MM:SS` or exiting 1 when the window would cross the device's midnight. Work directory files named `<workdir>/<id>/`, `id` being `<target>-<YYYYmmdd-HHMMSS>`.

`--arm-only` stops after arming, for a change applied by hand from Winbox: the undo is the same, and it keeps this task's tests honest about how far they reached.

- [ ] **Step 1: Write the failing tests**

```python
class ArmTests(unittest.TestCase):
    def test_a_dry_run_prints_the_undo_and_writes_nothing(self):
        with fake("dry") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--dry-run", "--yes"])
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("/interface bridge port set [find interface=\"sfp-sfpplus2\"] pvid=1",
                          done.stdout)
            self.assertEqual(f.ros("/system scheduler print detail"), "")
            self.assertEqual(f.files(), [])

    def test_arming_uploads_both_files_and_sets_the_start_time(self):
        with fake("arm") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes", "--window", "10m"])
            self.assertEqual(done.returncode, 0, done.stderr)
            detail = f.ros('/system scheduler print detail')
            self.assertIn('name="revert-', detail)
            self.assertIn("start-time=", detail)
            self.assertIn("run-count=0", detail)
            self.assertEqual(len([n for n in f.files() if n.startswith("undo-")]), 1)
            self.assertEqual(len([n for n in f.files() if n.startswith("change-")]), 1)

    def test_a_previous_runs_timer_is_cancelled_and_named(self):
        with fake("stale") as f:
            f.arm("revert-oldrun", "10:00:00")
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--dry-run", "--yes"])
            self.assertIn("revert-oldrun", done.stdout)
            self.assertNotIn("revert-oldrun", f.ros("/system scheduler print detail"))

    def test_a_window_across_midnight_is_refused_with_nothing_armed(self):
        with fake("midnight", now="2026-11-25 23:58:00") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes"])
            self.assertEqual(done.returncode, 1)
            self.assertIn("midnight", done.stderr)
            self.assertEqual(f.ros("/system scheduler print detail"), "")
            self.assertEqual(f.files(), [])

    def test_without_yes_and_without_a_terminal_it_refuses(self):
        with fake("tty") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only"], stdin=subprocess.DEVNULL)
            self.assertEqual(done.returncode, 2)
            self.assertEqual(f.ros("/system scheduler print detail"), "")

    def test_a_refused_backup_still_arms(self):
        with fake("nobackup", backup_denied="1") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes"])
            self.assertEqual(done.returncode, 0)
            self.assertIn("no binary fallback", done.stdout)
            self.assertIn("run-count=0", f.ros("/system scheduler print detail"))
```

(`f.arm(name, start_time)`, `f.script(args, stdin=...)`, and the `fake()` keywords `now`, `backup_denied`, `version`, `config` are part of the helper; add them to the test file in this task.)

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py -k Arm`
Expected: FAIL.

- [ ] **Step 3: Implement phases 1 and 2**

Order inside the script: resolve target and change file, build `id`, read the device (major, plain export, verbose export, `/system clock print`, and the `revert-*` listing), cancel any stale `revert-*` entry printing each name, render the undo, print it, and — unless `--dry-run` — ask once on `/dev/tty` (or accept `--yes`; neither means exit 2), then `put` both files, read `/file print` back for each, `ros` the backup (a refusal prints `no binary fallback` and continues), and add the entry with `policy=read,write,test interval=0s start-time=<computed>` reading `print detail` back to compare `start-time`.

The RouterOS command for the entry, verbatim:

```
/system scheduler add name=revert-<id> policy=read,write,test interval=0s start-time=<HH:MM:SS> on-event="/import file=undo-<id>.rsc"
```

- [ ] **Step 4: Run the tests**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py -k Arm`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible/switches/switch-change.sh hack/tests/test_switch_change.py
git commit -m "🚀 feat(switches): read a switch and arm the undo before the change"
```

---

### Task 5: Phases apply and verify, and every exit path

**Files:**
- Modify: `ansible/switches/switch-change.sh`
- Test: `hack/tests/test_switch_change.py`

**Interfaces:**
- Consumes: everything from Task 4.
- Produces: the complete tool with the exit-code contract of the Global Constraints.

- [ ] **Step 1: Write the failing tests**

One test per exit path, each asserting the code, one line of stdout that says which path it took, and the device's end state:

```python
class RunTests(unittest.TestCase):
    def test_a_clean_change_cancels_the_timer_and_prints_the_diff(self):
        with fake("clean", after_change=CHANGED) as f:      # CHANGED = v7-plain with pvid=20
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"), "--yes"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("applied", done.stdout)
        self.assertIn("pvid=20", done.stdout)               # the diff, not the export
        self.assertEqual(f.ros("/system scheduler print detail"), "")
        self.assertEqual([n for n in f.files() if n.startswith(("undo-", "change-"))], [])

    def test_a_change_that_takes_the_switch_away_is_reverted_and_identical(self):
        with fake("away", break_after_change="1",
                  after_change=CHANGED, after_undo=(EXPORTS / "v7-plain.rsc").read_text()) as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "3m"])
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("reverted", done.stdout)
        self.assertIn("identical", done.stdout)
        self.assertEqual(f.ros("/system scheduler print detail"), "")

    def test_a_revert_that_leaves_a_difference_exits_4(self):
        with fake("differs", break_after_change="1", after_change=CHANGED,
                  after_undo=(EXPORTS / "v7-plain.rsc").read_text() + "add name=leftover\n") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "3m"])
        self.assertEqual(done.returncode, 4, done.stderr)
        self.assertIn("add name=leftover", done.stdout)

    def test_a_switch_that_never_comes_back_exits_5(self):
        with fake("gone", break_after_change="1", after_change=CHANGED, after_undo=CHANGED) as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "1m"])
        self.assertEqual(done.returncode, 5)
        self.assertIn("did not answer", done.stdout)

    def test_an_import_error_undoes_on_demand_without_waiting(self):
        with fake("error", import_error="bad command (line 1)", after_undo=CHANGED) as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "10m"])
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("on demand", done.stdout)
        self.assertLess(f.calls(), 12)          # it did not sit out the window

    def test_an_entry_whose_run_count_is_one_is_reported_as_reverted(self):
        with fake("fired", after_change=CHANGED, after_undo=CHANGED) as f:
            f.fire_on_arm("1")                  # the entry runs before the first poll
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "10m"])
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertNotIn("applied", done.stdout)
```

`fake()` gains `after_change`, `after_undo`, `break_after_change`, `import_error` keywords, `f.calls()` (the number of device calls so far) and `f.fire_on_arm("1")` (the fake fires the entry as soon as it is added), all part of the helper in this task.

`test_a_change_that_takes_the_switch_away_is_reverted_and_identical` is the card's own verification, driven through the fake: the switch stops answering once the change lands, the timer fires at its `start-time`, the switch answers again, and the export is identical.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py -k Run`
Expected: FAIL.

- [ ] **Step 3: Implement phases 3 and 4**

Apply with `/import file=change-<id>.rsc verbose=yes`; treat ssh's non-zero exit with no output as expected, and treat a printed RouterOS error as the on-demand path (import `undo-<id>.rsc`, remove the entry, remove the files, compare, exit 3 or 4). Then read `run-count` from `print detail`: not `0` → the reverted path without polling. Then poll `--expect` every `SWITCH_CHANGE_POLL` seconds until the window expires; the cancel goes to whichever address answered. Compare with `noise_free` on both sides; the printed diff is `diff -u` of the cleaned files. Remove `revert-<id>` and both uploaded files on every path that reached the device, but leave `pre-<id>.backup` behind and say where it is.

- [ ] **Step 4: Run the tests**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py`
Expected: PASS, all tasks' cases.

- [ ] **Step 5: Commit**

```bash
git add ansible/switches/switch-change.sh hack/tests/test_switch_change.py
git commit -m "🚀 feat(switches): apply a change, verify it, and name every exit path"
```

---

### Task 6: An interrupted run keeps the undo armed

**Files:**
- Modify: `ansible/switches/switch-change.sh`
- Test: `hack/tests/test_switch_change.py`

**Interfaces:**
- Consumes: the tool from Task 5.
- Produces: a `trap` on `INT TERM HUP` that prints the entry's `start-time` and the cancel command and exits 1 without touching the device.

- [ ] **Step 1: Write the failing test**

```python
def test_a_sigterm_during_verify_leaves_the_timer_armed_and_names_it(self):
    # the switch never comes back and the fake clock does not advance, so the poll waits
    with fake("term", break_after_change="1", after_change=CHANGED, step="0") as f:
        proc = f.spawn(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"), "--yes"])
        f.wait_for("verifying", proc)          # phase 4 has started, so the entry is armed
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=10)
    self.assertEqual(proc.returncode, 1)
    self.assertIn("cancel", out)
    self.assertIn("revert-", out)
    self.assertIn("run-count=0", f.ros("/system scheduler print detail"))
```

`f.spawn(args)` and `f.wait_for(text, proc)` are added to the helper in this task: `spawn` is `script` without waiting, `wait_for` reads the child's stdout until the text appears.

Assert both the printed line (`revert-<id>` and the word `cancel`) and that `print detail` still holds the entry.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py -k sigterm`
Expected: FAIL.

- [ ] **Step 3: Add the trap**

The trap is installed once the entry is armed and removed after the cancel: arming a trap before there is an entry to name would print a command for a timer that does not exist.

- [ ] **Step 4: Run the tests and commit**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py
git add ansible/switches/switch-change.sh hack/tests/test_switch_change.py
git commit -m "🐛 fix(switches): an interrupted run leaves the undo armed and names it"
```

---

### Task 7: The page, the nav, the pointers

**Files:**
- Create: `docs/runbooks/switch-change.md`
- Create: `ansible/switches/changes/README.md`
- Modify: `mkdocs.yml`, `docs/runbooks/README.md`, `ansible/README.MD`

**Interfaces:**
- Consumes: the finished CLI.
- Produces: nothing the code depends on.

- [ ] **Step 1: Write the runbook**

`docs/runbooks/switch-change.md`, banner in the shape `docs/runbooks/k3s-upgrade.md` uses, five labels present. It carries: the one command, the four phase lines an operator sees, the shape of the undo with one example line, the two RouterOS facts the design rests on (import is additive and stops at the first failing line; `interval=0s` runs once), the exit-code table, what a fired revert means for the traffic on that switch, what to do with the `pre-<id>.backup` file, and one honest line under **Verified**: the renderer and the fake-device runs are green, no switch has run this yet, card #485 is the key it waits on.

- [ ] **Step 2: Add the nav entry, the runbook index row and the two pointers**

`mkdocs.yml` under `Running it`, beside the K3s and Ubuntu runbooks: `- Changing a switch: runbooks/switch-change.md`. `docs/runbooks/README.md` gets its row in the table there. `ansible/README.MD` gets `switches/switch-change.sh` in the "What is here" table and a section of its own after the network collector's, naming the deploy account (#485) and what the script refuses. `ansible/switches/changes/README.md` says what a change file is, that it holds no secret, and that two paths may not appear in one (`/system scheduler`, `/user`).

- [ ] **Step 3: Run the documentation checks**

Run: `bash hack/check-docs.sh --base origin/develop`
Expected: no failure; the new page's banner and links pass.

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/switch-change.md ansible/switches/changes/README.md mkdocs.yml docs/runbooks/README.md ansible/README.MD
git commit -m "📝 docs(switches): the procedure that changes a switch with an armed undo"
```

---

### Task 8: The gate

**Files:** none modified unless a gate fails.

- [ ] **Step 1: Run every check the repository has**

```bash
bash -n ansible/switches/switch-change.sh
shellcheck ansible/switches/switch-change.sh 2>/dev/null || true
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
bash hack/check-docs.sh --base origin/develop
```

Expected: pytest green including the collector's and the new file's cases; `check-docs.sh` green.

- [ ] **Step 2: Comment on the card, and leave it in `Doing`**

Post one comment on #486 (task id 698) naming the branch, the design and plan paths, what the gates proved, and the one thing that is not proved: the live revert, which waits on #485. Move the card to `Doing` with `PUT /projects/2/views/13/buckets/11/tasks {"task_id": 698}` if it is still in `Backlog`; it does not reach `Validation` until a switch has answered a timer.
