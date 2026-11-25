"""The CrowdSec web UI's chart, checked where a wrong value is expensive.

The app is a second, Grafana-independent view of the LAPI. Four things it depends on fail
silently when wrong: the LAPI's address, the two Secret keys it logs in with, the OIDC
issuer and client, and the exact route. The rest is the contract every hand-written chart
in this repo meets (`docs/charts/README.md`).
"""

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "cluster/helm/security/crowdsec-web-ui"
APP = ROOT / "cluster/argocd/projects/security/crowdsec-web-ui.yaml"
BACKUP = ROOT / "cluster/helm/backup/values.yaml"
CATALOGUE = ROOT / "docs/how-it-works/applications.md"
BLUEPRINT = ROOT / "cluster/helm/authentik/files/app-crowdsec-web-ui.yaml"

HOST = "crowdsec.kub.local"
CALLBACK = f"https://{HOST}/api/auth/oidc/callback"


def env(values):
    return {name: str(value) for name, value in values.items()}


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
        self.values = yaml.safe_load((CHART / "values.yaml").read_text())

    def test_the_chart_pins_the_image_and_app_version(self):
        self.assertEqual(self.chart["name"], "crowdsec-web-ui")
        self.assertNotIn("dependencies", self.chart)
        tag = self.values["image"]["tag"]
        self.assertEqual(tag, "2026.11.12")
        self.assertEqual(self.chart["appVersion"], tag)

    def test_the_image_is_not_a_floating_tag(self):
        for path in (CHART / "values.yaml", *sorted((CHART / "templates").glob("*.yaml"))):
            self.assertNotIn("latest", path.read_text(), msg=path.name)

    def test_the_instance_points_at_the_lapi_service(self):
        values = env(self.values["env"])
        self.assertEqual(values["CONFIG_INSTANCES_0_LAPI_URL"], "http://crowdsec-lapi:8080")
        self.assertEqual(values["CONFIG_INSTANCES_0_LAPI_AUTH_USERNAME"], "crowdsec-web-ui")
        # A metrics tab would need the LAPI's 6060, which no Service exposes: Prometheus
        # scrapes that port on the pod IP. Pointing the UI at the Service would leave the
        # tab permanently empty, so no metrics entry is configured.
        self.assertNotIn("CONFIG_INSTANCES_0_METRICS_0_URL", values)

    def test_the_two_secrets_come_from_a_secret_and_never_from_values(self):
        references = {
            entry["name"]: entry["valueFrom"]["secretKeyRef"]
            for entry in self.values["extraEnv"]
        }
        self.assertEqual(
            references["CONFIG_INSTANCES_0_LAPI_AUTH_PASSWORD"],
            {"name": "crowdsec-web-ui", "key": "lapi-password"},
        )
        self.assertEqual(
            references["CONFIG_AUTH_OIDC_CLIENT_SECRET"],
            {"name": "crowdsec-web-ui", "key": "oidc-client-secret"},
        )
        plain = env(self.values["env"])
        self.assertNotIn("CONFIG_INSTANCES_0_LAPI_AUTH_PASSWORD", plain)
        self.assertNotIn("CONFIG_AUTH_OIDC_CLIENT_SECRET", plain)

    def test_oidc_is_the_only_login(self):
        values = env(self.values["env"])
        self.assertEqual(
            values["CONFIG_AUTH_OIDC_ISSUER_URL"],
            "https://authentik.kub.home.ashudev.com/application/o/crowdsec-web-ui/",
        )
        self.assertEqual(values["CONFIG_AUTH_OIDC_CLIENT_ID"], "crowdsec-web-ui")
        self.assertEqual(values["CONFIG_AUTH_OIDC_ADMIN_GROUPS_0"], "authentik Admins")
        self.assertEqual(values["CONFIG_AUTH_OIDC_UNMATCHED_ROLE"], "deny")
        self.assertEqual(values["CONFIG_AUTH_PASSWORD_LOGIN_ENABLED"], "false")
        self.assertEqual(values["CONFIG_AUTH_OIDC_AUTO_LOGIN"], "true")

    def test_the_route_is_internal_tls_on_kub_local_and_lan_only(self):
        route = self.values["ingressRoute"]
        self.assertEqual(route["host"], HOST)
        self.assertEqual(route["entryPoints"], ["websecure"])
        self.assertEqual(
            [(m["name"], m["namespace"]) for m in route["middlewares"]],
            [("lan-only", "kube-system")],
        )
        names = [m["name"] for m in route["middlewares"]]
        self.assertNotIn("authentik-forwardauth", names)
        self.assertTrue(route["tls"]["enabled"])
        self.assertEqual(route["tls"]["options"], "internal-tls")
        self.assertEqual(route["tls"]["secretName"], "")
        self.assertEqual(route["tls"]["certResolver"], "")
        self.assertTrue(route["redirectToHttps"])

    def test_the_pod_is_pinned_to_nuc0_with_a_local_path_claim(self):
        self.assertEqual(
            self.values["nodeSelector"], {"kubernetes.io/hostname": "nuc0"}
        )
        self.assertEqual(self.values["strategy"], "Recreate")
        storage = self.values["storage"]
        self.assertTrue(storage["enabled"])
        self.assertEqual(storage["className"], "local-path")
        self.assertEqual(storage["mountPath"], "/app/data")
        # The claim mirrors a 168h alert lookback, not just settings, so it is larger
        # than the 128Mi house standard.
        self.assertEqual(storage["size"], "1Gi")

    def test_the_probes_ask_the_app_own_health_endpoint(self):
        for probe in ("startup", "liveness", "readiness"):
            self.assertEqual(self.values["probes"][probe]["path"], "/api/health")

    def test_the_container_runs_as_the_images_own_non_root_user(self):
        context = self.values["securityContext"]
        self.assertTrue(context["runAsNonRoot"])
        self.assertEqual(context["runAsUser"], 1000)
        self.assertFalse(context["allowPrivilegeEscalation"])
        self.assertEqual(context["capabilities"]["drop"], ["ALL"])
        self.assertEqual(
            self.values["podSecurityContext"]["seccompProfile"]["type"],
            "RuntimeDefault",
        )

    def test_every_container_has_a_memory_request_and_limit(self):
        resources = self.values["resources"]
        self.assertIn("memory", resources["requests"])
        self.assertIn("memory", resources["limits"])

    def test_no_copy_paste_leftovers_and_no_tabs(self):
        for path in [CHART / "values.yaml", *sorted((CHART / "templates").iterdir())]:
            text = path.read_text()
            self.assertNotRegex(text, r"(?i)autobrr", msg=path.name)
            self.assertNotIn("\t", text, msg=path.name)
            self.assertNotIn("networking.k8s.io", text, msg=path.name)

    def test_the_application_names_the_chart(self):
        application = yaml.safe_load(APP.read_text())
        self.assertEqual(application["kind"], "Application")
        self.assertEqual(
            application["spec"]["destination"]["namespace"], "security"
        )
        self.assertEqual(
            application["spec"]["source"]["path"],
            "cluster/helm/security/crowdsec-web-ui",
        )
        self.assertNotIn("automated", application["spec"]["syncPolicy"])

    def test_the_claim_is_in_the_nuc0_backup_matrix(self):
        values = yaml.safe_load(BACKUP.read_text())
        claims = values["nodes"]["nuc0"]["claims"]
        row = next(c for c in claims if c["claim"] == "crowdsec-web-ui-config")
        self.assertEqual(row["namespace"], "security")
        self.assertEqual(row["kind"], "sqlite")

    def test_the_catalogue_names_the_application(self):
        rows = {
            line.split("|")[1].strip().strip("`")
            for line in CATALOGUE.read_text().splitlines()
            if line.startswith("| `")
        }
        self.assertIn("crowdsec-web-ui", rows)


