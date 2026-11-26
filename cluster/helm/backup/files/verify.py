#!/usr/bin/env python3
"""Check that every restic repository is still advancing, and that every server is shipping etcd.

The jobs can all be green while a stream has silently stopped: a row that resolves to
nothing fails its node job, but a repository that receives no snapshot at all fails
nothing until the day it is needed. This script is the one that says so, once a night,
in a line Loki can index:

    stream=<name> snapshots=<n> bytes=<b> newest=<iso8601|none> age_h=<h|->
    etcd-s3 server=<name> file=<basename> age_h=<h>
    staged stream=<name> row=<namespace>/<claim> files=<n>
    files stream=<name> row=<namespace>/<claim> matched=<n>
    files stream=<name> path=<pattern> matched=<n>
    exclude-unused stream=<name> row=<namespace>/<claim> pattern=<entry>

Exits non-zero when a stream is empty, older than 26 hours, or larger than its budget, when a
control-plane server has no etcd snapshot in the bucket or one older than the same window,
when a `sqlite` row has staged no database into the stream's newest snapshot, which is what a
`db:` pattern that went stale looks like from outside: the job stays green and the copy the row
exists for is gone, when a `files` row that declares `mustMatch` holds nothing matching it,
which is a dump CronJob that stopped while its claim directory stayed, and when an `external`
stream's newest snapshot holds nothing matching one of its `mustMatch` paths, which is a night
that staged a fragment.

The etcd half is not read from a restic snapshot. Each server uploads its own snapshots to the
bucket's `etcd/` prefix, and K3s's reconcile publishes one `ETCDSnapshotFile` per object, naming
the server that took it. Those objects are the proof: one is asked of every control-plane node,
and a server whose upload stopped stops appearing in the list, which is exactly the fault the
restic copy of the snapshot directory used to catch.

It also repeats the node jobs' `exclude-unused` lines — a row's exclusion that matched nothing
in its claim. Those are warnings and do not fail the run: an application installed and not yet
started matches nothing either, and an exclusion that matches nothing costs space rather than
data, which the budget check is what bounds. The job cannot check them itself — it reads
snapshots and never a claim — so it reports the answer `prepare.py` wrote into the snapshot.
The `nfs` stream has no prepare step: its job counts its own words and prints the line into its
own log, so there is nothing here to read for that stream.
"""

import argparse
import fnmatch
import json
import pathlib
import ssl
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

STALE_AFTER = timedelta(hours=26)
# Where the pod's service account is mounted. The request has to carry its token and trust the
# cluster CA, because the verifier is not a kubectl and the image carries no client binary.
SERVICE_ACCOUNT = pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount")
API = "https://kubernetes.default.svc"
# prepare.py is given `--staging /staging` and hands restic `<staging>/claims`, so a staged
# database is stored under this absolute prefix, one directory per row.
STAGED_PREFIX = "/staging/claims/"
# The same job writes the rows' unmatched exclusions here, beside the staged databases, because
# this script is given no claim to look at.
EXCLUDE_REPORT = f"{STAGED_PREFIX}exclude-unused.json"
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


def stream_snapshots(snapshots: list[dict], name: str) -> list[dict]:
    """The snapshots the nightly job wrote for this stream.

    `latest` is not it: an etcd copy taken by hand between two nights lands in the same
    repository with a later timestamp and tags of its own, and holds none of the stream's
    claims. The nightly job tags every snapshot `stream=<name>`, which is what tells them
    apart.
    """
    return [snap for snap in snapshots if f"stream={name}" in (snap.get("tags") or [])]


def stream_snapshot(snapshots: list[dict], name: str) -> dict | None:
    return newest_snapshot(stream_snapshots(snapshots, name))


def hours(value: timedelta) -> float:
    return round(value.total_seconds() / 3600, 1)


def stream_line(name: str, snapshots: list[dict], bytes_used: int, now: datetime) -> str:
    nightly = stream_snapshots(snapshots, name)
    newest = newest_snapshot(nightly)
    if newest is None:
        return f"stream={name} snapshots=0 bytes={bytes_used} newest=none age_h=-"
    when = parse_time(newest["time"])
    return (
        f"stream={name} snapshots={len(nightly)} bytes={bytes_used} "
        f"newest={when:%Y-%m-%dT%H:%M:%SZ} age_h={hours(now - when)}"
    )


def check_stream(name: str, snapshots: list[dict], bytes_used: int, budget_bytes: int,
                 now: datetime) -> list[str]:
    newest = stream_snapshot(snapshots, name)
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


def api_get(path: str) -> dict:
    """One authenticated GET against the cluster's API, as the pod's service account."""
    token = (SERVICE_ACCOUNT / "token").read_text().strip()
    context = ssl.create_default_context(cafile=str(SERVICE_ACCOUNT / "ca.crt"))
    request = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, context=context, timeout=60) as response:
        return json.load(response)


