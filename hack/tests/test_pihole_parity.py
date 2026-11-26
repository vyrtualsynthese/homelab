import json
import pathlib
import sys

import pytest

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import check_pihole_parity as parity  # noqa: E402


def panel(block_ttl=2, listening="ALL", lists=("https://example.test/a.txt",)):
    return parity.snapshot(
        {
            "dns": {"blockTTL": block_ttl, "listeningMode": listening},
            "webserver": {"api": {"app_pwhash": "secret-value"}},
        },
        [{"address": address} for address in lists],
    )


def test_identical_panels_produce_no_diff():
    assert parity.diff_panels(panel(), panel()) == []


def test_diff_names_the_settings_that_differ():
    differences = parity.diff_panels(panel(block_ttl=86400), panel(block_ttl=2))

    assert len(differences) == 1
    assert "dns.blockTTL" in differences[0]
    assert "86400" in differences[0] and "2" in differences[0]


def test_diff_names_the_blocklists_that_differ():
    differences = parity.diff_panels(
        panel(lists=("https://example.test/a.txt", "https://example.test/b.txt")),
        panel(lists=("https://example.test/a.txt",)),
    )

    assert len(differences) == 1
    assert "lists" in differences[0]


def test_a_password_hash_is_not_printed_and_not_called_drift():
    """The two panels must hold different hashes — the app password is per host."""
    differences = parity.diff_panels(panel(), panel())

    assert differences == []
    source = panel()
    replica = panel()
    source["config"]["webserver"]["api"]["app_pwhash"] = "one"
    replica["config"]["webserver"]["api"]["app_pwhash"] = "two"
    printed = " ".join(parity.diff_panels(source, replica))
    assert "one" not in printed and "two" not in printed
    assert "contents not compared" in printed


def test_the_clean_line_counts_a_tree_that_holds_lists():
    """`flatten` yields (key, value) pairs and a value can be a list, which is
    what the adlist set is: counting them in a set raised on the first panel that
    agreed."""
    assert parity.count_settings(panel()) > 0


class Answer:
    """A urlopen answer holding only what `_call` reads from it."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_the_api_key_is_posted_over_https_only(monkeypatch):
    """The panel answers HTTPS on the same address, so the scheme is a choice,
    and over http an on-path LAN device reads a live app password."""
    calls = []
    answers = iter(
        [
            {"session": {"sid": "s1"}},
            {"config": {"dns": {"blockTTL": 2}}},
            {"lists": []},
            {},
        ]
    )

    def fake_urlopen(request, timeout=None, context=None):
        calls.append((request, context))
        return Answer(next(answers))

    monkeypatch.setattr(parity.urllib.request, "urlopen", fake_urlopen)

    parity.read_panel("rbp0", "dummy-key")

    assert [request.full_url for request, _ in calls] == [
        "https://192.168.1.22/api/auth",
        "https://192.168.1.22/api/config",
        "https://192.168.1.22/api/lists",
        "https://192.168.1.22/api/auth/session/s1",
    ]
    assert b"dummy-key" in calls[0][0].data
    assert all(context is not None for _, context in calls)


def test_the_connection_trusts_only_the_panels_pinned_certificate():
    """Pi-hole mints a self-signed leaf whose only name is `pi.hole`, so the
    address the check dials can never match it: the committed leaf is the whole
    trust store instead, and a server without its key cannot complete the
    handshake."""
    context = parity._context("rbp0")

    assert context.cert_store_stats()["x509"] == 1
    assert context.check_hostname is False
    assert parity.CERTIFICATES["rbp0"].read_text() != parity.CERTIFICATES["rbp1"].read_text()


def test_a_pin_that_cannot_be_read_stops_the_check(monkeypatch, tmp_path):
    """No pin means no verified connection: the read fails, it does not fall back
    to plain http or to an unverified context."""
    monkeypatch.setitem(parity.CERTIFICATES, "rbp0", tmp_path / "missing.pem")

    with pytest.raises(RuntimeError, match="pinned certificate"):
        parity.read_panel("rbp0", "dummy-key")


def test_a_panel_session_is_closed_after_the_read():
    """The panel allows a handful of sessions and answers 429 when they run out,
    so a checker that never logs out stops being runnable — which happened."""
    text = (pathlib.Path(__file__).resolve().parents[1] / "check_pihole_parity.py").read_text()

    assert 'f"/api/auth/session/{sid}"' in text
    assert '"DELETE"' in text
