#!/usr/bin/env bash
# Every key in a rendered config file must exist in the pinned chart's schema.
#
# helm template renders any key; the application refuses to start on one it does
# not know, and that failure only shows after a sync. This checker renders each
# chart listed in hack/config-key-checks.yaml and compares its config file
# against the key tree the dependency's own values.yaml declares.
#
#   hack/check-config-keys.sh                  # every listed chart
#   hack/check-config-keys.sh --only filebrowser
#   hack/check-config-keys.sh --self-test      # the checker's unit tests
#
# Exit: 0 clean, 1 violations, 2 a chart could not be checked.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${1:-}" in
  --self-test)
    exec python3 -m unittest discover -s "$ROOT/hack/tests" -p 'test_check_config_keys.py' -v ;;
  -h|--help)
    sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
esac

exec python3 "$ROOT/hack/check_config_keys.py" --manifest "$ROOT/hack/config-key-checks.yaml" "$@"
