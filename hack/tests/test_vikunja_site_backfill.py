import pathlib
import subprocess
import sys
import unittest
from unittest import mock

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import vikunja_site_backfill as b  # noqa: E402

DOCS = ("docs: https://github.com/vyrtualsynthese/homelab/blob/develop/"
        "docs/superpowers/plans/x.md")
SITE = ("site: [Rendered page](https://docs.kub.local/homelab/develop/"
        "superpowers/plans/x/)")
BLOCK = f"repo: vyrtualsynthese/homelab\n{DOCS}\nbranch: feature/a\npr: none"

BODY = ("What changed\n"
        "- one chart, nothing else  \n"
        "\n"
        "Verification\n"
        "$ helm template test chart -f chart/values.yaml\n"
        "            - name: URL\n"
        "              value: \"http://sonarr:8989\"")


class AddSiteLineTests(unittest.TestCase):
    def test_gains_the_line_directly_after_docs(self):
        self.assertEqual(b.add_site_line(BLOCK).splitlines()[:4], [
            "repo: vyrtualsynthese/homelab",
            DOCS,
            SITE,
            "branch: feature/a",
        ])

    def test_a_card_that_already_has_the_line_is_unchanged(self):
        text = b.add_site_line(BLOCK)
        self.assertEqual(b.add_site_line(text), text)

    def test_none_is_a_site_line_and_is_not_added_again(self):
        text = b.add_site_line(BLOCK, on_develop=False)
        self.assertEqual(b.add_site_line(text), text)

    def test_a_labelled_docs_line_is_read_as_well(self):
        labelled = BLOCK.replace(
            DOCS, "docs: [Plan — x](https://github.com/vyrtualsynthese/homelab/blob/develop/"
                  "docs/superpowers/plans/x.md)")
        self.assertEqual(b.add_site_line(labelled).splitlines()[2], SITE)

    def test_docs_none_gets_no_line(self):
        text = "repo: vyrtualsynthese/homelab\ndocs: none\nbranch: none\npr: none"
        self.assertEqual(b.add_site_line(text), text)
        self.assertNotIn("site:", b.add_site_line(text))

    def test_a_non_docs_path_gets_no_line(self):
        text = BLOCK.replace(DOCS, "docs: https://github.com/vyrtualsynthese/homelab/"
                                   "blob/develop/cluster/helm/sonarr")
        self.assertEqual(b.add_site_line(text), text)

    def test_the_body_is_preserved_byte_for_byte(self):
        got = b.add_site_line(f"{BLOCK}\n\n{BODY}")
        self.assertTrue(got.endswith(f"\n\n{BODY}"))
        self.assertEqual(got.replace(f"{SITE}\n", "", 1), f"{BLOCK}\n\n{BODY}")

    def test_running_twice_equals_running_once(self):
        once = b.add_site_line(f"{BLOCK}\n\n{BODY}")
        self.assertEqual(b.add_site_line(once), once)

    def test_a_path_that_is_not_on_develop_says_none(self):
        text = b.add_site_line(BLOCK, on_develop=False)
        self.assertEqual(text.splitlines()[2], "site: none")
        self.assertNotIn("docs.kub.local", text)

    def test_a_description_without_a_block_is_left_alone(self):
        self.assertEqual(b.add_site_line("Just a sentence, no block."),
                         "Just a sentence, no block.")

    def test_the_block_is_the_part_before_the_first_blank_line(self):
        self.assertEqual(b.fixed_block(f"{BLOCK}\n\n{BODY}"),
                         ["repo: vyrtualsynthese/homelab", DOCS,
                          "branch: feature/a", "pr: none"])

    def test_html_from_the_editor_is_recognised(self):
        self.assertTrue(b.looks_html("<p>Rough shape, nothing decided.</p>"))
        self.assertTrue(b.looks_html("<h2>Context</h2>\n<p>Firefly runs.</p>"))
        self.assertFalse(b.looks_html(BLOCK))
        self.assertFalse(b.looks_html("Just a sentence."))


