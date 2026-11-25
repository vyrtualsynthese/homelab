#!/usr/bin/env bash
# switch-change.sh — carry one RouterOS change onto a switch with an undo armed on the device.
#
#   ./switch-change.sh --target crs326 --change changes/x.rsc --window 10m
#   ./switch-change.sh --target crs326 --change changes/x.rsc --dry-run --yes
#   ./switch-change.sh --render-undo --major 7 --change changes/x.rsc \
#        --export /tmp/opencode/pre.rsc --verbose-export /tmp/opencode/pre-verbose.rsc
#   ./switch-change.sh --noise-free < an-export.rsc     # drop comments and blanks, for a comparison
#   ./switch-change.sh --list-targets
#   ./switch-change.sh --help
#
# --target reads a switch, renders the undo for one change, shows it, and — with --yes, or after
# asking on a terminal — uploads the undo and the change and arms the one-shot timer that runs the
# undo at the device clock's now + window. --dry-run stops after printing the undo; --arm-only stops
# after arming, for a change applied by hand from Winbox.
#
# --render-undo prints the undo file for one change: guarded one-liners, one per inverse operation,
# each of which does nothing when its object is already in the state it wants. It reads the change
# and the two pre-change exports and prints nothing when it refuses — a refusal names the line.
#
# --noise-free is the filter the verify phase compares two exports with: every export opens with a
# dated comment, so two of them are never byte-equal.
set -uo pipefail

SCRIPT_VERSION="2026-11-25.1"
SWITCH_CHANGE_TARGETS="${SWITCH_CHANGE_TARGETS:-crs326=192.168.1.11 crs309=192.168.1.43}"
SWITCH_CHANGE_USER="${SWITCH_CHANGE_USER:-deploy}"
SWITCH_CHANGE_KEY="${SWITCH_CHANGE_KEY:-$HOME/.ssh/switches-deploy}"
SWITCH_CHANGE_WINDOW="${SWITCH_CHANGE_WINDOW:-10m}"
SWITCH_CHANGE_WORKDIR="${SWITCH_CHANGE_WORKDIR:-/tmp/opencode/switch-change}"
SWITCH_CHANGE_CONNECT_TIMEOUT="${SWITCH_CHANGE_CONNECT_TIMEOUT:-5}"
SWITCH_CHANGE_CMD_TIMEOUT="${SWITCH_CHANGE_CMD_TIMEOUT:-20}"
SWITCH_CHANGE_POLL="${SWITCH_CHANGE_POLL:-5}"

usage() { awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0; }
die() { printf 'switch-change.sh: %s\n' "$1" >&2; exit 2; }
say() { printf '%s\n' "$*"; }
warn() { printf 'switch-change.sh: %s\n' "$*" >&2; }

# Die on a list that is not "name=host", before any pair is printed: a malformed list that emitted
# the pairs it had already read would look like a good run that lost targets.
validate_targets() {
  local pair name host
  for pair in $SWITCH_CHANGE_TARGETS; do
    name="${pair%%=*}"; host="${pair#*=}"
    [ "$name" != "$host" ] || die "target '$pair' is not name=host"
  done
}

# One "name host" pair per line, from SWITCH_CHANGE_TARGETS.
targets() {
  local pair name host
  validate_targets
  for pair in $SWITCH_CHANGE_TARGETS; do
    name="${pair%%=*}"; host="${pair#*=}"
    printf '%s %s\n' "$name" "$host"
  done
}

# The address of a target by name, 1 when the name is unknown.
host_of() {
  local device="$1" name host
  while read -r name host; do
    if [ "$name" = "$device" ]; then printf '%s\n' "$host"; return 0; fi
  done < <(targets)
  return 1
}

# Drop comment lines and blank lines. Reads stdin, writes stdout; it is what makes two exports of
# the same configuration compare equal.
noise_free() {
  awk '/^[[:space:]]*#/ { next } /^[[:space:]]*$/ { next } { print }'
}

