#!/usr/bin/env python3
"""Stage a node's claim rows for restic.

Runs as the initContainer of a ``backup-node-*`` job, where the node's local-path roots
and the claim directories are mounted read-only. It does two things the shell cannot:

* resolve each row in ``rows.json`` to exactly one claim directory, refusing to guess when
  a released PV left a second directory with the same name behind;
* copy every SQLite database with the ``sqlite3`` module's backup API, which produces a
  consistent snapshot of a database that is being written, unlike ``cp``.

Everything it writes lands in the staging directory, and the two files it leaves there are
what ``backup-node.sh`` hands to restic: the paths to read and the patterns to skip.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import pathlib
import sqlite3
import sys
from typing import Sequence

DEFAULT_DB_PATTERNS = ("*.db", "*.sqlite", "*.sqlite3")
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class RowError(RuntimeError):
    """A row in values.yaml that the live cluster does not match."""


def _roots(roots: Sequence[pathlib.Path]) -> str:
    return ", ".join(str(r) for r in roots)


def resolve_claim(
    roots: Sequence[pathlib.Path],
    namespace: str,
    claim: str,
    pv: str | None = None,
) -> pathlib.Path:
    """Return the one directory holding ``namespace/claim``.

    The local-path provisioner names the directory ``<pvName>_<namespace>_<pvcName>``, so the
    suffix identifies the claim. A row that names its ``pv`` is matched exactly; without it,
    exactly one directory must match, and two is an error rather than a coin toss.
    """
    suffix = f"_{namespace}_{claim}"
    if pv:
        expected = f"{pv}{suffix}"
        for root in roots:
            candidate = root / expected
            if candidate.is_dir():
                return candidate
        raise RowError(f"row {namespace}/{claim}: pv {pv} not found under {_roots(roots)}")

    matches = sorted(p for root in roots for p in root.glob(f"*{suffix}") if p.is_dir())
    if not matches:
        raise RowError(f"row {namespace}/{claim}: no directory matching *{suffix} under {_roots(roots)}")
    if len(matches) > 1:
        found = ", ".join(str(m) for m in matches)
        raise RowError(f"row {namespace}/{claim}: {len(matches)} directories match ({found}); set pv")
    return matches[0]


def _copy_sqlite(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Copy one database through SQLite's backup API, never with a byte copy."""
    if destination.exists():
        destination.unlink()
    try:
        try:
            live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            live.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.OperationalError:
            # A WAL database on a read-only mount can refuse the read-only open. The fallback is
            # a read-write open of the same file, which works when the mount itself is writable;
            # on a read-only mount it raises, which is a RowError and fails the job loudly.
            try:
                live.close()
            except (NameError, sqlite3.Error):
                pass
            live = sqlite3.connect(f"file:{source}", uri=True)
            live.execute("SELECT count(*) FROM sqlite_master").fetchone()
        try:
            copy = sqlite3.connect(destination)
            try:
                live.backup(copy)
            finally:
                copy.close()
        finally:
            live.close()
    except sqlite3.DatabaseError as exc:
        raise RowError(f"cannot stage {source}: {exc}") from exc


def stage_databases(
    source: pathlib.Path,
    destination: pathlib.Path,
    patterns: Sequence[str] = DEFAULT_DB_PATTERNS,
    exclude: Sequence[str] = (),
) -> list[pathlib.Path]:
    """Stage every database under ``source`` matching ``patterns``, keeping relative paths.

    A database the row excludes — ``logs.db`` for the *arr stack — is not staged either: the
    row's exclusion is the single statement of what is not backed up, and staging it anyway
    would quietly defeat it.
    """
    found: list[pathlib.Path] = []
    for pattern in patterns:
        for candidate in sorted(source.glob(pattern)):
            # A pattern wide enough to catch a database's side files (``db/*.sqlite*`` matches
            # ``db.sqlite-wal``) must not stage them: they are not databases, and copying one
            # raises "file is not a database" and fails the whole node job.
            if not candidate.is_file() or candidate.name.endswith(SIDECAR_SUFFIXES):
                continue
            found.append(candidate)
    unique = [p for p in dict.fromkeys(found) if not matches_any(p.relative_to(source), exclude)]
    if not unique:
        raise RowError(f"no database matched {', '.join(patterns)} in {source}")

    written = []
    for live in unique:
        staged = destination / live.relative_to(source)
        staged.parent.mkdir(parents=True, exist_ok=True)
        _copy_sqlite(live, staged)
        print(f"staged {live} -> {staged}")
        written.append(staged)
    return written


def matches_any(relative: pathlib.Path, patterns: Sequence[str]) -> bool:
    """Whether a claim-relative path is covered by one of the row's restic patterns."""
    for pattern in patterns:
        trimmed = pattern.rstrip("/")
        if fnmatch.fnmatch(relative.name, trimmed) or fnmatch.fnmatch(str(relative), trimmed):
            return True
        if relative.parts and fnmatch.fnmatch(relative.parts[0], trimmed):
            return True
    return False


