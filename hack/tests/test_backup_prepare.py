"""Tests for cluster/helm/backup/files/prepare.py.

The script runs inside the backup jobs, where the node's local-path roots and the
claim directories are already mounted. Every case here is a shape the live cluster
was measured to have on 2026-11-20: a claim that resolves, a claim whose name is a
prefix of another, a claim whose directory is gone, and a claim that matches two
directories because a released PV left one behind.
"""

import contextlib
import io
import json
import pathlib
import shutil
import sqlite3
import tempfile
import unittest
from importlib import util

PREPARE = pathlib.Path(__file__).resolve().parents[2] / "cluster/helm/backup/files/prepare.py"


def load():
    """Import prepare.py once: a second import would give a second RowError class."""
    if not hasattr(load, "module"):
        spec = util.spec_from_file_location("prepare", PREPARE)
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)
        load.module = module
    return load.module


class ResolveTests(unittest.TestCase):
    def test_resolves_the_single_matching_directory(self):
        with tempfile.TemporaryDirectory() as root:
            p = pathlib.Path(root) / "pvc-abc_media-stack_sonarr-config"
            p.mkdir()
            self.assertEqual(load().resolve_claim([pathlib.Path(root)], "media-stack", "sonarr-config"), p)

    def test_a_longer_claim_name_is_not_a_match(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "pvc-abc_media-stack_sonarr-anime-config").mkdir()
            with self.assertRaises(load().RowError):
                load().resolve_claim([pathlib.Path(root)], "media-stack", "sonarr-config")

    def test_no_match_raises_naming_the_row(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(load().RowError, "media-stack/sonarr-config"):
                load().resolve_claim([pathlib.Path(root)], "media-stack", "sonarr-config")

    def test_two_matches_raise_instead_of_picking_one(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "pvc-old_media-stack_sonarr-config").mkdir()
            (pathlib.Path(root) / "pvc-new_media-stack_sonarr-config").mkdir()
            with self.assertRaises(load().RowError):
                load().resolve_claim([pathlib.Path(root)], "media-stack", "sonarr-config")

    def test_a_pinned_row_uses_only_that_pv(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "pvc-old_media-stack_pgadmin-config").mkdir()
            new = pathlib.Path(root) / "pvc-new_media-stack_pgadmin-config"
            new.mkdir()
            self.assertEqual(
                load().resolve_claim([pathlib.Path(root)], "media-stack", "pgadmin-config", pv="pvc-new"),
                new,
            )

    def test_a_pinned_row_that_is_missing_raises_naming_the_pv(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "pvc-other_media-stack_pgadmin-config").mkdir()
            with self.assertRaisesRegex(load().RowError, "pvc-gone"):
                load().resolve_claim([pathlib.Path(root)], "media-stack", "pgadmin-config", pv="pvc-gone")


class StageTests(unittest.TestCase):
    def test_wal_rows_are_in_the_staged_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "sonarr.db"
            live = sqlite3.connect(src)
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("CREATE TABLE t (v TEXT)")
            live.execute("INSERT INTO t VALUES ('kept')")
            live.commit()                      # committed, still not checkpointed
            staged = pathlib.Path(tmp) / "out"
            staged.mkdir()
            load().stage_databases(src.parent, staged, ["sonarr.db"])
            live.close()
            copy = sqlite3.connect(staged / "sonarr.db")
            self.assertEqual(copy.execute("SELECT v FROM t").fetchone()[0], "kept")
            copy.close()

    def test_a_wal_database_on_a_read_only_mount_is_staged_from_a_copy(self):
        # Every claim is mounted read-only, and SQLite cannot open a WAL database read-only
        # without creating its -shm. The staging copy is the way in: the WAL is replayed
        # there, a committed frame survives, and nothing is written back to the claim.
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "src"
            src.mkdir()
            live = sqlite3.connect(src / "kavita.db")
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("PRAGMA wal_autocheckpoint=0")
            live.execute("CREATE TABLE t (v TEXT)")
            live.execute("INSERT INTO t VALUES ('kept')")
            live.commit()
            frozen = pathlib.Path(tmp) / "frozen"
            frozen.mkdir()
            shutil.copy2(src / "kavita.db", frozen / "kavita.db")
            shutil.copy2(src / "kavita.db-wal", frozen / "kavita.db-wal")
            live.close()
            frozen.chmod(0o555)
            try:
                out = pathlib.Path(tmp) / "out"
                written = load().stage_databases(frozen, out, ["*.db"])
                self.assertEqual([p.name for p in written], ["kavita.db"])
                copy = sqlite3.connect(written[0])
                self.assertEqual(copy.execute("SELECT v FROM t").fetchone()[0], "kept")
                copy.close()
                self.assertEqual(sorted(p.name for p in out.iterdir()), ["kavita.db"])
            finally:
                frozen.chmod(0o755)

    def test_a_claim_with_no_database_yet_stages_nothing_instead_of_failing(self):
        # A freshly installed application has created its claim and no database yet. The claim
        # is still backed up; only the staged copy is missing, and that is not a failure.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load().stage_databases(pathlib.Path(tmp), pathlib.Path(tmp), ["*.db"]), [])

    def test_a_nested_pattern_keeps_its_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = pathlib.Path(tmp) / "Plug-in Support" / "Databases"
            nested.mkdir(parents=True)
            db = nested / "library.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE t (v TEXT)")
            conn.commit()
            conn.close()
            staged = pathlib.Path(tmp) / "out"
            staged.mkdir()
            written = load().stage_databases(pathlib.Path(tmp), staged, ["Plug-in Support/Databases/*.db"])
            self.assertEqual(written, [staged / "Plug-in Support" / "Databases" / "library.db"])

    def test_an_excluded_database_is_not_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "claim"
            src.mkdir()
            for name in ("sonarr.db", "logs.db"):
                conn = sqlite3.connect(src / name)
                conn.execute("CREATE TABLE t (v TEXT)")
                conn.commit()
                conn.close()
            out = pathlib.Path(tmp) / "out"
            written = load().stage_databases(src, out, ["*.db"], exclude=["logs.db*"])
            self.assertEqual([p.name for p in written], ["sonarr.db"])

    def test_a_sqlite_row_excludes_every_database_in_the_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            claim = pathlib.Path(tmp) / "pvc-abc_media-stack_sonarr-config"
            claim.mkdir()
            (claim / "sonarr.db").touch()
            (claim / "logs.db").touch()
            rows = [{"namespace": "media-stack", "claim": "sonarr-config", "kind": "sqlite",
                     "dir": str(claim), "db_patterns": ["*.db"], "databases": ["sonarr.db"]}]
            _, exclude = load().write_restic_inputs(rows, pathlib.Path(tmp) / "staging")
            text = exclude.read_text()
            self.assertIn(str(claim / "sonarr.db-wal"), text)
            self.assertIn(str(claim / "logs.db"), text)

    def test_a_wide_pattern_does_not_stage_a_databases_side_files(self):
        # seerr's row globs db/*.sqlite*, which also matches db.sqlite-wal. Staging that file
        # raises "file is not a database" and fails the whole node job.
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "seerr"
            (src / "db").mkdir(parents=True)
            conn = sqlite3.connect(src / "db" / "db.sqlite")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE t (v TEXT)")
            conn.commit()
            conn.close()
            for suffix in ("-wal", "-shm"):
                (src / "db" / f"db.sqlite{suffix}").write_bytes(b"not a database")
            out = pathlib.Path(tmp) / "out"
            written = load().stage_databases(src, out, ["db/*.sqlite*"])
            self.assertEqual([p.name for p in written], ["db.sqlite"])

    def test_a_file_that_is_not_a_database_raises_a_row_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "broken.db").write_text("not a database")
            with self.assertRaisesRegex(load().RowError, "broken.db"):
                load().stage_databases(pathlib.Path(tmp), pathlib.Path(tmp) / "out", ["*.db"])

    def test_the_exclude_list_covers_wal_shm_and_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "app"
            src.mkdir()
            (src / "app.db").touch()
            paths = load().exclude_patterns(src, ["app.db"])
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertIn(str(src / f"app.db{suffix}"), paths)