class BlueprintTests(unittest.TestCase):
    """The provider and the chart are two halves of one login; a mismatch is a loop."""

    def setUp(self):
        self.text = BLUEPRINT.read_text()
        self.values = yaml.safe_load((CHART / "values.yaml").read_text())

    def test_the_registered_callback_is_the_chart_host(self):
        # The app builds this from the request's Host and X-Forwarded-* headers.
        # authentik compares the whole string, so both copies are pinned here.
        self.assertEqual(re.findall(r"^\s+url:\s*(\S+)\s*$", self.text, re.MULTILINE), [CALLBACK])

    def test_the_client_id_matches_the_chart(self):
        client_id = self.values["env"]["CONFIG_AUTH_OIDC_CLIENT_ID"]
        self.assertIn(f"client_id: {client_id}", self.text)
        self.assertIn(f"https://authentik.kub.home.ashudev.com/application/o/{client_id}/",
                      self.values["env"]["CONFIG_AUTH_OIDC_ISSUER_URL"])

    def test_it_is_an_oauth2_provider_not_a_proxy_one(self):
        # A proxy provider would have to be listed on the embedded outpost; this
        # app authenticates itself, so the outpost must never see it.
        self.assertIn("authentik_providers_oauth2.oauth2provider", self.text)
        self.assertNotIn("authentik_providers_proxy.proxyprovider", self.text)

    def test_the_application_launches_the_internal_host_for_admins_only(self):
        self.assertIn("meta_launch_url: https://crowdsec.kub.local/", self.text)
        self.assertIn(
            "group: !Find [authentik_core.group, [name, authentik Admins]]", self.text
        )
