import os
import pathlib
import re
import subprocess
from types import SimpleNamespace

import jinja2
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
ANSIBLE = REPO / "ansible"


def load(path):
    return yaml.safe_load((ANSIBLE / path).read_text())


def test_inventory_has_six_hosts_in_three_groups():
    inv = load("inventory/hosts.yml")["all"]["children"]
    assert sorted(inv["cluster"]["hosts"]) == ["nas", "nuc0", "rbp2", "streaming"]
    assert sorted(inv["servers"]["hosts"]) == ["nuc0", "rbp2", "streaming"]
    assert sorted(inv["edge"]["hosts"]) == ["rbp0", "rbp1"]


def test_every_host_has_an_address_from_the_record():
    inv = load("inventory/hosts.yml")["all"]["children"]
    seen = {
        host: spec["ansible_host"]
        for group in inv.values()
        for host, spec in group["hosts"].items()
    }
    assert seen == {
        "nas": "192.168.1.41",
        "nuc0": "192.168.1.27",
        "streaming": "192.168.1.46",
        "rbp2": "192.168.1.48",
        "rbp0": "192.168.1.22",
        "rbp1": "192.168.1.38",
    }


def yaml_files():
    return sorted(list(ANSIBLE.rglob("*.yml")) + list(ANSIBLE.rglob("*.yaml")))


def test_an_address_is_written_once():
    for path in yaml_files():
        if path.name == "hosts.yml":
            continue
        assert "192.168.1." not in path.read_text(), (
            f"{path} repeats an address; read it from hostvars instead"
        )


def test_site_lists_every_role_that_exists():
    site = (ANSIBLE / "playbooks/site.yml").read_text()
    roles = sorted(p.name for p in (ANSIBLE / "roles").iterdir() if p.is_dir())
    assert roles, "no roles yet"
    for role in roles:
        assert f"- {role}" in site or f"- role: {role}" in site, (
            f"{role} is not reached from site.yml"
        )


def test_every_role_declares_its_metadata():
    for role in (ANSIBLE / "roles").iterdir():
        if role.is_dir():
            meta = yaml.safe_load((role / "meta/main.yml").read_text())
            assert meta["galaxy_info"]["role_name"] == role.name


def test_lock_timeout_is_set_and_short():
    cfg = (ANSIBLE / "ansible.cfg").read_text()
    assert "lock_timeout" in cfg
    assert "inventory = inventory/hosts.yml" in cfg


