"""The installer: a clean install, a second run, and the states it refuses."""

import hashlib
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

    def test_the_inbox_keeps_the_setgid_bit(self):
        # The agent stages changes 0640; only group inheritance makes them readable by netcfg, so the
        # setgid bit is load-bearing, not decoration.
        done = install(self.dest)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.mode("var/lib/switch-change/inbox"), 0o2750)
        self.assertEqual(self.mode("var/lib/switch-change/work"), 0o750)

    def test_it_ends_on_the_agents_command(self):
        done = install(self.dest)
        self.assertEqual(done.returncode, 0, done.stderr)
        last = done.stdout.strip().splitlines()[-1]
        self.assertIn("sudo -u netcfg /usr/local/sbin/switch-apply", last)

    def test_the_digest_names_the_installed_script(self):
        done = install(self.dest)
        self.assertEqual(done.returncode, 0, done.stderr)
        script = self.dest / "usr/local/lib/homelab/switch-change.sh"
        digest = self.dest / "usr/local/lib/homelab/switch-change.sh.sha256"
        expected = hashlib.sha256(script.read_bytes()).hexdigest() + "  switch-change.sh\n"
        self.assertEqual(digest.read_text(), expected)
        self.assertEqual(self.mode("usr/local/lib/homelab/switch-change.sh.sha256"), 0o644)

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

    def test_the_second_run_names_all_six_artefacts(self):
        self.assertEqual(install(self.dest).returncode, 0)
        done = install(self.dest)
        self.assertEqual(done.returncode, 0, done.stderr)
        already = [l for l in done.stdout.splitlines() if l.startswith("already ")]
        self.assertEqual(len(already), 6, done.stdout)

    def test_a_group_writable_install_dir_is_refused(self):
        # Only the install directory exists: the refusal has to come from the directory check, not
        # from a write that never happens.
        lib = self.dest / "usr/local/lib/homelab"
        lib.mkdir(parents=True)
        lib.chmod(0o775)
        done = install(self.dest)
        self.assertEqual(done.returncode, 1)
        self.assertIn(str(lib), done.stderr)
        self.assertIn("is writable by more than", done.stderr)
        self.assertEqual(self.mode("usr/local/lib/homelab"), 0o775)

    def test_a_drifted_install_dir_is_refused_on_a_second_run(self):
        self.assertEqual(install(self.dest).returncode, 0)
        lib = self.dest / "usr/local/lib/homelab"
        lib.chmod(0o775)
        done = install(self.dest)
        self.assertEqual(done.returncode, 1)
        self.assertIn(str(lib), done.stderr)
        self.assertEqual(self.mode("usr/local/lib/homelab"), 0o775)

    def test_a_symlinked_destination_is_refused(self):
        lib = self.dest / "usr/local/lib/homelab"
        lib.mkdir(parents=True)
        lib.chmod(0o755)
        target = self.tmp / "elsewhere.sh"
        target.write_text("#!/bin/bash\n")
        (lib / "switch-change.sh").symlink_to(target)
        done = install(self.dest)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not a regular file", done.stderr)

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
