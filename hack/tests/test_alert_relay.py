import importlib.util
import pathlib
import sys
import unittest
from datetime import datetime, timezone

RELAY = pathlib.Path(__file__).resolve().parents[1] / ".." / "cluster" / "helm" / "monitoring" / "alert-relay" / "files" / "relay.py"
spec = importlib.util.spec_from_file_location("relay", RELAY.resolve())
relay = importlib.util.module_from_spec(spec)
sys.modules["relay"] = relay
spec.loader.exec_module(relay)


def payload(status="firing", severity="critical", alerts=None, group="g1"):
    return {
        "status": status,
        "groupKey": group,
        "title": "[FIRING:1] DiskFull",
        "alerts": alerts or [{
            "status": status,
            "fingerprint": "abc123",
            "startsAt": "2026-11-16T20:00:00Z",
            "generatorURL": "https://grafana.kub.local/alerting/rule/1",
            "labels": {"alertname": "DiskFull", "severity": severity, "instance": "nas"},
            "annotations": {"summary": "disk 92% full", "runbook_url": "https://x"},
        }],
    }


class DefaultsTests(unittest.TestCase):
    def test_the_default_board_url_carries_vikunja_s_port(self):
        """Without 3456 the call hangs on port 80 — that was a live outage."""
        self.assertEqual(relay.DEFAULT_BASE, "http://vikunja.vikunja.svc.cluster.local:3456")


class DeliveryLineTests(unittest.TestCase):
    def test_the_line_names_the_rule_and_the_outcome(self):
        self.assertEqual(relay.delivery_line(payload(), "opened #42"),
                         "firing: DiskFull on nas: opened #42")


class KeyTests(unittest.TestCase):
    def test_the_group_key_is_the_incident(self):
        self.assertEqual(relay.incident_key(payload(group="grafana/disk/nas")), "grafana/disk/nas")

    def test_a_payload_without_group_falls_back_to_the_fingerprint(self):
        p = payload()
        p.pop("groupKey")
        self.assertEqual(relay.incident_key(p), "abc123")


class RenderTests(unittest.TestCase):
    def test_title_names_the_rule_and_where(self):
        self.assertEqual(relay.render_title(payload()), "DiskFull on nas")

    def test_the_title_prefers_the_host_over_the_scrape_target(self):
        """A real alert carries instance=10.42.1.198:9902 and host=nas; the pod IP is noise."""
        p = payload()
        p["alerts"][0]["labels"].update({"host": "nas", "instance": "10.42.1.198:9902"})
        self.assertEqual(relay.render_title(p), "DiskFull on nas")

    def test_body_opens_with_the_protocol_block(self):
        body = relay.render_body(payload(), "g1")
        self.assertTrue(body.startswith("repo: vyrtualsynthese/homelab\n"))
        self.assertIn("Alert key: g1", body)

    def test_body_carries_the_slug_and_the_raw_group(self):
        p = payload(group='{}/{alertname="DiskFull"}:{alertname="DiskFull", instance="nas"}')
        body = relay.render_body(p, relay.key_slug(p))
        self.assertIn(f"Alert key: {relay.key_slug(p)}", body)
        self.assertIn("Group: " + relay.incident_key(p), body)

    def test_body_links_grafana_as_markdown(self):
        body = relay.render_body(payload(), "g1")
        self.assertIn("[open the alert](https://grafana.kub.local/alerting/rule/1)", body)

    def test_body_carries_a_next_step(self):
        self.assertIn("## Next step", relay.render_body(payload(), "g1"))

    def test_resolve_comment_says_it_stays_open(self):
        text = relay.render_comment(payload(status="resolved"))
        self.assertIn("stays open", text)

    def test_repetition_comment_counts_the_group(self):
        self.assertIn("1 alert(s)", relay.render_comment(payload()))


class QueryTests(unittest.TestCase):
    def test_the_filter_searches_a_hex_slug_not_the_raw_key(self):
        """The board stores a description as HTML: a raw group key's quotes come back
        as &#34; and the search never matches, which opened a card per retry."""
        p = payload(group='{}/{alertname="DiskFull"}:{alertname="DiskFull", instance="nas"}')
        self.assertRegex(relay.find_query(relay.key_slug(p)),
                         r"^description like '[0-9a-f]{16}' && done = false$")

    def test_the_slug_is_stable_for_the_same_group(self):
        self.assertEqual(relay.key_slug(payload(group="g1")), relay.key_slug(payload(group="g1")))

    def test_a_quote_in_the_key_cannot_break_the_filter(self):
        self.assertEqual(relay.find_query("a'b"), "description like 'ab' && done = false")


class BoardLabelTests(unittest.TestCase):
    class Stub(relay.Board):
        def __init__(self, titles):
            super().__init__("http://board", "token", 9)
            self.titles, self.written = titles, []

        def call(self, method, path, body=None):
            if path == "/api/v1/labels":
                return [{"id": n, "title": t} for n, t in enumerate(self.titles)]
            self.written.append((method, path, body))
            return None

    def test_a_label_the_board_lacks_is_skipped_not_raised(self):
        """A missing label is decoration; losing the card over it is not acceptable."""
        self.assertFalse(self.Stub(["task"]).label(1, "alert"))

    def test_a_label_the_board_has_is_attached(self):
        board = self.Stub(["task", "alert"])
        self.assertTrue(board.label(1, "alert"))
        self.assertEqual(board.written, [("PUT", "/api/v1/tasks/1/labels", {"label_id": 1})])


