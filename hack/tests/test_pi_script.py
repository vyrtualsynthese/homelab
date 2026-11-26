import json
import os
import pathlib
import re
import sqlite3
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ansible/pi/hosts-pi.sh"


def run(*args, root=None, env=None):
    merged = dict(os.environ)
    if root:
        merged["PI_ROOT"] = str(root)
    merged.update(env or {})
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=merged)


COMPOSE_HEADER = "--- /home/vronteix/dockers/docker-compose.yml\n"


def rendered_compose(host):
    """The compose block as text, newline-normalised, ending in one newline —
    the same shape `read_text()` gives the file written on the Pi."""
    out = run("render", host)
    assert out.returncode == 0, out.stderr
    assert COMPOSE_HEADER in out.stdout, out.stdout
    block = out.stdout.split(COMPOSE_HEADER, 1)[1]
    return block.split("\n--- ", 1)[0] + "\n"


CADDYFILE_HEADER = "--- /home/vronteix/dockers/Caddyfile\n"


def rendered_caddyfile(host):
    """The Caddyfile block, shaped the same way."""
    out = run("render", host)
    assert out.returncode == 0, out.stderr
    assert CADDYFILE_HEADER in out.stdout, out.stdout
    block = out.stdout.split(CADDYFILE_HEADER, 1)[1]
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
    env_file passed in. What it does read is the panel's port, the proxy it sits
    behind, and the provider it signs in with."""
    compose = yaml.safe_load(
        (REPO / "ansible/playbooks/templates/runners/docker-compose.yml").read_text()
    )
    service = compose["services"]["wg-easy"]
    assert service["image"] == "ghcr.io/wg-easy/wg-easy:15.5.0"
    environment = dict(item.split("=", 1) for item in service["environment"])
    assert environment["PORT"] == "5000"
    assert environment["INSECURE"] == "false", "caddy terminates TLS in front of it"
    assert environment["TRUSTED_PROXIES"] == "172.30.0.0/24"
    assert environment["OAUTH_PROVIDERS"] == "oidc"
    assert environment["OAUTH_OIDC_CLIENT_ID"] == "wg-easy"
    assert "PASSWORD" not in environment
    assert "PASSWORD_HASH" not in environment
    assert service["env_file"] == ["./wg-easy.env"], (
        "the OIDC client secret is the secret, and it does not live in the template"
    )


def test_the_panel_is_not_published_and_caddy_is_the_only_way_in():
    """The finding this closes: 5000 answered plain HTTP on every interface.
    It is now reachable on the compose network alone, and caddy holds the only
    socket the LAN can open."""
    compose = yaml.safe_load(
        (REPO / "ansible/playbooks/templates/runners/docker-compose.yml").read_text()
    )
    assert compose["services"]["wg-easy"]["ports"] == [
        "@WG_UDP_PORT@:@WG_UDP_PORT@/udp"
    ], "only the WireGuard UDP port is published; the panel's 5000 is not"

    caddy = compose["services"]["caddy"]
    assert caddy["image"] == "caddy:2.11.9"
    assert caddy["ports"] == ["8443:8443"]
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    assert "./certs:/certs:ro" in caddy["volumes"]
    assert caddy["networks"] == ["panel"]
    assert compose["services"]["wg-easy"]["networks"] == ["panel"]
    assert compose["networks"]["panel"]["ipam"]["config"][0]["subnet"] == (
        "172.30.0.0/24"
    )


def test_the_pihole_panel_serves_a_leaf_from_the_internal_ca():
    """Pi-hole minted its own CN=pi.hole leaf, and both hosts presented the same
    one, so no client could verify it against the address it dialled. Each host
    now serves one combined PEM from the internal CA, named by
    `webserver.tls.cert`, and ./certs reaches the container read-only."""
    compose = yaml.safe_load(
        (REPO / "ansible/playbooks/templates/runners/docker-compose.yml").read_text()
    )
    assert "./certs:/certs:ro" in compose["services"]["pihole"]["volumes"]

    settings = (
        REPO / "ansible/playbooks/templates/runners/pihole.settings"
    ).read_text()
    assert "FTLCONF_webserver_tls_cert=/certs/pihole.pem" in settings

    for host, name in (("rbp0", "pihole-rbp0.kub.local"), ("rbp1", "pihole-rbp1.kub.local")):
        out = run("render", host)
        assert out.returncode == 0, out.stderr
        assert name in out.stdout


def test_each_pihole_name_wins_over_the_wildcard():
    """The cluster's exporter dials the name, so the record has to beat the
    `*.kub.local` wildcard above it; dnsmasq matches the longest suffix."""
    dns = (
        REPO / "ansible/playbooks/templates/runners/02-my-wildcard-dns.conf"
    ).read_text()
    assert "address=/pihole-rbp0.kub.local/192.168.1.22" in dns
    assert "address=/pihole-rbp1.kub.local/192.168.1.38" in dns


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


def test_each_host_serves_the_panel_on_its_own_name():
    """One label under the internal wildcard, one leaf, one port. The Caddyfile
    is the only rendered file that carries the name, so the compose stays
    byte-identical across the two hosts."""
    rbp0 = rendered_caddyfile("rbp0")
    rbp1 = rendered_caddyfile("rbp1")
    assert "https://vpn-rbp0.kub.local:8443 {" in rbp0
    assert "vpn-rbp1.kub.local" not in rbp0
    assert "https://vpn-rbp1.kub.local:8443 {" in rbp1
    for block in (rbp0, rbp1):
        assert "@PANEL_HOST@" not in block
        assert "tls /certs/tls.pem /certs/tls-key.pem" in block
        assert "reverse_proxy wg-easy:5000" in block
        assert "admin off" in block
        assert "auto_https off" in block


def test_setup_places_the_caddyfile_and_verify_names_a_different_one(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    placed = stack / "Caddyfile"
    assert placed.read_text() == rendered_caddyfile("rbp1")

    placed.write_text("https://wrong:8443 {\n}\n")
    out = run("verify", "rbp1", root=root)

    assert out.returncode == 1
    assert f"DIFFERS {placed}" in out.stdout


def test_setup_refuses_without_the_panel_secret_or_its_certificate(tmp_path):
    for missing in ("wg-easy.env", "certs/tls.pem", "certs/tls-key.pem", "certs/pihole.pem"):
        root = tmp_path / missing.replace("/", "-")
        stack = _pi_fixture(root)
        (stack / missing).unlink()

        out = run("setup", "rbp0", root=root)

        assert out.returncode == 1, missing
        assert missing in out.stderr, out.stderr


def test_the_placed_file_is_what_render_printed(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)

    assert run("setup", "rbp0", root=root).returncode == 0
    assert (stack / "docker-compose.yml").read_text() == rendered_compose("rbp0")
    assert b"\r\n" in (stack / "docker-compose.yml").read_bytes()


def test_setup_is_idempotent(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
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
    root = tmp_path / "pi"
    _pi_fixture(root)
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
    stack = _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0

    backup = tmp_path / "backup"
    out = run("backup", str(backup), "rbp1", root=root)
    assert out.returncode == 0, out.stderr
    assert (backup / "dockers/pihole.env").exists()
    assert (backup / "dockers/certs/tls-key.pem").exists(), (
        "the panel's TLS key leaves the host only inside the backup"
    )
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


SSHD_DROPIN = "etc/ssh/sshd_config.d/00-homelab.conf"

SSHD_CONFIG_FIXTURE = """\
# This is the sshd server system-wide configuration file.  See
# sshd_config(5) for more information.

