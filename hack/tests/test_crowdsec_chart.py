"""The chart's values, checked where a wrong value is expensive.

Three of them are load bearing and silent when wrong: the agent must be a
Deployment pinned to the node that owns the log file, the acquisition must set
the label the Traefik parser filters on, and the whitelist must cover the four
ranges and the four node addresses before any of this bans the operator's own
LAN.
"""

import ipaddress
import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "cluster/helm/security/crowdsec"
APP = ROOT / "cluster/argocd/projects/security/crowdsec.yaml"

WHITELIST_RANGES = ("192.168.1.0/24", "10.8.0.0/24", "10.42.0.0/16", "10.43.0.0/16")


def without_comments(text):
    """A template's comment explains what it must not render; only the rest counts."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


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

    def test_the_lapi_secrets_come_from_one_made_by_hand(self):
        # The chart mints `csLapiSecret` and `registrationToken` at render time
        # and keeps the live values through `lookup`, which ArgoCD cannot do: it
        # renders without a cluster connection, so every refresh produced new
        # values and the Application never settled. Pointing it at a Secret made
        # by hand is what stops the churn.
        external = self.crowdsec["secrets"]["externalSecret"]
        self.assertEqual(external["name"], "crowdsec-lapi-secrets")
        # Unset, so the keys are the chart's defaults and the Secret in the
        # cluster holds exactly what its templates read.
        self.assertFalse(external.get("csLapiSecretKey"))
        self.assertFalse(external.get("registrationTokenKey"))
        # The values themselves are not in git, and neither is a stand-in for
        # them: the chart's own minting path stays off.
        self.assertFalse(self.crowdsec["lapi"].get("secrets"))

    def test_the_central_api_is_on_and_the_identity_lives_on_the_config_claim(self):
        # CAPI is the point of the workstream: the blocklist is what refuses an
        # address before it sends a request. No registration Job is rendered
        # because the chart only renders one when the config claim is *not*
        # persistent; with it, the LAPI's start script registers and writes
        # `online_api_credentials.yaml` to the claim, which the backup matrix
        # already covers, so a rebuild keeps the identity.
        env = {e["name"]: e.get("value") for e in self.crowdsec["lapi"]["env"]}
        self.assertNotIn("DISABLE_ONLINE_API", env)
        self.assertTrue(self.crowdsec["lapi"]["persistentVolume"]["config"]["enabled"])

    def test_the_blocklist_cannot_insert_a_lan_address(self):
        # A blocklist decision never passes the postoverflow whitelist: that runs
        # before a decision exists from a local alert, and CAPI inserts into the
        # database directly. This file is read at pull time instead, so the
        # address never enters the table — the only control that keeps the
        # operator's rule true for a decision nobody here made.
        raw = self.crowdsec["config"]["capi_whitelists.yaml"]
        # `%+v` on a string renders the string; on a map it renders Go syntax.
        self.assertIsInstance(raw, str)
        capi = yaml.safe_load(raw)
        self.assertEqual(set(capi["cidrs"]), set(WHITELIST_RANGES))
        self.assertEqual(
            set(capi["ips"]),
            {"192.168.1.41", "192.168.1.27", "192.168.1.46", "192.168.1.48"},
        )

    def test_the_two_whitelists_name_the_same_addresses(self):
        post = yaml.safe_load(
            self.crowdsec["config"]["postoverflows"]["s01-whitelist"]["whitelist-lan.yaml"]
        )["whitelist"]
        capi = yaml.safe_load(self.crowdsec["config"]["capi_whitelists.yaml"])
        self.assertEqual(set(capi["cidrs"]), set(post["cidr"]))
        self.assertEqual(set(capi["ips"]), set(post["ip"]))

    def test_the_engine_does_not_share_custom_or_tainted_alerts(self):
        # The official hub scenarios still leave — that is how the full list is
        # earned, and the design states it (§4.3). A local parser or a modified
        # scenario counts for nothing upstream and there is no reason to send it.
        raw = self.crowdsec["config"]["console.yaml"]
        self.assertIsInstance(raw, str)
        console = yaml.safe_load(raw)
        self.assertIs(console["share_custom"], False)
        self.assertIs(console["share_tainted"], False)

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

    def test_the_host_service_is_a_clusterip_and_publishes_nothing(self):
        # A NodePort answers on every node's LAN address, which puts the LAPI's
        # management surface in front of every device on the house network. A
        # ClusterIP answers the node that runs the bouncer and nothing else.
        spec = without_comments((CHART / "templates/lapi-host-service.yaml").read_text())
        self.assertIn("type: ClusterIP", spec)
        self.assertNotIn("NodePort", spec)
        self.assertNotIn("nodePort", spec)
        self.assertNotIn("192.168.", spec)

    def test_the_host_service_pins_its_address_inside_the_service_network(self):
        # The address is written into the bouncer's host configuration, so it
        # must be fixed in this file rather than allocated at creation.
        spec = without_comments((CHART / "templates/lapi-host-service.yaml").read_text())
        address = re.search(r"^  clusterIP: (\S+)$", spec, re.MULTILINE).group(1)
        self.assertIn(ipaddress.ip_address(address), ipaddress.ip_network("10.43.0.0/16"))

    def test_no_template_publishes_a_port_on_a_node(self):
        templates = sorted((CHART / "templates").glob("*.yaml"))
        self.assertTrue(templates)
        for path in templates:
            spec = without_comments(path.read_text())
            self.assertNotIn("NodePort", spec, path.name)
            self.assertNotIn("nodePort", spec, path.name)

    def test_auto_registration_is_open_to_the_agents_node_subnet_only(self):
        # A registered machine can push alerts that become decisions, so the
        # addresses allowed to register are the agent's and no others: not the
        # LAN, not the tunnel inside it, not the pod network at large.
        config = yaml.safe_load(self.crowdsec["config"]["config.yaml.local"])
        ranges = config["api"]["server"]["auto_registration"]["allowed_ranges"]
        self.assertEqual(ranges, ["10.42.3.0/24"])
        for wide in ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "127.0.0.1/32"):
            self.assertNotIn(wide, ranges)


if __name__ == "__main__":
    unittest.main()
