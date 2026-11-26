"""The change script's contract: the undo it renders, and the runs it drives."""

import contextlib
import json
import os
import pathlib
import re
import signal
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ansible" / "switches" / "switch-change.sh"
FIXTURE = ROOT / "hack" / "fixtures" / "switch-change"
EXPORTS = FIXTURE / "exports"
CHANGES = FIXTURE / "changes"

# The same switch after `set-pvid.rsc` landed: the port now carries pvid=20.
CHANGED = (EXPORTS / "v7-plain.rsc").read_text().replace(
    "interface=sfp-sfpplus2 comment=", "interface=sfp-sfpplus2 pvid=20 comment=")


def run(args=(), env=None, stdin=None):
    merged = {k: v for k, v in os.environ.items() if not k.startswith("SWITCH_CHANGE_")}
    merged.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin, text=True, capture_output=True, env=merged,
    )


FAKE = FIXTURE / "fake"


class Fake:
    """A switch that answers the commands the script sends, and nothing a switch would not."""

    def __init__(self, root, **kw):
        self.root = root
        self.state = root / "state"
        self.state.mkdir(parents=True)
        self.bin = root / "bin"
        self.bin.mkdir()
        for name in ("ssh", "scp"):
            link = self.bin / name
            link.symlink_to(FAKE / name)
        (self.state / "key").write_text("")
        self.env = {
            "FAKE_STATE": str(self.state),
            "FAKE_NOW": kw.get("now", "2026-11-25 09:20:00"),
            "FAKE_STEP": str(kw.get("step", 10)),
            "FAKE_VERSION": kw.get("version", "7.16.1"),
        }
        self._write("clock", self.env["FAKE_NOW"] + "\n")
        self._write("config", kw.get("config", (EXPORTS / "v7-plain.rsc").read_text()))
        self._write("config-verbose", kw.get("verbose", (EXPORTS / "v7-verbose.rsc").read_text()))
        self._write("config-original",
                    kw.get("config", (EXPORTS / "v7-plain.rsc").read_text()))
        for src, name in (("after_change", "after_change"), ("after_undo", "after_undo"),
                          ("import_error", "import_error")):
            if kw.get(src) is not None:
                self._write(name, kw[src])
        if kw.get("break_after_change"):
            self._write("break_after_change", "1")
        if kw.get("backup_denied"):
            self._write("backup_denied", "1")
        if kw.get("fire_on_arm"):
            self._write("fire_on_arm", str(kw["fire_on_arm"]))
        if kw.get("stays_down"):
            self._write("stays_down", "1")
        if kw.get("hide_timer"):
            self._write("hide_timer", "1")
        if kw.get("hide_run_count_after_change"):
            self._write("hide_run_count_after_change", "1")
        if kw.get("stubborn_timer"):
            self._write("stubborn_timer", "1")
        if kw.get("stubborn_file"):
            self._write("stubborn_file", "1")
        if kw.get("refuse_upload"):
            self._write("refuse_upload", str(kw["refuse_upload"]))
        if kw.get("clock_output"):
            self._write("clock-output", kw["clock_output"])

    def _write(self, name, text):
        (self.state / name).write_text(text)

    def _env(self, extra=None):
        env = dict(os.environ)
        env.update(self.env)
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        env.update(extra or {})
        return env

    def ros(self, command):
        done = subprocess.run(
            ["ssh", "-i", str(self.state / "key"), "-o", "BatchMode=yes",
             "deploy@192.168.1.11", command],
            text=True, capture_output=True, env=self._env({"FAKE_IGNORE_BREAK": "1"}),
        )
        return done.stdout if done.returncode == 0 else ""

    def script(self, args, stdin=None):
        env = self._env({
            "SWITCH_CHANGE_TARGETS": "crs326=192.168.1.11",
            "SWITCH_CHANGE_USER": "deploy",
            "SWITCH_CHANGE_KEY": str(self.state / "key"),
            "SWITCH_CHANGE_POLL": "0",
            "SWITCH_CHANGE_WORKDIR": str(self.root / "work"),
            "SWITCH_CHANGE_CONNECT_TIMEOUT": "1",
            "SWITCH_CHANGE_CMD_TIMEOUT": "5",
        })
        kwargs = {"stdin": stdin} if isinstance(stdin, int) else {"input": stdin}
        return subprocess.run(["bash", str(SCRIPT), *args], text=True,
                              capture_output=True, env=env, **kwargs)

    def spawn(self, args):
        env = self._env({
            "SWITCH_CHANGE_TARGETS": "crs326=192.168.1.11",
            "SWITCH_CHANGE_USER": "deploy",
            "SWITCH_CHANGE_KEY": str(self.state / "key"),
            "SWITCH_CHANGE_POLL": "0",
            "SWITCH_CHANGE_WORKDIR": str(self.root / "work"),
            "SWITCH_CHANGE_CONNECT_TIMEOUT": "1",
            "SWITCH_CHANGE_CMD_TIMEOUT": "5",
        })
        return subprocess.Popen(["bash", str(SCRIPT), *args], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    def wait_for(self, text, proc):
        seen = ""
        while True:
            line = proc.stdout.readline()
            if line == "":
                raise AssertionError(f"the run ended without printing {text!r}: {seen!r}")
            seen += line
            if text in line:
                return seen

    def files(self):
        d = self.state / "files"
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def arm(self, name, start_time, on_event=None):
        (self.state / "scheduler.json").write_text(json.dumps({
            name: {"start-time": start_time, "on-event": on_event or "/import file=undo-old.rsc",
                   "run-count": 0}}))

    def calls(self):
        p = self.state / "calls"
        return int(p.read_text()) if p.exists() else 0

    def commands(self):
        p = self.state / "commands"
        return p.read_text().splitlines() if p.exists() else []

    def argv(self, tool):
        """Every argv the fake `ssh` or `scp` was called with, one line per call."""
        p = self.state / f"{tool}-argv"
        return p.read_text().splitlines() if p.exists() else []

    def fire_on_arm(self, value="1"):
        """Have the fake run the entry as soon as it is added, before the first poll."""
        self._write("fire_on_arm", str(value))


@contextlib.contextmanager
def fake(tag, **kw):
    with tempfile.TemporaryDirectory(prefix=f"switch-change-{tag}-") as tmp:
        yield Fake(pathlib.Path(tmp), **kw)


class FakeDeviceTests(unittest.TestCase):
    def test_the_fake_answers_a_routeros_read_and_an_export(self):
        with fake("read") as f:
            self.assertIn("version: 7.16.1", f.ros("/system resource print"))
            self.assertEqual(f.ros("/export").strip(),
                             (EXPORTS / "v7-plain.rsc").read_text().strip())


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

    def test_a_missing_option_value_is_a_usage_error(self):
        done = run(["--target"])
        self.assertEqual(done.returncode, 2)
        self.assertIn("--target needs a value", done.stderr)

    def test_a_window_outside_the_ceiling_and_the_floor_is_refused(self):
        for window in ("30s", "2h", "ten-minutes"):
            done = run(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                        "--dry-run", "--yes", "--window", window])
            self.assertEqual(done.returncode, 2, (window, done.stderr))

    def test_a_window_that_is_not_a_number_is_refused_before_any_arithmetic(self):
        # The gate passes --window through, so the text is the caller's. Digits are checked before it
        # reaches `$(( ))`, and each of these would otherwise be a shell syntax error at best.
        for window in ("1;2m", "0x10m", "$(x)m", "m", "s"):
            done = run(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                        "--dry-run", "--yes", "--window", window])
            self.assertEqual(done.returncode, 2, (window, done.stderr))
            self.assertIn("is not a duration", done.stderr, window)

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

    def test_an_enable_of_an_enabled_object_is_undone_by_enabling_it(self):
        self.assertIn(
            ':if ([:len [/interface bridge port find interface="sfp-sfpplus1"]] > 0) '
            'do={ /interface bridge port enable [find interface="sfp-sfpplus1"] }',
            self.render("enable-port.rsc"))

    def test_an_enable_of_a_disabled_object_is_undone_by_disabling_it(self):
        self.assertIn(
            ':if ([:len [/interface bridge port find interface="sfp-sfpplus1"]] > 0) '
            'do={ /interface bridge port disable [find interface="sfp-sfpplus1"] }',
            self.render("enable-disabled-port.rsc", export="v7-plain-disabled.rsc",
                        verbose="v7-verbose-disabled.rsc"))

    def test_an_add_keyed_by_comment_uses_the_comment(self):
        self.assertIn('/ip route find comment=uplink', self.render("comment-add.rsc"))

    def test_a_quoted_value_survives_byte_for_byte(self):
        self.assertIn('comment="a \\"b\\" c=d"', self.render("quoted-value.rsc"))

    def test_a_set_whose_selector_uses_another_attribute_still_renders(self):
        out = self.render("quoted-value.rsc")
        self.assertIn('find interface="sfp-sfpplus2"', out)
        self.assertIn("comment=", out)


