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

case "${1:-}" in
  --help|-h) usage ;;
  --list-targets) targets; exit 0 ;;
  "") ;;
  *) die "unknown option '$1' (try --help)" ;;
esac