# One RouterOS command on a device, its output on stdout. Shaped like the collector's: BatchMode so
# no password is ever asked, the connect and the command both bounded, and LogLevel=ERROR so ssh's
# greeting does not land inside an export. The carriage returns go, because RouterOS answers with
# CRLF and an invisible byte at a line's end makes every comparison wrong.
ros() {                                   # ros <host> <routeros command...>
  local host="$1"; shift
  timeout "$SWITCH_CHANGE_CMD_TIMEOUT" ssh \
    -i "$SWITCH_CHANGE_KEY" -o BatchMode=yes \
    -o ConnectTimeout="$SWITCH_CHANGE_CONNECT_TIMEOUT" \
    -o StrictHostKeyChecking=accept-new \
    -o LogLevel=ERROR \
    "$SWITCH_CHANGE_USER@$host" "$*" 2>&1 | tr -d '\r'
}

# One file onto a device, under the name the timer's on-event will use. `-O` is the legacy protocol:
# the newer one speaks SFTP, which the deploy account is not given.
put() {                                   # put <host> <local file> <remote name>
  local host="$1" file="$2" name="$3"
  timeout "$SWITCH_CHANGE_CMD_TIMEOUT" scp -O -q \
    -i "$SWITCH_CHANGE_KEY" -o BatchMode=yes \
    -o ConnectTimeout="$SWITCH_CHANGE_CONNECT_TIMEOUT" \
    -o StrictHostKeyChecking=accept-new \
    -o LogLevel=ERROR \
    "$file" "$SWITCH_CHANGE_USER@$host:$name" 2>&1 | tr -d '\r'
}

# The RouterOS major version of a device, from its resource block. It decides the export command
# (verbose on 7, hide-sensitive verbose on 6) and the parse the renderer expects.
routeros_major() {                        # routeros_major <host>
  ros "$1" '/system resource print' | awk '
    /^[[:space:]]*version:/ { print $2; exit }' | cut -d. -f1
}

# The device's own clock as "YYYY-MM-DD HH:MM:SS". The entry's start-time is computed from this,
# never from the workstation's clock: RouterOS dates the entry when it is created.
device_now() {                            # device_now <host>
  local out date time
  out=$(ros "$1" '/system clock print')
  date=$(printf '%s\n' "$out" | awk '/^[[:space:]]*date:/ { print $2; exit }')
  time=$(printf '%s\n' "$out" | awk '/^[[:space:]]*time:/ { print $2; exit }')
  [ -n "$date" ] && [ -n "$time" ] || return 1
  # RouterOS 6 and 7 write the date differently; one shape for arithmetic.
  date -d "$date $time" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || return 1
}

# Warn when the two clocks disagree by more than a minute: the window is measured on the device, so
# a workstation that is far off would otherwise make the operator read the run's timing wrong.
warn_on_clock_skew() {                    # warn_on_clock_skew <device stamp>
  local device difference
  device=$(date -d "$1" +%s 2>/dev/null) || return 0
  difference=$(( device - $(date +%s) ))
  [ "${difference#-}" -le 60 ] && return 0
  warn "the device clock reads $1, this workstation $(date '+%Y-%m-%d %H:%M:%S'); the window is measured on the device"
}

# A window as seconds. The ceiling is an hour: a timer left far in the future outlives the run that
# armed it and quietly puts an old configuration back later.
window_seconds() {                        # window_seconds <10m|600s|600>
  local raw="$1" seconds
  case "$raw" in
    *m) seconds=$(( ${raw%m} * 60 )) ;;
    *s) seconds="${raw%s}" ;;
    *)  seconds="$raw" ;;
  esac
  case "$seconds" in
    ''|*[!0-9]*) die "--window $raw is not a duration: 10m, 600s and 600 mean the same thing" ;;
  esac
  [ "$seconds" -ge 60 ] || die "--window $raw is shorter than a minute"
  [ "$seconds" -le 3600 ] || die "--window $raw is longer than the one-hour ceiling"
  printf '%s\n' "$seconds"
}