def test_the_one_passwordless_grant_left_names_the_automation_account():
    """`%admins ALL=(ALL) NOPASSWD: ALL` is removed because it follows every
    account that joins the group. What stays is one account's, in a file this
    layer owns, `0440` and checked by visudo: unattended runs cannot type the
    password the removal would otherwise put in `become`'s way."""
    offenders = []
    for path in yaml_files():
        text = path.read_text()
        if "NOPASSWD: ALL" not in text:
            continue
        removing = "state: absent" in text
        granting = "roles/automation/" in path.as_posix()
        if not (removing or granting):
            offenders.append(path.relative_to(ANSIBLE).as_posix())
    assert offenders == [], f"{offenders} write passwordless root"

    tasks = yaml.safe_load((ANSIBLE / "roles/automation/tasks/main.yml").read_text())
    grants = [
        task["ansible.builtin.copy"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.copy" in task
        and "NOPASSWD: ALL" in str(task["ansible.builtin.copy"].get("content", ""))
    ]
    assert len(grants) == 1, "exactly one file may carry the grant"
    grant = grants[0]
    assert grant["dest"] == "/etc/sudoers.d/00-homelab-automation"
    assert grant["mode"] == "0440"
    assert grant["validate"] == "visudo -cf %s"
    assert "{{ automation_user }} ALL=(ALL) NOPASSWD: ALL" in grant["content"]
    assert "ALL=(" in grant["content"] and "%" not in grant["content"]


def test_the_inventory_connects_as_the_automation_account():
    defaults = load("inventory/group_vars/all.yml")
    inv = load("inventory/hosts.yml")["all"]["children"]
    users = {
        spec["ansible_user"] for group in inv.values() for spec in group["hosts"].values()
    }
    assert users == {defaults["automation_user"]}


def test_the_human_grant_is_removed_only_by_a_run_that_no_longer_uses_it():
    """A run logged in as the human removes the grant it is still using, and the
    next task dies on `Missing sudo password`. Gating the removal on the account
    proves the automation path works before the human one goes away."""
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    removal = [
        task
        for task in tasks
        if isinstance(task, dict)
        and task.get("ansible.builtin.lineinfile", {}).get("path") == "/etc/sudoers"
    ]
    assert len(removal) == 1
    condition = removal[0].get("when")
    assert condition == "ansible_user == automation_user", condition


def test_the_automation_role_can_be_applied_alone_and_runs_before_the_removal():
    """The bootstrap is two runs: the first logs in as the human and may touch
    nothing but the account, `--tags automation`; the second logs in as the
    account and does the rest."""
    site = (ANSIBLE / "playbooks/site.yml").read_text()
    assert site.index("- automation") < site.index("- accounts")
    tasks = yaml.safe_load((ANSIBLE / "roles/automation/tasks/main.yml").read_text())
    untagged = [task.get("name") for task in tasks if "automation" not in (task.get("tags") or [])]
    assert untagged == [], f"{untagged} would not run under --tags automation"


def test_the_readme_documents_the_two_step_bootstrap():
    text = (ANSIBLE / "README.MD").read_text()
    assert "--tags automation" in text
    assert "ansible_user" in text


def test_accounts_removes_the_admins_grant_and_keeps_sudo():
    text = (ANSIBLE / "roles/accounts/tasks/main.yml").read_text()
    assert "path: /etc/sudoers" in text
    assert "line: '%admins ALL=(ALL) NOPASSWD: ALL'" in text
    assert "state: absent" in text
    assert "line: '%sudo" not in text, "the stock %sudo line must not be rewritten"


def test_accounts_removes_the_cloud_init_grant_for_an_account_that_does_not_exist():
    """rbp2's image wrote `/etc/sudoers.d/90-cloud-init-users`, a NOPASSWD grant for
    an `ubuntu` account that no host has had since the rename. It is inert today and
    passwordless root the moment anything creates that name, and it names another
    identity than `%admins`, so the role's own grant removal leaves it alone."""
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    removals = [
        task
        for task in tasks
        if isinstance(task, dict)
        and task.get("ansible.builtin.file", {}).get("path")
        == "/etc/sudoers.d/90-cloud-init-users"
    ]
    assert len(removals) == 1, removals
    assert removals[0]["ansible.builtin.file"]["state"] == "absent"
    assert removals[0].get("when") is None, "nothing about the grant depends on the caller"


def test_accounts_removes_the_orphan_home_only_when_no_account_owns_it():
    """`/home/ubuntu` on rbp2 is uid 1000's, and no account holds that id any more.
    The directory is removed, but only on the answer "no such account": a home that
    belongs to a live account is not this role's to delete."""
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    read = next(t for t in tasks if t.get("register") == "accounts_ubuntu_account")
    assert read["ansible.builtin.command"]["cmd"] == "getent passwd ubuntu"
    assert read["changed_when"] is False
    assert read["failed_when"] is False, "an absent account is the answer, not a failure"
    assert read["check_mode"] is False, "the removal's `when` reads it; --check must still run it"
    home = next(
        t
        for t in tasks
        if isinstance(t, dict)
        and t.get("ansible.builtin.file", {}).get("path") == "/home/ubuntu"
    )
    assert home["ansible.builtin.file"]["state"] == "absent"
    assert "accounts_ubuntu_account.rc == 2" in home["when"], home["when"]
    names = [t.get("name") for t in tasks if isinstance(t, dict)]
    assert names.index(read["name"]) < names.index(home["name"]), "read before remove"


def test_accounts_closes_password_auth_for_every_host():
    defaults = yaml.safe_load((ANSIBLE / "inventory/group_vars/all.yml").read_text())
    assert defaults["sshd_password_auth"] is False
    text = (ANSIBLE / "roles/accounts/tasks/main.yml").read_text()
    assert "PasswordAuthentication" in text
    assert "X11Forwarding" in text


def test_the_sshd_edits_match_active_directives_only():
    """`lineinfile` rewrites the last match, so `^#?\\s*X11Forwarding` edits the
    commented example at the bottom of Ubuntu's file and leaves the active
    `X11Forwarding yes` above it — which still wins, sshd taking the first value."""
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    patterns = {
        task["ansible.builtin.lineinfile"]["regexp"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.lineinfile" in task
        and task["ansible.builtin.lineinfile"]["path"] == "/etc/ssh/sshd_config"
    }
    assert patterns == {"^PasswordAuthentication\\s", "^X11Forwarding\\s"}, patterns


def test_accounts_sets_its_own_sshd_config_and_the_first_word_wins():
    """A drop-in numbered 50 can set PasswordAuthentication yes and win over the
    main file, because sshd takes the first value it reads. Ours has to sort
    ahead of it, and the role has to say so."""
    text = (ANSIBLE / "roles/accounts/tasks/main.yml").read_text()
    assert "sshd_config.d/00-homelab.conf" in text
    assert "first value" in text


def test_a_read_whose_output_another_task_decides_on_runs_in_check_mode():
    """`ansible.builtin.command` is skipped under `--check`, so its register comes back
    with an empty `stdout` and no error. Every task here that another task's `when`,
    `name` or `failed_when` consumes is therefore read-only by nature and must carry
    `check_mode: false`: without it `make check` — the drift report the README sells —
    silently describes a different host than `make apply` would act on.

    The failure this caught, on 2026-11-24 after the kernel fix had been accepted:
    `uname -r` was skipped, `running` rendered empty, and `make check --diff` offered to
    purge the *running* kernel on all four nodes."""
    wanted = {
        ("system", "system_running_kernel"),
        ("packages", "packages_hits"),
        ("nfs", "nfs_exported"),
        ("snapraid", "snapraid_status"),
    }
    seen = set()
    for role, path in [(p.parent.parent.name, p) for p in sorted(ANSIBLE.glob("roles/*/tasks/*.yml"))]:
        for task in yaml.safe_load(path.read_text()) or []:
            if not isinstance(task, dict):
                continue
            register = task.get("register")
            if (role, register) in wanted:
                seen.add((role, register))
                assert task.get("check_mode") is False, (
                    f"{role}: {task.get('name')!r} feeds a decision but is skipped under --check"
                )
    assert seen == wanted, f"task or register renamed: {wanted - seen}"


def test_the_kernel_list_refuses_to_run_without_a_running_kernel():
    """The guard behind the test above: a list computed with an empty `running` names
    the running kernel for removal, so the lister stops instead of guessing."""
    tasks = yaml.safe_load((ANSIBLE / "roles/system/tasks/main.yml").read_text())
    lister = next(t for t in tasks if t.get("register") == "system_kernel_remove")
    script = lister["ansible.builtin.shell"]
    assert '[ -z "$running" ]' in script, "an unread version must stop the task, not shorten it"
    assert "exit 1" in script


def test_system_removes_the_kernels_autoremove_is_forbidden_to_touch():
    """Ubuntu's /etc/apt/apt.conf.d/01autoremove lists `^linux-image-[a-z0-9]*-[a-z0-9]*$`
    under NeverAutoRemove, so `apt-mark auto` plus `autoremove` keeps every kernel:
    on 2026-11-24 `nas` still held four and `rbp2` eighteen after two clean runs,
    while the role reported `changed=0`. The removal therefore names the versions,
    computed on the host, with the running one and the newest kept excluded."""
    text = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    tasks = yaml.safe_load(text)
    lister = next(t for t in tasks if t.get("register") == "system_kernel_remove")
    remover = next(
        t
        for t in tasks
        if "ansible.builtin.apt" in t
        and t["ansible.builtin.apt"].get("state") == "absent"
        and "system_kernel_remove" in str(t["ansible.builtin.apt"].get("name"))
    )
    script = lister["ansible.builtin.shell"]
    assert "{{ kernel_keep | int }}" in script
    assert "{{ system_running_kernel.stdout }}" in script, "the running kernel must never be listed"
    assert "tail -n" in script, "the newest kept kernel is chosen, not written down"
    assert "linux-modules-extra" in script, "the module tree is what holds the image"
    assert remover["ansible.builtin.apt"]["purge"] is True
    assert lister["changed_when"] is False and lister["check_mode"] is False
    assert "01autoremove" in text, "the next reader has to know why autoremove is not enough"
    assert "linux-image-{{" not in text, "a version is never written into the role"


def test_system_pins_the_running_kernel_before_any_removal():
    text = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    tasks = [
        "- name: Read the kernel this host booted",
        "- name: List the kernel packages that are neither running nor the newest kept",
        "- name: Drop the kernels that are neither running nor the newest kept",
    ]
    order = [text.index(task) for task in tasks]
    assert order == sorted(order), "nothing may be removed before the running kernel is known"


def test_system_keeps_two_kernels_and_sets_the_zone_from_one_variable():
    defaults = yaml.safe_load((ANSIBLE / "inventory/group_vars/all.yml").read_text())
    assert defaults["kernel_keep"] == 2
    assert defaults["timezone"] == "Europe/Paris"
    tasks = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    assert "{{ timezone }}" in tasks, "the zone comes from group_vars, not a literal in the role"
    assert "hwclock" not in tasks, "a Raspberry Pi has no RTC; the module would fail on the edge hosts"


def test_the_cache_measure_counts_bytes_and_not_directory_blocks():
    """`du -sm` rounds up: an emptied `/var/cache/apt/archives` still measures
    1 MB because of the directory's own blocks, so `apt clean` reported `changed`
    on every run of a host that was already correct (seen on `streaming`,
    2026-11-24). Byte sizes of the files are zero when the cache is empty."""
    tasks = yaml.safe_load((ANSIBLE / "roles/packages/tasks/main.yml").read_text())
    measure, clean = tasks[0], tasks[1]
    argv = measure["ansible.builtin.command"]["argv"]
    assert "du" not in argv, "block sizes make an empty cache look like 1 MB"
    assert "-type" in argv and "-printf" in argv and "%s" in argv[-1]
    assert measure["changed_when"] is False
    assert measure["check_mode"] is False
    assert "packages_cache_before" in clean["changed_when"]


def test_packages_absent_is_per_host_and_never_touches_the_driver():
    nas = yaml.safe_load((ANSIBLE / "inventory/host_vars/nas.yml").read_text())
    streaming = yaml.safe_load((ANSIBLE / "inventory/host_vars/streaming.yml").read_text())
    rbp2 = yaml.safe_load((ANSIBLE / "inventory/host_vars/rbp2.yml").read_text())
    assert "intel-i915-dkms" in nas["packages_absent"]
    assert any(p.startswith("cuda-") and "12-4" in p for p in streaming["packages_absent"])
    assert all("nvidia" not in p for p in streaming["packages_absent"])
    assert all("linux-image" not in p for p in rbp2["packages_absent"])


def test_packages_resolves_patterns_before_removing():
    text = (ANSIBLE / "roles/packages/tasks/main.yml").read_text()
    assert "dpkg-query" in text, "apt would take a glob literally"
    assert "autoremove: false" in text, "the system role owns autoremove"


def test_packages_runs_before_the_role_that_autoremoves():
    site = (ANSIBLE / "playbooks/site.yml").read_text()
    assert site.index("- packages") < site.index("- system"), (
        "the orphans the removals create are collected in the same run, after the "
        "running kernel is pinned"
    )


def test_firewall_covers_every_port_k3s_needs():
    rules = yaml.safe_load((ANSIBLE / "roles/firewall/vars/main.yml").read_text())
    cluster = {(r["port"], r["proto"]) for r in rules["firewall_rules_cluster"]}
    for needed in [(6443, "tcp"), (10250, "tcp"), (2379, "tcp"), (2380, "tcp"), (8472, "udp")]:
        assert needed in cluster, f"k3s needs {needed}"


def test_firewall_leaves_the_container_ports_alone():
    rules = yaml.safe_load((ANSIBLE / "roles/firewall/vars/main.yml").read_text())
    edge = {(r["port"], r["proto"]) for r in rules["firewall_rules_edge"]}
    assert (22, "tcp") in edge and (53, "udp") in edge
    assert (5000, "tcp") not in edge, "5000 is DNAT, not INPUT: a rule for it filters nothing"
    assert (51821, "udp") not in edge, "the tunnel is a published container port, so ufw never sees it"


def test_firewall_says_what_it_does_not_cover():
    text = (ANSIBLE / "roles/firewall/tasks/main.yml").read_text()
    assert "DOCKER-USER" in text, "the reader has to know where the published ports are filtered"


def test_firewall_takes_its_addresses_from_the_inventory():
    text = "".join(
        (ANSIBLE / f"roles/firewall/tasks/{name}.yml").read_text()
        for name in ("main", "resolve")
    )
    assert "hostvars" in text and "ansible_host" in text


def export_lines():
    """The lines the template emits, without its comments or its Jinja setup."""
    tpl = (ANSIBLE / "roles/nfs/templates/exports.j2").read_text()
    return [
        line
        for line in tpl.splitlines()
        if line.strip() and not line.strip().startswith(("#", "{%"))
    ]


def test_exports_drop_mnt_and_squash_root():
    tpl = (ANSIBLE / "roles/nfs/templates/exports.j2").read_text()
    assert "/mnt/storage/nfs/plexmedia" in tpl
    assert "no_root_squash" not in tpl
    assert "insecure" not in tpl
    assert "root_squash" in tpl
    lines = export_lines()
    assert len(lines) == 7, f"expected the seven the cluster mounts, got {len(lines)}"
    assert not any(line.split()[0] == "/mnt" for line in lines), "/mnt must not be exported"


def test_the_certificate_store_belongs_to_the_identity_that_serves_tls():
    """Traefik runs as 65532 and keeps acme.json at 0600. The restic job arrives
    as root over the export, so the chown and the export must both name 65532;
    1100 would lock the backup out, and widening the mode would stop the router."""
    text = (ANSIBLE / "roles/nfs/tasks/main.yml").read_text()
    assert "/mnt/storage/traefik" in text
    assert "65532" in text
    tpl = (ANSIBLE / "roles/nfs/templates/exports.j2").read_text()
    assert "anonuid=65532" in tpl, "fsid 8 must map the squashed root to acme.json's owner"


def test_the_nfs_role_runs_only_where_the_export_is():
    site = (ANSIBLE / "playbooks/site.yml").read_text()
    assert "group_names" in site and "storage" in site


def test_every_notify_names_a_handler_that_exists():
    for role in (ANSIBLE / "roles").iterdir():
        if not role.is_dir():
            continue
        handler_file = role / "handlers/main.yml"
        handlers = yaml.safe_load(handler_file.read_text()) or [] if handler_file.exists() else []
        names = {h["name"] for h in handlers if isinstance(h, dict) and h.get("name")}
        for path in (role / "tasks").glob("*.yml"):
            for task in yaml.safe_load(path.read_text()) or []:
                if not isinstance(task, dict) or "notify" not in task:
                    continue
                notified = task["notify"]
                notified = [notified] if isinstance(notified, str) else notified
                missing = sorted(set(notified) - names)
                assert not missing, f"{path}: notify names {missing}, which {role.name} does not define"


def test_every_export_line_attaches_the_options_to_each_address():
    for line in export_lines():
        assert "regex_replace" in line, (
            "exports(5) gives an option list to the host it follows: a bare "
            "address takes the defaults instead of this file's options"
        )


def test_snapraid_runner_does_not_need_python2():
    text = (ANSIBLE / "roles/snapraid/tasks/main.yml").read_text()
    assert "python2" not in text
    assert "/etc/cron.d/snapraid" in text
    script = (ANSIBLE / "roles/snapraid/files/snapraid-sync.sh").read_text()
    assert "snapraid sync" in script
    assert "snapraid status" in script


def test_snapraid_replaces_the_hand_written_cron_line():
    """The line in root's crontab was written by hand, so `cron: name=...` cannot
    match it: the module only removes entries it marked with its own comment."""
    text = (ANSIBLE / "roles/snapraid/tasks/main.yml").read_text()
    assert "crontab -u root" in text
    assert "snapraid-runner" in text


def test_snapraid_runs_outside_the_backup_window():
    text = (ANSIBLE / "roles/snapraid/tasks/main.yml").read_text()
    assert "17 3 * * *" in text, "03:17 is after the 02:00 window and before the 05:00 backup"


def test_snapraid_skips_a_night_that_overlaps_a_running_sync():
    """A 51 TB array's sync outlives its night, and SnapRAID refuses a second
    instance on its own lock: without a guard the next 03:17 is a failed run."""
    script = (ANSIBLE / "roles/snapraid/files/snapraid-sync.sh").read_text()
    lock = script.index("flock -n 9")
    assert lock < script.index("/usr/bin/snapraid sync"), "the lock is taken before the sync starts"
    assert "exit 0" in script[lock:script.index("rc=0")], (
        "a skipped run is the expected outcome, not a cron failure"
    )


def test_snapraid_writes_the_parity_marker_after_a_successful_sync():
    """The alert reads a timestamp the runner writes. An age written once a night
    would be frozen by the dead runner it exists to expose."""
    script = (ANSIBLE / "roles/snapraid/files/snapraid-sync.sh").read_text()
    assert "write_marker ||" in script, "the call must tolerate a failed write and log it"
    assert script.index("write_marker ||") > script.index("/usr/bin/snapraid sync"), (
        "the marker must be written after the sync it reports"
    )
    assert "homelab_snapraid_last_sync_timestamp_seconds" in script
    assert 'chmod 0644 "$tmp"' in script, (
        "the exporter reads the file as uid 65534; root's umask must not decide whether it can"
    )
    assert 'mv "$tmp" "$marker"' in script, "the swap is a rename, never a copy"
    assert '>"$marker"' not in script, "an in-place write lets a scrape read half a file"
    assert '$marker.$$' in script, "the temporary name must not end in .prom"
    tasks = (ANSIBLE / "roles/snapraid/tasks/main.yml").read_text()
    assert "/var/lib/node-exporter/textfile" in tasks, "the role owns the directory"


def test_snapraid_role_does_not_claim_the_journal_reaches_loki():
    """Fluent Bit tails /var/log/containers/*.log only: no host line reaches Loki.
    Asserted as the sentence that says so, not only as the absence of the false
    one: a comment that dropped the claim altogether would pass an absence check."""
    tasks = (ANSIBLE / "roles/snapraid/tasks/main.yml").read_text()
    script = (ANSIBLE / "roles/snapraid/files/snapraid-sync.sh").read_text()
    assert "nothing from this host reaches Loki" in tasks
    assert "not shipped to Loki" in script
    for text in (tasks, script):
        assert "already reaches Loki" not in text


def test_readme_no_longer_claims_the_layer_does_not_work():
    text = (REPO / "ansible/README.MD").read_text()
    assert "does not currently work" not in text
    assert "make check" in text


def test_the_edge_firewall_keeps_the_host_exporters_reachable():
    """9100 and 9323 are `network_mode: host`, so unlike a published container
    port they reach INPUT. Prometheus scrapes both on both Pis; default deny
    without a rule takes them off the monitoring silently."""
    rules = yaml.safe_load((ANSIBLE / "roles/firewall/vars/main.yml").read_text())
    edge = {(r["port"], r["proto"]): r["source"] for r in rules["firewall_rules_edge"]}
    assert edge[(9100, "tcp")] == "nodes"
    assert edge[(9323, "tcp")] == "nodes"


def test_every_firewall_rule_names_a_source_set_that_resolves():
    rules = yaml.safe_load((ANSIBLE / "roles/firewall/vars/main.yml").read_text())
    defined = set()
    resolve = (ANSIBLE / "roles/firewall/tasks/resolve.yml").read_text()
    for name in ("lan", "nodes", "servers", "vpn"):
        assert f"'{name}':" in resolve, f"resolve.yml defines no source set {name!r}"
        defined.add(name)
    for key in ("firewall_rules_cluster", "firewall_rules_edge", "firewall_rules_nas"):
        for rule in rules[key]:
            assert rule["source"] in defined, f"{key}: unknown source {rule['source']!r}"


def test_every_export_maps_the_squashed_root_to_the_writer():
    """Mapping root to `nobody` turns a container's start-up chown into EACCES on
    files the app itself owns. 1100 is what every writer here runs as, except the
    certificate store, whose writer is Traefik at 65532."""
    for line in export_lines():
        expected = "65532" if line.startswith("/mnt/storage/traefik") else "1100"
        assert f"anonuid={expected}" in line and f"anongid={expected}" in line, line


def test_the_read_back_checks_the_table_not_its_line_count():
    """`exportfs -v` prints one line per export and client, so a count of 7 is
    true only while seven things mount."""
    text = (ANSIBLE / "roles/nfs/tasks/main.yml").read_text()
    assert "stdout_lines | length" not in text
    assert "no_root_squash" in text and "'^/mnt\\s'" in text


def test_snapraid_scrub_is_reachable_when_status_reports_unsynced_blocks():
    """`snapraid status` exits 2 while blocks are unsynced, which on a live array
    is the normal outcome. Under `set -e` that ended the script before the scrub."""
    script = (ANSIBLE / "roles/snapraid/files/snapraid-sync.sh").read_text()
    status = script.index("/usr/bin/snapraid status")
    scrub = script.index("/usr/bin/snapraid scrub")
    assert status < scrub
    assert "|| rc=$?" in script
    assert "2)" in script, "exit 2 must be classified, not treated as a failure"


def test_the_kernel_kept_beside_the_running_one_is_a_different_kernel():
    """If the host booted the newest image, `tail -n keep-1` over all of them
    returns that same image twice and autoremove takes the rollback kernel."""
    text = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    tail = text.index("tail -n $(( keep - 1 ))")
    assert 'grep -vx "$running"' in text[:tail], (
        "the running kernel has to leave the set before the newest others are chosen"
    )


def test_accounts_checks_the_declared_list_before_it_takes_the_password_away():
    """The guard used to read `authorized_keys` on disk. This role now writes that
    file from `trusted_ssh_keys`, so the file is no longer independent evidence:
    the guard has to read the declared list, and it has to run before the first
    write it is meant to protect."""
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    names = [task.get("name") for task in tasks if isinstance(task, dict)]
    guard = names.index("Refuse to close password logins without a declared key")
    write = names.index("Give the human account the keys declared in group_vars")
    sshd = names.index("State the password-login policy in the main sshd config")
    assert guard < write < sshd, "a guard that runs after the change protects nothing"
    checks = tasks[guard]["ansible.builtin.assert"]["that"]
    assert "trusted_ssh_keys | length > 0" in checks
    assert (
        "trusted_ssh_keys | reject('match', trusted_ssh_key_pattern) | list | length == 0"
        in checks
    ), "a list of the right length and the wrong shape is written all the same"
    assert not any("accounts_authorized_keys" in str(task) for task in tasks)


def test_the_collections_are_pinned_below_what_the_controller_supports():
    """community.general 14 needs ansible-core 2.19; the controller here is 2.18,
    and `make init` installed 14 anyway and then warned on every run."""
    text = (ANSIBLE / "Makefile").read_text()
    assert "community.general:>=13.0.0,<14.0.0" in text
    assert "ansible.posix:>=" in text and "community.docker:>=" in text


def test_the_union_is_owned_by_the_host_layer_with_three_options_changed():
    """The union's line was hand-written in /etc/fstab on `nas`, so a host
    rebuilt from this repository came back without it. The three changes are the
    point of the role: a failed write moves instead of dying, a move has a disk
    to go to, and a mount that cannot be made refuses instead of hiding writes
    in the empty mountpoint."""
    defaults = yaml.safe_load((ANSIBLE / "roles/union/defaults/main.yml").read_text())
    options = defaults["union_options"]
    assert defaults["union_mountpoint"] == "/mnt/storage"
    assert defaults["union_branches"] == "/mnt/hdd*"
    assert "moveonenospc=true" in options
    assert "minfreespace=100G" in options
    assert "nonempty" not in options
    for kept in (
        "use_ino",
        "inodecalc=path-hash",
        "noforget",
        "cache.files=off",
        "dropcacheonclose=true",
    ):
        assert kept in options, "the NFS recipe is not this role's to change"


def test_the_union_is_written_where_mergerfs_keeps_its_two_places():
    """`mount -o remount` is rejected by fuse on this host — rehearsed on `nas`
    with mergerfs 2.33.3, `fuse: unknown option 'remount'` — so a line in
    /etc/fstab only takes effect at the next boot. The running mount is the
    xattrs on `<mount>/.mergerfs`, which is the only runtime path mergerfs
    documents. `state: present` is the safe state here: `mounted` remounts."""
    tasks = yaml.safe_load((ANSIBLE / "roles/union/tasks/main.yml").read_text())
    written = [t for t in tasks if isinstance(t, dict) and "ansible.posix.mount" in t]
    assert len(written) == 1, "one task writes the line"
    mount = written[0]["ansible.posix.mount"]
    assert mount["state"] == "present", "`mounted` remounts, and fuse rejects that"
    assert mount["path"] == "{{ union_mountpoint }}"
    assert mount["backup"] is True, "the previous file is what a rollback restores"
    assert any(isinstance(t, dict) and t.get("ansible.builtin.script") for t in tasks), (
        "nothing converges the running mount"
    )
    text = (ANSIBLE / "roles/union/tasks/main.yml").read_text()
    assert "union-options.py --apply" in text
    assert "fuse: unknown option" in text, "the next reader has to know why not a remount"
    assert "is match('changed')" in text, (
        "the `script` module merges stderr into stdout, so the change marker is matched "
        "from the start of the output and never compared whole"
    )


def test_the_runtime_pair_is_the_form_mergerfs_reports():
    """`moveonenospc=true` is mergerfs's alias for the `mfs` policy, so the xattr
    the card checks — `xattr -p user.mergerfs.moveonenospc .mergerfs` — answers
    `mfs`, not `true`; and a size is printed in bytes, not as `100G`."""
    defaults = yaml.safe_load((ANSIBLE / "roles/union/defaults/main.yml").read_text())
    assert defaults["union_runtime"] == {
        "moveonenospc": "mfs",
        "minfreespace": str(100 * 1024**3),
    }


def test_the_union_role_runs_only_where_the_disks_are():
    site = (ANSIBLE / "playbooks/site.yml").read_text()
    assert "- role: union" in site
    tasks = yaml.safe_load((ANSIBLE / "roles/union/tasks/main.yml").read_text())
    assert any("group_names" in str(t) and "storage" in str(t) for t in tasks)
    assert site.index("- role: union") < site.index("- role: nfs"), (
        "the export table names paths inside the union, so the union mounts first"
    )


def test_the_union_script_writes_only_what_differs(tmp_path):
    """The drift report is the point: a converged union has to print no change.
    The script is therefore run against a directory whose xattrs stand in for
    the mount's control file."""
    import os
    import subprocess
    import sys

    control = tmp_path / ".mergerfs"
    control.write_text("")
    wanted = {"moveonenospc": "mfs", "minfreespace": "107374182400"}
    script = str(ANSIBLE / "roles/union/files/union-options.py")

    def run(*args):
        env = dict(os.environ, UNION_RUNTIME='{"moveonenospc": "mfs", "minfreespace": "107374182400"}')
        return subprocess.run(
            [sys.executable, script, "--control", str(control), *args],
            capture_output=True, text=True, env=env,
        )

    try:
        os.setxattr(control, "user.mergerfs.moveonenospc", b"false")
        os.setxattr(control, "user.mergerfs.minfreespace", b"53687091200")
    except OSError:
        import pytest

        pytest.skip("this filesystem does not carry user xattrs")

    read = run()
    assert read.returncode == 1, "a mount that differs from the file is a failure, not silence"
    assert "moveonenospc=false" in read.stdout
    assert "differs: minfreespace, moveonenospc" in read.stderr

    first = run("--apply")
    assert first.returncode == 0, first.stderr
    assert first.stdout.startswith("changed"), first.stdout
    assert "moveonenospc" in first.stdout
    assert all(
        os.getxattr(control, "user.mergerfs." + name).decode() == value
        for name, value in wanted.items()
    )

    second = run("--apply")
    assert second.returncode == 0
    assert second.stdout.strip() == "unchanged", "a converged mount reported a change"


def make_adopt(key=None, env=None):
    command = ["make", "-C", str(ANSIBLE), "adopt"]
    if key is not None:
        command.append("KEY=" + str(key))
    return subprocess.run(
        command, capture_output=True, text=True, env=env,
    )


def a_key(tmp_path, name="homelab_live", comment="operator@laptop", password=""):
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", password, "-C", comment,
         "-f", str(tmp_path / name)],
        check=True, capture_output=True,
    )
    return tmp_path / (name + ".pub")


