#!/bin/bash
# Every step has to be able to fail the Job. Without `set -e` and with the
# `exit 0` this script used to carry, a run that could not reach the repository
# at all still exited 0: the 2026-11-19 05:00 run was `Complete` while restic
# had printed `Fatal: unable to open config file` four times.
set -euo pipefail

if restic cat config >/dev/null 2>&1; then
  echo 'repo already initialized'
else
  echo 'initializing repo'
  restic init
fi

restic backup /data --host syncthing -v
restic forget --keep-within-daily 7d --keep-within-weekly 1m --prune -v --host syncthing
