#!/bin/bash
# SnapRAID's nightly work without /opt/snapraid-runner, which needs a Python that
# is not installed on this host and never will be again. The result goes to the
# journal: it already reaches Loki, and this homelab has no mail path of its own.
set -euo pipefail

logger -t snapraid "sync starting"
/usr/bin/snapraid sync
logger -t snapraid "sync finished"

# `status` exits 2 when the array still holds unsynced blocks, which after a
# successful sync means the sync did not cover everything. The exit code is left
# alone so cron reports it.
/usr/bin/snapraid status

# One scrub slice a month, on the first Sunday: scrubbing the whole array at once
# would keep the disks busy for hours on a storage node that also serves NFS.
if [ "$(date +%u)" = "7" ] && [ "$(date +%d)" -le 7 ]; then
  logger -t snapraid "scrub starting"
  /usr/bin/snapraid scrub
  logger -t snapraid "scrub finished"
fi

logger -t snapraid "done"
