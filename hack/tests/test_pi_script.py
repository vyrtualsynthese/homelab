import json
import os
import pathlib
import re
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ansible/pi/hosts-pi.sh"


def run(*args, root=None):
    env = dict(os.environ)
    if root:
        env["PI_ROOT"] = str(root)
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=env)


COMPOSE_HEADER = "--- /home/vronteix/dockers/docker-compose.yml\n"


def rendered_compose(host):
    """The compose block as text, newline-normalised, ending in one newline —
    the same shape `read_text()` gives the file written on the Pi."""
    out = run("render", host)
    assert out.returncode == 0, out.stderr
    assert COMPOSE_HEADER in out.stdout, out.stdout
    block = out.stdout.split(COMPOSE_HEADER, 1)[1]
    return block.split("\n--- ", 1)[0] + "\n"


def test_render_prints_the_pinned_images_for_each_pi():
    for host, port in (("rbp0", "51821"), ("rbp1", "51822")):
        out = run("render", host)
        assert out.returncode == 0, out.stderr
        assert "jacklul/pihole:2026.11.0" in out.stdout
        assert "wg-easy:15.5.0" in out.stdout
        assert port in out.stdout


def test_wg_easy_15_gets_its_settings_from_the_panel_not_the_environment():
    """v15 keeps its configuration in its own database and refuses to start
    when PASSWORD or PASSWORD_HASH is in the environment — which is what a v14
    env_file passed in. The three variables it does read are the ones that make
    the panel answer where the stack says it does."""
    compose = yaml.safe_load(
        (REPO / "ansible/playbooks/templates/runners/docker-compose.yml").read_text()
    )
    service = compose["services"]["wg-easy"]
    assert service["image"] == "ghcr.io/wg-easy/wg-easy:15.5.0"
    assert "env_file" not in service, (
        "wg-easy.env held PASSWORD_HASH, and v15 crash-loops on that variable"
    )
    environment = dict(item.split("=", 1) for item in service["environment"])
    assert environment == {
        "PORT": "5000",
        "INSECURE": "true",
        "DISABLE_IPV6": "true",
    }
    assert "5000:5000/tcp" in service["ports"]


def test_renovate_may_bump_wg_easy_again():
    """The hold at <15 existed only until the v14 config was migrated. Leaving
    it behind freezes the image on the release that carried the migration."""
    renovate = json.loads((REPO / ".github/renovate.json").read_text())
    holds = [
        rule
        for rule in renovate["packageRules"]
        if "ghcr.io/wg-easy/wg-easy" in rule.get("matchPackageNames", [])
    ]
    assert not holds, holds


def test_render_refuses_a_host_it_does_not_know():
    out = run("render", "rbp9")
    assert out.returncode == 2
    assert "rbp9" in out.stderr


def test_the_two_hosts_render_the_same_stack_apart_from_the_wireguard_port():
    rbp0 = rendered_compose("rbp0").replace("51821", "PORT")
    rbp1 = rendered_compose("rbp1").replace("51822", "PORT")
    assert rbp0 == rbp1


def test_each_host_gets_its_own_wireguard_port():
    assert "51822" not in rendered_compose("rbp0")
    assert "51821" not in rendered_compose("rbp1")
    assert "@WG_UDP_PORT@" not in rendered_compose("rbp0")


def test_the_placed_file_is_what_render_printed(tmp_path):
    root = tmp_path
    stack = root / "home/vronteix/dockers"
    stack.mkdir(parents=True)
    (stack / "pihole.env").write_text("FTLCONF_webserver_api_password=x\n")
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")

    assert run("setup", "rbp0", root=root).returncode == 0
    assert (stack / "docker-compose.yml").read_text() == rendered_compose("rbp0")
    assert b"\r\n" in (stack / "docker-compose.yml").read_bytes()


def test_setup_is_idempotent(tmp_path):
    root = tmp_path
    (root / "home/vronteix/dockers").mkdir(parents=True)
    (root / "home/vronteix/dockers/pihole.env").write_text("FTLCONF_webserver_api_password=x\n")
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")
    first = run("setup", "rbp1", root=root)
    assert first.returncode == 0, first.stderr
    second = run("setup", "rbp1", root=root)
    assert second.returncode == 0, second.stderr
    assert "changed=0" in second.stdout


def test_setup_refuses_when_an_env_file_is_missing(tmp_path):
    (tmp_path / "home/vronteix/dockers").mkdir(parents=True)
    out = run("setup", "rbp0", root=tmp_path)
    assert out.returncode == 1
    assert "pihole.env" in out.stderr


