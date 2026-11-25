"""The chart's values, checked where a wrong value is expensive.

Three of them are load bearing and silent when wrong: the agent must be a
Deployment pinned to the node that owns the log file, the acquisition must set
the label the Traefik parser filters on, and the whitelist must cover the four
ranges and the four node addresses before any of this bans the operator's own
LAN.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "cluster/helm/security/crowdsec"
APP = ROOT / "cluster/argocd/projects/security/crowdsec.yaml"

WHITELIST_RANGES = ("192.168.1.0/24", "10.8.0.0/24", "10.42.0.0/16", "10.43.0.0/16")


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
        self.crowdsec = yaml.safe_load((CHART / "values.yaml").read_text())["crowdsec"]

    def test_the_chart_pins_the_upstream_version_and_the_image(self):
        dep = self.chart["dependencies"][0]
        self.assertEqual((dep["name"], dep["version"]), ("crowdsec", "0.24.3"))
        self.assertEqual(self.chart["appVersion"], "1.8.2")
        self.assertEqual(self.crowdsec["image"]["tag"], "v1.8.2")

    def test_the_agent_is_a_deployment_on_streaming_with_the_log_mounted(self):
        agent = self.crowdsec["agent"]
        self.assertTrue(agent["isDeployment"])
        self.assertEqual(agent["replicas"], 1)
        self.assertEqual(agent["nodeSelector"], {"kubernetes.io/hostname": "streaming"})
        volumes = {v["name"]: v for v in agent["extraVolumes"]}
        self.assertEqual(volumes["traefik-logs"]["hostPath"]["path"], "/var/log/traefik")
        mounts = {m["name"]: m for m in agent["extraVolumeMounts"]}
        self.assertTrue(mounts["traefik-logs"]["readOnly"])
        # One file, not the machine: the image's own /var/log mount is off.
        self.assertFalse(agent["hostVarLog"])
        self.assertEqual(len(agent["extraVolumeMounts"]), 1)

    def test_the_acquisition_sets_the_label_the_traefik_parser_filters_on(self):
        acquisition = self.crowdsec["agent"]["additionalAcquisition"]
        self.assertEqual(len(acquisition), 1)
        entry = acquisition[0]
        self.assertEqual(entry["filenames"], ["/var/log/traefik/access.log"])
        # `crowdsecurity/traefik-logs` filters on `evt.Parsed.program startsWith
        # 'traefik'`; the label is what fills that field for a file source.
        self.assertEqual(entry["labels"]["program"], "traefik")
        self.assertEqual(entry["labels"]["type"], "traefik")

    def test_the_collection_that_supplies_the_parser_is_installed(self):
        env = {e["name"]: e.get("value") for e in self.crowdsec["agent"]["env"]}
        self.assertIn("crowdsecurity/traefik", env["COLLECTIONS"])

    def test_the_lapi_keeps_its_state_on_local_path(self):
        lapi = self.crowdsec["lapi"]
        self.assertEqual(lapi["nodeSelector"], {"kubernetes.io/hostname": "nas"})
        for name in ("data", "config"):
            claim = lapi["persistentVolume"][name]
            self.assertTrue(claim["enabled"])
            self.assertEqual(claim["storageClassName"], "local-path")

    def test_the_bouncer_key_comes_from_a_secret_and_not_from_values(self):
        env = {e["name"]: e for e in self.crowdsec["lapi"]["env"]}
        ref = env["BOUNCER_KEY_firewall"]["valueFrom"]["secretKeyRef"]
        self.assertEqual((ref["name"], ref["key"]), ("crowdsec-bouncer-key", "api-key"))

    def test_the_central_api_is_off(self):
        # Without this the LAPI registers with CrowdSec's central API on first
        # start and subscribes to the shared blocklist — a decision to take after
        # measuring our own traffic, and the only part of this that talks to a
        # company.
        env = {e["name"]: e.get("value") for e in self.crowdsec["lapi"]["env"]}
        self.assertEqual(env["DISABLE_ONLINE_API"], "true")

    def test_both_workloads_listen_on_6060(self):
        # The chart's probes GET `/metrics` on port 6060 for both workloads, and
        # it configures no listener of its own. An agent without this block never
        # becomes Ready; a LAPI without it does the same.
        agent = self.crowdsec["config"]["agent_config.yaml.local"]
        self.assertIn("listen_port: 6060", agent)
        self.assertIn("listen_addr: 0.0.0.0", agent)
        lapi = self.crowdsec["config"]["config.yaml.local"]
        self.assertIn("listen_port: 6060", lapi)
        self.assertIn("auto_registration", lapi)

    def test_the_whitelist_covers_every_range_a_ban_must_never_reach(self):
        files = self.crowdsec["config"]["postoverflows"]["s01-whitelist"]
        self.assertEqual(len(files), 1)
        text = next(iter(files.values()))
        for cidr in WHITELIST_RANGES:
            self.assertIn(cidr, text)
        for node in ("192.168.1.41", "192.168.1.27", "192.168.1.46", "192.168.1.48"):
            self.assertIn(node, text)

    def test_the_whitelist_is_a_postoverflow_so_no_decision_is_ever_created(self):
        # The chart mounts `config.postoverflows.s01-whitelist` in the agent; a
        # whitelist written as a scenario or a parser would excuse a decision
        # instead of preventing it.
        self.assertNotIn("whitelist", self.crowdsec["config"].get("scenarios", {}))
        self.assertNotIn("whitelist", self.crowdsec["config"].get("parsers", {}))

    def test_the_application_deploys_into_security_and_has_no_auto_sync(self):
        app = yaml.safe_load(APP.read_text())
        self.assertEqual(app["spec"]["destination"]["namespace"], "security")
        self.assertEqual(app["spec"]["source"]["path"], "cluster/helm/security/crowdsec")
        self.assertNotIn("automated", yaml.safe_dump(app["spec"]["syncPolicy"]))

    def test_the_lan_service_is_the_only_new_port(self):
        template = (CHART / "templates/lapi-lan-service.yaml").read_text()
        self.assertIn("type: NodePort", template)
        self.assertIn("nodePort: 30500", template)


if __name__ == "__main__":
    unittest.main()
