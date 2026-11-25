#!/bin/bash
# SnapRAID's nightly work without /opt/snapraid-runner, which needs a Python that
# is not installed on this host and never will be again. The result goes to the
# journal, and into the marker below, which is what the parity alert reads: this
# homelab has no mail path of its own and the journal is not shipped to Loki.
set -euo pipefail

# A sync of a 51 TB array outlives the night it starts in, and SnapRAID refuses a
# second instance on its own lock: under `set -e` the next 03:17 would be a failed
# cron run with a `sync starting` in the journal and no `sync finished` after it.
# This lock turns that collision into a logged skip instead, and `flock` drops it
# when the process goes away, however it goes away.
exec 9>/run/lock/snapraid-sync.lock
if ! flock -n 9; then
  logger -t snapraid "a run is still going; skipping this one"
  exit 0
fi

rc=0

marker=/var/lib/node-exporter/textfile/snapraid.prom

# A timestamp, not an age: the rule subtracts it from now, so a run that stops
# makes the number grow instead of freezing it. `rename(2)` inside one directory
# is atomic, so a scrape sees the old body or the new one, never half of either.
# The mode is set here rather than left to root's umask: the exporter reads the
# file as uid 65534, and a 0600 marker would read as "no sync ever finished".
write_marker() {
  local tmp="$marker.$$"
  {
    printf '# HELP homelab_snapraid_last_sync_timestamp_seconds Unix time of the last snapraid sync that finished without an error.\n'
    printf '# TYPE homelab_snapraid_last_sync_timestamp_seconds gauge\n'
    printf 'homelab_snapraid_last_sync_timestamp_seconds %s\n' "$(date +%s)"
  } >"$tmp" && chmod 0644 "$tmp" && mv "$tmp" "$marker" && return 0
  rm -f "$tmp"
  return 1
}

logger -t snapraid "sync starting"
/usr/bin/snapraid sync
logger -t snapraid "sync finished"

# Not fatal: the monthly scrub must not be lost to it, and a marker that stops
# being written is already what the alert reports.
write_marker || logger -t snapraid "ERROR could not write $marker"

# `status` exits 2 while blocks are unsynced, which on a live array means a file
# changed during the sync — the normal outcome on a NAS that is being written to,
# not a failure. `set -e` would end the script here and the scrub below would
# never run, so the code is caught, named in the journal, and only a real error
# (anything but 0 and 2) fails the run.
/usr/bin/snapraid status || rc=$?
case "$rc" in
  0) ;;
  2) logger -t snapraid "status: blocks remain unsynced, files changed during the sync" ;;
  *) logger -t snapraid "status failed with exit $rc"; exit "$rc" ;;
esac

# One scrub slice a month, on the first Sunday: scrubbing the whole array at once
# would keep the disks busy for hours on a storage node that also serves NFS.
if [ "$(date +%u)" = "7" ] && [ "$(date +%d)" -le 7 ]; then
  logger -t snapraid "scrub starting"
  /usr/bin/snapraid scrub
  logger -t snapraid "scrub finished"
fi

logger -t snapraid "done"