def read_etcd_s3() -> tuple[list[str], list[dict]]:
    """Every control-plane server, and every etcd snapshot K3s has published.

    The servers are read from the nodes rather than from a list beside this script, so a
    fourth one that joins is named by the check instead of being believed. The snapshots are
    `ETCDSnapshotFile` objects: K3s's reconcile writes one for every object it finds in the
    bucket, so a server whose upload stopped stops appearing in the list.
    """
    nodes = api_get("/api/v1/nodes").get("items") or []
    servers = sorted(
        node["metadata"]["name"] for node in nodes
        if "node-role.kubernetes.io/control-plane" in (node["metadata"].get("labels") or {})
    )
    snapshots = api_get("/apis/k3s.cattle.io/v1/etcdsnapshotfiles").get("items") or []
    return servers, snapshots


def newest_s3_snapshot(server: str, snapshots: list[dict], now: datetime) -> dict | None:
    """The newest snapshot one server has uploaded, as its `ETCDSnapshotFile` reports it."""
    found = []
    for entry in snapshots:
        spec = entry.get("spec") or {}
        status = entry.get("status") or {}
        if not str(spec.get("location", "")).startswith("s3://"):
            continue
        if spec.get("nodeName") != server or not status.get("readyToUse"):
            continue
        if (entry.get("metadata") or {}).get("deletionTimestamp"):
            continue  # mid-delete: it is not this server's newest any more
        created = status.get("creationTime")
        if not created:
            continue
        found.append({
            "file": pathlib.PurePosixPath(spec.get("snapshotName", "")).name,
            "age_h": hours(now - parse_time(created)),
        })
    return min(found, key=lambda item: item["age_h"]) if found else None


def check_etcd_s3(
    servers: list[str], snapshots: list[dict], now: datetime
) -> tuple[list[str], list[str]]:
    """One line per server, and a problem for each whose upload is missing or stale."""
    lines, problems = [], []
    for server in servers:
        newest = newest_s3_snapshot(server, snapshots, now)
        if newest is None:
            lines.append(f"etcd-s3 server={server} file=- age_h=-")
            problems.append(f"etcd-s3 server={server} has no snapshot in the bucket")
            continue
        lines.append(f"etcd-s3 server={server} file={newest['file']} age_h={newest['age_h']}")
        if newest["age_h"] > STALE_AFTER.total_seconds() / 3600:
            problems.append(f"etcd-s3 server={server} snapshot {newest['age_h']:.1f}h old (>26h)")
    return lines, problems


def staged_databases(entries: list[dict], namespace: str, claim: str) -> list[str]:
    """The files a `sqlite` row staged into the snapshot, under claims/<namespace>/<claim>/."""
    prefix = f"{STAGED_PREFIX}{namespace}/{claim}/"
    return [
        entry["path"] for entry in entries
        if entry.get("path", "").startswith(prefix)
        and entry.get("type", "file") != "dir"
    ]


def check_staged(name: str, rows: list[dict], entries: list[dict]) -> tuple[list[str], list[str]]:
    """Every `sqlite` row's staging directory in the stream's newest snapshot must hold a database.

    prepare.py creates the directory before it stages anything, so a row tolerated because
    its claim had no database yet (#416) leaves the directory empty rather than absent. After
    the first night that emptiness is the symptom: a `db:` pattern that upstream moved or
    renamed matches nothing every night while the job stays green.
    """
    lines, problems = [], []
    for row in rows:
        label = f"{row['namespace']}/{row['claim']}"
        files = staged_databases(entries, row["namespace"], row["claim"])
        lines.append(f"staged stream={name} row={label} files={len(files)}")
        if not files:
            problems.append(f"row {label} staged no database")
    return lines, problems


def claim_files(entries: list[dict], namespace: str, claim: str) -> list[str]:
    """The files a `files` row put in the snapshot, under its claim's own directory.

    The local-path provisioner names that directory `<pvName>_<namespace>_<claim>`, and the
    node job hands restic that path, so it is stored as it is. Matching the suffix rather
    than a fixed root is what lets a stream cover more than one local-path root.
    """
    suffix = f"_{namespace}_{claim}"
    found = []
    for entry in entries:
        if entry.get("type", "file") == "dir":
            continue
        parts = pathlib.PurePosixPath(entry.get("path", "")).parts
        if len(parts) > 1 and parts[-2].endswith(suffix):
            found.append(entry["path"])
    return found


def check_files(name: str, rows: list[dict], entries: list[dict]) -> tuple[list[str], list[str]]:
    """A `files` row that declares what it must hold must hold it in the newest snapshot.

    A job whose claim resolved and then emptied — a lost volume, a dump CronJob that stopped
    while the claim directory stayed — leaves every stream green: the repository advances,
    a little smaller each night. A pattern that matches nothing is that night's symptom.
    """
    lines, problems = [], []
    for row in rows:
        label = f"{row['namespace']}/{row['claim']}"
        patterns = row.get("mustMatch") or []
        matched = [
            path for path in claim_files(entries, row["namespace"], row["claim"])
            if any(fnmatch.fnmatch(pathlib.PurePosixPath(path).name, pattern)
                   for pattern in patterns)
        ]
        lines.append(f"files stream={name} row={label} matched={len(matched)}")
        if not matched:
            problems.append(f"row {label} holds no {', '.join(patterns)}")
    return lines, problems


