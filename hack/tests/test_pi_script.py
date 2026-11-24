import os
import pathlib
import subprocess

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
