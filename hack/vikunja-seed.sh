#!/usr/bin/env bash
# Shape the repository boards on the task board (Vikunja), idempotently.
#
# For every repository in REPOS below — or for the one named by --repo:
#   the project        PUT  /api/v1/projects          when it is missing
#   its kanban view    POST /api/v1/projects/{project}/views/{view}
#                      adopted, never created: renamed "Board" and wired in one
#                      full record (position, bucket mode, default and done column)
#   the six columns    PUT  /api/v1/projects/{project}/views/{view}/buckets
#                      a missing one is created, an old one is renamed in place
#   the labels         PUT  /api/v1/labels
# and once for the whole board:
#   two saved filters  PUT  /api/v1/filters
#   the retired filter DELETE /api/v1/filters/{id}
#
# The columns are Idea, Shaping, Backlog, Doing, Validation, Done. The six live
# repositories were built when that list was four long: "Ideas" becomes "Idea"
# and "Revision" becomes "Validation" by rename, so the column ids — which the
# saved filters store — survive. A column holding cards is never renamed; the
# run stops and says which column and how many.
#
# Every object is matched by title, so a second run creates nothing and prints
# "existing" for each line. No view and no column is ever deleted. A column the
# script does not name is printed as "kept" and left untouched, because a column
# that happens to be empty is indistinguishable from one holding work.
#
#   hack/vikunja-seed.sh --dry-run            # read the board, print every call, write nothing
#   hack/vikunja-seed.sh --repo homelab       # shape one project, leave the other six alone
#   hack/vikunja-seed.sh                      # create what is missing, everywhere
#   hack/vikunja-seed.sh --verify             # read back and fail unless the boards are shaped right
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
  ashuvidz-website
  ashudev-website
  selfhosted-helmcharts
  ashuworld
  ashuvidz-creator
)

# The view Vikunja gives every project. The script adopts it and renames it; it
# never creates a second kanban view, because two would hold two answers to
# "where is this task".
VIEW_TITLE="Board"
# A bucket renamed from an earlier shape is adopted in place, so its id — which
# the saved filters store — survives. Only buckets holding no cards are renamed.
BUCKET_RENAMES=("Ideas:Idea" "Revision:Validation")
# Reading order. Positions are written explicitly so the order is the design's,
# not whatever the UI last dragged.
BUCKETS=(Idea Shaping Backlog Doing Validation Done)
DONE_TITLE="Done"
DEFAULT_BUCKET_TITLE="Backlog"
BUCKET_MODE="manual"
FILTER_TITLE="Awaiting my review"

VIKUNJA_URL="${VIKUNJA_URL:-https://vikunja.kub.local}"
VIKUNJA_URL="${VIKUNJA_URL%/}"
VIKUNJA_API_TOKEN="${VIKUNJA_API_TOKEN:-}"
VIKUNJA_CACERT="${VIKUNJA_CACERT:-}"
API_PATH="/api/v1"
# The bucketed-task view lives under v2 on this instance. The v1 bucket listing
# always answers a count of 0, which would make every rename guard pass; this one
# path is read from v2 for that reason and nothing else is.
API_V2_PATH="/api/v2"

DRY_RUN=0
VERIFY=0
REPO_SCOPE=""
W_PLANNED=0
C_CREATED=0
C_EXISTING=0
C_UPDATED=0
C_KEPT=0

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT
trap 'rc=$?; printf "vikunja-seed: internal error at line %s (exit %s): %s\n" "$LINENO" "$rc" "$BASH_COMMAND" >&2; exit "$rc"' ERR

