"""Tests for cluster/helm/backup/files/verify.py.

The verifier is the only thing that notices a stream which has quietly stopped
advancing, so its cases are the ways that happens: a repository with no snapshot,
a snapshot too old to be last night's, a repository that grew past its budget, and
the etcd copy that lags behind the backups it ships with.
"""

import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from importlib import util

CHART = pathlib.Path(__file__).resolve().parents[2] / "cluster/helm/backup"
VERIFY = CHART / "files/verify.py"
NOW = datetime(2026, 11, 21, 8, 30, tzinfo=timezone.utc)


def load():
    if not hasattr(load, "module"):
        spec = util.spec_from_file_location("verify", VERIFY)
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)
        load.module = module
    return load.module


def snaps(*hours):
    return [{"id": str(i), "time": (NOW - timedelta(hours=h)).isoformat()} for i, h in enumerate(hours)]


class StreamTests(unittest.TestCase):
    def test_a_repository_with_no_snapshot_is_broken(self):
        self.assertEqual(load().check_stream("nfs", [], 0, 1000, NOW), ["no snapshot"])

    def test_twenty_five_hours_is_fine_and_twenty_seven_is_not(self):
        self.assertEqual(load().check_stream("nfs", snaps(25), 10, 1000, NOW), [])
        problems = load().check_stream("nfs", snaps(27), 10, 1000, NOW)
        self.assertEqual(len(problems), 1)
        self.assertIn("27.0h", problems[0])

    def test_over_budget_is_a_problem_and_at_budget_is_not(self):
        self.assertEqual(load().check_stream("nfs", snaps(1), 2**30, 2**30, NOW), [])
        self.assertIn("over budget",
                      load().check_stream("nfs", snaps(1), 2**30 + 1, 2**30, NOW)[0])

    def test_the_line_carries_every_field_the_dashboard_reads(self):
        line = load().stream_line("nodes-nas", snaps(3), 123456, NOW)
        self.assertEqual(
            line,
            "stream=nodes-nas snapshots=1 bytes=123456 "
            f"newest={(NOW - timedelta(hours=3)):%Y-%m-%dT%H:%M:%SZ} age_h=3.0",
        )

    def test_nanosecond_timestamps_from_restic_parse(self):
        parsed = load().parse_time("2026-11-20T05:03:00.123456789+01:00")
        self.assertEqual(parsed, datetime(2026, 11, 20, 4, 3, 0, 123456, tzinfo=timezone.utc))

    def test_a_snapshot_in_both_timezones_compares_by_instant(self):
        # Restic writes the timestamp with the host's offset. Two snapshots an hour apart
        # must order correctly whatever the offsets say.
        older = load().parse_time("2026-11-21T07:00:00+01:00")
        newer = load().parse_time("2026-11-21T06:30:00+00:00")
        self.assertLess(older, newer)

    def test_the_newest_snapshot_decides_the_age(self):
        self.assertIn("age_h=2.0", load().stream_line("nfs", snaps(9, 2, 5), 1, NOW))


