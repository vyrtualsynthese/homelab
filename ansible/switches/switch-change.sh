#!/usr/bin/env bash
# switch-change.sh — carry one RouterOS change onto a switch with an undo armed on the device.
#
#   ./switch-change.sh --render-undo --major 7 --change changes/x.rsc \
#        --export /tmp/opencode/pre.rsc --verbose-export /tmp/opencode/pre-verbose.rsc
#   ./switch-change.sh --noise-free < an-export.rsc     # drop comments and blanks, for a comparison
#   ./switch-change.sh --list-targets
#   ./switch-change.sh --help
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
    host_of "$target" >/dev/null || die "unknown target '$target'"
    # The read, arm, apply and verify phases land in a later change; until then this build renders
    # and refuses rather than half-touching a switch.
    printf 'switch-change.sh: the read, arm, apply and verify phases are not in this build\n' >&2
    exit 1 ;;
esac
