#!/usr/bin/env bash
# Every non-public Traefik route must carry the lan-only middleware.
#
# The home router forwards 80 and 443 to the cluster, and Traefik chooses a route
# by the Host header alone, so a route answers the internet whether or not its
# hostname resolves publicly. This checker renders every chart, reads the routes,
# and reports the ones that are not gated.
#
#   hack/check-exposure.sh                 # every chart
#   hack/check-exposure.sh --only media    # charts whose path contains "media"
#   hack/check-exposure.sh --self-test     # the parser's unit tests
#
# Exit: 0 clean, 1 violations, 2 some charts could not be rendered.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKER="$ROOT/hack/check_exposure.py"
HELM="${HELM:-helm}"
ONLY=""
SELF_TEST=0

while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) SELF_TEST=1; shift ;;
    --only) ONLY="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ "$SELF_TEST" = 1 ]; then
  python3 -m unittest discover -s "$ROOT/hack/tests" -v
  exit $?
fi

violations=0
skipped=0
routes=0
tmpdirs=()

cleanup() {
  for dir in "${tmpdirs[@]}"; do rm -rf "$dir"; done
}
trap cleanup EXIT

render() {
  local chart="$1" work="$1" repoconfig cache
  if grep -q '^dependencies:' "$chart/Chart.yaml" 2>/dev/null && [ ! -d "$chart/charts" ]; then
    work="$(mktemp -d)"
    tmpdirs+=("$work")
    cp -R "$chart/." "$work/"
    # Build dependencies against a private repo config so the operator's helm is untouched.
    repoconfig="$work/.repositories.yaml"
    userconfig="$("$HELM" env HELM_REPOSITORY_CONFIG 2>/dev/null || true)"
    usercache="$("$HELM" env HELM_REPOSITORY_CACHE 2>/dev/null || true)"
    if [ -n "$userconfig" ] && [ -f "$userconfig" ]; then
      cp "$userconfig" "$repoconfig"
    fi
    HELM_REPOSITORY_CONFIG="$repoconfig" HELM_REPOSITORY_CACHE="$usercache" \
      "$HELM" repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1
    if ! HELM_REPOSITORY_CONFIG="$repoconfig" HELM_REPOSITORY_CACHE="$usercache" \
      "$HELM" dependency build "$work" >/dev/null 2>&1; then
      return 3
    fi
    HELM_REPOSITORY_CONFIG="$repoconfig" HELM_REPOSITORY_CACHE="$usercache" \
      "$HELM" template test "$work" -f "$work/values.yaml"
    return $?
  fi
  "$HELM" template test "$work" -f "$work/values.yaml"
}

while IFS= read -r chart_yaml; do
  chart="$(dirname "$chart_yaml")"
  rel="${chart#"$ROOT"/}"
  if [ -n "$ONLY" ] && [[ "$rel" != *"$ONLY"* ]]; then
    continue
  fi

  errfile="$(mktemp)"
  tmpdirs+=("$errfile")
  rendered="$(render "$chart" 2>"$errfile")"
  status=$?
  if [ $status -eq 3 ]; then
    echo "SKIP $rel (helm dependency build failed)" >&2
    skipped=1
    continue
  elif [ $status -ne 0 ]; then
    echo "FAIL $rel (helm template failed)" >&2
    tail -5 "$errfile" >&2
    violations=1
    continue
  fi
  # helm writes warnings to stderr; they are not part of the rendered manifest.
  if [ -s "$errfile" ]; then
    grep -v '^level=INFO' "$errfile" >&2 || true
  fi

  count="$(printf '%s\n' "$rendered" | grep -c '^kind: Ingress')"
  routes=$((routes + count))

  if ! out="$(printf '%s\n' "$rendered" | python3 "$CHECKER" -)"; then
    while IFS= read -r line; do
      printf '%s: %s\n' "$rel" "$line"
    done <<< "$out"
    violations=1
  fi

  if [ -f "$chart/values.yaml" ] && ! out="$(python3 "$CHECKER" "$chart/values.yaml")"; then
    printf '%s: values.yaml: %s\n' "$rel" "$out"
    violations=1
  fi

  # A chart that renders nothing passes vacuously; say so, so a missing route is visible.
  printf '%-52s %s routes\n' "$rel" "$count"
done < <(find "$ROOT/cluster/helm" -name Chart.yaml | sort)

# Charts that exist only as values for an external chart have no Chart.yaml, so they
# render nothing here. Their route lives in an annotation; check it as text.
while IFS= read -r values; do
  dir="$(dirname "$values")"
  [ -f "$dir/Chart.yaml" ] && continue
  rel="${dir#"$ROOT"/}"
  if [ -n "$ONLY" ] && [[ "$rel" != *"$ONLY"* ]]; then
    continue
  fi
  grep -qE '^[[:space:]]*[^[:space:]#].*router\.middlewares' "$values" || continue
  if ! grep -q 'kube-system-lan-only@kubernetescrd' "$values"; then
    echo "$rel: values.yaml: router.middlewares does not include kube-system-lan-only@kubernetescrd"
    violations=1
  fi
  printf '%-52s static (values-only)\n' "$rel"
done < <(find "$ROOT/cluster/helm" -name values.yaml | sort)

# The Traefik dashboard is rendered from a hand-applied HelmChartConfig, not a chart.
if [ -z "$ONLY" ]; then
  dashboard="$ROOT/cluster/infrastructure/traefik/traefik-config.yaml"
  if ! out="$(python3 "$CHECKER" "$dashboard")"; then
    printf 'cluster/infrastructure/traefik/traefik-config.yaml: %s\n' "$out"
    violations=1
  fi
fi

# cluster/argocd and the Traefik config are CRLF by contract; an LF rewrite is a whole-file diff.
if [ -z "$ONLY" ]; then
  while IFS= read -r file; do
    if ! file "$ROOT/$file" | grep -q CRLF; then
      echo "$file: expected CRLF line endings"
      violations=1
    fi
  done < <(cd "$ROOT" && { git ls-files 'cluster/argocd/**'; echo 'cluster/infrastructure/traefik/traefik-config.yaml'; })
fi

echo "checked: $routes routes"
if [ $skipped -ne 0 ]; then
  echo "result: INCOMPLETE (a chart could not be rendered)"
  exit 2
fi
if [ $violations -ne 0 ]; then
  echo "result: FAIL"
  exit 1
fi
echo "result: OK"
exit 0
