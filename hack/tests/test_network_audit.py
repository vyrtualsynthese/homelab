"""The network collector's contract: read-only, redacted, testable without a device."""

import os
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ansible" / "collectors" / "network-audit.sh"


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

    def test_list_targets_prints_the_defaults(self):
        done = run(["--list-targets"])
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.split(), ["crs326", "192.168.1.11", "crs309", "192.168.1.43"])

    def test_list_targets_honours_the_override(self):
        done = run(["--list-targets"], env={"NET_AUDIT_TARGETS": "only=203.0.113.9"})
        self.assertEqual(done.stdout.split(), ["only", "203.0.113.9"])

    def test_an_unknown_flag_is_a_usage_error(self):
        self.assertEqual(run(["--nope"]).returncode, 2)


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
