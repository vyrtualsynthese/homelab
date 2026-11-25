# Gateway for a switch change, and an undo that can find its object — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent carry a change onto a switch as `netcfg` through one root-owned wrapper, and refuse a change whose armed undo could never match its own object.

**Architecture:** Three small bash artefacts — a renderer refusal inside `ansible/switches/switch-change.sh`, a gate `ansible/switches/switch-apply.sh` installed to `/usr/local/sbin/switch-apply`, and an idempotent `ansible/switches/install-switch-change.sh` that installs both plus `/etc/sudoers.d/50-switch-apply`. The gate is sourceable so its checks are unit-testable, and the installer takes `--destdir` only when it is not root, so the tests exercise the real install path into a temp tree.

**Tech Stack:** bash (`set -uo pipefail`, no `set -e`), awk (the embedded renderer), python3 `unittest` under pytest, `install`, `visudo`, `sha256sum`, `realpath`.

**Spec:** `docs/superpowers/specs/2026-11-25-switch-change-gate-design.md`

## Global Constraints

- Every file here is bash: `set -uo pipefail` (never `set -e`), and `bash -n` clean. `shellcheck` when installed.
- Tests: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`.
- Docs: a page carries the five banner labels (`In one line`, `Use it when`, `Verified`, `Kind:`, `Sources`); `bash hack/check-docs.sh` must pass.
- Commit prefixes: `🚀` feature, `🐛` fix, `📝` docs, `♻️` refactor.
- Nothing under `cluster/` is touched. No secret, key or token enters the repository.
- The installed script and wrapper are `root:root 0755`; the sudoers drop-in is `root:root 0440`.
- Paths fixed by the spec: inbox `/var/lib/switch-change/inbox` (`vsynthese:netcfg 2750`), work directory `/var/lib/switch-change/work` (`netcfg:vsynthese 0750`), installed script `/usr/local/lib/homelab/switch-change.sh`, wrapper `/usr/local/sbin/switch-apply`, sudoers `/etc/sudoers.d/50-switch-apply`, canonical checkout `/home/vsynthese/projets/homelab`.
- The wrapper accepts exactly: `--target`, `--change`, `--window`, `--expect`, `--yes`, `--dry-run`, `--arm-only`.
- Exit codes: the script's own pass through; wrapper `1` = refused with nothing touched, `2` = bad argument.

## Review Focus

1. **A `--change` that is the inbox directory, a FIFO, a symlink, or a path that merely starts with the inbox's name** (`/var/lib/switch-change/inbox-evil/x.rsc`): the gate must refuse each, or a file the agent controls is imported with the key's account. Test in Task 2.
2. **The renderer refusal firing where it must not** — an `add` keyed by an attribute it also sets, or a `set` whose selector uses a *different* attribute: both must still render. Pinned by the existing `test_an_add_keyed_by_comment_uses_the_comment` and `test_a_set_while_the_selector_uses_another_attribute_still_renders` in Task 1.
3. **Installing twice, and installing over a looser tree** (inbox `0777`, sudoers `0644`): the second run must succeed unchanged, and the loose tree must be refused, not quietly tightened. Test in Task 3.
4. **`visudo` rejecting the drop-in** (a bad line): the installer removes the file again and exits non-zero, leaving no sudoers file. Test in Task 3.
5. **Running the gate when the canonical checkout is absent or its copy differs** (the operator upgraded the script and did not reinstall): refuse with one line naming the installer, never run a stale copy. Test in Task 2.

---

### Task 1: Refuse a `set` that rewrites its own selector

**Files:**
- Modify: `ansible/switches/switch-change.sh` (the awk renderer, `undo_set` and the text helpers around it)
- Create: `hack/fixtures/switch-change/changes/refuse-selector-rewrite.rsc`
- Create: `hack/fixtures/switch-change/changes/refuse-selector-partial.rsc`
- Test: `hack/tests/test_switch_change.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no new CLI. `switch-change.sh --render-undo` keeps exit `1` for every refusal, with stdout empty and the message on stderr. Later tasks call the script unchanged.

- [ ] **Step 1: Write the failing tests**

