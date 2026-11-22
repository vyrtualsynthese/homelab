#!/usr/bin/env python3
"""Stage a node's claim rows for restic.

Runs as the initContainer of a ``backup-node-*`` job, where the node's local-path roots
and the claim directories are mounted read-only. It does two things the shell cannot:

* resolve each row in ``rows.json`` to exactly one claim directory, refusing to guess when
  a released PV left a second directory with the same name behind;
* copy every SQLite database with the ``sqlite3`` module's backup API, which produces a
  consistent snapshot of a database that is being written, unlike ``cp``.

Everything it writes lands in the staging directory, and the two files it leaves there are
what ``backup-node.sh`` hands to restic: the paths to read and the patterns to skip. A third
file, the rows' unmatched exclusions, is not read by this job at all: it sits inside the tree
restic backs up so the nightly verifier, which never sees a claim, can report it.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import pathlib
import shutil
import sqlite3
import sys
from typing import Sequence

DEFAULT_DB_PATTERNS = ("*.db", "*.sqlite", "*.sqlite3")
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
# Where the rows' unmatched exclusions are written, inside the tree restic backs up: the
# verifier reads snapshots and never a claim, so this file is how it hears about them.
EXCLUDE_REPORT = "exclude-unused.json"


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
    scratch: pathlib.Path | None = None
    try:
        try:
            # The clean path: a read-only open writes nowhere, and the backup API reads the
            # database as it stands.
            live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            live.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.OperationalError:
            # A WAL database cannot be opened read-only unless its -shm exists and can be
            # updated, and every claim is mounted read-only on purpose. So the copy is taken
            # into the staging directory, where SQLite may create the side files and replay the
            # WAL: a torn tail is discarded, a committed frame is applied.
            live, scratch = _open_copy(source, destination)
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
    finally:
        if scratch is not None:
            for suffix in ("", *SIDECAR_SUFFIXES):
                pathlib.Path(f"{scratch}{suffix}").unlink(missing_ok=True)


def _open_copy(
    source: pathlib.Path, destination: pathlib.Path
) -> tuple[sqlite3.Connection, pathlib.Path]:
    """Copy a database and its side files into the writable staging directory, then open it."""
    scratch = destination.parent / f".{destination.name}.copy"
    for suffix in SIDECAR_SUFFIXES:
        side = pathlib.Path(f"{source}{suffix}")
        if side.exists():
            shutil.copy2(side, f"{scratch}{suffix}")
    shutil.copy2(source, scratch)
    live = sqlite3.connect(scratch)
    live.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return live, scratch


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

    A claim that holds no matching database yet returns an empty list instead of raising. That
    is a freshly installed application, not a broken setup: the claim is still backed up as it
    stands, and failing the node's job for a database that does not exist would report a
    working night as a broken backup. A database that *is* there and cannot be staged is the
    opposite case and raises: it is data, and silence about it is how a backup stops being one.
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
        return []

    written = []
    for live in unique:
        staged = destination / live.relative_to(source)
        staged.parent.mkdir(parents=True, exist_ok=True)
        _copy_sqlite(live, staged)
        print(f"staged {live} -> {staged}")
        written.append(staged)
    return written


def matches(relative: pathlib.Path, pattern: str) -> bool:
    """Whether a claim-relative path is covered by one of the row's exclude entries.

    The same reading as ``_absolute_patterns`` gives restic, so the two halves of a row agree:
    a pattern without a slash is a name and matches a path component at any depth; a pattern
    with a slash is relative to the claim root.
    """
    trimmed = pattern.rstrip("/")
    if "/" in trimmed:
        text = str(relative)
        return fnmatch.fnmatch(text, trimmed) or text.startswith(f"{trimmed}/")
    return any(fnmatch.fnmatch(part, trimmed) for part in relative.parts)


def matches_any(relative: pathlib.Path, patterns: Sequence[str]) -> bool:
    """Whether a claim-relative path is covered by any of the row's restic patterns."""
    return any(matches(relative, pattern) for pattern in patterns)


