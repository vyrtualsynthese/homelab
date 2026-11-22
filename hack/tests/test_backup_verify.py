"""Tests for cluster/helm/backup/files/verify.py.

The verifier is the only thing that notices a stream which has quietly stopped
advancing, so its cases are the ways that happens: a repository with no snapshot,
a snapshot too old to be last night's, a repository that grew past its budget, and
the etcd copy that lags behind the backups it ships with.
"""

import contextlib
import io
import json
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


def snaps(*hours, stream="nfs"):
    return [
        {"id": str(i), "time": (NOW - timedelta(hours=h)).isoformat(),
         "tags": [f"stream={stream}"]}
        for i, h in enumerate(hours)
    ]


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
        line = load().stream_line("nodes-nas", snaps(3, stream="nodes-nas"), 123456, NOW)
        self.assertEqual(
            line,
            "stream=nodes-nas snapshots=1 bytes=123456 "
            f"newest={(NOW - timedelta(hours=3)):%Y-%m-%dT%H:%M:%SZ} age_h=3.0",
        )

    def test_a_newer_untagged_snapshot_does_not_young_the_stream(self):
        # nodes-streaming holds an etcd copy taken by hand (etcd-pre131-nas, 2026-11-22 00:31)
        # beside the nightly snapshot. Counting it made the stream look fresh and inflated the
        # snapshot count the dashboard reads.
        untagged = {"id": "hand", "time": (NOW - timedelta(minutes=30)).isoformat(),
                    "tags": ["etcd-pre131-nas"]}
        line = load().stream_line("nodes-streaming", snaps(5, stream="nodes-streaming") + [untagged],
                                  1, NOW)
        self.assertIn("snapshots=1", line)
        self.assertIn("age_h=5.0", line)
        self.assertEqual(
            load().check_stream("nodes-streaming", snaps(30, stream="nodes-streaming") + [untagged],
                                0, 1000, NOW),
            ["newest snapshot 30.0h old (>26h)"],
        )

    def test_a_repository_with_only_untagged_snapshots_is_an_empty_stream(self):
        untagged = [{"id": "hand", "time": NOW.isoformat(), "tags": ["etcd-pre131-nas"]}]
        self.assertEqual(
            load().stream_line("nfs", untagged, 1, NOW),
            "stream=nfs snapshots=0 bytes=1 newest=none age_h=-",
        )
        self.assertEqual(load().check_stream("nfs", untagged, 0, 1000, NOW), ["no snapshot"])

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


class StagedTests(unittest.TestCase):
    """The other half of #416: a row tolerated on its first night must not stay empty forever."""

    def entry(self, path, kind="file"):
        return {"path": path, "type": kind, "mtime": NOW.isoformat()}

    def rows(self, *labels):
        return [{"namespace": ns, "claim": claim} for ns, claim in (label.split("/") for label in labels)]

    def test_a_staged_database_is_listed_under_its_own_row(self):
        entries = [
            self.entry("/staging/claims/databases/pgadmin-config/sqlite.db"),
            self.entry("/staging/claims/databases/pgadmin-config", "dir"),
        ]
        self.assertEqual(
            load().staged_databases(entries, "databases", "pgadmin-config"),
            ["/staging/claims/databases/pgadmin-config/sqlite.db"],
        )

    def test_another_rows_staged_file_does_not_count(self):
        entries = [self.entry("/staging/claims/databases/n8n-config/database.sqlite")]
        self.assertEqual(load().staged_databases(entries, "databases", "pgadmin-config"), [])

    def test_an_empty_staging_directory_is_a_problem_that_names_the_row(self):
        entries = [self.entry("/staging/claims/databases/pgadmin-config", "dir")]
        lines, problems = load().check_staged(
            "nodes-nuc0", self.rows("databases/pgadmin-config"), entries)
        self.assertEqual(lines, ["staged stream=nodes-nuc0 row=databases/pgadmin-config files=0"])
        self.assertEqual(problems, ["row databases/pgadmin-config staged no database"])

    def test_a_row_the_snapshot_does_not_carry_at_all_is_a_problem_too(self):
        lines, problems = load().check_staged("nodes-nuc0", self.rows("databases/n8n-config"), [])
        self.assertEqual(lines, ["staged stream=nodes-nuc0 row=databases/n8n-config files=0"])
        self.assertEqual(problems, ["row databases/n8n-config staged no database"])

    def test_every_row_is_reported_and_only_the_empty_ones_fail(self):
        entries = [self.entry("/staging/claims/databases/n8n-config/database.sqlite")]
        lines, problems = load().check_staged(
            "nodes-nuc0", self.rows("databases/pgadmin-config", "databases/n8n-config"), entries)
        self.assertEqual(len(lines), 2)
        self.assertIn("row=databases/n8n-config files=1", lines[1])
        self.assertEqual(problems, ["row databases/pgadmin-config staged no database"])