def check_external(name: str, must_match: list[str], entries: list[dict])         -> tuple[list[str], list[str]]:
    """A stream written by a host the cluster does not drive.

    Freshness and budget are all `check_stream` can see, and both pass a night that copied
    a fragment into the staging directory. The paths here are the lower bound the host
    promises every night; a pattern that matches nothing is that night's symptom.
    """
    lines, problems = [], []
    paths = [entry["path"] for entry in entries]
    for pattern in must_match:
        matched = [path for path in paths if path.endswith("/" + pattern)]
        lines.append(f"files stream={name} path={pattern} matched={len(matched)}")
        if not matched:
            problems.append(f"{name} is missing {pattern}")
    return lines, problems


def read_exclude_report(
    repository: str, snapshot_id: str, entries: list[dict], restic: str = "restic"
) -> list[dict]:
    """The unused-exclusion report the node job left in the snapshot, if it left one.

    A claim is never mounted where this runs, so whether an `exclude` entry still matches
    anything is answered by the job that does have the claim, and travels with the snapshot. A
    snapshot older than the report carries no file: absence is not a problem to report, it is a
    night whose job did not write one, and the next night's snapshot has it.
    """
    if not any(entry.get("path") == EXCLUDE_REPORT for entry in entries):
        return []
    text = run_restic(repository, "dump", snapshot_id, EXCLUDE_REPORT, "--no-lock", restic=restic)
    return json.loads(text)


def exclude_lines(name: str, report: list[dict]) -> list[str]:
    """One line per exclusion that matched nothing, in the shape the node job printed it."""
    return [
        f"exclude-unused stream={name} row={item['row']} pattern={item['pattern']}"
        for item in report
    ]


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


def read_snapshot_files(repository: str, snapshot_id: str, restic: str = "restic") -> list[dict]:
    """Every entry of one snapshot, as `restic ls --json` reports it."""
    text = run_restic(repository, "ls", "--json", snapshot_id, "--no-lock", restic=restic)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def main(argv: list[str] | None = None, etcd_s3=None) -> int:
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
        rows = stream.get("staged") or []
        files_rows = stream.get("files") or []
        must_match = stream.get("mustMatch") or []
        # The stream's own snapshot, not `latest`: a copy taken by hand in between is newer
        # and holds none of the claims.
        snapshot = stream_snapshot(snapshots, name) or newest_snapshot(snapshots)
        if (rows or files_rows or must_match or stream.get("claims")) and snapshot:
            # `restic ls` exits 1 when the repository holds no snapshot at all, and a
            # repository with no snapshot is what check_stream just reported; asking for the
            # file list would abort the run instead of naming the stream.
            entries = read_snapshot_files(repository, snapshot["id"], restic=args.restic)
            if rows:
                lines, staged_problems = check_staged(name, rows, entries)
                for line in lines:
                    print(line, flush=True)
                problems.extend(staged_problems)
            if files_rows:
                lines, files_problems = check_files(name, files_rows, entries)
                for line in lines:
                    print(line, flush=True)
                problems.extend(files_problems)
            if must_match:
                lines, external_problems = check_external(name, must_match, entries)
                for line in lines:
                    print(line, flush=True)
                problems.extend(external_problems)
            if stream.get("claims"):
                # Warned, never failed: see the module docstring. The line is the point; a
                # warning that changes the exit code would page for a working night.
                report = read_exclude_report(
                    repository, snapshot["id"], entries, restic=args.restic)
                for line in exclude_lines(name, report):
                    print(line, flush=True)
        else:
            for row in rows:
                print(f"staged stream={name} row={row['namespace']}/{row['claim']} files=-",
                      flush=True)
            for row in files_rows:
                print(f"files stream={name} row={row['namespace']}/{row['claim']} matched=-",
                      flush=True)
            for pattern in must_match:
                print(f"files stream={name} path={pattern} matched=-", flush=True)

        for problem in problems:
            print(f"problem stream={name} {problem}", flush=True)
        failures += len(problems)

    # The cluster's own state, not a stream: every server uploads its own etcd snapshots, so
    # this is asked once and answered per server. An API that cannot be reached is a finding
    # of its own rather than a reason to lose the stream report above.
    try:
        servers, etcd_snapshots = (etcd_s3 or read_etcd_s3)()
    except Exception as err:  # noqa: BLE001 — any failure here is the finding
        print(f"problem etcd-s3 api {err}", flush=True)
        failures += 1
    else:
        lines, problems = check_etcd_s3(servers, etcd_snapshots, now)
        for line in lines:
            print(line, flush=True)
        for problem in problems:
            print(f"problem {problem}", flush=True)
        failures += len(problems)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
