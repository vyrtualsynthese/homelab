"""Tests for cluster/helm/backup/files/verify.py.

The verifier is the only thing that notices a stream which has quietly stopped
advancing, so its cases are the ways that happens: a repository with no snapshot,
a snapshot too old to be last night's, a repository that grew past its budget, and
an etcd server whose own upload stopped reaching the bucket.
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


def etcd_s3_stub(*servers, age_h=2.0):
    """A stand-in for read_etcd_s3: what the API would answer, dated from now.

    `main` asks the API rather than a restic repository, so a test that runs it end to end
    has to hand it this. The dates are relative to the real clock, because main reports
    against `datetime.now`.
    """
    def read():
        stamp = (datetime.now(timezone.utc) - timedelta(hours=age_h)).isoformat()
        return list(servers), [
            {
                "metadata": {"name": f"s3-{server}"},
                "spec": {"location": f"s3://bucket/etcd/snap-{server}.zip",
                         "nodeName": server, "snapshotName": f"snap-{server}.zip"},
                "status": {"readyToUse": True, "creationTime": stamp},
            }
            for server in servers
        ]
    return read


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


class EtcdS3Tests(unittest.TestCase):
    """Each server's own upload, as K3s publishes it as an `ETCDSnapshotFile`."""

    def snapshot(self, server, hours_ago, name=None, ready=True, location=None, deleting=False):
        stamp = (NOW - timedelta(hours=hours_ago)).isoformat()
        entry = {
            "metadata": {"name": f"{server}-{hours_ago}"},
            "spec": {
                "location": location or f"s3://bucket/etcd/{name or f'snap-{server}.zip'}",
                "nodeName": server,
                "snapshotName": name or f"snap-{server}.zip",
            },
            "status": {"readyToUse": ready, "creationTime": stamp},
        }
        if deleting:
            entry["metadata"]["deletionTimestamp"] = stamp
        return entry

    def test_the_newest_upload_for_one_server_decides_its_age(self):
        snapshots = [
            self.snapshot("nuc0", 30, name="old.zip"),
            self.snapshot("nuc0", 2, name="new.zip"),
            self.snapshot("streaming", 1, name="other.zip"),
        ]
        newest = load().newest_s3_snapshot("nuc0", snapshots, NOW)
        self.assertEqual(newest["file"], "new.zip")
        self.assertEqual(newest["age_h"], 2.0)

    def test_a_local_snapshot_is_not_an_upload(self):
        # The same reconcile publishes both kinds. A local one proves the disk, which is the
        # half that dies with the node.
        snapshots = [self.snapshot("nuc0", 1,
                                   location="file:///var/lib/rancher/k3s/server/db/snapshots/a")]
        self.assertIsNone(load().newest_s3_snapshot("nuc0", snapshots, NOW))

    def test_a_snapshot_still_being_written_is_not_counted(self):
        self.assertIsNone(
            load().newest_s3_snapshot("nuc0", [self.snapshot("nuc0", 1, ready=False)], NOW)
        )

    def test_a_snapshot_mid_delete_is_not_counted(self):
        self.assertIsNone(
            load().newest_s3_snapshot("nuc0", [self.snapshot("nuc0", 1, deleting=True)], NOW)
        )

    def test_every_server_gets_a_line_and_a_quiet_run_has_no_problem(self):
        snapshots = [self.snapshot(n, 2) for n in ("nuc0", "streaming", "rbp2")]
        lines, problems = load().check_etcd_s3(["nuc0", "rbp2", "streaming"], snapshots, NOW)
        self.assertEqual(problems, [])
        self.assertEqual(lines, [
            "etcd-s3 server=nuc0 file=snap-nuc0.zip age_h=2.0",
            "etcd-s3 server=rbp2 file=snap-rbp2.zip age_h=2.0",
            "etcd-s3 server=streaming file=snap-streaming.zip age_h=2.0",
        ])

    def test_twenty_five_hours_is_fine_and_twenty_seven_is_not(self):
        fresh, _ = load().check_etcd_s3(["nuc0"], [self.snapshot("nuc0", 25)], NOW)
        _, problems = load().check_etcd_s3(["nuc0"], [self.snapshot("nuc0", 27)], NOW)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(problems, ["etcd-s3 server=nuc0 snapshot 27.0h old (>26h)"])

    def test_a_server_that_uploaded_nothing_is_named(self):
        # The fault the restic copy used to catch: the other two servers stay green, so the
        # check has to say which one stopped.
        snapshots = [self.snapshot(n, 2) for n in ("nuc0", "streaming")]
        lines, problems = load().check_etcd_s3(["nuc0", "rbp2", "streaming"], snapshots, NOW)
        self.assertIn("etcd-s3 server=rbp2 file=- age_h=-", lines)
        self.assertEqual(problems, ["etcd-s3 server=rbp2 has no snapshot in the bucket"])

    def test_the_servers_come_from_the_nodes_and_the_snapshots_from_the_api(self):
        # One stub API standing in for the two GETs: the role in the chart grants exactly
        # these two reads, so the paths are part of the contract.
        calls = []

        def api_get(path):
            calls.append(path)
            if path == "/api/v1/nodes":
                return {"items": [
                    {"metadata": {"name": "nas", "labels": {"kubernetes.io/hostname": "nas"}}},
                    {"metadata": {"name": "streaming",
                                  "labels": {"node-role.kubernetes.io/control-plane": "true"}}},
                    {"metadata": {"name": "nuc0",
                                  "labels": {"node-role.kubernetes.io/control-plane": "true"}}},
                ]}
            self.assertEqual(path, "/apis/k3s.cattle.io/v1/etcdsnapshotfiles")
            return {"items": [self.snapshot("nuc0", 1)]}

        module = load()
        original = module.api_get
        module.api_get = api_get
        try:
            servers, snapshots = module.read_etcd_s3()
        finally:
            module.api_get = original
        self.assertEqual(servers, ["nuc0", "streaming"])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(calls, ["/api/v1/nodes", "/apis/k3s.cattle.io/v1/etcdsnapshotfiles"])


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


