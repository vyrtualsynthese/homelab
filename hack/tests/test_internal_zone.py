"""No chart may declare a `*.kub.local` host again.

The zone is `*.kub.internal` from 2026-11-27. `.local` is reserved for mDNS by RFC 6762, and Android
sends every `.local` name to 224.0.0.251 and never to the configured DNS, so a chart that reintroduces
the suffix reintroduces the bug for every phone on the LAN. The old zone is answered by one wildcard
redirect in `traefik-gate`, not by a route of its own.
"""

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
HELM = REPO / "cluster" / "helm"

# The two names the cluster dials on the Pis; they keep their per-host leaves and their Pi-hole
# records, and they are the only `*.kub.local` values left in the charts.
EDGE_NAMES = ("pihole-rbp0.kub.local", "pihole-rbp1.kub.local")


def active_lines(path):
    """Configuration only: a comment may explain the old zone, a value may not carry it."""
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        yield number, line


def test_no_chart_value_names_the_old_zone():
    offenders = [
        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
        for path in sorted(HELM.rglob("values.yaml"))
        for number, line in active_lines(path)
        if ".kub.local" in line and not any(edge in line for edge in EDGE_NAMES)
    ]
    assert offenders == [], "\n".join(offenders)


def test_the_edge_pi_names_stay_on_the_old_zone():
    """`pihole-rbp0`/`rbp1` are served by the Pis themselves, with a per-host leaf, and only the
    cluster's exporter dials them. They are not Traefik hosts and they do not move."""
    exporter = (HELM / "monitoring/pihole-exporter/values.yaml").read_text()
    for name in EDGE_NAMES:
        assert name in exporter


# Writing the dot escaped is how a name survives a search for the suffix: Dashy's
# `redirect-to-dashy` carried `dashy\.kub\.local` through the 2026-11-27 rename, so its new
# internal name matched no redirect and answered 418 through `noop@internal`. A double-quoted
# YAML scalar holds `\\.`, so the backslashes are stripped before the suffix is looked for.
ALLOWED_OLD_ZONE = {
    # the soak itself: the wildcard that keeps the old names alive until 2027-01-01
    "platform/traefik-gate/templates/legacy-redirect.yaml",
}
EDGE_DIRS = ("monitoring/pihole-exporter/",)
EDGE_LINE_NAMES = EDGE_NAMES + (
    "vpn-rbp0.kub.local",
    "vpn-rbp1.kub.local",
    "rbp0.kub.local",
    "rbp1.kub.local",
)


def test_no_chart_file_names_the_old_zone():
    offenders = []
    for path in sorted(HELM.rglob("*")):
        if not path.is_file() or path.suffix not in {".yaml", ".yml", ".txt", ".json"}:
            continue
        relative = path.relative_to(HELM).as_posix()
        if relative in ALLOWED_OLD_ZONE or relative.startswith(EDGE_DIRS):
            continue
        for number, line in active_lines(path):
            normalized = line.replace("\\", "")
            if ".kub.local" not in normalized:
                continue
            if any(edge in normalized for edge in EDGE_LINE_NAMES):
                continue
            offenders.append(f"cluster/helm/{relative}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


EXTRA = [
    REPO / "cluster/helm/dashy/files/conf.yml",
    REPO / "cluster/infrastructure/traefik/dashboard/redirect.yaml",
    REPO / "cluster/helm/monitoring/grafana/export/traefik/dashboard.json",
]


def test_the_hand_maintained_copies_agree():
    offenders = [
        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
        for path in EXTRA
        for number, line in active_lines(path)
        if ".kub.local" in line
    ]
    assert offenders == [], "\n".join(offenders)


def test_the_start_page_keeps_one_internal_name():
    """Dashy's own host is typed in a template rather than a value: two rules, one entry point
    each, both redirecting to the public page."""
    text = (HELM / "dashy/templates/ingress-redirect.yaml").read_text()
    assert text.count("Host(`dashy.kub.internal`)") == 2
    assert "dashy.kub.local" not in text
