#!/bin/bash
# install-switch-change.sh — put the switch gate, the change script and its sudo rule in place.
#
#   sudo ansible/switches/install-switch-change.sh
#
# Installs, from the tree it runs in:
#
#   /usr/local/lib/homelab/switch-change.sh          root:root 0755   the change script
#   /usr/local/lib/homelab/switch-change.sh.sha256   root:root 0644   the digest the gate checks
#   /usr/local/sbin/switch-apply                     root:root 0755   the gate
#   /etc/sudoers.d/50-switch-apply                   root:root 0440   one command, one user
#   /var/lib/switch-change/inbox                     vsynthese:netcfg 2750   where the agent stages
#   /var/lib/switch-change/work                      netcfg:vsynthese 0750   where the script works
#
# The digest is written on every run, beside the script, so the gate always has the bytes it is
# meant to run: a script installed without it is refused on every run.
#
# A second run changes nothing and prints "already" for each artefact. A directory or drop-in that
# already exists with the wrong owner, group or mode is refused, never tightened behind the
# operator's back: a wrong mode there means someone else has been writing to it.
#
# The directory a file is installed into is held to the same rule: it must belong to the account the
# files are installed as (`root` normally, the invoking user under --destdir) and must not be writable
# by its group or by anyone else. A group-writable one lets that group replace the script and the
# digest together, which is exactly what the digest exists to prevent. These directories are checked
# on every run, not only when a file has to be written.
#
# Flags: --destdir DIR and --sudoers-line LINE are for the test suite only; both are refused as
# root, and --sudoers-line is refused outside --destdir.
set -uo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 022

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
destdir=""
sudoers_given=0
sudoers_line='vsynthese ALL=(netcfg) NOPASSWD: /usr/local/sbin/switch-apply'

die() { printf 'install-switch-change.sh: %s\n' "$1" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0; }

parse() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --help|-h) usage ;;
      --destdir|--sudoers-line)
        [ $# -ge 2 ] || die "$1 needs a value"
        case "$1" in
          --destdir) destdir="$2" ;;
          --sudoers-line) sudoers_line="$2"; sudoers_given=1 ;;
        esac
        shift ;;
      *) die "unknown argument '$1'; try --help" ;;
    esac
    shift
  done
}

# The path an artefact is installed to; with --destdir the whole tree is built under it instead.
at() { printf '%s%s' "$destdir" "$1"; }

