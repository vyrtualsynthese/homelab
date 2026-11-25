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
