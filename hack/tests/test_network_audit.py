"""The network collector's contract: read-only, redacted, testable without a device."""

import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ansible" / "collectors" / "network-audit.sh"
FIXTURE = ROOT / "hack" / "fixtures" / "network"


def run(args=(), env=None, stdin=None):
    merged = {k: v for k, v in os.environ.items() if not k.startswith("NET_AUDIT_")}
    merged.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin, text=True, capture_output=True, env=merged,
    )


class UsageTests(unittest.TestCase):
    def test_the_script_parses(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)

    def test_help_names_the_read_only_contract(self):
        done = run(["--help"])
        self.assertEqual(done.returncode, 0)
        self.assertIn("read-only", done.stdout)
        self.assertIn("NET_AUDIT_FROM_FIXTURE", done.stdout)
        self.assertIn("LIVEBOX_HOST", done.stdout)

    def test_help_prints_comment_lines_only(self):
        for line in run(["--help"]).stdout.splitlines():
            self.assertTrue(line.startswith("#"), line)

    def test_list_targets_prints_the_defaults(self):
        done = run(["--list-targets"])
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.split(), ["crs326", "192.168.1.11", "crs309", "192.168.1.43"])

    def test_list_targets_honours_the_override(self):
        done = run(["--list-targets"], env={"NET_AUDIT_TARGETS": "only=203.0.113.9"})
        self.assertEqual(done.stdout.split(), ["only", "203.0.113.9"])

    def test_an_unknown_flag_is_a_usage_error(self):
        self.assertEqual(run(["--nope"]).returncode, 2)

    def test_a_malformed_target_list_prints_no_partial_set(self):
        done = run(["--list-targets"], env={"NET_AUDIT_TARGETS": "crs326=192.168.1.11 broken"})
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")


class RouterOSRedactionTests(unittest.TestCase):
    def redact(self, text):
        done = run(["--redact-routeros"], stdin=text)
        self.assertEqual(done.returncode, 0)
        return done.stdout

    def test_wifi_keys_are_removed_wherever_they_sit(self):
        out = self.redact(
            '/interface wireless security-profiles set [ find default=yes ] '
            'authentication-types=wpa2-psk mode=dynamic-keys wpa2-pre-shared-key="hunter2" '
            'wpa-pre-shared-key=oldkey\n'
            '/interface wireless security-profiles set [ find default=yes ] '
            'authentication-password=authpw encryption-password=encpw mode=dynamic-keys\n'
        )
        self.assertNotIn("hunter2", out)
        self.assertNotIn("oldkey", out)
        self.assertNotIn("authpw", out)
        self.assertNotIn("encpw", out)
        self.assertIn("wpa2-pre-shared-key=<redacted>", out)
        self.assertIn("wpa-pre-shared-key=<redacted>", out)
        self.assertIn("authentication-password=<redacted>", out)
        self.assertIn("encryption-password=<redacted>", out)
        self.assertIn("mode=dynamic-keys", out)

    def test_a_secret_that_is_not_the_first_pair_is_removed(self):
        out = self.redact('/interface wireguard set wg1 private-key="k3y" listen-port=51820\n')
        self.assertNotIn("k3y", out)
        self.assertIn("private-key=<redacted>", out)
        self.assertIn("listen-port=51820", out)

    def test_a_password_mid_line_is_removed_and_the_policy_kept(self):
        out = self.redact('/user set [ find name=audit ] password="p4ss word" policy=read\n')
        self.assertNotIn("p4ss word", out)
        self.assertIn("password=<redacted>", out)
        self.assertIn("policy=read", out)

    def test_snmp_community_is_removed(self):
        out = self.redact('/snmp community set [ find default=yes ] name=public community=s3cr3t\n')
        self.assertNotIn("s3cr3t", out)

    def test_the_snmp_community_carried_by_name_is_removed(self):
        out = self.redact(
            '/snmp community add name=lan9xyz comment="discovery"\n'
            '/interface wireguard peers add name=peer1\n'
        )
        self.assertNotIn("lan9xyz", out)
        self.assertIn("name=<redacted>", out)
        self.assertIn("name=peer1", out)

    def test_a_v6_snmp_block_hides_the_community_on_the_line_below(self):
        out = self.redact(
            '/snmp community\n'
            'add addresses=192.168.1.0/24 name=lan6xyz\n'
            '/interface bridge\n'
            'add name=bridge\n'
        )
        self.assertNotIn("lan6xyz", out)
        self.assertIn("name=<redacted>", out)
        self.assertIn("name=bridge", out)

    def test_a_value_that_reopens_a_quote_is_redacted_to_the_end_of_the_line(self):
        out = self.redact('password="ab"cd"\n')
        self.assertNotIn("cd", out)
        self.assertIn("password=<redacted>", out)

    def test_a_backslash_escaped_quote_in_a_password_does_not_leak_the_tail(self):
        out = self.redact('password="a\\"b"\n')
        self.assertNotIn('a\\"b', out)
        self.assertNotIn("b", out)
        self.assertIn("password=<redacted>", out)

    def test_non_secrets_survive(self):
        line = ('/interface wireguard peers add public-key="AAAAB3NzaC1yc2E=" allowed-address=10.8.0.2/32 '
                'comment="uplink to the NAS" name=peer1\n')
        out = self.redact(line)
        self.assertIn('public-key="AAAAB3NzaC1yc2E="', out)
        self.assertIn('comment="uplink to the NAS"', out)
        self.assertIn("name=peer1", out)

    def test_a_dynamic_keys_mode_survives_after_a_psk(self):
        out = self.redact("authentication-types=wpa2-psk mode=dynamic-keys\n")
        self.assertIn("mode=dynamic-keys", out)

    def test_addresses_and_ports_survive(self):
        line = "/ip address add address=192.168.1.11/24 interface=bridge comment=mgmt\n"
        self.assertEqual(self.redact(line), line)


