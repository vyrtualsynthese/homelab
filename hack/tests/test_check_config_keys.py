import pathlib
import sys
import tarfile
import tempfile
import unittest

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import check_config_keys  # noqa: E402


def configmap(name, data):
    return {"kind": "ConfigMap", "metadata": {"name": name}, "data": data}


class ComparisonTests(unittest.TestCase):
    def test_a_declared_key_passes(self):
        rendered = "server:\n  cacheDir: /data/cache\n"
        self.assertEqual(check_config_keys.undeclared_keys(rendered, {"server": {"cacheDir": "tmp"}}), [])

    def test_an_unknown_key_is_reported_with_its_line(self):
        rendered = "server:\n  cacheDir: /data/cache\n  cacheDirMaxSizeMB: 100\n"
        self.assertEqual(
            check_config_keys.undeclared_keys(rendered, {"server": {"cacheDir": "tmp"}}),
            [(3, "server.cacheDirMaxSizeMB")],
        )

    def test_an_unknown_key_below_a_nested_map_is_named_by_its_path(self):
        rendered = "auth:\n  methods:\n    oidc:\n      bogus: true\n"
        schema = {"auth": {"methods": {"oidc": {"enabled": False}}}}
        self.assertEqual(
            check_config_keys.undeclared_keys(rendered, schema),
            [(4, "auth.methods.oidc.bogus")],
        )

    def test_an_unknown_key_in_a_list_element_is_reported(self):
        rendered = "server:\n  sources:\n    - path: /srv/a\n      name: a\n      wrongKey: 1\n"
        schema = {"server": {"sources": [{"path": "", "name": ""}]}}
        self.assertEqual(
            check_config_keys.undeclared_keys(rendered, schema),
            [(5, "server.sources[].wrongKey")],
        )

    def test_every_list_element_is_checked(self):
        rendered = (
            "frontend:\n  externalLinks:\n"
            "    - text: a\n      title: a\n      url: a\n"
            "    - text: b\n      title: b\n      url: b\n      extra: c\n"
        )
        schema = {"frontend": {"externalLinks": [{"text": "", "title": "", "url": ""}]}}
        self.assertEqual(
            check_config_keys.undeclared_keys(rendered, schema),
            [(9, "frontend.externalLinks[].extra")],
        )

    def test_a_list_under_a_scalar_declaration_is_accepted(self):
        rendered = "auth:\n  methods:\n    oidc:\n      userGroups:\n        - authentik Admins\n"
        schema = {"auth": {"methods": {"oidc": {"userGroups": None}}}}
        self.assertEqual(check_config_keys.undeclared_keys(rendered, schema), [])

    def test_an_unknown_key_below_two_nested_lists_is_reported(self):
        rendered = (
            "server:\n  sources:\n    - config:\n        rules:\n"
            "          - folderName: x\n            bogus: 1\n"
        )
        schema = {"server": {"sources": [{"config": {"rules": [{"folderName": ""}]}}]}}
        self.assertEqual(
            check_config_keys.undeclared_keys(rendered, schema),
            [(6, "server.sources[].config.rules[].bogus")],
        )


class DiscoveryTests(unittest.TestCase):
    def test_the_configmap_holding_the_key_is_read(self):
        documents = [configmap("other", {"x": "1"}), configmap("app", {"config.yaml": "a: 1\n"})]
        self.assertEqual(check_config_keys.config_text(documents, "config.yaml"), "a: 1\n")

    def test_no_configmap_holding_the_key_is_a_setup_error(self):
        with self.assertRaises(check_config_keys.CheckError) as caught:
            check_config_keys.config_text([configmap("other", {"x": "1"})], "config.yaml")
        self.assertIn("config.yaml", str(caught.exception))

    def test_two_configmaps_holding_the_key_is_a_setup_error(self):
        documents = [configmap("a", {"config.yaml": "{}\n"}), configmap("b", {"config.yaml": "{}\n"})]
        with self.assertRaises(check_config_keys.CheckError):
            check_config_keys.config_text(documents, "config.yaml")