class EtcdTests(unittest.TestCase):
    def test_the_etcd_copy_ages_from_the_newest_file_in_the_snapshots(self):
        entries = [
            {"path": "/x/other", "mtime": NOW.isoformat()},
            {"path": "/host/etcd-snapshots/streaming-1", "mtime": (NOW - timedelta(hours=30)).isoformat()},
            {"path": "/host/etcd-snapshots/streaming-2", "mtime": (NOW - timedelta(hours=2)).isoformat()},
        ]
        self.assertEqual(load().newest_etcd(entries, NOW)["age_h"], 2.0)
        self.assertEqual(load().newest_etcd(entries, NOW)["file"], "streaming-2")

    def test_a_directory_entry_is_not_a_snapshot(self):
        entries = [
            {"path": "/host/etcd-snapshots", "type": "dir", "mtime": NOW.isoformat()},
            {"path": "/host/etcd-snapshots/sub", "type": "dir", "mtime": NOW.isoformat()},
        ]
        self.assertIsNone(load().newest_etcd(entries, NOW))

    def test_the_prefix_is_the_path_the_node_job_hands_restic(self):
        # The chart passes etcdSnapshotsPath as a --host-path argument, so restic stores that
        # path in the snapshot. A verifier looking anywhere else reports "no etcd snapshot"
        # every night while the copies are there.
        import yaml

        values = yaml.safe_load((CHART / "values.yaml").read_text())
        self.assertEqual(load().ETCD_PREFIX, values["etcdSnapshotsPath"] + "/")

    def test_an_etcd_copy_older_than_the_window_is_a_problem(self):
        self.assertIn("etcd", load().check_etcd("nodes-streaming", {"age_h": 27.5, "file": "f"}, NOW)[0])
        self.assertEqual(load().check_etcd("nodes-streaming", {"age_h": 2.0, "file": "f"}, NOW), [])

    def test_a_stream_with_no_etcd_snapshot_at_all_is_a_problem(self):
        self.assertEqual(load().check_etcd("nodes-streaming", None, NOW), ["no etcd snapshot"])


class MainTests(unittest.TestCase):
    """The wiring, against a stand-in restic: argument order, exit code and output shape."""

    def write_restic(self, tmp: pathlib.Path, snapshot_iso: str, ls_mtime: str) -> pathlib.Path:
        fake = tmp / "restic"
        fake.write_text(
            "#!/bin/sh\n"
            'case "$3" in\n'
            f"  snapshots) echo '[{{\"id\": \"a\", \"time\": \"{snapshot_iso}\"}}]' ;;\n"
            "  stats) echo '{\"total_size\": 4096}' ;;\n"
            f"  ls) echo '{{\"path\": \"/host/etcd-snapshots/nuc0-1\", \"type\": \"file\", \"mtime\": \"{ls_mtime}\"}}' ;;\n"
            "  *) exit 9 ;;\n"
            "esac\n"
        )
        fake.chmod(0o755)
        return fake

    def run_main(self, tmp: pathlib.Path, snapshot_iso: str, ls_mtime: str, streams: list[dict]):
        import contextlib
        import io
        import json

        config = tmp / "streams.json"
        config.write_text(json.dumps(streams))
        fake = self.write_restic(tmp, snapshot_iso, ls_mtime)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = load().main(["--config", str(config), "--restic", str(fake)])
        return code, out.getvalue()

    def test_a_healthy_run_prints_the_line_and_exits_zero(self):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(
                pathlib.Path(tmp), stamp, stamp,
                [{"name": "nfs", "repository": "s3:x/nfs", "budgetGiB": 1, "etcd": False}],
            )
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^stream=nfs snapshots=1 bytes=4096 newest=\S+ age_h=0\.0$")

    def test_a_stale_stream_exits_one_and_names_the_problem(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(
                pathlib.Path(tmp), old, old,
                [{"name": "nodes-nuc0", "repository": "s3:x/nodes-nuc0", "budgetGiB": 1, "etcd": True}],
            )
        self.assertEqual(code, 1)
        self.assertIn("problem stream=nodes-nuc0 newest snapshot", out)
        self.assertIn("problem stream=nodes-nuc0 etcd copy", out)

    def test_a_missing_repository_is_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            fake = tmp / "restic"
            fake.write_text("#!/bin/sh\necho 'repository does not exist' >&2\nexit 1\n")
            fake.chmod(0o755)
            config = tmp / "streams.json"
            config.write_text('[{"name": "nfs", "repository": "s3:x/nfs", "budgetGiB": 1}]')
            with self.assertRaisesRegex(RuntimeError, "repository does not exist"):
                load().main(["--config", str(config), "--restic", str(fake)])


if __name__ == "__main__":
    unittest.main()
