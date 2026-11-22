"""Check the documentation rules that a script can hold.

Five checks, each returning a list of `"<path>: <message>"` problems:

    references   every `docs/*.md` path cited anywhere in the repository exists
    nav          every page under `docs/` is in `mkdocs.yml`, and every entry resolves

    hack/check_docs.py                       every check
    hack/check_docs.py --only references

Exit: 0 clean, 1 problems.
"""

from __future__ import annotations

import argparse
import fnmatch
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
REFERENCE_RE = re.compile(r"(?<![\w/.-])docs/[A-Za-z0-9_./-]+\.md")
SKIP_DIRS = {".git", "node_modules", ".venv", ".superpowers", "site"}
SKIP_PATHS = ("docs/superpowers/", "hack/fixtures/", "hack/tests/")


def _files(root: pathlib.Path):
    """Every text file under root, minus the skipped directories and agent artifacts."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in relative.split("/")):
            continue
        if any(relative.startswith(prefix) for prefix in SKIP_PATHS):
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def reference_problems(root: pathlib.Path) -> list[str]:
    """Every `docs/` path a file cites must resolve, whatever kind of file cites it."""
    problems: list[str] = []
    for path, text in _files(root):
        relative = path.relative_to(root).as_posix()
        seen: set[str] = set()
        for match in REFERENCE_RE.finditer(text):
            target = match.group(0)
            if target in seen:
                continue
            seen.add(target)
            if not (root / target).exists():
                problems.append(f"{relative}: {target}")
    return problems


def _nav_targets(node) -> list[str]:
    """Flatten a nav tree to the strings that are paths; URLs and sections are dropped."""
    targets: list[str] = []
    if isinstance(node, str):
        if not node.startswith(("http://", "https://")):
            targets.append(node.lstrip("/"))
        return targets
    if isinstance(node, list):
        for item in node:
            targets.extend(_nav_targets(item))
        return targets
    if isinstance(node, dict):
        for value in node.values():
            targets.extend(_nav_targets(value))
    return targets


class _Loader(yaml.SafeLoader):
    """SafeLoader that tolerates MkDocs-only tags such as `!!python/name:pymdownx...`."""


_Loader.add_multi_constructor("tag:yaml.org,2002:python/name:", lambda loader, suffix, node: suffix)


def nav_problems(root: pathlib.Path) -> list[str]:
    """The nav is the reading order: a page it does not list, or an entry with no page, is a defect."""
    config = yaml.load((root / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_Loader) or {}
    docs = root / "docs"
    targets = _nav_targets(config.get("nav") or [])
    omitted = config.get("not_in_nav")
    omitted_patterns = omitted.splitlines() if isinstance(omitted, str) else []

    problems: list[str] = []
    listed: set[pathlib.Path] = set()
    for target in targets:
        path = docs / target
        if not path.is_file():
            problems.append(f"mkdocs.yml: {target} does not exist")
            continue
        listed.add(path.resolve())
    for path in sorted(docs.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(SKIP_PATHS[0]):
            continue
        if any(fnmatch.fnmatch(target, pattern.strip()) for pattern in omitted_patterns if pattern.strip()):
            continue
        if path.resolve() not in listed:
            problems.append(f"{relative}: no nav entry")
    return problems


CHECKS = {
    "references": reference_problems,
    "nav": nav_problems,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=sorted(CHECKS), help="one check")
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    names = [args.only] if args.only else sorted(CHECKS)
    problems: list[str] = []
    for name in names:
        problems.extend(CHECKS[name](root))
    for problem in problems:
        print(problem)
    print(f"{len(problems)} documentation problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