class CheckDevelopTests(unittest.TestCase):
    """The pre-flight proves ``origin/develop`` is current, not merely present."""

    @staticmethod
    def _done(returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def _run(self, *answers):
        return mock.patch.object(b.subprocess, "run", side_effect=list(answers))

    def test_a_failed_fetch_stops_the_run_and_names_the_fetch(self):
        with self._run(self._done(128, "", "fatal: could not read from remote")):
            with self.assertRaises(SystemExit) as caught:
                b.check_develop()
        message = str(caught.exception)
        self.assertIn("git fetch origin develop", message)
        self.assertIn("could not read from remote", message)

    def test_a_ref_the_remote_has_moved_past_stops_the_run(self):
        with self._run(self._done(0, "", ""),
                       self._done(0, "a" * 40),
                       self._done(0, ("b" * 40) + "\trefs/heads/develop\n")):
            with self.assertRaises(SystemExit) as caught:
                b.check_develop()
        self.assertIn("advertises", str(caught.exception))

    def test_a_ref_that_matches_the_remote_is_returned(self):
        with self._run(self._done(0, "", ""),
                       self._done(0, "a" * 40),
                       self._done(0, ("a" * 40) + "\trefs/heads/develop\n")):
            self.assertEqual(b.check_develop(), "a" * 40)

    def test_a_local_ref_is_refused_because_it_cannot_be_fetched(self):
        with self.assertRaises(SystemExit) as caught:
            b.check_develop("develop")
        self.assertIn("remote-tracking ref", str(caught.exception))


class StubBoard:
    """A board that answers the one read ``open_cards`` makes."""

    def __init__(self, buckets):
        self.buckets = buckets
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        return {"items": self.buckets}


def _card(card_id):
    return {"id": card_id, "title": f"Card {card_id}", "description": ""}


class OpenCardsTests(unittest.TestCase):
    def test_only_the_open_columns_are_returned(self):
        board = StubBoard([
            {"title": "Idea", "count": 1, "tasks": [_card(1)]},
            {"title": "Shaping", "count": 1, "tasks": [_card(2)]},
            {"title": "Backlog", "count": 2, "tasks": [_card(3), _card(4)]},
            {"title": "Doing", "count": 1, "tasks": [_card(5)]},
            {"title": "Validation", "count": 1, "tasks": [_card(6)]},
            {"title": "Done", "count": 1, "tasks": [_card(7)]},
        ])
        cards = b.open_cards(board, 2, 13)
        self.assertEqual([card["id"] for card in cards], [3, 4, 5, 6])
        self.assertEqual(board.calls[0][0], "GET")
        self.assertTrue(board.calls[0][1].endswith("/buckets/tasks"))

    def test_a_bucket_with_no_tasks_key_is_empty_not_an_error(self):
        board = StubBoard([
            {"title": "Doing", "count": 0},
            {"title": "Backlog", "count": 1, "tasks": [_card(3)]},
        ])
        self.assertEqual([card["id"] for card in b.open_cards(board, 2, 13)], [3])

    def test_a_cap_sized_bucket_aborts_the_run(self):
        board = StubBoard([{"title": "Backlog", "count": 100,
                            "tasks": [_card(i) for i in range(100)]}])
        with self.assertRaises(SystemExit) as caught:
            b.open_cards(board, 2, 13)
        self.assertIn("Backlog", str(caught.exception))

    def test_a_bucket_shorter_than_its_count_aborts_the_run(self):
        board = StubBoard([{"title": "Backlog", "count": 120, "tasks": [_card(3)]}])
        with self.assertRaises(SystemExit) as caught:
            b.open_cards(board, 2, 13)
        self.assertIn("truncated", str(caught.exception))

    def test_the_same_card_in_two_columns_aborts_the_run(self):
        board = StubBoard([
            {"title": "Backlog", "count": 1, "tasks": [_card(3)]},
            {"title": "Doing", "count": 1, "tasks": [_card(3)]},
        ])
        with self.assertRaises(SystemExit):
            b.open_cards(board, 2, 13)


if __name__ == "__main__":
    unittest.main()
