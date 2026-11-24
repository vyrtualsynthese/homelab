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


def test_no_playbook_grants_passwordless_root():
    for path in yaml_files():
        text = path.read_text()
        if "NOPASSWD: ALL" in text:
            assert "state: absent" in text, f"{path} writes passwordless root"


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


def test_accounts_sets_its_own_sshd_config_and_the_first_word_wins():
    """A drop-in numbered 50 can set PasswordAuthentication yes and win over the
    main file, because sshd takes the first value it reads. Ours has to sort
    ahead of it, and the role has to say so."""
    text = (ANSIBLE / "roles/accounts/tasks/main.yml").read_text()
    assert "sshd_config.d/00-homelab.conf" in text
    assert "first value" in text


def test_system_pins_the_running_kernel_before_any_autoremove():
    text = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    order = [text.index(needle) for needle in ("uname -r", "apt-mark", "autoremove")]
    assert order == sorted(order), "autoremove must come after the running kernel is pinned"


def test_system_keeps_two_kernels_and_sets_the_zone_from_one_variable():
    defaults = yaml.safe_load((ANSIBLE / "inventory/group_vars/all.yml").read_text())
    assert defaults["kernel_keep"] == 2
    assert defaults["timezone"] == "Europe/Paris"
    tasks = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    assert "{{ timezone }}" in tasks, "the zone comes from group_vars, not a literal in the role"
    assert "hwclock" not in tasks, "a Raspberry Pi has no RTC; the module would fail on the edge hosts"


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


def test_exports_drop_mnt_and_squash_root():
    tpl = (ANSIBLE / "roles/nfs/templates/exports.j2").read_text()
    assert "/mnt/storage/nfs/plexmedia" in tpl
    assert "no_root_squash" not in tpl
    assert "insecure" not in tpl
    assert "root_squash" in tpl
    lines = [l for l in tpl.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert len(lines) == 8, f"expected the eight the cluster mounts, got {len(lines)}"
    assert not any(l.split()[0] == "/mnt" for l in lines), "/mnt must not be exported"


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
    tpl = (ANSIBLE / "roles/nfs/templates/exports.j2").read_text()
    lines = [l for l in tpl.splitlines() if l.strip() and not l.strip().startswith("#")]
    for line in lines:
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
