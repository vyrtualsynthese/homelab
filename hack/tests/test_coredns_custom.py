"""`cluster/infrastructure/coredns/custom.yaml` is applied by hand, so nothing
else in the repository reads it. These checks pin the one thing it must keep
true: `*.kub.local` goes to the two edge Pi-holes, and the catch-all stays the
node's own list."""
import pathlib

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]


def custom_data():
    path = REPO / "cluster/infrastructure/coredns/custom.yaml"
    return yaml.safe_load(path.read_text())["data"]


def edge_addresses():
    inventory = yaml.safe_load((REPO / "ansible/inventory/hosts.yml").read_text())
    hosts = inventory["all"]["children"]["edge"]["hosts"]
    return [hosts["rbp0"]["ansible_host"], hosts["rbp1"]["ansible_host"]]


def test_the_internal_wildcard_is_forwarded_to_the_two_pis():
    """The three comment-only files are not enough: on 2026-11-26 nuc0's
    resolv.conf held 1.1.1.1 beside the Pi-hole, `1.1.1.1` answers `*.kub.local`
    with NXDOMAIN, and CoreDNS treated both as peers (card 889)."""
    data = custom_data()
    override = data["kub-local.override"]
    forward = [
        line.strip() for line in override.splitlines() if line.strip().startswith("forward")
    ]
    assert forward == [f"forward kub.local {' '.join(edge_addresses())}"], (
        "one zone forward, to the two Pis in inventory order and nothing else"
    )


def test_the_override_does_not_replace_the_catch_all():
    """A `forward .` here would shadow the packaged one: CoreDNS chains forward
    stanzas in file order, so every name would stop using the node's list. The
    card is about `*.kub.local`; the rest of the internet keeps its resolver."""
    override = custom_data()["kub-local.override"]
    assert "forward . " not in override
    assert "forward .\n" not in override


def test_the_globs_keep_their_comment_only_files():
    """Without a match for each glob, CoreDNS logs a warning on every reload —
    6,152 lines in the 24 h to 2026-11-22. A new key must not replace them."""
    data = custom_data()
    assert set(data) >= {
        "empty.server",
        "empty.override",
        "kub-local.override",
    }
    assert all(
        line.strip().startswith("#")
        for line in data["empty.server"].splitlines()
        if line.strip()
    )
    assert all(
        line.strip().startswith("#")
        for line in data["empty.override"].splitlines()
        if line.strip()
    )