class DependencyTests(unittest.TestCase):
    def values(self):
        return "config:\n  server:\n    cacheDir: tmp\n"

    def test_values_come_from_an_extracted_dependency(self):
        charts = pathlib.Path(tempfile.mkdtemp()) / "charts"
        chart = charts / "filebrowser-quantum"
        chart.mkdir(parents=True)
        (chart / "Chart.yaml").write_text("name: filebrowser-quantum\nversion: 2.0.2-stable\n")
        (chart / "values.yaml").write_text(self.values())
        text, name, version = check_config_keys.dependency_chart(charts, "filebrowser-quantum")
        self.assertEqual((text, name, version), (self.values(), "filebrowser-quantum", "2.0.2-stable"))

    def test_values_come_from_a_packed_dependency(self):
        charts = pathlib.Path(tempfile.mkdtemp()) / "charts"
        charts.mkdir(parents=True)
        source = pathlib.Path(tempfile.mkdtemp()) / "filebrowser-quantum"
        source.mkdir()
        (source / "Chart.yaml").write_text("name: filebrowser-quantum\nversion: 2.0.2-stable\n")
        (source / "values.yaml").write_text(self.values())
        with tarfile.open(charts / "filebrowser-quantum-2.0.2-stable.tgz", "w:gz") as archive:
            archive.add(source, arcname="filebrowser-quantum")
        text, name, version = check_config_keys.dependency_chart(charts, "filebrowser-quantum")
        self.assertEqual((text, name, version), (self.values(), "filebrowser-quantum", "2.0.2-stable"))

    def test_a_missing_dependency_names_the_build_command(self):
        charts = pathlib.Path(tempfile.mkdtemp()) / "charts"
        charts.mkdir(parents=True)
        with self.assertRaises(check_config_keys.CheckError) as caught:
            check_config_keys.dependency_chart(charts, "filebrowser-quantum")
        self.assertIn("helm dependency build", str(caught.exception))

    def test_the_schema_subtree_is_returned(self):
        self.assertEqual(
            check_config_keys.schema_tree(self.values(), "config"),
            {"server": {"cacheDir": "tmp"}},
        )

    def test_a_missing_schema_subtree_is_a_setup_error(self):
        with self.assertRaises(check_config_keys.CheckError):
            check_config_keys.schema_tree(self.values(), "nope")


class EntryTests(unittest.TestCase):
    def test_an_entry_reports_the_chart_the_key_and_the_dependency(self):
        root = pathlib.Path(tempfile.mkdtemp())
        chart = root / "cluster/helm/app"
        (chart / "charts/filebrowser-quantum").mkdir(parents=True)
        (chart / "Chart.yaml").write_text("name: app\nversion: 0.1.0\n")
        (chart / "values.yaml").write_text("filebrowser-quantum: {}\n")
        (chart / "charts/filebrowser-quantum/Chart.yaml").write_text(
            "name: filebrowser-quantum\nversion: 2.0.2-stable\n")
        (chart / "charts/filebrowser-quantum/values.yaml").write_text(
            "config:\n  server:\n    cacheDir: tmp\n")
        rendered = [configmap("app-filebrowser-quantum", {"config.yaml": "server:\n  nope: 1\n"})]
        entry = {"chart": "cluster/helm/app", "dependency": "filebrowser-quantum",
                 "schema": "config", "key": "config.yaml"}
        found = check_config_keys.check_entry(entry, root, render_fn=lambda chart_dir, helm: rendered)
        self.assertEqual(
            found,
            ["cluster/helm/app: config.yaml:2: server.nope: not declared by filebrowser-quantum 2.0.2-stable"],
        )


class MainTests(unittest.TestCase):
    def test_an_unbuilt_dependency_exits_2(self):
        root = pathlib.Path(tempfile.mkdtemp())
        chart = root / "cluster/helm/app"
        (chart / "charts").mkdir(parents=True)
        (chart / "Chart.yaml").write_text("name: app\nversion: 0.1.0\n")
        (chart / "values.yaml").write_text("filebrowser-quantum: {}\n")
        manifest = root / "manifest.yaml"
        manifest.write_text(
            "- chart: cluster/helm/app\n"
            "  dependency: filebrowser-quantum\n"
            "  schema: config\n"
            "  key: config.yaml\n"
        )
        self.assertEqual(check_config_keys.main(["--manifest", str(manifest)]), 2)