class StreamSnapshotTests(unittest.TestCase):
    def test_a_copy_made_outside_the_nightly_job_does_not_stand_in_for_the_stream(self):
        # `restic ls latest` picks this one: a later etcd copy, tagged for what it was,
        # holding none of the stream's claims. The file list must come from the stream.
        snapshots = [
            {"id": "night", "time": (NOW - timedelta(hours=20)).isoformat(),
             "tags": ["stream=nodes-streaming", "node=streaming"]},
            {"id": "adhoc", "time": (NOW - timedelta(hours=1)).isoformat(),
             "tags": ["etcd-pre131-nas"]},
        ]
        self.assertEqual(load().stream_snapshot(snapshots, "nodes-streaming")["id"], "night")

    def test_the_newest_nightly_snapshot_wins(self):
        snapshots = [
            {"id": "old", "time": (NOW - timedelta(hours=44)).isoformat(), "tags": ["stream=nodes-nuc0"]},
            {"id": "new", "time": (NOW - timedelta(hours=20)).isoformat(), "tags": ["stream=nodes-nuc0"]},
        ]
        self.assertEqual(load().stream_snapshot(snapshots, "nodes-nuc0")["id"], "new")

    def test_a_repository_with_no_tagged_snapshot_yields_none(self):
        snapshots = [{"id": "x", "time": NOW.isoformat(), "tags": None}]
        self.assertIsNone(load().stream_snapshot(snapshots, "nodes-nas"))