In `hack/tests/test_switch_change.py`, add to `RefusalTests`:

```python
    def test_a_set_that_rewrites_its_own_selector_is_refused(self):
        err = self.refused("refuse-selector-rewrite.rsc")
        self.assertIn("line 2", err)
        self.assertIn("address", err)
        self.assertIn("selector", err)

    def test_a_set_that_rewrites_one_of_two_selector_attributes_is_refused(self):
        err = self.refused("refuse-selector-partial.rsc")
        self.assertIn("line 2", err)
        self.assertIn("pvid", err)
```

And in `RendererV7Tests`, pin that the refusal is narrow:

```python
    def test_a_set_whose_selector_uses_another_attribute_still_renders(self):
        out = self.render("quoted-value.rsc")
        self.assertIn('find interface="sfp-sfpplus2"', out)
        self.assertIn("comment=", out)
```

(The existing `quoted-value.rsc` test already covers this; add the name only if it is not already present.)

- [ ] **Step 2: Create the two fixture change files**

`hack/fixtures/switch-change/changes/refuse-selector-rewrite.rsc`:

```
/ip address
set [ find address="192.168.1.11/24" ] address=192.168.1.211/24
```

`hack/fixtures/switch-change/changes/refuse-selector-partial.rsc`:

```
/interface bridge port
set [ find interface="sfp-sfpplus1" pvid=10 ] pvid=20
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py -k selector`
Expected: both refusal tests FAIL, because the renderer emits an undo line and exits 0.

- [ ] **Step 4: Implement the refusal**

In `ansible/switches/switch-change.sh`, beside `keyname`/`keyval`, add:

```awk
  # A selector the change itself rewrites can never match when the undo runs: the attribute it finds
  # the object by is the one the change replaced.
  function sel_uses(sel, k,   st, sn, i) {
    sn = tokens(sel, st)
    for (i = 1; i <= sn; i++) if (keyname(st[i]) == k) return 1
    return 0
  }
```

In `undo_set`, inside the argument loop and **after** the existing `if (k == "")` refusal:

```awk
      if (sel_uses(SEL, k)) {
        do_refuse(lineno, "the change sets " k ", which its own selector matches on, so the undo would find nothing to put back; select the object by an attribute the change leaves alone")
        return
      }
```

- [ ] **Step 5: Run the whole switch-change suite**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_change.py`
Expected: PASS, including the existing 57 cases — no fixture change sets an attribute its own selector uses.

- [ ] **Step 6: Commit**

```bash
git add ansible/switches/switch-change.sh hack/fixtures/switch-change/changes/refuse-selector-*.rsc hack/tests/test_switch_change.py
git commit -m "🐛 fix(switches): refuse a set whose undo could never find its object"
```

---

### Task 2: The gate, `ansible/switches/switch-apply.sh`

**Files:**
- Create: `ansible/switches/switch-apply.sh`
- Test: `hack/tests/test_switch_apply.py`

**Interfaces:**
- Consumes: the installed script path `/usr/local/lib/homelab/switch-change.sh`, its flags, and its exit codes (Task 1 leaves them unchanged).
- Produces: `main "$@"`, callable only as `netcfg`, and the sourced functions `parse`, `check_target`, `check_change`, `check_installed`, `usage`, `refuse`, `badarg`. Variables the tests override after sourcing: `INBOX`, `WORK`, `SCRIPT`, `CANON`, `TARGETS`, `TARGET_USER`, `HOME_DIR`. Task 3 installs this file verbatim.

- [ ] **Step 1: Write the failing tests**

Create `hack/tests/test_switch_apply.py`:

```python
"""The gate's contract: what it accepts, what it refuses, and who may run it."""

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATE = ROOT / "ansible" / "switches" / "switch-apply.sh"


