import os
import pathlib
import subprocess
import tempfile
import unittest
import zipfile

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CHART = REPO / "cluster/helm/media-stack/qbittorrent"
VERSION = "2.37.0"
PREF = "WebUI\\AlternativeUIEnabled"

APK_STUB = "#!/bin/sh\nexit 0\n"

WGET_STUB = """#!/bin/sh
# wget -q -T 20 -O <file> <url>
echo called > "$STUB_LOG/wget.called"
[ "$DOWNLOAD_OK" = "1" ] || exit 1
target=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-O" ]; then target="$2"; shift; fi
  shift
done
cp "$ZIP_FIXTURE" "$target"
"""

UNZIP_STUB = """#!/bin/sh
# unzip -q <file> -d <dir>
file="$2"
dir="$4"
mkdir -p "$dir"
python3 -m zipfile -e "$file" "$dir"
"""

CHOWN_STUB = '#!/bin/sh\necho "$@" >> "$STUB_LOG/chown.log"\n'


def rendered_script():
    """The init container's shell script, as helm renders it."""
    out = subprocess.run(
        ["helm", "template", "qbittorrent", str(CHART), "--namespace", "media-stack"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    deployment = next(d for d in docs if d.get("kind") == "Deployment")
    init = next(
        c
        for c in deployment["spec"]["template"]["spec"]["initContainers"]
        if c["name"] == "init-qbittorrent"
    )
    return init["command"][2]


def make_zip(path, with_index=True):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("vuetorrent/version.txt", VERSION + "\n")
        if with_index:
            archive.writestr("vuetorrent/public/index.html", "<html>vuetorrent</html>\n")
    return path


class Sandbox:
    """A throwaway `/config`, a fake release asset and the stubs the script calls."""

    def __init__(self, root, conf=None, tree_version=None, tree_index=True, cached=False,
                 download_ok=True, zip_index=True):
        self.root = pathlib.Path(root)
        self.config = self.root / "config"
        self.config.mkdir()
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        self.log = self.root / "log"
        self.log.mkdir()
        self.zip_fixture = make_zip(self.root / "vuetorrent.zip", with_index=zip_index)

        for name, body in (
            ("apk", APK_STUB),
            ("wget", WGET_STUB),
            ("unzip", UNZIP_STUB),
            ("chown", CHOWN_STUB),
        ):
            stub = self.stubs / name
            stub.write_text(body)
            stub.chmod(0o755)

        if conf is not None:
            self.conf_file.parent.mkdir(parents=True)
            self.conf_file.write_text(conf)
        if tree_version is not None:
            ui = self.config / "ui"
            (ui / "public").mkdir(parents=True)
            (ui / "version.txt").write_text(tree_version + "\n")
            if tree_index:
                (ui / "public" / "index.html").write_text("<html>old</html>\n")
        if cached:
            make_zip(self.config / f"vuetorrent-{VERSION}.zip")

        self.download_ok = download_ok

    @property
    def conf_file(self):
        return self.config / "qBittorrent" / "qBittorrent.conf"

    @property
    def ui(self):
        return self.config / "ui"

    def run(self):
        env = {
            "PATH": f"{self.stubs}:{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "ZIP_FIXTURE": str(self.zip_fixture),
            "DOWNLOAD_OK": "1" if self.download_ok else "0",
            "VUETORRENT_REPOSITORY": "VueTorrent/VueTorrent",
            "VUETORRENT_VERSION": VERSION,
            "PUID": "1100",
            "PGID": "1100",
            "HOME": str(self.root),
        }
        script = rendered_script().replace("/config", str(self.config))
        return subprocess.run(
            ["sh", "-c", script], env=env, cwd=self.root, capture_output=True, text=True
        )


class InstallerPrefTests(unittest.TestCase):
    """The preference qBittorrent writes when the UI is missing must not survive an install."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def sandbox(self, **kwargs):
        return Sandbox(self._tmp.name, **kwargs)

    def run_ok(self, box):
        result = box.run()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def test_no_conf_is_created_with_pref(self):
        box = self.sandbox()
        self.run_ok(box)
        text = box.conf_file.read_text()
        self.assertIn("[Preferences]", text)
        self.assertIn(PREF + "=true", text)

    def test_false_pref_is_flipped(self):
        conf = f"[Preferences]\n{PREF}=false\n"
        box = self.sandbox(conf=conf)
        self.run_ok(box)
        text = box.conf_file.read_text()
        self.assertIn(PREF + "=true", text)
        self.assertEqual(text.count(PREF), 1)

    def test_absent_key_lands_in_preferences(self):
        conf = "[Preferences]\nWebUI\\Address=*\n[BitTorrent]\nSession\\Port=18515\n"
        box = self.sandbox(conf=conf)
        self.run_ok(box)
        lines = box.conf_file.read_text().splitlines()
        self.assertLess(lines.index("[Preferences]"), lines.index(PREF + "=true"))
        self.assertLess(lines.index(PREF + "=true"), lines.index("[BitTorrent]"))
        self.assertIn("WebUI\\Address=*", lines)

    def test_no_preferences_section_is_added(self):
        box = self.sandbox(conf="[BitTorrent]\nSession\\Port=18515\n")
        self.run_ok(box)
        lines = box.conf_file.read_text().splitlines()
        self.assertIn("[Preferences]", lines)
        self.assertLess(lines.index("[Preferences]"), lines.index(PREF + "=true"))
        self.assertIn("Session\\Port=18515", lines)

    def test_success_chowns_conf(self):
        box = self.sandbox()
        self.run_ok(box)
        log = (box.log / "chown.log").read_text()
        self.assertIn("1100:1100", log)
        self.assertIn(str(box.conf_file), log)


class InstallerTreeTests(unittest.TestCase):
    """Only a complete tree counts as installed, and only a good download is swapped in."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def sandbox(self, **kwargs):
        return Sandbox(self._tmp.name, **kwargs)

    def run_ok(self, box):
        result = box.run()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def test_matching_version_with_index_is_a_noop(self):
        box = self.sandbox(tree_version=VERSION)
        self.run_ok(box)
        self.assertFalse((box.log / "wget.called").exists())

    def test_matching_version_without_index_reinstalls(self):
        box = self.sandbox(tree_version=VERSION, tree_index=False)
        self.run_ok(box)
        self.assertTrue((box.log / "wget.called").exists())
        self.assertTrue((box.ui / "public" / "index.html").exists())

    def test_failed_download_uses_cached_zip(self):
        box = self.sandbox(cached=True, download_ok=False)
        self.run_ok(box)
        self.assertTrue((box.ui / "public" / "index.html").exists())

    def test_failed_download_without_cache_keeps_ui(self):
        box = self.sandbox(download_ok=False)
        result = self.run_ok(box)
        self.assertIn("WARNING", result.stdout)
        self.assertFalse(box.ui.exists())

    def test_truncated_zip_leaves_ui_and_pref(self):
        conf = f"[Preferences]\n{PREF}=false\n"
        box = self.sandbox(conf=conf, tree_version="2.36.0", zip_index=False)
        self.run_ok(box)
        self.assertEqual((box.ui / "version.txt").read_text().strip(), "2.36.0")
        self.assertIn(PREF + "=false", box.conf_file.read_text())
        chown_log = box.log / "chown.log"
        self.assertTrue(
            not chown_log.exists() or str(box.conf_file) not in chown_log.read_text()
        )

    def test_read_only_claim_exits_zero(self):
        box = self.sandbox()
        box.config.chmod(0o500)
        self.addCleanup(box.config.chmod, 0o700)
        result = self.run_ok(box)
        self.assertIn("WARNING", result.stdout)
        self.assertFalse(box.ui.exists())
