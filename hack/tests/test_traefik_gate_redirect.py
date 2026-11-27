"""The legacy redirect is the only route that answers `*.kub.local` once the charts are renamed.
It is rendered, so it can be read without a cluster; it must stay gated, and it must upgrade to TLS
in one hop for every name that has a `websecure` router, while leaving the two plain-HTTP names on
`http`."""

import pathlib
import subprocess
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CHART = REPO / "cluster/helm/platform/traefik-gate"


def render():
    out = subprocess.run(
        ["helm", "template", "test", str(CHART), "-f", str(CHART / "values.yaml")],
        check=True, capture_output=True, text=True,
    )
    return [d for d in yaml.safe_load_all(out.stdout) if d]


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.docs = render()
        self.routes = {
            tuple(d["spec"]["entryPoints"]): d
            for d in self.docs
            if d["kind"] == "IngressRoute"
        }
        self.middlewares = {d["metadata"]["name"]: d for d in self.docs if d["kind"] == "Middleware"}

    def test_one_router_per_entry_point(self):
        # The entry-point keyed dict in setUp collapses a duplicate, so count the objects themselves:
        # two `web` routers would leave the dict with one key and hide the extra route.
        entrypoints = sorted(
            tuple(d["spec"]["entryPoints"]) for d in self.docs if d["kind"] == "IngressRoute"
        )
        self.assertEqual(entrypoints, [("web",), ("websecure",)])

    def test_the_plain_names_have_their_own_rule_and_win(self):
        rules = self.routes[("web",)]["spec"]["routes"]
        plain = [r for r in rules if "pgadmin" in r["match"]]
        self.assertEqual(len(plain), 1)
        self.assertEqual(
            plain[0]["match"],
            "HostRegexp(`^pgadmin\\.kub\\.local$|^prometheus\\.kub\\.local$`)",
        )
        general = [r for r in rules if r is not plain[0]]
        self.assertEqual(len(general), 1)
        self.assertGreater(plain[0]["priority"], general[0]["priority"])

    def test_the_general_rule_is_an_exact_one_label_wildcard(self):
        for entrypoint in (("web",), ("websecure",)):
            rules = [
                r for r in self.routes[entrypoint]["spec"]["routes"] if "pgadmin" not in r["match"]
            ]
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]["match"], "HostRegexp(`^[a-z0-9-]+\\.kub\\.local$`)")

    def test_the_general_rules_sit_below_every_exact_route(self):
        for entrypoint in (("web",), ("websecure",)):
            for rule in self.routes[entrypoint]["spec"]["routes"]:
                if "pgadmin" not in rule["match"]:
                    self.assertEqual(rule["priority"], 1)

    def test_every_rule_is_gated_before_it_redirects(self):
        for router in self.routes.values():
            for rule in router["spec"]["routes"]:
                names = [m["name"] for m in rule["middlewares"]]
                self.assertEqual(names[0], "kube-system-lan-only@kubernetescrd")
                self.assertIn(
                    names[1],
                    {
                        "kube-system-redirect-to-internal-tls@kubernetescrd",
                        "kube-system-redirect-to-internal-plain@kubernetescrd",
                    },
                )
                self.assertEqual(len(names), 2, "the gate and the redirect, nothing else")

    def test_the_general_rules_use_the_tls_middleware(self):
        """Pinning which middleware serves the wildcard is the point: the plain one would send
        `http://sonarr.kub.local` to `http://sonarr.kub.internal`, and the application's own `web`
        router would then add a second redirect."""
        for entrypoint in (("web",), ("websecure",)):
            general = [
                r for r in self.routes[entrypoint]["spec"]["routes"] if "pgadmin" not in r["match"]
            ]
            self.assertEqual(len(general), 1)
            self.assertEqual(
                [m["name"] for m in general[0]["middlewares"]],
                [
                    "kube-system-lan-only@kubernetescrd",
                    "kube-system-redirect-to-internal-tls@kubernetescrd",
                ],
            )

    def test_the_plain_rule_uses_the_plain_middleware(self):
        plain = [
            r for r in self.routes[("web",)]["spec"]["routes"] if "pgadmin" in r["match"]
        ][0]
        self.assertEqual(
            [m["name"] for m in plain["middlewares"]][1],
            "kube-system-redirect-to-internal-plain@kubernetescrd",
        )

    def test_the_tls_router_uses_the_default_certificate(self):
        self.assertNotIn("tls", self.routes[("web",)]["spec"])
        tls = self.routes[("websecure",)]["spec"]["tls"]
        self.assertNotIn("secretName", tls)
        self.assertNotIn("certResolver", tls)

    def test_the_redirects_upgrade_to_tls_in_one_hop(self):
        """`http://sonarr.kub.local` must land on `https://sonarr.kub.internal` directly: replaying
        the scheme would send it to `http`, and the app's own router would add a second hop."""
        for name, scheme in (
            ("redirect-to-internal-tls", "https"),
            ("redirect-to-internal-plain", "http"),
        ):
            redirect = self.middlewares[name]["spec"]["redirectRegex"]
            self.assertEqual(redirect["regex"], r"^https?://([a-z0-9-]+)\.kub\.local(.*)$")
            self.assertEqual(redirect["replacement"], f"{scheme}://${{1}}.kub.internal${{2}}")
            self.assertFalse(
                redirect["permanent"], "a POST client must not be taught to resend as GET"
            )
