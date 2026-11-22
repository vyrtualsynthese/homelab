"""Tests that the drill table and the metric it feeds stay in step.

The alert `RestoreDrillOverdue` reads `restoreDrills` in the Prometheus chart, not the table in
`docs/runbooks/restore.md`, so a drill recorded in one place and not the other is silent: the rule
keeps ageing the previous date and nothing says a row is missing. This suite turns that miss into a
gate failure. It checks dates only — how a table row maps to a stream is prose (`Section 1 —
PostgreSQL dumps (nodes-nas)`), so counting and dating every row is what can be verified without
guessing.
"""

import datetime
import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "restore.md"
VALUES = ROOT / "cluster" / "helm" / "monitoring" / "prometheus" / "values.yaml"

HEADER = "| Date (UTC) | Drill | Duration | Result | By |"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def table_dates(text):
    """The dates of the drill table, in order; other tables in the page are ignored."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(HEADER)]
    if not starts:
        raise AssertionError(f"no drill table header in {RUNBOOK}")
    dates = []
    for line in lines[starts[0] + 2 :]:
        if not line.startswith("|"):
            break
        cell = line.split("|")[1].strip()
        if DATE.fullmatch(cell):
            dates.append(cell)
    return dates


def drill_entries():
    return yaml.safe_load(VALUES.read_text())["restoreDrills"]


def entry_dates(entries):
    return [
        datetime.datetime.fromtimestamp(entry["at"], datetime.timezone.utc).strftime("%Y-%m-%d")
        for entry in entries
    ]


FABRICATED = """
| Repository | Written by |
|---|---|
| `nodes-nas` | `backup-node-nas` |

## The drill log

| Date (UTC) | Drill | Duration | Result | By |
|---|---|---|---|---|
| 2026-01-02 | Section 1 — PostgreSQL dumps | ~3 min | restored | agent |
| 2026-02-03 | Section 2 — Sonarr's database | ~1 min | restored | agent |

**Recorded.** prose.
"""


class ParserTests(unittest.TestCase):
    def test_only_the_drill_table_is_read(self):
        self.assertEqual(table_dates(FABRICATED), ["2026-01-02", "2026-02-03"])

    def test_a_missing_header_is_an_error(self):
        with self.assertRaises(AssertionError):
            table_dates("| Repository | Written by |\n|---|---|\n")


class DrillLogTests(unittest.TestCase):
    def test_every_runbook_row_has_a_values_entry(self):
        rows = table_dates(RUNBOOK.read_text())
        entries = entry_dates(drill_entries())
        self.assertEqual(
            sorted(rows),
            sorted(entries),
            "the drill table and restoreDrills disagree: a row in one has no entry in the other, "
            "so RestoreDrillOverdue ages the wrong date. Update both in the same change.",
        )

    def test_every_entry_is_complete(self):
        for entry in drill_entries():
            self.assertTrue(entry["stream"], entry)
            self.assertTrue(entry["drill"], entry)
            self.assertIsInstance(entry["at"], int, entry)
            self.assertEqual(entry_dates([entry])[0], datetime.datetime.fromtimestamp(
                entry["at"], datetime.timezone.utc).strftime("%Y-%m-%d"))

    def test_no_duplicate_series(self):
        seen = {(e["stream"], e["drill"], entry_dates([e])[0]) for e in drill_entries()}
        self.assertEqual(len(seen), len(drill_entries()),
                         "a duplicate series makes node-exporter reject the whole textfile, "
                         "so every drill disappears and the rule reports an unreadable log")
