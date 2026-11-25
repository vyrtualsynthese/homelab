"""Keep an alert card moving with the work it spawned.

An alert card is opened by the relay in Backlog and investigated by a session. The
session records each fix card as a `blocked` relation on the alert card, so the
alert waits on its fixes. This script is the backstop that moves the card:

  * at least one blocked fix, not all of them closed -> the card belongs in Doing;
  * every blocked fix closed -> the card belongs in Validation;
  * no blocked fix, already in Validation, frozen or not an alert -> nothing.

A fix counts as closed when it is done, or when it sits in Validation or Done. That
second half is a deliberate exception to the board's "only Done releases" rule: work
waiting for acceptance is finished work, and the alert follows it.

Validation is terminal. Nothing here moves a card backwards, and nothing closes one.

Deployed from the chart's ConfigMap and run by a CronJob; see
docs/runbooks/alert-relay.md.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

CLOSED_COLUMNS = ("Validation", "Done")
COLUMNS = ("Backlog", "Doing")
BANDS = {"priority/critical": 0, "priority/high": 1, "priority/medium": 2,
         "priority/low": 3}
NO_PRIORITY = 4
STEP = 1_000_000


def has_label(card: dict, name: str) -> bool:
    for label in card.get("labels") or []:
        if (label.get("title") or "") == name:
            return True
    return False


def band(card: dict) -> int:
    """The card's place in the priority order; no priority is last.

    Alert cards carry no priority label, so they land below every prioritised card
    in the column they enter, which is what the board asks for.
    """
    lowest = NO_PRIORITY
    for label in card.get("labels") or []:
        place = BANDS.get(label.get("title") or "")
        if place is not None and place < lowest:
            lowest = place
    return lowest


def closed(fix: dict) -> bool:
    """Whether a fix card releases the alert waiting on it.

    A fix reported without any bucket is a broken answer, not a fix in an unknown
    place: guessing open keeps the alert stuck, guessing closed closes it over real
    work. Fail the card loudly instead.
    """
    if fix.get("done"):
        return True
    buckets = fix.get("buckets") or []
    if not buckets:
        raise ValueError(f"fix #{fix.get('id')} carries no bucket")
    return any((bucket.get("title") or "") in CLOSED_COLUMNS for bucket in buckets)


def target_column(card: dict, fixes: list[dict]) -> str | None:
    """Where the card belongs, or None when it stays where it is."""
    if card.get("done") or has_label(card, "frozen") or not has_label(card, "alert"):
        return None
    if card.get("column") not in COLUMNS or not fixes:
        return None
    # Every fix is checked before the answer is used, so a broken one cannot hide
    # behind a closed one in front of it.
    states = [closed(fix) for fix in fixes]
    if all(states):
        return "Validation"
    return "Doing" if card.get("column") == "Backlog" else None


def position_for(card: dict, column_cards: list[dict]) -> float:
    """Where the card sorts among the cards already in the target column.

    The board's order is the priority bands: critical first, then high, medium, low,
    and unlabelled last. A card that nothing in the column blocks sits above the
    first card at or below its own band, so an alert card — which carries no
    priority — enters at the top of the unlabelled cards and below every
    prioritised one.
    """
    if not column_cards:
        return 1
    mine = band(card)
    index = len(column_cards)
    for place, other in enumerate(column_cards):
        if band(other) >= mine:
            index = place
            break
    if index == 0:
        return column_cards[0].get("position", 0) / 2
    if index == len(column_cards):
        return column_cards[-1].get("position", 0) + STEP
    return (column_cards[index - 1].get("position", 0)
            + column_cards[index].get("position", 0)) / 2


# ---------------------------------------------------------------------------
# The board.
# ---------------------------------------------------------------------------
DEFAULT_BASE = "http://vikunja.vikunja.svc.cluster.local:3456"


class Board:
    def __init__(self, base: str, token: str, project: int, view: int,
                 doing_bucket: int, validation_bucket: int):
        self.base = base.rstrip("/")
        self.token = token
        self.project = project
        self.view = view
        self.doing_bucket = doing_bucket
        self.validation_bucket = validation_bucket

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=10) as answer:
            raw = answer.read()
        return json.loads(raw) if raw else None

    def alert_cards(self) -> list[dict]:
        """Every card on the board, each carrying the column it sits in."""
        answer = self.call("GET", f"/api/v2/projects/{self.project}/views/{self.view}/buckets/tasks")
        cards = []
        for bucket in (answer or {}).get("items") or []:
            for task in bucket.get("tasks") or []:
                task["column"] = bucket.get("title") or ""
                cards.append(task)
        return cards

    def fix_card(self, task_id: int) -> dict:
        return self.call("GET", f"/api/v2/tasks/{task_id}?expand=buckets")

    def move(self, card: dict, target: str) -> None:
        bucket = {"Doing": self.doing_bucket, "Validation": self.validation_bucket}[target]
        self.call("PUT", f"/api/v2/projects/{self.project}/views/{self.view}/buckets/{bucket}/tasks",
                  {"task_id": card["id"]})
        # The column is re-read because a task's position is per view and only the
        # board knows where its neighbours sit. The card being moved is excluded so
        # it cannot become its own neighbour, and a card whose position the board
        # did not report is not treated as being at zero.
        neighbours = [other for other in self.alert_cards()
                      if other.get("column") == target and other.get("id") != card.get("id")
                      and isinstance(other.get("position"), (int, float))]
        position = position_for(card, neighbours) if neighbours else STEP
        self.call("PUT", f"/api/v2/tasks/{card['id']}/position",
                  {"position": position, "project_view_id": self.view})


def run_once(board) -> tuple[int, int]:
    """One pass over the board. Returns (moved, failed)."""
    moved = failed = 0
    for card in board.alert_cards():
        if card.get("done") or has_label(card, "frozen") or not has_label(card, "alert"):
            continue
        if card.get("column") not in COLUMNS:
            continue
        blocked = (card.get("related_tasks") or {}).get("blocked") or []
        if not blocked:
            continue
        try:
            fixes = [board.fix_card(other.get("id")) for other in blocked]
            target = target_column(card, fixes)
            if target is None:
                continue
            open_fixes = sum(1 for fix in fixes if not closed(fix))
            # The move is inside the guard: a write that a share forbids fails this
            # card, not the whole pass.
            board.move(card, target)
        except (ValueError, urllib.error.URLError) as broken:
            print(f"failed #{card.get('id')}: {broken}", file=sys.stderr)
            failed += 1
            continue
        print(f"#{card['id']} {card.get('title', '')}: {card.get('column')} -> {target} "
              f"({open_fixes} fixes open)")
        moved += 1
    if moved or failed:
        print(f"lifecycle: {moved} moved, {failed} failed")
    return moved, failed


def main() -> int:
    board = Board(
        base=os.environ.get("VIKUNJA_BASE", DEFAULT_BASE),
        token=os.environ["VIKUNJA_TOKEN"],
        project=int(os.environ.get("ALERTS_PROJECT", "9")),
        view=int(os.environ.get("ALERTS_VIEW", "59")),
        doing_bucket=int(os.environ.get("ALERTS_DOING_BUCKET", "111")),
        validation_bucket=int(os.environ.get("ALERTS_VALIDATION_BUCKET", "116")),
    )
    _, failed = run_once(board)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