def test_verify_accepts_the_hosts_file_cloud_init_writes(tmp_path):
    """`manage_etc_hosts: true` in these images makes cloud-init rewrite
    /etc/hosts on every boot, in its own layout. A script that owned the file
    would be overwritten and then report a difference no run could settle —
    which is what rbp1 did after its unattended-upgrades reboot."""
    root = tmp_path / "pi"
    _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    (root / "etc/hosts").write_text(
        "# Your system has configured 'manage_etc_hosts' as True.\n"
        "127.0.1.1 rbp1 rbp1\n"
        "127.0.0.1 localhost\n"
    )

    out = run("verify", "rbp1", root=root)

    assert "etc/hosts" not in out.stdout, out.stdout
    assert out.returncode == 0, out.stdout


def test_verify_names_what_is_missing(tmp_path):
    (tmp_path / "home/vronteix/dockers").mkdir(parents=True)
    out = run("verify", "rbp0", root=tmp_path)
    assert out.returncode == 1
    assert "docker-compose.yml" in out.stdout


def test_the_root_device_is_read_not_assumed():
    text = SCRIPT.read_text()
    assert "sda" not in text
    assert "/proc/mounts" in text


def sh(code):
    return subprocess.run(["bash", "-c", f'source "{SCRIPT}"; {code}'],
                          capture_output=True, text=True, env=dict(os.environ))


def test_sourcing_the_script_does_not_run_a_verb():
    out = sh("echo ok")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_a_version_inside_its_band_matches_and_a_major_jump_does_not():
    assert sh('version_in_band "5:29.10.1-1~ubuntu.26.04" "5:29.*" && echo in').stdout.strip() == "in"
    assert sh('version_in_band "5:30.0-1~ubuntu.26.04" "5:29.*" || echo out').stdout.strip() == "out"
    assert sh('version_in_band "5:28.3.2-1~ubuntu.24.04" "5:29.*" || echo out').stdout.strip() == "out"


def test_the_pin_list_names_the_four_packages_once_each():
    out = sh("engine_pairs").stdout.split()
    assert out == ["docker-ce", "5:29.*", "docker-ce-cli", "5:29.*",
                   "containerd.io", "2.4.*", "docker-compose-plugin", "5:8.*"]


def test_engine_drift_checks_nothing_under_a_fixture():
    out = sh("ROOT=/tmp; engine_drift; echo rc=$?")
    assert "engine: not checked (PI_ROOT is set)" in out.stdout
    assert "rc=0" in out.stdout


def test_render_prints_the_engine_it_would_install():
    out = run("render", "rbp0")
    assert out.returncode == 0, out.stderr
    for pin in ("docker-ce=5:29.*", "docker-ce-cli=5:29.*",
                "containerd.io=2.4.*", "docker-compose-plugin=5:8.*"):
        assert pin in out.stdout
    assert "/etc/apt/keyrings/docker.asc" in out.stdout


def test_setup_leaves_the_engine_alone_in_a_fixture(tmp_path):
    root = tmp_path
    (root / "home/vronteix/dockers").mkdir(parents=True)
    (root / "home/vronteix/dockers/pihole.env").write_text("FTLCONF_webserver_api_password=x\n")
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")
    out = run("setup", "rbp1", root=root)
    assert out.returncode == 0, out.stderr
    assert "engine: not touched (PI_ROOT is set)" in out.stdout
    assert not (root / "etc/apt").exists()


def test_the_engine_suite_is_read_not_hard_coded():
    text = SCRIPT.read_text()
    assert "/etc/os-release" in text
    for codename in ("resolute", "noble", "jammy"):
        assert codename not in text


def test_verify_says_it_cannot_check_the_engine_in_a_fixture(tmp_path):
    (tmp_path / "home/vronteix/dockers").mkdir(parents=True)
    out = run("verify", "rbp0", root=tmp_path)
    assert "engine: not checked (PI_ROOT is set)" in out.stdout
    assert out.returncode == 1  # the stack is still missing, as before


