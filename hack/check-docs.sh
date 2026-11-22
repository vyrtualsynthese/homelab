#!/usr/bin/env bash
# The documentation checks: four scripts, one gate.
#
#   hack/check-docs.sh                  # against origin/develop
#   hack/check-docs.sh --base main      # if the branch was cut from main
#
# 1. gen_docs.py --check     every generated page still matches its source
# 2. check_docs_links.py     every relative link between pages resolves
# 3. check_docs.py           cited paths, nav, banner, catalogue, secrets
# 4. check_docs_staleness.py pages older than their sources (report only, never fails)
#
# Exit: 0 clean, 1 a check reported.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BASE="origin/develop"

while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

status=0

step() {
  local name="$1"
  shift
  if ! "$@"; then
    echo "FAIL $name"
    status=1
  fi
}

step "generated pages" python3 hack/gen_docs.py --check
step "relative links" python3 hack/check_docs_links.py
step "documentation rules" python3 hack/check_docs.py --base "$BASE"

# The staleness report prints what it found and always exits 0: it files work, it does not gate.
if [ -f hack/check_docs_staleness.py ]; then
  python3 hack/check_docs_staleness.py || true
fi

exit "$status"
