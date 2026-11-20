"""Check that a chart's `appVersion` names the version of the image it runs.

`Chart.yaml` has no way to read `values.yaml`, so the application version is
written twice: once as `appVersion`, once inside `image.tag`. Nothing makes the
two move together, and a Renovate pull request that bumps the tag leaves
`appVersion` behind, so `app.kubernetes.io/version` starts lying about what the
cluster runs.

The contract (docs/charts/README.md, requirement 1) settles what `appVersion`
means: the application's own version, without the packaging around the tag.
`v2.1.7-ls237` is version `2.1.7`; `13.3.1-distroless` is `13.3.1`;
`5.3.0_v2.1.2-ls487` is `5.3.0`. Stripping the suffix is what keeps the value
stable across a base-image rebuild, which is the bump that happens most often
and needs no version change.

The image a chart runs is the first `tag:` in its `values.yaml`: the app's own
image goes first there, sidecars after it. A chart that pins no versioned tag —
a middleware-only chart, a wrapper whose tag is not ours, an image published
only as `latest` — states why on an `# appVersion-check: exempt — <reason>`
line, next to the `appVersion` it would otherwise carry.
"""

import re
from pathlib import Path

# `appVersion: "1.2.3"`, unquoted, and with a trailing comment.
APP_VERSION_RE = re.compile(r"""^appVersion:[ \t]*["']?([^"'\s#]+)""", re.MULTILINE)
# The first `tag:` anywhere in values.yaml, at any indent, quoted or not.
TAG_RE = re.compile(r"""^[ \t]*tag:[ \t]*["']?([^"'\s#]+)""", re.MULTILINE)
# The application's version: a leading `v` and whatever packaging follows are not it.
VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")
# The exemption has to carry a reason: `exempt — <reason>` or `exempt - <reason>`.
EXEMPT_RE = re.compile(r"^[ \t]*#[ \t]*appVersion-check:[ \t]*exempt[ \t]*[—-][ \t]*(\S.*)$", re.MULTILINE)


def app_version(chart_yaml):
    """The chart's `appVersion`, or None when it carries none."""
    match = APP_VERSION_RE.search(chart_yaml)
    return match.group(1) if match else None


def exemption(chart_yaml):
    """The reason this chart is out of scope, or None."""
    match = EXEMPT_RE.search(chart_yaml)
    return match.group(1).strip() if match else None


def primary_tag(values_yaml):
    """The tag of the chart's own image: the first `tag:` in values.yaml."""
    match = TAG_RE.search(values_yaml or "")
    return match.group(1) if match else None


def version_of(tag):
    """The application version inside an image tag, or None when it has none."""
    if not tag:
        return None
    match = VERSION_RE.match(tag)
    return match.group(1) if match else None


def check_chart(chart_dir):
    """Return the violations for one chart directory; empty means clean."""
    chart_dir = Path(chart_dir)
    chart_yaml = (chart_dir / "Chart.yaml").read_text(encoding="utf-8")
    values_path = chart_dir / "values.yaml"
    values_yaml = values_path.read_text(encoding="utf-8") if values_path.exists() else ""

    if exemption(chart_yaml) is not None:
        return []

    name = chart_dir.name
    declared = app_version(chart_yaml)
    tag = primary_tag(values_yaml)
    version = version_of(tag)

    if declared is None:
        if version is None:
            return [f"{name}: Chart.yaml has no appVersion and values.yaml pins no versioned tag; "
                    f"say why on an `# appVersion-check: exempt — <reason>` line"]
        return [f"{name}: Chart.yaml has no appVersion, but image.tag {tag} is version {version}; "
                f"add `appVersion: \"{version}\"` or an `# appVersion-check: exempt — <reason>` line"]
    if tag is None:
        return [f"{name}: appVersion {declared} cannot be checked, values.yaml pins no `tag:` "
                f"(add `# appVersion-check: exempt — <reason>`)"]
    if version is None:
        return [f"{name}: appVersion {declared} cannot be checked, image.tag {tag} carries no version "
                f"(add `# appVersion-check: exempt — <reason>`)"]
    if declared != version:
        return [f"{name}: appVersion {declared}, but image.tag {tag} is version {version}"]
    return []


def violations(chart_dirs):
    """Check several chart directories, in order, keeping one list of violations."""
    found = []
    for chart_dir in chart_dirs:
        found.extend(check_chart(chart_dir))
    return found


def main(argv):
    found = violations(argv[1:])
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv))
