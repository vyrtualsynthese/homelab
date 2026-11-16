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
#   hack/vikunja-seed.sh                      # shape the homelab board, and nothing else
#   hack/vikunja-seed.sh --repo config        # shape one named board
#   hack/vikunja-seed.sh --all                # shape every board in REPOS (deliberate, rare)
#   hack/vikunja-seed.sh --verify             # read back and fail unless the board is shaped right
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
# Projects that are boards without being a repository. The alert board is one:
# it is a queue of things to investigate, not the state of a codebase.
EXTRA_PROJECTS=(alerts)
VIEW_TITLE="Board"
VIEW_KIND="kanban"
# A bucket renamed from an earlier shape is adopted in place, so its id — which
# the saved filters store — survives. Only buckets holding no cards are renamed.
BUCKET_RENAMES=("Ideas:Idea" "Revision:Validation" "To-Do:Shaping")
# Reading order. Positions are written explicitly so the order is the design's,
# not whatever the UI last dragged.
BUCKETS=(Idea Shaping Backlog Doing Validation Done)
DONE_TITLE="Done"
DEFAULT_BUCKET_TITLE="Backlog"
VALIDATION_TITLE="Validation"
DOING_TITLE="Doing"
BUCKET_MODE="manual"
FILTER_TITLE="Awaiting my review"

# Labels are global to the account: three kinds, then who wrote the ticket and
# what the session that did the work played. Colours are the design's.
LABELS=(
  "feature:1f6feb"
  "story:8250df"
  "task:1a7f37"
  "implementer:0e8a16"
  "reviewer:6e7781"
)
KIND_LABELS=(feature story task)
ROLE_LABELS=(implementer reviewer)

