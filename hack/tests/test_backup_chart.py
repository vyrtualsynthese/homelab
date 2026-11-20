"""Tests for the backup chart's rendered shapes.

Every assertion here is about something the cluster will do later: which node a job is
pinned to, which directories it may write, and whether restic is told to resume from the
previous night's snapshot. The chart is rendered with `helm template` rather than parsed
as YAML so that a template error fails the suite.
"""

import json
import pathlib
import subprocess
import unittest

import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "cluster/helm/backup"


def render():
    out = subprocess.run(
        ["helm", "template", "backup", str(CHART), "-f", str(CHART / "values.yaml"), "--namespace", "backup"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def crons():
    return [d for d in render() if d["kind"] == "CronJob"]


def pod_of(name):
    return next(c for c in crons() if c["metadata"]["name"] == name)["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def values():
    return yaml.safe_load((CHART / "values.yaml").read_text())


def scripts():
    return {d["metadata"]["name"]: d["data"] for d in render() if d["kind"] == "ConfigMap"}["backup-scripts"]


class NodeJobTests(unittest.TestCase):
    def test_one_job_per_node_with_its_stream_label(self):
        labels = {c["metadata"]["name"]: c["metadata"]["labels"]["homelab.io/backup-stream"] for c in crons()}
        self.assertEqual(labels["backup-node-nas"], "nodes-nas")
        self.assertEqual(labels["backup-node-nuc0"], "nodes-nuc0")
        self.assertEqual(labels["backup-node-streaming"], "nodes-streaming")

    def test_node_jobs_are_forbid_and_pinned(self):
        node_jobs = [c for c in crons() if c["metadata"]["name"].startswith("backup-node-")]
        self.assertEqual(len(node_jobs), 3)
        for c in node_jobs:
            self.assertEqual(c["spec"]["concurrencyPolicy"], "Forbid")
            node = c["metadata"]["name"].removeprefix("backup-node-")
            self.assertEqual(c["spec"]["jobTemplate"]["spec"]["template"]["spec"]["nodeSelector"],
                             {"kubernetes.io/hostname": node})

    def test_every_row_reaches_its_rows_configmap(self):
        cms = {d["metadata"]["name"]: json.loads(d["data"]["rows.json"]) for d in render()
               if d["kind"] == "ConfigMap" and d["metadata"]["name"].startswith("backup-rows-")}
        for node, cfg in values()["nodes"].items():
            self.assertEqual(len(cms[f"backup-rows-{node}"]["claims"]), len(cfg["claims"]))

    def test_rows_are_unique(self):
        rows = [(r["namespace"], r["claim"]) for cfg in values()["nodes"].values() for r in cfg["claims"]]
        self.assertEqual(len(rows), len(set(rows)))

    def test_no_job_initialises_a_repository(self):
        self.assertNotIn("restic init", json.dumps(render()))

    def test_every_restic_call_retries_the_lock(self):
        self.assertIn("--retry-lock 2h", scripts()["backup-node.sh"])

    def test_snapshots_are_grouped_by_node_not_by_pod(self):
        # Without an explicit host, restic names each snapshot after the pod, never finds a
        # parent, and rescans every gigabyte every night.
        self.assertIn('--host "$NODE"', scripts()["backup-node.sh"])

    def test_each_root_reaches_the_prepare_container(self):
        for node, cfg in values()["nodes"].items():
            args = next(c for c in pod_of(f"backup-node-{node}")["initContainers"] if c["name"] == "prepare")["args"]
            self.assertEqual(args.count("--root"), len(cfg["roots"]))

    def test_reads_are_mounted_read_only(self):
        writable = ("/staging", "/scripts", "/config")
        for c in crons():
            pod = c["spec"]["jobTemplate"]["spec"]["template"]["spec"]
            for container in pod["initContainers"] + pod["containers"]:
                for mount in container["volumeMounts"]:
                    if mount["mountPath"] in writable:
                        continue
                    self.assertTrue(mount.get("readOnly"),
                                    f"{c['metadata']['name']} {container['name']} {mount['mountPath']}")

    def test_etcd_bridge_only_on_control_plane_nodes(self):
        snap = "/var/lib/rancher/k3s/server/db/snapshots"

        def mounts(name):
            return {v["hostPath"]["path"] for v in pod_of(name)["volumes"] if "hostPath" in v}

        def args(name):
            return next(c for c in pod_of(name)["initContainers"] if c["name"] == "prepare")["args"]

        self.assertIn(snap, mounts("backup-node-streaming"))
        self.assertIn("/host/etcd-snapshots", args("backup-node-streaming"))
        self.assertIn(snap, mounts("backup-node-nuc0"))
        self.assertNotIn(snap, mounts("backup-node-nas"))
        self.assertNotIn("/host/etcd-snapshots", args("backup-node-nas"))


if __name__ == "__main__":
    unittest.main()
