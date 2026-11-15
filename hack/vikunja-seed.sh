#!/usr/bin/env bash
# Seed the repository boards on the task board (Vikunja), idempotently.
#
# For every repository in REPOS below, this creates — only when it is missing:
#   the project       PUT  /api/v1/projects
#   one kanban view   PUT  /api/v1/projects/{project}/views
#   four buckets      PUT  /api/v1/projects/{project}/views/{view}/buckets
#   the done bucket   POST /api/v1/projects/{project}/views/{view}
# and once for the whole board:
#   the saved filter  PUT  /api/v1/filters
#
# Every object is matched by title, so a second run creates nothing and prints
# "existing" for each line. Nothing is ever deleted. A bucket the script did not
# create is printed as "kept" and left untouched, because a default column that
# happens to be empty is indistinguishable from one holding work.
#
#   hack/vikunja-seed.sh --dry-run   # read the board, print every call, write nothing
#   hack/vikunja-seed.sh             # create what is missing
#   hack/vikunja-seed.sh --verify    # read back and fail unless the boards are shaped right
#
# Environment:
#   VIKUNJA_API_TOKEN  required. An operator API token, made in the board under
#                      Settings -> API Tokens. Never echoed by this script.
#   VIKUNJA_URL        default https://vikunja.kub.local
#   VIKUNJA_CACERT     optional. Verify the TLS chain against this CA file
#                      instead of accepting the certificate with -k.
#
# Exit: 0 success, 1 an HTTP call failed or a verification did not hold, 2 usage.
#
# The procedure, the token's scope and what to do when a line fails are in
# docs/runbooks/vikunja-board-seed.md.

set -euo pipefail

# ---------------------------------------------------------------------------
# The repositories. One line per project; adding a repository means adding a
# line here and re-running. Nothing else in the file depends on the list.
# ---------------------------------------------------------------------------
REPOS=(
  homelab
  config
  scripts
  ashuvidz-website
  ashudev-website
  selfhosted-helmcharts
)

VIEW_TITLE="Board"
BUCKETS=(Ready Doing "Waiting for validation" Done)
DONE_TITLE="Done"
FILTER_TITLE="Awaiting my review"

VIKUNJA_URL="${VIKUNJA_URL:-https://vikunja.kub.local}"
VIKUNJA_URL="${VIKUNJA_URL%/}"
VIKUNJA_API_TOKEN="${VIKUNJA_API_TOKEN:-}"
VIKUNJA_CACERT="${VIKUNJA_CACERT:-}"
API_PATH="/api/v1"

DRY_RUN=0
VERIFY=0
W_PLANNED=0
C_CREATED=0
C_EXISTING=0
C_KEPT=0

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT
trap 'rc=$?; printf "vikunja-seed: internal error at line %s (exit %s): %s\n" "$LINENO" "$rc" "$BASH_COMMAND" >&2; exit "$rc"' ERR

usage() {
  sed -n '2,31p' "${BASH_SOURCE[0]}"
}

die() {
  printf 'vikunja-seed: %s\n' "$*" >&2
  exit 1
}

say() {
  if [ -n "${4-}" ]; then
    printf '%-8s %-44s %-13s %s\n' "$1" "${2-}" "${3-}" "$4"
  else
    printf '%-8s %-44s %s\n' "$1" "${2-}" "${3-}"
  fi
}

note() {
  printf '         %s\n' "$*"
}

