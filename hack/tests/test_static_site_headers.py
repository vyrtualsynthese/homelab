"""The two static-site charts, and the hand-applied middlewares they name.

Every claim here is about what the cluster will answer later, and none of them
needs a cluster to read:

* `security-static` carries a content policy whose `img-src` allows `data:`,
  because `ashuvidz.com`'s stylesheet paints its noise texture from a data URI;
  dropping the scheme removes the background with no error and nothing in the
  console naming the cause;
* both website charts name `security-static` next to `security`, or the site
  answers without a policy at all;
* each `www` pair is two routers, because a router carrying a `tls` block is
  attached to the TLS entry points only — the mistake that answered
  `http://www` with Traefik's own 404 (tasks 615 and 623);
* every hand-applied middleware in `cluster/infrastructure/traefik/middleware/`
  names `kube-system`: `kubectl apply -f <file>` without `-n` would create a
  second object in `default` and leave the live one on its old spec (task 625).
"""

import pathlib
import subprocess
import unittest

import yaml

CLUSTER = pathlib.Path(__file__).resolve().parents[2] / "cluster"
MIDDLEWARE = CLUSTER / "infrastructure" / "traefik" / "middleware"
WEBSITES = ("ashudev-website", "ashuvidz-website")


def render(chart):
    """The chart's documents, rendered so that a template error fails the suite."""
    path = CLUSTER / "helm" / chart
    out = subprocess.run(
        ["helm", "template", "site", str(path), "-f", str(path / "values.yaml"), "--namespace", "media-stack"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def values(chart):
    return yaml.safe_load((CLUSTER / "helm" / chart / "values.yaml").read_text())


def routes(chart):
    return {d["metadata"]["name"]: d for d in render(chart) if d["kind"] == "IngressRoute"}


def policy():
    document = yaml.safe_load((MIDDLEWARE / "security-static.yaml").read_text())
    return document["spec"]["headers"]["contentSecurityPolicy"]


class ContentPolicyTests(unittest.TestCase):
    def test_the_policy_allows_data_uri_images(self):
        self.assertIn("img-src 'self' data:", policy())

    def test_the_policy_denies_what_it_does_not_name(self):
        self.assertIn("default-src 'none'", policy())

    def test_every_static_site_names_the_policy(self):
        for chart in WEBSITES:
            with self.subTest(chart=chart):
                named = [
                    (m["name"], m["namespace"])
                    for route in routes(chart).values()
                    for rule in route["spec"]["routes"]
                    for m in rule.get("middlewares", [])
                ]
                self.assertIn(("security-static", "kube-system"), named)


class WwwRouterTests(unittest.TestCase):
    def test_the_https_router_owns_the_certificate(self):
        for chart in WEBSITES:
            if not values(chart)["ingressRoute"]["www"]["enabled"]:
                continue
            with self.subTest(chart=chart):
                route = next(r for n, r in routes(chart).items() if n.endswith("-www"))
                self.assertEqual(route["spec"]["entryPoints"], ["websecure"])
                self.assertIn("tls", route["spec"])

    def test_the_http_router_carries_no_tls(self):
        for chart in WEBSITES:
            if not values(chart)["ingressRoute"]["www"]["enabled"]:
                continue
            with self.subTest(chart=chart):
                route = next(r for n, r in routes(chart).items() if n.endswith("-www-web"))
                self.assertEqual(route["spec"]["entryPoints"], ["web"])
                self.assertNotIn("tls", route["spec"])

    def test_both_routers_send_the_same_redirect(self):
        for chart in WEBSITES:
            if not values(chart)["ingressRoute"]["www"]["enabled"]:
                continue
            routers = routes(chart)
            with self.subTest(chart=chart):
                middlewares = {
                    n: [m["name"] for m in routers[n]["spec"]["routes"][0]["middlewares"]]
                    for n in routers
                    if n.endswith("-www") or n.endswith("-www-web")
                }
                self.assertEqual(len(middlewares), 2)
                self.assertEqual(len({tuple(v) for v in middlewares.values()}), 1)


class HandAppliedMiddlewareTests(unittest.TestCase):
    def test_every_middleware_file_names_its_namespace(self):
        files = sorted(MIDDLEWARE.glob("*.yaml"))
        self.assertTrue(files, "no middleware files found")
        for path in files:
            for document in yaml.safe_load_all(path.read_text()):
                if document is None:
                    continue
                with self.subTest(file=path.name):
                    self.assertEqual(document["metadata"].get("namespace"), "kube-system")


if __name__ == "__main__":
    unittest.main()