class ServiceRowTests(unittest.TestCase):
    """A menu RouterOS exports in short form, and the object that sits at its defaults.

    `/ip service` is exported as `set ssh disabled=no`, not `set [find name=ssh] ...`, and a service
    left at its defaults is absent from the plain export and listed only in the verbose one. Both are
    what the SSH change in the runbook's live proof meets.
    """

    CHANGE = '/ip service\nset [ find name="ssh" ] disabled=yes\n'
    FLAG_CHANGE = '/ip service\ndisable [ find name="ssh" ]\n'
    PLAIN_ROW = "/ip service\nset ssh disabled=yes\n"
    DEFAULT_ROW = "/ip service\nset ssh port=22 disabled=no\n"
    DNS_CHANGE = '/ip dns\nset [ find servers=192.168.1.1 ] servers=8.8.8.8\n'
    DNS_ROW = "/ip dns\nset servers=192.168.1.1\n"

    def render(self, change, plain, verbose):
        with tempfile.TemporaryDirectory(prefix="switch-change-service-") as tmp:
            d = pathlib.Path(tmp)
            (d / "change.rsc").write_text(change)
            (d / "plain.rsc").write_text(plain)
            (d / "verbose.rsc").write_text(verbose)
            return run(["--render-undo", "--major", "7",
                        "--change", str(d / "change.rsc"),
                        "--export", str(d / "plain.rsc"),
                        "--verbose-export", str(d / "verbose.rsc")])

    def rendered(self, change, plain, verbose):
        done = self.render(change, plain, verbose)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def plain(self):
        return (EXPORTS / "v7-plain.rsc").read_text()

    def verbose(self):
        return (EXPORTS / "v7-verbose.rsc").read_text()

    def test_an_object_at_its_defaults_is_read_from_the_verbose_export(self):
        self.assertEqual(
            self.rendered(self.CHANGE, self.plain(), self.verbose() + self.DEFAULT_ROW).strip(),
            ':if ([:len [/ip service find name="ssh"]] > 0) '
            'do={ /ip service set [find name="ssh"] disabled=no }')

    def test_a_short_form_row_in_the_plain_export_is_the_object(self):
        self.assertEqual(
            self.rendered(self.CHANGE, self.plain() + self.PLAIN_ROW,
                          self.verbose() + self.DEFAULT_ROW).strip(),
            ':if ([:len [/ip service find name="ssh"]] > 0) '
            'do={ /ip service set [find name="ssh"] disabled=no }')

    def test_a_short_form_row_on_a_menu_not_written_that_way_is_not_an_object(self):
        done = self.render(self.DNS_CHANGE, self.plain() + self.DNS_ROW, self.verbose())
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stdout, "")
        self.assertIn("no object matching", done.stderr)
        self.assertIn("plain", done.stderr)
        self.assertIn("verbose", done.stderr)

    def test_a_flag_change_on_an_object_at_its_defaults_renders_the_enable(self):
        self.assertEqual(
            self.rendered(self.FLAG_CHANGE, self.plain(), self.verbose() + self.DEFAULT_ROW).strip(),
            ':if ([:len [/ip service find name="ssh"]] > 0) '
            'do={ /ip service enable [find name="ssh"] }')