def test_make_adopt_prints_a_line_that_loads_as_yaml(tmp_path):
    run = make_adopt(a_key(tmp_path))
    assert run.returncode == 0, run.stderr
    entry = next(line for line in run.stdout.splitlines() if line.startswith("  - "))
    assert yaml.safe_load("keys:\n" + entry)["keys"][0].startswith("ssh-ed25519 ")
    assert "fingerprint SHA256:" in run.stdout


def test_make_adopt_never_edits_the_inventory(tmp_path):
    before = (ANSIBLE / "inventory/group_vars/all.yml").read_bytes()
    assert make_adopt(a_key(tmp_path)).returncode == 0
    assert (ANSIBLE / "inventory/group_vars/all.yml").read_bytes() == before


def test_make_adopt_refuses_a_file_that_is_not_a_key(tmp_path):
    junk = tmp_path / "junk.pub"
    junk.write_text("hello\n")
    run = make_adopt(junk)
    assert run.returncode != 0
    assert "not an OpenSSH public key" in run.stderr


def test_make_adopt_refuses_a_public_key_without_its_private_half(tmp_path):
    pub = a_key(tmp_path)
    orphan = tmp_path / "orphan.pub"
    orphan.write_bytes(pub.read_bytes())
    run = make_adopt(orphan)
    assert run.returncode != 0
    assert "proves nothing on its own" in run.stderr


