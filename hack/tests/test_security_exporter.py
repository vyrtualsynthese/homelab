"""The security exporter's values, and the one tie that keeps them honest.

`hack/check_exposure.py` owns the definition of a public host. The chart needs
the same list to count what answers the internet, and a copy that drifts is a
posture panel that says the door is shut while a route answers the world. This
test reads both and fails on a difference.
"""

import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hack"))

import check_exposure  # noqa: E402
import gen_docs  # noqa: E402

VALUES = ROOT / "cluster/helm/security/security-exporter/values.yaml"


class ReconciledConstantsTests(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load(VALUES.read_text())["securityExporter"]

    def test_the_public_host_list_is_the_checker_s_own(self):
        self.assertEqual(set(self.values["publicHosts"]), set(check_exposure.PUBLIC_HOSTS))

    def test_the_expected_counts_are_present_and_explained(self):
        expected = self.values["expected"]
        self.assertEqual(expected["ingressRoutes"], 73)
        self.assertEqual(expected["publicHosts"], 9)
        self.assertEqual(expected["privilegedWorkloads"], 9)
        # Every number is a claim about the cluster today; the note says when it
        # was read, so a stale constant is visible rather than plausible.
        self.assertIn("2026-11-25", self.values["expectedNote"])

    def test_the_page_and_the_threshold_hold_the_same_live_count(self):
        # The routes page publishes the live count from `gen_docs.LIVE_INGRESS_ROUTES`,
        # and the alert family compares against `expected.ingressRoutes`. Two copies of
        # one number, so neither may move alone.
        self.assertEqual(
            gen_docs.LIVE_INGRESS_ROUTES, self.values["expected"]["ingressRoutes"]
        )


class WhitelistTests(unittest.TestCase):
    def test_the_exporter_carries_no_network_range_at_all(self):
        # The exporter counts; it never decides. A cidr in its values would be a
        # second place a policy could live, and the first place to drift.
        text = VALUES.read_text()
        self.assertNotIn("cidr", text)
        self.assertNotIn("192.168", text)


class PostureTests(unittest.TestCase):
    """The counting is pure, so it is tested without a cluster."""

    def load(self):
        import importlib.util
        path = ROOT / "cluster/helm/security/security-exporter/files/exporter.py"
        spec = importlib.util.spec_from_file_location("security_exporter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_each_route_is_counted_against_the_gate_and_the_public_list(self):
        exporter = self.load()
        public = {"plex.kub.home.ashudev.com", "filebrowser.kub.home.ashudev.com"}
        documents = [
            # A public host whose only middleware is the header set: intended,
            # because the app authenticates itself.
            {"metadata": {"name": "filebrowser"},
             "spec": {"routes": [{"match": "Host(`filebrowser.kub.home.ashudev.com`)",
                                  "middlewares": [{"name": "security", "namespace": "kube-system"}]}]}},
            # An internal host with its LAN gate: the normal case.
            {"metadata": {"name": "sonarr"},
             "spec": {"routes": [{"match": "Host(`sonarr.kub.local`)",
                                  "middlewares": [{"name": "lan-only", "namespace": "kube-system"}]}]}},
            # An internal host with nothing at all: this is the finding.
            {"metadata": {"name": "leak"},
             "spec": {"routes": [{"match": "Host(`leak.kub.local`)", "middlewares": []}]}},
            # A host nobody put on the public list, with no gate: also a finding,
            # because it answers the internet the same way the nine do.
            {"metadata": {"name": "surprise"},
             "spec": {"routes": [{"match": "Host(`new.kub.home.ashudev.com`)", "middlewares": []}]}},
        ]
        posture = exporter.posture(documents, public)
        self.assertEqual(posture.routes, 4)
        self.assertEqual(posture.ungated, 2)   # leak + surprise
        # Only the allowlisted host: the surprise is exposed, and that is the
        # `ungated` family's job. Counting it here too would move two numbers for
        # one event and leave neither meaning one thing.
        self.assertEqual(posture.public, 1)

    def test_one_ingressroute_with_two_hosts_counts_both(self):
        exporter = self.load()
        documents = [{"spec": {"routes": [
            {"match": "Host(`a.kub.local`) || Host(`b.kub.local`)", "middlewares": []}]}}]
        self.assertEqual(exporter.posture(documents, set()).routes, 2)

    def test_the_gate_is_a_middleware_name_not_a_route_shape(self):
        exporter = self.load()
        # Traefik's CRD carries `middlewares` as {name, namespace} objects; the
        # render of a chart can also carry the legacy `namespace-name@kubernetescrd`
        # string. Both name the same middleware.
        self.assertTrue(exporter.gated({"spec": {"routes": [
            {"middlewares": [{"name": "lan-only", "namespace": "kube-system"}]}]}}))
        self.assertTrue(exporter.gated({"spec": {"routes": [
            {"middlewares": ["kube-system-lan-only@kubernetescrd"]}]}}))
        self.assertFalse(exporter.gated({"spec": {"routes": [
            {"middlewares": [{"name": "security", "namespace": "kube-system"}]}]}}))

    def test_a_workload_is_counted_once_however_many_pods_it_runs(self):
        # This is the rule the drift alert rests on. Pods are the only thing the
        # exporter can see, and a pod count is not a workload count: a Deployment
        # has one pod per replica, a DaemonSet has one per node, and a rollout
        # renames every pod. So the count is of distinct controllers, with the
        # ReplicaSet's pod-template hash stripped, and pods that are over or
        # belong to nobody are left out.
        exporter = self.load()
        pods = [
            # One Deployment, two pods, one ReplicaSet hash: one workload.
            {"metadata": {"name": "mktxp-7d9c8b6f4-abc12",
                          "ownerReferences": [{"kind": "ReplicaSet", "name": "mktxp-7d9c8b6f4"}]},
             "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
            {"metadata": {"name": "mktxp-7d9c8b6f4-def34",
                          "ownerReferences": [{"kind": "ReplicaSet", "name": "mktxp-7d9c8b6f4"}]},
             "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
            # A DaemonSet, one pod per node: still one workload. The privileged
            # container here is an init container, which the rule also reads.
            {"metadata": {"name": "smartctl-nas",
                          "ownerReferences": [{"kind": "DaemonSet", "name": "smartctl"}]},
             "spec": {"initContainers": [{"name": "init", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
            {"metadata": {"name": "smartctl-nuc0",
                          "ownerReferences": [{"kind": "DaemonSet", "name": "smartctl"}]},
             "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
            # A job that is over: not a running workload, whatever it declared.
            {"metadata": {"name": "sync-7xk2p",
                          "ownerReferences": [{"kind": "Job", "name": "sync"}]},
             "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Succeeded"}},
            # No owner at all: k3s's own pods, not a decision this repository made.
            {"metadata": {"name": "k3s-helper"},
             "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
        ]
        self.assertEqual(exporter.privileged(pods), 2)  # mktxp and smartctl