class SelectorKeyTests(unittest.TestCase):
    """A selector key as RouterOS reads it, not as the line spells it.

    `find !disabled`, `find ?comment` and `find disabled` all match on the property named after the
    prefix, so a change that writes that property is refused the same way a `pvid=10` selector is.
    The two-row exports are the point: a renderer that ignores the prefix matches both rows.
    """

    TWO_ROWS = ("/ip service\n"
                "set ssh port=22 disabled=no\n"
                "set www port=80 disabled=yes\n")
    COMMENTED = ("/ip service\n"
                 'set ssh port=22 comment="the door"\n'
                 "set www port=80\n")

    def render(self, change, plain=None, verbose=None, major="7"):
        with tempfile.TemporaryDirectory(prefix="switch-change-selector-") as tmp:
            d = pathlib.Path(tmp)
            (d / "change.rsc").write_text(change)
            (d / "plain.rsc").write_text(plain if plain is not None else self.TWO_ROWS)
            (d / "verbose.rsc").write_text(verbose if verbose is not None else
                                           (plain if plain is not None else self.TWO_ROWS))
            return run(["--render-undo", "--major", major,
                        "--change", str(d / "change.rsc"),
                        "--export", str(d / "plain.rsc"),
                        "--verbose-export", str(d / "verbose.rsc")])

    def rendered(self, change, plain=None, verbose=None, major="7"):
        done = self.render(change, plain, verbose, major)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def refused(self, change, plain=None, verbose=None, major="7"):
        done = self.render(change, plain, verbose, major)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stdout, "")
        return done.stderr

    def test_a_negated_flag_selector_that_the_change_flips_is_refused(self):
        err = self.refused("/ip service\nset [ find !disabled ] disabled=yes\n")
        self.assertIn("disabled", err)
        self.assertIn("its own selector matches on", err)

    def test_a_questioned_selector_that_the_change_flips_is_refused(self):
        err = self.refused("/ip service\nset [ find ?comment ] comment=gone\n",
                           plain=self.COMMENTED)
        self.assertIn("comment", err)
        self.assertIn("its own selector matches on", err)

    def test_a_bare_flag_selector_that_the_change_flips_is_refused(self):
        err = self.refused("/ip service\nset [ find disabled ] disabled=no\n")
        self.assertIn("disabled", err)
        self.assertIn("its own selector matches on", err)

    def test_an_inequality_selector_that_the_change_flips_is_refused(self):
        err = self.refused("/ip service\nset [ find port!=22 ] port=2222\n")
        self.assertIn("port", err)
        self.assertIn("its own selector matches on", err)

    def test_a_negated_flag_selector_the_change_leaves_alone_still_renders(self):
        self.assertIn(
            ':if ([:len [/ip service find !disabled]] > 0) '
            'do={ /ip service set [find !disabled] port=22 }',
            self.rendered("/ip service\nset [ find !disabled ] port=2222\n"))

    def test_a_questioned_selector_the_change_leaves_alone_still_renders(self):
        self.assertIn(
            ':if ([:len [/ip service find ?comment]] > 0) '
            'do={ /ip service set [find ?comment] port=22 }',
            self.rendered("/ip service\nset [ find ?comment ] port=2222\n",
                          plain=self.COMMENTED))

    def test_a_bare_flag_selector_the_change_leaves_alone_still_renders(self):
        self.assertIn(
            ':if ([:len [/ip service find disabled]] > 0) '
            'do={ /ip service set [find disabled] port=80 }',
            self.rendered("/ip service\nset [ find disabled ] port=8080\n"))

    def test_an_inequality_selector_the_change_leaves_alone_still_renders(self):
        self.assertIn(
            ':if ([:len [/ip service find port!="22"]] > 0) '
            'do={ /ip service set [find port!="22"] disabled=yes }',
            self.rendered("/ip service\nset [ find port!=22 ] disabled=no\n"))

    def test_a_default_name_selector_with_a_name_write_is_not_refused(self):
        # `default-name` is the immutable identity and `name` is what the change writes, so the
        # selector survives the change: this one renders, and the two are never aliased.
        out = self.rendered(
            '/interface ethernet\nset [ find default-name="sfp-sfpplus1" ] name="sfp-sfpplus2"\n',
            plain=(EXPORTS / "v6-plain.rsc").read_text(),
            verbose=(EXPORTS / "v6-verbose.rsc").read_text(), major="6")
        self.assertIn('find default-name="sfp-sfpplus1"', out)
        self.assertIn("name=sfp-sfpplus1", out)

    # A flag verb writes `disabled` itself, and its guard is evaluated after that write: a selector
    # testing the flag can stop matching the object the verb just changed. Whether it does depends
    # on the direction, so the flag is simulated at the value the verb gives it and only a selector
    # that really stops matching is refused. ssh is enabled in TWO_ROWS, www disabled.
    def test_a_disable_whose_selector_flips_with_the_verb_is_refused(self):
        err = self.refused("/ip service\ndisable [ find !disabled ]\n")
        self.assertIn("disabled", err)
        self.assertIn("its own selector matches on", err)

    def test_an_enable_whose_selector_flips_with_the_verb_is_refused(self):
        err = self.refused("/ip service\nenable [ find disabled ]\n")
        self.assertIn("disabled", err)
        self.assertIn("its own selector matches on", err)

    def test_a_disable_whose_selector_survives_the_verb_still_renders(self):
        self.assertEqual(
            self.rendered("/ip service\ndisable [ find disabled ]\n").strip(),
            ':if ([:len [/ip service find disabled]] > 0) '
            'do={ /ip service disable [find disabled] }')

    def test_an_enable_whose_selector_survives_the_verb_still_renders(self):
        self.assertEqual(
            self.rendered("/ip service\nenable [ find !disabled ]\n").strip(),
            ':if ([:len [/ip service find !disabled]] > 0) '
            'do={ /ip service enable [find !disabled] }')