# The two filters the operator reads, and the one they replace.
FILTER_VALIDATION_TITLE="Needs my validation"
FILTER_DOING_TITLE="Agent work in flight"

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
# The one repository the board is for. Everything else stays as it is unless the
# operator asks for it by name or passes --all.
BOARDED_REPO="homelab"
REPO_SCOPE=""
RUN_ALL=0
# Filled by collect_filter_ids for every boarded board. The filters are global to
# the account, so they must name every board's columns; naming one board's ids
# makes the operator's queue skip the others without saying so.
VALIDATION_IDS=()
DOING_IDS=()
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
# 405. They do appear in GET /projects as pseudo-projects, with an id derived
# from the filter's own: a filter with id 2 is project -3, so the real id is
# -id - 1. That derivation is the only way to reach GET/POST/DELETE /filters/{id}.
list_saved_filters() {
  read_endpoint /projects
  API_BODY="$(jq -c '[.[] | select(.id < 0) | {id: (-.id - 1), title, is_favorite}]' <<<"$API_BODY")"
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
  local project_id="" view_id="" done_id="" default_id="" body="" projects_json=""
  local view_title_current="" view_position_current="" view_kind_current="" default_current=""
  local views_json="" view_json="" buckets_json="" known_json=""
  local bid="" btitle="" title="" bucket_id="" position="" current_position="" cards="" counts=""
  local mode_current="" done_current="" pair old new renamed=0
  local desired="" current=""

  printf '\n'

  # ---- the project -------------------------------------------------------
  read_endpoint /projects
  projects_json="$API_BODY"
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

  # ---- the front page ----------------------------------------------------
  # The description is what a stranger reads first: where the code is, which
  # branch is the integration branch, where the documentation lives, and what the
  # three labels mean.
  if [ "$repo" = "alerts" ]; then
    body="$(jq -cn --arg t "$repo" --arg d "Grafana alerts, one card per incident.

Written by \`bot-alertmanager\`; investigated in a session, never closed by the relay.
A repeat of the same alert is a comment on the same card. A critical alert arrives in
\`Backlog\` with the payload and a \`Next step:\` line, and nothing moves until somebody looks.

Kind: label \`task\`, plus \`alert\` and — when the rule itself is the problem — \`noise\`.
Order is priority: the card highest in a column is the most urgent." \
      '{title: $t, description: $d}')"
  else
  body="$(jq -cn --arg t "$repo" --arg d "Repository: https://github.com/vyrtualsynthese/$repo
Branches: \`develop\` (integration), \`main\` (production)
Docs: https://github.com/vyrtualsynthese/$repo/tree/develop/docs
Protocol: https://github.com/vyrtualsynthese/$repo/blob/develop/docs/agent-protocol.md

Kind: label \`feature\` / \`story\` / \`task\`. A subtask is a checklist line inside a task." \
    '{title: $t, description: $d}')"
  fi
  if [ "$(jq -r --arg t "$repo" '[.[] | select(.title == $t)][0].description // ""' <<<"$projects_json")" = "$(jq -r '.description' <<<"$body")" ]; then
    say project "$repo" existing "description already set"
    C_EXISTING=$((C_EXISTING + 1))
  else
    write_or_plan POST "/projects/$project_id" "$body"
    say project "$repo" described "repository, branches, docs, and the three kinds"
    C_UPDATED=$((C_UPDATED + 1))
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
  view_kind_current="$(jq -r '.view_kind // ""' <<<"$view_json")"
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

  # view_kind is in the body because it is zeroed like every other omitted
  # field: leaving it out turns the board into a list view on the first repair.
  desired="$(jq -cn --arg t "$VIEW_TITLE" --arg m "$BUCKET_MODE" --arg k "$VIEW_KIND" \
    --argjson d "$done_id" --argjson b "$default_id" \
    '{title: $t, view_kind: $k, position: 1, bucket_configuration_mode: $m, done_bucket_id: $d, default_bucket_id: $b}')"
  current="$(jq -cn --arg t "$view_title_current" --arg m "$mode_current" --arg k "$view_kind_current" \
    --argjson pos "$view_position_current" --argjson d "$done_current" --argjson b "$default_current" \
    '{title: $t, view_kind: $k, position: $pos, bucket_configuration_mode: $m, done_bucket_id: $d, default_bucket_id: $b}')"

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
# The labels. One lookup for the whole account, before any project is shaped.
# ---------------------------------------------------------------------------
seed_labels() {
  local read_json="" pair title colour id body=""

  printf '\n'
  read_endpoint /labels
  read_json="$API_BODY"
  for pair in "${LABELS[@]}"; do
    title="${pair%%:*}"
    colour="#${pair##*:}"
    id="$(jq -r --arg t "$title" '[.[] | select(.title == $t)][0].id // empty' <<<"$read_json")"
    if [ -n "$id" ]; then
      say label "$title" existing "id=$id"
      C_EXISTING=$((C_EXISTING + 1))
      continue
    fi
    body="$(jq -cn --arg t "$title" --arg c "$colour" '{title: $t, hex_color: $c}')"
    write_or_plan PUT /labels "$body"
    if [ "$DRY_RUN" = 1 ]; then
      say label "$title" would-create "$colour"
    else
      say label "$title" created "$colour"
      C_CREATED=$((C_CREATED + 1))
    fi
  done
}

# ---------------------------------------------------------------------------
# The two saved filters, and the retired one.
#
# A filter stores column ids, so it is written after the columns are read back,
# and rewritten on every run: a filter pointing at a column that no longer holds
# that id matches nothing, and an empty queue looks like a finished day.
# ---------------------------------------------------------------------------
# "1, 2, 3" from a list, which is the shape the filter language wants.
join_ids() {
  local out="" id=""
  for id in "$@"; do
    out="${out:+$out, }$id"
  done
  printf '%s' "$out"
}

# Every boarded board's Validation and Doing column ids. Reads only.
collect_filter_ids() {
  local repo="" pid="" view="" vjson="" buckets="" id=""
  VALIDATION_IDS=()
  DOING_IDS=()
  for repo in "${REPOS_ALL[@]}"; do
    http GET /projects
    pid="$(jq -r --arg t "$repo" '[.[] | select(.title == $t)][0].id // empty' <<<"$API_BODY")"
    if [ -z "$pid" ]; then
      continue
    fi
    http GET "/projects/$pid/views"
    vjson="$(jq -c '[.[] | select(.view_kind == "kanban")][0] // empty' <<<"$API_BODY")"
    if [ -z "$vjson" ]; then
      continue
    fi
    view="$(jq -r '.id' <<<"$vjson")"
    http GET "/projects/$pid/views/$view/buckets"
    buckets="$API_BODY"
    id="$(jq -r --arg t "$VALIDATION_TITLE" '[.[] | select(.title == $t)][0].id // empty' <<<"$buckets")"
    if [ -n "$id" ]; then
      VALIDATION_IDS+=("$id")
    fi
    id="$(jq -r --arg t "$DOING_TITLE" '[.[] | select(.title == $t)][0].id // empty' <<<"$buckets")"
    if [ -n "$id" ]; then
      DOING_IDS+=("$id")
    fi
  done
}

seed_filters() {
  local validation_id="$1" doing_id="$2" title="" query="" id="" body="" filters_json=""

  printf '\n'
  for pair in "$FILTER_VALIDATION_TITLE:$validation_id" "$FILTER_DOING_TITLE:$doing_id"; do
    title="${pair%:*}"
    query="bucket_id in ${pair##*:} && done = false"
    list_saved_filters
    filters_json="$API_BODY"
    id="$(jq -r --arg t "$title" '[.[] | select(.title == $t)][0].id // empty' <<<"$filters_json")"
    body="$(jq -cn --arg t "$title" --arg q "$query" \
      '{title: $t, is_favorite: ($t == "Needs my validation"),
        filters: {filter: $q, sort_by: ["created"], order_by: ["asc"]}}')"
    if [ -n "$id" ]; then
      write_or_plan POST "/filters/$id" "$body"
      say filter "$title" updated "$query"
      C_UPDATED=$((C_UPDATED + 1))
    else
      write_or_plan PUT /filters "$body"
      if [ "$DRY_RUN" = 1 ]; then
        say filter "$title" would-create "$query"
      else
        say filter "$title" created "$query"
        C_CREATED=$((C_CREATED + 1))
      fi
    fi
  done
}

retire_old_filter() {
  local id=""

  list_saved_filters
  id="$(jq -r --arg t "$FILTER_TITLE" '[.[] | select(.title == $t)][0].id // empty' <<<"$API_BODY")"
  if [ -z "$id" ]; then
    say filter "$FILTER_TITLE" absent
    return 0
  fi
  write_or_plan DELETE "/filters/$id" ""
  say filter "$FILTER_TITLE" retired "id=$id"
  C_UPDATED=$((C_UPDATED + 1))
}

# ---------------------------------------------------------------------------
# Read the boards back. Never writes. Exits 1 with the offending project named.
# ---------------------------------------------------------------------------
# Every ticket of a project, across pages, as one JSON array.
fetch_project_tasks() {
  local pid="$1" page=1 tmp size=0 out=""

  tmp="$(mktemp)"
  while :; do
    http GET "/projects/$pid/tasks?per_page=100&page=$page"
    size="$(jq 'length' <<<"$API_BODY")"
    jq -c '.' <<<"$API_BODY" >>"$tmp"
    if [ "$size" -lt 100 ]; then
      break
    fi
    page=$((page + 1))
    if [ "$page" -gt 20 ]; then
      break
    fi
  done
  out="$(jq -sc 'add // []' "$tmp")"
  rm -f "$tmp"
  printf '%s\n' "$out"
}
verify() {
  local failures=0 repo="" projects_json="" pid="" views_json="" vjson=""
  local view_id="" view_title="" view_position="" buckets_json="" order=""
  local expected_order="" done_id="" default_id="" done_current="" default_current=""
  local mode="" missing="" labels_json="" title="" tasks_json="" total=0
  local kinds_json="" bad_kind="" kinds_map="" column_ids="" bad_column=""
  local v2_json="" filters_json="" query="" filter_id=""

  printf 'Verifying %d board(s) against %s\n\n' "${#REPOS_ALL[@]}" "$VIKUNJA_URL"

  expected_order="$(printf '%s, ' "${BUCKETS[@]}")"
  expected_order="${expected_order%, }"

  # ---- the labels, once for the account --------------------------------
  http GET /labels
  labels_json="$API_BODY"
  missing=""
  for title in "${KIND_LABELS[@]}" "${ROLE_LABELS[@]}"; do
    jq -e --arg t "$title" 'any(.[]; .title == $t)' >/dev/null <<<"$labels_json" \
      || missing="${missing:+$missing, }$title"
  done
  if [ -n "$missing" ]; then
    printf 'FAIL %-12s labels missing: %s\n' labels "$missing"
    failures=$((failures + 1))
  else
    printf 'PASS %-12s %d labels present\n' labels "${#LABELS[@]}"
  fi

  http GET /projects
  projects_json="$API_BODY"

  for repo in "${REPOS_ALL[@]}"; do
    pid="$(jq -r --arg t "$repo" '[.[] | select(.title == $t)][0].id // empty' <<<"$projects_json")"
    if [ -z "$pid" ]; then
      printf 'FAIL %-12s no project titled "%s"\n' "$repo" "$repo"
      failures=$((failures + 1))
      continue
    fi

    # ---- the view -------------------------------------------------------
    http GET "/projects/$pid/views"
    views_json="$API_BODY"
    vjson="$(jq -c '[.[] | select(.view_kind == "kanban")][0] // empty' <<<"$views_json")"
    if [ -z "$vjson" ] || [ "$vjson" = "null" ]; then
      printf 'FAIL %-12s no kanban view\n' "$repo"
      failures=$((failures + 1))
      continue
    fi
    view_id="$(jq -r '.id' <<<"$vjson")"
    view_title="$(jq -r '.title // ""' <<<"$vjson")"
    view_position="$(jq -r '.position // 0' <<<"$vjson")"
    mode="$(jq -r '.bucket_configuration_mode // ""' <<<"$vjson")"
    done_current="$(jq -r '.done_bucket_id // 0' <<<"$vjson")"
    default_current="$(jq -r '.default_bucket_id // 0' <<<"$vjson")"

    if [ "$view_title" = "$VIEW_TITLE" ] && [ "$view_position" = "1" ]; then
      printf 'PASS %-12s view "%s" at position 1\n' "$repo" "$view_title"
    else
      printf 'FAIL %-12s view is "%s" at position %s, expected "%s" at 1\n' \
        "$repo" "$view_title" "$view_position" "$VIEW_TITLE"
      failures=$((failures + 1))
    fi

    # A repair that leaves view_kind out of the body turns the board into a list
    # view: the columns still exist, and nobody can drag a card.
    view_kind_current="$(jq -r '.view_kind // ""' <<<"$vjson")"
    if [ "$view_kind_current" = "$VIEW_KIND" ]; then
      printf 'PASS %-12s view kind %s\n' "$repo" "$view_kind_current"
    else
      printf 'FAIL %-12s view kind is "%s", expected "%s"\n' \
        "$repo" "${view_kind_current:-unset}" "$VIEW_KIND"
      failures=$((failures + 1))
    fi

    # ---- the columns ----------------------------------------------------
    http GET "/projects/$pid/views/$view_id/buckets"
    buckets_json="$API_BODY"
    order="$(jq -r 'sort_by(.position) | [.[].title] | join(", ")' <<<"$buckets_json")"
    done_id="$(jq -r --arg t "$DONE_TITLE" '[.[] | select(.title == $t)][0].id // 0' <<<"$buckets_json")"
    default_id="$(jq -r --arg t "$DEFAULT_BUCKET_TITLE" '[.[] | select(.title == $t)][0].id // 0' <<<"$buckets_json")"

    if [ "$order" = "$expected_order" ]; then
      printf 'PASS %-12s columns %s\n' "$repo" "$order"
    else
      printf 'FAIL %-12s columns are %s, expected %s\n' "$repo" "$order" "$expected_order"
      failures=$((failures + 1))
    fi

    if [ "$done_current" = "$done_id" ] && [ "$done_id" != "0" ]; then
      printf 'PASS %-12s done column %s\n' "$repo" "$done_id"
    else
      printf 'FAIL %-12s done column is %s, expected %s\n' "$repo" "$done_current" "$done_id"
      failures=$((failures + 1))
    fi

    if [ "$default_current" = "$default_id" ] && [ "$default_id" != "0" ]; then
      printf 'PASS %-12s default column %s\n' "$repo" "$default_id"
    else
      printf 'FAIL %-12s default column is %s, expected %s\n' "$repo" "$default_current" "$default_id"
      failures=$((failures + 1))
    fi

    if [ "$mode" = "$BUCKET_MODE" ]; then
      printf 'PASS %-12s columns are dragged by hand (mode %s)\n' "$repo" "$mode"
    else
      printf 'FAIL %-12s column mode is "%s", expected "%s"\n' "$repo" "${mode:-unset}" "$BUCKET_MODE"
      failures=$((failures + 1))
    fi

    # ---- every ticket carries exactly one kind --------------------------
    tasks_json="$(fetch_project_tasks "$pid")"
    total="$(jq 'length' <<<"$tasks_json")"
    kinds_json="$(jq -cn --args '$ARGS.positional' "${KIND_LABELS[@]}")"
    bad_kind="$(jq --argjson kinds "$kinds_json" '
      [ .[] | ([ (.labels // [])[].title ] | map(select(. as $x | $kinds | index($x) != null)) | length)
        | select(. != 1) ] | length' <<<"$tasks_json")"
    if [ "$bad_kind" = "0" ]; then
      printf 'PASS %-12s %s tickets, each with exactly one kind label\n' "$repo" "$total"
    else
      printf 'FAIL %-12s %s ticket(s) without exactly one kind label (of %s)\n' \
        "$repo" "$bad_kind" "$total"
      failures=$((failures + 1))
    fi

    # ---- Doing and Validation hold tasks only ---------------------------
    read_endpoint_v2 "/projects/$pid/views/$view_id/buckets/tasks"
    v2_json="$API_BODY"
    kinds_map="$(jq -c --argjson kinds "$kinds_json" \
      '[.[] | {id: .id, kinds: [((.labels // [])[].title) | select(. as $x | $kinds | index($x) != null)]}]' \
      <<<"$tasks_json")"
    column_ids="$(jq -c --arg d "$DOING_TITLE" --arg v "$VALIDATION_TITLE" \
      '[.items[] | select(.title == $d or .title == $v) | (.tasks // [])[].id]' <<<"$v2_json")"
    # A column with cards that yields no ids would make the next check pass by
    # being empty, which is exactly how a vacuous check looks.
    unread="$(jq -r --arg d "$DOING_TITLE" --arg v "$VALIDATION_TITLE" \
      '[.items[] | select(.title == $d or .title == $v) | select((.count // 0) > ((.tasks // []) | length)) | .title] | join(", ")' <<<"$v2_json")"
    if [ -n "$unread" ]; then
      printf 'FAIL %-12s cannot read the cards of %s, so their kind was not checked\n' "$repo" "$unread"
      failures=$((failures + 1))
    fi
    bad_column="$(jq -n --argjson map "$kinds_map" --argjson ids "$column_ids" '
      (reduce $map[] as $e ({}; .[($e.id | tostring)] = $e.kinds)) as $byid
      | [ $ids[] | select(((($byid[. | tostring]) // ["task"]) | any(. != "task"))) ] | length')"
    if [ "$bad_column" = "0" ]; then
      printf 'PASS %-12s %s and %s hold task cards only\n' "$repo" "$DOING_TITLE" "$VALIDATION_TITLE"
    else
      printf 'FAIL %-12s %s card(s) in %s or %s are a feature or a story\n' \
        "$repo" "$bad_column" "$DOING_TITLE" "$VALIDATION_TITLE"
      failures=$((failures + 1))
    fi
  done

  # ---- the two filters, once for the account -----------------------------
  collect_filter_ids
  list_saved_filters
  filters_json="$API_BODY"
  for pair in "$FILTER_VALIDATION_TITLE:$(join_ids "${VALIDATION_IDS[@]}")" \
              "$FILTER_DOING_TITLE:$(join_ids "${DOING_IDS[@]}")"; do
    title="${pair%:*}"
    query="bucket_id in ${pair##*:} && done = false"
    filter_id="$(jq -r --arg t "$title" '[.[] | select(.title == $t)][0].id // empty' <<<"$filters_json")"
    if [ -z "$filter_id" ]; then
      printf 'FAIL %-12s saved filter "%s" is missing\n' filters "$title"
      failures=$((failures + 1))
    elif http GET "/filters/$filter_id" \
      && jq -e --arg q "$query" '(.filters.filter // "") | contains($q)' >/dev/null <<<"$API_BODY"; then
      printf 'PASS %-12s saved filter "%s" reads %s\n' filters "$title" "$query"
    else
      printf 'FAIL %-12s saved filter "%s" does not read "%s"\n' filters "$title" "$query"
      failures=$((failures + 1))
    fi
  done

  printf '\n'
  if [ "$failures" -ne 0 ]; then
    printf 'verify: %d problem(s) found\n' "$failures"
    exit 1
  fi
  printf 'verify: all %d board(s) hold the shape the protocol expects\n' "${#REPOS_ALL[@]}"
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
    --all) RUN_ALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'vikunja-seed: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# One project, or all seven. The scope exists because a board is now shaped
# repository by repository: the six repositories without work keep the shape
# they already have until someone boards them.
if [ "$RUN_ALL" = 0 ] && [ -z "$REPO_SCOPE" ]; then
  REPO_SCOPE="$BOARDED_REPO"
fi

if [ "$RUN_ALL" = 1 ] && [ -n "$REPO_SCOPE" ]; then
  printf 'vikunja-seed: --all and --repo cannot be combined\n' >&2
  exit 2
fi

REPOS_ALL=("${REPOS[@]}" "${EXTRA_PROJECTS[@]}")

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

seed_labels
for repo in "${REPOS[@]}" "${EXTRA_PROJECTS[@]}"; do
  seed_project "$repo"
done

# The filters name every boarded board's columns, so they are rebuilt from the
# whole list, not only from the boards this run happened to touch.
collect_filter_ids
seed_filters "$(join_ids "${VALIDATION_IDS[@]}")" "$(join_ids "${DOING_IDS[@]}")"
retire_old_filter

printf '\n'
if [ "$DRY_RUN" = 1 ]; then
  printf 'dry run: %d write(s) planned, none sent; %d object(s) already exist\n' \
    "$W_PLANNED" "$C_EXISTING"
  exit 0
fi
printf 'seeded: %d created, %d already present, %d updated, %d kept (not created here)\n' \
  "$C_CREATED" "$C_EXISTING" "$C_UPDATED" "$C_KEPT"
