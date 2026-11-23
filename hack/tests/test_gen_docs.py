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

    def test_a_document_that_names_two_hosts_yields_one_row_each(self):
        docs = self.DOCS.replace(
            "    - kind: Rule\n",
            "    - kind: Rule\n      match: Host(`sonarr-anime.kub.local`)\n"
            "      middlewares:\n        - name: kube-system-lan-only@kubernetescrd\n"
            "      services: [{name: sonarr-anime, port: 8989}]\n    - kind: Rule\n",
        )
        routes = gen_docs.parse_rendered_routes(docs, "sonarr", "media-stack")
        self.assertEqual([route.host for route in routes], ["sonarr-anime.kub.local", "sonarr.kub.local"])
        self.assertEqual(routes[0].middlewares, ("lan-only",))
        self.assertEqual(routes[1].middlewares, ("lan-only", "authentik-forwardauth"))

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


class DeclaresARouteTests(unittest.TestCase):
    def test_a_dashboard_values_block_declares_a_route(self):
        with temp_tree(
            {
                "cluster/helm/x/Chart.yaml": "apiVersion: v2\nname: x\nversion: 0.1.0\n",
                "cluster/helm/x/values.yaml": (
                    "traefik:\n  ingressRoute:\n    dashboard:\n      enabled: true\n"
                ),
            }
        ) as root:
            self.assertTrue(gen_docs._declares_a_route(root / "cluster/helm/x"))

    def test_a_disabled_dashboard_block_declares_no_route(self):
        with temp_tree(
            {
                "cluster/helm/x/Chart.yaml": "apiVersion: v2\nname: x\nversion: 0.1.0\n",
                "cluster/helm/x/values.yaml": (
                    "traefik:\n  ingressRoute:\n    dashboard:\n      enabled: false\n"
                ),
            }
        ) as root:
            self.assertFalse(gen_docs._declares_a_route(root / "cluster/helm/x"))

    def test_an_enabled_dashboard_beside_a_disabled_one_is_found(self):
        with temp_tree(
            {
                "cluster/helm/x/Chart.yaml": "apiVersion: v2\nname: x\nversion: 0.1.0\n",
                "cluster/helm/x/values.yaml": (
                    "traefik:\n"
                    "  ingressRoute:\n"
                    "    dashboard:\n"
                    "      enabled: false\n"
                    "  extra:\n"
                    "    ingressRoute:\n"
                    "      dashboard:\n"
                    "        enabled: true\n"
                ),
            }
        ) as root:
            self.assertTrue(gen_docs._declares_a_route(root / "cluster/helm/x"))


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

    def test_an_application_is_named_by_metadata_name_not_the_file_stem(self):
        with temp_tree(
            {
                "cluster/argocd/projects/news/freshrss.yaml": (
                    "apiVersion: argoproj.io/v1alpha1\nkind: Application\n"
                    "metadata: {name: news-freshrss}\n"
                    "spec: {destination: {namespace: news}, source: {path: cluster/helm/news}}\n"
                ),
            }
        ) as root:
            self.assertIn("news-freshrss", gen_docs.applications(root))

    def test_an_application_with_no_chart_in_the_repository_is_named_in_the_page(self):
        with temp_tree(
            {
                "cluster/argocd/projects/storage/volumes.yaml": (
                    "apiVersion: argoproj.io/v1alpha1\nkind: Application\n"
                    "metadata: {name: volume-provisioner}\n"
                    "spec: {destination: {namespace: storage}, source: {repoURL: x, path: deploy/chart}}\n"
                ),
            }
        ) as root:
            page = gen_docs.render_routes(root)
        self.assertIn("## Applications this table cannot see", page)
        self.assertIn("- `volume-provisioner`", page)

    def test_a_values_only_directory_that_declares_a_route_fails_loudly(self):
        with temp_tree(
            {
                "cluster/argocd/projects/x/app.yaml": (
                    "apiVersion: argoproj.io/v1alpha1\nkind: Application\n"
                    "metadata: {name: app}\n"
                    "spec: {destination: {namespace: x}, source: {path: cluster/helm/x}}\n"
                ),
                "cluster/helm/x/values.yaml": "extraObjects: [{kind: IngressRoute}]\n",
            }
        ) as root:
            with self.assertRaises(gen_docs.GenError) as caught:
                gen_docs.render_routes(root)
        self.assertIn("no Chart.yaml", str(caught.exception))


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


class CoverageTests(unittest.TestCase):
    NODES = {
        "streams": [{"name": "nodes-nas"}],
        "nodes": {
            "nas": {
                "schedule": "30 4 * * *",
                "claims": [{"claim": "grafana", "namespace": "monitoring", "kind": "sqlite"}],
            }
        },
    }
    NFS = {
        "streams": [{"name": "nfs"}],
        "nfs": {"schedule": "0 6 * * *", "paths": [{"tag": "traefik", "path": "/mnt/storage/traefik"}]},
    }

    def test_a_claim_row_carries_its_stream_and_node(self):
        self.assertEqual(
            gen_docs.coverage_rows(self.NODES),
            [gen_docs.CoverageRow("nodes-nas", "30 4 * * *", "nas", "grafana", "monitoring", "sqlite")],
        )

    def test_an_nfs_path_is_a_row_named_by_its_tag(self):
        self.assertEqual(
            gen_docs.coverage_rows(self.NFS),
            [gen_docs.CoverageRow("nfs", "0 6 * * *", "nfs", "traefik", "-", "files")],
        )

    def test_a_stream_with_no_rows_emits_none(self):
        self.assertEqual(gen_docs.coverage_rows({"streams": [{"name": "nodes-nuc0"}], "nodes": {}}), [])

    def test_the_page_is_generated_from_the_matrix(self):
        with temp_tree(
            {
                "cluster/helm/backup/values.yaml": "streams:\n  - name: nodes-nas\nnodes:\n  nas:\n    schedule: 30 4 * * *\n    claims:\n      - claim: grafana\n        namespace: monitoring\n        kind: sqlite\n"
            }
        ) as root:
            page = gen_docs.render_coverage(root)
        self.assertTrue(page.startswith(gen_docs.GENERATED_MARKER))
        self.assertIn("| `grafana` |", page)
        self.assertIn("python3 hack/gen_docs.py --write --only coverage", page)


if __name__ == "__main__":
    unittest.main()
