import importlib.util
import pathlib
import sys
import unittest
import urllib.error

LIFECYCLE = pathlib.Path(__file__).resolve().parents[1] / ".." / "cluster" / "helm" / "monitoring" / "alert-relay" / "files" / "lifecycle.py"
spec = importlib.util.spec_from_file_location("lifecycle", LIFECYCLE.resolve())
lifecycle = importlib.util.module_from_spec(spec)
sys.modules["lifecycle"] = lifecycle
spec.loader.exec_module(lifecycle)


def labels(*names):
    return [{"title": name} for name in names]


def alert_card(column="Backlog", done=False, label="alert", priority=None):
    card = {"id": 7, "title": "DiskFull on nas", "done": done, "column": column,
            "labels": labels("task", label, *([priority] if priority else [])),
            "related_tasks": {"blocked": [{"id": 90}]}}
    return card


def fix(column="Doing", done=False):
    return {"id": 90, "done": done,
            "buckets": [{"title": column, "project_view_id": 13}]}


class ClosedTests(unittest.TestCase):
    def test_validation_closes_a_fix_even_when_not_done(self):
        self.assertTrue(lifecycle.closed(fix(column="Validation")))

    def test_done_closes_a_fix(self):
        self.assertTrue(lifecycle.closed(fix(column="Done", done=True)))

    def test_doing_is_open(self):
        self.assertFalse(lifecycle.closed(fix(column="Doing")))

    def test_a_fix_without_a_bucket_fails_loudly(self):
        with self.assertRaises(ValueError):
            lifecycle.closed({"id": 90, "done": False, "buckets": []})


class TargetTests(unittest.TestCase):
    def test_no_fixes_leaves_the_card_alone(self):
        self.assertIsNone(lifecycle.target_column(alert_card(), []))

    def test_open_fixes_move_backlog_to_doing(self):
        self.assertEqual(lifecycle.target_column(alert_card("Backlog"), [fix("Doing")]), "Doing")

    def test_open_fixes_leave_a_doing_card_alone(self):
        self.assertIsNone(lifecycle.target_column(alert_card("Doing"), [fix("Doing")]))

    def test_all_closed_moves_backlog_to_validation(self):
        self.assertEqual(lifecycle.target_column(alert_card("Backlog"), [fix("Validation")]), "Validation")

    def test_all_closed_moves_doing_to_validation(self):
        self.assertEqual(lifecycle.target_column(alert_card("Doing"), [fix("Done", done=True)]), "Validation")

    def test_validation_is_terminal(self):
        card = alert_card("Validation")
        self.assertIsNone(lifecycle.target_column(card, [fix("Backlog")]))

    def test_a_non_alert_card_is_untouched(self):
        self.assertIsNone(lifecycle.target_column(alert_card(label="task"), [fix("Doing")]))

    def test_a_frozen_card_is_untouched(self):
        self.assertIsNone(lifecycle.target_column(alert_card(label="frozen"), [fix("Doing")]))

    def test_a_done_card_is_untouched(self):
        self.assertIsNone(lifecycle.target_column(alert_card(done=True), [fix("Doing")]))


class PositionTests(unittest.TestCase):
    def test_an_empty_column_starts_at_one(self):
        self.assertEqual(lifecycle.position_for(alert_card(), []), 1)

    def test_a_no_priority_card_lands_last(self):
        cards = [{"id": 1, "position": 10, "labels": labels("priority/high")},
                 {"id": 2, "position": 20, "labels": []}]
        self.assertEqual(lifecycle.position_for(alert_card(), cards), 20 + 1_000_000)

    def test_a_high_priority_card_lands_first(self):
        cards = [{"id": 1, "position": 10, "labels": labels("priority/medium")}]
        self.assertEqual(lifecycle.position_for(alert_card(priority="priority/high"), cards), 5)

    def test_a_card_lands_between_its_band_neighbours(self):
        cards = [{"id": 1, "position": 10, "labels": labels("priority/high")},
                 {"id": 2, "position": 20, "labels": labels("priority/low")}]
        self.assertEqual(lifecycle.position_for(alert_card(priority="priority/medium"), cards), 15)


