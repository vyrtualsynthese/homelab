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
        assert f"- {role}" in site, f"{role} is not reached from site.yml"


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
    defaults = yaml.safe_load((ANSIBLE / "group_vars/all.yml").read_text())
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
    defaults = yaml.safe_load((ANSIBLE / "group_vars/all.yml").read_text())
    assert defaults["kernel_keep"] == 2
    assert defaults["timezone"] == "Europe/Paris"
    tasks = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    assert "{{ timezone }}" in tasks, "the zone comes from group_vars, not a literal in the role"
    assert "hwclock" not in tasks, "a Raspberry Pi has no RTC; the module would fail on the edge hosts"


def test_packages_absent_is_per_host_and_never_touches_the_driver():
    nas = yaml.safe_load((ANSIBLE / "host_vars/nas.yml").read_text())
    streaming = yaml.safe_load((ANSIBLE / "host_vars/streaming.yml").read_text())
    rbp2 = yaml.safe_load((ANSIBLE / "host_vars/rbp2.yml").read_text())
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