# ---------------------------------------------------------------------------
# HTTP. One place that builds the request, so the token is never printed and
# every failure is reported the same way: method, path, status, response body.
# ---------------------------------------------------------------------------
http() {
  local method="$1" path="$2" body="${3-}"
  local -a args=(-sS --max-time 30 -o "$BODY_FILE" -w '%{http_code}'
                 "${TLS_ARGS[@]}" -X "$method"
                 -H "Authorization: Bearer $VIKUNJA_API_TOKEN"
                 -H 'Content-Type: application/json'
                 -H 'Accept: application/json')
  if [ -n "$body" ]; then
    args+=(--data-binary "$body")
  fi
  if ! API_STATUS="$(curl "${args[@]}" "$VIKUNJA_URL$API_PATH$path")"; then
    printf 'FAIL %s %s%s — curl could not complete (DNS, connection or TLS; the message above says which)\n' \
      "$method" "$API_PATH" "$path" >&2
    exit 1
  fi
  API_BODY="$(cat "$BODY_FILE")"
  if [ "${API_STATUS:0:1}" != "2" ]; then
    printf 'FAIL %s %s%s — HTTP %s\n%s\n' \
      "$method" "$API_PATH" "$path" "$API_STATUS" "$API_BODY" >&2
    exit 1
  fi
}

# A read is always safe: it runs in both modes. The dry run prints it, so the
# operator sees the whole conversation and not only the writes.
read_endpoint() {
  if [ "$DRY_RUN" = 1 ]; then
    note "-> GET $API_PATH$1"
  fi
  http GET "$1"
}

# A write sends in a real run, and only prints in a dry run. API_BODY is left
# empty after a planned write, so a caller must not read an id from it.
write_or_plan() {
  local method="$1" path="$2" body="$3"
  if [ "$DRY_RUN" = 1 ]; then
    note "-> $method $API_PATH$path $body"
    API_BODY="{}"
    W_PLANNED=$((W_PLANNED + 1))
    return 0
  fi
  http "$method" "$path" "$body"
}

