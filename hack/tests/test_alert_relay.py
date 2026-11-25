import importlib.util
import pathlib
import sys
import unittest

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


class QueryTests(unittest.TestCase):
    def test_the_filter_searches_a_hex_slug_not_the_raw_key(self):
        """The board stores a description as HTML: a raw group key's quotes come back
        as &#34; and the search never matches, which opened a card per retry."""
        p = payload(group='{}/{alertname="DiskFull"}:{alertname="DiskFull", instance="nas"}')
        self.assertRegex(relay.find_query(relay.key_slug(p)),
                         r"^description like '[0-9a-f]{16}'$")

    def test_the_slug_is_stable_for_the_same_group(self):
        self.assertEqual(relay.key_slug(payload(group="g1")), relay.key_slug(payload(group="g1")))

    def test_a_quote_in_the_key_cannot_break_the_filter(self):
        self.assertEqual(relay.find_query("a'b"), "description like 'ab'")


class BucketTests(unittest.TestCase):
    def test_the_title_comes_from_the_configured_view(self):
        card = {"id": 7, "buckets": [{"title": "Doing", "project_view_id": 59},
                                     {"title": "List", "project_view_id": 56}]}
        self.assertEqual(relay.bucket_title(card, 59), "Doing")

    def test_a_card_without_a_bucket_fails_loudly(self):
        """Silently opening would duplicate a card that is already in someone's hands."""
        with self.assertRaises(ValueError):
            relay.bucket_title({"id": 7, "buckets": []}, 59)


class BoardCallTests(unittest.TestCase):
    class Stub(relay.Board):
        def __init__(self):
            super().__init__("http://board", "token", 9, 59, 115)
            self.paths, self.calls = [], []

        def call(self, method, path, body=None):
            self.paths.append(path)
            self.calls.append((method, path, body))
            if method == "GET":
                return {"items": [{"id": 9, "done": False,
                                   "buckets": [{"title": "Backlog", "project_view_id": 59}]},
                                  {"id": 7, "done": True,
                                   "buckets": [{"title": "Done", "project_view_id": 59}]}]}
            return {"id": 42}

    def test_the_lookup_asks_for_the_newest_card_with_its_bucket(self):
        board = self.Stub()
        self.assertEqual(board.find_card("abc")["id"], 9)
        self.assertIn("/api/v2/projects/9/tasks", board.paths[0])
        self.assertIn("expand=buckets", board.paths[0])
        self.assertIn("sort_by=id", board.paths[0])
        self.assertIn("order_by=desc", board.paths[0])
        self.assertIn("description%20like", board.paths[0])

    def test_a_new_card_is_created_in_backlog_with_markdown(self):
        board = self.Stub()
        board.open_card("DiskFull on nas", "body")
        method, path, body = board.calls[-1]
        self.assertEqual(method, "POST")
        self.assertIn("/api/v2/projects/9/tasks?format=markdown", path)
        self.assertEqual(body["bucket_id"], 115)


class BoardLabelTests(unittest.TestCase):
    class Stub(relay.Board):
        def __init__(self, titles):
            super().__init__("http://board", "token", 9, 59, 115)
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
        relay.handle(p, board)
        self.assertEqual(board.looked_up, [relay.key_slug(p)])
        self.assertIn(f"Alert key: {relay.key_slug(p)}", board.calls[0][2])


def card(id=7, done=False, bucket="Backlog"):
    return {"id": id, "done": done,
            "buckets": [{"title": bucket, "project_view_id": 59}]}


class FakeBoard:
    view = 59

    def __init__(self, card=None):
        self.card, self.calls, self.looked_up = card, [], []

    def find_card(self, key):
        self.looked_up.append(key)
        return self.card

    def open_card(self, title, description):
        self.calls.append(("open", title, description))
        return {"id": 42}

    def label(self, task, name):
        self.calls.append(("label", task, name))


class HandleTests(unittest.TestCase):
    def write_modes(self, board):
        return [c[0] for c in board.calls]

    def test_a_new_alert_opens_one_card(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(), board), "opened #42")
        self.assertEqual(self.write_modes(board), ["open", "label", "label"])

    def test_a_new_card_carries_its_labels(self):
        board = FakeBoard()
        relay.handle(payload(), board)
        self.assertEqual([c[2] for c in board.calls if c[0] == "label"], ["task", "alert"])

    def test_a_card_in_backlog_is_left_alone(self):
        board = FakeBoard(card(bucket="Backlog"))
        self.assertEqual(relay.handle(payload(), board), "ignored, #7 in Backlog")
        self.assertEqual(board.calls, [])

    def test_a_card_in_doing_is_left_alone(self):
        board = FakeBoard(card(bucket="Doing"))
        self.assertEqual(relay.handle(payload(), board), "ignored, #7 in Doing")
        self.assertEqual(board.calls, [])

    def test_a_card_in_validation_opens_a_new_card(self):
        board = FakeBoard(card(bucket="Validation"))
        self.assertEqual(relay.handle(payload(), board), "opened #42")
        self.assertEqual(self.write_modes(board), ["open", "label", "label"])

    def test_a_done_card_opens_a_new_card(self):
        board = FakeBoard(card(done=True, bucket="Done"))
        self.assertEqual(relay.handle(payload(), board), "opened #42")

    def test_an_unknown_column_opens_a_new_card(self):
        board = FakeBoard(card(bucket="Idea"))
        self.assertEqual(relay.handle(payload(), board), "opened #42")

    def test_a_resolve_with_a_card_writes_nothing(self):
        for bucket in ("Backlog", "Doing", "Validation"):
            board = FakeBoard(card(bucket=bucket))
            self.assertEqual(relay.handle(payload(status="resolved"), board),
                             f"resolved, ignored, #7 in {bucket}")
            self.assertEqual(board.calls, [])

    def test_a_resolve_without_a_card_writes_nothing(self):
        board = FakeBoard()
        self.assertEqual(relay.handle(payload(status="resolved"), board), "resolved, no card")
        self.assertEqual(board.calls, [])

    def test_a_capitalised_resolve_is_a_resolve(self):
        board = FakeBoard(card(bucket="Doing"))
        self.assertEqual(relay.handle(payload(status="Resolved"), board),
                         "resolved, ignored, #7 in Doing")
        self.assertEqual(board.calls, [])

    def test_a_mixed_group_keeps_the_warning(self):
        p = payload()
        p["alerts"].append({
            "status": "firing", "fingerprint": "w1",
            "labels": {"alertname": "DiskFull", "severity": "warning", "instance": "nuc0"},
            "annotations": {},
        })
        board = FakeBoard()
        relay.handle(p, board)
        self.assertIn("carries 2 alerts", board.calls[0][2])


if __name__ == "__main__":
    unittest.main()