class RefusalTests(unittest.TestCase):
    def refused(self, change, export="v7-plain.rsc", verbose="v7-verbose.rsc"):
        done = run(["--render-undo", "--major", "7",
                    "--change", str(CHANGES / change),
                    "--export", str(EXPORTS / export),
                    "--verbose-export", str(EXPORTS / verbose)])
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

    def test_resetting_the_configuration_is_refused(self):
        self.assertIn("/system reset-configuration", self.refused("refuse-reset.rsc"))

    def test_a_move_written_absolutely_is_refused(self):
        self.assertIn("move", self.refused("refuse-absolute-move.rsc"))

    def test_a_change_with_no_invertible_line_arms_nothing(self):
        self.assertIn("no line", self.refused("refuse-header-only.rsc"))

    def test_an_add_whose_key_is_already_in_the_export_is_refused(self):
        self.assertIn("already holds", self.refused("refuse-add-collides.rsc"))

    def test_a_set_argument_without_a_value_is_refused(self):
        self.assertIn("disabled", self.refused("refuse-unset.rsc"))

    def test_a_value_holding_a_brace_is_refused(self):
        self.assertIn("brace", self.refused("refuse-brace.rsc"))

    def test_a_set_that_rewrites_its_own_selector_is_refused(self):
        err = self.refused("refuse-selector-rewrite.rsc")
        self.assertIn("line 2", err)
        self.assertIn("address", err)
        self.assertIn("its own selector matches on", err)

    def test_a_set_that_rewrites_one_of_two_selector_attributes_is_refused(self):
        err = self.refused("refuse-selector-partial.rsc")
        self.assertIn("line 2", err)
        self.assertIn("pvid", err)

    def test_a_change_to_the_files_is_refused(self):
        self.assertIn("/file", self.refused("refuse-file.rsc"))


