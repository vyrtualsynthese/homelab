# Config-key check for wrapper charts — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `hack/` checker that renders an opted-in wrapper chart and fails any key in the rendered config file that the pinned dependency's own `values.yaml` does not declare, naming the key and its line.

**Architecture:** One Python module holds the pure comparison plus three readers (manifest entry → rendered documents → dependency `values.yaml` → config file text). A bash driver runs it over a manifest, and the manifest is data: FileBrowser is its first entry.

**Tech Stack:** Python 3 with PyYAML (already in `hack/tests/requirements.txt`), bash driver, helm for rendering.

**Spec:** [`docs/superpowers/specs/2026-11-20-config-key-check-design.md`](../specs/2026-11-20-config-key-check-design.md)

## Global Constraints

- Files under `hack/` use LF, YAML uses 2 spaces.
- Exit codes: `0` clean, `1` violations, `2` a chart could not be checked.
- Unit tests must need neither helm nor network: the render step is injected.
- A missing or unbuilt dependency is a setup error (exit 2) naming `helm dependency build`, never a violation.
- Commit prefix `🚀` for the feature and `📝` for the docs lines, per `AGENTS.md`.
- The whole checker must pass `hack/check-config-keys.sh` and `hack/check-exposure.sh` on `develop`.

## Review Focus

- Line numbers must be inside the rendered `config.yaml`, not inside the multi-document manifest it is embedded in. A block scalar loaded through `yaml` gives the right numbering; a text scan of the manifest does not.
- Where the chart's `values.yaml` declares a scalar or `null` and the user's values put a list there (`auth.methods.oidc.userGroups`), that must pass — the app accepts it, the chart just writes its default empty.
- Helm replaces lists wholesale instead of merging them, so a chart's one example source becomes the user's three. Validating only the first element would let an unknown key through on `sources[1]` and `sources[2]`.
- A chart whose dependency is not built must exit `2`, not fail with a phantom violation — otherwise a PR author chases a config key that is fine.
- A key inside a list of maps nested in another list of maps (`server.sources[].config.rules[]`) must still be checked, and named by its full dotted path.

---

### Task 1: The checker module and its unit tests

**Files:**
- Create: `hack/check_config_keys.py`
- Test: `hack/tests/test_check_config_keys.py`

**Interfaces:**
- Produces: `class CheckError(Exception)` — a setup problem, exit 2.
- Produces: `undeclared_keys(config_yaml: str, schema: dict) -> list[tuple[int, str]]` — 1-based line of the key in `config_yaml`, dotted path, in document order.
- Produces: `config_text(documents: list, key: str) -> str` — the data value of the single `ConfigMap` carrying `key`; raises `CheckError` when none or several do.
- Produces: `dependency_chart(charts_dir: pathlib.Path, dependency: str) -> tuple[str, str, str]` — `(values.yaml text, dependency name, dependency version)` from `<charts_dir>/<dependency>/` or `<charts_dir>/<dependency>-*.tgz`; raises `CheckError` naming `helm dependency build`.
- Produces: `schema_tree(values_yaml: str, subtree: str) -> dict` — the declared key tree; raises `CheckError` when the subtree is absent or not a map.
- Produces: `render(chart_dir: pathlib.Path, helm: str = "helm") -> list` — `helm template <chart-dir-name> <chart_dir> -f <chart_dir>/values.yaml`, parsed into a document list; raises `CheckError` on a non-zero exit.
- Produces: `check_entry(entry: dict, root: pathlib.Path, helm: str = "helm", render_fn=render) -> list[str]` — one manifest entry (`{"chart": ..., "dependency": ..., "schema": ..., "key": ...}`) to formatted violations: `"<chart>: <key>:<line>: <path>: not declared by <name> <version>"`.
- Produces: `main(argv: list[str]) -> int` — `--manifest PATH`, `--only SUBSTR`, prints `<chart>  ok` per clean entry, returns 0/1/2.

- [ ] **Step 1: Write the failing tests**

Create `hack/tests/test_check_config_keys.py`:

```python
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
            [(8, "frontend.externalLinks[].extra")],
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_check_config_keys.py`
Expected: collection error, `ModuleNotFoundError: No module named 'check_config_keys'`.

- [ ] **Step 3: Implement the module**