def test_make_adopt_refuses_a_mismatched_pair(tmp_path):
    a_key(tmp_path, "first")
    pub = a_key(tmp_path, "second")
    pub.write_bytes((tmp_path / "first.pub").read_bytes())
    run = make_adopt(pub)
    assert run.returncode != 0
    assert "does not match" in run.stderr


def test_make_adopt_survives_a_comment_that_would_break_yaml(tmp_path):
    run = make_adopt(a_key(tmp_path, comment='a"b@laptop'))
    assert run.returncode == 0, run.stderr
    entry = next(line for line in run.stdout.splitlines() if line.startswith("  - "))
    assert yaml.safe_load("keys:\n" + entry)["keys"][0].endswith('a"b@laptop')


def test_make_adopt_survives_a_backslash_in_the_comment(tmp_path):
    run = make_adopt(a_key(tmp_path, comment='a\\b@laptop'))
    assert run.returncode == 0, run.stderr
    entry = next(line for line in run.stdout.splitlines() if line.startswith("  - "))
    assert yaml.safe_load("keys:\n" + entry)["keys"][0].endswith('a\\b@laptop')


def test_make_adopt_reports_a_passphrase_protected_key(tmp_path):
    pub = a_key(tmp_path, password="secret")
    run = make_adopt(pub)
    assert run.returncode != 0
    assert "cannot be read without a passphrase" in run.stderr