def unused_excludes(claim_dir: pathlib.Path, patterns: Sequence[str]) -> list[str]:
    """The row's exclude entries that match nothing under its claim, in the order written.

    An entry that matches nothing is a line doing nothing while the row reads as if it did: the
    exclusions are what keep 6.73 GiB of Plex's poster art, transcoder cache and dated database
    copies out of the stream, and from 2026-11-20 to 2026-11-22 none of them matched, because
    they were anchored at the claim root and Plex keeps its tree three levels down (#447).
    Nothing failed, because nothing looked.

    The walk stops at the first path that satisfies the last unmatched entry, so a row whose
    entries all match costs a directory listing rather than a full traversal, and a row with an
    unmatched entry costs one pass over the claim.
    """
    pending = [pattern for pattern in patterns if pattern.rstrip("/")]
    if not pending:
        return []
    for dirpath, dirnames, filenames in os.walk(claim_dir):
        base = pathlib.Path(dirpath)
        for name in (*dirnames, *filenames):
            relative = (base / name).relative_to(claim_dir)
            pending = [p for p in pending if not matches(relative, p)]
            if not pending:
                return []
    return pending


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
    """Turn a row's relative exclude into patterns restic matches against the full path.

    A pattern without a slash is a *name*, and it is excluded at any depth. That is the reading
    the rows are written in — ``Cache/`` means Plex's cache, not only a directory at the claim
    root — and it is what the 2026-11-20 matrix measured. The anchored-only reading this
    replaced matched nothing for Plex, whose whole tree sits under ``Library/Application
    Support/Plex Media Server/``: 6.73 GiB of poster art, transcoder cache and Plex's own
    database copies rode in the stream from the first snapshot until 2026-11-22.

    A pattern that contains a slash is a claim-relative path and stays anchored at the root,
    because a path written out is a path, not a name to be found anywhere.

    ``restic`` needs both the directory and ``/**``: the job lists explicit paths, so a
    directory entry that matches does not by itself drop the entries beneath it.
    """
    trimmed = pattern.rstrip("/")
    if "/" in trimmed:
        joined = f"{base}/{trimmed}"
        return [joined, f"{joined}/**"]
    return [
        f"{base}/{trimmed}",
        f"{base}/{trimmed}/**",
        f"{base}/**/{trimmed}",
        f"{base}/**/{trimmed}/**",
    ]


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
    unused: list[dict] = []
    for row in config.get("claims", []):
        label = f"{row['namespace']}/{row['claim']}"
        try:
            claim_dir = resolve_claim(roots, row["namespace"], row["claim"], pv=row.get("pv"))
        except RowError as exc:
            failures.append(str(exc))
            print(f"row-error {exc}", file=sys.stderr)
            continue

        # A warning, not an error: a freshly installed application matches nothing either, and
        # an exclusion that matches nothing costs space rather than data — the stream budget is
        # what bounds that. See unused_excludes for the night this was written for.
        for pattern in unused_excludes(claim_dir, row.get("exclude") or []):
            print(f"exclude-unused row={label} pattern={pattern}")
            unused.append({"row": label, "pattern": pattern})

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
            if not written:
                # Not a failure: the claim exists and is backed up whole, minus the databases
                # the row would exclude. See stage_databases for why a missing one is tolerated
                # and an unreadable one is not.
                print(
                    f"row-warning {label}: no database matched {', '.join(patterns)} in "
                    f"{claim_dir} yet; the claim is backed up without a staged copy"
                )

        resolved.append(entry)
        print(f"row {label} dir={claim_dir} kind={row.get('kind', 'files')}")

    present, missing = host_paths(args.host_path or config.get("hostPaths") or [])
    for path in missing:
        print(f"host-path missing: {path}")

    # Written even when every entry matched: the verifier cannot tell an old snapshot, which has
    # no file, from a night whose entries all matched, which has this one with an empty list.
    (claims_dir / EXCLUDE_REPORT).write_text(json.dumps(unused) + "\n")

    paths_file, exclude_file = write_restic_inputs(resolved, staging, present)
    print(f"restic-paths={paths_file} exclude={exclude_file} rows={len(resolved)}")

    if failures:
        print(f"row-errors={len(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