def test_backup_then_restore_puts_the_stack_back(tmp_path):
    root = tmp_path / "pi"
    stack = root / "home/vronteix/dockers"
    stack.mkdir(parents=True)
    (stack / "pihole.env").write_text("FTLCONF_webserver_api_password=x\n")
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")

    assert run("setup", "rbp1", root=root).returncode == 0

    backup = tmp_path / "backup"
    out = run("backup", str(backup), "rbp1", root=root)
    assert out.returncode == 0, out.stderr
    assert (backup / "dockers/pihole.env").exists()
    assert (backup / "etc/hostname").exists()

    (stack / "docker-compose.yml").unlink()
    (root / "etc/hostname").unlink()

    out = run("restore", str(backup), "rbp1", root=root)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (stack / "docker-compose.yml").exists()


def test_the_template_does_not_start_a_second_router():
    compose = yaml.safe_load(
        (REPO / "ansible/playbooks/templates/runners/docker-compose.yml").read_text()
    )
    assert "traefik" not in compose["services"], (
        "neither host runs its own Traefik: the cluster's router owns 80/443 for "
        "*.kub.home.ashudev.com, and a copy here served --api.insecure=true with no "
        "certificate store of its own"
    )
    for host in ("rbp0", "rbp1"):
        assert "traefik" not in yaml.safe_load(rendered_compose(host))["services"], host


def test_setup_removes_a_service_the_template_no_longer_declares():
    """`docker compose up -d` starts what the file names and leaves the rest
    running. rbp0's live stack still has the router this template dropped."""
    text = SCRIPT.read_text()
    assert "--remove-orphans" in text


def test_setup_converges_every_service_before_recreating_pihole():
    """`up -d <service>` touches only that service. When a mounted file changed,
    the plain run must still happen first, or an image bump in the same run is
    left running the old container while verify (names and state only) says ok."""
    text = SCRIPT.read_text()
    plain = "docker compose up -d --remove-orphans >/dev/null"
    recreate = "docker compose up -d --force-recreate pihole >/dev/null"
    assert plain in text
    assert recreate in text, "pihole is recreated with a command of its own"
    assert text.index(plain) < text.index(recreate), "the full converge comes first"


def test_setup_rewrites_a_file_that_verify_would_flag(tmp_path):
    """A file missing its final newline: setup must repair it, not bless it.
    Otherwise verify flags it forever and setup's only answer is changed=0."""
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    placed = stack / "etc-dnsmasq.d/02-my-wildcard-dns.conf"
    placed.write_bytes(placed.read_bytes().rstrip(b"\n"))
    assert run("verify", "rbp1", root=root).returncode == 1

    out = run("setup", "rbp1", root=root)

    assert "changed=1" in out.stdout, out.stdout
    assert run("verify", "rbp1", root=root).returncode == 0


def test_setup_restarts_pihole_when_a_mounted_file_changed():
    """A bind mount is resolved at container start. Writing a config under a
    running pihole leaves the old mount in place, so the change is invisible."""
    text = SCRIPT.read_text()
    assert "--force-recreate pihole" in text
    assert "PI_ROOT is set" in text, "the docker branch stays behind the PI_ROOT guard"


def _pi_fixture(root):
    stack = root / "home/vronteix/dockers"
    stack.mkdir(parents=True)
    (stack / "pihole.env").write_text("FTLCONF_webserver_api_password=x\n")
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")
    (root / "boot/firmware").mkdir(parents=True)
    (root / "boot/firmware/cmdline.txt").write_text("console=serial0 root=PARTUUID=aaaa-01 rootwait\n")
    (root / "boot/firmware/config.txt").write_text("arm_64bit=1\n")
    return stack


CONFIG_FILES = (
    ("etc-pihole-updatelists/pihole-updatelists.conf", "pihole-updatelists.conf"),
    ("etc-dnsmasq.d/02-my-wildcard-dns.conf", "02-my-wildcard-dns.conf"),
)


