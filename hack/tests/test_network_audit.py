"""The network collector's contract: read-only, redacted, testable without a device."""

import os
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ansible" / "collectors" / "network-audit.sh"


def run(args=(), env=None, stdin=None):
    merged = {k: v for k, v in os.environ.items() if not k.startswith("NET_AUDIT_")}
    merged.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin, text=True, capture_output=True, env=merged,
    )


class UsageTests(unittest.TestCase):
    def test_the_script_parses(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)

    def test_help_names_the_read_only_contract(self):
        done = run(["--help"])
        self.assertEqual(done.returncode, 0)
        self.assertIn("read-only", done.stdout)
        self.assertIn("NET_AUDIT_FROM_FIXTURE", done.stdout)

    def test_list_targets_prints_the_defaults(self):
        done = run(["--list-targets"])
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.split(), ["crs326", "192.168.1.11", "crs309", "192.168.1.43"])

    def test_list_targets_honours_the_override(self):
        done = run(["--list-targets"], env={"NET_AUDIT_TARGETS": "only=203.0.113.9"})
        self.assertEqual(done.stdout.split(), ["only", "203.0.113.9"])

    def test_an_unknown_flag_is_a_usage_error(self):
        self.assertEqual(run(["--nope"]).returncode, 2)
