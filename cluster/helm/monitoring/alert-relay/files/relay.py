#!/usr/bin/env python3
"""Turn a Grafana alert into a card on the alerts board.

Grafana sends its own JSON; the board creates a card from {"title": …}. Nothing
translates between them, so this does: one endpoint, one card per incident, a
comment per repetition, a comment on resolve, and never a close.

The rules it enforces, in order:

  * a warning is logged and dropped — only critical reaches the board;
  * a firing alert with no open card opens one;
  * the same alert firing again is a comment on that card, never a second card;
  * a resolve is a comment and nothing else: the card stays where it is until a
    session investigates it;
  * a malformed body is refused out loud, never dropped in silence.

Deployed from the chart's ConfigMap; see docs/runbooks/alert-relay.md.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

BOARD_NOTE = "Opened by the alert relay from Grafana. `bot-alertmanager` wrote it."


# ---------------------------------------------------------------------------
# Pure helpers, tested in hack/tests/test_alert_relay.py.
# ---------------------------------------------------------------------------
def is_critical(alert: dict) -> bool:
    """Only critical alerts are worth an investigation."""
    labels = alert.get("labels") or {}
    return str(labels.get("severity", "")).lower() == "critical"


def incident_key(payload: dict) -> str:
    """One incident, however many alerts and repetitions it carries.

    Grafana groups alerts before delivering them, so the groupKey is stable for
    the same rule firing on the same labels — that is the incident. Falling back
    to the single alert's fingerprint keeps a hand-made payload working.
    """
    key = payload.get("groupKey") or ""
    if key:
        return key
    alerts = payload.get("alerts") or [{}]
    return str(alerts[0].get("fingerprint") or "unknown")


def render_title(payload: dict) -> str:
    """What the card is called: the rule, and where it fired."""
    alerts = payload.get("alerts") or [{}]
    labels = alerts[0].get("labels") or {}
    rule = labels.get("alertname") or payload.get("title") or "Grafana alert"
    where = labels.get("instance") or labels.get("job") or ""
    return f"{rule} on {where}" if where else rule


def render_body(payload: dict, key: str, grafana_url: str = "") -> str:
    """The description every alert card opens with, protocol block first."""
    alerts = payload.get("alerts") or [{}]
    first = alerts[0]
    labels = first.get("labels") or {}
    annotations = first.get("annotations") or {}
    rule = labels.get("alertname") or "unknown rule"
    link = first.get("generatorURL") or grafana_url or ""

    lines = [
        "repo: vyrtualsynthese/homelab",
        "docs: none",
        "branch: none",
        "pr: none",
        "",
        f"Alert key: {key}",
        f"State: {payload.get('status') or first.get('status') or 'firing'}",
        f"Rule: {rule}",
        f"Severity: {labels.get('severity', 'unknown')}",
        f"Started: {first.get('startsAt', 'unknown')}",
        f"Grafana: [open the alert]({link})" if link else "Grafana: none",
        "",
        "Labels",
    ]
    for name in sorted(labels):
        lines.append(f"- {name}: {labels[name]}")
    if annotations:
        lines += ["", "Annotations"]
        for name in sorted(annotations):
            lines.append(f"- {name}: {annotations[name]}")
    if len(alerts) > 1:
        lines += ["", f"This incident carries {len(alerts)} alerts; the first is shown above."]
    lines += [
        "",
        "## Next step",
        "Read the alert, look at the service it names, and write what you found below. Close it as "
        "resolved only with a cause: what happened, what fixed it, and what stops it recurring. If the "
        "fix is real work, open a card for it on the board and link it here.",
        "",
        BOARD_NOTE,
    ]
    return "\n".join(lines)


def render_comment(payload: dict, now: datetime | None = None) -> str:
    """Repetition and resolution are comments: same incident, more evidence."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    alerts = payload.get("alerts") or [{}]
    if (payload.get("status") or "").lower() == "resolved":
        return (f"Resolved {stamp}. The card stays open on purpose: what caused it, and what stops it "
                "coming back, belongs here before it is closed.")
    count = len(alerts)
    return f"Fired again {stamp} ({count} alert(s) in this group)."