class ThrottleTests(unittest.TestCase):
    def test_first_comment_always_passes(self):
        self.assertTrue(relay.should_comment(None, 1000.0, 600))

    def test_a_repeat_inside_the_cooldown_is_held(self):
        self.assertFalse(relay.should_comment(500.0, 1000.0, 600))

    def test_a_repeat_after_the_cooldown_passes(self):
        self.assertTrue(relay.should_comment(300.0, 1000.0, 600))


class ResolveBypassTests(unittest.TestCase):
    def test_a_resolve_is_never_throttled(self):
        """An incident ending right after a repeat must leave a trace."""
        board = FakeBoard(card={"id": 7}, last=1500.0)
        now = datetime.fromtimestamp(1600.0, tz=timezone.utc)
        self.assertEqual(relay.handle(payload(status="resolved"), board, 600, now=now),
                         "resolved on #7")
        self.assertEqual([c[0] for c in board.calls], ["comment"])

    def test_a_repeat_is_still_throttled(self):
        board = FakeBoard(card={"id": 7}, last=1500.0)
        now = datetime.fromtimestamp(1600.0, tz=timezone.utc)
        self.assertEqual(relay.handle(payload(), board, 600, now=now), "throttled on #7")


class ParseTests(unittest.TestCase):
    def test_non_json_is_refused_with_a_reason(self):
        with self.assertRaises(ValueError) as caught:
            relay.parse_payload(b"not json")
        self.assertIn("not JSON", str(caught.exception))

    def test_a_body_without_alerts_is_refused(self):
        with self.assertRaises(ValueError):
            relay.parse_payload(b'{"status": "firing"}')


class CardLookupTests(unittest.TestCase):
    def test_the_lookup_uses_the_slug_not_the_raw_group(self):
        """A card is found through the description; the raw key never matches there."""
        p = payload(group='{}/{alertname="DiskFull"}:{alertname="DiskFull", instance="nas"}')
        board = FakeBoard()
        relay.handle(p, board, 600)
        self.assertEqual(board.looked_up, [relay.key_slug(p)])
        self.assertIn(f"Alert key: {relay.key_slug(p)}", board.calls[0][2])


class FakeBoard:
    def __init__(self, card=None, last=None):
        self.card, self.last, self.calls, self.looked_up = card, last, [], []

    def find_open_card(self, key):
        self.looked_up.append(key)
        return self.card

    def open_card(self, title, description):
        self.calls.append(("open", title, description))
        return {"id": 42}

    def comment(self, task, text):
        self.calls.append(("comment", task, text))

    def label(self, task, name):
        self.calls.append(("label", task, name))

    def last_comment_epoch(self, task):
        return self.last


class HandleTests(unittest.TestCase):
    def test_a_warning_opens_a_card(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(severity="warning"), board, 600), "opened #42")
        self.assertEqual(board.calls[0][0], "open")

    def test_a_mixed_group_keeps_the_warning(self):
        p = payload()
        p["alerts"].append({
            "status": "firing", "fingerprint": "w1",
            "labels": {"alertname": "DiskFull", "severity": "warning", "instance": "nuc0"},
            "annotations": {},
        })
        board = FakeBoard()
        relay.handle(p, board, 600)
        self.assertIn("carries 2 alerts", board.calls[0][2])

    def test_a_resolve_without_a_card_writes_nothing(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(status="resolved"), board, 600),
                         "resolved with no card")
        self.assertEqual(board.calls, [])

    def test_a_new_critical_alert_opens_one_card(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(), board, 600), "opened #42")
        self.assertEqual(board.calls[0][0], "open")

    def test_a_new_card_carries_its_labels(self):
        """An unlabelled card fails the board's own check: every ticket needs a kind."""
        board = FakeBoard()
        relay.handle(payload(), board, 600)
        labels = [c[2] for c in board.calls if c[0] == "label"]
        self.assertEqual(labels, ["task", "alert"])

    def test_a_repeat_comments_instead_of_opening(self):
        board = FakeBoard(card={"id": 7}, last=1000.0)
        now = datetime.fromtimestamp(2000.0, tz=timezone.utc)
        self.assertEqual(relay.handle(payload(), board, 600, now=now), "commented on #7")
        self.assertEqual([c[0] for c in board.calls], ["comment"])

    def test_a_repeat_inside_the_cooldown_is_throttled(self):
        board = FakeBoard(card={"id": 7}, last=1500.0)
        now = datetime.fromtimestamp(1600.0, tz=timezone.utc)
        self.assertEqual(relay.handle(payload(), board, 600, now=now), "throttled on #7")
        self.assertEqual(board.calls, [])


if __name__ == "__main__":
    unittest.main()
