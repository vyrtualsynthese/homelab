#!/bin/bash
if restic cat config >/dev/null 2>&1; then
  echo 'repo already initialized'
else
  echo 'initializing repo'
  restic init
fi

restic backup /data --host nextcloudbackup -v
restic forget --keep-within-daily 7d --keep-within-weekly 1m --prune -v --host nextcloudbackup
exit 0