def test_make_adopt_finds_the_default_key_without_a_key_argument(tmp_path):
    a_key(tmp_path)
    (tmp_path / ".ssh").mkdir()
    for half in ("homelab_live", "homelab_live.pub"):
        (tmp_path / half).rename(tmp_path / ".ssh" / half)
    run = make_adopt(env={**os.environ, "HOME": str(tmp_path)})
    assert run.returncode == 0, run.stderr
    entry = next(line for line in run.stdout.splitlines() if line.startswith("  - "))
    assert yaml.safe_load("keys:\n" + entry)["keys"][0].startswith("ssh-ed25519 ")


def test_make_adopt_prints_a_creation_command_that_works(tmp_path):
    """The hint names the file the target then reads, so `$(KEY)` ending in
    `.pub` sent `ssh-keygen` to write the private half under that name and the
    target refused its own output. The command is run here, not matched."""
    env = {**os.environ, "HOME": str(tmp_path)}
    missing = make_adopt(env=env)
    assert missing.returncode != 0, "a laptop with no key was adopted anyway"
    created = re.search(r"-f (\S+) creates it", missing.stderr)
    assert created, missing.stderr
    (tmp_path / ".ssh").mkdir()
    # The empty answer is the empty passphrase the runbook tells the operator to
    # accept; a real terminal prompts for it, a test has to supply it.
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", created.group(1)],
        check=True, input="", capture_output=True, text=True,
    )
    run = make_adopt(env=env)
    assert run.returncode == 0, run.stderr
    entry = next(line for line in run.stdout.splitlines() if line.startswith("  - "))
    assert yaml.safe_load("keys:\n" + entry)["keys"][0].startswith("ssh-ed25519 ")


