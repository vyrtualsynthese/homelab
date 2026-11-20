#!/usr/bin/env python3
"""Check that every restic repository is still advancing, and that etcd is shipping with it.

The jobs can all be green while a stream has silently stopped: a row that resolves to
nothing fails its node job, but a repository that receives no snapshot at all fails
nothing until the day it is needed. This script is the one that says so, once a night,
in a line Loki can index:

    stream=<name> snapshots=<n> bytes=<b> newest=<iso8601|none> age_h=<h|->
    etcd stream=<name> file=<basename> age_h=<h>

Exits non-zero when a stream is empty, older than 26 hours, or larger than its budget —
and when a control-plane stream's etcd copy is older than the same window, because the
copy is the only part of the etcd story this cluster can prove today.
"""

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

STALE_AFTER = timedelta(hours=26)
# The node job hands restic the value of etcdSnapshotsPath, so the paths stored in the
# snapshot start there; /etc/rancher/k3s is mounted at /host/etc/rancher/k3s for the
# same reason.
ETCD_PREFIX = "/host/etcd-snapshots/"
GIB = 1024**3


def parse_time(value: str) -> datetime:
    """Parse a restic timestamp, whose fraction may have more than six digits."""
    text = value.strip()
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        rest = tail
        for index, char in enumerate(tail):
            if not char.isdigit():
                digits, rest = tail[:index], tail[index:]
                break
        else:
            digits, rest = tail, ""
        text = f"{head}.{digits[:6]}{rest}"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def newest_snapshot(snapshots: list[dict]) -> dict | None:
    if not snapshots:
        return None
    return max(snapshots, key=lambda snap: parse_time(snap["time"]))


def hours(value: timedelta) -> float:
    return round(value.total_seconds() / 3600, 1)


def stream_line(name: str, snapshots: list[dict], bytes_used: int, now: datetime) -> str:
    newest = newest_snapshot(snapshots)
    if newest is None:
        return f"stream={name} snapshots=0 bytes={bytes_used} newest=none age_h=-"
    when = parse_time(newest["time"])
    return (
        f"stream={name} snapshots={len(snapshots)} bytes={bytes_used} "
        f"newest={when:%Y-%m-%dT%H:%M:%SZ} age_h={hours(now - when)}"
    )


def check_stream(name: str, snapshots: list[dict], bytes_used: int, budget_bytes: int,
                 now: datetime) -> list[str]:
    newest = newest_snapshot(snapshots)
    if newest is None:
        return ["no snapshot"]
    problems = []
    age = now - parse_time(newest["time"])
    if age > STALE_AFTER:
        problems.append(f"newest snapshot {hours(age)}h old (>26h)")
    if bytes_used > budget_bytes:
        problems.append(
            f"repository over budget {bytes_used / GIB:.1f} GiB > {budget_bytes / GIB:.1f} GiB"
        )
    return problems


def newest_etcd(entries: list[dict], now: datetime) -> dict | None:
    """The newest file the nightly copy left under /host/etcd-snapshots/."""
    files = [
        entry for entry in entries
        if entry.get("path", "").startswith(ETCD_PREFIX)
        and entry.get("type", "file") != "dir"
        and "@" not in entry.get("path", "")  # a failed copy leaves a partial file
        and entry.get("mtime")
    ]
    if not files:
        return None
    newest = max(files, key=lambda entry: parse_time(entry["mtime"]))
    return {
        "file": pathlib.PurePosixPath(newest["path"]).name,
        "age_h": hours(now - parse_time(newest["mtime"])),
    }


def check_etcd(name: str, newest: dict | None, now: datetime) -> list[str]:
    if newest is None:
        return ["no etcd snapshot"]
    if newest["age_h"] > STALE_AFTER.total_seconds() / 3600:
        return [f"etcd copy {newest['age_h']:.1f}h old (>26h)"]
    return []


def run_restic(repository: str, *args: str, restic: str = "restic") -> str:
    done = subprocess.run(
        [restic, "-r", repository, *args],
        check=False, capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise RuntimeError(f"restic {args[0]} {repository}: {done.stderr.strip()}")
    return done.stdout


def read_repository(repository: str, restic: str = "restic") -> tuple[list[dict], int]:
    snapshots = json.loads(run_restic(repository, "snapshots", "--json", "--no-lock", restic=restic))
    stats = json.loads(
        run_restic(repository, "stats", "--json", "--mode", "raw-data", "--no-lock", restic=restic)
    )
    return snapshots, int(stats.get("total_size", 0))


def read_etcd(repository: str, restic: str = "restic") -> list[dict]:
    text = run_restic(repository, "ls", "--json", "latest", "--no-lock", restic=restic)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/config/streams.json")
    parser.add_argument("--restic", default="restic", help="the restic binary to run")
    args = parser.parse_args(argv)

    streams = json.loads(pathlib.Path(args.config).read_text())
    now = datetime.now(timezone.utc)
    failures = 0
    for stream in streams:
        name = stream["name"]
        repository = stream["repository"]
        budget = int(stream["budgetGiB"] * GIB)
        snapshots, used = read_repository(repository, restic=args.restic)
        print(stream_line(name, snapshots, used, now), flush=True)

        problems = check_stream(name, snapshots, used, budget, now)
        if stream.get("etcd"):
            newest = newest_etcd(read_etcd(repository, restic=args.restic), now)
            if newest is None:
                print(f"etcd stream={name} file=- age_h=-", flush=True)
            else:
                print(f"etcd stream={name} file={newest['file']} age_h={newest['age_h']}", flush=True)
            problems.extend(check_etcd(name, newest, now))

        for problem in problems:
            print(f"problem stream={name} {problem}", flush=True)
        failures += len(problems)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
