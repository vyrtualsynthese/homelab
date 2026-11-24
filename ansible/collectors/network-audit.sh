#!/usr/bin/env bash
# network-audit.sh — read-only configuration dump of the network hardware, printed as Markdown.
#
#   ./network-audit.sh > /tmp/opencode/network-audit.md
#   NET_AUDIT_FROM_FIXTURE=hack/fixtures/network ./network-audit.sh   # no device touched
#   ./network-audit.sh --list-targets
#   ./network-audit.sh --redact-routeros < some-export
#   ./network-audit.sh --help
#   ./network-audit.sh --dump <device> <slug>      # for tests: one capture, redacted
#   ./network-audit.sh --major <device>            # for tests: its RouterOS major, 6 or 7
#   ./network-audit.sh --export-command <major>    # for tests: the /export command for it
#
# A slug is one of resource, routerboard, package, identity, ethernet, address, route, export.
#
# Read-only: the RouterOS account carries the `read` policy, every command is a `print` or an
# `/export`, and no file is written anywhere. The page goes to stdout, warnings to stderr; exit 0
# on a printed page, 1 when no device answered, 2 on a usage error.
#
# NET_AUDIT_TARGETS      "name=host" pairs, default crs326=192.168.1.11 crs309=192.168.1.43
# NET_AUDIT_FROM_FIXTURE a directory of captured output; when set, no device is contacted
# NET_AUDIT_USER, NET_AUDIT_KEY, NET_AUDIT_DATE   account, its key, and the page's date
# NET_AUDIT_CONNECT_TIMEOUT, NET_AUDIT_CMD_TIMEOUT  seconds, default 5 and 20
#
# The MikroTik half is read in full. The Livebox half is partly remembered: what the box will not
# answer is supplied by the operator, and the page marks it as remembered.
set -uo pipefail

SCRIPT_VERSION="2026-11-24.1"
NET_AUDIT_TARGETS="${NET_AUDIT_TARGETS:-crs326=192.168.1.11 crs309=192.168.1.43}"
NET_AUDIT_USER="${NET_AUDIT_USER:-audit}"
NET_AUDIT_KEY="${NET_AUDIT_KEY:-$HOME/.ssh/network-audit}"
NET_AUDIT_DATE="${NET_AUDIT_DATE:-$(date +%F)}"
NET_AUDIT_FROM_FIXTURE="${NET_AUDIT_FROM_FIXTURE:-}"
NET_AUDIT_CONNECT_TIMEOUT="${NET_AUDIT_CONNECT_TIMEOUT:-5}"
NET_AUDIT_CMD_TIMEOUT="${NET_AUDIT_CMD_TIMEOUT:-20}"
LIVEBOX_HOST="${LIVEBOX_HOST:-192.168.1.1}"

usage() { sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0; }
die() { printf 'network-audit.sh: %s\n' "$1" >&2; exit 2; }

# Die on a list that is not "name=host", before any pair is printed or fetched: a malformed list
# that emitted the pairs it had already read would look like a good run that lost targets.
validate_targets() {
  local pair name host
  for pair in $NET_AUDIT_TARGETS; do
    name="${pair%%=*}"; host="${pair#*=}"
    [ "$name" != "$host" ] || die "target '$pair' is not name=host"
  done
}