class RendererV6Tests(unittest.TestCase):
    def render(self, change, major="6"):
        done = run(["--render-undo", "--major", major,
                    "--change", str(CHANGES / change),
                    "--export", str(EXPORTS / "v6-plain.rsc"),
                    "--verbose-export", str(EXPORTS / "v6-verbose.rsc")])
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_an_added_object_on_a_v6_device_is_removed_under_a_guard(self):
        self.assertIn(
            ':if ([:len [/ip address find address="192.168.1.44/24"]] > 0) '
            'do={ /ip address remove [find address="192.168.1.44/24"] }',
            self.render("add-address-v6.rsc"))

    def test_a_removed_v6_object_is_added_back_with_every_argument(self):
        self.assertIn(
            'do={ /interface bridge add admin-mac=AA:BB:CC:DD:EE:FF auto-mac=no name=bridge }',
            self.render("remove-bridge-v6.rsc"))

    def test_an_object_the_export_introduces_with_set_is_indexed(self):
        self.assertIn(
            'do={ /interface ethernet set [find default-name="sfp-sfpplus1"] mtu=1500 }',
            self.render("set-mtu-v6.rsc"))

    def test_an_export_whose_header_names_another_major_is_refused(self):
        done = run(["--render-undo", "--major", "7",
                    "--change", str(CHANGES / "add-address-v6.rsc"),
                    "--export", str(EXPORTS / "v6-plain.rsc"),
                    "--verbose-export", str(EXPORTS / "v6-verbose.rsc")])
        self.assertEqual(done.returncode, 1)
        self.assertIn("says RouterOS 6", done.stderr)