# The device-clock time of now + window, as the HH:MM:SS RouterOS takes. A window that crosses the
# device's midnight is refused: RouterOS dates the entry when it is created, so a start-time earlier
# than today's clock would run hours early, and one on the next day runs a day late. Neither is a
# revert path, and nothing is armed when this refuses.
start_time_for() {                        # start_time_for "<date> <time>" <seconds>
  local stamp="$1" seconds="$2" base day at
  # Epoch arithmetic, not "date -d '$stamp + 600 seconds'": GNU date reads the `+ 600` there as a
  # UTC offset and answers a time six hours off. The device's wall clock is taken in the
  # workstation's zone, which is the same Europe/Paris on both sides of this homelab.
  base=$(date -d "$stamp" +%s 2>/dev/null) || return 1
  base=$(( base + seconds ))
  day=$(date -d "@$base" '+%Y-%m-%d')
  at=$(date -d "@$base" '+%H:%M:%S')
  if [ "$day" != "${stamp%% *}" ]; then
    warn "the $seconds-second window would cross the device's midnight (device time $stamp): nothing was armed"
    exit 1
  fi
  printf '%s\n' "$at"
}

# Ask the one question this script asks. A pipeline or a cron has no terminal, and a run that assumed
# yes because nobody was there would arm a timer that changes a switch unattended.
confirm() {
  local answer
  [ "$assume_yes" = 1 ] && return 0
  [ -t 0 ] || die "no terminal to ask on, and --yes was not given: nothing was changed"
  printf 'arm the undo on %s and apply %s? [y/N] ' "$target" "$change" >&2
  read -r answer
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
  esac
  warn "nothing was changed"
  exit 1
}