def run_gate(args=(), env=None):
    merged = {k: v for k, v in os.environ.items() if not k.startswith("SWITCH_")}
    merged.update(env or {})
    return subprocess.run(["bash", str(GATE), *args], text=True,
                          capture_output=True, env=merged)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.inbox = self.tmp / "inbox"
        self.inbox.mkdir()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.script = self.tmp / "switch-change.sh"
        self.script.write_text("#!/bin/bash\nexit 0\n")
        self.script.chmod(0o755)
        self.canon = self.tmp / "canon.sh"
        self.canon.write_text(self.script.read_text())
        self.change = self.inbox / "c.rsc"
        self.change.write_text("/ip service\nset [ find name=ssh ] disabled=yes\n")

    def gate(self, args, **over):
        """Source the gate with the temp paths, then call the named function."""
        body = f'''
source "{GATE}"
INBOX="{self.inbox}"; WORK="{self.work}"; SCRIPT="{self.script}"
CANON="{self.canon}"; HOME_DIR="{self.tmp}"
{over.pop("call")}
'''
        return subprocess.run(["bash", "-c", body], text=True, capture_output=True)

    def test_it_refuses_to_run_as_another_user(self):
        done = run_gate(["--target", "crs326", "--change", "/x"])
        self.assertEqual(done.returncode, 1)
        self.assertIn("netcfg", done.stderr)

    def test_a_relative_change_path_is_refused(self):
        done = self.gate("", call=f'check_change "inbox/c.rsc"; echo ok')
        self.assertNotEqual(done.returncode, 0)

    def test_a_change_outside_the_inbox_is_refused(self):
        other = self.tmp / "elsewhere.rsc"
        other.write_text("x")
        done = self.gate("", call=f'check_change "{other}"; echo ok')
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("ok", done.stdout)

    def test_a_directory_named_like_the_inbox_prefix_is_refused(self):
        evil = self.tmp / "inbox-evil"
        evil.mkdir()
        f = evil / "c.rsc"
        f.write_text("x")
        done = self.gate("", call=f'check_change "{f}"; echo ok')
        self.assertEqual(done.returncode, 1)

    def test_a_symlink_inside_the_inbox_is_refused(self):
        link = self.inbox / "link.rsc"
        link.symlink_to(self.change)
        done = self.gate("", call=f'check_change "{link}"; echo ok')
        self.assertEqual(done.returncode, 1)

    def test_a_directory_change_path_is_refused(self):
        done = self.gate("", call=f'check_change "{self.inbox}"; echo ok')
        self.assertEqual(done.returncode, 1)

    def test_a_fifo_change_path_is_refused(self):
        fifo = self.inbox / "f.rsc"
        os.mkfifo(fifo)
        done = self.gate("", call=f'check_change "{fifo}"; echo ok')
        self.assertEqual(done.returncode, 1)

    def test_a_target_outside_the_two_names_is_refused(self):
        done = self.gate("", call='target=other; check_target; echo ok')
        self.assertNotEqual(done.returncode, 0)

    def test_a_change_inside_the_inbox_is_accepted(self):
        done = self.gate("", call=f'check_change "{self.change}"; echo ok')
        self.assertEqual(done.returncode, 0)
        self.assertIn("ok", done.stdout)

    def test_an_unknown_flag_is_a_usage_error(self):
        done = self.gate("", call='parse --target crs326 --change x --force')
        self.assertEqual(done.returncode, 2)

    def test_a_missing_option_value_is_a_usage_error(self):
        done = self.gate("", call='parse --target')
        self.assertEqual(done.returncode, 2)

    def test_a_stale_installed_script_is_refused(self):
        self.canon.write_text("#!/bin/bash\nexit 0\n# newer\n")
        done = self.gate("", call="check_installed; echo ok")
        self.assertEqual(done.returncode, 1)
        self.assertIn("install-switch-change.sh", done.stderr)

    def test_a_missing_canonical_checkout_is_refused(self):
        self.canon.unlink()
        done = self.gate("", call="check_installed; echo ok")
        self.assertEqual(done.returncode, 1)

    def test_a_matching_install_is_accepted(self):
        done = self.gate("", call="check_installed; echo ok")
        self.assertEqual(done.returncode, 0)
        self.assertIn("ok", done.stdout)

    def test_the_passthrough_flags_reach_the_script_in_order(self):
        shim = self.tmp / "shim.sh"
        shim.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
        shim.chmod(0o755)
        body = f'''
source "{GATE}"
INBOX="{self.inbox}"; WORK="{self.work}"; SCRIPT="{shim}"; CANON="{shim}"
HOME_DIR="{self.tmp}"; TARGET_USER="$(id -un)"
main --target crs326 --change "{self.change}" --dry-run --yes
'''
        done = subprocess.run(["bash", "-c", body], text=True, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["--target", "crs326", "--change",
                                              str(self.change), "--dry-run", "--yes"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_apply.py`
Expected: every test FAILS — the file does not exist, so `bash` exits 127.

- [ ] **Step 3: Write the gate**

Create `ansible/switches/switch-apply.sh`:

```bash
#!/bin/bash
# switch-apply — the one way the agent carries a change onto a switch.
#
# Installed to /usr/local/sbin/switch-apply, owned root, and reached through
# /etc/sudoers.d/50-switch-apply as netcfg. It is the narrow door in front of
# switch-change.sh: the agent writes the change in git, stages it in the inbox,
# and never holds or reads the key the installed script signs in with.
#
#   sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
#        --change /var/lib/switch-change/inbox/<change>.rsc --dry-run
#
# Flags: --target, --change, --window, --expect, --yes, --dry-run, --arm-only.
# Exit: the script's own; 1 = refused, nothing touched; 2 = bad argument.
set -uo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

INBOX="${INBOX:-/var/lib/switch-change/inbox}"
WORK="${WORK:-/var/lib/switch-change/work}"
SCRIPT="${SCRIPT:-/usr/local/lib/homelab/switch-change.sh}"
CANON="${CANON:-/home/vsynthese/projets/homelab/ansible/switches/switch-change.sh}"
TARGETS="${TARGETS:-crs326 crs309}"
TARGET_USER="${TARGET_USER:-netcfg}"
HOME_DIR="${HOME_DIR:-/home/netcfg}"

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0; }

refuse() { printf 'switch-apply: %s\n' "$1" >&2; exit 1; }
badarg() { printf 'switch-apply: %s\n' "$1" >&2; exit 2; }

# One set of arguments, and nothing the script does not take.
parse() {
  target=""; change=""; passthru=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --help|-h) usage ;;
      --target|--change|--window|--expect)
        [ $# -ge 2 ] || badarg "$1 needs a value"
        case "$1" in
          --target) target="$2" ;;
          --change) change="$2" ;;
          *) passthru+=("$1" "$2") ;;
        esac
        shift ;;
      --yes|--dry-run|--arm-only) passthru+=("$1") ;;
      *) badarg "unknown argument '$1'" ;;
    esac
    shift
  done
}

