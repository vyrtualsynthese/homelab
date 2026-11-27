import pathlib
import subprocess
import sys
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import vikunja_site_backfill as b  # noqa: E402

DOCS = ("docs: https://github.com/vyrtualsynthese/homelab/blob/develop/"
        "docs/superpowers/plans/x.md")
SITE = ("site: [Rendered page](https://docs.kub.internal/homelab/develop/"
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
        self.assertNotIn("docs.kub.internal", text)

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
    """A board that answers the bucket list and the paged, filtered task list.

    ``columns`` is ``{bucket_id: (title, cards)}``. The stub pages and filters
    the way the server does; ``ignore_filter`` mimics a server that drops the
    filter and answers every page with the first one.
    """

    def __init__(self, columns, ignore_filter=False):
        self.columns = columns
        self.ignore_filter = ignore_filter
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if path.endswith("/buckets"):
            return {"items": [{"id": bucket_id, "title": title}
                              for bucket_id, (title, _) in self.columns.items()]}
        query = parse_qs(urlsplit(path).query)
        bucket_id = int(query["filter"][0].rsplit("=", 1)[1])
        page = int(query["page"][0])
        per_page = int(query["per_page"][0])
        cards = [card for _, found in self.columns.values() for card in found] \
            if self.ignore_filter else self.columns[bucket_id][1]
        if self.ignore_filter:
            page = 1
        return {"items": cards[(page - 1) * per_page:page * per_page],
                "total": len(cards)}

    def task_calls(self):
        return [path for _, path in self.calls if "/tasks?" in path]


def _card(card_id):
    return {"id": card_id, "title": f"Card {card_id}", "description": ""}


class OpenCardsTests(unittest.TestCase):
    def test_only_the_open_columns_are_returned(self):
        board = StubBoard({
            10: ("Idea", [_card(1)]),
            97: ("Shaping", [_card(2)]),
            13: ("Backlog", [_card(3), _card(4)]),
            11: ("Doing", [_card(5)]),
            14: ("Validation", [_card(6)]),
            12: ("Done", [_card(7)]),
        })
        cards = b.open_cards(board, 2, 13)
        self.assertEqual([card["id"] for card in cards], [3, 4, 5, 6])
        self.assertEqual(board.calls[0][0], "GET")
        self.assertTrue(board.calls[0][1].endswith("/buckets"))
        # Idea, Shaping and Done are never asked for.
        self.assertEqual(len(board.task_calls()), 3)
        self.assertIn("filter=bucket_id+%3D+13", board.task_calls()[0])

    def test_a_column_with_no_cards_is_empty_not_an_error(self):
        board = StubBoard({11: ("Doing", []), 13: ("Backlog", [_card(3)])})
        self.assertEqual([card["id"] for card in b.open_cards(board, 2, 13)], [3])

    def test_a_column_over_the_page_cap_is_read_whole(self):
        board = StubBoard({13: ("Backlog", [_card(i) for i in range(173)])})
        cards = b.open_cards(board, 2, 13)
        self.assertEqual(len(cards), 173)
        self.assertEqual([card["id"] for card in cards], list(range(173)))
        self.assertEqual(len(board.task_calls()), 2)
        self.assertIn("page=2", board.task_calls()[1])

    def test_a_column_exactly_at_the_cap_takes_one_request(self):
        board = StubBoard({13: ("Backlog", [_card(i) for i in range(100)])})
        self.assertEqual(len(b.open_cards(board, 2, 13)), 100)
        self.assertEqual(len(board.task_calls()), 1)

    def test_a_server_that_ignores_the_filter_stops_the_run(self):
        board = StubBoard({13: ("Backlog", [_card(i) for i in range(150)])},
                          ignore_filter=True)
        with self.assertRaises(SystemExit) as caught:
            b.open_cards(board, 2, 13)
        message = str(caught.exception)
        self.assertIn("Backlog", message)
        self.assertIn("filter", message)

    def test_the_same_card_in_two_columns_aborts_the_run(self):
        board = StubBoard({13: ("Backlog", [_card(3)]), 11: ("Doing", [_card(3)])})
        with self.assertRaises(SystemExit):
            b.open_cards(board, 2, 13)


if __name__ == "__main__":
    unittest.main()
