"""Report documentation pages older than the files they were written from.

A page names its sources in the opening blockquote. This script compares the page's `Verified` date
with the last commit date of each source file, and prints the pairs where the source moved later. It
never fails: the finding is work for the hardening pass, which files it as a card rather than editing
the page here.

    python3 hack/check_docs_staleness.py

Exit: 0 always.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = ("docs/superpowers/",)
BACKTICK_RE = re.compile(r"`([^`]+)`")
VERIFIED_RE = re.compile(r"\*\*Verified\*\*\s*(\d{4}-\d{2}-\d{2})")
SOURCES_RE = re.compile(r"\*\*Sources\*\*")
KIND_RE = re.compile(r"\*\*Kind:\*\*\s*([^*·\n]+)")


def _blockquote(text: str) -> list[str]:
    """The opening blockquote, line by line, without the leading `>`."""
    lines = text.splitlines()
    block: list[str] = []
    started = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            started = True
            block.append(stripped.lstrip("> ").strip())
            continue
        if started:
            break
    return block


def _looks_like_a_path(token: str) -> bool:
    if "*" in token or " " in token or "<" in token:
        return False
    if token.startswith(("~", "/", "http")):
        return False
    return "/" in token or token.endswith((".md", ".py", ".sh", ".yaml", ".yml", ".json"))


def sources_of(page: pathlib.Path) -> list[str]:
    """The backticked paths on the Sources line and its continuation lines.

    A continuation line is one that starts with a backtick, which is how a long Sources list is
    wrapped; prose that follows on the same line is not a source.
    """
    lines = _blockquote(page.read_text(encoding="utf-8"))
    found: list[str] = []
    collecting = False
    for line in lines:
        if SOURCES_RE.search(line):
            collecting = True
        elif collecting and not line.startswith("`"):
            break
        if not collecting:
            continue
        for candidate in BACKTICK_RE.findall(line):
            if _looks_like_a_path(candidate) and candidate not in found:
                found.append(candidate)
    return found


def kind_of(page: pathlib.Path) -> str | None:
    match = KIND_RE.search(page.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else None


def verified_on(page: pathlib.Path) -> str | None:
    match = VERIFIED_RE.search(page.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def last_commit_date(root: pathlib.Path, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%cs", "--", path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    date = result.stdout.strip()
    return date or None


def stale_pages(root: pathlib.Path) -> list[tuple[pathlib.Path, str, str]]:
    """(page, source, source date) for every page a source has outrun, oldest fact first."""
    stale: list[tuple[pathlib.Path, str, str]] = []
    for page in sorted((root / "docs").rglob("*.md")):
        relative = page.relative_to(root).as_posix()
        if relative.startswith(SKIP):
            continue
        if kind_of(page) == "record":
            continue  # a record is never refreshed, so it cannot be stale
        verified = verified_on(page)
        if not verified:
            continue
        for source in sources_of(page):
            if not (root / source).exists():
                stale.append((page, source, "missing"))
                continue
            date = last_commit_date(root, source)
            if date and date > verified:
                stale.append((page, source, date))
    return stale


def main(argv: list[str] | None = None) -> int:
    root = pathlib.Path(argv[0]).resolve() if argv else ROOT
    stale = stale_pages(root)
    for page, source, date in stale:
        relative = page.relative_to(root).as_posix()
        if date == "missing":
            print(f"{relative}: source `{source}` does not exist")
            continue
        print(f"{relative}: verified {verified_on(page)}, `{source}` changed {date}")
    print(f"{len(stale)} stale source(s); this report never fails")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
