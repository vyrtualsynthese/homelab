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
        )
        self.assertNotIn("hunter2", out)
        self.assertNotIn("oldkey", out)
        self.assertIn("wpa2-pre-shared-key=<redacted>", out)
        self.assertIn("wpa-pre-shared-key=<redacted>", out)

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

    def test_the_export_flag_follows_the_routeros_major(self):
        six = run(["--export-command", "6"])
        self.assertEqual(six.returncode, 0)
        self.assertEqual(six.stdout.strip(), "/export hide-sensitive")
        seven = run(["--export-command", "7"])
        self.assertEqual(seven.returncode, 0)
        self.assertEqual(seven.stdout.strip(), "/export terse")

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


spec = importlib.util.spec_from_file_location("check_docs", ROOT / "hack" / "check_docs.py")
check_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_docs)


class PageTests(unittest.TestCase):
    def page(self):
        done = run(env={"NET_AUDIT_FROM_FIXTURE": str(FIXTURE), "NET_AUDIT_DATE": "2026-11-24"})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

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
        self.assertIn("CR326", page)
        self.assertIn("7.16.1", page)

    def test_a_large_export_is_not_truncated(self):
        page = self.page()
        self.assertIn("/ip address add address=192.168.1.11/24", page)
        self.assertIn("fixture-last-line", page)
        self.assertNotIn("(timed out", page)

    def test_the_page_says_what_it_does_not_cover(self):
        self.assertIn("## What this record does not cover", self.page())