Create `hack/check_config_keys.py`. Its docstring states the why (the FileBrowser cutover, three syncs lost to unknown keys) and the rule (the pinned dependency's `values.yaml` is the schema).

Signatures exactly as the Interfaces block. Bodies:

- `undeclared_keys` walks `yaml.compose(config_yaml)` with the schema beside it. A `MappingNode` against a dict: each key present in the schema recurses, each absent key appends `(key.start_mark.line + 1, dotted_path)`. A `SequenceNode` against a list: merge the dict members of the schema's elements into one dict, then walk every rendered element against it, with `[]` appended to the path for all elements. A rendered sequence or mapping against a non-dict, non-list schema value (a scalar or `None`) is accepted and not descended into.
- `config_text` filters `kind == "ConfigMap"` documents whose `data` is a dict carrying `key`; zero or more than one raises `CheckError` naming the key and the count.
- `dependency_chart` looks for `<charts_dir>/<dependency>/values.yaml` first, then `<dependency>-*.tgz`; both routes read `Chart.yaml` for the name and version. Anything else raises `CheckError` with `helm dependency build` and the chart path in the message. Only a `.tgz` that unpacks to one top-level directory is accepted.
- `schema_tree` parses the values text and returns the `subtree` value, raising `CheckError` unless it is a dict.
- `render` runs `helm template <chart_dir.name> <chart_dir> -f <chart_dir>/values.yaml`, captures stdout, parses with `yaml.safe_load_all`, and turns a non-zero exit into a `CheckError` carrying the first lines of stderr.
- `check_entry` chains them — chart directory, then `dependency_chart` before any helm call, so an unbuilt dependency fails fast without needing helm installed — and formats each `undeclared_keys` result as `f"{chart}: {key}:{line}: {path}: not declared by {name} {version}"`.
- `main` parses `--manifest` (default `hack/config-key-checks.yaml`), `--only`, reads the manifest with `yaml.safe_load`, filters by substring on `chart`, prints `<chart>  ok` for clean entries, prints violations as `FAIL <violation>`, and returns 2 the moment an entry raises `CheckError`, 1 if any violation, else 0.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_check_config_keys.py`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add hack/check_config_keys.py hack/tests/test_check_config_keys.py
git commit -m "🚀 feat(hack): compare a rendered config file with its chart's schema"
```

---

### Task 2: Manifest, driver, and the documented call

**Files:**
- Create: `hack/config-key-checks.yaml`
- Create: `hack/check-config-keys.sh`
- Modify: `AGENTS.md` (§6, after the `check-app-version.sh` paragraph)
- Modify: `docs/charts/README.md` (the Required table)

**Interfaces:**
- Consumes: `check_config_keys.main` from Task 1.
- Produces: `hack/config-key-checks.yaml` — a list of `{chart, dependency, schema, key}` maps, path relative to the repo root.

- [ ] **Step 1: Write the manifest**

```yaml
# Charts whose application refuses to start on a config key it does not know.
# Each entry renders the chart and compares its config file against the key tree
# the pinned dependency's own values.yaml declares — that file is written against
# the release's schema, so a key missing there is a key the release rejects.
#
# Only list a chart whose app validates its whole config file at startup. A chart
# whose upstream defaults are not a complete schema would report false failures.
#
# The rule is requirement 20 of docs/charts/README.md.

- chart: cluster/helm/filebrowser
  dependency: filebrowser-quantum
  schema: config        # the values.yaml subtree that declares the config file's keys
  key: config.yaml      # the ConfigMap data key holding the rendered file
```

- [ ] **Step 2: Write the driver**

Create `hack/check-config-keys.sh`, modelled on `hack/check-app-version.sh`:

```bash
#!/usr/bin/env bash
# Every key in a rendered config file must exist in the pinned chart's schema.
#
# helm template renders any key; the application refuses to start on one it does
# not know, and that failure only shows after a sync. This checker renders each
# chart listed in hack/config-key-checks.yaml and compares its config file
# against the key tree the dependency's own values.yaml declares.
#
#   hack/check-config-keys.sh                  # every listed chart
#   hack/check-config-keys.sh --only filebrowser
#   hack/check-config-keys.sh --self-test      # the checker's unit tests
#
# Exit: 0 clean, 1 violations, 2 a chart could not be checked.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${1:-}" in
  --self-test)
    exec python3 -m unittest discover -s "$ROOT/hack/tests" -p 'test_check_config_keys.py' -v ;;
  -h|--help)
    sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
esac

exec python3 "$ROOT/hack/check_config_keys.py" --manifest "$ROOT/hack/config-key-checks.yaml" "$@"
```

Make it executable: `chmod +x hack/check-config-keys.sh`.

- [ ] **Step 3: Run it against the real FileBrowser chart**

Run: `hack/check-config-keys.sh`
Expected: `cluster/helm/filebrowser  ok`, `result: OK` is not printed by this driver, exit 0. The dependency must be built first (`cd cluster/helm/filebrowser && helm dependency build`); without it the script exits 2 with the `helm dependency build` hint.

- [ ] **Step 4: Prove it catches the failure it was written for**

Append `      cacheDirMaxSizeMB: 100` under `server:` in `cluster/helm/filebrowser/values.yaml`, then run `hack/check-config-keys.sh`.
Expected: exit 1 with `FAIL cluster/helm/filebrowser: config.yaml:<line>: server.cacheDirMaxSizeMB: not declared by filebrowser-quantum 2.0.2-stable`, where `<line>` is the line in the rendered config file (a value around 40), not the line in `values.yaml`. Revert the values edit with `git checkout -- cluster/helm/filebrowser/values.yaml`.

- [ ] **Step 5: Document the call**

In `AGENTS.md` §6, after the `hack/check-app-version.sh` paragraph, add:

```markdown
`hack/check-config-keys.sh` renders each chart listed in `hack/config-key-checks.yaml` and fails
any key in its config file that the pinned dependency's `values.yaml` does not declare — the class
of failure that cost the FileBrowser cutover three syncs. A chart whose app rejects unknown config
keys at startup belongs in that manifest; the check names the key and its line in the rendered file.
```

In `docs/charts/README.md`, add row 20 to the **Required once the app needs it** table:

```markdown
| 20 | A chart whose app rejects unknown config keys at startup is listed in `hack/config-key-checks.yaml` | An unknown key fails the app at startup, and only after a sync; the manifest is how the local check knows to read it |
```

- [ ] **Step 6: Run the whole checker suite**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests && hack/check-config-keys.sh && hack/check-exposure.sh`
Expected: all tests pass, `cluster/helm/filebrowser  ok`, `result: OK` from the exposure checker.

- [ ] **Step 7: Commit**

```bash
git add hack/config-key-checks.yaml hack/check-config-keys.sh AGENTS.md docs/charts/README.md
git commit -m "🔧 chore(hack): wire the config-key check into the PR checks"
```
