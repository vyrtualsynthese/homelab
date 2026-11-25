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
