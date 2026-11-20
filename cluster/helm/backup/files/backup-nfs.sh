#!/bin/sh
# One restic run per export on the NAS. The list is written by the chart and never discovered
# here: an export that is named but missing fails the job, because backing up less than the
# matrix says without saying so is the failure this repository exists to prevent.
set -eu
# -f: the exclude words are patterns for restic, not for the shell. Without it a word like
# logs* would be expanded against this directory and reach restic as a filename, or vanish.
set -f
: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD:?}" "${STREAM:?}"

while IFS='|' read -r tag path exclude; do
  [ -n "$tag" ] || continue
  [ -d "$path" ] || { echo "export missing: $path" >&2; exit 1; }
  set --
  for pattern in $exclude; do
    set -- "$@" --exclude "$pattern"
  done
  echo "path=$tag"
  # </dev/null: the loop reads the path list from stdin, and a restic that inherited it would
  # swallow the next export. --host nas: without it restic names every snapshot after the pod,
  # finds no parent and rescans every path from scratch each night.
  restic backup \
    --retry-lock 2h \
    --host nas \
    --tag "stream=${STREAM}" \
    --tag "path=${tag}" \
    "$@" \
    "$path" </dev/null
done < /config/nfs-paths.txt
