import os
import pathlib
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ansible/pi/hosts-pi.sh"


def run(*args, root=None):
    env = dict(os.environ)
    if root:
        env["PI_ROOT"] = str(root)
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_render_prints_the_pinned_images_for_each_pi():
    for host, port in (("rbp0", "51821"), ("rbp1", "51822")):
        out = run("render", host)
        assert out.returncode == 0, out.stderr
        assert "jacklul/pihole:2026.11.0" in out.stdout
        assert "wg-easy:15" in out.stdout
        assert port in out.stdout


def test_render_refuses_a_host_it_does_not_know():
    out = run("render", "rbp9")
    assert out.returncode == 2
    assert "rbp9" in out.stderr


def test_setup_is_idempotent(tmp_path):
    root = tmp_path
    (root / "home/vronteix/dockers").mkdir(parents=True)
    (root / "home/vronteix/dockers/pihole.env").write_text("WEBPASSWORD=x\n")
    (root / "home/vronteix/dockers/wg-easy.env").write_text("WG_HOST=x\n")
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


def test_verify_names_what_is_missing(tmp_path):
    (tmp_path / "home/vronteix/dockers").mkdir(parents=True)
    out = run("verify", "rbp0", root=tmp_path)
    assert out.returncode == 1
    assert "docker-compose.yml" in out.stdout


def test_the_root_device_is_read_not_assumed():
    text = SCRIPT.read_text()
    assert "sda" not in text
    assert "/proc/mounts" in text


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
    (root / "home/vronteix/dockers/pihole.env").write_text("WEBPASSWORD=x\n")
    (root / "home/vronteix/dockers/wg-easy.env").write_text("WG_HOST=x\n")
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
    (stack / "pihole.env").write_text("WEBPASSWORD=x\n")
    (stack / "wg-easy.env").write_text("WG_HOST=x\n")
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


def test_the_rbp0_template_does_not_start_a_second_router():
    compose = yaml.safe_load(
        (REPO / "ansible/playbooks/templates/runners/rbp0/docker-compose.yml").read_text()
    )
    assert "traefik" not in compose["services"], (
        "rbp0 must not run its own Traefik: the cluster's router owns 80/443 for "
        "*.kub.home.ashudev.com, and this copy served --api.insecure=true with no "
        "certificate store of its own"
    )


def test_setup_removes_a_service_the_template_no_longer_declares():
    """`docker compose up -d` starts what the file names and leaves the rest
    running. rbp0's live stack still has the router this template dropped."""
    text = SCRIPT.read_text()
    assert "--remove-orphans" in text


def _pi_fixture(root):
    stack = root / "home/vronteix/dockers"
    stack.mkdir(parents=True)
    (stack / "pihole.env").write_text("WEBPASSWORD=x\n")
    (stack / "wg-easy.env").write_text("WG_HOST=x\n")
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")
    (root / "boot/firmware").mkdir(parents=True)
    (root / "boot/firmware/cmdline.txt").write_text("console=serial0 root=PARTUUID=aaaa-01 rootwait\n")
    (root / "boot/firmware/config.txt").write_text("arm_64bit=1\n")
    return stack


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