# One "name host" pair per line, from NET_AUDIT_TARGETS.
targets() {
  local pair name host
  validate_targets
  for pair in $NET_AUDIT_TARGETS; do
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

# Replace the value of every attribute whose name says it holds a credential with <redacted>,
# quotes included. Reads stdin, writes stdout, one line in for one line out, and is the same
# filter every captured export goes through before it can reach the page committed to git.
redact_routeros() {
  awk '
  # A RouterOS line is a command followed by name=value pairs, some quoted and containing spaces.
  # A key is sensitive when, with - turned to _, it ends in one of the secret words or in _key, and
  # the match starts the key or follows an underscore; public_key is the one exemption, because
  # the public key of a peer is not a secret. The whole value, quotes included, becomes <redacted>.
  function sensitive(k,   t, i) {
    t = tolower(k); gsub(/-/, "_", t)
    if (t == "public_key") return 0
    i = match(t, /(password|passwd|secret|passphrase|psk|token|community|key)$/)
    if (!i) return 0
    return i == 1 || substr(t, i - 1, 1) == "_"
  }
  # Position of the quote that closes the string opening line, or 0 when it never closes. A
  # backslash escapes the character after it, which is how RouterOS writes a literal quote.
  function close_quote(s,   i) {
    for (i = 2; i <= length(s); i++) {
      if (substr(s, i, 1) == "\\") { i++; continue }
      if (substr(s, i, 1) == "\"") return i
    }
    return 0
  }
  {
    out = ""; line = $0
    # Two export shapes carry the community: RouterOS 7 writes the whole object on an absolute
    # command line, RouterOS 6 prints a `/snmp community` header and the `add`/`set` lines below
    # it, where the community is a `name=`. So the header opens a block, remembered across lines,
    # and any other absolute command closes it.
    snmp = 0
    if (line ~ /^[[:space:]]*\//) {
      snmp = (line ~ /^[[:space:]]*\/snmp[[:space:]]+community([[:space:]]|$)/)
      in_snmp = snmp
    }
    while (match(line, /[A-Za-z0-9._-]+=/)) {
      key = substr(line, RSTART, RLENGTH - 1)
      out = out substr(line, 1, RSTART - 1) key "="
      line = substr(line, RSTART + RLENGTH)
      # On an /snmp community object the community string itself is carried by name=.
      if (sensitive(key) || ((snmp || in_snmp) && key == "name")) {
        if (substr(line, 1, 1) == "\"") {
          end = close_quote(line)
          # Keep what follows only when the quote closes on the end of the line or on a space;
          # a quote that closes on a word means the value held one of its own, so the rest of the
          # line goes with it rather than being printed raw.
          if (end > 0 && (end == length(line) || substr(line, end + 1, 1) ~ /[[:space:]]/))
            line = substr(line, end + 1)
          else
            line = ""
        } else {
          sub(/^[^[:space:]]+/, "", line)
        }
        out = out "<redacted>"
      }
    }
    print out line
  }
  '
}

# One RouterOS command on a device, its output on stdout (ssh's own messages included, so a
# failure has a reason to print). Bounded on the connect and on the command; BatchMode keeps ssh
# from ever asking for a password, so a key the device refuses ends the call instead of hanging.
ros() {                                  # ros <host> <routeros command...>
  local host="$1"; shift
  timeout "$NET_AUDIT_CMD_TIMEOUT" ssh \
    -i "$NET_AUDIT_KEY" -o BatchMode=yes \
    -o ConnectTimeout="$NET_AUDIT_CONNECT_TIMEOUT" \
    -o StrictHostKeyChecking=accept-new \
    "$NET_AUDIT_USER@$host" "$*" 2>&1
}

slug_command() {                         # slug_command <slug> <major>
  case "$1" in
    resource)    printf '/system resource print' ;;
    routerboard) printf '/system routerboard print' ;;
    package)     printf '/system package print' ;;
    identity)    printf '/system identity print' ;;
    ethernet)    printf '/interface ethernet print' ;;
    address)     printf '/ip address print' ;;
    route)       printf '/ip route print' ;;
    export)      if [ "$2" = "6" ]; then printf '/export hide-sensitive'; else printf '/export terse'; fi ;;
    *) return 1 ;;
  esac
}

fetch() {                                # fetch <device> <slug>
  local device="$1" slug="$2" host major rc out
  host="$(host_of "$device")" || { printf '(unreachable: no such target)\n'; return 0; }
  if [ -n "$NET_AUDIT_FROM_FIXTURE" ]; then
    if [ -f "$NET_AUDIT_FROM_FIXTURE/$device.$slug.txt" ]; then
      redact_routeros < "$NET_AUDIT_FROM_FIXTURE/$device.$slug.txt"; return 0
    fi
    printf '(unreachable: no fixture for %s.%s)\n' "$device" "$slug"; return 0
  fi
  major="$(routeros_major "$device")"
  out="$(ros "$host" "$(slug_command "$slug" "$major")")"; rc=$?
  if [ "$rc" -ne 0 ] || printf '%s' "$out" | grep -q '^Bad command name\|^failure:\|Permission denied\|Connection refused\|Connection timed out\|No route to host'; then
    printf '(unreachable: %s)\n' "$(printf '%s' "$out" | head -1)"
    return 0
  fi
  printf '%s\n' "$out" | redact_routeros
}

# The major version decides one thing: `/export terse` on 7, `/export hide-sensitive` on 6, never
# the other way round. Read from the resource block directly, not through fetch, which asks this.
routeros_major() {                       # routeros_major <device> → 6, 7, or empty
  local device="$1" host
  host="$(host_of "$device")" || return 0
  if [ -n "$NET_AUDIT_FROM_FIXTURE" ]; then
    awk '/^ *version:/{print $2; exit}' "$NET_AUDIT_FROM_FIXTURE/$device.resource.txt" 2>/dev/null | cut -d. -f1
    return 0
  fi
  ros "$host" "/system resource print" | awk '/^ *version:/{print $2; exit}' | cut -d. -f1
}

case "${1:-}" in
  --help|-h) usage ;;
  --list-targets) targets; exit 0 ;;
  --redact-routeros) redact_routeros; exit 0 ;;
  --dump)
    [ $# -eq 3 ] || die "--dump takes <device> <slug>"
    validate_targets; fetch "$2" "$3"; exit 0 ;;
  --major)
    [ $# -eq 2 ] || die "--major takes <device>"
    validate_targets; routeros_major "$2"; exit 0 ;;
  --export-command)
    [ $# -eq 2 ] || die "--export-command takes <major>"
    slug_command export "$2"; printf '\n'; exit 0 ;;
  "") validate_targets ;;
  *) die "unknown option '$1' (try --help)" ;;
esac
