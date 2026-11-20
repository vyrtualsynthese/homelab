import pathlib
import sys
import tempfile
import unittest

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import check_app_version  # noqa: E402


def chart(chart_yaml, values_yaml=None, name="app"):
    """A throwaway chart directory, to check one rule at a time."""
    directory = pathlib.Path(tempfile.mkdtemp()) / name
    directory.mkdir()
    (directory / "Chart.yaml").write_text(chart_yaml)
    if values_yaml is not None:
        (directory / "values.yaml").write_text(values_yaml)
    return directory


CHART = 'apiVersion: v2\nname: app\nversion: 0.1.0\nappVersion: "{app}"\n'
VALUES = "image:\n  repository: example/app\n  tag: {tag}\n"


class VersionTests(unittest.TestCase):
    def test_the_v_prefix_is_packaging(self):
        self.assertEqual(check_app_version.version_of("v2.1.7-ls237"), "2.1.7")

    def test_the_distroless_suffix_is_packaging(self):
        self.assertEqual(check_app_version.version_of("13.3.1-distroless"), "13.3.1")

    def test_a_second_component_in_the_tag_is_packaging(self):
        self.assertEqual(check_app_version.version_of("5.3.0_v2.1.2-ls487"), "5.3.0")

    def test_a_hash_in_the_tag_is_packaging(self):
        self.assertEqual(check_app_version.version_of("1.43.5.11079-b3b13e506"), "1.43.5.11079")

    def test_a_stable_suffix_is_packaging(self):
        self.assertEqual(check_app_version.version_of("2.0.2-stable"), "2.0.2")

    def test_latest_carries_no_version(self):
        self.assertIsNone(check_app_version.version_of("latest"))


class CheckTests(unittest.TestCase):
    def test_a_matching_chart_passes(self):
        found = check_app_version.check_chart(chart(CHART.format(app="2.1.7"), VALUES.format(tag="v2.1.7-ls237")))
        self.assertEqual(found, [])

    def test_a_stale_appversion_is_reported_with_both_numbers(self):
        found = check_app_version.check_chart(chart(CHART.format(app="1.0.0"), VALUES.format(tag="v2.1.7-ls237")))
        self.assertEqual(len(found), 1)
        self.assertIn("appVersion 1.0.0", found[0])
        self.assertIn("2.1.7", found[0])

    def test_a_missing_appversion_names_the_version_to_add(self):
        found = check_app_version.check_chart(chart(CHART.format(app="").replace("appVersion: \"\"\n", ""),
                                                   VALUES.format(tag="v2.5.0")))
        self.assertEqual(len(found), 1)
        self.assertIn('appVersion: "2.5.0"', found[0])

    def test_a_latest_tag_needs_an_exemption(self):
        found = check_app_version.check_chart(chart(CHART.format(app="1.16.0"), VALUES.format(tag="latest")))
        self.assertEqual(len(found), 1)
        self.assertIn("appVersion-check: exempt", found[0])

    def test_a_quoted_tag_with_a_comment_is_read(self):
        values = 'image:\n  tag: "2.8.0"  # the MCP endpoint needs >= 2.7.0\n'
        self.assertEqual(check_app_version.check_chart(chart(CHART.format(app="2.8.0"), values)), [])

    def test_the_first_tag_is_the_app_image(self):
        values = "image:\n  tag: 1.2.3\nexporter:\n  image:\n    tag: 9.9.9\n"
        self.assertEqual(check_app_version.check_chart(chart(CHART.format(app="1.2.3"), values)), [])
        found = check_app_version.check_chart(chart(CHART.format(app="9.9.9"), values))
        self.assertEqual(len(found), 1)
        self.assertIn("1.2.3", found[0])

    def test_a_chart_with_no_image_tag_must_say_why(self):
        found = check_app_version.check_chart(chart(CHART.format(app="2.9.4"), "nowhere: true\n"))
        self.assertEqual(len(found), 1)
        self.assertIn("appVersion-check: exempt", found[0])

    def test_an_exemption_with_a_reason_passes(self):
        chart_yaml = CHART.format(app="2.9.4") + "  # appVersion-check: exempt — names the Traefik release\n"
        self.assertEqual(check_app_version.check_chart(chart(chart_yaml, "nowhere: true\n")), [])

    def test_an_exemption_without_a_reason_does_not_count(self):
        chart_yaml = CHART.format(app="2.9.4") + "# appVersion-check: exempt\n"
        found = check_app_version.check_chart(chart(chart_yaml, "nowhere: true\n"))
        self.assertEqual(len(found), 1)

    def test_a_chart_without_values_yaml_is_reported(self):
        found = check_app_version.check_chart(chart(CHART.format(app="1.0.0")))
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
