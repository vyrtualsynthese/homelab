import pathlib
import sys
import unittest

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import vikunja_migrate as m  # noqa: E402


class TitleTests(unittest.TestCase):
    def test_strips_only_the_plans_own_prefix(self):
        self.assertEqual(
            m.clean_title("sonarr-chart-refactor: Rebuild the chart", "sonarr-chart-refactor"),
            "Rebuild the chart")

    def test_leaves_a_colon_that_is_not_the_prefix(self):
        self.assertEqual(
            m.clean_title("Rebuild `radarr`: the chart", "radarr-chart-refactor"),
            "Rebuild `radarr`: the chart")

    def test_capitalises_the_first_letter(self):
        self.assertEqual(m.clean_title("w0-cleanup: inventory it", "w0-cleanup"), "Inventory it")


class SiteUrlTests(unittest.TestCase):
    def test_a_plan(self):
        self.assertEqual(m.site_url("docs/superpowers/plans/x.md"),
                         "https://docs.kub.local/homelab/develop/superpowers/plans/x/")

    def test_a_page(self):
        self.assertEqual(m.site_url("docs/agent-protocol.md"),
                         "https://docs.kub.local/homelab/develop/agent-protocol/")

    def test_a_non_docs_path_has_no_page(self):
        self.assertIsNone(m.site_url("cluster/helm/sonarr"))


class DescriptionTests(unittest.TestCase):
    def test_block_with_no_pr(self):
        self.assertEqual(
            m.render_description("docs/x.md", "none", "none", ""),
            "repo: [vyrtualsynthese/homelab](https://github.com/vyrtualsynthese/homelab)\n"
            "docs: [X](https://github.com/vyrtualsynthese/homelab/blob/develop/docs/x.md)\n"
            "site: [Rendered page](https://docs.kub.local/homelab/develop/x/)\n"
            "branch: none\npr: none")

    def test_body_is_kept_under_its_heading(self):
        got = m.render_description("docs/x.md", "feature/a", "#7", "What changed\n- one")
        self.assertTrue(got.startswith(
            "repo: [vyrtualsynthese/homelab](https://github.com/vyrtualsynthese/homelab)\n"))
        self.assertTrue(got.endswith("branch: feature/a\npr: #7\n\nWhat changed\n- one"))

    def test_commit_stands_in_when_there_is_no_pull_request(self):
        got = m.render_description("docs/x.md", "none", "none", "", commit="0c83219c")
        self.assertTrue(got.endswith(
            "branch: none\ncommit: [0c83219c]"
            "(https://github.com/vyrtualsynthese/homelab/commit/0c83219c)"))
        self.assertNotIn("pr:", got)

    def test_the_site_line_sits_directly_after_docs(self):
        text = m.render_description("docs/status.md", "none", "none", "")
        self.assertEqual(text.splitlines()[:3], [
            "repo: [vyrtualsynthese/homelab](https://github.com/vyrtualsynthese/homelab)",
            "docs: [Status](https://github.com/vyrtualsynthese/homelab/blob/develop/docs/status.md)",
            "site: [Rendered page](https://docs.kub.local/homelab/develop/status/)",
        ])

    def test_a_plan_is_named_not_pathed(self):
        plan = "docs/superpowers/plans/2026-11-17-docs-for-the-operator.md"
        text = m.render_description(plan, "none", "none", "")
        self.assertEqual(
            text.splitlines()[1],
            f"docs: [Plan — Docs for the operator](https://github.com/{m.REPO}/blob/develop/{plan})")

    def test_no_docs_means_no_site_line(self):
        text = m.render_description("none", "none", "none", "")
        self.assertEqual(text.splitlines()[:2], [
            "repo: [vyrtualsynthese/homelab](https://github.com/vyrtualsynthese/homelab)",
            "docs: none",
        ])
        self.assertNotIn("site:", text)

    def test_a_page_that_is_not_on_develop_says_none(self):
        text = m.render_description("docs/x.md", "feature/a", "none", "", on_develop=False)
        self.assertEqual(text.splitlines()[2], "site: none")


