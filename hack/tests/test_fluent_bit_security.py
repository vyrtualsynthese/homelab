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


def _blocks(text, kind, selectors):
    """The [INPUT]/[OUTPUT] block holding each selector, as a list of blocks."""
    found = []
    for selector in selectors:
        block = [b for b in text.split(f"[{kind}]") if selector in b]
        if len(block) != 1:
            raise AssertionError(f"{selector}: {len(block)} blocks")
        found.append(block[0])
    return found


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


class JournalTests(unittest.TestCase):
    def test_two_inputs_select_ssh_and_kernel(self):
        inputs = config()["inputs"]
        self.assertEqual(inputs.count("Name systemd"), 2, "one input per selection")
        self.assertIn("Tag host.ssh", inputs)
        self.assertIn("Tag host.kernel", inputs)
        # Not `_SYSTEMD_UNIT=ssh.service`: Ubuntu 22.10 and later socket-activate
        # sshd, so a connection is a transient `ssh@<n>-<addr>.service` and a
        # filter naming `ssh.service` ships nothing. `_COMM` matches both shapes.
        self.assertIn("Systemd_Filter _COMM=sshd", inputs)
        # On the directives, not the whole string: the comment above the block
        # names the wrong unit to explain why it is not used.
        directives = "\n".join(
            line for line in inputs.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("_SYSTEMD_UNIT=ssh.service", directives)
        self.assertIn("Systemd_Filter _TRANSPORT=kernel", inputs)

    def test_each_input_resumes_rather_than_replaying(self):
        # `DB` lives on an emptyDir, so the position dies with the pod; without
        # Read_From_Tail a restart ships the whole persistent journal again.
        inputs = config()["inputs"]
        self.assertEqual(inputs.count("Read_From_Tail On"), 2)

    def test_each_output_carries_a_static_unit_label(self):
        outputs = config()["outputs"]
        for tag, unit in (("host.ssh", "ssh"), ("host.kernel", "kernel")):
            block = [b for b in outputs.split("[OUTPUT]") if f"Match {tag}" in b]
            self.assertEqual(len(block), 1, tag)
            self.assertIn(f"unit={unit}", block[0])
            self.assertIn("source=journal", block[0])

    def test_neither_journal_input_pins_a_directory(self):
        # `Path` switches in_systemd from sd_journal_open(LOCAL_ONLY) — both the
        # persistent and the volatile journal — to opening one directory. A node
        # whose journal is volatile would then ship nothing while looking
        # configured, which is the same silent zero the `_COMM` choice exists to
        # prevent.
        for block in _blocks(config()["inputs"], "INPUT", ("Tag host.ssh", "Tag host.kernel")):
            self.assertNotIn("Path ", block)

    def test_each_output_keeps_the_pipeline_options(self):
        # Copied from the access-log output, which the measured day above proves.
        # Losing one of these is a silent change to what the store holds.
        for tag in ("host.ssh", "host.kernel"):
            block = _blocks(config()["outputs"], "OUTPUT", (f"Match {tag}",))[0]
            for option in ("Host loki.monitoring.svc.cluster.local", "Port 3100",
                           "Line_Format json", "Compress gzip", "Retry_Limit no_limits"):
                self.assertIn(option, block, tag)

    def test_the_chart_mounts_the_runtime_journal_and_a_place_for_the_positions(self):
        # The chart mounts `/var/log` but not `/run`, so the volatile journal is
        # invisible without the second mount; and `DB` needs a writable directory,
        # which the upstream chart does not provide.
        chart = yaml.safe_load(VALUES.read_text())["fluent-bit"]
        mounts = {m["mountPath"]: m for m in chart["extraVolumeMounts"]}
        self.assertTrue(mounts["/run/log/journal"].get("readOnly"))
        self.assertIn("/var/lib/fluent-bit", mounts)
        volumes = {v["name"]: v for v in chart["extraVolumes"]}
        self.assertEqual(volumes["host-run-journal"]["hostPath"]["path"], "/run/log/journal")
        self.assertEqual(volumes["host-run-journal"]["hostPath"]["type"], "DirectoryOrCreate")
        self.assertIn("emptyDir", volumes["fb-journal-db"])

if __name__ == "__main__":
    unittest.main()