# ---------------------------------------------------------------------------
# One repository.
# ---------------------------------------------------------------------------
seed_project() {
  local repo="$1"
  local project_id="" view_id="" done_id="" done_current="" kind="" body=""
  local p_new=0 v_new=0
  local views_json="" view_json="" buckets_json="" known_json=""
  local bid="" btitle="" title="" bucket_id=""

  printf '\n'

  # ---- the project -------------------------------------------------------
  read_endpoint /projects
  project_id="$(jq -r --arg t "$repo" '[.[] | select(.title == $t)][0].id // empty' <<<"$API_BODY")"
  if [ -n "$project_id" ]; then
    say project "$repo" existing "id=$project_id"
    C_EXISTING=$((C_EXISTING + 1))
  else
    p_new=1
    body="$(jq -cn --arg t "$repo" '{title: $t}')"
    write_or_plan PUT /projects "$body"
    if [ "$DRY_RUN" = 1 ]; then
      project_id="<new project $repo>"
      say project "$repo" would-create
    else
      project_id="$(jq -r '.id // empty' <<<"$API_BODY")"
      if [ -z "$project_id" ]; then
        die "the board accepted project \"$repo\" but returned no id — body: $API_BODY"
      fi
      say project "$repo" created "id=$project_id"
      C_CREATED=$((C_CREATED + 1))
    fi
  fi

  # ---- the kanban view ---------------------------------------------------
  if [ "$p_new" = 1 ]; then
    views_json='[]'          # a project created a second ago has no views
  else
    read_endpoint "/projects/$project_id/views"
    views_json="$API_BODY"
  fi
  view_json="$(jq -c --arg t "$VIEW_TITLE" '[.[] | select(.title == $t)][0] // empty' <<<"$views_json")"
  if [ -n "$view_json" ] && [ "$view_json" != "null" ]; then
    kind="$(jq -r '.view_kind // empty' <<<"$view_json")"
    if [ "$kind" != "kanban" ]; then
      die "project \"$repo\" already has a view titled \"$VIEW_TITLE\", but it is a \"${kind:-unknown}\" view. Rename or delete it in the board UI and re-run; this script will not convert an existing view."
    fi
    view_id="$(jq -r '.id // empty' <<<"$view_json")"
    done_current="$(jq -r '.done_bucket_id // empty' <<<"$view_json")"
    say view "$repo / $VIEW_TITLE" existing "id=$view_id"
    C_EXISTING=$((C_EXISTING + 1))
  else
    v_new=1
    # bucket_configuration_mode defaults to "none", which fixes each card to the
    # bucket it was created in. "manual" is what lets a person drag a card from
    # "Doing" to "Waiting for validation".
    body="$(jq -cn --arg t "$VIEW_TITLE" \
      '{title: $t, view_kind: "kanban", bucket_configuration_mode: "manual"}')"
    write_or_plan PUT "/projects/$project_id/views" "$body"
    if [ "$DRY_RUN" = 1 ]; then
      view_id="<new view $VIEW_TITLE>"
      say view "$repo / $VIEW_TITLE" would-create
    else
      view_id="$(jq -r '.id // empty' <<<"$API_BODY")"
      if [ -z "$view_id" ]; then
        die "the board accepted view \"$VIEW_TITLE\" in \"$repo\" but returned no id — body: $API_BODY"
      fi
      say view "$repo / $VIEW_TITLE" created "id=$view_id"
      C_CREATED=$((C_CREATED + 1))
    fi
  fi

  # ---- the buckets -------------------------------------------------------
  if [ "$v_new" = 1 ] || [ "$p_new" = 1 ]; then
    buckets_json='[]'        # a view created a second ago may still have defaults
    done_current=""          # ...and its done bucket is unset by definition
  else
    read_endpoint "/projects/$project_id/views/$view_id/buckets"
    buckets_json="$API_BODY"
  fi

  known_json="$(jq -cn --args '$ARGS.positional' "${BUCKETS[@]}")"
  for title in "${BUCKETS[@]}"; do
    bucket_id="$(jq -r --arg t "$title" '[.[] | select(.title == $t)][0].id // empty' <<<"$buckets_json")"
    if [ -n "$bucket_id" ]; then
      say bucket "$repo / $VIEW_TITLE / $title" existing "id=$bucket_id"
      C_EXISTING=$((C_EXISTING + 1))
    else
      body="$(jq -cn --arg t "$title" '{title: $t}')"
      write_or_plan PUT "/projects/$project_id/views/$view_id/buckets" "$body"
      if [ "$DRY_RUN" = 1 ]; then
        bucket_id="<new bucket $title>"
        say bucket "$repo / $VIEW_TITLE / $title" would-create
      else
        bucket_id="$(jq -r '.id // empty' <<<"$API_BODY")"
        if [ -z "$bucket_id" ]; then
          die "the board accepted bucket \"$title\" in \"$repo\" but returned no id — body: $API_BODY"
        fi
        say bucket "$repo / $VIEW_TITLE / $title" created "id=$bucket_id"
        C_CREATED=$((C_CREATED + 1))
      fi
    fi
    if [ "$title" = "$DONE_TITLE" ]; then
      done_id="$bucket_id"
    fi
  done

  # Columns this script did not create. Named, never moved, never deleted.
  while IFS=$'\t' read -r bid btitle; do
    if [ -z "$bid" ]; then
      continue
    fi
    say bucket "$repo / $VIEW_TITLE / $btitle" kept "id=$bid"
    C_KEPT=$((C_KEPT + 1))
  done < <(jq -r --argjson known "$known_json" \
    '.[] | select(.title as $t | $known | index($t) | not) | "\(.id)\t\(.title)"' <<<"$buckets_json")

  # ---- the done bucket ---------------------------------------------------
  # This is the one setting that makes the board honest: without it, a card in
  # "Done" stays open as far as Vikunja and every filter are concerned.
  if [ -n "$done_id" ] && [ "$done_current" = "$done_id" ]; then
    say done "$repo / $VIEW_TITLE" existing "bucket $done_id ($DONE_TITLE)"
    C_EXISTING=$((C_EXISTING + 1))
  elif [ "$DRY_RUN" = 1 ]; then
    write_or_plan POST "/projects/$project_id/views/$view_id" \
      "$(jq -cn --arg id "$done_id" '{done_bucket_id: $id}')"
    say done "$repo / $VIEW_TITLE" would-create "bucket $done_id ($DONE_TITLE)"
  else
    write_or_plan POST "/projects/$project_id/views/$view_id" \
      "$(jq -cn --argjson id "$done_id" '{done_bucket_id: $id}')"
    say done "$repo / $VIEW_TITLE" created "bucket $done_id ($DONE_TITLE)"
    C_CREATED=$((C_CREATED + 1))
  fi
}