class ArmTests(unittest.TestCase):
    def test_a_dry_run_prints_the_undo_and_writes_nothing(self):
        with fake("dry") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--dry-run", "--yes"])
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("/interface bridge port set [find interface=\"sfp-sfpplus2\"] pvid=1",
                          done.stdout)
            self.assertEqual(f.ros("/system scheduler print detail"), "")
            self.assertEqual(f.files(), [])

    def test_arming_uploads_both_files_and_sets_the_start_time(self):
        with fake("arm") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes", "--window", "10m"])
            self.assertEqual(done.returncode, 0, done.stderr)
            detail = f.ros('/system scheduler print detail')
            self.assertIn('name="revert-', detail)
            self.assertIn("start-time=", detail)
            self.assertIn("run-count=0", detail)
            self.assertEqual(len([n for n in f.files() if n.startswith("undo-")]), 1)
            self.assertEqual(len([n for n in f.files() if n.startswith("change-")]), 1)

    def test_a_previous_runs_timer_is_named_and_left_alone_by_a_dry_run(self):
        with fake("stale-dry") as f:
            f.arm("revert-oldrun", "10:00:00")
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--dry-run", "--yes"])
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("revert-oldrun", done.stdout)
            self.assertIn('name="revert-oldrun"', f.ros("/system scheduler print detail"))
            self.assertEqual(f.files(), [])

    def test_an_armed_run_cancels_the_timer_an_earlier_run_left(self):
        with fake("stale-run") as f:
            f.arm("revert-oldrun", "10:00:00")
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes"])
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("cancelled the timer an earlier run left armed: revert-oldrun", done.stdout)
            self.assertNotIn("revert-oldrun", f.ros("/system scheduler print detail"))

    def test_the_timer_carries_the_device_date_and_the_window(self):
        with fake("window", now="2026-11-25 09:20:00", step="0") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes", "--window", "10m"])
            self.assertEqual(done.returncode, 0, done.stderr)
            detail = f.ros("/system scheduler print detail")
            self.assertIn("start-date=nov/25/2026", detail)
            self.assertIn("start-time=09:30:00", detail)
            self.assertIn("interval=0s", detail)
            self.assertIn("policy=read,write,test", detail)
            name = re.search(r'name="(revert-[^"]+)"', detail).group(1)
            self.assertIn(f'on-event="/import file=undo-{name[len("revert-"):]}.rsc"', detail)

    def test_an_upload_that_fails_removes_the_file_it_put_there(self):
        with fake("upload", refuse_upload="change-") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes"])
            self.assertEqual(done.returncode, 1)
            self.assertIn("change-", done.stderr)
            self.assertEqual([n for n in f.files() if n.startswith(("undo-", "change-"))], [])
            self.assertEqual(f.ros("/system scheduler print detail"), "")

    def test_a_timer_that_cannot_be_read_back_is_removed_again(self):
        with fake("readback", hide_timer="1") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes"])
            self.assertEqual(done.returncode, 1)
            self.assertIn("revert-", done.stderr)
            self.assertEqual([n for n in f.files() if n.startswith(("undo-", "change-"))], [])
            self.assertEqual(json.loads((f.state / "scheduler.json").read_text()), {})

    def test_a_window_across_midnight_is_refused_with_nothing_armed(self):
        with fake("midnight", now="2026-11-25 23:58:00") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes"])
            self.assertEqual(done.returncode, 1)
            self.assertIn("midnight", done.stderr)
            self.assertEqual(f.ros("/system scheduler print detail"), "")
            self.assertEqual(f.files(), [])

    def test_without_yes_and_without_a_terminal_it_refuses(self):
        with fake("tty") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only"], stdin=subprocess.DEVNULL)
            self.assertEqual(done.returncode, 2)
            self.assertEqual(f.ros("/system scheduler print detail"), "")

    def test_a_change_that_cannot_be_undone_touches_nothing(self):
        with fake("refuse") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "refuse-move.rsc"), "--yes"])
            self.assertEqual(done.returncode, 1, done.stderr)
            self.assertIn("move", done.stderr)
            self.assertEqual(f.ros("/system scheduler print detail"), "")
            self.assertEqual(f.files(), [])

    def test_a_window_is_read_in_minutes_seconds_or_plain_seconds(self):
        for window in ("10m", "600s", "600"):
            with fake(f"win-{window}") as f:
                done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                                 "--dry-run", "--yes", "--window", window])
                self.assertEqual(done.returncode, 0, (window, done.stderr))

    def test_a_refused_backup_still_arms(self):
        with fake("nobackup", backup_denied="1") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes"])
            self.assertEqual(done.returncode, 0)
            self.assertIn("no binary fallback", done.stdout)
            self.assertIn("run-count=0", f.ros("/system scheduler print detail"))


