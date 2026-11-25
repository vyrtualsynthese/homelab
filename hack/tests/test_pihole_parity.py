import pathlib
import sys

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
