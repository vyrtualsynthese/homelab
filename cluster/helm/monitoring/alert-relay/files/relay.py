#!/usr/bin/env python3
"""Turn a Grafana alert into a card on the alerts board.

Grafana sends its own JSON; the board creates a card from {"title": …}. Nothing
translates between them, so this does: one endpoint, one card per incident, and
the card's column decides what a delivery does.

The rules it enforces, in order:

  * a firing with no card, or with the newest card in Done, opens one;
  * a firing whose newest card is in Backlog or Doing writes nothing;
  * a firing whose newest card is in Validation, or any other open column,
    opens a new card;
  * a resolve writes nothing at all and never moves a card;
  * a malformed body is refused out loud, never dropped in silence.

Deployed from the chart's ConfigMap; see docs/runbooks/alert-relay.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOARD_NOTE = "Opened by the alert relay from Grafana. `bot-alertmanager` wrote it."

# Vikunja listens on 3456. A base without that port reaches nothing and hangs
# until the socket timeout, which is how the first deployment failed.
DEFAULT_BASE = "http://vikunja.vikunja.svc.cluster.local:3456"


# ---------------------------------------------------------------------------
# Pure helpers, tested in hack/tests/test_alert_relay.py.
# ---------------------------------------------------------------------------
def delivery_line(payload: dict, outcome: str) -> str:
    """One line per delivery, so a silent relay is obvious in its own log."""
    status = str(payload.get("status") or "firing").lower()
    return f"{status}: {render_title(payload)}: {outcome}"


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


def key_slug(payload: dict) -> str:
    """A searchable fingerprint of the incident, never the raw group key.

    The board stores a description as HTML, so a group key's quotes come back as
    &#34; and a `like` on it never matches: every retry opened another card. A hex
    digest has no character the filter language can mangle.
    """
    return hashlib.sha256(incident_key(payload).encode()).hexdigest()[:16]


def render_title(payload: dict) -> str:
    """What the card is called: the rule, and where it fired."""
    alerts = payload.get("alerts") or [{}]
    labels = alerts[0].get("labels") or {}
    rule = labels.get("alertname") or payload.get("title") or "Grafana alert"
    # host first: a real alert carries instance=10.42.1.198:9902 (the scrape target)
    # and host=nas. The pod IP tells a reader nothing and changes when the pod does.
    where = labels.get("host")
    # A batch alert is the exception the other way round: JobFailed reads
    # kube_job_status_failed, whose instance is kube-state-metrics' own address and
    # whose job label is the scrape job, so the only useful name is the Job's. The
    # namespace comes with it because a Job name alone does not say which board.
    if not where and labels.get("job_name"):
        namespace = labels.get("namespace")
        where = f"{namespace}/{labels['job_name']}" if namespace else labels["job_name"]
    where = where or labels.get("instance") or labels.get("job") or ""
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
        f"Group: {incident_key(payload)}",
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


def find_query(key: str) -> str:
    """The search that finds the newest card for this incident.

    Done cards are included on purpose: a closed incident must not stop the next
    one from opening a card. Vikunja's filter language quotes values with single
    quotes; double quotes make the API answer 400, which is how this cost a
    round. A quote inside the key is dropped rather than escaped, because the
    language has no escape.
    """
    return f"description like '{key.replace(chr(39), '')}'"


def parse_payload(raw: bytes) -> dict:
    """Grafana's body, or a clean refusal — never a silent drop."""
    try:
        payload = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"body is not JSON: {error}") from error
    if not isinstance(payload, dict) or "alerts" not in payload:
        raise ValueError("body carries no alerts")
    return payload


def bucket_title(card: dict, view: int) -> str:
    """The card's column in the kanban view whose id is `view`.

    A card reported without a bucket is a broken answer, not a card in an unknown
    place: opening another would duplicate one someone is already holding. Fail the
    delivery visibly instead, so Grafana records it and no card is created.
    """
    for bucket in card.get("buckets") or []:
        if bucket.get("project_view_id") == view:
            return bucket.get("title") or ""
    raise ValueError(f"card #{card.get('id')} carries no bucket for view {view}")


