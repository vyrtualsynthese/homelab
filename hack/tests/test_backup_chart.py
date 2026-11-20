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

    def test_host_data_is_never_mounted_writable(self):
        # The claims and the node's /etc arrive by hostPath, so the only thing that may be
        # labelled writable is the empty staging volume. A hostPath mounted writable would let
        # a job write into a live claim.
        for c in crons():
            pod = c["spec"]["jobTemplate"]["spec"]["template"]["spec"]
            kinds = {}
            for volume in pod["volumes"]:
                kinds[volume["name"]] = (
                    "hostPath" if "hostPath" in volume
                    else "configMap" if "configMap" in volume
                    else "emptyDir"
                )
            for container in pod.get("initContainers", []) + pod["containers"]:
                for mount in container["volumeMounts"]:
                    if kinds[mount["name"]] == "emptyDir":
                        continue
                    self.assertTrue(
                        mount.get("readOnly"),
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


class NfsJobTests(unittest.TestCase):
    def test_one_backup_per_row(self):
        cms = {d["metadata"]["name"]: d["data"] for d in render() if d["kind"] == "ConfigMap"}
        lines = [l for l in cms["backup-nfs-paths"]["nfs-paths.txt"].splitlines() if l.strip()]
        self.assertEqual(len(lines), len(values()["nfs"]["paths"]))

    def test_the_exclude_words_are_not_shell_expanded(self):
        cms = {d["metadata"]["name"]: d["data"] for d in render() if d["kind"] == "ConfigMap"}
        self.assertIn("set -f", cms["backup-scripts"]["backup-nfs.sh"])

    def test_missing_export_fails_the_run(self):
        cms = {d["metadata"]["name"]: d["data"] for d in render() if d["kind"] == "ConfigMap"}
        self.assertIn('[ -d "$path" ] ||', cms["backup-scripts"]["backup-nfs.sh"])

    def test_exports_are_mounted_read_only(self):
        job = next(c for c in crons() if c["metadata"]["name"] == "backup-nfs")
        nfs = [v for v in job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"] if "nfs" in v]
        self.assertTrue(nfs)
        for v in nfs:
            self.assertTrue(v["nfs"]["readOnly"])

    def test_every_export_is_mounted_from_the_configured_server(self):
        job = next(c for c in crons() if c["metadata"]["name"] == "backup-nfs")
        nfs = [v["nfs"] for v in job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"] if "nfs" in v]
        self.assertEqual({n["server"] for n in nfs}, {values()["nfs"]["server"]})
        self.assertEqual({n["path"] for n in nfs}, {row["path"] for row in values()["nfs"]["paths"]})

    def test_job_runs_on_nas_with_its_stream_label(self):
        job = next(c for c in crons() if c["metadata"]["name"] == "backup-nfs")
        self.assertEqual(job["metadata"]["labels"]["homelab.io/backup-stream"], "nfs")
        self.assertEqual(job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["nodeSelector"],
                         {"kubernetes.io/hostname": "nas"})

    def test_nfs_backup_pins_the_host_too(self):
        self.assertIn('--host nas', scripts()["backup-nfs.sh"])

    def test_koillection_row_names_the_directory_its_pv_mounts(self):
        paths = [row["path"] for row in values()["nfs"]["paths"]]
        self.assertTrue(any(p.startswith("/mnt/storage/nfs/stash/koillection-") for p in paths))

    def test_excluded_exports_are_absent_not_excluded(self):
        # The operator's decisions: music, media, stash's library, downloads and (2026-11-20) the
        # books library stay on `nas`. An out-of-scope export is a row that does not exist, so the
        # job cannot reach it; an `exclude` is for files inside an export that is backed up.
        paths = [row["path"] for row in values()["nfs"]["paths"]]
        for out_of_scope in ("/mnt/storage/nfs/books", "/mnt/storage/nfs/audio",
                             "/mnt/storage/nfs/plexmedia", "/mnt/storage/nfs/stash"):
            self.assertNotIn(out_of_scope, paths)


class PruneTests(unittest.TestCase):
    def test_runs_sunday_at_noon_outside_every_backup(self):
        job = next(c for c in crons() if c["metadata"]["name"] == "backup-prune")
        self.assertEqual(job["spec"]["schedule"], "0 12 * * 0")

    def test_forgets_each_repository_with_the_agreed_retention(self):
        script = scripts()["prune.sh"]
        for flag in ("--keep-daily 7", "--keep-weekly 4", "--keep-monthly 12", "--prune",
                     "--retry-lock 6h"):
            self.assertIn(flag, script)
        for repo in ("nodes-nas", "nodes-nuc0", "nodes-streaming", "nfs"):
            self.assertIn(repo, script)

    def test_retention_comes_from_values(self):
        text = (CHART / "values.yaml").read_text()
        self.assertIn("retention:", text)
        self.assertEqual(values()["retention"],
                         {"keepDaily": 7, "keepWeekly": 4, "keepMonthly": 12})

    def test_prune_finishes_every_repository_before_failing(self):
        # A repository that fails on Sunday must not leave the other three unpruned for the
        # week: the loop remembers the failure and the last test fails the job.
        script = scripts()["prune.sh"]
        self.assertNotIn("set -eu", script)
        self.assertIn("failed=0", script)
        self.assertEqual(script.count("|| failed=1"), len(values()["streams"]))
        self.assertIn('[ "$failed" -eq 0 ]', script)

    def test_prune_runs_one_repository_at_a_time(self):
        # Two restic processes on one repository would fight over the lock; the loop is a
        # sequence, not a fan-out.
        script = scripts()["prune.sh"]
        self.assertEqual(script.count("restic -r"), len(values()["streams"]))


class VerifyTests(unittest.TestCase):
    def test_runs_after_every_backup_and_carries_its_label(self):
        job = next(c for c in crons() if c["metadata"]["name"] == "backup-verify")
        self.assertEqual(job["spec"]["schedule"], "30 8 * * *")
        self.assertEqual(job["metadata"]["labels"]["homelab.io/backup-stream"], "verify")

    def test_every_repository_is_checked(self):
        cms = {d["metadata"]["name"]: d["data"] for d in render() if d["kind"] == "ConfigMap"}
        streams = json.loads(cms["backup-streams"]["streams.json"])
        self.assertEqual({s["name"] for s in streams}, {"nodes-nas", "nodes-nuc0", "nodes-streaming", "nfs"})
        self.assertEqual({s["etcd"] for s in streams if s["name"] == "nodes-streaming"}, {True})
        self.assertEqual({s["etcd"] for s in streams if s["name"] == "nodes-nas"}, {False})

    def test_the_verifier_cannot_initialise_a_repository(self):
        self.assertNotIn("restic init", scripts()["verify.py"])
        self.assertNotIn("forget", scripts()["verify.py"])


if __name__ == "__main__":
    unittest.main()
