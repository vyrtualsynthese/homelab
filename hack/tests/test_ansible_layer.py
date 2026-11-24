import pathlib

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


def test_the_automation_account_takes_its_key_from_the_human_account():
    """The controller's `~/.ssh` is off limits and a `*.pub` in the tree is a
    committed identity, so the key is copied on the host, from the account that
    already logs in: the role trusts nothing it did not trust before."""
    text = (ANSIBLE / "roles/automation/tasks/main.yml").read_text()
    assert "remote_src: true" in text
    assert "lookup('file'" not in text, "a key read on the controller"
    assert "automation_key_source_user" in text
    assert "authorized_keys" in text


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
    assert len(lines) == 8, f"expected the eight the cluster mounts, got {len(lines)}"
    assert not any(line.split()[0] == "/mnt" for line in lines), "/mnt must not be exported"


def test_the_certificate_store_belongs_to_the_squashed_identity():
    """Traefik runs as root and writes acme.json. Squashing root without giving
    that store to 1100 breaks certificate renewal on a delay nobody watches."""
    text = (ANSIBLE / "roles/nfs/tasks/main.yml").read_text()
    assert "/mnt/storage/traefik" in text
    assert "1100" in text
    tpl = (ANSIBLE / "roles/nfs/templates/exports.j2").read_text()
    assert "anonuid=1100" in tpl, "fsid 8 maps the squashed root back to the writer"


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
    files the app itself owns. 1100 is what every writer here runs as."""
    for line in export_lines():
        assert "anonuid=1100" in line and "anongid=1100" in line, line


def test_the_read_back_checks_the_table_not_its_line_count():
    """`exportfs -v` prints one line per export and client, so a count of 8 is
    true only while eight things mount."""
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


def test_accounts_checks_for_a_key_before_it_takes_the_password_away():
    text = (ANSIBLE / "roles/accounts/tasks/main.yml").read_text()
    guard = text.index("authorized_keys")
    write = text.index("PasswordAuthentication {{")
    assert guard < write, "a guard that runs after the change protects nothing"
    assert "getent" in text


def test_the_collections_are_pinned_below_what_the_controller_supports():
    """community.general 14 needs ansible-core 2.19; the controller here is 2.18,
    and `make init` installed 14 anyway and then warned on every run."""
    text = (ANSIBLE / "Makefile").read_text()
    assert "community.general:>=13.0.0,<14.0.0" in text
    assert "ansible.posix:>=" in text and "community.docker:>=" in text