def test_the_key_list_is_declared_and_well_formed():
    defaults = load("inventory/group_vars/all.yml")
    keys = defaults["trusted_ssh_keys"]
    assert isinstance(keys, list) and keys, "an empty list locks every host out"
    pattern = defaults["trusted_ssh_key_pattern"]
    malformed = [key for key in keys if not re.match(pattern, key)]
    assert malformed == [], malformed
    assert defaults["human_user"] == "vronteix"


def test_the_key_pattern_refuses_a_body_that_is_not_whole_base64_groups():
    """The shape check is the only one an offline run makes, so it has to refuse
    the ragged paste: a body whose length cannot be a base64 body at all."""
    pattern = load("inventory/group_vars/all.yml")["trusted_ssh_key_pattern"]
    assert re.match(pattern, "ssh-ed25519 QUJDRA operator@laptop") is None
    assert re.match(pattern, "ssh-ed25519 QUJDR operator@laptop") is None
    assert re.match(pattern, "ssh-ed25519 QUJD= operator@laptop") is None
    assert re.match(pattern, "ssh-ed25519 A operator@laptop") is None
    assert re.match(pattern, "ssh-ed25519 QUJDRA== operator@laptop") is not None
    assert re.match(pattern, "ssh-ed25519 QUJD operator@laptop") is not None