def should_comment(last_comment_epoch: float | None, now_epoch: float, cooldown: float) -> bool:
    """A flapping rule must not write a hundred comments in an hour."""
    if last_comment_epoch is None:
        return True
    return (now_epoch - last_comment_epoch) >= cooldown


def parse_payload(raw: bytes) -> dict:
    """Grafana's body, or a clean refusal — never a silent drop."""
    try:
        payload = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"body is not JSON: {error}") from error
    if not isinstance(payload, dict) or "alerts" not in payload:
        raise ValueError("body carries no alerts")
    return payload


# ---------------------------------------------------------------------------
# The board.
# ---------------------------------------------------------------------------
class Board:
    def __init__(self, base: str, token: str, project: int):
        self.base = base.rstrip("/")
        self.token = token
        self.project = project

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=15) as answer:
            raw = answer.read()
        return json.loads(raw) if raw else None

    def find_open_card(self, key: str) -> dict | None:
        query = urllib.parse.quote(f'description like "{key}" && done = false')
        found = self.call("GET", f"/api/v1/projects/{self.project}/tasks?per_page=20&filter={query}")
        return found[0] if found else None

    def open_card(self, title: str, description: str) -> dict:
        return self.call("PUT", f"/api/v1/projects/{self.project}/tasks",
                         {"title": title, "description": description})

    def comment(self, task: int, text: str) -> None:
        self.call("PUT", f"/api/v1/tasks/{task}/comments", {"comment": text})

    def last_comment_epoch(self, task: int) -> float | None:
        comments = self.call("GET", f"/api/v1/tasks/{task}/comments") or []
        if not comments:
            return None
        created = comments[-1].get("created", "")
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# The endpoint.
# ---------------------------------------------------------------------------
def handle(payload: dict, board: Board, cooldown: float, now: datetime | None = None) -> str:
    """What to do with one delivery. Returns a word for the log."""
    key = incident_key(payload)
    alerts = [a for a in (payload.get("alerts") or []) if is_critical(a)]
    if not alerts:
        return "ignored: not critical"

    payload = dict(payload, alerts=alerts)
    card = board.find_open_card(key)
    if card is None:
        created = board.open_card(render_title(payload), render_body(payload, key))
        return f"opened #{created.get('id')}"

    stamp = (now or datetime.now(timezone.utc)).timestamp()
    last = board.last_comment_epoch(card["id"])
    if not should_comment(last, stamp, cooldown):
        return f"throttled on #{card['id']}"
    board.comment(card["id"], render_comment(payload, now))
    return f"commented on #{card['id']}"


class Handler(BaseHTTPRequestHandler):
    board: Board
    cooldown: float

    def do_GET(self):  # noqa: N802 — the name is the HTTP method
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):  # noqa: N802
        if self.path != "/alert":
            self.send_response(404)
            self.end_headers()
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            payload = parse_payload(raw)
        except ValueError as error:
            print(f"refused: {error}", file=sys.stderr, flush=True)
            self.send_response(400)
            self.end_headers()
            return
        try:
            outcome = handle(payload, self.board, self.cooldown)
        except Exception as error:  # noqa: BLE001 — a failed delivery must be visible
            print(f"failed: {error}", file=sys.stderr, flush=True)
            self.send_response(502)
            self.end_headers()
            return
        print(f"{payload.get('status')}: {outcome}", flush=True)
        self.send_response(202)
        self.end_headers()

    def log_message(self, fmt, *args):  # noqa: A003
        return  # the outcome is logged above; keep the noise down


def main() -> int:
    board = Board(
        base=os.environ.get("VIKUNJA_BASE", "http://vikunja.vikunja.svc.cluster.local"),
        token=os.environ["VIKUNJA_TOKEN"],
        project=int(os.environ.get("ALERTS_PROJECT", "9")),
    )
    Handler.board = board
    Handler.cooldown = float(os.environ.get("COMMENT_COOLDOWN_SECONDS", "600"))
    port = int(os.environ.get("PORT", "8080"))
    HTTPServer(("", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