# Print the undo file for one change, or refuse it. Four arguments: the RouterOS major, the change
# file, the plain export and the verbose one. Every refusal is a line on stderr naming the change
# file and the line; a refusal prints nothing on stdout and exits 1.
render_undo() {
  local major="$1" change="$2" plain="$3" verbose="$4"
  awk -v major="$major" -v CHANGEFILE="$change" \
      -v plain="$plain" -v verbose="$verbose" '
  # ---------------------------------------------------------------- text helpers
  function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }

  # Split on spaces that are not inside a quoted value. A backslash keeps the character after it,
  # which is how RouterOS writes a literal quote inside a value.
  function tokens(s, out,   n, i, c, cur, inq) {
    n = 0; cur = ""; inq = 0
    for (i = 1; i <= length(s); i++) {
      c = substr(s, i, 1)
      if (c == "\\") { cur = cur c; i++; cur = cur substr(s, i, 1); continue }
      if (c == "\"") { inq = !inq; cur = cur c; continue }
      if (c == " " && !inq) { if (cur != "") { out[++n] = cur; cur = "" }; continue }
      cur = cur c
    }
    if (cur != "") out[++n] = cur
    return n
  }

  function keyname(tok,   i) { i = index(tok, "="); return i ? substr(tok, 1, i - 1) : "" }
  function keyval(tok,   i)  { i = index(tok, "="); return i ? substr(tok, i + 1) : "" }

  # A value with its surrounding quotes removed, so "a" and a compare equal.
  function unq(v) {
    if (length(v) >= 2 && substr(v, 1, 1) == "\"" && substr(v, length(v), 1) == "\"")
      return substr(v, 2, length(v) - 2)
    return v
  }

  # ---------------------------------------------------------------- object index
  function store(mode, path, args, src) {
    if (mode == "plain") { ocount[path]++; oi = ocount[path]; oargs[path, oi] = args; osrc[path, oi] = src }
    else { vcount[path]++; oi = vcount[path]; vargs[path, oi] = args; vsrc[path, oi] = src }
  }

  function split_sel(args,   i, j) {
    if (substr(args, 1, 1) != "[") return 0
    j = index(args, "]")
    if (j == 0) return 0
    SEL = trim(substr(args, 2, j - 2))
    sub(/^find[ \t]+/, "", SEL)
    AFTER = trim(substr(args, j + 1))
    return 1
  }

  function is_verb(w) { return w == "add" || w == "set" || w == "remove" || w == "enable" || w == "disable" }

  # An absolute line carries its own path, and a path may hold spaces (`/interface bridge vlan`):
  # the path is every token before the first verb. A line with no verb at all is a section header.
  function split_cmd(line, t, n,   i, v) {
    v = 0
    for (i = 2; i <= n; i++) if (is_verb(t[i])) { v = i; break }
    if (!v) return 0
    PATH = ""; for (i = 1; i < v; i++) PATH = PATH (i > 1 ? " " : "") t[i]
    VERB = t[v]; ARGS = ""
    for (i = v + 1; i <= n; i++) ARGS = ARGS (i > v + 1 ? " " : "") t[i]
    return 1
  }

  function index_export(mode, line,   t, n, i, path, verb, args) {
    line = trim(line)
    if (line == "" || substr(line, 1, 1) == "#") return
    n = tokens(line, t)
    if (substr(line, 1, 1) == "/") {
      if (!split_cmd(line, t, n)) { cur[mode] = trim(line); return }
      path = PATH; verb = VERB; args = ARGS
    } else {
      path = cur[mode]; verb = t[1]; args = ""
      for (i = 2; i <= n; i++) args = args (i > 2 ? " " : "") t[i]
    }
    if (path == "") return
    if (verb == "add") store(mode, path, args, "add")
    else if (verb == "set" && split_sel(args)) store(mode, path, trim(SEL " " AFTER), "set")
  }

  # ---------------------------------------------------------------- matching
  function obj_matches(args, sel,   st, sn, at, an, i, j, ok) {
    sn = tokens(sel, st); an = tokens(args, at)
    for (i = 1; i <= sn; i++) {
      if (index(st[i], "=") == 0) continue
      ok = 0
      for (j = 1; j <= an; j++)
        if (keyname(at[j]) == keyname(st[i]) && unq(keyval(at[j])) == unq(keyval(st[i]))) { ok = 1; break }
      if (!ok) return 0
    }
    return 1
  }

  # The selector as the undo writes it: every pair quoted, whichever way the change wrote it.
  function render_sel(sel,   st, sn, i, o) {
    sn = tokens(sel, st); o = ""
    for (i = 1; i <= sn; i++) {
      if (index(st[i], "=") == 0) { o = o (o == "" ? "" : " ") st[i]; continue }
      o = o (o == "" ? "" : " ") keyname(st[i]) "=\"" unq(keyval(st[i])) "\""
    }
    return o
  }

  # An add key: as written when it was quoted; quoted when it holds something RouterOS reads as
  # syntax unquoted.
  function render_key(k, v) {
    if (substr(v, 1, 1) == "\"") return k "=" v
    if (v ~ /[ \/="]/) { gsub(/"/, "\\\"", v); return k "=\"" v "\"" }
    return k "=" v
  }

  function find_one(path, sel, mode, lineno,   i, n, cnt, args, key) {
    for (i = 1; i <= ocount[path]; i++) {
      if (mode == "verbose") { n = vcount[path]; args = vargs[path, i] }
      else { n = ocount[path]; args = oargs[path, i] }
      if (i > n) break
      if (obj_matches(args, sel)) { cnt++; key = i }
    }
    if (cnt == 0) {
      do_refuse(lineno, "the " mode " export has no object matching " render_sel(sel))
      return 0
    }
    if (cnt > 1) {
      do_refuse(lineno, render_sel(sel) " matches " cnt " objects in the " mode " export")
      return 0
    }
    return key
  }

  function obj_value(args, k,   at, an, j) {
    an = tokens(args, at)
    for (j = 1; j <= an; j++) if (keyname(at[j]) == k) return keyval(at[j])
    return ""
  }

  # ---------------------------------------------------------------- refusals and output
  function do_refuse(lineno, msg) {
    refused = 1
    printf "switch-change.sh: %s line %d: %s\n", CHANGEFILE, lineno, msg > "/dev/stderr"
  }
  function fail(msg) { refused = 1; printf "switch-change.sh: %s: %s\n", plain, msg > "/dev/stderr" }
  function emit(s) { out[++outn] = s }

  function refuse_path(path, lineno) {
    if (path ~ /^\/import([ \t]|$)/) { do_refuse(lineno, "/import may not appear in a change: it runs another file"); return 1 }
    if (path ~ /^\/(system scheduler|user|system reset|system backup)([ \t]|$)/) {
      do_refuse(lineno, path " may not be changed: it could remove the undo timer or the account that cancels it")
      return 1
    }
    return 0
  }

  # ---------------------------------------------------------------- the three verbs
  function undo_add(path, args, lineno,   t, n, i, keys, kn, k, v, key) {
    n = tokens(args, t)
    split("name comment address dst-address interface mac-address", keys, " "); kn = 6
    for (i = 1; i <= kn; i++) {
      if (obj_value(args, keys[i]) != "") { k = keys[i]; v = obj_value(args, keys[i]); break }
    }
    if (k == "") {
      do_refuse(lineno, "an add with no identifying attribute (name, comment, address, dst-address, interface, mac-address)")
      return
    }
    key = render_key(k, v)
    emit(":if ([:len [" path " find " key "]] > 0) do={ " path " remove [find " key "] }")
  }

  function undo_remove(path, args, lineno,   sel, selsel, idx) {
    if (!split_sel(args)) { do_refuse(lineno, "a remove this renderer cannot read (no [find ...] selector)"); return }
    selsel = render_sel(SEL)
    idx = find_one(path, SEL, "plain", lineno)
    if (!idx) return
    if (osrc[path, idx] != "add") {
      do_refuse(lineno, "the object this removes is described by a device default, not by an add line, so it cannot be re-added")
      return
    }
    emit(":if ([:len [" path " find " selsel "]] = 0) do={ " path " add " oargs[path, idx] " }")
  }

  function undo_set(path, args, lineno,   sel, selsel, after, at, an, i, k, oldv, idx, vidx) {
    if (!split_sel(args)) { do_refuse(lineno, "a set this renderer cannot read (no [find ...] selector)"); return }
    selsel = render_sel(SEL); after = AFTER
    idx = find_one(path, SEL, "plain", lineno)
    if (!idx) return
    vidx = find_one(path, SEL, "verbose", lineno)
    if (!vidx) return
    an = tokens(after, at)
    for (i = 1; i <= an; i++) {
      k = keyname(at[i])
      if (k == "") continue
      oldv = obj_value(vargs[path, vidx], k)
      if (oldv == "") { do_refuse(lineno, "no old value for " k " in the verbose export"); return }
      emit(":if ([:len [" path " find " selsel "]] > 0) do={ " path " set [find " selsel "] " k "=" oldv " }")
    }
  }

  function undo_flag(path, verb, args, lineno,   sel, selsel, idx, want) {
    if (!split_sel(args)) { do_refuse(lineno, "a " verb " this renderer cannot read (no [find ...] selector)"); return }
    selsel = render_sel(SEL)
    idx = find_one(path, SEL, "plain", lineno)
    if (!idx) return
    want = (verb == "disable") ? "enable" : "disable"
    if (obj_value(oargs[path, idx], "disabled") == "yes") want = (verb == "disable") ? "disable" : "enable"
    emit(":if ([:len [" path " find " selsel "]] > 0) do={ " path " " want " [find " selsel "] }")
  }

  # ---------------------------------------------------------------- a change line
  function process_change(line, lineno,   t, n, i, path, verb, args) {
    line = trim(line)
    if (line == "" || substr(line, 1, 1) == "#") return
    n = tokens(line, t)
    if (substr(line, 1, 1) == "/") {
      if (!split_cmd(line, t, n)) {
        path = trim(line)
        if (refuse_path(path, lineno)) return
        cur["change"] = path; return
      }
      path = PATH
      if (refuse_path(path, lineno)) return
      verb = VERB; args = ARGS
    } else {
      path = cur["change"]; verb = t[1]; args = ""
      for (i = 2; i <= n; i++) args = args (i > 2 ? " " : "") t[i]
      if (path == "") { do_refuse(lineno, "no section header before this line"); return }
      if (refuse_path(path, lineno)) return
    }
    if (verb == "add") undo_add(path, args, lineno)
    else if (verb == "set") undo_set(path, args, lineno)
    else if (verb == "remove") undo_remove(path, args, lineno)
    else if (verb == "enable" || verb == "disable") undo_flag(path, verb, args, lineno)
    else do_refuse(lineno, "cannot undo the command \"" verb "\"")
  }

  # ---------------------------------------------------------------- line joining and dispatch
  # A real export wraps a long line with a trailing backslash; a half line must never be read as a
  # whole object, so the unit is joined first and reported at the line it started on.
  function take(mode, line,   start) {
    if (buf[mode] != "") { line = buf[mode] " " line; start = bstart[mode] }
    else start = FNR
    if (line ~ /\\[ \t]*$/) { sub(/\\[ \t]*$/, "", line); buf[mode] = trim(line); bstart[mode] = start; return }
    line = trim(line); buf[mode] = ""
    if (mode == "change") process_change(line, start)
    else index_export(mode, line)
  }

  BEGIN { refused = 0; outn = 0; p_major = "" }

  FILENAME == plain {
    if (FNR == 1 && index($0, "by RouterOS ") > 0) {
      v = substr($0, index($0, "by RouterOS ") + 12); gsub(/[^0-9.].*/, "", v)
      split(v, pv, "."); p_major = pv[1]
    }
    take("plain", $0); next
  }
  FILENAME == verbose { take("verbose", $0); next }
  { take("change", $0) }

  END {
    if (p_major != "" && p_major != major)
      fail("says RouterOS " p_major ", but --major " major " was given")
    if (refused) exit 1
    for (i = outn; i >= 1; i--) print out[i]
  }
  ' "$plain" "$verbose" "$change"
}

