#!/bin/sh
# One restic run for one node's stream. prepare.py has already staged the SQLite copies and
# written the two lists this reads.
set -eu
: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD:?}" "${STREAM:?}" "${NODE:?}"

# --files-from-verbatim, not --files-from: the list holds literal paths, and the glob form
# would expand a path that ever contains a bracket or an asterisk.
# --host "$NODE": without it restic names the snapshot after the pod, finds no parent
# snapshot, and rescans every gigabyte of the stream every night.
exec restic backup \
  --retry-lock 2h \
  --host "$NODE" \
  --tag "stream=${STREAM}" \
  --tag "node=${NODE}" \
  --exclude-file /staging/exclude.txt \
  --files-from-verbatim /staging/restic-paths.txt