class HostPathTests(unittest.TestCase):
    def test_a_missing_host_path_is_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            present, missing = load().host_paths([tmp, str(pathlib.Path(tmp) / "absent")])
            self.assertEqual(present, [tmp])
            self.assertEqual(missing, [str(pathlib.Path(tmp) / "absent")])

    def test_host_paths_reach_the_restic_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths_file, _ = load().write_restic_inputs([], pathlib.Path(tmp), [tmp])
            self.assertIn(tmp, paths_file.read_text().split())


class ResticInputTests(unittest.TestCase):
    def test_row_excludes_are_written_as_absolute_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            claim_dir = root / "pvc-abc_media-stack_sonarr-config"
            claim_dir.mkdir()
            rows = [{"namespace": "media-stack", "claim": "sonarr-config",
                     "kind": "files", "dir": str(claim_dir), "exclude": ["logs/"]}]
            paths, exclude = load().write_restic_inputs(rows, root / "staging")
            self.assertIn(str(claim_dir), paths.read_text().split())
            text = exclude.read_text()
            self.assertIn(str(claim_dir / "logs"), text)
            self.assertIn(str(claim_dir / "logs" / "**"), text)

    def test_a_name_excludes_at_any_depth(self):
        # Plex keeps Cache/ three levels down under Library/Application Support/Plex Media
        # Server/. Anchored at the claim root, the entry matched nothing and 2.1 GiB of
        # transcoder cache rode in the stream from 2026-11-20: the pattern restic is given has
        # to reach it, and one for the directory plus one for everything beneath it.
        claim_dir = pathlib.Path("/mnt/ssd0/pvc-abc_media-stack_plex-config")
        patterns = load()._absolute_patterns(claim_dir, "Cache/")
        self.assertIn(f"{claim_dir}/Cache", patterns)
        self.assertIn(f"{claim_dir}/Cache/**", patterns)
        self.assertIn(f"{claim_dir}/**/Cache", patterns)
        self.assertIn(f"{claim_dir}/**/Cache/**", patterns)

    def test_a_path_stays_anchored_at_the_claim_root(self):
        # An entry that names a path is a path, not a name to be found anywhere: it must not
        # start excluding a same-named directory in some unrelated part of the claim.
        claim_dir = pathlib.Path("/mnt/ssd0/pvc-abc_media-stack_plex-config")
        patterns = load()._absolute_patterns(claim_dir, "Plug-in Support/Databases/*.db-*")
        self.assertEqual(patterns, [
            f"{claim_dir}/Plug-in Support/Databases/*.db-*",
            f"{claim_dir}/Plug-in Support/Databases/*.db-*/**",
        ])


