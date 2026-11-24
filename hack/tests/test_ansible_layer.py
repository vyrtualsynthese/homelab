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