class FetchTests(unittest.TestCase):
    def env(self, **extra):
        merged = {"NET_AUDIT_FROM_FIXTURE": str(FIXTURE)}
        merged.update(extra)
        return merged

    def test_a_fixture_is_read_for_a_known_slug(self):
        done = run(["--dump", "crs326", "identity"], env=self.env())
        self.assertEqual(done.returncode, 0)
        self.assertIn("CR326", done.stdout)

    def test_a_missing_fixture_is_reported_as_unreachable(self):
        done = run(["--dump", "ghost", "identity"], env=self.env())
        self.assertEqual(done.returncode, 0)
        self.assertIn("(unreachable:", done.stdout)

    def test_the_routeros_major_is_read_from_the_resource_block(self):
        self.assertEqual(run(["--major", "crs326"], env=self.env()).stdout.strip(), "7")
        self.assertEqual(run(["--major", "crs309"], env=self.env()).stdout.strip(), "6")

    def test_the_export_command_follows_the_routeros_major(self):
        six = run(["--export-command", "6"])
        self.assertEqual(six.returncode, 0)
        self.assertEqual(six.stdout.strip(), "/export hide-sensitive")
        # RouterOS 7 hides sensitive values by default and `terse` was never verified on a device,
        # so major 7 gets the bare command and the redactor is the backstop.
        seven = run(["--export-command", "7"])
        self.assertEqual(seven.returncode, 0)
        self.assertEqual(seven.stdout.strip(), "/export")

    def test_an_unknown_major_sends_no_export_at_all(self):
        done = run(["--export-command", "unknown"])
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")
        self.assertIn("export", done.stderr)

    def test_an_export_with_no_readable_version_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "crs326.export.txt").write_text(
                '/ip route add dst-address=10.0.0.0/8 gateway=192.168.1.254\n'
            )
            done = run(["--dump", "crs326", "export"],
                       env=self.env(NET_AUDIT_FROM_FIXTURE=str(root)))
        self.assertEqual(done.returncode, 0)
        self.assertIn("(unreachable: RouterOS version unknown, export skipped)", done.stdout)
        self.assertNotIn("10.0.0.0/8", done.stdout)

    def test_the_v6_export_hides_the_snmp_community(self):
        done = run(["--dump", "crs309", "export"], env=self.env())
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("fixture-community-not-real", done.stdout)
        self.assertIn("name=<redacted>", done.stdout)

    def test_a_malformed_target_list_is_a_usage_error_before_any_fetch(self):
        done = run(
            ["--dump", "crs326", "identity"],
            env=self.env(NET_AUDIT_TARGETS="crs326=192.168.1.11 broken"),
        )
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertIn("is not name=host", done.stderr)

    def test_a_device_that_refuses_the_key_is_unreachable_and_fast(self):
        done = run(
            ["--dump", "ghost", "identity"],
            env={"NET_AUDIT_TARGETS": "ghost=203.0.113.9", "NET_AUDIT_CONNECT_TIMEOUT": "1",
                 "NET_AUDIT_CMD_TIMEOUT": "2"},
        )
        self.assertIn("(unreachable:", done.stdout)


