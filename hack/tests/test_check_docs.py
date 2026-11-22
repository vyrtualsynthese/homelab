import contextlib
import pathlib
import shutil
import sys
import tempfile
import unittest

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import check_docs  # noqa: E402

FIXTURE_DOCS = HACK / "fixtures" / "docs"


@contextlib.contextmanager
def temp_tree(files: dict[str, str]):
    root = pathlib.Path(tempfile.mkdtemp())
    try:
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        yield root
    finally:
        shutil.rmtree(root)


@contextlib.contextmanager
def fixture_root(nav_extra: str = "", unlisted: bool = False):
    root = pathlib.Path(tempfile.mkdtemp())
    try:
        for source in FIXTURE_DOCS.rglob("*"):
            if not source.is_file():
                continue
            target = root / source.relative_to(FIXTURE_DOCS)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        if unlisted:
            page = root / "docs" / "things" / "two.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("# Two\n\nNot listed, and that is the fixture.\n", encoding="utf-8")
        if nav_extra:
            mkdocs = root / "mkdocs.yml"
            mkdocs.write_text(mkdocs.read_text(encoding="utf-8") + nav_extra, encoding="utf-8")
        yield root
    finally:
        shutil.rmtree(root)


class ReferenceTests(unittest.TestCase):
    def test_a_cited_path_with_an_anchor_resolves(self):
        with temp_tree({"docs/a.md": "see docs/b.md#later\n", "docs/b.md": "# Later\n"}) as root:
            self.assertEqual(check_docs.reference_problems(root), [])

    def test_a_cited_path_in_a_yaml_comment_that_is_missing_is_reported(self):
        with temp_tree({"docs/a.md": "x\n", "chart/values.yaml": "# see docs/gone.md\n"}) as root:
            self.assertEqual(check_docs.reference_problems(root), ["chart/values.yaml: docs/gone.md"])

    def test_a_crlf_citation_resolves(self):
        with temp_tree({"docs/a.md": "x\r\nsee docs/b.md\r\n", "docs/b.md": "y\r\n"}) as root:
            self.assertEqual(check_docs.reference_problems(root), [])

    def test_a_path_inside_superpowers_is_not_checked(self):
        with temp_tree({"docs/a.md": "x\n", "docs/superpowers/plans/p.md": "see docs/gone.md\n"}) as root:
            self.assertEqual(check_docs.reference_problems(root), [])


class NavTests(unittest.TestCase):
    def test_a_page_missing_from_the_nav_is_reported(self):
        with fixture_root(unlisted=True) as root:
            self.assertEqual(check_docs.nav_problems(root), ["docs/things/two.md: no nav entry"])

    def test_a_nav_entry_with_no_file_is_reported(self):
        with fixture_root(nav_extra="  - Missing: things/ghost.md\n") as root:
            self.assertEqual(check_docs.nav_problems(root), ["mkdocs.yml: things/ghost.md does not exist"])

    def test_a_url_in_the_nav_is_ignored(self):
        with fixture_root(nav_extra="  - Board: https://vikunja.kub.local\n") as root:
            self.assertEqual(check_docs.nav_problems(root), [])


if __name__ == "__main__":
    unittest.main()