def test_setup_places_the_two_mounted_config_files(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    for relative, template in CONFIG_FILES:
        placed = stack / relative
        assert placed.is_file(), relative
        assert placed.read_text() == (
            REPO / "ansible/playbooks/templates/runners" / template
        ).read_text()


def test_verify_names_each_config_file_that_differs(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    for relative, _ in CONFIG_FILES:
        placed = stack / relative
        kept = placed.read_bytes()
        placed.write_bytes(b"address=/wrong.local/10.0.0.1\n")
        out = run("verify", "rbp1", root=root)
        assert out.returncode == 1
        assert f"DIFFERS {placed}" in out.stdout
        placed.write_bytes(kept)


def test_a_directory_in_place_of_a_config_file_is_refused_not_deleted(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    victim = stack / "etc-dnsmasq.d/02-my-wildcard-dns.conf"
    victim.mkdir(parents=True)

    out = run("setup", "rbp1", root=root)

    assert out.returncode == 1
    assert str(victim) in out.stderr
    assert victim.is_dir(), "the script deletes nothing"


def test_restore_keeps_a_boot_line_it_cannot_check(tmp_path):
    """The backup's cmdline.txt names the disk the image was written to. Writing
    it back onto a different disk makes the Pi unbootable, and the machine that
    would fix it is the one that will not start."""
    root = tmp_path / "pi"
    _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    backup = tmp_path / "backup"
    assert run("backup", str(backup), "rbp1", root=root).returncode == 0
    (backup / "boot/cmdline.txt").write_text("console=serial0 root=PARTUUID=bbbb-02 rootwait\n")
    (root / "boot/firmware/cmdline.txt").unlink()
    (root / "boot/firmware/config.txt").unlink()

    out = run("restore", str(backup), "rbp1", root=root)

    assert out.returncode == 0, out.stdout + out.stderr
    assert "PARTUUID" in out.stderr
    assert not (root / "boot/firmware/cmdline.txt").exists()
    assert (root / "boot/firmware/config.txt").exists(), "config.txt carries no disk identity"


def test_verify_does_not_pass_a_stack_that_is_not_up():
    text = SCRIPT.read_text()
    assert "docker compose ps --all" in text, "ps without --all hides a stopped container"
    assert "docker compose config --services" in text, "the declared services are the ones to compare"


SETTINGS_HEADER = "--- /home/vronteix/dockers/pihole.settings\n"
NETPLAN_HEADER = "--- /etc/netplan/50-cloud-init.yaml\n"


def rendered_section(host, header):
    """One block of `render`'s output, normalised the way write_abs writes it."""
    out = run("render", host)
    assert out.returncode == 0, out.stderr
    assert header in out.stdout, out.stdout
    return out.stdout.split(header, 1)[1].split("\n--- ", 1)[0] + "\n"


def test_both_pis_get_the_same_pihole_settings():
    """On 2026-11-25 the two panels differed in four of 167 settings, because the
    file was hand-kept on each host. One rendered file is what makes that
    impossible: the password is the only thing left in pihole.env."""
    rbp0 = rendered_section("rbp0", SETTINGS_HEADER)
    rbp1 = rendered_section("rbp1", SETTINGS_HEADER)
    assert rbp0 == rbp1, "the two hosts must render byte-identical settings"
    for line in (
        "FTLCONF_dns_upstreams=86.54.11.100;86.54.11.200",
        "FTLCONF_dns_blockTTL=2",
        "FTLCONF_dns_rateLimit_count=1000",
        "FTLCONF_dns_listeningMode=ALL",
        "FTLCONF_dns_revServers=true,192.168.1.0/24,192.168.1.1,local",
    ):
        assert line in rbp0, line
    assert "WEBPASSWORD" not in rbp0, "the password is a secret and is never rendered"
    assert "FTLCONF_webserver_api_password" not in rbp0, "the v6 name of the same secret"
    assert not re.search(r"^PIHOLE_DNS_=", rbp0, re.M), "a v5 key FTL ignores, next to the one it reads"


def test_both_pis_resolve_through_pihole():
    """1.1.1.1 answered around the blocklist, and 127.0.0.1 is unreachable from
    inside a container — which is why rbp0's containers queried rbp1 instead."""
    for host in ("rbp0", "rbp1"):
        netplan = yaml.safe_load(rendered_section(host, NETPLAN_HEADER))
        addresses = netplan["network"]["ethernets"]["eth0"]["nameservers"]["addresses"]
        assert addresses == ["192.168.1.22", "192.168.1.38"], addresses
        assert "1.1.1.1" not in addresses
        assert "127.0.0.1" not in addresses


def test_setup_places_the_shared_settings_and_the_resolvers(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    assert (stack / "pihole.settings").read_text() == (
        REPO / "ansible/playbooks/templates/runners/pihole.settings"
    ).read_text()
    netplan = root / "etc/netplan/50-cloud-init.yaml"
    assert netplan.read_text() == rendered_section("rbp1", NETPLAN_HEADER)
    resolved = root / "etc/systemd/resolved.conf.d/90-pihole-resolvers.conf"
    assert resolved.read_text() == rendered_section("rbp1", RESOLVED_HEADER)
    assert run("verify", "rbp1", root=root).returncode == 0


def test_netplan_is_applied_only_on_a_real_host():
    """A live resolver change is not something a fixture run should perform."""
    text = SCRIPT.read_text()
    assert "netplan apply" in text
    apply_line = text.index("netplan apply")
    guard = text.rindex('-z "$ROOT"', 0, apply_line)
    assert guard > text.index("cmd_setup()"), "the apply sits behind the PI_ROOT guard"


def test_the_resolver_list_is_the_two_edge_addresses():
    """The script carries the two addresses because it runs on the host, where
    the inventory is not. The inventory stays the record, so they are compared."""
    netplan = yaml.safe_load(rendered_section("rbp0", NETPLAN_HEADER))
    addresses = netplan["network"]["ethernets"]["eth0"]["nameservers"]["addresses"]
    edge = yaml.safe_load(
        (REPO / "ansible/inventory/hosts.yml").read_text()
    )["all"]["children"]["edge"]["hosts"]
    assert addresses == [edge["rbp0"]["ansible_host"], edge["rbp1"]["ansible_host"]]


RESOLVED_HEADER = "--- /etc/systemd/resolved.conf.d/90-pihole-resolvers.conf\n"


def test_resolv_conf_lists_the_piholes_instead_of_the_loopback():
    """With the stub listener on, /etc/resolv.conf is 127.0.0.53, Docker drops
    that entry and falls back to 8.8.8.8 — the blocklist bypassed from inside
    the host that serves it. The drop-in also names no server: netplan does."""
    rbp0 = rendered_section("rbp0", RESOLVED_HEADER)
    rbp1 = rendered_section("rbp1", RESOLVED_HEADER)
    assert rbp0 == rbp1, "one file, no per-host server"
    assert "DNSStubListener=no" in rbp0
    assert "\nDNS=\n" in rbp0, "the nameservers come from netplan"
    assert "192.168.1." not in rbp0, "an address here would be a second record of it"


def test_the_wildcard_file_carries_every_local_override():
    """These six were also in FTLCONF_misc_dnsmasq_lines in each host's
    pihole.env, which settings now clears. Dropping one here would stop it
    resolving on the LAN, and `ashudev.com` only ever worked through that
    duplicate."""
    out = run("render", "rbp0")
    assert out.returncode == 0, out.stderr
    block = out.stdout.split("--- /home/vronteix/dockers/etc-dnsmasq.d/02-my-wildcard-dns.conf\n", 1)[1]
    block = block.split("\n--- ", 1)[0]
    for line in (
        "address=/livebox.local/192.168.1.1",
        "address=/kub.local/192.168.1.46",
        "address=/rbp0.local/192.168.1.22",
        "address=/rbp1.local/192.168.1.38",
        "address=/kub.home.ashudev.com/192.168.1.46",
        "address=/.ashudev.com/192.168.1.46",
    ):
        assert line in block, line


def test_the_netplan_file_is_not_world_readable(tmp_path):
    """netplan prints 'Permissions ... are too open' on every apply, and the file
    describes the host's addresses, so setup narrows it."""
    root = tmp_path / "pi"
    _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0

    mode = (root / "etc/netplan/50-cloud-init.yaml").stat().st_mode & 0o777

    assert mode == 0o600, oct(mode)


def test_every_setup_flag_is_initialised():
    """The script runs under `set -u`, so a flag used in the branch a fixture
    never reaches crashes a real host halfway through a rollout — which is how
    `resolver_changed` reached rbp1 once."""
    text = SCRIPT.read_text()
    setup = text[text.index("cmd_setup()"):]
    local = next(
        line for line in setup.splitlines() if line.strip().startswith("local host=")
    )
    used = {word.strip('"$') for word in text.split() if word.strip('"$').endswith("_changed")}
    for flag in sorted(used):
        assert f"{flag}=0" in local, flag
    assert "--force-recreate wg-easy smartctl-exporter" in text


def test_the_shared_settings_turn_on_the_dnsmasq_include():
    """Pi-hole v6 defaults misc.etc_dnsmasq_d to false, so the address= file was
    dead on both hosts — the overrides only worked through the env copy that
    settings now clears. Measured on rbp1: with the key, all six resolve again."""
    settings = rendered_section("rbp0", SETTINGS_HEADER)

    assert "FTLCONF_misc_etc_dnsmasq_d=true" in settings
    assert "\nFTLCONF_misc_dnsmasq_lines=\n" in settings