class FilesRowTests(unittest.TestCase):
    """A `files` row that must hold something: the PostgreSQL dumps, the one backup of the database."""

    def entry(self, path, kind="file"):
        return {"path": path, "type": kind, "mtime": NOW.isoformat()}

    def rows(self):
        return [{"namespace": "databases", "claim": "postgres-dumps", "mustMatch": ["*.dump"]}]

    def test_a_row_whose_pattern_matches_nothing_is_a_problem(self):
        entries = [self.entry("/mnt/ssd0/pvc-abc_databases_postgres-dumps", "dir")]
        lines, problems = load().check_files("nodes-nas", self.rows(), entries)
        self.assertEqual(lines, ["files stream=nodes-nas row=databases/postgres-dumps matched=0"])
        self.assertEqual(problems, ["row databases/postgres-dumps holds no *.dump"])

    def test_the_dumps_in_the_claim_are_counted(self):
        entries = [
            self.entry("/mnt/ssd0/pvc-abc_databases_postgres-dumps", "dir"),
            self.entry("/mnt/ssd0/pvc-abc_databases_postgres-dumps/authentik-20261122T023000Z.dump"),
            self.entry("/mnt/ssd0/pvc-abc_databases_postgres-dumps/globals.sql"),
        ]
        lines, problems = load().check_files("nodes-nas", self.rows(), entries)
        self.assertEqual(lines, ["files stream=nodes-nas row=databases/postgres-dumps matched=1"])
        self.assertEqual(problems, [])

    def test_another_claims_files_do_not_count(self):
        entries = [self.entry("/mnt/ssd0/pvc-xyz_databases_pgadmin-config/dump.dump")]
        _, problems = load().check_files("nodes-nas", self.rows(), entries)
        self.assertEqual(problems, ["row databases/postgres-dumps holds no *.dump"])

    def test_a_partial_dump_alone_is_not_a_dump(self):
        entries = [self.entry("/mnt/ssd0/pvc-abc_databases_postgres-dumps/x-1.dump.partial")]
        _, problems = load().check_files("nodes-nas", self.rows(), entries)
        self.assertEqual(len(problems), 1)

    def test_the_run_fails_and_names_the_row_when_the_claim_holds_no_dump(self):
        # The dump CronJob can stop while every stream keeps succeeding on an empty claim.
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            fake = tmp / "restic"
            fake.write_text(
                "#!/bin/sh\n"
                'case "$3" in\n'
                f"  snapshots) echo '[{{\"id\": \"a\", \"time\": \"{stamp}\", \"tags\": [\"stream=nodes-nas\"]}}]' ;;\n"
                "  stats) echo '{\"total_size\": 4096}' ;;\n"
                f"  ls) echo '{{\"path\": \"/mnt/ssd0/pvc-abc_databases_postgres-dumps\", \"type\": \"dir\", \"mtime\": \"{stamp}\"}}' ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n"
            )
            fake.chmod(0o755)
            config = tmp / "streams.json"
            config.write_text(json.dumps([{
                "name": "nodes-nas", "repository": "s3:x/nodes-nas", "budgetGiB": 1,
                "files": [{"namespace": "databases", "claim": "postgres-dumps",
                           "mustMatch": ["*.dump"]}],
            }]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = load().main(["--config", str(config), "--restic", str(fake)],
                                   etcd_s3=etcd_s3_stub("nuc0"))
        self.assertEqual(code, 1)
        self.assertIn("files stream=nodes-nas row=databases/postgres-dumps matched=0", out.getvalue())
        self.assertIn("problem stream=nodes-nas row databases/postgres-dumps holds no *.dump",
                      out.getvalue())


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


class ExcludeReportTests(unittest.TestCase):
    """The report `prepare.py` leaves in the snapshot, which is the verifier's only way in."""

    def test_the_line_names_the_stream_the_row_and_the_pattern(self):
        self.assertEqual(
            load().exclude_lines(
                "nodes-streaming",
                [{"row": "media-stack/plex-config", "pattern": "Cache/"}]),
            ["exclude-unused stream=nodes-streaming "
             "row=media-stack/plex-config pattern=Cache/"],
        )

    def test_a_snapshot_taken_before_the_report_existed_is_left_alone(self):
        self.assertEqual(load().read_exclude_report("s3:x/nfs", "a", []), [])


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
            f"  ls) echo '{{\"path\": \"/staging/claims/monitoring/grafana/grafana.db\", \"type\": \"file\", \"mtime\": \"{ls_mtime}\"}}' ;;\n"
            "  *) exit 9 ;;\n"
            "esac\n"
        )
        fake.chmod(0o755)
        return fake

    def run_main(self, tmp: pathlib.Path, snapshot_iso: str, ls_mtime: str, streams: list[dict],
                 etcd=None):
        import contextlib
        import io
        import json

        config = tmp / "streams.json"
        config.write_text(json.dumps(streams))
        fake = self.write_restic(tmp, snapshot_iso, ls_mtime, streams[0]["name"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = load().main(["--config", str(config), "--restic", str(fake)],
                               etcd_s3=etcd or etcd_s3_stub("nuc0"))
        return code, out.getvalue()

    def test_a_healthy_run_prints_the_line_and_exits_zero(self):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(
                pathlib.Path(tmp), stamp, stamp,
                [{"name": "nfs", "repository": "s3:x/nfs", "budgetGiB": 1}],
            )
        self.assertEqual(code, 0)
        self.assertRegex(out, r"(?m)^stream=nfs snapshots=1 bytes=4096 newest=\S+ age_h=0\.0$")
        self.assertIn("etcd-s3 server=nuc0 file=snap-nuc0.zip age_h=2.0", out)

    def test_a_stale_stream_exits_one_and_names_the_problem(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(
                pathlib.Path(tmp), old, old,
                [{"name": "nodes-nuc0", "repository": "s3:x/nodes-nuc0", "budgetGiB": 1}],
            )
        self.assertEqual(code, 1)
        self.assertIn("problem stream=nodes-nuc0 newest snapshot", out)

    def test_a_stale_etcd_upload_fails_the_run_even_when_every_stream_is_fresh(self):
        # The two halves fail independently: the restic streams say nothing about whether a
        # server is still uploading its own snapshots.
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(
                pathlib.Path(tmp), stamp, stamp,
                [{"name": "nodes-nuc0", "repository": "s3:x/nodes-nuc0", "budgetGiB": 1}],
                etcd=etcd_s3_stub("nuc0", "rbp2", age_h=30.0),
            )
        self.assertEqual(code, 1)
        self.assertIn("problem etcd-s3 server=nuc0 snapshot 30.0h old (>26h)", out)
        self.assertNotIn("problem stream=", out)

    def test_an_api_that_cannot_be_reached_is_a_problem_not_a_crash(self):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        def broken():
            raise RuntimeError("dial tcp: connection refused")

        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(
                pathlib.Path(tmp), stamp, stamp,
                [{"name": "nfs", "repository": "s3:x/nfs", "budgetGiB": 1}],
                etcd=broken,
            )
        self.assertEqual(code, 1)
        self.assertIn("stream=nfs snapshots=1", out)  # the stream report survives
        self.assertIn("problem etcd-s3 api dial tcp: connection refused", out)

    def test_an_empty_repository_names_the_stream_and_does_not_abort(self):
        # `restic ls latest` exits 1 with "no snapshot found" on an empty repository: the
        # verifier must report the empty stream, not die on the file list.
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
                '[{"name": "nodes-nuc0", "repository": "s3:x/nodes-nuc0", "budgetGiB": 1}]'
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = load().main(["--config", str(config), "--restic", str(fake)],
                                   etcd_s3=etcd_s3_stub("nuc0"))
        self.assertEqual(code, 1)
        self.assertIn("problem stream=nodes-nuc0 no snapshot", out.getvalue())
        self.assertIn("etcd-s3 server=nuc0 file=snap-nuc0.zip", out.getvalue())

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
                code = load().main(["--config", str(config), "--restic", str(fake)],
                                   etcd_s3=etcd_s3_stub("nuc0"))
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
                code = load().main(["--config", str(config), "--restic", str(fake)],
                                   etcd_s3=etcd_s3_stub("nuc0"))
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
                f'        echo \'{{"path": "/staging/claims/monitoring/grafana/grafana.db", "type": "file", "mtime": "{adhoc}"}}\' ;\n'
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
                code = load().main(["--config", str(config), "--restic", str(fake)],
                                   etcd_s3=etcd_s3_stub("nuc0"))
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("staged stream=nodes-nuc0 row=databases/pgadmin-config files=1", out.getvalue())

    def test_an_unused_exclusion_is_repeated_in_the_morning_and_does_not_fail_the_run(self):
        # The verifier never sees a claim, so it repeats what the node job wrote into the
        # snapshot. A warning line, not a problem: a warning that changed the exit code would
        # page for a night that backed everything up.
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        report = '[{"row": "media-stack/plex-config", "pattern": "Cache/"}]'
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            fake = tmp / "restic"
            fake.write_text(
                "#!/bin/sh\n"
                'case "$3" in\n'
                f"  snapshots) echo '[{{\"id\": \"a\", \"time\": \"{stamp}\", \"tags\": [\"stream=nodes-streaming\"]}}]' ;;\n"
                "  stats) echo '{\"total_size\": 4096}' ;;\n"
                "  ls) echo '{\"path\": \"/staging/claims/exclude-unused.json\", \"type\": \"file\"}' ;;\n"
                "  dump) if [ \"$5\" = \"/staging/claims/exclude-unused.json\" ]; then\n"
                f"          echo '{report}' ;\n"
                "        else exit 8 ; fi ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n"
            )
            fake.chmod(0o755)
            config = tmp / "streams.json"
            config.write_text(json.dumps([{
                "name": "nodes-streaming", "repository": "s3:x/nodes-streaming",
                "budgetGiB": 1, "claims": True,
            }]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = load().main(["--config", str(config), "--restic", str(fake)],
                                   etcd_s3=etcd_s3_stub("nuc0"))
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn(
            "exclude-unused stream=nodes-streaming row=media-stack/plex-config pattern=Cache/",
            out.getvalue(),
        )

    def test_a_snapshot_without_the_report_prints_no_exclude_line(self):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            fake = tmp / "restic"
            fake.write_text(
                "#!/bin/sh\n"
                'case "$3" in\n'
                f"  snapshots) echo '[{{\"id\": \"a\", \"time\": \"{stamp}\", \"tags\": [\"stream=nodes-nas\"]}}]' ;;\n"
                "  stats) echo '{\"total_size\": 4096}' ;;\n"
                "  ls) echo '{\"path\": \"/staging/claims/monitoring/grafana/grafana.db\", \"type\": \"file\"}' ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n"
            )
            fake.chmod(0o755)
            config = tmp / "streams.json"
            config.write_text(json.dumps([{
                "name": "nodes-nas", "repository": "s3:x/nodes-nas", "budgetGiB": 1,
                "claims": True, "staged": [{"namespace": "monitoring", "claim": "grafana"}],
            }]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = load().main(["--config", str(config), "--restic", str(fake)],
                                   etcd_s3=etcd_s3_stub("nuc0"))
        self.assertEqual(code, 0, out.getvalue())
        self.assertNotIn("exclude-unused", out.getvalue())

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


class ExternalStreamTests(unittest.TestCase):
    """A stream written by a host the cluster does not drive.

    The chart cannot prove the Pi's copy is complete, so a `mustMatch` pattern that matches
    nothing is how a night that copied a fragment shows up.
    """

    def test_an_external_stream_must_hold_its_required_files(self):
        entries = [{"path": "/var/lib/pi-backup/tree/dockers/etc-pihole/gravity.db"},
                   {"path": "/var/lib/pi-backup/tree/dockers/config/wg-easy.db"}]
        lines, problems = load().check_external(
            "edge-rbp0", ["dockers/etc-pihole/gravity.db", "dockers/config/wg-easy.db"], entries)
        self.assertEqual(problems, [])
        self.assertEqual(lines, [
            "files stream=edge-rbp0 path=dockers/etc-pihole/gravity.db matched=1",
            "files stream=edge-rbp0 path=dockers/config/wg-easy.db matched=1",
        ])

    def test_an_external_stream_missing_one_required_file_is_a_problem(self):
        entries = [{"path": "/var/lib/pi-backup/tree/dockers/etc-pihole/gravity.db"}]
        lines, problems = load().check_external(
            "edge-rbp0", ["dockers/etc-pihole/gravity.db", "dockers/config/wg-easy.db"], entries)
        self.assertEqual(problems, ["edge-rbp0 is missing dockers/config/wg-easy.db"])


if __name__ == "__main__":
    unittest.main()