class FakeBoard:
    def __init__(self, cards, fixes):
        self.cards, self.fixes, self.moves = cards, fixes, []

    def alert_cards(self):
        return self.cards

    def fix_card(self, task_id):
        return self.fixes[task_id]

    def move(self, card, target):
        self.moves.append((card["id"], target))


class RunTests(unittest.TestCase):
    def test_a_backlog_card_with_open_fixes_moves_to_doing(self):
        board = FakeBoard([alert_card("Backlog")], {90: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (1, 0))
        self.assertEqual(board.moves, [(7, "Doing")])

    def test_a_doing_card_with_open_fixes_is_not_moved(self):
        board = FakeBoard([alert_card("Doing")], {90: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (0, 0))
        self.assertEqual(board.moves, [])

    def test_all_closed_moves_the_card_to_validation(self):
        board = FakeBoard([alert_card("Doing")], {90: fix("Validation")})
        self.assertEqual(lifecycle.run_once(board), (1, 0))
        self.assertEqual(board.moves, [(7, "Validation")])

    def test_a_non_alert_card_is_not_inspected(self):
        board = FakeBoard([alert_card("Backlog", label="task")], {90: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (0, 0))
        self.assertEqual(board.moves, [])

    def test_a_frozen_card_is_not_moved(self):
        board = FakeBoard([alert_card("Backlog", label="frozen")], {90: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (0, 0))
        self.assertEqual(board.moves, [])

    def test_a_broken_fix_fails_its_card_and_does_not_stop_the_other(self):
        good = alert_card("Backlog")
        good["id"] = 8
        board = FakeBoard([alert_card("Backlog"), good],
                          {90: {"id": 90, "done": False, "buckets": []}})
        self.assertEqual(lifecycle.run_once(board), (0, 2))
        self.assertEqual(board.moves, [])


class BoardCallTests(unittest.TestCase):
    class Stub(lifecycle.Board):
        def __init__(self):
            super().__init__("http://board", "token", 9, 59, 111, 116)
            self.calls = []

        def call(self, method, path, body=None):
            self.calls.append((method, path, body))
            if path.endswith("/buckets/tasks"):
                return {"items": [
                    {"id": 115, "title": "Backlog",
                     "tasks": [{"id": 7, "title": "a", "done": False,
                                "labels": [{"title": "alert"}], "related_tasks": {}}]},
                    {"id": 116, "title": "Validation", "tasks": []}]}
            return None

    def test_the_board_read_names_each_card_s_column(self):
        cards = self.Stub().alert_cards()
        self.assertEqual(cards[0]["column"], "Backlog")

    def test_a_move_uses_the_bucket_then_sets_the_position(self):
        board = self.Stub()
        board.move({"id": 7, "position": 0, "labels": []}, "Validation")
        self.assertEqual(board.calls[0],
                         ("PUT", "/api/v2/projects/9/views/59/buckets/116/tasks", {"task_id": 7}))
        method, path, body = board.calls[-1]
        self.assertEqual(method, "PUT")
        self.assertIn("/api/v2/tasks/7/position", path)
        self.assertEqual(body["project_view_id"], 59)


class ReadFailureTests(unittest.TestCase):
    class Board(FakeBoard):
        def fix_card(self, task_id):
            if task_id == 90:
                raise urllib.error.HTTPError("http://board", 403, "Forbidden", None, None)
            return self.fixes[task_id]

    def test_a_fix_that_cannot_be_read_fails_only_its_card(self):
        """A half-applied share must not stop the rest of the board from moving."""
        readable = alert_card("Backlog")
        readable["id"] = 8
        readable["related_tasks"] = {"blocked": [{"id": 91}]}
        board = self.Board([alert_card("Backlog"), readable], {91: fix("Doing")})
        self.assertEqual(lifecycle.run_once(board), (1, 1))
        self.assertEqual(board.moves, [(8, "Doing")])
