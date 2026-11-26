"""The exporter's two names, held where a wrong one is silent.

CoreDNS answers from the node's `/etc/resolv.conf`, and `nuc0` — the node this
pod is pinned to — listed `1.1.1.1` beside the Pi-hole, so about one `*.kub.local`
query in three went to Cloudflare and came back NXDOMAIN: the exporter failed for
a minute, recovered, and failed again, while every certificate check passed. The
two records are therefore written into the pod's own `/etc/hosts`. The name stays,
because the leaf is verified against it; only the address is pinned.
"""

import pathlib
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "cluster/helm/monitoring/pihole-exporter"
INVENTORY = ROOT / "ansible/inventory/hosts.yml"


def values():
    return yaml.safe_load((CHART / "values.yaml").read_text())


def inventory_address(host):
    return yaml.safe_load(INVENTORY.read_text())["all"]["children"]["edge"]["hosts"][host][
        "ansible_host"
    ]


def render(*extra):
    out = subprocess.run(
        ["helm", "template", "pihole-exporter", str(CHART), "-f", str(CHART / "values.yaml"), *extra],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise subprocess.CalledProcessError(out.returncode, out.args, out.stdout, out.stderr)
    return [doc for doc in yaml.safe_load_all(out.stdout) if doc]


def deployment(*extra):
    return [doc for doc in render(*extra) if doc["kind"] == "Deployment"][0]


class AddressTests(unittest.TestCase):
    def test_every_hostname_carries_the_inventory_address(self):
        """The chart repeats the two addresses on purpose; the inventory is where
        they are decided, so a move on the LAN is a test failure here rather than
        an exporter that dials whatever used to answer."""
        pihole = values()["pihole"]
        expected = {
            "pihole-rbp0.kub.local": inventory_address("rbp0"),
            "pihole-rbp1.kub.local": inventory_address("rbp1"),
        }
        self.assertEqual(pihole["addresses"], expected)
        self.assertEqual(sorted(pihole["addresses"]), sorted(pihole["hostnames"]))

    def test_the_pod_finds_both_names_without_asking_cluster_dns(self):
        first, second = deployment()["spec"]["template"]["spec"]["hostAliases"]
        self.assertEqual(first, {"ip": "192.168.1.22", "hostnames": ["pihole-rbp0.kub.local"]})
        self.assertEqual(second, {"ip": "192.168.1.38", "hostnames": ["pihole-rbp1.kub.local"]})

    def test_the_chart_refuses_a_hostname_it_could_not_resolve(self):
        """A new Pi-hole added to `hostnames` alone would fall back to the
        resolver this pin exists to bypass, and the failure would be the same
        once-a-minute warning nobody reads."""
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            render("--set", r"pihole.addresses.pihole-rbp1\.kub\.local=null")
        self.assertIn("pihole-rbp1.kub.local", caught.exception.stderr)
        self.assertIn("pihole.addresses", caught.exception.stderr)


class TrustTests(unittest.TestCase):
    def test_the_process_trusts_one_configmap_key_and_skips_nothing(self):
        spec = deployment()["spec"]["template"]["spec"]
        env = {e["name"]: e.get("value") for e in spec["containers"][0]["env"]}
        key = values()["pihole"]["ca"]["configMapKey"]
        self.assertEqual(env["SSL_CERT_FILE"], f"/etc/ssl/certs/{key}")
        self.assertFalse([name for name in env if "SKIP" in name.upper()])
        volumes = {v["name"]: v for v in spec["volumes"]}
        self.assertTrue(volumes["pihole-ca"]["configMap"]["name"].endswith("-ca"))
        mounted = {m["name"]: m for m in spec["containers"][0]["volumeMounts"]}
        self.assertEqual(mounted["pihole-ca"]["mountPath"], "/etc/ssl/certs")

    def test_the_configmap_carries_the_root_the_environment_names(self):
        configmap = [doc for doc in render() if doc["kind"] == "ConfigMap"][0]
        key = values()["pihole"]["ca"]["configMapKey"]
        root = configmap["data"][key]
        self.assertIn("BEGIN CERTIFICATE", root)
        self.assertIn("END CERTIFICATE", root)
