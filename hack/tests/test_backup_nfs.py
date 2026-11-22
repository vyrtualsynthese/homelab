"""Behavioural tests for the nfs job's exclusion check.

`backup-nfs.sh` is the one backup script with no prepare step, so the reading of an `exclude`
word that the node rows get from `prepare.py` is written a second time, in shell. These tests
run the script itself: the exports are real directories and `restic` is a stub that records the
arguments it was handed, so what is asserted is what the job would print and back up on the
night rather than what the script looks like.
"""

import pathlib
import subprocess
import tempfile
import textwrap
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "cluster/helm/backup/files/backup-nfs.sh"


class NfsExclusionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmp = pathlib.Path(scratch.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "restic-calls.txt"
        # Every argument on its own line, so an assertion can read one flag per line.
        stub = self.bin / "restic"
        stub.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            printf '%s\\n' "$@" >> {self.calls}
            printf '%s\\n' --- >> {self.calls}
            """))
        stub.chmod(0o755)

    def export(self, name, files=()):
        """A temporary export directory with the given relative files inside it."""
        root = self.tmp / name
        root.mkdir()
        for relative in files:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        return root

    def run_job(self, rows):
        """Run the job against `rows` — (tag, export directory, exclude words) — and return stdout."""
        listing = self.tmp / "nfs-paths.txt"
        listing.write_text("".join(f"{tag}|{root}|{words}\n" for tag, root, words in rows))
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "RESTIC_REPOSITORY": "stub:repo",
            "RESTIC_PASSWORD": "secret",
            "STREAM": "nfs",
            # The chart mounts the list at /config; here it is a file the test wrote.
            "NFS_PATHS_FILE": str(listing),
        }
        done = subprocess.run(["/bin/sh", str(SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def unused_lines(self, out):
        return [line for line in out.splitlines() if line.startswith("exclude-unused")]

    def test_a_word_that_matches_at_depth_is_not_reported(self):
        # Syncthing writes `.stversions` inside each shared folder, never at the export root, and
        # a word without a slash is a name at any depth.
        root = self.export("sync", files=["photos/.stversions/IMG_0001.jpg"])
        out = self.run_job([("syncthing", root, ".stversions")])
        self.assertEqual(self.unused_lines(out), [])

    def test_a_word_that_matches_nothing_is_named_and_the_backup_still_runs(self):
        root = self.export("qb", files=["logs/qbittorrent.log", "BT_backup/x.torrent"])
        out = self.run_job([("qbittorrent-config", root, "logs geoip .npm")])
        self.assertEqual(
            self.unused_lines(out),
            ["exclude-unused path=qbittorrent-config pattern=geoip",
             "exclude-unused path=qbittorrent-config pattern=.npm"])
        called = self.calls.read_text()
        # The export was still handed to restic, with every word, unused or not: the check is a
        # warning and must not change what the night backs up.
        self.assertIn("--tag\npath=qbittorrent-config", called)
        self.assertIn("--exclude\ngeoip", called)

    def test_a_word_with_a_slash_is_anchored_at_the_export_root(self):
        # `log/` means the export's own log directory, not every directory called log.
        at_root = self.export("fresh", files=["log/app.log"])
        nested = self.export("fresh-nested", files=["data/log/app.log"])
        out = self.run_job([("root-log", at_root, "log/"), ("nested-log", nested, "log/")])
        self.assertEqual(
            self.unused_lines(out),
            ["exclude-unused path=nested-log pattern=log/"])

    def test_every_word_still_reaches_restic_once_in_order(self):
        root = self.export("qb", files=["logs/a.log"])
        self.run_job([("qb", root, "logs geoip")])
        args = self.calls.read_text().splitlines()
        passed = [args[i + 1] for i, arg in enumerate(args) if arg == "--exclude"]
        self.assertEqual(passed, ["logs", "geoip"])

    def test_an_export_with_no_exclude_words_is_untouched(self):
        root = self.export("koillection", files=["uploads/a.txt"])
        out = self.run_job([("koillection", root, "")])
        self.assertEqual(self.unused_lines(out), [])
        self.assertNotIn("--exclude", self.calls.read_text())


if __name__ == "__main__":
    unittest.main()
