#!/usr/bin/env bash
# Nothing in the cluster or the manifests may sit on an API that 1.32-1.36 removes.
#
# Reads the served group/versions and the live volumes, then every manifest Argo CD
# applies under cluster/ and kubectl/, and matches them against the removal table in
# hack/check_api_removals.py. Run it before the climb, from the repository root, with
# kubectl pointed at the cluster.
#
#   hack/check-api-removals.sh               # scan the live cluster and the tree
#   hack/check-api-removals.sh --self-test   # the checker's unit tests
#
# Exit: 0 clean, 1 hits, 2 kubectl could not be read.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${1:-}" in
  --self-test)
    exec python3 -m unittest discover -s "$ROOT/hack/tests" -p 'test_check_api_removals.py' -v ;;
  -h|--help)
    sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
esac

exec python3 "$ROOT/hack/check_api_removals.py" "$@"
