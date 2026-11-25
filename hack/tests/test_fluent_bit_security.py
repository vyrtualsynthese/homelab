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
# Every key the store must not gain. Each one is checked in both directions:
# present in the tag-scoped filter, absent from every other filter.
REMOVED = ("RequestPath", "request_User-Agent", "RequestReferer")


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
        # The plugin too, not just the tag: a block that matches the tag but
        # removes nothing would otherwise pass.
        self.assertIn("Name modify", scoped[0])
        for key in REMOVED:
            self.assertIn(f"Remove {key}", scoped[0])
        # And the container pipeline must not have picked up any of the rules.
        for other in block:
            if other.strip() and f"Match {TAG}" not in other:
                for key in REMOVED:
                    self.assertNotIn(f"Remove {key}", other)

    def test_the_labels_keep_every_existing_query_resolving(self):
        outputs = config()["outputs"]
        block = [b for b in outputs.split("[OUTPUT]") if f"Match {TAG}" in b]
        self.assertEqual(len(block), 1)
        for label in ("job=fluent-bit", "namespace=kube-system", "container=traefik", "node=streaming"):
            self.assertIn(label, block[0])
        # The one label the file's stream has and the container's does not.
        self.assertIn("source=accesslog", block[0])

    def test_the_tail_input_declares_no_time_parser(self):
        # Traefik writes RFC3339 with nanoseconds and an offset; a `Time_Format`
        # that does not match it falls back silently, so the input must name
        # neither. Checked on the block, not the string: the comment above the
        # block discusses both options by name.
        block = [b for b in config()["inputs"].split("[INPUT]") if f"Tag {TAG}" in b]
        self.assertEqual(len(block), 1)
        self.assertNotIn("Time_Key", block[0])
        self.assertNotIn("Time_Format", block[0])


if __name__ == "__main__":
    unittest.main()
