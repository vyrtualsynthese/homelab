#!/usr/bin/env bash
# Every chart's appVersion must name the version of the image it runs.
#
# `appVersion` and `image.tag` are the same number written twice, and Renovate
# moves the tag without touching Chart.yaml. This checker compares the two: the
# app version inside the tag (the leading number, `v` and packaging suffix
# dropped) must equal `appVersion`. A chart that pins no versioned tag states
# why on an `# appVersion-check: exempt — <reason>` line.
#
# The rule is requirement 1 of docs/charts/README.md.
#
#   hack/check-app-version.sh                  # every chart
#   hack/check-app-version.sh --only media     # charts whose path contains "media"
#   hack/check-app-version.sh --self-test      # the checker's unit tests
#
# Exit: 0 clean, 1 violations.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKER="$ROOT/hack/check_app_version.py"
ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) exec python3 -m unittest discover -s "$ROOT/hack/tests" -p 'test_check_app_version.py' -v ;;
    --only) ONLY="${2:-}"; shift ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

violations=0
checked=0
exempt=0

while IFS= read -r chart_yaml; do
  chart="$(dirname "$chart_yaml")"
  rel="${chart#"$ROOT"/}"
  if [ -n "$ONLY" ] && [[ "$rel" != *"$ONLY"* ]]; then
    continue
  fi
  checked=$((checked + 1))
  if ! out="$(python3 "$CHECKER" "$chart")"; then
    printf 'FAIL %s: %s\n' "$rel" "$out"
    violations=1
    continue
  fi
  if grep -qE '^[[:space:]]*#[[:space:]]*appVersion-check:[[:space:]]*exempt[[:space:]]*[—-]' "$chart_yaml"; then
    exempt=$((exempt + 1))
    printf '%-52s exempt\n' "$rel"
    continue
  fi
  declared="$(grep -m1 '^appVersion:' "$chart_yaml" | sed 's/^appVersion:[[:space:]]*//; s/["'"'"']//g; s/[[:space:]]*#.*$//')"
  printf '%-52s %s\n' "$rel" "$declared"
done < <(find "$ROOT/cluster/helm" -name Chart.yaml | sort)

echo "checked: $checked charts, $exempt exempt"
if [ "$violations" -ne 0 ]; then
  echo "result: FAIL"
  exit 1
fi
echo "result: OK"
exit 0
