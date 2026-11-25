"""The change script's contract: the undo it renders, and the runs it drives."""

import os
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ansible" / "switches" / "switch-change.sh"
FIXTURE = ROOT / "hack" / "fixtures" / "switch-change"
EXPORTS = FIXTURE / "exports"
CHANGES = FIXTURE / "changes"


def run(args=(), env=None, stdin=None):
    merged = {k: v for k, v in os.environ.items() if not k.startswith("SWITCH_CHANGE_")}
    merged.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin, text=True, capture_output=True, env=merged,
    )


class UsageTests(unittest.TestCase):
    def test_the_script_parses(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)

    def test_help_prints_comment_lines_only(self):
        done = run(["--help"])
        self.assertEqual(done.returncode, 0)
        for line in done.stdout.splitlines():
            self.assertTrue(line.startswith("#"), line)

    def test_an_unknown_flag_is_a_usage_error(self):
        self.assertEqual(run(["--nope"]).returncode, 2)

    def test_a_malformed_target_list_prints_no_partial_set(self):
        done = run(["--list-targets"], env={"SWITCH_CHANGE_TARGETS": "crs326=192.168.1.11 broken"})
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")

    def test_list_targets_prints_the_defaults(self):
        done = run(["--list-targets"])
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.split(),
                         ["crs326", "192.168.1.11", "crs309", "192.168.1.43"])


class RendererV7Tests(unittest.TestCase):
    def render(self, change, export="v7-plain.rsc", verbose="v7-verbose.rsc"):
        done = run(["--render-undo", "--major", "7",
                    "--change", str(CHANGES / change),
                    "--export", str(EXPORTS / export),
                    "--verbose-export", str(EXPORTS / verbose)])
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_an_added_object_is_removed_under_a_guard(self):
        self.assertEqual(
            self.render("add-address.rsc").strip(),
            ':if ([:len [/ip address find address="192.168.1.99/24"]] > 0) '
            'do={ /ip address remove [find address="192.168.1.99/24"] }')

    def test_an_absolute_change_line_is_read_too(self):
        self.assertIn('find address="192.168.1.98/24"', self.render("absolute-add.rsc"))

    def test_a_set_restores_the_verbose_value_not_the_plain_one(self):
        self.assertIn(
            ':if ([:len [/interface bridge port find interface="sfp-sfpplus2"]] > 0) '
            'do={ /interface bridge port set [find interface="sfp-sfpplus2"] pvid=1 }',
            self.render("set-pvid.rsc"))

    def test_a_removed_object_is_added_back_with_its_wrapped_line_joined(self):
        self.assertIn(
            ':if ([:len [/ip route find dst-address="0.0.0.0/0"]] = 0) '
            'do={ /ip route add dst-address=0.0.0.0/0 gateway=192.168.1.1 check-gateway=ping }',
            self.render("remove-route.rsc"))

    def test_a_disabled_object_is_enabled_again(self):
        self.assertIn(
            ':if ([:len [/interface bridge port find interface="sfp-sfpplus1"]] > 0) '
            'do={ /interface bridge port enable [find interface="sfp-sfpplus1"] }',
            self.render("disable-port.rsc"))

    def test_an_add_keyed_by_comment_uses_the_comment(self):
        self.assertIn('/ip route find comment=uplink', self.render("comment-add.rsc"))

    def test_a_quoted_value_survives_byte_for_byte(self):
        self.assertIn('comment="a \\"b\\" c=d"', self.render("quoted-value.rsc"))


class RefusalTests(unittest.TestCase):
    def refused(self, change):
        done = run(["--render-undo", "--major", "7",
                    "--change", str(CHANGES / change),
                    "--export", str(EXPORTS / "v7-plain.rsc"),
                    "--verbose-export", str(EXPORTS / "v7-verbose.rsc")])
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stdout, "")
        return done.stderr

    def test_an_add_with_no_key_names_its_line(self):
        self.assertIn("line 2", self.refused("refuse-add-unkeyed.rsc"))

    def test_a_selector_matching_two_objects_is_refused(self):
        self.assertIn("matches 2 objects", self.refused("refuse-multi-match.rsc"))

    def test_a_move_is_refused(self):
        self.assertIn("move", self.refused("refuse-move.rsc"))

    def test_a_set_with_no_old_value_in_either_export_is_refused(self):
        self.assertIn("line 2", self.refused("refuse-set-unknown-attr.rsc"))

    def test_a_change_to_the_scheduler_is_refused(self):
        self.assertIn("/system scheduler", self.refused("refuse-scheduler.rsc"))

    def test_an_import_inside_a_change_is_refused(self):
        self.assertIn("/import", self.refused("refuse-import.rsc"))


class NoiseFreeTests(unittest.TestCase):
    def test_comments_and_blank_lines_go_and_every_other_line_stays(self):
        text = ("# 2026-11-25 09:14:02 by RouterOS 7.16.1\n\n/ip route\n"
                "add dst-address=0.0.0.0/0 gateway=1.1.1.1\n")
        done = run(["--noise-free"], stdin=text)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout,
                         "/ip route\nadd dst-address=0.0.0.0/0 gateway=1.1.1.1\n")


if __name__ == "__main__":
    unittest.main()