# Same octal number whether stat prints "750" or the caller wrote "0750".
norm_mode() { printf '%o' $((8#$1)); }
same_mode() { [ $((8#$1)) = $((8#$2)) ]; }

sha() { sha256sum -- "$1" | cut -d' ' -f1; }

require_user() { id -u "$1" >/dev/null 2>&1 || die "no user $1; create it first, then re-run"; }
require_group() { getent group "$1" >/dev/null 2>&1 || die "no group $1; create it first, then re-run"; }

# A directory is created once and then only ever checked: an existing one whose mode is looser than
# the gate expects is refused and named, because tightening it would hide how it got that way.
check_dir() {  # path mode owner group
  local path have have_owner have_group
  path="$(at "$1")"
  if [ -e "$path" ] || [ -L "$path" ]; then
    { [ -d "$path" ] && [ ! -L "$path" ]; } || die "$path is not a directory; move it aside and re-run"
    have="$(stat -c '%a' -- "$path")"
    have_owner="$(stat -c '%U' -- "$path")"
    have_group="$(stat -c '%G' -- "$path")"
    same_mode "$have" "$2" || die "$path is mode $have, not $(norm_mode "$2"); fix it and re-run (refusing to tighten it)"
    [ "$have_owner" = "$3" ] || die "$path is owned by $have_owner, not $3; chown it and re-run"
    [ "$have_group" = "$4" ] || die "$path is group $have_group, not $4; chgrp it and re-run"
    say "already $path ($3:$4 $2)"
    return 0
  fi
  require_user "$3"
  require_group "$4"
  install -d -m "$2" -o "$3" -g "$4" -- "$path" || die "cannot create $path"
  say "created $path ($3:$4 $2)"
}

# The destination's own directory is created only when it is missing — a real install lands in
# directories the system already owns, and a test tree has none of them. An existing one is not
# created but inspected: see check_parent.
need_parent() {  # dst
  local dir
  dir="$(dirname -- "$1")"
  if [ -e "$dir" ] || [ -L "$dir" ]; then
    check_parent "$dir"
    return 0
  fi
  install -d -m 0755 -o "$owner" -g "$group" -- "$dir" || die "cannot create $dir"
}

# A directory the install writes into is only safe when nobody but its owner can write there: the
# digest names the script's bytes, so whoever may write to the directory may swap both.
check_parent() {  # dir
  local dir="$1" have_mode have_owner
  [ -e "$dir" ] || [ -L "$dir" ] || return 0
  { [ -d "$dir" ] && [ ! -L "$dir" ]; } || die "$dir is not a directory; move it aside and re-run"
  have_owner="$(stat -c '%U' -- "$dir")"
  [ "$have_owner" = "$owner" ] || die "$dir is owned by $have_owner, not $owner; chown it and re-run"
  have_mode="$(stat -c '%a' -- "$dir")"
  # The last two digits are the group and other permissions; a write bit in either is the defect.
  case "${have_mode: -2}" in
    *[2367]*) die "$dir is writable by more than $owner; fix it and re-run (refusing to tighten it)" ;;
  esac
}

# An installed file belongs to the account this run installs as: someone else who owns it can rewrite
# it whatever its mode allows, so the owner and group are checked with the mode, not instead of it.
check_owner_group() {  # path
  local path="$1" have_owner have_group
  have_owner="$(stat -c '%U' -- "$path")"
  have_group="$(stat -c '%G' -- "$path")"
  [ "$have_owner" = "$owner" ] || die "$path is owned by $have_owner, not $owner; chown it and re-run"
  [ "$have_group" = "$group" ] || die "$path is group $have_group, not $group; chgrp it and re-run"
}

# An unchanged file is left alone, digest and all. A file whose bytes are right but whose mode is
# not is refused rather than silently left unusable.
check_file() {  # src dst mode
  local src="$1" dst sum have
  dst="$(at "$2")"
  [ -f "$src" ] || die "no such source file: $src (run this from the tree it lives in)"
  sum="$(sha "$src")" || die "cannot hash $src"
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    { [ -f "$dst" ] && [ ! -L "$dst" ]; } || die "$dst is not a regular file; move it aside and re-run"
    if [ "$(sha "$dst")" = "$sum" ]; then
      have="$(stat -c '%a' -- "$dst")"
      same_mode "$have" "$3" || die "$dst is mode $have, not $(norm_mode "$3"); fix it and re-run (refusing to tighten it)"
      check_owner_group "$dst"
      say "already $dst ($sum)"
      return 0
    fi
  fi
  need_parent "$dst"
  install -m "$3" -o "$owner" -g "$group" -- "$src" "$dst" || die "cannot install $dst"
  say "installed $dst ($sum)"
}

# The gate reads this file and refuses to run when it does not name the installed script's bytes, so
# it is written on every run, from the script that was just put in place.
write_digest() {  # dst sum
  local dst want tmp have
  dst="$(at "$1")"
  want="$2  switch-change.sh"
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    { [ -f "$dst" ] && [ ! -L "$dst" ]; } || die "$dst is not a regular file; move it aside and re-run"
    if [ "$(cat -- "$dst")" = "$want" ]; then
      have="$(stat -c '%a' -- "$dst")"
      same_mode "$have" 0644 || die "$dst is mode $have, not $(norm_mode 0644); fix it and re-run (refusing to tighten it)"
      check_owner_group "$dst"
      say "already $dst ($2)"
      return 0
    fi
  fi
  tmp="$dst.tmp.$$"
  need_parent "$dst"
  printf '%s\n' "$want" > "$tmp" || { rm -f -- "$tmp"; die "cannot write $tmp"; }
  install -m 0644 -o "$owner" -g "$group" -- "$tmp" "$dst" || { rm -f -- "$tmp"; die "cannot install $dst"; }
  rm -f -- "$tmp"
  say "installed $dst ($2)"
}

# one line, named by path; the body lives here so both comments and the rule come from one place
sudoers_body() {
  printf '%s\n' \
    '# switch-apply: the agent carries a switch change as netcfg, through one command.' \
    '# Installed by ansible/switches/install-switch-change.sh — edit git, not this file.' \
    "$sudoers_line"
}

write_sudoers() {  # dst
  local dst tmp have
  dst="$(at "$1")"
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    { [ -f "$dst" ] && [ ! -L "$dst" ]; } || die "$dst is not a regular file; move it aside and re-run"
    have="$(stat -c '%a' -- "$dst")"
    same_mode "$have" 0440 || die "$dst is mode $have, not $(norm_mode 0440); fix it and re-run (refusing to tighten it)"
    check_owner_group "$dst"
    if [ "$(cat -- "$dst")" = "$(sudoers_body)" ]; then
      say "already $dst"
      return 0
    fi
  fi
  # Validate before the file is in place: visudo never sees a drop-in it would then be too late for.
  tmp="$dst.tmp.$$"
  need_parent "$dst"
  sudoers_body > "$tmp" || die "cannot write $tmp"
  chmod 0440 "$tmp" || { rm -f -- "$tmp"; die "cannot set the mode of $tmp"; }
  if ! visudo -cf "$tmp" >/dev/null; then
    rm -f -- "$tmp"
    die "the sudoers line was refused by visudo, so $dst was not written: $sudoers_line"
  fi
  mv -f -- "$tmp" "$dst" || { rm -f -- "$tmp"; die "cannot install $dst"; }
  say "installed $dst"
}

main() {
  parse "$@"

  # The tests build a whole tree under --destdir as an ordinary user; a real install is root's and
  # never takes one, so neither flag can be used to write a fake root while holding real rights.
  if [ -n "$destdir" ]; then
    [ "$(id -u)" != 0 ] || die "--destdir is for the tests only"
    case "$destdir" in
      /*) ;;
      *) die "--destdir needs an absolute path" ;;
    esac
    [ -d "$destdir" ] || mkdir -p -- "$destdir" || die "cannot create $destdir"
    owner="$(id -un)"; group="$(id -gn)"
    inbox_owner="$owner"; inbox_group="$group"
    work_owner="$owner"; work_group="$group"
  else
    [ "$(id -u)" = 0 ] || die "run with sudo: sudo ansible/switches/install-switch-change.sh"
    [ "$sudoers_given" = 0 ] || die "--sudoers-line is for the tests only"
    owner=root; group=root
    inbox_owner=vsynthese; inbox_group=netcfg
    work_owner=netcfg; work_group=vsynthese
  fi

  # What is installed has to be what someone reviewed: an edit still in the working tree cannot
  # become the code that holds the key. The two files live in ansible/switches, and so does nothing
  # else this run depends on that a dirty tree could slip in.
  if [ -n "$destdir" ]; then
    [ "${SWITCH_INSTALL_DIRTY:-0}" != 1 ] || die "ansible/switches has uncommitted changes; commit or merge first"
  else
    # This runs as root inside the operator's checkout, where git refuses to work on a repository it
    # does not own ("dubious ownership"); the -c names that one directory and nothing else.
    repo="$(cd -- "$here/../.." && pwd)" || die "cannot find the repository root from $here"
    git -c "safe.directory=$repo" -C "$repo" rev-parse --git-dir >/dev/null 2>&1 \
      || die "not a git checkout: cannot tell whether ansible/switches is committed"
    # The exit status is kept apart from the output: an empty answer from a git that failed is not a
    # clean tree, and reading it as one would install whatever an unreadable checkout holds.
    dirty=""
    if ! dirty="$(git -c "safe.directory=$repo" -C "$repo" status --porcelain -- ansible/switches 2>&1)"; then
      die "cannot ask git whether ansible/switches is committed: $dirty"
    fi
    [ -z "$dirty" ] || die "ansible/switches has uncommitted changes; commit or merge first: $dirty"
  fi

  # The directories the files land in are inspected on every run, before anything is written: a second
  # run has nothing to write, and a directory that drifted to group-writable is exactly what it still
  # has to catch.
  for dir in /usr/local/lib/homelab /usr/local/sbin /etc/sudoers.d; do
    check_parent "$(at "$dir")"
  done

  check_dir /var/lib/switch-change/inbox 2750 "$inbox_owner" "$inbox_group"
  check_dir /var/lib/switch-change/work 0750 "$work_owner" "$work_group"

  check_file "$here/switch-change.sh" /usr/local/lib/homelab/switch-change.sh 0755
  check_file "$here/switch-apply.sh" /usr/local/sbin/switch-apply 0755
  write_digest /usr/local/lib/homelab/switch-change.sh.sha256 "$(sha "$here/switch-change.sh")"

  write_sudoers /etc/sudoers.d/50-switch-apply

  say "the agent's first command: sudo -u netcfg /usr/local/sbin/switch-apply --target crs326 --change /var/lib/switch-change/inbox/<change>.rsc --dry-run"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
