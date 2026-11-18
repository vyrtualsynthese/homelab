import pathlib
import sys
import unittest

import yaml

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import check_exposure  # noqa: E402


def violations(fixture):
    docs = [d for d in yaml.safe_load_all((HACK / "fixtures" / fixture).read_text()) if d]
    return check_exposure.check_documents(docs)


class GateTests(unittest.TestCase):
    def test_gated_route_passes(self):
        self.assertEqual(violations("gated.yaml"), [])

    def test_ungated_route_is_reported(self):
        found = violations("ungated.yaml")
        self.assertEqual(len(found), 1)
        self.assertIn("sonarr.kub.local", found[0])

    def test_every_route_in_a_document_is_checked(self):
        self.assertEqual(len(violations("duplicate-host.yaml")), 1)

    def test_bare_middleware_name_is_not_a_gate(self):
        self.assertEqual(len(violations("bare-name.yaml")), 1)

    def test_v3_field_is_reported(self):
        found = violations("ipallowlist.yaml")
        self.assertEqual(len(found), 1)
        self.assertIn("ipAllowList", found[0])

    def test_core_ingress_annotation_is_checked(self):
        found = violations("ingress-annotation.yaml")
        self.assertEqual(len(found), 1)
        self.assertIn("koillection", found[0])

    def test_public_host_must_not_be_gated(self):
        found = violations("public-gated.yaml")
        self.assertEqual(len(found), 1)
        self.assertIn("public host gated", found[0])

    def test_public_host_without_gate_passes(self):
        found = violations("public-gated.yaml")
        self.assertFalse(any("seerr" in v for v in found))

    def test_traefik_dashboard_requires_the_gate(self):
        found = violations("helmchartconfig.yaml")
        self.assertEqual(len(found), 1)
        self.assertIn("traefik dashboard", found[0])
        self.assertIn("traefik:", found[0])

    def test_traefik_dashboard_is_checked_when_the_values_do_not_parse(self):
        found = violations("helmchartconfig-malformed.yaml")
        self.assertEqual(len(found), 1)
        self.assertIn("traefik dashboard", found[0])

    def test_each_host_in_a_multi_host_route_is_evaluated(self):
        found = violations("mixed-hosts.yaml")
        self.assertEqual(len(found), 1)
        self.assertIn("sonarr.kub.local", found[0])

    def test_the_gate_itself_must_hold_both_ranges(self):
        found = violations("empty-gate.yaml")
        self.assertEqual(len(found), 1)
        self.assertIn("sourceRange", found[0])


if __name__ == "__main__":
    unittest.main()
