"""Check that a rendered config file uses only keys its release knows.

`helm template` renders any key happily; the application then refuses to start on
a field it does not recognise. The FileBrowser cutover lost three syncs in a row
to exactly that, one unknown key at a time, and nothing local could have caught
it (docs/superpowers/plans/2026-11-19-filebrowser-quantum.md).

The pinned dependency's own `values.yaml` is written against the release's
schema: it is the list of keys the release knows. This module renders a wrapper
chart, reads the config file out of its ConfigMap, and compares every key path
in it against that tree.

Only charts listed in `hack/config-key-checks.yaml` are checked. A chart whose
upstream defaults are not a complete schema would report keys the app accepts,
so opting in is a deliberate act.

    hack/check-config-keys.sh             # every listed chart
    hack/check-config-keys.sh --only media

Exit: 0 clean, 1 violations, 2 a chart could not be checked.
"""

import argparse
import pathlib
import subprocess
import tarfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "hack" / "config-key-checks.yaml"


class CheckError(Exception):
    """A chart could not be checked: setup problem, not a violation."""


def _dotted(path, key):
    return f"{path}.{key}" if path else key


def _merge(left, right):
    """Recursively union two key trees: a nested map is merged, never replaced."""
    merged = dict(left)
    for key, value in right.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _walk(node, schema, path, found):
    """Collect the keys of a parsed config node that the schema does not declare."""
    if isinstance(node, yaml.MappingNode):
        if not isinstance(schema, dict):
            # The schema declares a scalar here and the render is richer; a type
            # change is the app's complaint, not a key the chart never declared.
            return
        for key_node, value_node in node.value:
            key = key_node.value
            child = _dotted(path, key)
            if key in schema:
                _walk(value_node, schema[key], child, found)
            else:
                found.append((key_node.start_mark.line + 1, child))
    elif isinstance(node, yaml.SequenceNode):
        if not isinstance(schema, list):
            return
        # Helm replaces a list wholesale, so the chart's example elements have
        # to stand for every element the user wrote: union their key trees.
        element_schema = {}
        for element in schema:
            if isinstance(element, dict):
                element_schema = _merge(element_schema, element)
        if not element_schema:
            # The schema declares no element at all (an empty list, or a list of
            # scalars), so there is nothing to compare the render against.
            return
        for element in node.value:
            _walk(element, element_schema, f"{path}[]", found)


def undeclared_keys(config_yaml, schema):
    """Return `(line, dotted path)` for every key `schema` does not declare.

    The line is 1-based and counts inside `config_yaml` itself, not inside the
    manifest it was rendered into.
    """
    node = yaml.compose(config_yaml)
    found = []
    if node is not None:
        _walk(node, schema, "", found)
    return found


def config_text(documents, key):
    """Return the data value of the one ConfigMap carrying `key`."""
    matches = [
        document
        for document in documents
        if isinstance(document, dict)
        and document.get("kind") == "ConfigMap"
        and isinstance(document.get("data"), dict)
        and key in document["data"]
    ]
    if len(matches) != 1:
        raise CheckError(f"expected exactly one ConfigMap carrying {key!r}, found {len(matches)}")
    return matches[0]["data"][key]


def _dependency_from_directory(directory):
    try:
        chart_yaml = (directory / "Chart.yaml").read_text(encoding="utf-8")
        values_yaml = (directory / "values.yaml").read_text(encoding="utf-8")
    except OSError as error:
        raise CheckError(f"{directory}: cannot read the dependency chart: {error}") from error
    return values_yaml, chart_yaml


def _dependency_from_archive(archive_path):
    with tarfile.open(archive_path) as archive:
        names = set(archive.getnames())
        tops = set()
        for name in names:
            parts = name.split("/")
            if len(parts) == 2 and parts[1] in ("Chart.yaml", "values.yaml") and parts[0]:
                tops.add(parts[0])
        if len(tops) != 1:
            raise CheckError(
                f"{archive_path.name}: expected one top-level directory, found {len(tops)}"
            )
        top = tops.pop()
        for filename in ("Chart.yaml", "values.yaml"):
            if f"{top}/{filename}" not in names:
                raise CheckError(f"{archive_path.name}: no {filename} in the archive")
        values_yaml = archive.extractfile(f"{top}/values.yaml").read().decode("utf-8")
        chart_yaml = archive.extractfile(f"{top}/Chart.yaml").read().decode("utf-8")
    return values_yaml, chart_yaml


