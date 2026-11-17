#!/usr/bin/env python3
"""One-shot migration of the homelab board onto the six-column workflow.

Consumes a reviewed mapping (``hack/vikunja-migrate-2026-11.json``) and writes it
onto the board:

  * one ``feature`` card per plan, labelled, described, and parent of its tasks;
  * every existing ticket gains the ``task`` label, a plain title, its fixed
    description block, and the column its evidence supports;
  * one ``task`` card per merged PR that closed no plan task, in ``Done``.

The mapping is the operator's: this script does not decide what is finished, it
applies what the map says. ``--dry-run`` prints every action and writes nothing.

Usage:
    VIKUNJA_API_TOKEN=... python3 hack/vikunja_migrate.py --dry-run
    VIKUNJA_API_TOKEN=... python3 hack/vikunja_migrate.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

REPO = "vyrtualsynthese/homelab"
DOCS_BASE = f"https://github.com/{REPO}/blob/develop/"
SITE_BASE = "https://docs.kub.local/homelab/develop/"
KINDS = ("feature", "story", "task")
COLUMNS = ("Idea", "Shaping", "Backlog", "Doing", "Validation", "Done")
CONTAINER_COLUMNS = ("Idea", "Shaping", "Backlog", "Done")
DEFAULT_MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "vikunja-migrate-2026-11.json")


# ---------------------------------------------------------------------------
# Pure helpers, tested in hack/tests/test_vikunja_migrate.py.
# ---------------------------------------------------------------------------
def clean_title(title: str, plan_key: str) -> str:
    """Drop the ``<plan-key>: `` prefix, keep every other colon, capitalise."""
    prefix = f"{plan_key}: "
    if title.startswith(prefix):
        title = title[len(prefix):]
    return title[:1].upper() + title[1:] if title else title


def site_url(docs: str) -> str | None:
    """The rendered page for a file under ``docs/``, or ``None`` when there is none.

    The URL is derived from the path alone. Whether that page is live yet is the
    caller's to know, because the site builds ``develop``, not a feature branch.
    """
    if not docs or not docs.startswith("docs/") or not docs.endswith(".md"):
        return None
    return f"{SITE_BASE}{docs[len('docs/'):-len('.md')]}/"


def render_description(docs: str, branch: str, pr: str, body: str,
                       commit: str = "none", on_develop: bool = True) -> str:
    """The fixed block every ticket opens with, then the body, if any.

    ``on_develop`` is the caller's knowledge, not this function's: the site builds
    ``develop``, so a ``docs:`` path that only exists on a feature branch renders
    nowhere and its ``site:`` line says ``none`` instead of pointing at a 404.
    """
    lines = [
        f"repo: {REPO}",
        f"docs: {DOCS_BASE}{docs}" if docs and docs != "none" else "docs: none",
    ]
    # A labelled link, never a bare URL: the protocol treats a bare URL as a card defect.
    site = site_url(docs)
    if site is not None:
        lines.append(f"site: [Rendered page]({site})" if on_develop else "site: none")
    lines.extend([
        f"branch: {branch or 'none'}",
        f"pr: {pr or 'none'}",
    ])
    if commit and commit != "none":
        lines.append(f"commit: https://github.com/{REPO}/commit/{commit}")
    block = "\n".join(lines)
    if body:
        return f"{block}\n\n{body}"
    return block


def validate(map_doc: dict[str, Any]) -> list[str]:
    """Every way the mapping could make the board lie. Empty means clean."""
    problems: list[str] = []
    features = map_doc.get("features", [])
    keys = [f.get("key") for f in features]
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            problems.append(f"duplicate feature key: {key}")
        seen.add(key)

    for task in map_doc.get("tasks", []):
        where = f"task {task.get('id')}"
        if task.get("feature") not in seen:
            problems.append(f"{where} points at unknown feature: {task.get('feature')}")
        if task.get("column") not in COLUMNS:
            problems.append(f"{where} has column outside the six: {task.get('column')}")
        if task.get("kind", "task") not in KINDS:
            problems.append(f"{where} has unknown kind: {task.get('kind')}")
        if task.get("kind", "task") != "task" and task.get("column") not in CONTAINER_COLUMNS:
            problems.append(
                f"{where} is a {task.get('kind')} card in {task.get('column')}, "
                "which only holds tasks")
    return problems


def plan_actions(map_doc: dict[str, Any], state: dict[str, Any],
                 description_ok: bool = False) -> list[dict[str, Any]]:
    """Only the differences between the map and the board, in apply order."""
    actions: list[dict[str, Any]] = []
    known_features: dict[str, int] = state.get("features", {})
    known_tasks: dict[int, dict[str, Any]] = state.get("tasks", {})

    labels_by_id = state.get("labels_by_id", {})
    placed: list[tuple[float, int]] = []
    for feature in map_doc.get("features", []):
        if feature["title"] in known_features:
            feature_id = known_features[feature["title"]]
            if "feature" not in (labels_by_id.get(feature_id) or []):
                actions.append({"op": "ensure_label", "id": feature_id, "label": "feature"})
            position = state.get("positions", {}).get(feature_id)
            if position is not None:
                placed.append((position, feature_id))
            continue
        actions.append({"op": "create_feature", "key": feature["key"],
                        "title": feature["title"], "position": feature.get("position", 0),
                        "description": render_description(feature.get("docs", "none"), "none",
                                                          "none", feature.get("why", ""))})

    for task in map_doc.get("tasks", []):
        current = known_tasks.get(task["id"])
        if current is None:
            actions.append({"op": "missing_task", "id": task["id"], "title": task.get("title")})
            continue
        kind = task.get("kind", "task")
        wanted = {"title": clean_title(task.get("title") or "", task.get("feature") or ""),
                  "bucket": task.get("column")}
        changes = {k: v for k, v in wanted.items() if v is not None and current.get(k) != v}
        if kind not in (current.get("labels") or []):
            changes["labels"] = [kind]
        wanted_description = render_description(
            task.get("docs", "none"), task.get("branch", "none"),
            task.get("pr", "none"), task.get("body", ""), task.get("commit", "none"))
        if not description_ok and current.get("description") != wanted_description:
            changes["description"] = wanted_description
        if changes:
            actions.append({"op": "update_task", "id": task["id"], "changes": changes})

    # Reading order is a sequence, not a number: the server recalculates positions
    # whenever it likes, so the board is reordered only when the observed sequence
    # differs from the map's. Values are spaced 1000 apart for the same reason.
    wanted_order = [known_features[f["title"]] for f in map_doc.get("features", [])
                    if f["title"] in known_features]
    observed_order = [feature_id for _position, feature_id in sorted(placed)]
    if observed_order and observed_order != wanted_order:
        actions.append({"op": "reorder", "order": wanted_order})

    existing_titles = {t.get("title") for t in known_tasks.values()}
    for entry in map_doc.get("backfill", []):
        if entry["title"] in existing_titles:
            continue
        actions.append({"op": "create_backfill", "title": entry["title"],
                        "feature": entry.get("feature"),
                        "description": render_description(
                            entry.get("docs", "none"), "none", entry.get("pr", "none"),
                            f"Merged {entry.get('merged', 'unknown')}.")})
    return actions


# ---------------------------------------------------------------------------
# The board.
# ---------------------------------------------------------------------------
class Board:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as answer:
                raw = answer.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode()[:300]
            raise SystemExit(f"{method} {path} failed: HTTP {error.code} {detail}") from error
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode()


def read_state(board: Board, project: int, view: int,
               self_features: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    """The board as the map compares itself against."""
    labels = {l["title"]: l["id"] for l in board.call("GET", "/api/v1/labels")}
    tasks = []
    page = 1
    while True:
        chunk = board.call("GET", f"/api/v1/projects/{project}/tasks?per_page=100&page={page}")
        chunk = chunk if isinstance(chunk, list) else []
        tasks.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1

    buckets = {b["id"]: b["title"] for b in
               board.call("GET", f"/api/v1/projects/{project}/views/{view}/buckets")}
    # The v2 buckets/tasks read stops at 100 cards per column, and the flat task
    # list reports bucket_id 0, so each column is asked for by filter instead.
    bucket_of: dict[int, str] = {}
    position_of: dict[int, float] = {}
    for bucket_id, title in buckets.items():
        page = 1
        while True:
            found = board.call(
                "GET", f"/api/v1/projects/{project}/views/{view}/tasks"
                       f"?per_page=100&page={page}&filter=bucket_id%20%3D%20{bucket_id}")
            found = found if isinstance(found, list) else []
            for task in found:
                bucket_of[task["id"]] = title
                position_of[task["id"]] = task.get("position", 0)
            if len(found) < 100:
                break
            page += 1
    features: dict[str, int] = {}
    known: dict[int, dict[str, Any]] = {}
    for task in tasks:
        titles = [l["title"] for l in (task.get("labels") or [])]
        bucket = bucket_of.get(task["id"], buckets.get(task.get("bucket_id", 0), ""))
        known[task["id"]] = {
            "title": task["title"],
            "bucket": bucket,
            "labels": titles,
            "description": task.get("description"),
        }
        # A feature is known by its title, not by its label: a run that died
        # between creating the card and labelling it must not create it again.
        if "feature" in titles or task["title"] in {
                f["title"] for f in self_features}:
            features[task["title"]] = task["id"]
    return {"features": features, "tasks": known, "labels": labels,
            "positions": position_of,
            "labels_by_id": {task_id: card["labels"] for task_id, card in known.items()}}


def relate(board: Board, child: int, parent: int) -> None:
    """Parent a task under its feature; an existing relation is success, not a failure."""
    try:
        board.call("PUT", f"/api/v1/tasks/{child}/relations",
                   {"other_task_id": parent, "relation_kind": "parenttask"})
    except SystemExit as error:
        if "HTTP 409" not in str(error):
            raise


def apply_actions(board: Board, map_doc: dict[str, Any], state: dict[str, Any],
                  actions: list[dict[str, Any]], project: int, view: int,
                  dry_run: bool) -> int:
    labels = state["labels"]
    feature_ids: dict[str, int] = dict(state["features"])
    buckets = {b["title"]: b["id"] for b in
               board.call("GET", f"/api/v1/projects/{project}/views/{view}/buckets")}
    keys_by_title = {f["title"]: f["key"] for f in map_doc.get("features", [])}
    tasks_by_id = {t["id"]: t for t in map_doc.get("tasks", [])}
    done = 0

    def label(task: int, kind: str) -> None:
        if kind not in labels:
            raise SystemExit(f"label {kind} does not exist; run hack/vikunja-seed.sh first")
        board.call("PUT", f"/api/v1/tasks/{task}/labels", {"label_id": labels[kind]})

    for action in actions:
        if action["op"] == "reorder":
            print(f"reorder         {len(action['order'])} feature cards into the map's reading order")
            done += 1
            if not dry_run:
                for index, feature_id in enumerate(action["order"]):
                    board.call("PUT", f"/api/v2/tasks/{feature_id}/position",
                               {"project_view_id": view, "position": 1000 * (index + 1)})
        elif action["op"] == "ensure_label":
            print(f"label task      #{action['id']}  {action['label']}")
            done += 1
            if not dry_run:
                label(action["id"], action["label"])
        elif action["op"] == "create_feature":
            print(f"create feature  {action['title']}")
            done += 1
            if dry_run:
                continue
            made = board.call("PUT", f"/api/v1/projects/{project}/tasks",
                              {"title": action["title"],
                               "description": action["description"]})
            feature_ids[action["title"]] = made["id"]
            label(made["id"], "feature")
            if action.get("position"):
                board.call("PUT", f"/api/v2/tasks/{made['id']}/position",
                           {"project_view_id": view, "position": action["position"]})
        elif action["op"] == "update_task":
            changes = action["changes"]
            print(f"update task     #{action['id']}  {sorted(changes)}")
            done += 1
            if dry_run:
                continue
            body: dict[str, Any] = {}
            if "title" in changes:
                body["title"] = changes["title"]
            if "description" in changes:
                body["description"] = changes["description"]
            if body:
                board.call("POST", f"/api/v1/tasks/{action['id']}", body)
            if "labels" in changes:
                label(action["id"], changes["labels"][0])
            if "bucket" in changes:
                column = changes["bucket"]
                if column not in buckets:
                    raise SystemExit(f"column {column} does not exist on the board")
                board.call("POST",
                           f"/api/v1/projects/{project}/views/{view}/buckets/"
                           f"{buckets[column]}/tasks", {"task_id": action["id"]})
            parent_key = tasks_by_id.get(action["id"], {}).get("feature")
            parent_id = None
            if parent_key:
                wanted = next((f["title"] for f in map_doc["features"] if f["key"] == parent_key), None)
                parent_id = feature_ids.get(wanted or "")
            if parent_id:
                relate(board, action["id"], parent_id)
        elif action["op"] == "create_backfill":
            print(f"create backfill {action['title']}  ->  {action['feature']}")
            done += 1
            if dry_run:
                continue
            made = board.call("PUT", f"/api/v1/projects/{project}/tasks",
                              {"title": action["title"],
                               "description": action["description"]})
            label(made["id"], "task")
            if action["feature"]:
                parent_id = feature_ids.get(
                    next((f["title"] for f in map_doc["features"] if f["key"] == action["feature"]), ""))
                if parent_id:
                    relate(board, made["id"], parent_id)
            if "Done" in buckets:
                board.call("POST", f"/api/v1/projects/{project}/views/{view}/buckets/"
                           f"{buckets['Done']}/tasks", {"task_id": made["id"]})
        elif action["op"] == "missing_task":
            print(f"MISSING task    #{action['id']}  {action['title']} — the map names a ticket "
                  "that is not on the board")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", default=DEFAULT_MAP)
    parser.add_argument("--project", type=int, default=2)
    parser.add_argument("--view", type=int, default=13)
    parser.add_argument("--base-url", default=os.environ.get("VIKUNJA_URL", "https://vikunja.kub.local"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--description-ok", action="store_true",
                        help="skip description rewrites (used by the idempotence check)")
    args = parser.parse_args(argv)
    if args.dry_run == args.apply:
        parser.error("pass exactly one of --dry-run or --apply")

    token = os.environ.get("VIKUNJA_API_TOKEN", "")
    if not token:
        parser.error("VIKUNJA_API_TOKEN is not set")

    map_doc = json.loads(open(args.map, encoding="utf-8").read())
    problems = validate(map_doc)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 2

    board = Board(args.base_url, token)
    state = read_state(board, args.project, args.view, tuple(map_doc.get("features", [])))
    actions = plan_actions(map_doc, state, description_ok=args.description_ok)
    counts: dict[str, int] = {}
    for action in actions:
        counts[action["op"]] = counts.get(action["op"], 0) + 1
    print(f"{'would apply' if args.dry_run else 'applying'}: "
          + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) if counts else "nothing to do")
    if not actions:
        print("existing: the board already matches the map")
        return 0

    wrote = apply_actions(board, map_doc, state, actions, args.project, args.view, args.dry_run)
    print(f"{'planned' if args.dry_run else 'applied'}: {wrote} object(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