class RunTests(unittest.TestCase):
    def test_a_clean_change_cancels_the_timer_and_prints_the_diff(self):
        with fake("clean", after_change=CHANGED) as f:      # CHANGED = v7-plain with pvid=20
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"), "--yes"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("applied", done.stdout)
        self.assertIn("pvid=20", done.stdout)               # the diff, not the export
        self.assertEqual(f.ros("/system scheduler print detail"), "")
        self.assertEqual([n for n in f.files() if n.startswith(("undo-", "change-"))], [])

    def test_no_call_carries_a_signature_allowance(self):
        # The switches run RouterOS 7.24.7 and the gate's key is ed25519, which needs no signature
        # allowance. The `PubkeyAcceptedAlgorithms=+ssh-rsa` line existed for the v6 pair alone, and
        # card #806 removed it with the key.
        with fake("ssh-ed25519", after_change=CHANGED) as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"), "--yes"])
            calls = {tool: f.argv(tool) for tool in ("ssh", "scp")}
        self.assertEqual(done.returncode, 0, done.stderr)
        for tool in ("ssh", "scp"):
            self.assertTrue(calls[tool], f"the fake {tool} was never called")
            for line in calls[tool]:
                self.assertNotIn("PubkeyAcceptedAlgorithms", line, (tool, line))

    def test_a_file_that_survives_its_removal_is_named_instead_of_ignored(self):
        # The device is asked after the removal, and the answer is read from `print detail`: a plain
        # listing shortens a long name, so the leftover would be invisible.
        with fake("stubborn-file", after_change=CHANGED, stubborn_file="1") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"), "--yes"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("still on the switch", done.stderr)

    def test_the_upload_is_verified_against_the_whole_name(self):
        # Every name the script uses is longer than the nineteen characters a plain listing shows, so
        # the fake prints it shortened there, as a real switch does. A run that completes proves the
        # check reads the whole name.
        with fake("long-name", after_change=CHANGED) as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"), "--yes"])
            long_names = [n for n in f.commands() if "/file print" in n]
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(long_names, "the run never checked the uploaded files")
        for command in long_names:
            self.assertIn("/file print detail", command, command)

    def test_the_change_is_uploaded_with_its_path_on_every_line(self):
        # RouterOS runs a verbose import's lines in the root menu, so a file that sets its menu on a
        # line of its own fails at its first command there — and a plain import cannot report the
        # failure. The uploaded file therefore carries the path on each command line, and a line that
        # already carried one is left alone. Three shapes: header then command, header then a
        # bracket selector, and an absolute line.
        for change, expected in (
            ("set-pvid.rsc", '/interface bridge port set [ find interface="sfp-sfpplus2" ] pvid=20'),
            ("disable-port.rsc", '/interface bridge port disable [ find interface="sfp-sfpplus1" ]'),
            ("absolute-add.rsc", "/ip address add address=192.168.1.98/24 interface=bridge"),
        ):
            with self.subTest(change=change), fake(f"full-path-{change}") as f:
                # --arm-only leaves the two files on the device.
                done = f.script(["--target", "crs326", "--change", str(CHANGES / change),
                                 "--arm-only", "--yes", "--window", "10m"])
                name = [n for n in f.files() if n.startswith("change-")][0]
                text = (f.state / "files" / name).read_text()
            self.assertEqual(done.returncode, 0, done.stderr)
            body = [line for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
            self.assertTrue(body, text)
            for line in body:
                self.assertTrue(line.startswith("/"), line)
            self.assertIn(expected, body)

    def test_a_timer_that_survives_its_removal_is_named_instead_of_claimed_gone(self):
        # The device is asked after the removal. An entry that is still there will run its undo at its
        # start-time, so the run must not say it was removed.
        with fake("stubborn", after_change=CHANGED, stubborn_timer="1") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"), "--yes"])
            self.assertIn('name="revert-', f.ros("/system scheduler print detail"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("still armed", done.stderr)
        self.assertIn("may still be armed", done.stdout)
        self.assertNotIn("was removed", done.stdout)

    def test_a_change_that_takes_the_switch_away_is_reverted_and_identical(self):
        with fake("away", break_after_change="1",
                  after_change=CHANGED, after_undo=(EXPORTS / "v7-plain.rsc").read_text()) as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "3m"])
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("reverted", done.stdout)
        self.assertIn("identical", done.stdout)
        self.assertEqual(f.ros("/system scheduler print detail"), "")

    def test_a_revert_that_leaves_a_difference_exits_4(self):
        with fake("differs", break_after_change="1", after_change=CHANGED,
                  after_undo=(EXPORTS / "v7-plain.rsc").read_text() + "add name=leftover\n") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "3m"])
        self.assertEqual(done.returncode, 4, done.stderr)
        diff = done.stdout.split("the difference against the export taken before the change:", 1)[1]
        diff = diff.split("\nreverted", 1)[0]
        self.assertIn("+add name=leftover", diff)
        # The run's own timer was removed before the export was taken, so the only difference is
        # what the undo left behind.
        self.assertNotIn("scheduler", diff)
        self.assertNotIn("revert-", diff)

    def test_a_switch_that_never_comes_back_exits_5(self):
        with fake("gone", break_after_change="1", stays_down="1",
                  after_change=CHANGED) as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "3m"])
        self.assertEqual(done.returncode, 5)
        self.assertIn("did not answer", done.stdout)

    def test_an_import_error_undoes_on_demand_without_waiting(self):
        with fake("error", import_error="bad command (line 1)") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "10m"])
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("on demand", done.stdout)
        # A ten-minute window is 60 device calls at the fake's 10 s per call; the run made far fewer.
        self.assertLess(f.calls(), 30)

    def test_an_entry_whose_run_count_is_one_is_reported_as_reverted(self):
        with fake("fired", after_change=CHANGED) as f:
            f.fire_on_arm("1")                  # the entry runs before the first poll
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "10m"])
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertNotIn("applied", done.stdout)

    def test_an_import_error_behind_an_echoed_line_still_undoes_on_demand(self):
        with fake("error-late", import_error="echo: /ip address add ...\nbad command (line 2)") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "10m"])
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("on demand", done.stdout)
        self.assertLess(f.calls(), 30)

    def test_a_run_count_that_cannot_be_read_claims_nothing_about_the_timer(self):
        with fake("nocount", break_after_change="1", after_change=CHANGED,
                  after_undo=(EXPORTS / "v7-plain.rsc").read_text(),
                  hide_run_count_after_change="1") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--yes", "--window", "3m"])
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("run-count could not be read", done.stdout)
        self.assertNotIn("the timer revert-", done.stdout)


