"""No chart configures a public resolver other than DNS4EU's unfiltered pair.

A resolver sees every name the house asks for, so the EU-only provider rule
covers it, and Pi-hole is the only filter in the path. Two pods carry their own
resolvers because they must not resolve through the cluster, which makes their
`values.yaml` the one place a public address is written down.

The decision is `docs/decisions/2026-11-27-public-dns-is-dns4eu-unfiltered.md`.
This test holds the chart half of it: the pair is the only one allowed, and it
is what both of those charts actually carry.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
HELM = ROOT / "cluster/helm"

DNS4EU = ["86.54.11.100", "86.54.11.200"]
BANNED = {
    "1.1.1.1",
    "1.0.0.1",
    "1.1.1.2",
    "1.1.1.3",
    "9.9.9.9",
    "149.112.112.112",
    "8.8.8.8",
    "8.8.4.4",
}


def leaves(node, path=""):
    """Every scalar in a parsed YAML tree, with the path that reaches it."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from leaves(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


class NoForeignPublicResolver(unittest.TestCase):
    def test_no_chart_values_names_cloudflare_google_or_quad9(self):
        findings = []
        for path in sorted(HELM.rglob("values.yaml")):
            data = yaml.safe_load(path.read_text()) or {}
            for leaf_path, value in leaves(data):
                if value.strip() in BANNED:
                    findings.append(f"{path.relative_to(ROOT)}: {leaf_path} = {value}")
        self.assertEqual(
            findings,
            [],
            "a public resolver other than DNS4EU's unfiltered pair is configured:\n"
            + "\n".join(findings),
        )

    def test_blackbox_exporter_probes_through_dns4eu(self):
        chart = HELM / "monitoring/blackbox-exporter"
        data = yaml.safe_load((chart / "values.yaml").read_text())
        dns = data["blackbox"]["dnsConfig"]
        self.assertEqual(dns["nameservers"], DNS4EU)

    def test_qbittorrent_resolves_through_dns4eu(self):
        chart = HELM / "media-stack/qbittorrent"
        data = yaml.safe_load((chart / "values.yaml").read_text())
        self.assertEqual(data["dns"]["nameservers"], DNS4EU)

    def test_pi_hole_forwards_to_dns4eu(self):
        settings = (
            ROOT / "ansible/playbooks/templates/runners/pihole.settings"
        ).read_text()
        self.assertIn(f"FTLCONF_dns_upstreams={';'.join(DNS4EU)}\n", settings)


if __name__ == "__main__":
    unittest.main()
