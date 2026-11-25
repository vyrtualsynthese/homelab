"""The two roles that exist only on `streaming`: the log directory and the bouncer.

Neither is a role for every host. `site.yml` runs one play over `all`, so a role
that is not for every host carries its own guard — and a guard that names a group
nobody is in is a role that silently never runs.
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "ansible"


class TraefikLogsRoleTests(unittest.TestCase):
    def setUp(self):
        self.role = ANSIBLE / "roles/traefik_logs"

    def test_the_role_exists_with_metadata_and_a_logrotate_template(self):
        self.assertTrue((self.role / "tasks/main.yml").is_file())
        self.assertTrue((self.role / "templates/logrotate.j2").is_file())
        self.assertIn("role_name: traefik_logs", (self.role / "meta/main.yml").read_text())

    def test_the_directory_belongs_to_traefiks_uid(self):
        tasks = (self.role / "tasks/main.yml").read_text()
        defaults = (self.role / "defaults/main.yml").read_text()
        self.assertIn("path: \"{{ traefik_logs_dir }}\"", tasks)
        self.assertIn('mode: "2770"', tasks)
        self.assertIn("owner: \"{{ traefik_logs_uid }}\"", tasks)
        self.assertIn("traefik_logs_dir: /var/log/traefik", defaults)
        self.assertIn("traefik_logs_uid: 65532", defaults)
        self.assertIn("traefik_logs_gid: 65532", defaults)

    def test_rotation_is_bounded_and_truncates_in_place(self):
        template = (self.role / "templates/logrotate.j2").read_text()
        defaults = (self.role / "defaults/main.yml").read_text()
        self.assertIn("size {{ traefik_logs_size }}", template)
        self.assertIn("rotate {{ traefik_logs_rotate }}", template)
        self.assertIn("traefik_logs_size: 200M", defaults)
        self.assertIn("traefik_logs_rotate: 7", defaults)
        # The pod has its own PID namespace, so a `postrotate` on the host cannot
        # signal Traefik: `create` plus SIGUSR1 would rotate the name and leave
        # the writer holding the old inode. Copy-then-truncate is the mechanism
        # that works from outside the namespace.
        self.assertIn("copytruncate", template)
        self.assertNotIn("kill -USR1", template)
        # The directory is group-writable for Traefik's gid, and logrotate
        # refuses to rotate a file there without being told which account runs.
        self.assertIn("su root root", template)

    def test_site_runs_it_and_says_which_host(self):
        site = (ANSIBLE / "playbooks/site.yml").read_text()
        self.assertIn("role: traefik_logs", site)
        block = site.split("role: traefik_logs")[1].split("- ")[0]
        self.assertIn("inventory_hostname == 'streaming'", block)


if __name__ == "__main__":
    unittest.main()