class TransportTests(unittest.TestCase):
    # The live branch is driven by stub `ssh` and `curl` scripts on PATH, the way LiveboxTests drives
    # the box: no device is contacted, and what the script does with a capture is what is measured.
    # This is the branch the two Important defects lived in, so it gets a harness of its own.

    RESOURCE = ("                  uptime: 3w2d1h\\n"
                "                 version: 7.16.1 (stable)\\n")

    def run_with(self, ssh_script, args=(), targets="crs326=127.0.0.1"):
        with tempfile.TemporaryDirectory() as tmp:
            for name, script in (("ssh", ssh_script), ("curl", "printf '200\\n'")):
                stub = pathlib.Path(tmp) / name
                stub.write_text("#!/usr/bin/env bash\n" + script)
                stub.chmod(0o755)
            return run(
                list(args),
                env={
                    "PATH": f"{tmp}:{os.environ['PATH']}",
                    "NET_AUDIT_TARGETS": targets,
                    "NET_AUDIT_CONNECT_TIMEOUT": "1",
                    "NET_AUDIT_CMD_TIMEOUT": "2",
                },
            )

    def ssh_answering(self, export):
        # A RouterOS session: the version block always answers, `/export` answers with the text
        # under test and exits 0, which is how a rejected export command actually arrives.
        return (
            "for last; do :; done\n"
            'case "$last" in\n'
            f"  \"/system resource print\") printf '{self.RESOURCE}' ;;\n"
            f"  \"/export\") printf '{export}' ;;\n"
            "  *) printf '\\n' ;;\n"
            "esac\n"
            "exit 0\n"
        )

    def test_a_routeros_error_on_an_exit_zero_call_is_not_printed_as_configuration(self):
        page = self.run_with(
            self.ssh_answering("expected end of command (line 1 column 12)\\n")
        ).stdout
        configuration = page.split("### Configuration")[1].split("## Livebox")[0]
        self.assertIn("(unreachable: expected end of command (line 1 column 12))", configuration)
        self.assertNotIn("```", configuration)

    def test_a_failure_word_deep_in_a_configuration_is_not_a_failed_capture(self):
        # Anchoring to the first line is what keeps a comment that happens to read `permission
        # denied` from turning a good export into an unreachable device.
        text = ('/ip route add dst-address=10.0.0.0/8 gateway=192.168.1.254\n'
                '/ip firewall filter add chain=forward comment="permission denied"\n')
        done = self.run_with(self.ssh_answering(text), ["--dump", "crs326", "export"])
        self.assertNotIn("(unreachable:", done.stdout)
        self.assertIn("permission denied", done.stdout)

    def test_ssh_is_invoked_at_log_level_error(self):
        # LogLevel=ERROR is what stops ssh printing `Warning: Permanently added ...` into a first
        # run's capture; a stub that echoes the invocation shows the flags the script builds.
        done = self.run_with('printf \'%s\\n\' "$*"\nexit 0\n',
                             ["--dump", "crs326", "identity"])
        self.assertIn("-o LogLevel=ERROR", done.stdout)

    def test_the_unreachable_reason_passes_the_redactor(self):
        # The reason is the one line outside the redactor's usual flow, and a RouterOS error can
        # echo a value beside its attribute name.
        done = self.run_with(self.ssh_answering('failure: bad password="s3cr3t" in /user\n'),
                             ["--dump", "crs326", "export"])
        self.assertIn("(unreachable:", done.stdout)
        self.assertNotIn("s3cr3t", done.stdout)

    def test_a_carriage_return_from_ssh_does_not_reach_the_page(self):
        # RouterOS answers with CRLF and ssh closes a connection error with a CR, so without a strip
        # the reason ends on an invisible byte and every fenced line carries one into the page.
        done = self.run_with(
            "printf 'ssh: connect to host 127.0.0.1 port 22: Connection refused\\r\\n'\n"
            "exit 255\n",
            ["--dump", "crs326", "identity"],
        )
        self.assertIn("(unreachable: ssh: connect to host 127.0.0.1 port 22: Connection refused)\n",
                      done.stdout)
        self.assertNotIn("\r", done.stdout)

    def test_a_call_that_answers_nothing_says_why_it_failed(self):
        # A capture the timeout killed prints no line at all, and `(unreachable: )` would read as a
        # device that answered with nothing.
        done = self.run_with("exit 124\n", ["--dump", "crs326", "identity"])
        self.assertIn("(unreachable: ssh exited 124 with no output)", done.stdout)


