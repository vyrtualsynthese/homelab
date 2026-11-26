"""The seed script's TLS contract: the cluster's CA by default, never -k.

The operator API token rides every request, so the transport has to prove it is
talking to the cluster: `-k` accepts whatever answers for `vikunja.kub.local`.
The script verifies the chain against `local-ca.pem` and stops when that file is
missing rather than falling back to trusting anything.
"""

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "hack" / "vikunja-seed.sh"
DEFAULT_CA = pathlib.Path(".config/secrets/kub-local/local-ca.pem")

# Answers every read with an empty board and records the argv of each call. An
# empty `/projects` keeps the run to reads: the dry run plans the writes.
FAKE_CURL = """#!/usr/bin/env bash
set -eu
out=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-o" ]; then out="$arg"; fi
  prev="$arg"
done
printf '%s\\n' "$*" >>"$FAKE_CURL_LOG"
: >"$out"
printf '[]' >"$out"
printf '200'
"""


class Seed:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.home = self.root / "home"
        self.home.mkdir()
        self.log = self.root / "curl.log"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        curl = self.bin / "curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)

    def ca(self, home=True):
        path = (self.home if home else self.root) / DEFAULT_CA
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-----BEGIN CERTIFICATE-----\n")
        return path

    def run(self, *args, env=None):
        merged = {k: v for k, v in os.environ.items() if not k.startswith("VIKUNJA_")}
        merged.update({
            "HOME": str(self.home),
            "PATH": str(self.bin) + os.pathsep + merged["PATH"],
            "FAKE_CURL_LOG": str(self.log),
            "VIKUNJA_URL": "https://vikunja.kub.local",
            "VIKUNJA_API_TOKEN": "not-a-real-token",
        })
        merged.update(env or {})
        done = subprocess.run(["bash", str(SCRIPT), *args], text=True,
                              capture_output=True, env=merged)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return done, calls


class DefaultCacertTests(unittest.TestCase):
    def test_every_call_verifies_against_the_cluster_ca(self):
        with tempfile.TemporaryDirectory() as root:
            seed = Seed(root)
            ca = seed.ca()
            done, calls = seed.run("--repo", "homelab", "--dry-run")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertGreater(len(calls), 0, "no request was sent, so nothing was checked")
            for call in calls:
                self.assertIn(f"--cacert {ca}", call)
                self.assertNotIn(" -k ", f" {call} ")

    def test_an_explicit_ca_wins_over_the_default(self):
        with tempfile.TemporaryDirectory() as root:
            seed = Seed(root)
            seed.ca()
            other = pathlib.Path(root) / "other-ca.pem"
            other.write_text("-----BEGIN CERTIFICATE-----\n")
            done, calls = seed.run("--repo", "homelab", "--dry-run",
                                   env={"VIKUNJA_CACERT": str(other)})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertGreater(len(calls), 0)
            for call in calls:
                self.assertIn(f"--cacert {other}", call)
                self.assertNotIn(" -k ", f" {call} ")

    def test_a_missing_ca_stops_the_run_without_sending_anything(self):
        with tempfile.TemporaryDirectory() as root:
            seed = Seed(root)
            expected = seed.home / DEFAULT_CA
            done, calls = seed.run("--repo", "homelab", "--dry-run")
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn(str(expected), done.stderr)
            self.assertEqual(calls, [], "a refused run must send no request")


if __name__ == "__main__":
    unittest.main()
