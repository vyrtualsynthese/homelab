#!/bin/sh
# One restic run per export on the NAS. The list is written by the chart and never discovered
# here: an export that is named but missing fails the job, because backing up less than the
# matrix says without saying so is the failure this repository exists to prevent.
set -eu
# -f: the exclude words are patterns for restic, not for the shell. Without it a word like
# logs* would be expanded against this directory and reach restic as a filename, or vanish.
set -f
: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD:?}" "${STREAM:?}"

# Whether one of an export's `exclude` words matches anything under it.
#
# The same reading prepare.py gives the node rows (`matches`, in files/prepare.py): a word
# without a slash is a name and matches that name at any depth; a word with a slash is a path
# from the export's root, and matches the path or anything beneath it. The two halves of one
# chart must not disagree about what an exclusion means.
#
# The walk stops at the first hit, so a word that matches costs a directory listing and only a
# word that matches nothing costs the pass over the export — the same trade prepare.py makes.
matches_exclusion() {
  root=$1
  word=$2
  case $word in
    */*)
      trimmed=${word%/}
      if [ -e "$root/$trimmed" ]; then
        return 0
      fi
      if [ -n "$(find "$root" \
        \( -path "$root/$trimmed" -o -path "$root/$trimmed/*" \) \
        -print 2>/dev/null | head -n 1)" ]; then
        return 0
      fi
      return 1
      ;;
    *)
      if [ -n "$(find "$root" -name "$word" -print 2>/dev/null | head -n 1)" ]; then
        return 0
      fi
      return 1
      ;;
  esac
}

while IFS='|' read -r tag path exclude; do
  [ -n "$tag" ] || continue
  [ -d "$path" ] || { echo "export missing: $path" >&2; exit 1; }
  set --
  for pattern in $exclude; do
    # A word that matches nothing is named and the export is backed up anyway: an export whose
    # versioning is off has no .stversions yet, and an exclusion that matches nothing costs
    # space rather than data, which the stream's budget is what bounds. A warning, never a
    # failed night. The chart README says where the morning check reads this line.
    if ! matches_exclusion "$path" "$pattern"; then
      echo "exclude-unused path=$tag pattern=$pattern"
    fi
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
  # The chart mounts the list at /config; the test suite points NFS_PATHS_FILE at a temporary
  # copy, because the match above is the one thing here that can only be tested against a tree.
done < "${NFS_PATHS_FILE:-/config/nfs-paths.txt}"