check_target() {
  local t
  for t in $TARGETS; do [ "$t" = "$target" ] && return 0; done
  badarg "unknown target '$target'; the gate carries $TARGETS"
}

# The change file is agent-written data, so it may only be a regular file the agent staged in the
# inbox: never a link, a device, a FIFO, or a path that merely starts with the inbox's name.
check_change() {
  local path="$1"
  case "$path" in
    /*) ;;
    *) refuse "the change must be an absolute path inside $INBOX" ;;
  esac
  [ -e "$path" ] || refuse "no such change file: $path"
  [ -f "$path" ] || refuse "not a regular file: $path"
  [ ! -L "$path" ] || refuse "the change may not be a symlink: $path"
  [ -r "$path" ] || refuse "the change is not readable by $TARGET_USER: $path"
  case "$(realpath -- "$path")" in
    "$INBOX"/*) ;;
    *) refuse "the change must sit in $INBOX: $path" ;;
  esac
}

# A live change must run the reviewed code. The installed copy is root-owned; the canonical
# checkout is what the operator reviewed.
check_installed() {
  [ -x "$SCRIPT" ] || refuse "not installed: $SCRIPT; run sudo ansible/switches/install-switch-change.sh"
  [ -r "$CANON" ] || refuse "cannot check the installed script against $CANON; re-run the installer from that checkout"
  [ "$(sha256sum -- "$SCRIPT" | cut -d' ' -f1)" = "$(sha256sum -- "$CANON" | cut -d' ' -f1)" ] \
    || refuse "the installed script differs from $CANON; re-run sudo ansible/switches/install-switch-change.sh"
}

main() {
  local user
  user="$(id -un)"
  [ "$user" = "$TARGET_USER" ] || refuse "run as $TARGET_USER through sudo, not as $user"
  parse "$@"
  [ -n "$target" ] || badarg "--target is required"
  [ -n "$change" ] || badarg "--change is required"
  check_target
  check_installed
  check_change "$change"
  HOME="$HOME_DIR" SWITCH_CHANGE_WORKDIR="$WORK" \
    exec "$SCRIPT" --target "$target" --change "$change" "${passthru[@]}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_apply.py`
Expected: PASS, 15 tests.

- [ ] **Step 5: Parse check and commit**

Run: `bash -n ansible/switches/switch-apply.sh`

```bash
git add ansible/switches/switch-apply.sh hack/tests/test_switch_apply.py
git commit -m "🚀 feat(switches): carry a change as netcfg through one narrow gate"
```

---

### Task 3: The installer, `ansible/switches/install-switch-change.sh`

**Files:**
- Create: `ansible/switches/install-switch-change.sh`
- Test: `hack/tests/test_switch_install.py`

**Interfaces:**
- Consumes: `ansible/switches/switch-apply.sh` and `ansible/switches/switch-change.sh` from the tree it runs in (Task 2 leaves the gate at that path).
- Produces: `/usr/local/lib/homelab/switch-change.sh`, `/usr/local/sbin/switch-apply`, `/etc/sudoers.d/50-switch-apply`, and the two directories. Prints one line per artefact and the agent's first command.

- [ ] **Step 1: Write the failing tests**

Create `hack/tests/test_switch_install.py`:

```python
"""The installer: a clean install, a second run, and the states it refuses."""

import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "ansible" / "switches" / "install-switch-change.sh"


def install(destdir, extra=()):
    return subprocess.run(["bash", str(INSTALLER), "--destdir", str(destdir), *extra],
                          text=True, capture_output=True)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.dest = self.tmp / "root"

    def mode(self, rel):
        return stat.S_IMODE(os.stat(self.dest / rel).st_mode)

    def test_it_installs_the_three_artefacts(self):
        done = install(self.dest)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.mode("usr/local/lib/homelab/switch-change.sh"), 0o755)
        self.assertEqual(self.mode("usr/local/sbin/switch-apply"), 0o755)
        self.assertEqual(self.mode("etc/sudoers.d/50-switch-apply"), 0o440)

    def test_the_sudoers_file_grants_one_command_to_one_user(self):
        install(self.dest)
        lines = [l for l in (self.dest / "etc/sudoers.d/50-switch-apply").read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
        self.assertEqual(lines, ["vsynthese ALL=(netcfg) NOPASSWD: /usr/local/sbin/switch-apply"])

    def test_a_second_run_changes_nothing_and_succeeds(self):
        self.assertEqual(install(self.dest).returncode, 0)
        done = install(self.dest)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("already", done.stdout.lower())

    def test_a_world_writable_inbox_is_refused_not_tightened(self):
        inbox = self.dest / "var/lib/switch-change/inbox"
        inbox.mkdir(parents=True)
        inbox.chmod(0o777)
        done = install(self.dest)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.mode("var/lib/switch-change/inbox"), 0o777)

    def test_a_loose_sudoers_file_is_refused(self):
        drop = self.dest / "etc/sudoers.d"
        drop.mkdir(parents=True)
        (drop / "50-switch-apply").write_text("x\n")
        (drop / "50-switch-apply").chmod(0o644)
        done = install(self.dest)
        self.assertEqual(done.returncode, 1)

    def test_a_refused_sudoers_line_leaves_no_file(self):
        done = install(self.dest, extra=("--sudoers-line", "nonsense here"))
        self.assertEqual(done.returncode, 1)
        self.assertFalse((self.dest / "etc/sudoers.d/50-switch-apply").exists())

    def test_it_names_the_agent_command(self):
        done = install(self.dest)
        self.assertIn("/usr/local/sbin/switch-apply", done.stdout)
        self.assertIn("sha256", done.stdout.lower())

    def test_it_refuses_a_dirty_tree(self):
        env = dict(os.environ, SWITCH_INSTALL_DIRTY="1")
        done = subprocess.run(["bash", str(INSTALLER), "--destdir", str(self.dest)],
                              text=True, capture_output=True, env=env)
        self.assertEqual(done.returncode, 1)
        self.assertIn("uncommitted", done.stderr)
```

`SWITCH_INSTALL_DIRTY` is the test's stand-in for `git diff` reporting an edit; the installer honours it only together with `--destdir`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_install.py`
Expected: FAIL, the installer does not exist.

- [ ] **Step 3: Write the installer**

Create `ansible/switches/install-switch-change.sh`. Behaviour, in this order:

1. `set -uo pipefail`, `PATH=...`, `umask 022`.
2. Parse `--destdir DIR` and the test-only `--sudoers-line LINE`.
3. If `--destdir` is given and `id -u` is 0 → `die "--destdir is for the tests only"`. If it is not given and `id -u` is not 0 → `die "run with sudo"`.
4. Owner and group: `root:root` normally, the invoking user when `--destdir` is given.
5. Dirty check: `SWITCH_INSTALL_DIRTY=1` (with `--destdir` only) or `git -C "$here/../.." diff --quiet -- ansible/switches` returning non-zero → `die "ansible/switches has uncommitted changes; commit or merge first"`.
6. `install -d` each directory with its mode, then check an existing one: `stat -c '%a'` must equal the expected mode and its group must be right; otherwise `die` naming the current state. Never `chmod` it.
7. For each of the two files, compare the source's SHA-256 with the installed copy: when they are equal, print `already <path> (<sha256>)` and leave it untouched; otherwise `install -m 0755` it and print `installed <path> (<sha256>)`.
8. Write the sudoers file to a temp name in the destination, `chmod 0440`, `visudo -cf` it; on failure remove it and `die`; on success `mv` it into place, printing `already` or `installed` for it as in step 7.
9. Print one closing `say` line with the first command the agent will use.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_switch_install.py`
Expected: PASS, 8 tests.

- [ ] **Step 5: Parse check and commit**

Run: `bash -n ansible/switches/install-switch-change.sh`

```bash
git add ansible/switches/install-switch-change.sh hack/tests/test_switch_install.py
git commit -m "🚀 feat(switches): install the gate and its sudo rule from one command"
```

---

### Task 4: The two runbooks and the pointer

**Files:**
- Modify: `docs/runbooks/switch-access.md` (the gate section, replacing "a sudoers rule that card 698 adds")
- Modify: `docs/runbooks/switch-change.md` (invocation as `netcfg`, the new refusal, the live proof, the banner)
- Modify: `ansible/README.MD` (the paragraph that says the script signs in with `~/.ssh/switches-deploy`)

**Interfaces:**
- Consumes: the command shapes from Tasks 2 and 3, and the refusal text from Task 1.
- Produces: no code.

- [ ] **Step 1: Rewrite the `switch-access.md` gate section**

Add a heading after "Where the key lives" that states: the custodian account, the wrapper path, the sudoers line, the inbox and work directory with their modes, and the install command:

```bash
cd ~/projets/homelab
sudo ./ansible/switches/install-switch-change.sh
```

State that the installed copy is pinned by SHA-256 to the canonical checkout, so a change to the script reaches a switch only after it is merged and the installer is re-run.

- [ ] **Step 2: Rewrite the invocation in `switch-change.md`**

Replace the `--target crs326` examples with the gate form:

```bash
sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
  --change /var/lib/switch-change/inbox/<change>.rsc --dry-run
```

Say that the change file is staged in the inbox first (`install -m 0640`), that the work directory moved to `/var/lib/switch-change/work`, and add the refusal from Task 1 to "What it refuses" with the address-move example and what to do instead.

- [ ] **Step 3: Add the live proof section**

Under **Verified**, replace "Not verified: a real switch" with what will be measured, and add a section "The first live runs" holding the three commands of the spec's live proof, the change file for the revert quoted in a fenced block:

```
/ip service
set [ find name=ssh ] disabled=yes
```

and the expected lines: `armed revert-… for <time> device time (window 5m)`, `reverted: …`, `the export is identical to the one taken before the change`. Keep the banner's `Verified` label honest until Task 5 has the real output.

- [ ] **Step 4: Fix the `ansible/README.MD` paragraph**

It currently says the script "signs in as `deploy` with `~/.ssh/switches-deploy`". Replace with the gate, the `netcfg` account, and a link to `switch-access.md`.

- [ ] **Step 5: Run the documentation checks**

Run: `bash hack/check-docs.sh --base origin/develop`
Expected: `0 problems`, and the banner check passes for both touched pages.

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/switch-access.md docs/runbooks/switch-change.md ansible/README.MD
git commit -m "📝 docs(switches): how a change reaches a switch, and what the gate refuses"
```

---

### Task 5: The live proof (operator installs, agent runs)

**Files:**
- Modify: `docs/runbooks/switch-change.md` (**Verified**, with the measured output)
- No code.

**Interfaces:**
- Consumes: Tasks 1–4, merged to `develop`, and one `sudo` run by the operator.

- [ ] **Step 1: The operator runs the installer**

```bash
cd ~/projets/homelab && git checkout develop && git pull --ff-only
sudo ./ansible/switches/install-switch-change.sh
```

Expected: four `say` lines, one per artefact, and the first command. Stop if the tree is dirty or a directory mode is refused.

- [ ] **Step 2: The dry run on `crs309`**

```bash
install -m 0640 /dev/stdin /var/lib/switch-change/inbox/arm-only.rsc <<'EOF'
/ip service
set [ find name=ssh ] disabled=yes
EOF
chmod 0640 /var/lib/switch-change/inbox/arm-only.rsc
sudo -u netcfg /usr/local/sbin/switch-apply --target crs309 \
  --change /var/lib/switch-change/inbox/arm-only.rsc --dry-run
```

Expected: the phase-1 read, and one undo line `set [find name=ssh] disabled=no`; exit 0, nothing written. If authentication fails on the RouterOS 6 RSA signature, add `-o PubkeyAcceptedAlgorithms=+ssh-rsa` to `ros()` and `put()` in `switch-change.sh` with a comment naming card #696, re-run Task 3's tests, and reinstall before Step 3.

- [ ] **Step 3: The arm-only run**

```bash
sudo -u netcfg /usr/local/sbin/switch-apply --target crs309 \
  --change /var/lib/switch-change/inbox/arm-only.rsc --arm-only --window 3m
```

Expected: `armed revert-… for <hh:mm:ss> device time (window 3m)` and exit 0. Three minutes later the switch's `/log print` shows `/import file=undo-….rsc`, and `ansible/collectors/network-audit.sh`-style reads still answer. Remove the two files by hand afterwards.

- [ ] **Step 4: The revert on `crs326`**

```bash
install -m 0640 /dev/stdin /var/lib/switch-change/inbox/ssh-off.rsc <<'EOF'
/ip service
set [ find name=ssh ] disabled=yes
EOF
chmod 0640 /var/lib/switch-change/inbox/ssh-off.rsc
sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
  --change /var/lib/switch-change/inbox/ssh-off.rsc --yes --window 5m
```

Expected: `verifying: … watching 192.168.1.11`, then `reverted: …`, then exit 3 with the export identical message. Read `/log print` on the switch for the timer's line.

- [ ] **Step 5: Record what happened**

Put the measured lines in `switch-change.md` **Verified**, drop "not verified on a real switch", and note the card's acceptance as satisfied by the SSH change rather than the address move.

- [ ] **Step 6: Run the full suite and the doc checks**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
bash hack/check-docs.sh --base origin/develop
```

Expected: everything green, `0 problems`.

- [ ] **Step 7: Commit, push and comment on the card**

```bash
git add docs/runbooks/switch-change.md
git commit -m "📝 docs(switches): record the first timer fired on a real switch"
git fetch origin && git rebase origin/develop && git push origin feature/switch-change-gate
```

Then one comment on card #486 with the branch, the PR, the three runs and their output lines, and move the card to `Validation`.
