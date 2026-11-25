"""The access log is read by two consumers with opposite requirements.

CrowdSec's `crowdsecurity/traefik-logs` parser reads the source address from
`ClientHost` (it uses `ClientAddr` as the *destination*, `Split(..., ':')[0]`)
and the user agent from `request_User-Agent`. Loki must hold neither the path
nor any header. Both are true only because the log goes to a file and Fluent
Bit strips the two keys on the way out, so this test pins the field list that
makes the file usable and the flag that makes the file exist.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUES = ROOT / "cluster/helm/platform/traefik/values.yaml"


class AccessLogTests(unittest.TestCase):
    def setUp(self):
        self.traefik = yaml.safe_load(VALUES.read_text())["traefik"]
        self.access = self.traefik["accessLog"]

    def test_the_log_is_json_at_a_file_on_the_host(self):
        self.assertTrue(self.access["enabled"])
        self.assertEqual(self.access["format"], "json")
        self.assertEqual(self.access["filePath"], "/var/log/traefik/access.log")

    def test_the_host_directory_is_mounted_read_write(self):
        volumes = {v["name"]: v for v in self.traefik["deployment"]["additionalVolumes"]}
        self.assertEqual(volumes["traefik-access-log"]["hostPath"]["path"], "/var/log/traefik")
        # DirectoryOrCreate, not Directory: an emptyDir-style hostPath of type
        # Directory refuses to mount when the path is missing, and the pod that
        # refuses to start is the router — the whole house offline to add a log.
        # Traefik itself survives a file it cannot open (accesslog.NewHandler
        # logs "Unable to create access logger" and returns a nil handler); the
        # kubelet does not survive a mount it cannot make. The Ansible role then
        # sets 2770 65532:65532 on the directory the kubelet created as root.
        self.assertEqual(volumes["traefik-access-log"]["hostPath"]["type"], "DirectoryOrCreate")
        mounts = {m["name"]: m for m in self.traefik["additionalVolumeMounts"]}
        self.assertEqual(mounts["traefik-access-log"]["mountPath"], "/var/log/traefik")
        self.assertFalse(mounts["traefik-access-log"].get("readOnly", False))

    def test_the_parser_can_find_the_client_and_the_user_agent(self):
        names = self.access["fields"]["names"]
        self.assertEqual(names["ClientHost"], "keep")
        self.assertEqual(names["ClientAddr"], "keep")
        self.assertEqual(names["RequestPath"], "keep")
        self.assertEqual(self.access["fields"]["headers"]["names"], {"User-Agent": "keep"})

    def test_everything_that_names_a_resource_stays_dropped(self):
        names = self.access["fields"]["names"]
        for field in ("ClientUsername", "ClientPort", "RequestReferer"):
            self.assertEqual(names[field], "drop", field)
        self.assertEqual(self.access["fields"]["headers"]["defaultMode"], "drop")
        self.assertEqual(self.access["fields"]["queryParameters"]["defaultMode"], "drop")


if __name__ == "__main__":
    unittest.main()
