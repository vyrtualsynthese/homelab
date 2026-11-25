"""The access-log pipeline, and the key removal that keeps the store honest.

Fluent Bit's config is a string, so a values edit is untyped and a silent
regression. This test reads the three blocks and checks the two properties that
matter: the removal is scoped to the new tag, and the labels keep every existing
panel query resolving.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUES = ROOT / "cluster/helm/monitoring/fluent-bit/values.yaml"
TAG = "traefik.access"


def config():
    return yaml.safe_load(VALUES.read_text())["fluent-bit"]["config"]


class AccessLogPipelineTests(unittest.TestCase):
    def test_a_second_input_tails_the_host_file(self):
        inputs = config()["inputs"]
        self.assertIn("Path /var/log/traefik/access.log", inputs)
        self.assertIn(f"Tag {TAG}", inputs)

    def test_the_path_and_the_user_agent_never_reach_loki(self):
        filters = config()["filters"]
        block = filters.split("[FILTER]")
        scoped = [b for b in block if f"Match {TAG}" in b]
        self.assertEqual(len(scoped), 1, "the removal must be its own, tag-scoped filter")
        self.assertIn("Remove RequestPath", scoped[0])
        self.assertIn("Remove request_User-Agent", scoped[0])
        # And the container pipeline must not have picked up the rule.
        for other in block:
            if other.strip() and f"Match {TAG}" not in other:
                self.assertNotIn("Remove RequestPath", other)

    def test_the_labels_keep_every_existing_query_resolving(self):
        outputs = config()["outputs"]
        block = [b for b in outputs.split("[OUTPUT]") if f"Match {TAG}" in b]
        self.assertEqual(len(block), 1)
        for label in ("job=fluent-bit", "namespace=kube-system", "container=traefik", "node=streaming"):
            self.assertIn(label, block[0])


if __name__ == "__main__":
    unittest.main()
