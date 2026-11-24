"""The redactor in ansible/collectors/host-audit.sh, proved on the shapes this homelab stores.

The collector prints a host's configuration to a file a person reads and an agent may hold. Every
value passes through `redact()` first, and that function is the only thing between a K3s token, a
WireGuard private key or a restic password and that file. So it earns a test: each fixture below is
a line the script can actually meet on one of the six machines, and each secret in it must not
survive.

Run with the rest of the suite:

    uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
"""

import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ansible" / "collectors" / "host-audit.sh"


def redact(text):
    done = subprocess.run(
        ["bash", str(SCRIPT), "--redact"],
        input=text,
        text=True,
        capture_output=True,
        check=True,
    )
    return done.stdout


class SecretRemovalTests(unittest.TestCase):
    def assertSecretsGone(self, text, secrets):
        out = redact(text)
        for secret in secrets:
            self.assertNotIn(secret, out, f"{secret!r} survived redaction:\n{out}")
        return out

    def test_k3s_token_keeps_its_key(self):
        out = self.assertSecretsGone(
            "users:\n- name: default\n  token: k10sekrittoken0001::server:abc\n",
            ["k10sekrittoken0001::server:abc"],
        )
        self.assertIn("token:", out)
        self.assertIn("<redacted>", out)

    def test_wireguard_keys(self):
        self.assertSecretsGone(
            "[Interface]\nPrivateKey = WGPRIVATEsekrit=\nListenPort = 51820\n"
            "[Peer]\nPresharedKey = WGPRESHAREDsekrit=\n",
            ["WGPRIVATEsekrit=", "WGPRESHAREDsekrit="],
        )

    def test_env_files(self):
        self.assertSecretsGone(
            "VIKUNJA_MCP_TOKEN=envtokensekrit\n"
            "AUTHENTIK_TOKEN=authsekrit\n"
            "POSTGRES_PASSWORD=dbsekrit\n"
            "TZ=Europe/Paris\n",
            ["envtokensekrit", "authsekrit", "dbsekrit"],
        )

    def test_yaml_and_json_values(self):
        self.assertSecretsGone(
            'password: yamlsekrit\n"api_key": "jsonsekrit"\nclient-secret: clientsekrit\n',
            ["yamlsekrit", "jsonsekrit", "clientsekrit"],
        )

    def test_credential_inside_a_url(self):
        out = self.assertSecretsGone(
            "DATABASE_URL=postgres://vikunja:urlsekrit@db.kub.local:5432/vikunja\n", ["urlsekrit"]
        )
        self.assertIn("db.kub.local:5432", out)

    def test_bearer_token(self):
        self.assertSecretsGone(
            "Authorization: Bearer barriersekrit0123456789\n", ["barriersekrit0123456789"]
        )

    def test_presigned_url_parameters(self):
        self.assertSecretsGone(
            "https://ashudev.s3.gra.io.cloud.ovh.net/x?X-Amz-Credential=credsekrit"
            "&X-Amz-Signature=sigsekrit&X-Amz-Expires=900\n",
            ["credsekrit", "sigsekrit"],
        )

    def test_jwt(self):
        self.assertSecretsGone(
            "cookie: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZ2VudCJ9.jwtsekritbody\n",
            ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZ2VudCJ9.jwtsekritbody"],
        )

    def test_private_key_block(self):
        out = self.assertSecretsGone(
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB\n"
            "-----END OPENSSH PRIVATE KEY-----\n",
            ["b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB"],
        )
        self.assertIn("private key block", out)

    def test_aws_key_id(self):
        self.assertSecretsGone("key id AKIAIOSFODNN7EXAMPLE here\n", ["AKIAIOSFODNN7EXAMPLE"])

    def test_secret_passed_as_a_flag(self):
        out = self.assertSecretsGone(
            "ExecStart=/usr/local/bin/k3s server --token=flagsekrit --data-dir /var/lib/rancher/k3s\n",
            ["flagsekrit"],
        )
        self.assertIn("/usr/local/bin/k3s", out)
        self.assertIn("--data-dir /var/lib/rancher/k3s", out)


class NothingElseIsTouchedTests(unittest.TestCase):
    """Over-redaction is a defect too: the report is worthless if every line is `<redacted>`."""

    def test_sshd_effective_settings_survive(self):
        text = "passwordauthentication no\npermitrootlogin no\npubkeyauthentication yes\n"
        self.assertEqual(redact(text), text)

    def test_sudoers_and_sysctl_survive(self):
        text = "%admins ALL=(ALL) NOPASSWD: ALL\nvm.swappiness = 60\n"
        self.assertEqual(redact(text), text)

    def test_package_lines_survive(self):
        text = "linux-image-generic\t6.8.0-49.49\tinstall ok installed\n"
        self.assertEqual(redact(text), text)

    def test_apt_sources_and_addresses_survive(self):
        text = (
            "deb https://download.docker.com/linux/ubuntu jammy stable\n"
            "    inet 192.168.1.22/24 brd 192.168.1.255 scope global eth0\n"
            "Address = 10.8.0.1/24\n"
        )
        self.assertEqual(redact(text), text)

    def test_a_path_to_a_secret_file_survives(self):
        text = "restic -r s3:https://s3.gra.io.cloud.ovh.net/nodes-nas --password-file /etc/restic/password\n"
        self.assertEqual(redact(text), text)


class ScriptTests(unittest.TestCase):
    def test_the_script_parses(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_redact_mode_needs_no_root_and_writes_nothing(self):
        done = subprocess.run(
            ["bash", str(SCRIPT), "--redact"],
            input="plain line\n",
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(done.stdout, "plain line\n")
        self.assertEqual(done.stderr, "")

    def test_help_names_the_redaction_contract(self):
        done = subprocess.run(
            ["bash", str(SCRIPT), "--help"], text=True, capture_output=True, check=True
        )
        self.assertIn("--redact", done.stdout)


if __name__ == "__main__":
    unittest.main()
