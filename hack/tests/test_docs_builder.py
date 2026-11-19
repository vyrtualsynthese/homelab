#!/usr/bin/env python3
"""Tests for the docs-site builder's identity and branch bar.

Run from the repository root:

    python3 hack/tests/test_docs_builder.py

The builder ships inside a Helm chart (`cluster/helm/docs-site/files/build.py`) and runs from a
ConfigMap; it is imported here by path so the functions that touch the served tree can be exercised
without GitHub, MkDocs or a cluster. Three properties are load-bearing and each has a test:

- the identity link and the bar are written once, not stacked;
- a chip points at the same page on the other branch when that page exists, and at that branch's
  start page when it does not;
- the landing page is complete without a browser: every branch is named, a failure is shown with
  the previous site still reachable, and no placeholder survives.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "cluster/helm/docs-site/files/build.py"

PAGE = (
    "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
    "<title>t</title>\n</head>\n<body>\n<header class=\"md-header\">\n"
    "<nav class=\"md-header__inner\">h</nav>\n</header>\n"
    "<div class=\"md-container\">content</div>\n</body></html>\n"
)


class DocsBuilderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="docs-builder-"))
        os.environ.update(
            GITHUB_TOKEN="test",
            SITE_ROOT=str(cls.tmp / "site"),
            WORK_ROOT=str(cls.tmp / "work"),
            STATE=str(cls.tmp / "state.json"),
            BUILDER_REVISION="5",
        )
        spec = importlib.util.spec_from_file_location("docs_site_build", BUILDER)
        assert spec and spec.loader
        cls.build = importlib.util.module_from_spec(spec)
        sys.modules["docs_site_build"] = cls.build
        spec.loader.exec_module(cls.build)
        cls.site = cls.tmp / "site"
        cls.stamp = cls.build.brand_stamp()
        cls.write_pages(
            "homelab/develop/index.html",
            "homelab/develop/how-it-works/index.html",
            "homelab/main/index.html",
            "homelab/main/guide/index.html",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def write_pages(cls, *relative: str) -> None:
        for name in relative:
            target = cls.site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(PAGE, encoding="utf-8")

    def read(self, relative: str) -> str:
        return (self.site / relative).read_text(encoding="utf-8")

    def stamp_all(self, touched: set[str] | None = None) -> int:
        return self.build.stamp_sites(self.site, ("main", "develop"), self.stamp, touched)

    def test_brand_is_published_once_and_readable(self) -> None:
        self.build.publish_brand(self.site)
        for name in self.build.BRAND_FILES:
            target = self.site / "brand" / name
            self.assertTrue(target.is_file(), f"{name} not published")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644, f"{name} not world-readable")
        # A second run must not rewrite what is already correct.
        before = (self.site / "brand/ashulab.css").stat().st_mtime_ns
        self.build.publish_brand(self.site)
        self.assertEqual(before, (self.site / "brand/ashulab.css").stat().st_mtime_ns)

    def test_bar_links_the_same_page_and_falls_back_to_the_root(self) -> None:
        self.stamp_all()
        home = self.read("homelab/develop/index.html")
        self.assertEqual(home.count('href="/brand/ashulab.css"'), 1, "stylesheet linked twice")
        self.assertEqual(home.count("ashulab-refbar:start"), 1, "bar injected twice")
        self.assertLess(home.index("</header>"), home.index("ashulab-refbar:start"))
        self.assertIn('aria-current="true">develop<', home)
        self.assertIn('href="/homelab/main/" title="this page on main">main</a>', home)

        # `how-it-works/` exists on develop only, so the main chip opens main's root.
        deeper = self.read("homelab/develop/how-it-works/index.html")
        self.assertIn('href="/homelab/main/" title="its start page on main">main</a>', deeper)

        # `guide/` exists on main only, so the develop chip opens develop's root.
        main_page = self.read("homelab/main/guide/index.html")
        self.assertIn('href="/homelab/develop/" title="its start page on develop">develop</a>', main_page)

    def test_second_pass_writes_nothing_and_a_touched_repo_catches_up(self) -> None:
        self.stamp_all()
        self.assertEqual(self.stamp_all(), 0, "an idle pass must not rewrite pages")

        # A page appears on develop: the stamp still matches, but main's chip for
        # the same page is now wrong, so the touched repository is rewritten.
        self.write_pages("homelab/develop/guide/index.html")
        self.assertGreaterEqual(self.stamp_all(touched={"homelab"}), 1)
        main_page = self.read("homelab/main/guide/index.html")
        self.assertIn('href="/homelab/develop/guide/" title="this page on develop">develop</a>', main_page)

    def test_landing_page_names_every_branch_and_survives_a_failure(self) -> None:
        state = {
            "refs": {
                "homelab/develop": {"status": "ok", "built_at": self.build.now_iso(), "detail": "built"},
                "homelab/main": {"status": "ok", "built_at": self.build.now_iso(), "detail": "built"},
                "config/develop": {"status": "absent", "detail": "no docs/ folder", "built_at": None},
                "config/main": {"status": "absent", "detail": "no such branch", "built_at": None},
                "scripts/main": {
                    "status": "failed",
                    "detail": "mkdocs said something long\nand then more",
                    "built_at": self.build.now_iso(),
                },
            },
            "invisible": ["secret-repo"],
        }
        self.build.write_index(self.site, state)
        page = self.read("index.html")

        self.assertNotIn("@@", page, "a placeholder survived into the page")
        for needle in (
            "AshuLab <span>documentation</span>",
            "2 sites from 3 repositories",
            'class="card"',
            "build failed",
            "no docs/ folder",
            "no such branch",
            "secret-repo",
            'class="ref ref--failed"',
        ):
            self.assertIn(needle, page)
        self.assertIn('href="/homelab/develop/"', page)
        self.assertTrue((self.site / "index.html").stat().st_mode & 0o444, "index not readable")

    def test_docs_dir_is_read_as_yaml_not_line_by_line(self) -> None:
        """A quoted key, flow style or a block scalar must not slip past the refusal."""
        src = self.tmp / "checkout"
        src.mkdir(exist_ok=True)
        cases = {
            "quoted key": '"docs_dir": /etc\nsite_name: x\n',
            "flow style": "{docs_dir: /etc, site_name: x}\n",
            "block scalar": "docs_dir: |-\n  /etc\nsite_name: x\n",
            "unknown tag": "docs_dir: /etc\nsite_name: !!python/object:x.Y {a: b}\n",
        }
        for name, text in cases.items():
            config = self.tmp / f"{name.replace(' ', '-')}.yml"
            config.write_text(text, encoding="utf-8")
            with self.subTest(case=name):
                with self.assertRaises(self.build.BuildError):
                    self.build.check_docs_dir(config, src)

    def test_docs_dir_inside_the_checkout_or_not_at_the_top_level_is_left_alone(self) -> None:
        src = self.tmp / "checkout"
        src.mkdir(exist_ok=True)
        cases = {
            "inside": "docs_dir: documentation\nsite_name: x\n",
            "nested": "site_name: x\nnav:\n  - docs_dir: /etc\n",
            "unparsable": "docs_dir: [ /etc\nsite_name: x\n",
        }
        for name, text in cases.items():
            config = self.tmp / f"allowed-{name}.yml"
            config.write_text(text, encoding="utf-8")
            with self.subTest(case=name):
                self.build.check_docs_dir(config, src)

    def test_a_failed_pair_that_lost_its_site_is_rebuilt_past_the_failure_limit(self) -> None:
        """A swap failure records `failed`; skipping it forever would strand the pair."""
        original = self.build.head_sha
        self.build.head_sha = lambda repo, ref, etag: ("sha1", "etag1", "ok")
        try:
            published_before = {
                "status": "failed",
                "sha": "sha1",
                "etag": "etag1",
                "digest": self.build.entry_digest("sha1", "etag1"),
                "built_at": "2026-11-17T00:00:00+00:00",
                "detail": "could not publish",
                "failures": self.build.FAILURE_LIMIT,
            }
            self.assertEqual(
                self.build.decide("ghost", "develop", published_before, full=False).action,
                "build",
                "a pair whose site disappeared must not wait for its head to move",
            )

            never_published = dict(published_before, built_at=None, detail="mkdocs refused")
            self.assertEqual(
                self.build.decide("ghost", "main", never_published, full=False).action,
                "skip",
                "a pair that never published keeps its backoff",
            )
        finally:
            self.build.head_sha = original

    def test_probe_tells_a_deleted_repository_from_a_refused_one(self) -> None:
        original = self.build.http_get
        try:
            self.build.http_get = lambda url, headers: (404, {}, b"{}")
            self.assertEqual(self.build.probe_repo("gone"), "gone")
            self.build.http_get = lambda url, headers: (403, {}, b"{}")
            self.assertEqual(self.build.probe_repo("hidden"), "refused")
            self.build.http_get = lambda url, headers: (200, {}, b"{}")
            self.assertEqual(self.build.probe_repo("fine"), "readable")
        finally:
            self.build.http_get = original

    def test_a_deleted_repository_is_forgotten_and_a_refused_one_is_kept(self) -> None:
        state = {
            "refs": {
                "gone/develop": {"status": "ok"},
                "hidden/main": {"status": "ok"},
            },
            "invisible": ["gone"],
        }
        original = self.build.probe_repo
        self.build.probe_repo = lambda name: {"gone": "gone", "hidden": "refused"}[name]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.build.resolve_invisible(state, []), [])
        finally:
            self.build.probe_repo = original
        self.assertNotIn("gone/develop", state["refs"], "a deleted repository was kept")
        self.assertNotIn("gone", state["invisible"], "a deleted repository stays on the index")
        self.assertIn("hidden/main", state["refs"], "a refused repository lost its branches")
        self.assertEqual(state["invisible"], ["hidden"])

    def test_clock_and_ago_read_the_states_timestamps(self) -> None:
        moment = "2026-11-18T00:46:45+00:00"
        self.assertEqual(self.build._clock(moment), "2026-11-18 00:46 UTC")
        self.assertEqual(self.build._clock(None), "never")
        self.assertEqual(self.build._clock("not a date"), "not a date")
        self.assertEqual(self.build._ago(None), "never")


if __name__ == "__main__":
    unittest.main()