class ExcludeMatchTests(unittest.TestCase):
    """The half that decides whether an excluded database is staged, and restic's half agree."""

    def test_a_name_matches_a_nested_component(self):
        relative = pathlib.Path("Library/Application Support/Plex Media Server/Cache/art.jpg")
        self.assertTrue(load().matches_any(relative, ["Cache/"]))

    def test_a_name_does_not_match_a_longer_component(self):
        self.assertFalse(load().matches_any(pathlib.Path("CacheInfo"), ["Cache/"]))

    def test_a_path_matches_only_from_the_claim_root(self):
        self.assertTrue(load().matches_any(
            pathlib.Path("Plug-in Support/Databases/library.db-2026-11-21"),
            ["Plug-in Support/Databases/*.db-*"]))
        # the same shape of file elsewhere in the claim is not what the path names
        self.assertFalse(load().matches_any(
            pathlib.Path("Other/Plug-in Support/Databases/x.db-1"),
            ["Plug-in Support/Databases/*.db-*"]))


class MainTests(unittest.TestCase):
    """The init container's exit code is the node job's: 0 means restic runs."""

    def run_main(self, root, rows):
        staging = pathlib.Path(root) / "staging"
        rows_file = pathlib.Path(root) / "rows.json"
        rows_file.write_text(rows)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = load().main(["--rows", str(rows_file), "--staging", str(staging), "--root", root])
        return code, out.getvalue(), err.getvalue(), staging

    def test_a_sqlite_row_with_no_database_yet_warns_and_the_job_runs(self):
        # The 2026-11-20 shape: Plex had never started, so its claim held no database. The
        # whole node failed for it, and a night that backed up eighteen claims looked like a
        # broken backup.
        with tempfile.TemporaryDirectory() as root:
            claim = pathlib.Path(root) / "pvc-abc_media-stack_plex-config"
            claim.mkdir()
            code, out, err, staging = self.run_main(root, json.dumps({"claims": [
                {"namespace": "media-stack", "claim": "plex-config", "kind": "sqlite",
                 "db": ["Plug-in Support/Databases/*.db"]},
            ]}))
            self.assertEqual(code, 0, err)
            self.assertIn("row-warning", out)
            self.assertIn("media-stack/plex-config", out)
            self.assertEqual(err, "")
            self.assertIn(str(claim), (staging / "restic-paths.txt").read_text())

    def test_a_database_that_cannot_be_staged_still_fails_the_job(self):
        with tempfile.TemporaryDirectory() as root:
            claim = pathlib.Path(root) / "pvc-abc_media-stack_kavita-config"
            claim.mkdir()
            (claim / "kavita.db").write_text("not a database")
            code, _, err, _ = self.run_main(root, json.dumps({"claims": [
                {"namespace": "media-stack", "claim": "kavita-config", "kind": "sqlite"},
            ]}))
            self.assertEqual(code, 1)
            self.assertIn("row-error", err)
            self.assertIn("row-errors=1", err)


if __name__ == "__main__":
    unittest.main()
