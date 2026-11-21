#!/usr/bin/env bash
# Every chart must pass `helm lint` and `helm template`: requirement 11 of
# docs/charts/README.md. No CI runs either, so this is the bulk gate.
#
# A chart whose dependencies are not vendored is copied to a temporary directory
# and `helm dependency build` runs there against a private repo config, so the
# operator's helm settings stay untouched.
#
#   hack/check-charts.sh                 # every chart
#   hack/check-charts.sh --only media    # charts whose path contains "media"
#
# Exit: 0 clean, 1 a chart failed, 2 a dependency build failed.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELM="${HELM:-helm}"
ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

tmpdirs=()
cleanup() {
  for dir in "${tmpdirs[@]}"; do rm -rf "$dir"; done
}
trap cleanup EXIT

failed=0
unbuilt=0
checked=0

fail() {
  local rel="$1" step="$2" errfile="$3"
  echo "FAIL $rel ($step)"
  tail -5 "$errfile" | sed 's/^/    /'
  failed=1
}

while IFS= read -r chart_yaml; do
  chart="$(dirname "$chart_yaml")"
  rel="${chart#"$ROOT"/}"
  if [ -n "$ONLY" ] && [[ "$rel" != *"$ONLY"* ]]; then
    continue
  fi

  work="$chart"
  if grep -q '^dependencies:' "$chart/Chart.yaml" 2>/dev/null && [ ! -d "$chart/charts" ]; then
    work="$(mktemp -d)"
    tmpdirs+=("$work")
    cp -R "$chart/." "$work/"
    repoconfig="$work/.repositories.yaml"
    usercache="$("$HELM" env HELM_REPOSITORY_CACHE 2>/dev/null || true)"
    if ! HELM_REPOSITORY_CONFIG="$repoconfig" HELM_REPOSITORY_CACHE="$usercache" \
      "$HELM" dependency build "$work" >/dev/null 2>&1; then
      echo "SKIP $rel (helm dependency build failed)" >&2
      unbuilt=1
      continue
    fi
  fi

  errfile="$(mktemp)"
  tmpdirs+=("$errfile")
  if ! "$HELM" lint "$work" -f "$chart/values.yaml" >"$errfile" 2>&1; then
    fail "$rel" "helm lint" "$errfile"
    continue
  fi
  if ! "$HELM" template test "$work" -f "$chart/values.yaml" >"$errfile" 2>&1; then
    fail "$rel" "helm template" "$errfile"
    continue
  fi
  checked=$((checked + 1))
done < <(find "$ROOT/cluster/helm" -name Chart.yaml | sort)

echo "checked: $checked charts"
if [ "$unbuilt" -ne 0 ]; then
  echo "result: INCOMPLETE (a chart's dependencies could not be built)"
  exit 2
fi
if [ "$failed" -ne 0 ]; then
  echo "result: FAIL"
  exit 1
fi
echo "result: OK"
exit 0