usage() {
  sed -n '2,/^set -euo pipefail$/{/^set -euo pipefail$/d;p}' "${BASH_SOURCE[0]}"
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

# Saved filters have no list endpoint on this API version: GET /filters answers
# 405. They do appear in GET /projects as pseudo-projects with negative ids, so
# that listing is where one is found — by title, and never by a positive id, so a
# real project of the same name cannot be mistaken for a filter.
list_saved_filters() {
  read_endpoint /projects
  API_BODY="$(jq -c '[.[] | select(.id < 0)]' <<<"$API_BODY")"
}

# One read against the v2 API, leaving API_PATH as it was.
read_endpoint_v2() {
  local saved="$API_PATH"
  if [ "$DRY_RUN" = 1 ]; then
    note "-> GET $API_V2_PATH$1"
  fi
  API_PATH="$API_V2_PATH"
  http GET "$1"
  API_PATH="$saved"
}

# How many cards each column holds, as "id<TAB>count" lines. The plain v1 bucket
# listing reports 0 for every column, so a rename guard reading it would never
# fire; this read is the one that tells the truth.
bucket_counts() {
  local project="$1" view="$2"
  read_endpoint_v2 "/projects/$project/views/$view/buckets/tasks"
  jq -r '.items[] | "\(.id)\t\(.count)"' <<<"$API_BODY"
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
# The position a column has in the reading order, 100 apart so a column added
# later can be slotted between two without a rewrite.
bucket_position() {
  local want="$1" index=0 title=""
  for title in "${BUCKETS[@]}"; do
    index=$((index + 1))
    if [ "$title" = "$want" ]; then
      printf '%d\n' $((100 * index))
      return 0
    fi
  done
  return 1
}

# Every bucket write goes through here: the endpoint replaces the record, so a
# body carrying only the title would clear the column's limit and its position.
put_bucket() {
  local project="$1" view="$2" id="$3" title="$4" position="$5" body=""
  body="$(jq -cn --arg t "$title" --argjson p "$position" '{title: $t, position: $p, limit: 0}')"
  write_or_plan POST "/projects/$project/views/$view/buckets/$id" "$body"
}

seed_project() {
  local repo="$1"
  local project_id="" view_id="" done_id="" default_id="" body=""
  local view_title_current="" view_position_current="" default_current=""
  local views_json="" view_json="" buckets_json="" known_json=""
  local bid="" btitle="" title="" bucket_id="" position="" current_position="" cards="" counts=""
  local mode_current="" done_current="" pair old new renamed=0
  local desired="" current=""

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

  # A project created on a planned run has no id to read a view from, and a
  # project created for real only carries the views Vikunja gives it. Shaping
  # that board is the next run's job.
  if ! [[ "$project_id" =~ ^[0-9]+$ ]]; then
    note "project \"$repo\" would be created here; its board is shaped on the next run"
    return 0
  fi

  # ---- the kanban view ---------------------------------------------------
  # Adopted, never created: Vikunja gives every project a kanban view, and a
  # second one would hold a second answer to "where is this task". The stock
  # view is renamed and wired here; its settings are written in one full record
  # after the columns exist, because two of them are column ids.
  read_endpoint "/projects/$project_id/views"
  views_json="$API_BODY"
  view_json="$(jq -c '[.[] | select(.view_kind == "kanban")][0] // empty' <<<"$views_json")"
  if [ -z "$view_json" ] || [ "$view_json" = "null" ]; then
    die "project \"$repo\" has no kanban view. Vikunja gives every project one; add it in the board UI and re-run. This script never creates a view."
  fi
  view_id="$(jq -r '.id' <<<"$view_json")"
  view_title_current="$(jq -r '.title // ""' <<<"$view_json")"
  view_position_current="$(jq -r '.position // 0' <<<"$view_json")"
  mode_current="$(jq -r '.bucket_configuration_mode // ""' <<<"$view_json")"
  done_current="$(jq -r '.done_bucket_id // 0' <<<"$view_json")"
  default_current="$(jq -r '.default_bucket_id // 0' <<<"$view_json")"
  if [ "$view_title_current" = "$VIEW_TITLE" ]; then
    say view "$repo / $VIEW_TITLE" existing "id=$view_id"
  else
    say view "$repo / $view_title_current" adopted "id=$view_id -> $VIEW_TITLE"
  fi
  C_EXISTING=$((C_EXISTING + 1))
  if [ "$(jq -r '[.[] | select(.view_kind == "kanban")] | length' <<<"$views_json")" != "1" ]; then
    note "$repo carries more than one kanban view; this script drives the first and leaves the rest alone"
  fi

  # ---- the columns -------------------------------------------------------
  read_endpoint "/projects/$project_id/views/$view_id/buckets"
  buckets_json="$API_BODY"

  # A column from an earlier shape is renamed in place, so its id — which the
  # saved filters store — survives the rename. A rename moves the column and not
  # the cards, so a column holding any is refused rather than silently relabelled.
  counts="$(bucket_counts "$project_id" "$view_id")"
  for pair in "${BUCKET_RENAMES[@]}"; do
    old="${pair%%:*}"; new="${pair##*:}"
    if jq -e --arg t "$new" 'any(.[]; .title == $t)' >/dev/null <<<"$buckets_json"; then
      continue
    fi
    bid="$(jq -r --arg t "$old" '[.[] | select(.title == $t)][0].id // empty' <<<"$buckets_json")"
    if [ -z "$bid" ]; then
      continue
    fi
    cards="$(awk -F'\t' -v id="$bid" '$1 == id {print $2; found=1} END {if (!found) print 0}' <<<"$counts")"
    if [ "${cards:-0}" != "0" ]; then
      die "bucket \"$old\" in \"$repo\" holds $cards cards; move them before renaming"
    fi
    position="$(bucket_position "$new")"
    put_bucket "$project_id" "$view_id" "$bid" "$new" "$position"
    if [ "$DRY_RUN" = 1 ]; then
      say bucket "$repo / $VIEW_TITLE / $old" would-rename "id=$bid -> $new"
    else
      say bucket "$repo / $VIEW_TITLE / $old" renamed "id=$bid -> $new"
      C_UPDATED=$((C_UPDATED + 1))
    fi
    buckets_json="$(jq -c --argjson id "$bid" --arg t "$new" --argjson p "$position" \
      'map(if .id == $id then .title = $t | .position = $p else . end)' <<<"$buckets_json")"
  done

  known_json="$(jq -cn --args '$ARGS.positional' "${BUCKETS[@]}")"
  for title in "${BUCKETS[@]}"; do
    position="$(bucket_position "$title")"
    bucket_id="$(jq -r --arg t "$title" '[.[] | select(.title == $t)][0].id // empty' <<<"$buckets_json")"
    current_position="$(jq -r --arg t "$title" '[.[] | select(.title == $t)][0].position // 0' <<<"$buckets_json")"
    if [ -n "$bucket_id" ]; then
      if [ "$current_position" = "$position" ]; then
        say bucket "$repo / $VIEW_TITLE / $title" existing "id=$bucket_id position=$position"
        C_EXISTING=$((C_EXISTING + 1))
      else
        put_bucket "$project_id" "$view_id" "$bucket_id" "$title" "$position"
        if [ "$DRY_RUN" = 1 ]; then
          say bucket "$repo / $VIEW_TITLE / $title" would-position "$current_position -> $position"
        else
          say bucket "$repo / $VIEW_TITLE / $title" positioned "$current_position -> $position"
          C_UPDATED=$((C_UPDATED + 1))
        fi
      fi
    else
      body="$(jq -cn --arg t "$title" --argjson p "$position" '{title: $t, position: $p}')"
      write_or_plan PUT "/projects/$project_id/views/$view_id/buckets" "$body"
      if [ "$DRY_RUN" = 1 ]; then
        bucket_id="<new bucket $title>"
        say bucket "$repo / $VIEW_TITLE / $title" would-create "position=$position"
      else
        bucket_id="$(jq -r '.id // empty' <<<"$API_BODY")"
        if [ -z "$bucket_id" ]; then
          die "the board accepted bucket \"$title\" in \"$repo\" but returned no id — body: $API_BODY"
        fi
        say bucket "$repo / $VIEW_TITLE / $title" created "id=$bucket_id position=$position"
        C_CREATED=$((C_CREATED + 1))
      fi
    fi
    if [ "$title" = "$DONE_TITLE" ]; then
      done_id="$bucket_id"
    fi
    if [ "$title" = "$DEFAULT_BUCKET_TITLE" ]; then
      default_id="$bucket_id"
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

  # ---- the view's settings, in one full record ----------------------------
  # The endpoint replaces the view rather than patching it: every field left out
  # of the body is zeroed. So the body always carries all five — title, reading
  # position, bucket mode, default column and done column — and it is written
  # only when at least one of them differs from the live view.
  #
  # The done column is what makes the board honest: without it, a card in "Done"
  # stays open as far as Vikunja and every filter are concerned. "manual" is what
  # lets a person drag a card between columns; the default, "none", pins every
  # card to the column it was created in.
  if ! [[ "$done_id" =~ ^[0-9]+$ ]] || ! [[ "$default_id" =~ ^[0-9]+$ ]]; then
    if [ "$DRY_RUN" = 1 ]; then
      note "$repo: the done and default columns do not both exist yet; a real run wires them"
      return 0
    fi
    die "project \"$repo\" has no \"$DONE_TITLE\" or no \"$DEFAULT_BUCKET_TITLE\" column after the bucket pass — cannot wire the view"
  fi

  desired="$(jq -cn --arg t "$VIEW_TITLE" --arg m "$BUCKET_MODE" \
    --argjson d "$done_id" --argjson b "$default_id" \
    '{title: $t, position: 1, bucket_configuration_mode: $m, done_bucket_id: $d, default_bucket_id: $b}')"
  current="$(jq -cn --arg t "$view_title_current" --arg m "$mode_current" \
    --argjson pos "$view_position_current" --argjson d "$done_current" --argjson b "$default_current" \
    '{title: $t, position: $pos, bucket_configuration_mode: $m, done_bucket_id: $d, default_bucket_id: $b}')"

  if [ "$desired" = "$current" ]; then
    say view "$repo / $VIEW_TITLE" existing "$desired"
    C_EXISTING=$((C_EXISTING + 1))
    return 0
  fi

  write_or_plan POST "/projects/$project_id/views/$view_id" "$desired"
  if [ "$DRY_RUN" = 1 ]; then
    say view "$repo / $VIEW_TITLE" would-update "title, position, mode, default column and done column in one write"
  else
    say view "$repo / $VIEW_TITLE" updated "title, position, mode, default column and done column in one write"
    C_UPDATED=$((C_UPDATED + 1))
  fi
}

# ---------------------------------------------------------------------------
# The saved filter. Created once, never updated: if it exists, the operator's
# own edits win, and a filter is not part of the board's shape.
# ---------------------------------------------------------------------------
seed_filter() {
  local filter_id="" body=""

  printf '\n'
  list_saved_filters
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
  local current="" mode="" problems="" present="" bucket_count=0 unchecked=0

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
    mode="$(jq -r '.bucket_configuration_mode // empty' <<<"$vjson")"

    http GET "/projects/$pid/views/$view_id/buckets"
    buckets_json="$API_BODY"
    bucket_count="$(jq 'length' <<<"$buckets_json")"

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
    if [ -n "$mode" ] && [ "$mode" != "$BUCKET_MODE" ]; then
      problems="${problems:+$problems; }bucket_configuration_mode is \"$mode\", expected \"$BUCKET_MODE\""
    fi

    if [ -n "$problems" ]; then
      printf 'FAIL %-22s %s\n' "$repo" "$problems"
      failures=$((failures + 1))
    elif [ -z "$mode" ]; then
      # A field the API does not return cannot be checked. Saying so is the
      # point: silence here would read as "the cards can be dragged". Counted
      # here, and only here, so this sentence and a FAIL never name one board.
      unchecked=$((unchecked + 1))
      printf 'PASS %-22s view %s, %d buckets, done bucket %s\n' \
        "$repo" "$view_id" "$bucket_count" "$done_id"
      printf 'NOTE %-22s bucket_configuration_mode is not returned by this API, so the mode was not checked\n' \
        "$repo"
    else
      printf 'PASS %-22s view %s, %d buckets, done bucket %s, mode %s\n' \
        "$repo" "$view_id" "$bucket_count" "$done_id" "$mode"
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
  if [ "$unchecked" -ne 0 ]; then
    printf 'verify: %d board(s) could not be checked for bucket_configuration_mode — the API does not return the field\n' \
      "$unchecked"
  fi
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
    --repo)
      shift
      [ $# -gt 0 ] || { printf 'vikunja-seed: --repo needs a project name\n' >&2; exit 2; }
      REPO_SCOPE="$1"
      ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'vikunja-seed: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# One project, or all seven. The scope exists because a board is now shaped
# repository by repository: the six repositories without work keep the shape
# they already have until someone boards them.
if [ -n "$REPO_SCOPE" ]; then
  scope_found=0
  for scope_repo in "${REPOS[@]}"; do
    if [ "$scope_repo" = "$REPO_SCOPE" ]; then
      scope_found=1
    fi
  done
  if [ "$scope_found" != 1 ]; then
    printf 'vikunja-seed: no repository named "%s" in this script'"'"'s list\n' "$REPO_SCOPE" >&2
    exit 2
  fi
  REPOS=("$REPO_SCOPE")
fi

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
printf 'seeded: %d created, %d already present, %d updated, %d kept (not created here)\n' \
  "$C_CREATED" "$C_EXISTING" "$C_UPDATED" "$C_KEPT"
