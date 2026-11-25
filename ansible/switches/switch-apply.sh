#!/bin/bash
# switch-apply — the one way the agent carries a change onto a switch.
#
# Installed to /usr/local/sbin/switch-apply, owned root, and reached through
# /etc/sudoers.d/50-switch-apply as netcfg. It is the narrow door in front of
# switch-change.sh: the agent writes the change in git, stages it in the inbox,
# and never holds or reads the key the installed script signs in with.
#
#   sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 \
#        --change /var/lib/switch-change/inbox/<change>.rsc --dry-run
#
# Flags: --target, --change, --window, --expect, --yes, --dry-run, --arm-only.
# The change is checked, copied into a private stage under the work directory, and switch-change.sh
# is handed that copy: the file the script reads cannot be swapped after the check.
# Exit: the script's own; 1 = refused, nothing touched; 2 = bad argument.
set -uo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

INBOX="${INBOX:-/var/lib/switch-change/inbox}"
WORK="${WORK:-/var/lib/switch-change/work}"
SCRIPT="${SCRIPT:-/usr/local/lib/homelab/switch-change.sh}"
CANON="${CANON:-/home/vsynthese/projets/homelab/ansible/switches/switch-change.sh}"
TARGETS="${TARGETS:-crs326 crs309}"
TARGET_USER="${TARGET_USER:-netcfg}"
HOME_DIR="${HOME_DIR:-/home/netcfg}"

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0; }

refuse() { printf 'switch-apply: %s\n' "$1" >&2; exit 1; }
badarg() { printf 'switch-apply: %s\n' "$1" >&2; exit 2; }

# One set of arguments, and nothing the script does not take.
parse() {
  target=""; change=""; passthru=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --help|-h) usage ;;
      --target|--change|--window|--expect)
        [ $# -ge 2 ] || badarg "$1 needs a value"
        case "$1" in
          --target) target="$2" ;;
          --change) change="$2" ;;
          *) passthru+=("$1" "$2") ;;
        esac
        shift ;;
      --yes|--dry-run|--arm-only) passthru+=("$1") ;;
      *) badarg "unknown argument '$1'" ;;
    esac
    shift
  done
}

check_target() {
  local t
  for t in $TARGETS; do [ "$t" = "$target" ] && return 0; done
  badarg "unknown target '$target'; the gate carries $TARGETS"
}

# The change file is agent-written data, so it may only be a regular file the agent staged in the
# inbox: never a link, a device, a FIFO, or a path that merely starts with the inbox's name.
check_change() {
  local path="$1"
  case "$path" in
    /*) ;;
    *) refuse "the change must be an absolute path inside $INBOX" ;;
  esac
  [ -e "$path" ] || refuse "no such change file: $path"
  [ -f "$path" ] || refuse "not a regular file: $path"
  [ ! -L "$path" ] || refuse "the change may not be a symlink: $path"
  [ -r "$path" ] || refuse "the change is not readable by $TARGET_USER: $path"
  case "$(realpath -- "$path")" in
    "$INBOX"/*) ;;
    *) refuse "the change must sit in $INBOX: $path" ;;
  esac
  # The inbox is writable by the agent, so the path can change hands between the checks above and
  # the script reading it. Open it once and check what was actually opened; the descriptor stays
  # open, and stage_change copies from it, so no later swap is followed.
  exec 3< "$path" || refuse "cannot open the change: $path"
  case "$(realpath -- /proc/self/fd/3)" in
    "$INBOX"/*) ;;
    *) exec 3<&-; refuse "the change moved while it was checked: $path" ;;
  esac
}

# Copy the pinned descriptor into a private stage, and hand the script that path: the inbox is the
# agent's, the stage is netcfg's 0700 directory, so the copy cannot be swapped after the check.
stage_change() {
  local dir base epoch pid staged_file
  dir="$WORK/staged"
  find "$dir" -type f -mmin +1440 -delete 2>/dev/null
  [ -d "$dir" ] || mkdir -m 0700 -p "$dir" || { exec 3<&-; refuse "cannot create $dir"; }
  base="$(basename -- "$change")"
  epoch="$(date +%s)"
  pid="$$"
  staged_file="$dir/$base.$epoch.$pid.rsc"
  cat <&3 > "$staged_file" || {
    rm -f -- "$staged_file"; exec 3<&-; refuse "cannot stage the change in $dir"
  }
  chmod 0600 -- "$staged_file" || {
    rm -f -- "$staged_file"; exec 3<&-; refuse "cannot stage the change in $dir"
  }
  exec 3<&-
  staged="$staged_file"
}

# A live change must run the reviewed code. The installed copy is root-owned; the canonical
# checkout is what the operator reviewed.
check_installed() {
  [ -x "$SCRIPT" ] || refuse "not installed: $SCRIPT; run sudo ansible/switches/install-switch-change.sh"
  [ -r "$CANON" ] || refuse "cannot check the installed script against $CANON; re-run the installer from that checkout"
  [ "$(sha256sum -- "$SCRIPT" | cut -d' ' -f1)" = "$(sha256sum -- "$CANON" | cut -d' ' -f1)" ] \
    || refuse "the installed script differs from $CANON; re-run sudo ansible/switches/install-switch-change.sh"
}

main() {
  local user
  user="$(id -un)"
  [ "$user" = "$TARGET_USER" ] || refuse "run as $TARGET_USER through sudo, not as $user"
  parse "$@"
  [ -n "$target" ] || badarg "--target is required"
  [ -n "$change" ] || badarg "--change is required"
  check_target
  check_installed
  check_change "$change"
  stage_change
  HOME="$HOME_DIR" SWITCH_CHANGE_WORKDIR="$WORK" \
    exec "$SCRIPT" --target "$target" --change "$staged" "${passthru[@]}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
