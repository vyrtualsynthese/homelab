"""The security exporter's values, and the one tie that keeps them honest.

`hack/check_exposure.py` owns the definition of a public host. The chart needs
the same list to count what answers the internet, and a copy that drifts is a
posture panel that says the door is shut while a route answers the world. This
test reads both and fails on a difference.
"""

import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hack"))

import check_exposure  # noqa: E402

VALUES = ROOT / "cluster/helm/security/security-exporter/values.yaml"


class ReconciledConstantsTests(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load(VALUES.read_text())["securityExporter"]

    def test_the_public_host_list_is_the_checker_s_own(self):
        self.assertEqual(set(self.values["publicHosts"]), set(check_exposure.PUBLIC_HOSTS))

    def test_the_expected_counts_are_present_and_explained(self):
        expected = self.values["expected"]
        self.assertEqual(expected["ingressRoutes"], 73)
        self.assertEqual(expected["publicHosts"], 9)
        self.assertEqual(expected["privilegedWorkloads"], 4)
        # Every number is a claim about the cluster today; the note says when it
        # was read, so a stale constant is visible rather than plausible.
        self.assertIn("2026-11-25", self.values["expectedNote"])


class WhitelistTests(unittest.TestCase):
    def test_the_exporter_carries_no_network_range_at_all(self):
        # The exporter counts; it never decides. A cidr in its values would be a
        # second place a policy could live, and the first place to drift.
        text = VALUES.read_text()
        self.assertNotIn("cidr", text)
        self.assertNotIn("192.168", text)
