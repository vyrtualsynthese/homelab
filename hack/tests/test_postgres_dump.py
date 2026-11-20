"""The PostgreSQL dump script must carry the roles, not only the databases.

A dump restores data into a role that must already exist, so a backup without
`pg_dumpall --globals-only` restores nothing on a rebuilt cluster: every table
would belong to an owner the new server has never heard of.
"""

import pathlib
import subprocess
import unittest

import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "cluster/helm/databases/postgres"


def dump_script() -> str:
    out = subprocess.run(
        ["helm", "template", "postgres", str(CHART), "-f", str(CHART / "values.yaml")],
        check=True, capture_output=True, text=True,
    ).stdout
    scripts = [
        doc["data"]["dump-all.sh"] for doc in yaml.safe_load_all(out)
        if doc and doc["kind"] == "ConfigMap" and "dump-all.sh" in doc.get("data", {})
    ]
    assert len(scripts) == 1, f"expected one dump script, found {len(scripts)}"
    return scripts[0]


class GlobalsTests(unittest.TestCase):
    def test_the_dump_script_captures_roles_beside_the_databases(self):
        script = dump_script()
        self.assertIn("pg_dumpall --globals-only", script)
        self.assertIn('globals="/dumps/globals.sql"', script)
        self.assertIn('"${globals}.partial"', script)

    def test_a_half_written_globals_file_never_takes_the_real_name(self):
        script = dump_script()
        partial = script.index('"${globals}.partial"')
        rename = script.index('mv "${globals}.partial"', partial)
        self.assertLess(partial, rename)

    def test_an_empty_globals_dump_fails_the_run(self):
        script = dump_script()
        self.assertRegex(script, r"\[ -s \"\$\{globals\}\.partial\" \]")

    def test_globals_are_dumped_before_the_databases(self):
        script = dump_script()
        self.assertLess(script.index("pg_dumpall"), script.index("for db in"))


if __name__ == "__main__":
    unittest.main()
