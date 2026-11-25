"""The change script's contract: the undo it renders, and the runs it drives."""

import contextlib
import json
import os
import pathlib
import subprocess
import tempfile
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
            "FAKE_STEP": str(kw.get("step", 60)),
            "FAKE_VERSION": kw.get("version", "7.16.1"),
        }
        self._write("clock", self.env["FAKE_NOW"] + "\n")
        self._write("config", kw.get("config", (EXPORTS / "v7-plain.rsc").read_text()))
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
            text=True, capture_output=True, env=self._env(),
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
        return subprocess.run(["bash", str(SCRIPT), *args], input=stdin, text=True,
                              capture_output=True, env=env)

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