class V6RunTests(unittest.TestCase):
    """A RouterOS 6 switch: the other export command, and a clock written the other way round."""

    V6_CLOCK = ("                  date: nov/25/2026\n"
                "                  time: 09:20:00\n"
                "       time-zone-autodetect: no\n"
                "           time-zone-name: Europe/Paris\n")

    def test_the_timer_date_is_the_form_the_device_takes(self):
        # Module `mmm/dd/yyyy`, the day zero-padded as the device prints it. An ISO date is refused
        # there as `invalid date`, which the fake models, so this fails on an ISO start-date.
        with fake("date-form", now="2026-12-06 09:20:00", step="0") as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"),
                             "--arm-only", "--yes", "--window", "10m"])
            detail = f.ros("/system scheduler print detail")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("start-date=dec/06/2026", detail)

    def test_a_v6_run_exports_hide_sensitive_verbose_and_reads_its_clock(self):
        with fake("v6run", version="6.49.10", step="0", clock_output=self.V6_CLOCK,
                  config=(EXPORTS / "v6-plain.rsc").read_text(),
                  verbose=(EXPORTS / "v6-verbose.rsc").read_text()) as f:
            done = f.script(["--target", "crs326", "--change", str(CHANGES / "add-address-v6.rsc"),
                             "--arm-only", "--yes", "--window", "10m"])
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("/export hide-sensitive verbose", f.commands())
            detail = f.ros("/system scheduler print detail")
            self.assertIn("start-date=nov/25/2026", detail)
            self.assertIn("start-time=09:30:00", detail)


class InterruptTests(unittest.TestCase):
    def test_a_sigterm_during_verify_leaves_the_timer_armed_and_names_it(self):
        # the switch never comes back and the fake clock does not advance, so the poll waits
        with fake("term", break_after_change="1", after_change=CHANGED, step="0") as f:
            proc = f.spawn(["--target", "crs326", "--change", str(CHANGES / "set-pvid.rsc"), "--yes"])
            seen = f.wait_for("verifying", proc)   # phase 4 has started, so the entry is armed
            proc.send_signal(signal.SIGTERM)
            rest, _ = proc.communicate(timeout=10)
            out = seen + rest
            self.assertEqual(proc.returncode, 1)
            self.assertIn("cancel", out)
            self.assertIn("revert-", out)
            self.assertIn("run-count=0", f.ros("/system scheduler print detail"))


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