class ValidateTests(unittest.TestCase):
    def _map(self, tasks, features=None):
        return {"features": features if features is not None else [{"key": "a", "title": "A"}],
                "tasks": tasks, "backfill": []}

    def test_unknown_feature_key_is_reported(self):
        problems = m.validate(self._map([{"id": 1, "feature": "nope", "kind": "task",
                                          "column": "Backlog", "title": "T"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("nope", problems[0])

    def test_task_column_outside_the_six_is_reported(self):
        problems = m.validate(self._map([{"id": 1, "feature": "a", "kind": "task",
                                          "column": "Ready", "title": "T"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("Ready", problems[0])

    def test_container_in_doing_is_reported(self):
        problems = m.validate(self._map([{"id": 1, "feature": "a", "kind": "feature",
                                          "column": "Doing", "title": "F"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("Doing", problems[0])

    def test_unknown_kind_is_reported(self):
        problems = m.validate(self._map([{"id": 1, "feature": "a", "kind": "epic",
                                          "column": "Backlog", "title": "T"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("epic", problems[0])

    def test_a_good_map_has_no_problems(self):
        self.assertEqual(m.validate(self._map([{"id": 1, "feature": "a", "kind": "task",
                                                "column": "Backlog", "title": "T"}])), [])


class PlanTests(unittest.TestCase):
    def _map(self):
        return {
            "features": [{"key": "a", "title": "A", "position": 100, "docs": "d", "why": "w"}],
            "tasks": [{"id": 1, "title": "T", "feature": "a", "kind": "task",
                       "column": "Backlog", "docs": "d", "branch": "none",
                       "pr": "none", "commit": "none", "body": ""}],
            "backfill": [],
        }

    def test_a_matching_board_produces_no_actions(self):
        state = {"features": {"A": 9},
                 "tasks": {1: {"title": "T", "bucket": "Backlog", "labels": ["task"],
                               "parent": 9, "description": None}},
                 "labels_by_id": {9: ["feature"]}}
        self.assertEqual(m.plan_actions(self._map(), state, description_ok=True), [])

    def test_a_feature_card_without_its_label_is_relabelled_not_recreated(self):
        state = {"features": {"A": 9}, "labels_by_id": {9: []},
                 "tasks": {1: {"title": "T", "bucket": "Backlog", "labels": ["task"],
                               "parent": 9}}}
        ops = m.plan_actions(self._map(), state, description_ok=True)
        self.assertEqual([o["op"] for o in ops], ["ensure_label"])

    def test_a_card_with_extra_labels_is_not_relabelled_again(self):
        state = {"features": {"A": 9}, "labels_by_id": {9: ["feature"]},
                 "tasks": {1: {"title": "T", "bucket": "Backlog",
                               "labels": ["task", "agent", "implementer"], "parent": 9}}}
        self.assertEqual(m.plan_actions(self._map(), state, description_ok=True), [])

    def test_a_title_that_still_carries_its_plan_prefix_is_stripped(self):
        doc = self._map()
        doc["tasks"][0]["title"] = "a: T"
        state = {"features": {"A": 9}, "labels_by_id": {9: ["feature"]},
                 "tasks": {1: {"title": "T", "bucket": "Backlog", "labels": ["task"],
                               "parent": 9}}}
        self.assertEqual(m.plan_actions(doc, state, description_ok=True), [])

    def test_a_missing_feature_is_created_and_a_missing_ticket_is_reported(self):
        state = {"features": {}, "tasks": {}}
        ops = m.plan_actions(self._map(), state)
        self.assertEqual([o["op"] for o in ops], ["create_feature", "missing_task"])

    def test_a_renamed_task_is_updated(self):
        state = {"features": {"A": 9},
                 "tasks": {1: {"title": "Old", "bucket": "Backlog", "labels": ["task"],
                               "parent": 9}}}
        ops = m.plan_actions(self._map(), state, description_ok=True)
        self.assertIn("update_task", [o["op"] for o in ops])


if __name__ == "__main__":
    unittest.main()