def exclude_patterns(source: pathlib.Path, database_names: Sequence[str]) -> list[str]:
    """Absolute patterns that keep restic away from a live database and its side files."""
    patterns: list[str] = []
    for name in database_names:
        live = source / name
        patterns.append(str(live))
        for suffix in ("-wal", "-shm", "-journal"):
            patterns.append(f"{live}{suffix}")
    return patterns


def _absolute_patterns(base: pathlib.Path, pattern: str) -> list[str]:
    """Turn a row's relative exclude into patterns restic matches against the full path."""
    trimmed = pattern.rstrip("/")
    joined = f"{base}/{trimmed}"
    return [joined, f"{joined}/**"]


def host_paths(candidates: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split host configuration paths into the ones this node has and the ones it lacks."""
    present, missing = [], []
    for candidate in candidates:
        (present if pathlib.Path(candidate).exists() else missing).append(candidate)
    return present, missing


def write_restic_inputs(
    rows: list[dict],
    staging: pathlib.Path,
    host_paths: Sequence[str] = (),
) -> tuple[pathlib.Path, pathlib.Path]:
    """Write the path list and the exclude list that ``backup-node.sh`` passes to restic."""
    staged = staging / "claims"
    staged.mkdir(parents=True, exist_ok=True)
    paths: list[str] = [str(staged)]
    excludes: list[str] = []

    for row in rows:
        claim_dir = pathlib.Path(row["dir"])
        paths.append(str(claim_dir))
        for pattern in row.get("exclude") or []:
            excludes.extend(_absolute_patterns(claim_dir, pattern))
        for pattern in row.get("db_patterns") or []:
            # Every database in a sqlite row is excluded from the claim copy, staged or not:
            # the staged files are the consistent source, and a live db must never be read raw.
            in_claim = sorted(p for p in claim_dir.glob(pattern) if p.is_file())
            excludes.extend(exclude_patterns(claim_dir, [str(p.relative_to(claim_dir)) for p in in_claim]))

    paths.extend(host_paths)

    paths_file = staging / "restic-paths.txt"
    exclude_file = staging / "exclude.txt"
    paths_file.write_text("\n".join(dict.fromkeys(paths)) + "\n")
    exclude_file.write_text("\n".join(dict.fromkeys(excludes)) + "\n")
    return paths_file, exclude_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="stage a backup job's rows for restic")
    parser.add_argument("--rows", required=True, help="rows.json rendered by the chart")
    parser.add_argument("--staging", required=True, help="writable staging directory")
    parser.add_argument("--root", action="append", default=[], help="local-path root, repeatable")
    parser.add_argument("--host-path", action="append", default=[], help="host path to include, repeatable")
    args = parser.parse_args(argv)

    config = json.loads(pathlib.Path(args.rows).read_text())
    roots = [pathlib.Path(r) for r in (args.root or config.get("roots") or [])]
    staging = pathlib.Path(args.staging)
    claims_dir = staging / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)

    # Proves the module is in the image, which is not obvious for a distroless-looking tag.
    print(f"sqlite_version={sqlite3.sqlite_version}")

    resolved: list[dict] = []
    failures: list[str] = []
    for row in config.get("claims", []):
        label = f"{row['namespace']}/{row['claim']}"
        try:
            claim_dir = resolve_claim(roots, row["namespace"], row["claim"], pv=row.get("pv"))
        except RowError as exc:
            failures.append(str(exc))
            print(f"row-error {exc}", file=sys.stderr)
            continue

        entry = dict(row)
        entry["dir"] = str(claim_dir)
        entry["databases"] = []
        if row.get("kind") == "sqlite":
            patterns = row.get("db") or list(DEFAULT_DB_PATTERNS)
            destination = claims_dir / row["namespace"] / row["claim"]
            destination.mkdir(parents=True, exist_ok=True)
            try:
                written = stage_databases(claim_dir, destination, patterns, row.get("exclude") or ())
            except RowError as exc:
                failures.append(str(exc))
                print(f"row-error {exc}", file=sys.stderr)
                continue
            entry["db_patterns"] = list(patterns)
            entry["databases"] = [str(p.relative_to(destination)) for p in written]

        resolved.append(entry)
        print(f"row {label} dir={claim_dir} kind={row.get('kind', 'files')}")

    present, missing = host_paths(args.host_path or config.get("hostPaths") or [])
    for path in missing:
        print(f"host-path missing: {path}")

    paths_file, exclude_file = write_restic_inputs(resolved, staging, present)
    print(f"restic-paths={paths_file} exclude={exclude_file} rows={len(resolved)}")

    if failures:
        print(f"row-errors={len(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
