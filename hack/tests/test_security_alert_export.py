"""The exported security rules, checked where a rule changed meaning.

`SecurityBanBurst` used to read the size of the kernel's ban set. That set is a
gauge, so the first CAPI blocklist pull raises it by thousands in one step and
the rule fires — on the one alert meant to say "something is happening here".
It now counts the decisions this installation created, which is what a burst is.
"""

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPORT = ROOT / "cluster/helm/monitoring/grafana/export/security/rules.json"

LOCAL_BURST = 'delta(sum(cs_active_decisions{origin="crowdsec"})[10m:1m])'


class SecurityAlertExportTests(unittest.TestCase):
    def setUp(self):
        self.rules = {rule["title"]: rule for rule in json.loads(EXPORT.read_text())}

    def test_the_burst_rule_counts_this_installation_and_not_the_blocklist(self):
        rule = self.rules["SecurityBanBurst"]
        self.assertEqual(rule["data"][0]["model"]["expr"], LOCAL_BURST)
        # The kernel set is what a blocklist pull moves. Reading it here is the
        # defect the expression change removes, so the whole rule is checked.
        self.assertNotIn("fw_bouncer_banned_ips", json.dumps(rule))

    def test_the_burst_threshold_is_still_twenty_in_ten_minutes(self):
        condition = self.rules["SecurityBanBurst"]["data"][1]["model"]["conditions"][0]
        self.assertEqual(condition["evaluator"]["params"], [20])
