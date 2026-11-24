#!/bin/bash
# SnapRAID's nightly work without /opt/snapraid-runner, which needs a Python that
# is not installed on this host and never will be again. The result goes to the
# journal: it already reaches Loki, and this homelab has no mail path of its own.
set -euo pipefail

rc=0

logger -t snapraid "sync starting"
/usr/bin/snapraid sync
logger -t snapraid "sync finished"

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