# ---------------------------------------------------------------------------
# The saved filter. Created once, never updated: if it exists, the operator's
# own edits win, and a filter is not part of the board's shape.
# ---------------------------------------------------------------------------
seed_filter() {
  local filter_id="" body=""

  printf '\n'
  read_endpoint /filters
  filter_id="$(jq -r --arg t "$FILTER_TITLE" '[.[] | select(.title == $t)][0].id // empty' <<<"$API_BODY")"
  if [ -n "$filter_id" ]; then
    say filter "$FILTER_TITLE" existing "id=$filter_id"
    C_EXISTING=$((C_EXISTING + 1))
    return 0
  fi

  body="$(jq -cn --arg t "$FILTER_TITLE" \
    '{title: $t, filters: {sort_by: ["created"], order_by: ["asc"], filter: "done = false"}}')"
  write_or_plan PUT /filters "$body"
  if [ "$DRY_RUN" = 1 ]; then
    say filter "$FILTER_TITLE" would-create
    return 0
  fi
  filter_id="$(jq -r '.id // empty' <<<"$API_BODY")"
  if [ -z "$filter_id" ]; then
    die "the board accepted the saved filter but returned no id — body: $API_BODY"
  fi
  say filter "$FILTER_TITLE" created "id=$filter_id"
  C_CREATED=$((C_CREATED + 1))
}