def _name_and_version(chart_yaml, fallback):
    try:
        chart = yaml.safe_load(chart_yaml) or {}
    except yaml.YAMLError as error:
        raise CheckError(f"the dependency's Chart.yaml is not YAML: {error}") from error
    name = chart.get("name") or fallback
    version = chart.get("version") or "?"
    return str(name), str(version)


def dependency_chart(charts_dir, dependency):
    """Return `(values.yaml text, name, version)` of a built dependency chart."""
    charts_dir = pathlib.Path(charts_dir)
    extracted = charts_dir / dependency
    if (extracted / "values.yaml").is_file():
        values_yaml, chart_yaml = _dependency_from_directory(extracted)
    else:
        archives = sorted(charts_dir.glob(f"{dependency}-*.tgz")) if charts_dir.is_dir() else []
        if not archives:
            raise CheckError(
                f"{dependency} is not built under {charts_dir}; run `helm dependency build` "
                f"in the chart that owns it"
            )
        if len(archives) > 1:
            # charts/ is gitignored scratch: a stale archive from an older pin
            # would silently become the schema while helm renders the new one.
            raise CheckError(
                f"several archives for {dependency} under {charts_dir} "
                f"({', '.join(archive.name for archive in archives)}); run "
                f"`helm dependency build` to leave only the pinned one"
            )
        values_yaml, chart_yaml = _dependency_from_archive(archives[0])
    name, version = _name_and_version(chart_yaml, dependency)
    return values_yaml, name, version


def schema_tree(values_yaml, subtree):
    """Return the declared key tree: the `subtree` map of the dependency's values."""
    try:
        values = yaml.safe_load(values_yaml) or {}
    except yaml.YAMLError as error:
        raise CheckError(f"the dependency's values.yaml is not YAML: {error}") from error
    tree = values.get(subtree) if isinstance(values, dict) else None
    if not isinstance(tree, dict):
        raise CheckError(f"no `{subtree}:` map in the dependency's values.yaml")
    return tree


def render(chart_dir, helm="helm"):
    """Render a chart the way ArgoCD would, into a list of documents."""
    chart_dir = pathlib.Path(chart_dir)
    try:
        result = subprocess.run(
            [
                helm,
                "template",
                chart_dir.name,
                str(chart_dir),
                "-f",
                str(chart_dir / "values.yaml"),
            ],
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise CheckError(f"cannot run {helm}: {error}") from error
    if result.returncode != 0:
        first_lines = "\n".join(result.stderr.strip().splitlines()[:5])
        raise CheckError(f"helm template {chart_dir} failed:\n{first_lines}")
    return list(yaml.safe_load_all(result.stdout))


def check_entry(entry, root, helm="helm", render_fn=render):
    """Check one manifest entry; return its formatted violations."""
    chart = entry["chart"]
    chart_dir = pathlib.Path(root) / chart
    # Read the dependency before shelling out to helm: an unbuilt chart is a
    # setup error that needs no renderer to report.
    values_yaml, name, version = dependency_chart(chart_dir / "charts", entry["dependency"])
    schema = schema_tree(values_yaml, entry["schema"])
    documents = render_fn(chart_dir, helm)
    text = config_text(documents, entry["key"])
    return [
        f"{chart}: {entry['key']}:{line}: {path}: not declared by {name} {version}"
        for line, path in undeclared_keys(text, schema)
    ]


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--root", default=str(ROOT), help="directory the manifest's chart paths are relative to")
    parser.add_argument("--only", default="", help="only charts whose path contains this string")
    arguments = parser.parse_args(argv)

    try:
        entries = yaml.safe_load(pathlib.Path(arguments.manifest).read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError) as error:
        print(f"SKIP {arguments.manifest}: {error}")
        return 2

    violations = 0
    checked = 0
    for entry in entries:
        chart = entry.get("chart", "?")
        if arguments.only and arguments.only not in chart:
            continue
        checked += 1
        try:
            missing = [field for field in ("chart", "dependency", "schema", "key") if not entry.get(field)]
            if missing:
                raise CheckError(f"the manifest entry is missing {', '.join(missing)}")
            found = check_entry(entry, arguments.root)
        except CheckError as error:
            print(f"SKIP {chart}: {error}")
            return 2
        if found:
            for violation in found:
                print(f"FAIL {violation}")
            violations += len(found)
        else:
            print(f"{chart}  ok")

    print(f"checked: {checked} chart(s)")
    if violations:
        print("result: FAIL")
        return 1
    print("result: OK")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
