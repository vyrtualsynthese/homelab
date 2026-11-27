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
