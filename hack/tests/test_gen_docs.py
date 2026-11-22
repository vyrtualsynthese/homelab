import contextlib
import pathlib
import shutil
import sys
import tempfile
import unittest

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import gen_docs  # noqa: E402

FIXTURE_ROOT = HACK / "fixtures" / "docsgen"
FIXTURE_CHART = FIXTURE_ROOT / "cluster" / "helm" / "media-stack" / "fixture"


@contextlib.contextmanager
def temp_tree(files: dict[str, str]):
    root = pathlib.Path(tempfile.mkdtemp())
    try:
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        yield root
    finally:
        shutil.rmtree(root)


class ParseRoutesTests(unittest.TestCase):
    DOCS = """\
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: sonarr
spec:
  entryPoints: [websecure]
  routes:
    - kind: Rule
      match: Host(`sonarr.kub.local`)
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
        - name: kube-system-authentik-forwardauth@kubernetescrd
      services: [{name: sonarr, port: 8989}]
  tls:
    options: {name: internal-tls, namespace: kube-system}
---
apiVersion: traefik.io/v1alpha1
kind: IngressRouteTCP
metadata:
  name: syncthing-tcp
spec:
  routes: []
"""

    def test_a_route_yields_its_host_gate_and_tls(self):
        routes = gen_docs.parse_rendered_routes(self.DOCS, "sonarr", "media-stack")
        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertEqual(route.host, "sonarr.kub.local")
        self.assertEqual(route.middlewares, ("lan-only", "authentik-forwardauth"))
        self.assertEqual(route.tls, "internal CA")
        self.assertFalse(route.public)

    def test_a_document_with_no_host_is_skipped(self):
        self.assertEqual(len(gen_docs.parse_rendered_routes(self.DOCS, "x", "y")), 1)

    def test_a_web_only_route_answers_plain_http(self):
        docs = self.DOCS.replace("  entryPoints: [websecure]", "  entryPoints: [web]")
        docs = docs.replace("  tls:\n    options: {name: internal-tls, namespace: kube-system}\n", "")
        route = gen_docs.parse_rendered_routes(docs, "sonarr", "media-stack")[0]
        self.assertEqual(route.tls, "none")

    def test_an_empty_tls_block_on_a_public_name_is_the_wildcard(self):
        docs = self.DOCS.replace("sonarr.kub.local", "www.ashudev.com").replace(
            "  tls:\n    options: {name: internal-tls, namespace: kube-system}",
            "  tls: {}",
        )
        route = gen_docs.parse_rendered_routes(docs, "ashudev", "ashudev")[0]
        self.assertEqual(route.tls, "Let's Encrypt")

    def test_a_websecure_route_without_a_tls_block_falls_through_to_the_default_certificate(self):
        docs = self.DOCS.replace("  tls:\n    options: {name: internal-tls, namespace: kube-system}\n", "")
        route = gen_docs.parse_rendered_routes(docs, "sonarr", "media-stack")[0]
        self.assertEqual(route.tls, "internal CA")

    def test_a_public_name_with_the_internal_tls_option_is_lets_encrypt(self):
        docs = self.DOCS.replace("sonarr.kub.local", "dashy.ashudev.com")
        route = gen_docs.parse_rendered_routes(docs, "dashy", "dashy")[0]
        self.assertEqual(route.tls, "Let's Encrypt")

    def test_a_route_with_its_own_certificate_names_lets_encrypt(self):
        docs = self.DOCS.replace(
            "    options: {name: internal-tls, namespace: kube-system}",
            "    secretName: ashudev-wildcard",
        )
        route = gen_docs.parse_rendered_routes(docs, "sonarr", "media-stack")[0]
        self.assertEqual(route.tls, "Let's Encrypt")


@unittest.skipUnless(shutil.which("helm"), "helm is not installed")
class RenderChartTests(unittest.TestCase):
    def test_a_fixture_chart_renders_one_route(self):
        out = gen_docs.render_chart(FIXTURE_CHART)
        self.assertIn("Host(`fixture.kub.local`)", out)


class RenderRoutesTests(unittest.TestCase):
    def test_the_page_opens_with_the_generated_marker(self):
        page = gen_docs.render_routes(FIXTURE_ROOT)
        self.assertTrue(page.startswith(gen_docs.GENERATED_MARKER))

    def test_the_page_names_its_regenerate_command(self):
        self.assertIn("python3 hack/gen_docs.py --write --only routes", gen_docs.render_routes(FIXTURE_ROOT))

    def test_the_page_carries_no_timestamp(self):
        import re

        self.assertIsNone(re.search(r"20\d\d-\d\d-\d\d", gen_docs.render_routes(FIXTURE_ROOT)))

    def test_the_page_names_the_application_and_its_namespace(self):
        page = gen_docs.render_routes(FIXTURE_ROOT)
        self.assertIn("| `fixture.kub.local` |", page)
        self.assertIn("| `media-stack` |", page)


class RenderFailureTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("helm"), "helm is not installed")
    def test_a_chart_that_cannot_render_is_named(self):
        with temp_tree(
            {
                "Chart.yaml": (
                    "apiVersion: v2\nname: broken\n"
                    "dependencies: [{name: upstream, version: 1.0.0, repository: https://example.invalid}]\n"
                )
            }
        ) as root:
            with self.assertRaises(gen_docs.GenError) as caught:
                gen_docs.render_chart(root)
            self.assertIn("broken", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
