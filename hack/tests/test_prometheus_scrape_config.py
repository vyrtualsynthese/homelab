"""The scrape drops that keep the head inside its budget.

`hack/tests` renders charts, but the prometheus wrapper's drop rules live in its values and
are easy to lose: a typo, a reordered entry or a deleted block is invisible to the cluster
until the head grows past the process. The two service-endpoint jobs are the pair that
matters here, because a Service carrying both scrape annotations is found by both.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUES = ROOT / "cluster/helm/monitoring/prometheus/values.yaml"

JOBS = ("kubernetes-service-endpoints", "kubernetes-service-endpoints-slow")


class ArgocdDropTests(unittest.TestCase):
    def setUp(self):
        values = yaml.safe_load(VALUES.read_text())
        self.configs = values["prometheus"]["scrapeConfigs"]

    def argocd_entries(self, job):
        return [
            rule
            for rule in self.configs[job]["metric_relabel_configs"]
            if rule.get("action") == "drop"
            and rule.get("source_labels") == ["namespace", "__name__"]
            and "argocd;" in (rule.get("regex") or "")
        ]

    def test_both_service_endpoint_jobs_drop_the_argocd_runtime_families(self):
        for job in JOBS:
            with self.subTest(job=job):
                entries = self.argocd_entries(job)
                self.assertEqual(len(entries), 1)
                regex = entries[0]["regex"]
                self.assertTrue(regex.startswith("argocd;"))
                for family in ("go_.*", "process_.*", "grpc_server_.*", "workqueue_.*"):
                    self.assertIn(family, regex)


if __name__ == "__main__":
    unittest.main()