spec = importlib.util.spec_from_file_location("check_docs", ROOT / "hack" / "check_docs.py")
check_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_docs)


class PageTests(unittest.TestCase):
    # The blocks a device answers when a test withholds only the one it is about.
    ANSWERING = {
        "resource": "                  uptime: 3w2d1h\n"
                    "                 version: 7.16.1 (stable)\n",
        "identity": "                   name: CR326\n",
        "routerboard": "             model: CRS326-24G-2S+\n",
    }

    def page(self):
        done = run(env={"NET_AUDIT_FROM_FIXTURE": str(FIXTURE), "NET_AUDIT_DATE": "2026-11-24"})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def page_of(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for slug, text in {**self.ANSWERING, **files}.items():
                (root / f"crs326.{slug}.txt").write_text(text)
            return run(env={"NET_AUDIT_FROM_FIXTURE": str(root), "NET_AUDIT_DATE": "2026-11-24",
                            "NET_AUDIT_TARGETS": "crs326=192.168.1.11"})

    def test_the_banner_passes_the_docs_checker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "network-2026-11-24.md").write_text(self.page())
            self.assertEqual(check_docs.banner_problems([root / "docs/network-2026-11-24.md"], root), [])

    def test_the_page_carries_no_secret_from_the_fixtures(self):
        page = self.page()
        self.assertNotIn("fixture-psk-not-real", page)
        self.assertNotIn("fixture-wg-psk", page)
        self.assertIn("AAAAB3NzaC1yc2E=", page)

    def test_a_section_per_device_with_its_identity(self):
        page = self.page()
        self.assertIn("## crs326 — 192.168.1.11", page)
        self.assertIn("## crs309 — 192.168.1.43", page)
        # The labelled line, not the bare value: 7.16.1 is also in the package fixture
        # (`crs326.package.txt`, the `0 routeros 7.16.1` row), so only `routeros: 7.16.1` shows
        # the version was read from the resource block's own `version:` field.
        self.assertIn("identity: CR326", page)
        self.assertIn("model: CRS326-24G-2S+", page)
        self.assertIn("serial: HE908ABCD", page)
        self.assertIn("routeros: 7.16.1", page)

    def test_the_ethernet_table_labels_every_port(self):
        page = self.page()
        self.assertIn("| port | status | speed |", page)
        self.assertIn("| ether1 | link-ok | 1Gbps |", page)
        self.assertIn("| ether3 | no-link | unknown |", page)
        self.assertIn("| sfp-sfpplus1 | link-ok | 10Gbps |", page)
        self.assertNotIn("| unknown | unknown | unknown |", page)

    def test_an_export_failure_is_a_line_above_the_fence(self):
        done = self.page_of({"ethernet": "                      name: ether1\n"
                                         "                    status: link-ok\n"
                                         "                      rate: 1Gbps\n"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("(unreachable: no fixture for crs326.export)", done.stdout)
        self.assertIn("### Configuration\n\n(unreachable:", done.stdout)

    def test_a_port_block_that_yields_no_port_says_so(self):
        done = self.page_of({"ethernet": "", "export": "/system identity set name=CR326\n"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("(no port reported)", done.stdout)
        self.assertNotIn("| port | status | speed |", done.stdout)

    def test_a_port_table_whose_blocks_are_not_blank_separated_has_one_row_each(self):
        # Some device shapes a monitor block without a blank line between ports, so a flush that
        # waits only for a blank line would merge every port into one row and drop the rest.
        done = self.page_of({
            "ethernet": "                      name: ether1\n"
                        "                    status: link-ok\n"
                        "                      rate: 1Gbps\n"
                        "                      name: ether2\n"
                        "                    status: no-link\n",
            "export": "/system identity set name=CR326\n",
        })
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("| ether1 | link-ok | 1Gbps |", done.stdout)
        self.assertIn("| ether2 | no-link | unknown |", done.stdout)
        self.assertNotIn("| ether1 ether2 |", done.stdout)

    def test_a_large_export_is_not_truncated(self):
        # Review focus #4 is thousands of lines, and the checked-in fixture is 11: a line cap added
        # by accident would pass against it. The marker is the last line of a 3,000-line export.
        lines = "".join(f"/ip firewall filter add chain=forward comment=rule-{i}\n"
                        for i in range(3000))
        done = self.page_of({"export": lines + "/system note set note=fixture-last-line\n"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("comment=rule-0\n", done.stdout)
        self.assertIn("comment=rule-2999\n", done.stdout)
        self.assertIn("note=fixture-last-line", done.stdout)

    def test_the_page_says_what_it_does_not_cover(self):
        self.assertIn("## What this record does not cover", self.page())


class LiveboxTests(unittest.TestCase):
    def page(self):
        return run(env={"NET_AUDIT_FROM_FIXTURE": str(FIXTURE)}).stdout

    # The live branch is the one the bug lived in, so it is driven by a stub `curl` on PATH — a
    # small script that prints a body and a status line the way `-w '\n%{http_code}'` does, or
    # fails with curl's own message. The switches are pointed at a loopback address that refuses
    # the connection, so the run reaches `page_livebox` without contacting a device.
    def live_page(self, script):
        with tempfile.TemporaryDirectory() as tmp:
            stub = pathlib.Path(tmp) / "curl"
            stub.write_text("#!/usr/bin/env bash\n" + script)
            stub.chmod(0o755)
            done = run(
                env={
                    "PATH": f"{tmp}:{os.environ['PATH']}",
                    "NET_AUDIT_TARGETS": "ghost=127.0.0.1",
                    "NET_AUDIT_CONNECT_TIMEOUT": "1",
                    "NET_AUDIT_CMD_TIMEOUT": "2",
                }
            )
        return done.stdout

    def test_the_firmware_build_is_read_from_the_login_page(self):
        self.assertIn("v1.0.161_20260923", self.page())

    def test_the_fixture_prints_no_status_it_did_not_measure(self):
        # The fixture directory never fetched the page, so a 200 here would be a guess printed in
        # the same `http:` line a measured answer uses.
        page = self.page()
        self.assertIn("http: not measured (fixture)", page)
        self.assertNotIn("http: 200", page)

    def test_a_measured_200_is_printed_as_200(self):
        page = self.live_page(
            "printf '<script data-main=\"loader.js?sah=v1.0.161_20260923\"></script>\\n'\n"
            "printf '200\\n'\n"
        )
        self.assertIn("http: 200", page)
        self.assertIn("firmware: v1.0.161_20260923", page)

    def test_an_answer_that_is_not_200_is_printed_as_the_code_it_gave(self):
        # `curl -sS` exits 0 for a 404 or a login challenge, so the exit code cannot supply this.
        page = self.live_page("printf '<html>who are you</html>\\n'\nprintf '404\\n'\n")
        self.assertIn("http: 404", page)
        self.assertNotIn("http: 200", page)

    def test_a_failed_read_prints_curls_reason_not_just_the_exit(self):
        page = self.live_page(
            "printf 'curl: (7) Failed to connect: Connection refused\\n' >&2\nexit 7\n"
        )
        self.assertIn("(unreachable: curl: (7) Failed to connect: Connection refused)", page)
        self.assertNotIn("curl exit 7", page)

    def test_a_page_with_no_sah_prints_firmware_unknown(self):
        page = self.live_page("printf '<title></title>\\n'\nprintf '200\\n'\n")
        self.assertIn("firmware: unknown", page)

    def test_a_build_string_outside_the_version_alphabet_is_unknown(self):
        # Truncating at the hyphen would print v1.0.161 as a measured build the box never ran.
        page = self.live_page("printf 'loader.js?sah=v1.0.161-20260923\\n'\nprintf '200\\n'\n")
        self.assertIn("firmware: unknown", page)
        self.assertNotIn("v1.0.161", page)

    def test_a_build_string_with_a_second_equals_is_unknown(self):
        # `cut -d= -f2` would turn v1.0.161=20260923 into v1.0.161, which passes the well-formedness
        # check and prints as a firmware the box never ran, so the whole tail is taken.
        page = self.live_page("printf 'loader.js?sah=v1.0.161=20260923\\n'\nprintf '200\\n'\n")
        self.assertIn("firmware: unknown", page)
        self.assertNotIn("v1.0.161", page)

    def test_the_remembered_half_is_labelled(self):
        page = self.page()
        self.assertIn("not read from the device", page)
        self.assertIn("192.168.1.46", page)

    def test_an_unreachable_livebox_still_prints_a_page(self):
        done = run(env={"NET_AUDIT_FROM_FIXTURE": "/nonexistent"})
        self.assertIn("(unreachable:", done.stdout)
        self.assertIn("## What this record does not cover", done.stdout)

    def test_a_run_with_no_reachable_device_exits_1(self):
        done = run(env={"NET_AUDIT_TARGETS": "ghost=203.0.113.9",
                        "NET_AUDIT_FROM_FIXTURE": "/nonexistent"})
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout.count("(unreachable:"), 2)