class MainTests(unittest.TestCase):
    """The wiring, against a stand-in restic: argument order, exit code and output shape."""

    def write_restic(self, tmp: pathlib.Path, snapshot_iso: str, ls_mtime: str,
                     tag: str) -> pathlib.Path:
        import json

        snapshot = json.dumps([{"id": "a", "time": snapshot_iso, "tags": [f"stream={tag}"]}])
        fake = tmp / "restic"
        fake.write_text(
            "#!/bin/sh\n"
            'case "$3" in\n'
            f"  snapshots) echo '{snapshot}' ;;\n"
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
        fake = self.write_restic(tmp, snapshot_iso, ls_mtime, streams[0]["name"])
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

    def test_an_etcd_stream_with_no_snapshot_names_the_stream_and_does_not_abort(self):
        # `restic ls latest` exits 1 with "no snapshot found" on an empty repository: the
        # verifier must report the empty stream, not die on the etcd query.
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            fake = tmp / "restic"
            fake.write_text(
                "#!/bin/sh\n"
                'case "$3" in\n'
                "  snapshots) echo '[]' ;;\n"
                "  stats) echo '{\"total_size\": 0}' ;;\n"
                "  ls) echo 'no snapshot found' >&2; exit 1 ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n"
            )
            fake.chmod(0o755)
            config = tmp / "streams.json"
            config.write_text(
                '[{"name": "nodes-nuc0", "repository": "s3:x/nodes-nuc0",'
                ' "budgetGiB": 1, "etcd": true}]'
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = load().main(["--config", str(config), "--restic", str(fake)])
        self.assertEqual(code, 1)
        self.assertIn("problem stream=nodes-nuc0 no snapshot", out.getvalue())
        self.assertIn("etcd stream=nodes-nuc0 file=-", out.getvalue())

    def test_a_sqlite_row_that_staged_nothing_fails_the_run_and_names_the_row(self):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            fake = tmp / "restic"
            fake.write_text(
                "#!/bin/sh\n"
                'case "$3" in\n'
                f"  snapshots) echo '[{{\"id\": \"a\", \"time\": \"{stamp}\", \"tags\": [\"stream=nodes-nuc0\"]}}]' ;;\n"
                "  stats) echo '{\"total_size\": 4096}' ;;\n"
                f"  ls) echo '{{\"path\": \"/staging/claims/databases/pgadmin-config\", \"type\": \"dir\", \"mtime\": \"{stamp}\"}}' ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n"
            )
            fake.chmod(0o755)
            config = tmp / "streams.json"
            config.write_text(json.dumps([{
                "name": "nodes-nuc0", "repository": "s3:x/nodes-nuc0", "budgetGiB": 1,
                "staged": [{"namespace": "databases", "claim": "pgadmin-config"}],
            }]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = load().main(["--config", str(config), "--restic", str(fake)])
        self.assertEqual(code, 1)
        self.assertIn("staged stream=nodes-nuc0 row=databases/pgadmin-config files=0", out.getvalue())
        self.assertIn("problem stream=nodes-nuc0 row databases/pgadmin-config staged no database",
                      out.getvalue())

    def test_a_staged_row_that_carries_its_database_passes(self):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            fake = tmp / "restic"
            fake.write_text(
                "#!/bin/sh\n"
                'case "$3" in\n'
                f"  snapshots) echo '[{{\"id\": \"a\", \"time\": \"{stamp}\", \"tags\": [\"stream=nodes-nuc0\"]}}]' ;;\n"
                "  stats) echo '{\"total_size\": 4096}' ;;\n"
                f"  ls) echo '{{\"path\": \"/staging/claims/databases/pgadmin-config/sqlite.db\", \"type\": \"file\", \"mtime\": \"{stamp}\"}}' ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n"
            )
            fake.chmod(0o755)
            config = tmp / "streams.json"
            config.write_text(json.dumps([{
                "name": "nodes-nuc0", "repository": "s3:x/nodes-nuc0", "budgetGiB": 1,
                "staged": [{"namespace": "databases", "claim": "pgadmin-config"}],
            }]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = load().main(["--config", str(config), "--restic", str(fake)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("staged stream=nodes-nuc0 row=databases/pgadmin-config files=1", out.getvalue())

    def test_the_file_list_comes_from_the_streams_snapshot_not_the_newest_anything(self):
        # The fake prints the staged database only when restic is asked for the nightly
        # snapshot's id, so reading `latest` would report the row as empty. The times are
        # relative to now so the nightly stays inside the 26-hour window whenever it runs.
        now = datetime.now(timezone.utc)
        night = (now - timedelta(hours=20)).isoformat()
        adhoc = (now - timedelta(hours=1)).isoformat()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            fake = tmp / "restic"
            fake.write_text(
                "#!/bin/sh\n"
                'case "$3" in\n'
                "  snapshots) echo '["
                f'{{"id": "night", "time": "{night}", "tags": ["stream=nodes-nuc0"]}},'
                f'{{"id": "adhoc", "time": "{adhoc}", "tags": ["etcd-pre131-nas"]}}'
                "]' ;;\n"
                "  stats) echo '{\"total_size\": 4096}' ;;\n"
                '  ls) if [ "$5" = "night" ]; then\n'
                '        echo \'{"path": "/staging/claims/databases/pgadmin-config/sqlite.db", "type": "file"}\' ;\n'
                "      else\n"
                f'        echo \'{{"path": "/host/etcd-snapshots/nuc0-1", "type": "file", "mtime": "{adhoc}"}}\' ;\n'
                "      fi ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n"
            )
            fake.chmod(0o755)
            config = tmp / "streams.json"
            config.write_text(json.dumps([{
                "name": "nodes-nuc0", "repository": "s3:x/nodes-nuc0", "budgetGiB": 1,
                "staged": [{"namespace": "databases", "claim": "pgadmin-config"}],
            }]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = load().main(["--config", str(config), "--restic", str(fake)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("staged stream=nodes-nuc0 row=databases/pgadmin-config files=1", out.getvalue())

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
