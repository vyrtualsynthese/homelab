import contextlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import check_docs_staleness  # noqa: E402

PAGE = """> **In one line.** x
> **Use it when** y · **Verified** 2026-11-01 · **Kind:** explanation
>
> **Sources** `cluster/helm/backup/values.yaml`
"""


def _git(root: pathlib.Path, *args: str, date: str = "2026-11-01") -> None:
    env = dict(os.environ)
    env["GIT_AUTHOR_DATE"] = f"{date}T12:00:00+01:00"
    env["GIT_COMMITTER_DATE"] = f"{date}T12:00:00+01:00"
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
        env=env,
    )


@contextlib.contextmanager
def git_repo(files: dict[str, str], date: str = "2026-11-01"):
    root = pathlib.Path(tempfile.mkdtemp())
    try:
        _git(root, "init", "-q", date=date)
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        _git(root, "add", "-A", date=date)
        _git(root, "commit", "-q", "-m", "initial", date=date)
        yield root
    finally:
        shutil.rmtree(root)


def touch_commit(root: pathlib.Path, path: str, date: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(target.read_text(encoding="utf-8") + "changed: true\n" if target.exists() else "x: 1\n", encoding="utf-8")
    _git(root, "add", path, date=date)
    _git(root, "commit", "-q", "-m", f"touch {path}", date=date)


class StalenessTests(unittest.TestCase):
    def test_a_page_older_than_its_source_is_listed(self):
        with git_repo({"docs/a.md": PAGE, "cluster/helm/backup/values.yaml": "x: 1\n"}) as root:
            touch_commit(root, "cluster/helm/backup/values.yaml", "2026-11-20")
            self.assertEqual(
                check_docs_staleness.stale_pages(root),
                [(root / "docs/a.md", "cluster/helm/backup/values.yaml", "2026-11-20")],
            )

    def test_a_page_newer_than_its_source_is_not(self):
        with git_repo({"docs/a.md": PAGE, "cluster/helm/backup/values.yaml": "x: 1\n"}, date="2026-11-01") as root:
            touch_commit(root, "cluster/helm/backup/values.yaml", "2026-10-20")
            self.assertEqual(check_docs_staleness.stale_pages(root), [])

    def test_a_page_without_sources_is_skipped(self):
        page = PAGE.replace("> **Sources** `cluster/helm/backup/values.yaml`\n", "")
        with git_repo({"docs/a.md": page}) as root:
            self.assertEqual(check_docs_staleness.stale_pages(root), [])

    def test_a_source_that_no_longer_exists_is_reported(self):
        page = PAGE.replace("cluster/helm/backup/values.yaml", "cluster/helm/gone/values.yaml")
        with git_repo({"docs/a.md": page}) as root:
            self.assertEqual(
                check_docs_staleness.stale_pages(root),
                [(root / "docs/a.md", "cluster/helm/gone/values.yaml", "missing")],
            )

    def test_a_glob_source_is_not_dated(self):
        page = PAGE.replace("cluster/helm/backup/values.yaml", "cluster/helm/**/templates/*.yaml")
        with git_repo({"docs/a.md": page}) as root:
            self.assertEqual(check_docs_staleness.stale_pages(root), [])


if __name__ == "__main__":
    unittest.main()