# ------------------------------------------------------------------ phases 1 and 2
# Read the device, render and show the undo, then arm it. Nothing on a switch is written before the
# undo exists locally and has passed the renderer: a refusal writes nothing at all.
change_run() {
  local host major verbose_cmd stamp start seconds id dir plain verbose undo_file change_file
  local stale detail name

  host=$(host_of "$target") || die "unknown target '$target'"
  [ -r "$change" ] || { warn "cannot read the change file $change"; exit 1; }
  seconds=$(window_seconds "$window") || exit 2

  major=$(routeros_major "$host")
  case "$major" in
    6) verbose_cmd='/export hide-sensitive verbose' ;;
    7) verbose_cmd='/export verbose' ;;
    *) warn "$target did not name a RouterOS major version"; exit 1 ;;
  esac

  stamp=$(device_now "$host") || { warn "$target did not print its clock"; exit 1; }
  warn_on_clock_skew "$stamp"
  # A refusal exits inside the command substitution, so its status has to be carried out by hand:
  # an empty `start` would otherwise be armed as `start-time=` and fail on the device instead.
  start=$(start_time_for "$stamp" "$seconds") || exit 1

  id="$target-$(date '+%Y%m%d-%H%M%S')"
  dir="$SWITCH_CHANGE_WORKDIR/$id"
  mkdir -p "$dir" || { warn "cannot write to $dir"; exit 1; }
  plain="$dir/pre-$id.rsc"
  verbose="$dir/pre-$id-verbose.rsc"
  undo_file="$dir/undo-$id.rsc"
  change_file="$dir/change-$id.rsc"

  ros "$host" '/export' > "$plain"
  [ -s "$plain" ] || { warn "$target did not answer /export"; exit 1; }
  ros "$host" "$verbose_cmd" > "$verbose"
  [ -s "$verbose" ] || { warn "$target did not answer $verbose_cmd"; exit 1; }

  # Render before cancelling anything: a change that cannot be inverted must stop the run before its
  # first write, and cancelling a stale timer is a write.
  if ! render_undo "$major" "$change" "$plain" "$verbose" > "$undo_file"; then
    rm -rf "$dir"
    die "nothing was changed: the change cannot be undone"
  fi
  cp "$change" "$change_file"

  # A timer an earlier run left is cancelled and named: two armed undos must never sit on one switch,
  # because the older one would put back a configuration that is already two changes old.
  while read -r stale; do
    [ -n "$stale" ] || continue
    ros "$host" "/system scheduler remove [find name=\"$stale\"]" >/dev/null
    say "cancelled the timer an earlier run left armed: $stale"
  done < <(ros "$host" '/system scheduler print detail' | sed -n 's/.*name="\(revert-[^"]*\)".*/\1/p')

  if [ "$dry_run" = 1 ]; then
    say "would arm revert-$id at $start device time and apply $change:"
    cat "$undo_file"
    rm -rf "$dir"
    exit 0
  fi

  if [ "$arm_only" != 1 ]; then
    warn "the apply and verify phases are not in this build; the undo was rendered, nothing was written"
    rm -rf "$dir"
    exit 1
  fi

  confirm

  put "$host" "$undo_file" "undo-$id.rsc" >/dev/null || { warn "could not upload undo-$id.rsc"; exit 1; }
  put "$host" "$change_file" "change-$id.rsc" >/dev/null || { warn "could not upload change-$id.rsc"; exit 1; }
  for name in "undo-$id.rsc" "change-$id.rsc"; do
    [ -n "$(ros "$host" "/file print where name=\"$name\"")" ] \
      || { warn "$name is not on the device after the upload"; exit 1; }
  done

  # The binary backup is the only step allowed to fail: it needs a policy right #485 withholds, and
  # the undo file does not depend on it. Say so and carry on armed.
  ros "$host" "/system backup save name=pre-$id" >/dev/null
  if [ -z "$(ros "$host" "/file print where name=\"pre-$id.backup\"")" ]; then
    say "no binary fallback for this run: the device did not write pre-$id.backup"
  fi

  detail=$(ros "$host" "/system scheduler add name=revert-$id policy=read,write,test interval=0s start-time=$start on-event=\"/import file=undo-$id.rsc\"")
  [ -z "$detail" ] || { warn "the device refused the timer: $detail"; exit 1; }

  detail=$(ros "$host" '/system scheduler print detail')
  printf '%s\n' "$detail" | grep -q "name=\"revert-$id\"" \
    || { warn "revert-$id is not on the device after it was added"; exit 1; }
  printf '%s\n' "$detail" | grep -q "start-time=$start" \
    || { warn "revert-$id did not keep start-time=$start"; exit 1; }

  say "armed revert-$id on $target for $start device time (window $window)"
  say "the entry runs /import file=undo-$id.rsc once; the change has not been applied"
}

