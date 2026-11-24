#!/usr/bin/env bash
# network-audit.sh — read-only configuration dump of the network hardware, printed as Markdown.
#
#   ./network-audit.sh > /tmp/opencode/network-audit.md
#   NET_AUDIT_FROM_FIXTURE=hack/fixtures/network ./network-audit.sh   # no device touched
#   ./network-audit.sh --list-targets
#   ./network-audit.sh --redact-routeros < some-export
#   ./network-audit.sh --help
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

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0; }
die() { printf 'network-audit.sh: %s\n' "$1" >&2; exit 2; }

# One "name host" pair per line, from NET_AUDIT_TARGETS.
targets() {
  local pair name host
  for pair in $NET_AUDIT_TARGETS; do
    name="${pair%%=*}"; host="${pair#*=}"
    [ "$name" != "$host" ] || die "target '$pair' is not name=host"
    printf '%s %s\n' "$name" "$host"
  done
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
    snmp = (line ~ /^[[:space:]]*\/snmp[[:space:]]+community([[:space:]]|$)/)
    while (match(line, /[A-Za-z0-9._-]+=/)) {
      key = substr(line, RSTART, RLENGTH - 1)
      out = out substr(line, 1, RSTART - 1) key "="
      line = substr(line, RSTART + RLENGTH)
      # On an /snmp community object the community string itself is carried by name=.
      if (sensitive(key) || (snmp && key == "name")) {
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

case "${1:-}" in
  --help|-h) usage ;;
  --list-targets) targets; exit 0 ;;
  --redact-routeros) redact_routeros; exit 0 ;;
  "") ;;
  *) die "unknown option '$1' (try --help)" ;;
esac