# ---------------------------------------------------------------------------
# The board.
# ---------------------------------------------------------------------------
class Board:
    def __init__(self, base: str, token: str, project: int, view: int, backlog_bucket: int):
        self.base = base.rstrip("/")
        self.token = token
        self.project = project
        self.view = view
        self.backlog_bucket = backlog_bucket

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=5) as answer:
            raw = answer.read()
        return json.loads(raw) if raw else None

    def find_card(self, key: str) -> dict | None:
        """The newest card for the key, with its column already expanded.

        Newest because one key recurs over months: the current incident is the
        latest card, never the oldest.
        """
        query = urllib.parse.quote(find_query(key), safe="")
        path = (f"/api/v2/projects/{self.project}/tasks?per_page=10"
                f"&sort_by=id&order_by=desc&expand=buckets&filter={query}")
        found = self.call("GET", path)
        items = found.get("items") if isinstance(found, dict) else found
        return items[0] if items else None

    def open_card(self, title: str, description: str) -> dict:
        return self.call("POST", f"/api/v2/projects/{self.project}/tasks?format=markdown",
                         {"title": title, "description": description,
                          "bucket_id": self.backlog_bucket})

    def label(self, task: int, name: str) -> bool:
        """Label the card if the board has that label; a missing one is not fatal.

        An unlabelled card fails the board's own check, so `task` matters; `alert`
        is decoration, and losing the card over it is worse than the missing label.
        """
        labels = self.call("GET", "/api/v1/labels") or []
        found = next((l["id"] for l in labels if l.get("title") == name), None)
        if found is None:
            print(f"label {name!r} does not exist on the board", file=sys.stderr, flush=True)
            return False
        self.call("PUT", f"/api/v1/tasks/{task}/labels", {"label_id": found})
        return True


# ---------------------------------------------------------------------------
# The endpoint.
# ---------------------------------------------------------------------------
def handle(payload: dict, board: Board) -> str:
    """What to do with one delivery. Returns the outcome for the log."""
    key = key_slug(payload)
    resolved = (payload.get("status") or "").lower() == "resolved"
    card = board.find_card(key)
    if resolved:
        if card is None:
            return "resolved, no card"
        return f"resolved, ignored, #{card['id']} in {bucket_title(card, board.view)}"
    if card is not None and not card.get("done"):
        column = bucket_title(card, board.view)
        if column in ("Backlog", "Doing"):
            return f"ignored, #{card['id']} in {column}"
    created = board.open_card(render_title(payload), render_body(payload, key))
    for name in ("task", "alert"):
        board.label(created["id"], name)
    return f"opened #{created.get('id')}"


class Handler(BaseHTTPRequestHandler):
    board: Board

    def do_GET(self):  # noqa: N802 — the name is the HTTP method
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            try:
                self.wfile.write(b"ok")
            except BrokenPipeError:
                # A probe that gave up early is not worth a traceback in the log.
                pass
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
            outcome = handle(payload, self.board)
        except Exception as error:  # noqa: BLE001 — a failed delivery must be visible
            print(f"failed: {error}", file=sys.stderr, flush=True)
            self.send_response(502)
            self.end_headers()
            return
        print(delivery_line(payload, outcome), flush=True)
        self.send_response(202)
        self.end_headers()

    def log_message(self, fmt, *args):  # noqa: A003
        return  # the outcome is logged above; keep the noise down


def main() -> int:
    board = Board(
        base=os.environ.get("VIKUNJA_BASE", DEFAULT_BASE),
        token=os.environ["VIKUNJA_TOKEN"],
        project=int(os.environ.get("ALERTS_PROJECT", "9")),
        view=int(os.environ.get("ALERTS_VIEW", "59")),
        backlog_bucket=int(os.environ.get("ALERTS_BACKLOG_BUCKET", "115")),
    )
    Handler.board = board
    port = int(os.environ.get("PORT", "8080"))
    # Threaded on purpose: one slow board call must not stall the probes. A wrong
    # port once made every request hang for fifteen seconds and the health checks
    # timed out behind it.
    ThreadingHTTPServer(("", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