# ------------------------------------------------------------------ argument parsing
major=""; change=""; export_file=""; verbose_export=""; target=""; window="$SWITCH_CHANGE_WINDOW"
expect=""; assume_yes=0; dry_run=0; arm_only=0; mode=""

while [ $# -gt 0 ]; do
  case "$1" in
    --render-undo) mode="render" ;;
    --noise-free)  mode="noise" ;;
    --list-targets) mode="list" ;;
    --help|-h)     usage ;;
    --target)      target="$2"; shift ;;
    --change)      change="$2"; shift ;;
    --major)       major="$2"; shift ;;
    --export)      export_file="$2"; shift ;;
    --verbose-export) verbose_export="$2"; shift ;;
    --window)      window="$2"; shift ;;
    --expect)      expect="$2"; shift ;;
    --yes)         assume_yes=1 ;;
    --dry-run)     dry_run=1 ;;
    --arm-only)    arm_only=1 ;;
    *) die "unknown argument '$1'" ;;
  esac
  shift
done

case "$mode" in
  list) targets ;;
  noise) noise_free ;;
  render)
    [ -n "$change" ] || die "--render-undo needs --change"
    [ -n "$major" ] || die "--render-undo needs --major"
    [ -n "$export_file" ] || die "--render-undo needs --export"
    [ -n "$verbose_export" ] || die "--render-undo needs --verbose-export"
    for f in "$change" "$export_file" "$verbose_export"; do
      [ -r "$f" ] || { printf 'switch-change.sh: cannot read %s\n' "$f" >&2; exit 1; }
    done
    render_undo "$major" "$change" "$export_file" "$verbose_export" ;;
  "")
    [ -n "$target" ] || die "either --target, --render-undo, --noise-free, --list-targets or --help is required"
    [ -n "$change" ] || die "--target needs --change"
    host_of "$target" >/dev/null || die "unknown target '$target'"
    change_run ;;
esac
