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


class SeverityTests(unittest.TestCase):
    def test_critical_is_wanted(self):
        self.assertTrue(relay.is_critical(payload()["alerts"][0]))

    def test_warning_is_dropped(self):
        self.assertFalse(relay.is_critical(payload(severity="warning")["alerts"][0]))

    def test_missing_severity_is_not_critical(self):
        alert = {"labels": {"alertname": "X"}}
        self.assertFalse(relay.is_critical(alert))


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

    def test_body_opens_with_the_protocol_block(self):
        body = relay.render_body(payload(), "g1")
        self.assertTrue(body.startswith("repo: vyrtualsynthese/homelab\n"))
        self.assertIn("Alert key: g1", body)

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


class ThrottleTests(unittest.TestCase):
    def test_first_comment_always_passes(self):
        self.assertTrue(relay.should_comment(None, 1000.0, 600))

    def test_a_repeat_inside_the_cooldown_is_held(self):
        self.assertFalse(relay.should_comment(500.0, 1000.0, 600))

    def test_a_repeat_after_the_cooldown_passes(self):
        self.assertTrue(relay.should_comment(300.0, 1000.0, 600))


class ParseTests(unittest.TestCase):
    def test_non_json_is_refused_with_a_reason(self):
        with self.assertRaises(ValueError) as caught:
            relay.parse_payload(b"not json")
        self.assertIn("not JSON", str(caught.exception))

    def test_a_body_without_alerts_is_refused(self):
        with self.assertRaises(ValueError):
            relay.parse_payload(b'{"status": "firing"}')


class FakeBoard:
    def __init__(self, card=None, last=None):
        self.card, self.last, self.calls = card, last, []

    def find_open_card(self, key):
        return self.card

    def open_card(self, title, description):
        self.calls.append(("open", title))
        return {"id": 42}

    def comment(self, task, text):
        self.calls.append(("comment", task, text))

    def last_comment_epoch(self, task):
        return self.last


class HandleTests(unittest.TestCase):
    def test_a_warning_writes_nothing(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(severity="warning"), board, 600), "ignored: not critical")
        self.assertEqual(board.calls, [])

    def test_a_new_critical_alert_opens_one_card(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(), board, 600), "opened #42")
        self.assertEqual(board.calls[0][0], "open")

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
