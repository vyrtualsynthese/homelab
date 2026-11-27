"""The cluster leaf is issued by hand, so nothing renders it. These checks pin the two names it has
to carry: the zone every route serves, and the old one the wildcard redirect still answers."""

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]


def san_line():
    text = (REPO / "hack/internal-cert.sh").read_text()
    match = re.search(r'^SAN="([^"]+)"', text, re.MULTILINE)
    assert match, "the SAN list is one line the script passes to openssl"
    return match.group(1).split(",")


def test_the_leaf_covers_both_internal_wildcards():
    san = san_line()
    assert "DNS:*.kub.internal" in san
    assert "DNS:*.kub.local" in san, "the redirect answers old names over TLS during the soak"


def test_the_leaf_keeps_the_names_the_script_already_serves():
    san = san_line()
    assert "DNS:*.app.local" in san
    assert "DNS:localhost" in san
    assert "IP:127.0.0.1" in san


def test_the_help_text_no_longer_claims_a_single_zone():
    text = (REPO / "hack/internal-cert.sh").read_text()
    header = text.split("set -euo pipefail", 1)[0]
    assert "kub.internal" in header