def test_the_list_is_refused_before_an_account_is_written():
    tasks = yaml.safe_load((ANSIBLE / "roles/automation/tasks/main.yml").read_text())
    asserts = [
        task["ansible.builtin.assert"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.assert" in task
    ]
    joined = "\n".join(str(check) for check in asserts)
    assert "trusted_ssh_keys is defined" in joined
    assert "trusted_ssh_keys | length > 0" in joined
    assert "reject('match', trusted_ssh_key_pattern)" in joined
    names = [task["name"] for task in tasks if isinstance(task, dict)]
    assert names.index("Refuse a key list that cannot be declared") < names.index(
        "Create the automation account, key-only"
    )


def test_the_automation_account_is_written_from_the_declared_list():
    tasks = yaml.safe_load((ANSIBLE / "roles/automation/tasks/main.yml").read_text())
    writes = [
        task["ansible.builtin.copy"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.copy" in task
        and str(task["ansible.builtin.copy"].get("dest", "")).endswith("/.ssh/authorized_keys")
    ]
    assert len(writes) == 1, writes
    write = writes[0]
    assert "trusted_ssh_keys | join" in write["content"]
    # The YAML scalar is double-quoted, so its `\n` are real newlines by the time
    # Jinja sees them: the join separator is a newline and the file ends with one.
    # A tidy-up into a single-quoted scalar would turn both into two characters
    # and write one long line holding two dead keys, so the two assertions below
    # exist to go red on it.
    assert "join('\n')" in write["content"], write["content"]
    assert write["content"].endswith("\n"), write["content"][-8:]
    assert "automation_user" in write["dest"]
    assert write["backup"] is True
    assert write["mode"] == "0600"


def test_no_task_reads_the_human_key_file_any_more():
    for path in yaml_files():
        assert "automation_key_source" not in path.read_text(), path
    role = (ANSIBLE / "roles/automation/tasks/main.yml").read_text()
    assert "remote_src" not in role
    assert "getent_passwd" not in role


def test_the_human_account_is_written_from_the_declared_list():
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    writes = [
        task["ansible.builtin.copy"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.copy" in task
        and str(task["ansible.builtin.copy"].get("dest", "")).endswith("/.ssh/authorized_keys")
    ]
    assert len(writes) == 1, writes
    write = writes[0]
    assert "trusted_ssh_keys | join" in write["content"]
    # Same double-quoted shape as the automation role: a single-quoted tidy-up
    # would pass backslash-n and write one line holding both keys, dead.
    assert "join('\n')" in write["content"], write["content"]
    assert write["content"].endswith("\n"), write["content"][-8:]
    assert write["owner"] == write["group"] == "{{ human_user }}"
    assert "human_user" in write["dest"]
    assert write["backup"] is True
    assert write["mode"] == "0600"
    names = [task["name"] for task in tasks if isinstance(task, dict)]
    assert names.index("Give the human account the keys declared in group_vars") < names.index(
        "Remove the passwordless sudo grant on the admins group"
    )


def test_accounts_refuses_to_close_password_logins_without_a_declared_key():
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    guards = [
        task["ansible.builtin.assert"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.assert" in task
    ]
    joined = "\n".join(str(check) for check in guards)
    assert "sshd_password_auth is defined" in joined
    assert "trusted_ssh_keys | length > 0" in joined
    assert "ansible_user | default('') != 'root'" in joined


def test_ansible_cfg_names_the_homelab_identity():
    cfg = (ANSIBLE / "ansible.cfg").read_text()
    assert "private_key_file = ~/.ssh/homelab_live" in cfg


def test_the_identity_is_named_in_one_place():
    for path in yaml_files():
        assert "ansible_ssh_private_key_file" not in path.read_text(), path
        assert "private_key_file" not in path.read_text(), path


def test_the_parity_read_accepts_a_syncs_lock_and_nothing_else():
    """A sync of the whole array holds the content lock for most of a day, and
    `snapraid status` then prints `Self test...`, writes `SnapRAID is already in
    use!` to stderr and exits 1. That is the array saying "busy", and taken as a
    failure it painted a correct node red at the end of the run for as long as
    the sync lasted — seen on `nas`, 2026-11-25, during the declared-key rollout.

    The condition is evaluated here rather than grepped, because "the lock answer
    is one escape and nothing else is" is the whole decision. `status` exits 2
    while blocks are unsynced, which on an array being written to is normal: that
    case prints the report and stays green.
    """
    tasks = yaml.safe_load((ANSIBLE / "roles/snapraid/tasks/main.yml").read_text())
    read = next(task for task in tasks if task.get("register") == "snapraid_status")
    failed_when = jinja2.Environment().compile_expression(read["failed_when"])

    def fails(rc, stdout, stderr):
        return bool(
            failed_when(snapraid_status=SimpleNamespace(rc=rc, stdout=stdout, stderr=stderr))
        )

    report = "SnapRAID status report\n  51 TB ...\n"
    assert fails(0, report, "") is False, "a clean read is green"
    assert fails(2, report, "") is False, "unsynced blocks print the report and are normal"
    assert fails(1, "Self test...\n", "SnapRAID is already in use!\n") is False, (
        "the lock answer is the array answering busy, not a broken host"
    )
    assert fails(1, "", "snapraid: error: cannot open /etc/snapraid.conf") is True
    assert fails(1, "Self test...\n", "") is True, "a run with no report and no lock answer fails"