# ---------------------------------------------------------------------------
# Read the boards back. Never writes. Exits 1 with the offending project named.
# ---------------------------------------------------------------------------
verify() {
  local failures=0 repo="" projects_json="" pid="" views_json="" vjson=""
  local view_id="" kind="" buckets_json="" title="" missing="" done_id=""
  local current="" problems="" present=""

  printf 'Verifying %d boards against %s\n\n' "${#REPOS[@]}" "$VIKUNJA_URL"

  http GET /projects
  projects_json="$API_BODY"

  for repo in "${REPOS[@]}"; do
    pid="$(jq -r --arg t "$repo" '[.[] | select(.title == $t)][0].id // empty' <<<"$projects_json")"
    if [ -z "$pid" ]; then
      printf 'FAIL %-22s no project titled "%s"\n' "$repo" "$repo"
      failures=$((failures + 1))
      continue
    fi

    http GET "/projects/$pid/views"
    views_json="$API_BODY"
    vjson="$(jq -c --arg t "$VIEW_TITLE" '[.[] | select(.title == $t)][0] // empty' <<<"$views_json")"
    if [ -z "$vjson" ] || [ "$vjson" = "null" ]; then
      present="$(jq -r '[.[].title] | join(", ")' <<<"$views_json")"
      printf 'FAIL %-22s no view titled "%s" (views present: %s)\n' \
        "$repo" "$VIEW_TITLE" "${present:-none}"
      failures=$((failures + 1))
      continue
    fi

    view_id="$(jq -r '.id // empty' <<<"$vjson")"
    kind="$(jq -r '.view_kind // empty' <<<"$vjson")"
    current="$(jq -r '.done_bucket_id // empty' <<<"$vjson")"

    http GET "/projects/$pid/views/$view_id/buckets"
    buckets_json="$API_BODY"

    missing=""
    for title in "${BUCKETS[@]}"; do
      if ! jq -e --arg t "$title" 'any(.[]; .title == $t)' >/dev/null <<<"$buckets_json"; then
        missing="${missing:+$missing, }$title"
      fi
    done
    done_id="$(jq -r --arg t "$DONE_TITLE" '[.[] | select(.title == $t)][0].id // empty' <<<"$buckets_json")"

    problems=""
    if [ "$kind" != "kanban" ]; then
      problems="view is \"${kind:-unknown}\", expected \"kanban\""
    fi
    if [ -n "$missing" ]; then
      problems="${problems:+$problems; }missing buckets: $missing"
    fi
    if [ -z "$done_id" ]; then
      problems="${problems:+$problems; }no bucket titled \"$DONE_TITLE\", so no done bucket can be set"
    elif [ "$current" != "$done_id" ]; then
      problems="${problems:+$problems; }done_bucket_id is ${current:-unset}, expected $done_id (\"$DONE_TITLE\")"
    fi

    if [ -n "$problems" ]; then
      printf 'FAIL %-22s %s\n' "$repo" "$problems"
      failures=$((failures + 1))
    else
      printf 'PASS %-22s view %s, %d buckets, done bucket %s\n' \
        "$repo" "$view_id" "${#BUCKETS[@]}" "$done_id"
    fi
  done

  http GET /filters
  if jq -e --arg t "$FILTER_TITLE" 'any(.[]; .title == $t)' >/dev/null <<<"$API_BODY"; then
    printf 'PASS saved filter "%s"\n' "$FILTER_TITLE"
  else
    printf 'FAIL saved filter "%s" is missing\n' "$FILTER_TITLE"
    failures=$((failures + 1))
  fi

  printf '\n'
  if [ "$failures" -ne 0 ]; then
    printf 'verify: %d problem(s) found\n' "$failures"
    exit 1
  fi
  printf 'verify: all %d boards hold the shape the protocol expects\n' "${#REPOS[@]}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --verify) VERIFY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'vikunja-seed: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ "$DRY_RUN" = 1 ] && [ "$VERIFY" = 1 ]; then
  printf 'vikunja-seed: --dry-run and --verify cannot be combined\n' >&2
  exit 2
fi

if [ -z "$VIKUNJA_API_TOKEN" ]; then
  printf 'vikunja-seed: VIKUNJA_API_TOKEN is not set.\n' >&2
  printf 'Create an API token in the board (Settings -> API Tokens) and export it; see\n' >&2
  printf 'docs/runbooks/vikunja-board-seed.md Step 1.\n' >&2
  exit 2
fi

command -v curl >/dev/null 2>&1 || die "curl is not installed"
command -v jq >/dev/null 2>&1 || die "jq is not installed"

# The board's certificate is signed by the cluster's own CA, which is not in the
# system store on every machine. -k accepts it without checking it; setting
# VIKUNJA_CACERT checks the chain against that CA instead. The runbook says which
# to use and what each proves.
if [ -n "$VIKUNJA_CACERT" ]; then
  TLS_ARGS=(--cacert "$VIKUNJA_CACERT")
else
  TLS_ARGS=(-k)
fi

if [ "$VERIFY" = 1 ]; then
  verify
  exit 0
fi

printf 'Board %s, %d repositories: %s\n' "$VIKUNJA_URL" "${#REPOS[@]}" "${REPOS[*]}"
if [ "$DRY_RUN" = 1 ]; then
  printf 'Dry run: reading the board, sending no write.\n'
fi

for repo in "${REPOS[@]}"; do
  seed_project "$repo"
done
seed_filter

printf '\n'
if [ "$DRY_RUN" = 1 ]; then
  printf 'dry run: %d write(s) planned, none sent; %d object(s) already exist\n' \
    "$W_PLANNED" "$C_EXISTING"
  exit 0
fi
printf 'seeded: %d created, %d already present, %d kept (not created here)\n' \
  "$C_CREATED" "$C_EXISTING" "$C_KEPT"
