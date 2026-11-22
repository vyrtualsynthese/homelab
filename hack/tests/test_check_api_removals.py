import pathlib
import sys
import tempfile
import unittest

HACK = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HACK))

import check_api_removals  # noqa: E402


class ServedVersionTests(unittest.TestCase):
    def test_a_removed_version_with_a_survivor_is_a_note(self):
        hits, notes = check_api_removals.check_served(
            {"flowcontrol.apiserver.k8s.io/v1", "flowcontrol.apiserver.k8s.io/v1beta3"}
        )
        self.assertEqual(hits, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("1.32", notes[0])

    def test_a_removed_version_with_no_survivor_is_a_hit(self):
        hits, notes = check_api_removals.check_served({"storagemigration.k8s.io/v1alpha1"})
        self.assertEqual(notes, [])
        self.assertEqual(len(hits), 1)
        self.assertIn("storagemigration.k8s.io/v1alpha1", hits[0])

    def test_an_unrelated_version_is_ignored(self):
        self.assertEqual(check_api_removals.check_served({"apps/v1", "batch/v1"}), ([], []))

    def test_the_note_names_the_surviving_version(self):
        _, notes = check_api_removals.check_served({"storage.k8s.io/v1", "storage.k8s.io/v1alpha1"})
        self.assertIn("storage.k8s.io/v1 ", notes[0] + " ")


class ObjectTests(unittest.TestCase):
    def pv(self, spec):
        return {"kind": "PersistentVolume", "metadata": {"name": "pv-x"}, "spec": spec}

    def test_a_portworx_volume_is_a_hit(self):
        hits = check_api_removals.check_object(self.pv({"portworxVolume": {"volumeID": "x"}}))
        self.assertEqual(len(hits), 1)
        self.assertIn("1.36", hits[0])

    def test_an_nfs_volume_is_clean(self):
        self.assertEqual(check_api_removals.check_object(self.pv({"nfs": {"server": "nas"}})), [])

    def test_another_kind_is_not_inspected(self):
        obj = {"kind": "Pod", "metadata": {"name": "p"}, "spec": {"portworxVolume": {}}}
        self.assertEqual(check_api_removals.check_object(obj), [])


class ManifestTests(unittest.TestCase):
    def test_a_removed_api_version_is_reported_with_its_line(self):
        text = "apiVersion: v1\nkind: List\n---\napiVersion: storage.k8s.io/v1alpha1\nkind: VolumeAttributesClass\n"
        hits = check_api_removals.check_manifest_text("cluster/x.yaml", text)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].startswith("cluster/x.yaml:4:"))
        self.assertIn("1.35", hits[0])

    def test_a_crlf_line_still_matches(self):
        hits = check_api_removals.check_manifest_text("cluster/x.yaml", "apiVersion: flowcontrol.apiserver.k8s.io/v1beta3\r\n")
        self.assertEqual(len(hits), 1)

    def test_a_surviving_version_is_clean(self):
        self.assertEqual(check_api_removals.check_manifest_text("x.yaml", "apiVersion: flowcontrol.apiserver.k8s.io/v1\n"), [])

    def test_the_tree_is_walked_for_yaml_only(self):
        root = pathlib.Path(tempfile.mkdtemp())
        (root / "cluster").mkdir()
        (root / "cluster" / "bad.yaml").write_text("apiVersion: storagemigration.k8s.io/v1alpha1\n")
        (root / "cluster" / "notes.md").write_text("apiVersion: storagemigration.k8s.io/v1alpha1\n")
        self.assertEqual(len(check_api_removals.check_manifests(root)), 1)


if __name__ == "__main__":
    unittest.main()