Include /etc/ssh/sshd_config.d/*.conf

#PasswordAuthentication yes
PasswordAuthentication no
X11Forwarding yes
"""

SUDOERS_FIXTURE = """\
Defaults	env_reset
root	ALL=(ALL:ALL) ALL
%admin ALL=(ALL) ALL
%sudo	ALL=(ALL:ALL) ALL
@includedir /etc/sudoers.d
%admins ALL=(ALL) NOPASSWD: ALL
"""


def _pi_fixture(root):
    """A Pi as the 2026-11-24 measurement found it: a key in the human account,
    cloud-init's sshd drop-in ahead of the main file's own `no`, the `%admins`
    grant, and cloud-init's grant for the retired `ubuntu` account."""
    stack = root / "home/vronteix/dockers"
    stack.mkdir(parents=True)
    (stack / "pihole.env").write_text("FTLCONF_webserver_api_password=x\n")
    (stack / "wg-easy.env").write_text("OAUTH_OIDC_CLIENT_SECRET=x\n")
    (stack / "certs").mkdir()
    (stack / "certs/tls.pem").write_text("-----BEGIN CERTIFICATE-----\n")
    (stack / "certs/tls-key.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    (stack / "certs/pihole.pem").write_text("-----BEGIN CERTIFICATE-----\n")
    (root / "home/vronteix/.ssh").mkdir(parents=True)
    (root / "home/vronteix/.ssh/authorized_keys").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBIltfI3EkKuIHapGzdy1weSV5AiBigks/Pm4zR9Xqd5 valentin@laptop\n"
    )
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")
    (root / "boot/firmware").mkdir(parents=True)
    (root / "boot/firmware/cmdline.txt").write_text("console=serial0 root=PARTUUID=aaaa-01 rootwait\n")
    (root / "boot/firmware/config.txt").write_text("arm_64bit=1\n")
    (root / "etc/ssh/sshd_config.d").mkdir(parents=True)
    (root / "etc/ssh/sshd_config.d/50-cloud-init.conf").write_text("PasswordAuthentication yes\n")
    (root / "etc/ssh/sshd_config").write_text(SSHD_CONFIG_FIXTURE)
    (root / "etc/sudoers").write_text(SUDOERS_FIXTURE)
    (root / "etc/sudoers.d").mkdir(parents=True)
    (root / "etc/sudoers.d/90-cloud-init-users").write_text("ubuntu ALL=(ALL) NOPASSWD:ALL\n")
    env_file = root / "etc/restic/edge.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "RESTIC_REPOSITORY=s3:https://example.invalid/bucket/edge-rbp0\n"
        "RESTIC_PASSWORD=test\nAWS_ACCESS_KEY_ID=test\nAWS_SECRET_ACCESS_KEY=test\n"
    )
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


def test_setup_makes_the_vpn_database_unreadable_to_other_local_users(tmp_path):
    """A fresh container writes config/wg-easy.db 0644, and the file holds every
    peer's private key and the panel's password hash. Both Pis were narrowed by
    hand on 2026-11-26; a reflash, or a config directory the stack recreated,
    brings the readable copy back, so setup does it now."""
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    (stack / "config").mkdir()
    database = stack / "config/wg-easy.db"
    database.write_bytes(b"SQLite format 3\x00")
    database.chmod(0o644)

    out = run("setup", "rbp1", root=root)

    assert out.returncode == 0, out.stderr
    mode = database.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)
    assert f"wg-easy: {database} mode 644 -> 600" in out.stdout


def test_the_vpn_database_mode_is_narrowed_once_then_left_alone(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    (stack / "config").mkdir()
    database = stack / "config/wg-easy.db"
    database.write_bytes(b"SQLite format 3\x00")
    database.chmod(0o644)
    assert run("setup", "rbp1", root=root).returncode == 0

    second = run("setup", "rbp1", root=root)

    assert second.returncode == 0, second.stderr
    assert "changed=0" in second.stdout


def test_verify_names_a_readable_vpn_database(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    (stack / "config").mkdir()
    database = stack / "config/wg-easy.db"
    database.write_bytes(b"SQLite format 3\x00")
    assert run("setup", "rbp1", root=root).returncode == 0
    assert database.stat().st_mode & 0o777 == 0o600

    database.chmod(0o644)
    out = run("verify", "rbp1", root=root)

    assert out.returncode == 1
    assert f"DIFFERS {database} mode 644, expected 600" in out.stdout


def test_setup_does_not_invent_the_vpn_database_in_a_fixture(tmp_path):
    """The file is the container's; setup narrows the mode of what the panel
    wrote, and a host without a database is not one it can finish for it."""
    root = tmp_path / "pi"
    stack = _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0

    assert not (stack / "config/wg-easy.db").exists()


def test_setup_hands_the_pihole_leaf_to_the_panel_account(tmp_path):
    """Pi-hole's web server drops to the image's `pihole` user and reads the
    certificate as that account. Copied as the runbook says — 0600 root:root in
    a 0700 directory — it is unreadable, and FTL then refuses to start its web
    server at all rather than fall back to a self-signed pair: both panels went
    dark on 2026-11-26. setup hands the directory and the leaf to the account the
    stack runs as, and says so."""
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    certs = stack / "certs"
    pem = certs / "pihole.pem"
    owner = (root / "home/vronteix").stat()

    out = run("setup", "rbp1", root=root)

    assert out.returncode == 0, out.stderr
    assert certs.stat().st_mode & 0o777 == 0o700
    assert certs.stat().st_uid == owner.st_uid
    assert pem.stat().st_mode & 0o777 == 0o600
    assert pem.stat().st_uid == owner.st_uid
    assert f"certs: {certs}" in out.stdout
    assert f"certs: {pem}" in out.stdout


def test_setup_leaves_the_vpn_key_root_only_beside_the_leaf(tmp_path):
    """The Caddy key shares the directory, so widening the directory for the
    panel must not widen the file: the VPN panel's private key stays for the
    account that runs Caddy and nobody else."""
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    key = stack / "certs/tls-key.pem"

    assert run("setup", "rbp1", root=root).returncode == 0

    assert key.stat().st_mode & 0o777 == 0o600
    second = run("setup", "rbp1", root=root)

    assert second.returncode == 0, second.stderr
    assert "certs:" not in second.stdout
    assert "changed=0" in second.stdout


def test_verify_names_a_leaf_the_panel_cannot_read(tmp_path):
    """An unreadable leaf is a panel that will not answer on 443, and nothing in
    the rendered files shows it."""
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    pem = stack / "certs/pihole.pem"
    assert run("setup", "rbp1", root=root).returncode == 0

    pem.chmod(0o640)
    out = run("verify", "rbp1", root=root)

    assert out.returncode == 1
    assert f"DIFFERS {pem} mode 640, expected 600" in out.stdout


def test_verify_names_a_vpn_key_another_account_can_read(tmp_path):
    """The mode is the whole protection on that file: the directory is 0700 and
    owned by the stack account, so a wider file mode hands the key to it."""
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    key = stack / "certs/tls-key.pem"
    assert run("setup", "rbp1", root=root).returncode == 0

    key.chmod(0o640)
    out = run("verify", "rbp1", root=root)

    assert out.returncode == 1
    assert f"DIFFERS {key} mode 640 lets another account read the VPN key" in out.stdout


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
    assert "--force-recreate wg-easy caddy smartctl-exporter" in text


def test_the_shared_settings_turn_on_the_dnsmasq_include():
    """Pi-hole v6 defaults misc.etc_dnsmasq_d to false, so the address= file was
    dead on both hosts — the overrides only worked through the env copy that
    settings now clears. Measured on rbp1: with the key, all six resolve again."""
    settings = rendered_section("rbp0", SETTINGS_HEADER)

    assert "FTLCONF_misc_etc_dnsmasq_d=true" in settings
    assert "\nFTLCONF_misc_dnsmasq_lines=\n" in settings


def test_the_shared_settings_leave_room_for_api_sessions():
    """16 sessions last 30 minutes and an app password cannot close one, so three
    runs of the parity check in half an hour answered `api_seats_exceeded`."""
    assert "FTLCONF_webserver_api_max_sessions=64" in rendered_section("rbp0", SETTINGS_HEADER)


# ---------------------------------------------------------------------------------------------
# SSH and sudo: the two settings the layer owns on the cluster nodes and the Pis did not have.
# Measured on 2026-11-24 and again on 2026-11-26: `sshd -T` answered
# `passwordauthentication yes` on both Pis and /etc/sudoers carried the grant.
# ---------------------------------------------------------------------------------------------

ADMINS_GRANT = "%admins ALL=(ALL) NOPASSWD: ALL"
SSHD_HEADER = "--- /etc/ssh/sshd_config.d/00-homelab.conf\n"


def test_setup_closes_password_logins_with_a_dropin_that_sorts_first(tmp_path):
    """The main file already said `no`; cloud-init's own drop-in sorts first and
    wins, because sshd takes the first value it reads."""
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0

    dropin = root / SSHD_DROPIN
    assert dropin.is_file()
    assert "PasswordAuthentication no" in dropin.read_text()
    assert dropin.read_text().startswith("# Written by ansible/pi/hosts-pi.sh")
    mode = dropin.stat().st_mode & 0o777
    assert mode == 0o644, oct(mode)


def test_setup_leaves_cloud_inits_sshd_dropin_alone(tmp_path):
    """It is cloud-init's file, and a later boot may rewrite it. Ours sorts ahead
    of it instead, which is what makes `no` the answer."""
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0

    assert (root / "etc/ssh/sshd_config.d/50-cloud-init.conf").read_text() == (
        "PasswordAuthentication yes\n"
    )


def test_setup_closes_password_logins_and_x11_in_the_main_file(tmp_path):
    """The drop-in is the one that wins; the main file is written as well so the
    two records agree and a run that loses the drop-in is not a silent reopening."""
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0

    text = (root / "etc/ssh/sshd_config").read_text()
    assert "\nPasswordAuthentication no\n" in text
    assert "\nX11Forwarding no\n" in text
    assert "#PasswordAuthentication yes" in text, "a commented example is not a directive"


def test_setup_refuses_to_close_password_logins_without_a_key(tmp_path):
    """Taking the password away from a host with no key is a lockout, and the
    machine that would fix it is the one nobody can log in to."""
    root = tmp_path / "pi"
    _pi_fixture(root)
    (root / "home/vronteix/.ssh/authorized_keys").write_text("")

    out = run("setup", "rbp1", root=root)

    assert out.returncode == 1
    assert "authorized_keys" in out.stderr
    assert not (root / SSHD_DROPIN).exists(), "the guard runs before the change"


def test_verify_names_password_auth_still_on(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    (root / "etc/ssh/sshd_config.d/50-cloud-init.conf").write_text("PasswordAuthentication yes\n")
    (root / "etc/ssh/sshd_config").write_text(SSHD_CONFIG_FIXTURE.replace(
        "PasswordAuthentication no", "PasswordAuthentication yes"))

    out = run("verify", "rbp1", root=root)

    assert out.returncode == 1
    assert "PasswordAuthentication" in out.stdout


def test_setup_removes_the_admins_passwordless_grant(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0

    assert ADMINS_GRANT not in (root / "etc/sudoers").read_text()


def test_setup_leaves_the_rest_of_sudoers_alone(tmp_path):
    """%sudo stays as it ships: the human account keeps a password on sudo."""
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0

    text = (root / "etc/sudoers").read_text()
    assert "%sudo\tALL=(ALL:ALL) ALL" in text
    assert "root\tALL=(ALL:ALL) ALL" in text
    assert "@includedir /etc/sudoers.d" in text
    assert (root / "etc/sudoers").stat().st_mode & 0o777 == 0o440


def test_setup_removes_cloud_inits_sudoers_grant(tmp_path):
    """It names the `ubuntu` account the image's rename retired, so the next
    account to take that name is root without a password."""
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0

    assert not (root / "etc/sudoers.d/90-cloud-init-users").exists()


def test_verify_names_the_admins_grant(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    sudoers = root / "etc/sudoers"
    sudoers.chmod(0o640)
    sudoers.write_text(SUDOERS_FIXTURE)

    out = run("verify", "rbp1", root=root)

    assert out.returncode == 1
    assert "admins" in out.stdout


def test_the_sudoers_edit_is_validated_with_visudo():
    """A broken sudoers takes every account's sudo away at once, including the
    run that is writing it."""
    text = SCRIPT.read_text()
    assert "visudo -cf" in text
    edit = text.index("visudo -cf")
    assert text.rindex('-z "$ROOT"', 0, edit) > text.index("ensure_sudo_policy"), (
        "the validation sits behind the PI_ROOT guard, on a real host"
    )


def test_setup_reloads_sshd_only_on_a_real_host():
    text = SCRIPT.read_text()
    assert "systemctl reload ssh" in text
    reload = text.index("systemctl reload ssh")
    assert text.rindex('-z "$ROOT"', 0, reload) > text.index("ensure_sshd_policy")


def test_render_prints_the_ssh_and_sudo_state():
    out = run("render", "rbp0")
    assert out.returncode == 0, out.stderr
    assert SSHD_HEADER in out.stdout
    assert "PasswordAuthentication no" in out.stdout
    assert "X11Forwarding no" in out.stdout
    assert ADMINS_GRANT in out.stdout, "render names the line setup removes"


def test_the_pi_and_the_ansible_layer_write_the_same_ssh_policy():
    """The two records must not drift: the Pis cannot be reached by the layer, so
    a change to one alone leaves half the fleet on the other."""
    role = (REPO / "ansible/roles/accounts/tasks/main.yml").read_text()
    variables = (REPO / "ansible/inventory/group_vars/all.yml").read_text()
    rendered = rendered_section("rbp0", SSHD_HEADER)
    assert "sshd_password_auth: false" in variables
    assert "PasswordAuthentication {{ sshd_password_auth | ternary('yes', 'no') }}" in role
    assert "X11Forwarding no" in role
    for line in ("PasswordAuthentication no", "X11Forwarding no"):
        assert line in rendered, line


def test_the_pi_and_the_ansible_layer_remove_the_same_grant():
    role = (REPO / "ansible/roles/accounts/tasks/main.yml").read_text()
    assert f"line: '{ADMINS_GRANT}'" in role
    assert ADMINS_GRANT in SCRIPT.read_text()


FIREWALL_HEADER = "--- ufw (the host firewall, enabled by setup on a real host)\n"


def firewall_commands(host="rbp0"):
    """The rules `render` prints, in the order `ufw show added` keeps them."""
    block = rendered_section(host, FIREWALL_HEADER)
    return [line for line in block.splitlines() if line.startswith("ufw ")]


def expected_firewall_commands():
    """The edge rule set resolved the way `roles/firewall/tasks/resolve.yml`
    resolves it: one rule per address, in inventory order."""
    rules = yaml.safe_load(
        (REPO / "ansible/roles/firewall/vars/main.yml").read_text()
    )["firewall_rules_edge"]
    inventory = yaml.safe_load(
        (REPO / "ansible/inventory/hosts.yml").read_text()
    )["all"]["children"]
    cluster = [
        spec["ansible_host"] for spec in inventory["cluster"]["hosts"].values()
    ]
    lan = re.sub(r"\.\d+$", ".0/24", cluster[0])
    edge = yaml.safe_load(
        (REPO / "ansible/inventory/group_vars/edge.yml").read_text()
    )
    sources = {"lan": [lan], "nodes": cluster, "vpn": edge["vpn_cidrs"]}
    return [
        f"ufw allow from {address} to any port {rule['port']} "
        f"proto {rule['proto']} comment '{rule['comment']}'"
        for rule in rules
        for address in sources[rule["source"]]
    ]


def test_the_pi_and_the_ansible_layer_allow_the_same_ports():
    """The Pis cannot be reached by the layer, so `hosts-pi.sh` carries the rule
    set and the role keeps the record. A change to one alone leaves half the
    fleet on the other, which is the state both hosts were in before this."""
    assert firewall_commands() == expected_firewall_commands()


def test_the_edge_rule_set_opens_piholes_https_port():
    """Pi-hole's panel runs with network_mode: host, so its 443 reaches ufw's
    INPUT and a default deny takes it off the LAN — and the parity check with it.
    A published container port (caddy's 8443) would bypass INPUT instead."""
    rules = yaml.safe_load(
        (REPO / "ansible/roles/firewall/vars/main.yml").read_text()
    )["firewall_rules_edge"]
    https = {(rule["source"], rule["proto"]) for rule in rules if rule["port"] == 443}
    assert https == {("lan", "tcp"), ("vpn", "tcp")}


def test_render_names_the_published_ports_ufw_never_sees():
    """A rule that filters nothing reads as protection, so the ports Docker
    DNATs are named next to the ones ufw does filter."""
    block = rendered_section("rbp0", FIREWALL_HEADER)
    for port in ("51821/udp", "8443/tcp", "9902/tcp"):
        assert port in block, port


def test_setup_leaves_the_firewall_alone_under_a_fixture(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    out = run("setup", "rbp1", root=root)
    assert out.returncode == 0, out.stderr
    assert "firewall: not applied (PI_ROOT is set)" in out.stdout


def test_verify_says_it_cannot_check_the_firewall_under_a_fixture(tmp_path):
    (tmp_path / "home/vronteix/dockers").mkdir(parents=True)
    out = run("verify", "rbp0", root=tmp_path)
    assert "firewall: not checked (PI_ROOT is set)" in out.stdout
    assert out.returncode == 1  # the stack is still missing, as before


def test_setup_adds_every_rule_before_it_enables_the_firewall():
    """Enabling first is how a run loses its own SSH session: default deny lands
    before the rule that allows 22 from the LAN."""
    text = SCRIPT.read_text()
    body = text[text.index("ensure_firewall()"):]
    assert body.index("ufw allow from") < body.index("ufw --force enable")
    assert body.index("ufw default deny incoming") < body.index("ufw --force enable")
    assert "ufw default allow routed" in body, (
        "without allowed routing the containers lose their egress"
    )


UFW_STUB = """#!/bin/bash
# The three calls ensure_firewall makes, with the state they change.
case "$1" in
  show)
    if [ -s "$UFW_STATE/added" ]; then
      echo "Added user rules (see 'ufw status' for running firewall):"
      cat "$UFW_STATE/added"
    fi
    ;;
  status)
    if [ -f "$UFW_STATE/active" ]; then
      echo "Status: active"
      echo "Default: deny (incoming), allow (outgoing), allow (routed)"
    else
      echo "Status: inactive"
    fi
    ;;
  allow)
    printf "ufw allow from %s to any port %s proto %s comment '%s'\\n" \\
      "$3" "$7" "$9" "${11}" >> "$UFW_STATE/added"
    ;;
  default)
    printf '%s\\n' "$2 $3" >> "$UFW_STATE/defaults"
    ;;
  --force)
    touch "$UFW_STATE/active"
    ;;
esac
"""


def test_ensure_firewall_applies_every_rule_and_is_idempotent(tmp_path):
    """The apply path cannot run in a fixture, so it is driven against a stub:
    every declared rule reaches ufw once, the defaults and the enable land after
    them, and a second run changes nothing."""
    state = tmp_path / "state"
    state.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "ufw"
    stub.write_text(UFW_STUB)
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               UFW_STATE=str(state), SSH_CLIENT="")
    code = (
        f'source "{SCRIPT}"; ROOT=""; CHANGED=0; '
        "ensure_firewall >/dev/null; echo first=$CHANGED; "
        "CHANGED=0; ensure_firewall >/dev/null; echo second=$CHANGED"
    )
    out = subprocess.run(["bash", "-c", code], capture_output=True, text=True, env=env)

    assert out.returncode == 0, out.stderr
    assert "first=19" in out.stdout, out.stdout  # 17 rules, the defaults, the enable
    assert "second=0" in out.stdout, out.stdout
    assert sorted((state / "added").read_text().splitlines()) == sorted(
        firewall_commands()
    )
    assert (state / "defaults").read_text().splitlines() == ["deny incoming", "allow routed"]
    assert (state / "active").exists()
BACKUP_EXCLUDED = ("pihole-FTL.db", "pihole-FTL.db-wal", "listsCache", "gravity_old.db")


def fake_restic(tmp_path, exit_code=0, keep=None):
    """A restic that records its argv instead of contacting a bucket.

    `keep` copies the last argument (the staged tree) before exiting, which is how
    a test reads what the run staged once the run has cleaned up after itself.
    """
    binary = tmp_path / "restic"
    log = tmp_path / "restic-argv"
    keep_cmd = (
        f'for arg in "$@"; do last="$arg"; done; cp -a "$last" {keep}'
        if keep else ":"
    )
    binary.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\n{keep_cmd}\nexit {exit_code}\n'
    )
    binary.chmod(0o755)
    return {"PI_RESTIC": str(binary), "PI_RESTIC_LOG": str(log)}


def _sqlite(path, table="domainlist", column="domain", value="ads.example"):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, {column} TEXT)")
    con.execute(f"INSERT INTO {table} ({column}) VALUES (?)", (value,))
    con.commit()
    con.close()


def _stack_with_excludables(root):
    stack = root / "home/vronteix/dockers"
    pihole = stack / "etc-pihole"
    pihole.mkdir(parents=True)
    _sqlite(pihole / "gravity.db")
    (pihole / "gravity_old.db").write_bytes(b"old")
    for name in ("pihole-FTL.db", "pihole-FTL.db-wal"):
        (pihole / name).write_bytes(b"x")
    (pihole / "listsCache").mkdir()
    (pihole / "listsCache/a.txt").write_text("x")
    (stack / "config").mkdir()
    _sqlite(stack / "config/wg-easy.db", table="client", column="name", value="phone")
    return stack


def test_stream_copies_a_live_database_through_the_sqlite_api(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    stack = _stack_with_excludables(root)
    live = stack / "etc-pihole/gravity.db"
    # The writer is still attached: the committed row sits in the -wal, not in
    # the main file, which is exactly why a byte copy is not a backup.
    con = sqlite3.connect(live)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("INSERT INTO domainlist (domain) VALUES ('live.example')")
    con.commit()
    assert (live.parent / "gravity.db-wal").exists()

    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path, keep=tmp_path / "kept"))
    con.close()
    assert out.returncode == 0, out.stdout + out.stderr

    copy = tmp_path / "kept/dockers/etc-pihole/gravity.db"
    check = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    rows = check.execute("SELECT domain FROM domainlist ORDER BY domain").fetchall()
    check.close()
    assert rows == [("ads.example",), ("live.example",)]
    assert not (copy.parent / "gravity.db-wal").exists()
    assert not (copy.parent / "gravity.db-shm").exists()


def test_stream_refuses_a_database_it_cannot_read(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    _stack_with_excludables(root)
    (root / "home/vronteix/dockers/etc-pihole/broken.db").write_text("not a database")

    out = run("stream", "rbp0", root=root)
    assert out.returncode != 0
    assert "broken.db" in out.stderr


def test_stream_runs_restic_as_the_host_with_its_stream_tag(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    _stack_with_excludables(root)

    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode == 0, out.stdout + out.stderr
    argv = (tmp_path / "restic-argv").read_text()
    assert "backup" in argv
    assert "--host rbp0" in argv
    assert "--tag stream=edge-rbp0" in argv
    assert "--retry-lock 2h" in argv
    assert str(root / "var/lib/pi-backup/tree") in argv


def test_stream_fails_when_the_restic_run_fails(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    _stack_with_excludables(root)

    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path, exit_code=1))
    assert out.returncode != 0


def test_stream_refuses_without_the_env_file(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    _stack_with_excludables(root)
    (root / "etc/restic/edge.env").unlink()

    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode != 0
    assert "/etc/restic/edge.env" in out.stderr


def test_stream_leaves_no_tree_behind_after_a_successful_run(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    _stack_with_excludables(root)

    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode == 0, out.stdout + out.stderr
    assert not (root / "var/lib/pi-backup/tree").exists()


def test_stream_stages_the_stack_without_the_three_excluded_paths(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    _stack_with_excludables(root)

    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path, keep=tmp_path / "kept"))
    assert out.returncode == 0, out.stdout + out.stderr
    tree = tmp_path / "kept"
    assert (tree / "dockers/etc-pihole/gravity.db").exists()
    assert (tree / "dockers/pihole.env").exists()
    assert (tree / "dockers/config/wg-easy.db").exists()
    for name in BACKUP_EXCLUDED:
        assert not (tree / "dockers/etc-pihole" / name).exists(), name


def test_the_manual_copy_still_holds_the_query_log(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    (root / "home/vronteix/dockers/etc-pihole").mkdir(parents=True)
    (root / "home/vronteix/dockers/etc-pihole/pihole-FTL.db").write_bytes(b"x")
    dest = tmp_path / "usb"
    out = run("backup", str(dest), "rbp0", root=root)
    assert out.returncode == 0, out.stderr
    assert (dest / "dockers/etc-pihole/pihole-FTL.db").exists(), (
        "the exclusions belong to the nightly stream; the pre-reflash USB copy keeps everything"
    )


def test_prune_forgets_with_the_charts_retention(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)

    out = run("prune", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode == 0, out.stdout + out.stderr
    argv = (tmp_path / "restic-argv").read_text()
    for flag in ("forget", "--keep-daily 7", "--keep-weekly 4", "--keep-monthly 12", "--prune",
                 "--retry-lock 2h"):
        assert flag in argv, flag


def test_the_pi_retention_matches_the_chart():
    values = yaml.safe_load((REPO / "cluster/helm/backup/values.yaml").read_text())["retention"]
    out = subprocess.run(
        ["bash", "-c", f'. "{SCRIPT}"; echo "$RETENTION_DAILY $RETENTION_WEEKLY $RETENTION_MONTHLY"'],
        capture_output=True, text=True,
    )
    assert out.stdout.split() == [str(values["keepDaily"]), str(values["keepWeekly"]),
                                  str(values["keepMonthly"])]


def test_setup_writes_the_backup_units_for_its_host(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0
    service = (root / "etc/systemd/system/pi-backup.service").read_text()
    assert "ExecStart=/usr/local/sbin/hosts-pi.sh stream rbp1" in service
    assert "EnvironmentFile=/etc/restic/edge.env" in service
    timer = (root / "etc/systemd/system/pi-backup.timer").read_text()
    assert "OnCalendar=*-*-* 04:30:00" in timer
    assert "RandomizedDelaySec=15min" in timer
    assert "Persistent=true" in timer
    prune = (root / "etc/systemd/system/pi-backup-prune.timer").read_text()
    assert "OnCalendar=Sun *-*-* 05:30:00" in prune


def test_setup_installs_the_script_where_root_will_run_it(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp1", root=root).returncode == 0
    installed = root / "usr/local/sbin/hosts-pi.sh"
    assert installed.exists()
    assert (installed.stat().st_mode & 0o777) == 0o555
    assert installed.read_text() == SCRIPT.read_text()


def test_setup_retires_the_legacy_cron_and_its_dead_keys(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    cron = root / "var/spool/cron/crontabs/root"
    cron.parent.mkdir(parents=True)
    cron.write_text("0 4 * * * /home/vronteix/scripts/backup.sh\nMAILTO=root\n")
    env = root / "etc/environment"
    env.write_text("PATH=/usr/bin:/bin\nRESTIC_PASSWORD=x\nAWS_ACCESS_KEY_ID=x\n"
                   "AWS_SECRET_ACCESS_KEY=x\nRESTIC_REPOSITORY=x\n")

    assert run("setup", "rbp1", root=root).returncode == 0
    assert "backup.sh" not in cron.read_text()
    assert "MAILTO=root" in cron.read_text()
    assert env.read_text() == "PATH=/usr/bin:/bin\n"


def test_setup_converges_a_host_that_never_had_the_legacy_job(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)

    assert run("setup", "rbp0", root=root).returncode == 0


def test_setup_is_idempotent_for_the_retirement(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    cron = root / "var/spool/cron/crontabs/root"
    cron.parent.mkdir(parents=True)
    cron.write_text("0 4 * * * /home/vronteix/scripts/backup.sh\nMAILTO=root\n")

    assert run("setup", "rbp1", root=root).returncode == 0
    first = cron.read_text()
    out = run("setup", "rbp1", root=root)
    assert out.returncode == 0, out.stderr
    assert cron.read_text() == first


def test_setup_installs_nothing_and_touches_no_service_under_pi_root(tmp_path):
    """A developer machine must not gain a restic binary or a started unit."""
    root = tmp_path / "pi"
    _pi_fixture(root)

    out = run("setup", "rbp0", root=root)
    assert out.returncode == 0, out.stderr
    assert "restic: not installed under PI_ROOT" in out.stdout
    assert "systemd: not touched under PI_ROOT" in out.stdout


def test_render_prints_the_units_it_would_write():
    out = run("render", "rbp0")
    assert out.returncode == 0, out.stderr
    assert "--- /etc/systemd/system/pi-backup.service" in out.stdout
    assert "stream rbp0" in out.stdout
    assert "0.19.1" in out.stdout


def test_verify_names_each_new_file_that_differs(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    assert run("setup", "rbp0", root=root).returncode == 0
    (root / "etc/systemd/system/pi-backup.timer").write_text("[Timer]\nOnCalendar=daily\n")

    out = run("verify", "rbp0", root=root)
    assert out.returncode == 1
    assert "pi-backup.timer" in out.stdout


def test_verify_names_a_missing_env_file_and_a_legacy_cron(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    (root / "etc/restic/edge.env").unlink()
    cron = root / "var/spool/cron/crontabs/root"
    cron.parent.mkdir(parents=True, exist_ok=True)
    cron.write_text("0 4 * * * /home/vronteix/scripts/backup.sh\n")

    out = run("verify", "rbp1", root=root)
    assert out.returncode == 1
    assert "/etc/restic/edge.env" in out.stdout
    assert "scripts/backup.sh" in out.stdout
