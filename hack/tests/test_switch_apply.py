"""The gate's contract: what it accepts, what it refuses, and who may run it."""

import hashlib
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATE = ROOT / "ansible" / "switches" / "switch-apply.sh"

# Only what a run needs: the ambient environment is never inherited, so nothing a session exports
# can change what these tests prove.
def clean_env():
    return {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "TMPDIR": os.environ.get("TMPDIR", "/tmp")}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_gate():
    return subprocess.run(["bash", str(GATE), "--target", "crs326", "--change", "/x"],
                          text=True, capture_output=True, env=clean_env(), timeout=30)


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
        self.digest = self.tmp / "switch-change.sh.sha256"
        self.write_digest(self.script)
        self.change = self.inbox / "c.rsc"
        self.change.write_text("/ip service\nset [ find name=ssh ] disabled=yes\n")

    def write_digest(self, target, path=None):
        (path or self.digest).write_text(f"{sha256(target)}  {target.name}\n")

    def gate(self, call):
        """Source the gate with the temp paths, then run the named function."""
        body = f'''
source "{GATE}"
INBOX="{self.inbox}"; WORK="{self.work}"; SCRIPT="{self.script}"
DIGEST="{self.digest}"; HOME_DIR="{self.tmp}"
{call}
'''
        return subprocess.run(["bash", "-c", body], text=True, capture_output=True,
                              env=clean_env(), timeout=30)

    def test_it_refuses_to_run_as_another_user(self):
        done = run_gate()
        self.assertEqual(done.returncode, 1)
        self.assertIn("netcfg", done.stderr)

    def test_a_relative_change_path_is_refused(self):
        done = self.gate(call='check_change "inbox/c.rsc"; echo ok')
        self.assertNotEqual(done.returncode, 0)

    def test_a_change_outside_the_inbox_is_refused(self):
        other = self.tmp / "elsewhere.rsc"
        other.write_text("x")
        done = self.gate(call=f'check_change "{other}"; echo ok')
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("ok", done.stdout)
        self.assertFalse((self.work / "staged").exists())

    def test_a_directory_named_like_the_inbox_prefix_is_refused(self):
        evil = self.tmp / "inbox-evil"
        evil.mkdir()
        f = evil / "c.rsc"
        f.write_text("x")
        done = self.gate(call=f'check_change "{f}"; echo ok')
        self.assertEqual(done.returncode, 1)

    def test_a_symlink_inside_the_inbox_is_refused(self):
        link = self.inbox / "link.rsc"
        link.symlink_to(self.change)
        done = self.gate(call=f'check_change "{link}"; echo ok')
        self.assertEqual(done.returncode, 1)

    def test_a_change_with_another_hard_link_is_refused(self):
        link = self.tmp / "same-file.rsc"
        os.link(self.change, link)
        done = self.gate(call=f'check_change "{self.change}"; echo ok')
        self.assertEqual(done.returncode, 1)
        self.assertIn("other links", done.stderr)

    def test_a_directory_change_path_is_refused(self):
        done = self.gate(call=f'check_change "{self.inbox}"; echo ok')
        self.assertEqual(done.returncode, 1)

    def test_a_fifo_change_path_is_refused(self):
        fifo = self.inbox / "f.rsc"
        os.mkfifo(fifo)
        done = self.gate(call=f'check_change "{fifo}"; echo ok')
        self.assertEqual(done.returncode, 1)

    def test_a_target_outside_the_two_names_is_refused(self):
        done = self.gate(call='target=other; check_target; echo ok')
        self.assertNotEqual(done.returncode, 0)

    def test_a_change_inside_the_inbox_is_accepted(self):
        done = self.gate(call=f'check_change "{self.change}"; echo ok')
        self.assertEqual(done.returncode, 0)
        self.assertIn("ok", done.stdout)

    def test_an_unknown_flag_is_a_usage_error(self):
        done = self.gate(call='parse --target crs326 --change x --force')
        self.assertEqual(done.returncode, 2)

    def test_a_missing_option_value_is_a_usage_error(self):
        done = self.gate(call='parse --target')
        self.assertEqual(done.returncode, 2)

    def test_a_stale_installed_script_is_refused(self):
        self.script.write_text("#!/bin/bash\nexit 0\n# newer\n")
        done = self.gate(call="check_installed; echo ok")
        self.assertEqual(done.returncode, 1)
        self.assertIn("install-switch-change.sh", done.stderr)

    def test_a_missing_digest_is_refused(self):
        self.digest.unlink()
        done = self.gate(call="check_installed; echo ok")
        self.assertEqual(done.returncode, 1)
        self.assertIn("install-switch-change.sh", done.stderr)

    def test_paths_that_reach_it_through_the_environment_are_ignored(self):
        # sudo resets the environment today; the gate must not depend on that. A surviving SCRIPT and
        # DIGEST pair would retarget the code the digest vouches for.
        body = f'''
SCRIPT=/tmp/not-the-installed-one
DIGEST=/tmp/not-the-digest
INBOX=/tmp/not-the-inbox
source "{GATE}"
printf "%s\\n%s\\n%s\\n" "$SCRIPT" "$DIGEST" "$INBOX"
'''
        done = subprocess.run(["bash", "-c", body], text=True, capture_output=True,
                              env=clean_env(), timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), [
            "/usr/local/lib/homelab/switch-change.sh",
            "/usr/local/lib/homelab/switch-change.sh.sha256",
            "/var/lib/switch-change/inbox",
        ])

    def test_the_scripts_own_overrides_do_not_survive_the_gate(self):
        # The account, the key and the target list are fixed on disk, not by the caller's environment.
        shim = self.tmp / "env-shim.sh"
        shim.write_text('#!/bin/bash\n'
                        'printf "%s|%s|%s\\n" "${SWITCH_CHANGE_KEY:-none}" '
                        '"${SWITCH_CHANGE_USER:-none}" "${SWITCH_CHANGE_TARGETS:-none}"\n')
        shim.chmod(0o755)
        digest = self.tmp / "env-shim.sh.sha256"
        self.write_digest(shim, digest)
        body = f'''
source "{GATE}"
INBOX="{self.inbox}"; WORK="{self.work}"; SCRIPT="{shim}"; DIGEST="{digest}"
HOME_DIR="{self.tmp}"; TARGET_USER="$(id -un)"
main --target crs326 --change "{self.change}"
'''
        env = dict(clean_env(), SWITCH_CHANGE_KEY="/tmp/another-key",
                   SWITCH_CHANGE_USER="admin", SWITCH_CHANGE_TARGETS="crs999=10.0.0.9")
        done = subprocess.run(["bash", "-c", body], text=True, capture_output=True,
                              env=env, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "none|none|none")

    def test_a_matching_install_is_accepted(self):
        self.write_digest(self.script)
        done = self.gate(call="check_installed; echo ok")
        self.assertEqual(done.returncode, 0)
        self.assertIn("ok", done.stdout)

    # A shim that prints its arguments, so the run can be inspected instead of executed.
    def shim(self):
        path = self.tmp / "shim.sh"
        path.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
        path.chmod(0o755)
        return path

    def run_main(self, *flags):
        shim = self.shim()
        digest = self.tmp / "shim.sh.sha256"
        self.write_digest(shim, digest)
        body = f'''
source "{GATE}"
INBOX="{self.inbox}"; WORK="{self.work}"; SCRIPT="{shim}"; DIGEST="{digest}"
HOME_DIR="{self.tmp}"; TARGET_USER="$(id -un)"
main --target crs326 --change "{self.change}" {" ".join(flags)}
'''
        return subprocess.run(["bash", "-c", body], text=True, capture_output=True,
                              env=clean_env(), timeout=30)

    def handed_change(self, done):
        args = done.stdout.split()
        return pathlib.Path(args[args.index("--change") + 1])

    def test_the_passthrough_flags_reach_the_script_in_order(self):
        done = self.run_main("--dry-run", "--yes")
        self.assertEqual(done.returncode, 0, done.stderr)
        args = done.stdout.split()
        self.assertEqual(args[0:2], ["--target", "crs326"])
        self.assertEqual(args[2], "--change")
        self.assertTrue(args[3].startswith(str(self.work / "staged") + "/"), args[3])
        self.assertEqual(args[4:], ["--dry-run", "--yes"])

    def test_the_script_is_handed_a_staged_copy_not_the_inbox_path(self):
        done = self.run_main()
        self.assertEqual(done.returncode, 0, done.stderr)
        handed = self.handed_change(done)
        self.assertTrue(str(handed).startswith(str(self.work / "staged") + "/"), handed)
        self.assertNotEqual(handed, self.change)
        self.assertEqual(handed.read_text(), self.change.read_text())
        self.assertEqual(oct(handed.stat().st_mode & 0o777), "0o600")

    def test_the_pin_survives_a_swap_between_check_and_stage(self):
        original = self.change.read_text()
        decoy = self.tmp / "decoy.rsc"
        decoy.write_text("decoy bytes\n")
        moved = self.tmp / "moved-away.rsc"
        body = f'''
source "{GATE}"
INBOX="{self.inbox}"; WORK="{self.work}"; SCRIPT="{self.script}"; DIGEST="{self.digest}"
HOME_DIR="{self.tmp}"; TARGET_USER="$(id -un)"
change="{self.change}"
check_change "$change"
mv -- "$change" "{moved}"
ln -s "{decoy}" "$change"
stage_change
cat -- "$staged"
'''
        done = subprocess.run(["bash", "-c", body], text=True, capture_output=True,
                              env=clean_env(), timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, original)
        self.assertNotEqual(done.stdout, decoy.read_text())

    def test_a_stale_staged_file_is_removed_on_the_next_run(self):
        staged = self.work / "staged"
        staged.mkdir()
        staged.chmod(0o700)
        old = staged / f"stage-1700000000-{os.getpid()}.rsc"
        old.write_text("old")
        two_days_ago = old.stat().st_mtime - 2 * 24 * 60 * 60
        os.utime(old, (two_days_ago, two_days_ago))
        dead = staged / "stage-1700000000-999999999.rsc"
        dead.write_text("dead process")
        done = self.run_main()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(old.exists())
        self.assertFalse(dead.exists())
        remaining = list(staged.glob("stage-*.rsc"))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].read_text(), self.change.read_text())
