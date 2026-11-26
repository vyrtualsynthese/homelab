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
# The installed script must match its root-owned digest, switch-change.sh.sha256, or the run refuses.
# The change is checked, copied into a private stage under the work directory, and switch-change.sh
# is handed that copy: the file the script reads cannot be swapped after the check.
# Exit: the script's own; 1 = refused, nothing touched; 2 = bad argument.
set -uo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 077

# These are assignments, not defaults: the gate runs through sudo, so a variable that survived into
# its environment must never be able to retarget a path the root-owned digest and the sudoers rule
# were written against. A test that wants another tree sets them after sourcing this file.
INBOX=/var/lib/switch-change/inbox
WORK=/var/lib/switch-change/work
SCRIPT=/usr/local/lib/homelab/switch-change.sh
DIGEST=/usr/local/lib/homelab/switch-change.sh.sha256
TARGETS="crs326 crs309"
TARGET_USER=netcfg
HOME_DIR=/home/netcfg

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
  # Defence in depth, and not covered by the pin below: the pin copies the bytes a descriptor points
  # at, and a second name for the same inode can keep writing those bytes while the copy runs. One
  # link means the agent's staged file is the only name, so nothing else can be writing it.
  [ "$(stat -c %h -- "$path")" = 1 ] || refuse "the change has other links: $path"
  case "$(realpath -- "$path")" in
    "$INBOX"/*) ;;
    *) refuse "the change must sit in $INBOX: $path" ;;
  esac
  # The inbox is writable by the agent, so a symlink can be put in place after every check above.
  # Opening the file once pins what was checked: the descriptor cannot be retargeted, and
  # stage_change copies from it, so the swap is never followed.
  exec 3< "$path" || refuse "cannot open the change: $path"
  case "$(realpath -- /proc/self/fd/3)" in
    "$INBOX"/*) ;;
    *) exec 3<&-; refuse "the change moved while it was checked: $path" ;;
  esac
}

# Staged copies whose run is over are debris: drop a copy whose process is gone, and any copy left
# from an earlier day, even if its process number has since been reused.
sweep_stage() {
  local dir="$1" f name rest pid
  find "$dir" -maxdepth 1 -type f -name 'stage-*-*.rsc' -mmin +1440 -delete 2>/dev/null
  for f in "$dir"/stage-*-*.rsc; do
    [ -e "$f" ] || continue
    name="${f##*/}"
    rest="${name#stage-}"
    pid="${rest#*-}"
    pid="${pid%.rsc}"
    [ -d "/proc/$pid" ] || rm -f -- "$f"
  done
}

# Copy the pinned descriptor into a private stage, and hand the script that path: the inbox is the
# agent's, the stage is netcfg's 0700 directory, so the copy cannot be swapped after the check.
stage_change() {
  local dir="$WORK/staged" staged_file owner mode
  [ -d "$WORK" ] || refuse "no work directory: $WORK; run sudo ansible/switches/install-switch-change.sh"
  [ ! -L "$dir" ] || refuse "$dir is a symlink, not the stage; run sudo ansible/switches/install-switch-change.sh"
  [ -d "$dir" ] || mkdir -m 0700 "$dir" || refuse "cannot create $dir"
  owner="$(stat -c '%U' -- "$dir")"
  mode="$(stat -c '%a' -- "$dir")"
  [ "$owner $mode" = "$(id -un) 700" ] \
    || refuse "$dir is $owner $mode, not $(id -un) 700; run sudo ansible/switches/install-switch-change.sh"
  sweep_stage "$dir"
  staged_file="$dir/stage-$(date +%s)-$$.rsc"
  cat <&3 > "$staged_file" || {
    rm -f -- "$staged_file"; exec 3<&-; refuse "cannot stage the change in $dir"
  }
  chmod 0600 -- "$staged_file" || {
    rm -f -- "$staged_file"; exec 3<&-; refuse "cannot stage the change in $dir"
  }
  exec 3<&-
  staged="$staged_file"
}

# A live change must run the reviewed code. A root-owned digest names the bytes that were installed;
# the installer writes it, and nothing the agent can write is consulted.
check_installed() {
  [ -x "$SCRIPT" ] || refuse "not installed: $SCRIPT; run sudo ansible/switches/install-switch-change.sh"
  [ -r "$DIGEST" ] || refuse "no digest for the installed script: $DIGEST; re-run sudo ansible/switches/install-switch-change.sh"
  [ "$(sha256sum -- "$SCRIPT" | cut -d' ' -f1)" = "$(cut -d' ' -f1 -- "$DIGEST")" ] \
    || refuse "the installed script does not match $DIGEST; re-run sudo ansible/switches/install-switch-change.sh"
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
  # The script's own overrides are cleared rather than inherited: the account, the key path and the
  # target list are fixed by what this file and the installer put on disk, and an inherited variable
  # could point the run at another account, key or device.
  unset SWITCH_CHANGE_TARGETS SWITCH_CHANGE_USER SWITCH_CHANGE_KEY
  HOME="$HOME_DIR" SWITCH_CHANGE_WORKDIR="$WORK" \
    exec "$SCRIPT" --target "$target" --change "$staged" "${passthru[@]}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
