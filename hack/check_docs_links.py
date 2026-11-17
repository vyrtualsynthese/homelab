#!/usr/bin/env python3
"""Check that every relative Markdown link in this repository resolves on disk.

Run from the repository root:

    python3 hack/check_docs_links.py

Every `*.md` file is walked, except the agent artifacts under `docs/superpowers/` (their
placeholders are intentional), the gitignored SDD scratch workspace `.superpowers/` (its briefs and
reports hold link-shaped examples), `.git`, `node_modules` and `.venv`. A link whose target is
absolute (`http://`, `https://`, `mailto:`, `tel:`) or a bare anchor (`#...`) is skipped;
anything else is resolved against the directory of the file that contains it.

A target may point outside `docs/`. That is deliberate: it is what keeps the chart READMEs and
`AGENTS.md` honest about the paths they cite.

Exit status is 1 when at least one link does not resolve, 0 otherwise. Every failure is printed
as `<source>:<line>: <target>`, one per line, and the successes end with
`0 broken relative links`.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = (".git", "node_modules", ".venv")
# `docs/superpowers` is agent-written documentation; `.superpowers` is the gitignored SDD scratch
# workspace, whose briefs and reports hold link-shaped examples that are not documentation.
SKIP_PATHS = ("docs/superpowers", ".superpowers")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#")

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _skipped(rel_path: str) -> bool:
    if any(path in rel_path for path in SKIP_PATHS):
        return True
    return any(part in SKIP_DIRS for part in rel_path.split("/"))


def markdown_files(root: str):
    """Yield every Markdown file under root that is not in a skipped directory."""
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(
            d for d in dirnames if not _skipped(f"{rel_dir}/{d}".lstrip("/"))
        )
        for name in sorted(filenames):
            if name.endswith(".md"):
                yield f"{rel_dir}/{name}".lstrip("/")


def check_file(root: str, rel_path: str) -> list[str]:
    """Return one `<source>:<line>: <target>` message per broken relative link."""
    errors: list[str] = []
    source_dir = os.path.dirname(os.path.join(root, rel_path))
    with open(os.path.join(root, rel_path), encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            for target in LINK_RE.findall(line):
                if target.startswith(SKIP_PREFIXES):
                    continue
                path = target.split("#", 1)[0].split("?", 1)[0]
                if not path:
                    continue
                if not os.path.exists(os.path.normpath(os.path.join(source_dir, path))):
                    errors.append(f"{rel_path}:{number}: {target}")
    return errors


def main() -> int:
    errors: list[str] = []
    sources: set[str] = set()
    for rel_path in markdown_files(ROOT):
        found = check_file(ROOT, rel_path)
        if found:
            sources.add(rel_path)
            errors.extend(found)

    for error in errors:
        print(error)

    if errors:
        print(f"{len(errors)} broken relative link(s) in {len(sources)} file(s)")
        return 1

    print("0 broken relative links")
    return 0


if __name__ == "__main__":
    sys.exit(main())
