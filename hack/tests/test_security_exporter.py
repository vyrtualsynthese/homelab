"""The security exporter's values, and the one tie that keeps them honest.

`hack/check_exposure.py` owns the definition of a public host. The chart needs
the same list to count what answers the internet, and a copy that drifts is a
posture panel that says the door is shut while a route answers the world. This
test reads both and fails on a difference.
"""

import json
import os
import pathlib
import shutil
import sys
import unittest
import unittest.mock
from datetime import datetime, timezone

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
        self.assertEqual(expected["ingressRoutes"], 75)
        self.assertEqual(expected["publicHosts"], 9)
        self.assertEqual(expected["privilegedWorkloads"], 7)
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
        # One object publishing two hostnames is one object and two hostnames.
        # Counting host matches as "routes" would make one `kubectl get ingressroute`
        # object weigh two against a constant measured in objects.
        documents = [{"spec": {"routes": [
            {"match": "Host(`a.kub.local`) || Host(`b.kub.local`)", "middlewares": []}]}}]
        self.assertEqual(exporter.posture(documents, set()).routes, 1)
        counted = exporter.posture(documents, {"a.kub.local", "b.kub.local"})
        self.assertEqual(counted.public, 2)
        self.assertEqual(counted.ungated, 0)

    def test_a_host_two_objects_serve_is_one_host(self):
        # The plain-HTTP twin of a `websecure` router is a second object on the
        # same name. Both are real objects, and the name is still one name.
        exporter = self.load()
        documents = [
            {"spec": {"routes": [{"match": "Host(`ashudev.com`)", "middlewares": ["kube-system-lan-only@kubernetescrd"]}]}},
            {"spec": {"routes": [{"match": "Host(`ashudev.com`)", "middlewares": []}]}},
        ]
        counted = exporter.posture(documents, {"ashudev.com"})
        self.assertEqual(counted.routes, 2)
        self.assertEqual(counted.public, 1)

    def test_a_gated_route_does_not_gate_its_neighbour(self):
        # The gate is a router property. An object holding one gated route and one
        # open route leaves the open one answering, so the host is ungated.
        exporter = self.load()
        documents = [{"spec": {"routes": [
            {"match": "Host(`vikunja.kub.local`)", "middlewares": ["kube-system-lan-only@kubernetescrd"]},
            {"match": "Host(`vikunja.kub.local`)", "middlewares": []},
        ]}}]
        self.assertEqual(exporter.posture(documents, set()).ungated, 1)
        self.assertFalse(exporter.gated(documents[0]))

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
            # Mid-rollout: last week's ReplicaSet still has a Running pod. Two
            # hashes, one Deployment — the case the strip exists for, and the only
            # fixture that fails if the strip is removed.
            {"metadata": {"name": "mktxp-5f8c7d6b9-ghi56",
                          "ownerReferences": [{"kind": "ReplicaSet", "name": "mktxp-5f8c7d6b9"}]},
             "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
            # A DaemonSet, one pod per node: still one workload. Its only
            # privileged declaration is an init container, on both pods, so the
            # count is 1 here only if initContainers are read at all.
            {"metadata": {"name": "smartctl-nas",
                          "ownerReferences": [{"kind": "DaemonSet", "name": "smartctl"}]},
             "spec": {"initContainers": [{"name": "init", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
            {"metadata": {"name": "smartctl-nuc0",
                          "ownerReferences": [{"kind": "DaemonSet", "name": "smartctl"}]},
             "spec": {"initContainers": [{"name": "init", "securityContext": {"privileged": True}}]},
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

    def test_a_workload_is_identified_by_namespace_and_kind_too(self):
        # `foo` as a Deployment in one namespace and `foo` as a DaemonSet in
        # another are two workloads. Keying on the bare name counts one.
        exporter = self.load()
        pods = [
            {"metadata": {"name": "foo-abc12", "namespace": "media-stack",
                          "ownerReferences": [{"kind": "ReplicaSet", "name": "foo-7d9c8b6f4"}]},
             "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
            {"metadata": {"name": "foo-xyz98", "namespace": "kube-system",
                          "ownerReferences": [{"kind": "DaemonSet", "name": "foo"}]},
             "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]},
             "status": {"phase": "Running"}},
        ]
        self.assertEqual(exporter.privileged(pods), 2)


class PageTests(unittest.TestCase):
    """The page itself: what it publishes, and what it refuses to publish."""

    def load(self):
        import importlib.util
        path = ROOT / "cluster/helm/security/security-exporter/files/exporter.py"
        spec = importlib.util.spec_from_file_location("security_exporter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_four_posture_families_appear_with_their_help_lines(self):
        exporter = self.load()
        page = exporter.render(exporter.Snapshot(73, 0, 9, 9, 1795000000.0))
        for name in (
            "homelab_security_ingress_routes",
            "homelab_security_ungated_routes",
            "homelab_security_public_hosts",
            "homelab_security_privileged_workloads",
            "homelab_security_exporter_last_success_timestamp_seconds",
        ):
            self.assertIn(f"# HELP {name} ", page)
            self.assertIn(f"# TYPE {name} gauge", page)
        self.assertIn("homelab_security_privileged_workloads 9", page)

    def test_nothing_is_published_before_the_first_successful_poll(self):
        # A zero for `ungated_routes` is "nothing read", not "nothing found", and
        # the two must not look the same on a security page.
        exporter = self.load()
        page = exporter.render(exporter.Snapshot(0, 0, 0, 0, 0.0))
        self.assertIn("homelab_security_exporter_last_success_timestamp_seconds 0", page)
        self.assertNotIn("homelab_security_ungated_routes", page)
        self.assertNotIn("homelab_security_privileged_workloads", page)

    def test_a_failed_read_raises_instead_of_publishing_a_zero(self):
        exporter = self.load()
        with unittest.mock.patch.object(exporter, "fetch", side_effect=RuntimeError("403")):
            with self.assertRaises(RuntimeError):
                exporter.measure(set())


class AuthentikTests(unittest.TestCase):
    """The window arithmetic, which is where a paginated API lies.

    `/api/v3/events/events/` returns a page. A burst is counted only if the
    request asks for the whole window in one page and the client keeps asking
    until the window is exhausted; a test with a fixed fixture proves the
    counting, not the transport. The transport is measured: the server caps
    `page_size` at 100 whatever is asked, so a client that reads one answer
    silently undercounts a busy day. No test here touches the network: the
    opener is injected.
    """

    class Answer:
        """The sliver of `http.client.HTTPResponse` the fetcher reads."""

        def __init__(self, body, status=200):
            self.status = status
            self._body = json.dumps(body).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def load(self):
        import importlib.util
        path = ROOT / "cluster/helm/security/security-exporter/files/exporter.py"
        spec = importlib.util.spec_from_file_location("security_exporter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def now(self):
        return datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)

    def test_outcomes_are_bucketed_the_way_the_rule_reads_them(self):
        exporter = self.load()
        events = [
            {"action": "login_failed"},
            {"action": "login_failed"},
            {"action": "login"},
            {"action": "logout"},
        ]
        counts = exporter.count_outcomes(events)
        assert counts["failure"] == 2
        assert counts["success"] == 1
        assert counts["other"] == 1

    def test_the_request_asks_for_the_whole_window_and_the_whole_page(self):
        exporter = self.load()
        url = exporter.events_url(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc), 15)
        assert "page_size=1000" in url
        assert "created__gte=2026-11-25T11%3A45" in url or "created__gte=2026-11-25T11:45" in url
        assert "ordering=-created" in url

    def test_two_pages_are_followed_and_both_are_counted(self):
        # The test that fails if the loop is dropped: the server caps a page at
        # 100 events, so a 24-hour window is several pages and page one is not
        # the window. `pagination.next` is an integer page number here, not a URL.
        exporter = self.load()
        requested = []
        pages = [
            {"results": [{"action": "login_failed"}, {"action": "logout"}],
             "pagination": {"count": 4, "total_pages": 2, "next": 2}},
            {"results": [{"action": "login_failed"}, {"action": "login"}],
             "pagination": {"count": 4, "total_pages": 2, "next": None}},
        ]

        def opener(request, timeout=None):
            requested.append(request.full_url)
            return self.Answer(pages.pop(0))

        events = exporter.fetch_authentik("token", self.now(), 15, opener=opener)
        counts = exporter.count_outcomes(events)
        self.assertEqual(counts, {"success": 1, "failure": 2, "other": 1})
        self.assertEqual(len(requested), 2)
        self.assertIn("page=2", requested[1])

    def test_the_window_is_applied_here_because_the_api_ignores_the_bound(self):
        # Measured: `/events/events/` has no date filter, so `created__gte` is
        # ignored and the answer holds the whole retention period. Counting that
        # as "the last 15 minutes" would publish a week of events. The walk stops
        # at the first event older than the window, which is also why page two is
        # never requested here.
        exporter = self.load()
        requested = []

        def opener(request, timeout=None):
            requested.append(request.full_url)
            return self.Answer({"results": [
                {"action": "login_failed", "created": "2026-11-25T11:59:00Z"},
                {"action": "login", "created": "2026-11-25T11:50:00Z"},
                {"action": "logout", "created": "2026-11-25T11:40:00Z"},
            ], "pagination": {"next": 2}})

        events = exporter.fetch_authentik("token", self.now(), 15, opener=opener)
        self.assertEqual(exporter.count_outcomes(events),
                         {"success": 1, "failure": 1, "other": 0})
        self.assertEqual(len(requested), 1)

    def test_a_refusal_and_the_page_cap_raise_instead_of_a_partial_count(self):
        # A partial list would publish a number that is quietly too small, which
        # reads exactly like a quiet day. Both paths raise.
        exporter = self.load()

        def refusing(request, timeout=None):
            return self.Answer({}, status=403)

        with self.assertRaises(RuntimeError):
            exporter.fetch_authentik("token", self.now(), 15, opener=refusing)

        endless = {"results": [{"action": "login"}], "pagination": {"next": 2}}

        def always_more(request, timeout=None):
            return self.Answer(endless)

        with self.assertRaises(RuntimeError):
            exporter.fetch_authentik("token", self.now(), 15, opener=always_more)

    def test_the_signins_appear_only_after_a_successful_authentik_read(self):
        # No token, or no read yet: the families are absent, never zero. After a
        # good read all three outcomes are published, because a zero there is a
        # measurement.
        exporter = self.load()
        before = exporter.render(exporter.Snapshot(73, 0, 9, 9, 1795000000.0))
        self.assertNotIn("homelab_authentik_signins_15m", before)
        self.assertNotIn("homelab_authentik_signins_24h", before)

        after = exporter.render(exporter.Snapshot(
            73, 0, 9, 9, 1795000000.0,
            exporter.SignIns(
                {"success": 3, "failure": 0, "other": 1},
                {"success": 9, "failure": 2, "other": 4},
            ),
        ))
        for name in ("homelab_authentik_signins_15m", "homelab_authentik_signins_24h"):
            self.assertIn(f"# HELP {name} ", after)
            self.assertIn(f"# TYPE {name} gauge", after)
        for outcome in ("success", "failure", "other"):
            self.assertIn(f'outcome="{outcome}"', after)
        self.assertIn('homelab_authentik_signins_24h{outcome="failure"} 2', after)
        # An address is never a label: the per-address view carries a pseudonym,
        # and the plain address stays in authentik's event log and the gated store.
        self.assertNotIn("address=", after)
        self.assertNotIn("client_ip=", after)

    def test_a_timestamp_without_an_offset_is_read_as_utc(self):
        # A naive `created` compared with an aware cutoff raises `TypeError` inside
        # the page loop, and that exception is not caught there: one such event
        # would fail every poll for as long as it stays in the retention period.
        exporter = self.load()
        self.assertEqual(
            exporter._created({"created": "2026-11-25T11:59:00"}),
            datetime(2026, 11, 25, 11, 59, tzinfo=timezone.utc),
        )


class SourceTests(unittest.TestCase):
    """The per-source view: a pseudonym in the label, the plain address nowhere.

    The sign-in families count outcomes over the whole window, which is exactly
    what stops a rule from joining "twenty failures from one address" to "the
    sign-in that worked". Prometheus answers unauthenticated on the LAN, so the
    address itself cannot be the label; a salted hash is, and the salt is what
    keeps the label from being a rainbow table away from the address. These tests
    pin the properties the join depends on: one address is one stable key, a
    repeated failure is what puts a source on the page, and the page never
    carries the address.
    """

    def load(self):
        import importlib.util
        path = ROOT / "cluster/helm/security/security-exporter/files/exporter.py"
        spec = importlib.util.spec_from_file_location("security_exporter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def failed(self, address, times=3, action="login_failed"):
        return [{"action": action, "client_ip": address} for _ in range(times)]

    def test_one_address_is_one_stable_pseudonym(self):
        exporter = self.load()
        key = exporter.source_key("203.0.113.9", "salt")
        self.assertEqual(key, exporter.source_key("203.0.113.9", "salt"))
        self.assertNotEqual(key, exporter.source_key("203.0.113.9", "another-salt"))
        self.assertNotEqual(key, exporter.source_key("203.0.113.10", "salt"))
        self.assertNotIn("203.0.113.9", key)
        self.assertRegex(key, r"^[0-9a-f]{16}$")

    def test_a_repeated_failure_publishes_both_outcomes_for_its_source(self):
        # The join the rule makes: one key, two outcomes, both in the same window.
        exporter = self.load()
        events = self.failed("203.0.113.9") + [{"action": "login", "client_ip": "203.0.113.9"}]
        counts, omitted = exporter.count_sources(events, "salt")
        key = exporter.source_key("203.0.113.9", "salt")
        self.assertEqual(counts[(key, "failure")], 3)
        self.assertEqual(counts[(key, "success")], 1)
        self.assertEqual(omitted, 0)

    def test_one_or_two_failures_are_a_typo_and_stay_off_the_page(self):
        # Measured on 2026-11-14: three failures then a success, 30 seconds apart,
        # all from the operator's own browser. The floor is what keeps a retyped
        # password out of the family a rule reads.
        exporter = self.load()
        counts, omitted = exporter.count_sources(self.failed("203.0.113.9", 2), "salt")
        self.assertEqual(counts, {})
        self.assertEqual(omitted, 0)
        counts, _ = exporter.count_sources(self.failed("203.0.113.9", 3), "salt")
        self.assertEqual(len(counts), 2)

    def test_the_busiest_sources_are_kept_and_the_rest_are_counted(self):
        # A distribute-and-pray spray can name hundreds of addresses in one window.
        # The cap keeps the page small; the omitted count is what stops a capped
        # page from reading like a complete one.
        exporter = self.load()
        events = []
        for n in range(25):
            events += self.failed(f"198.51.100.{n}", 3 + n)
        counts, omitted = exporter.count_sources(events, "salt")
        self.assertEqual(omitted, 5)
        self.assertEqual(len({source for source, _ in counts}), 20)
        busiest = exporter.source_key("198.51.100.24", "salt")
        self.assertIn((busiest, "failure"), counts)
        quietest = exporter.source_key("198.51.100.0", "salt")
        self.assertNotIn((quietest, "failure"), counts)

    def test_an_event_without_an_address_is_skipped(self):
        exporter = self.load()
        events = self.failed("203.0.113.9") + [{"action": "login_failed", "client_ip": ""}]
        counts, _ = exporter.count_sources(events, "salt")
        self.assertEqual(len({source for source, _ in counts}), 1)

    def test_the_page_carries_the_pseudonyms_and_never_the_address(self):
        exporter = self.load()
        events = self.failed("203.0.113.9") + [{"action": "login", "client_ip": "203.0.113.9"}]
        counts, omitted = exporter.count_sources(events, "salt")
        page = exporter.render(exporter.Snapshot(
            73, 0, 9, 9, 1795000000.0,
            exporter.SignIns(
                {"success": 1, "failure": 3, "other": 0},
                {"success": 1, "failure": 3, "other": 0},
                exporter.Sources(counts, omitted),
            ),
        ))
        key = exporter.source_key("203.0.113.9", "salt")
        self.assertIn(
            f'homelab_authentik_signins_15m_by_source{{source="{key}",outcome="failure"}} 3', page
        )
        self.assertIn(
            f'homelab_authentik_signins_15m_by_source{{source="{key}",outcome="success"}} 1', page
        )
        self.assertIn("# HELP homelab_authentik_signins_by_source_omitted ", page)
        self.assertNotIn("203.0.113.9", page)

    def test_a_source_with_no_salt_publishes_nothing(self):
        # No salt means no join and no pseudonym; a silently weaker hash is worse
        # than an absent family, which is the same rule the token follows.
        exporter = self.load()
        page = exporter.render(exporter.Snapshot(
            73, 0, 9, 9, 1795000000.0,
            exporter.SignIns({"success": 1, "failure": 3, "other": 0}, None),
        ))
        self.assertIn('homelab_authentik_signins_15m{outcome="failure"} 3', page)
        self.assertNotIn("homelab_authentik_signins_15m_by_source", page)


class WiringTests(unittest.TestCase):
    """`measure()` with a token: what moves, what is carried, what is refused."""

    class Answer:
        def __init__(self, body, status=200):
            self.status = status
            self._body = json.dumps(body).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def load(self):
        import importlib.util
        path = ROOT / "cluster/helm/security/security-exporter/files/exporter.py"
        spec = importlib.util.spec_from_file_location("security_exporter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def now(self):
        return datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)

    def test_no_token_means_no_signin_family_and_a_successful_cluster_poll(self):
        exporter = self.load()
        with unittest.mock.patch.dict(os.environ, {"AUTHENTIK_TOKEN": ""}), \
             unittest.mock.patch.object(exporter, "fetch", return_value={"items": []}):
            snapshot = exporter.measure(set(), now=self.now())
        self.assertIsNone(snapshot.signins)
        self.assertEqual(snapshot.last_success, self.now().timestamp())
        self.assertNotIn("homelab_authentik_signins_15m", exporter.render(snapshot))

    def test_a_token_publishes_both_windows_and_moves_the_timestamp(self):
        exporter = self.load()

        def opener(request, timeout=None):
            return self.Answer({"results": [
                {"action": "login_failed", "created": "2026-11-25T11:59:00Z"}],
                "pagination": {"next": None}})

        with unittest.mock.patch.dict(os.environ, {"AUTHENTIK_TOKEN": "token"}), \
             unittest.mock.patch.object(exporter, "fetch", return_value={"items": []}):
            snapshot = exporter.measure(set(), now=self.now(), opener=opener)
        self.assertEqual(snapshot.last_success, self.now().timestamp())
        self.assertEqual(snapshot.signins.last_15m["failure"], 1)
        self.assertEqual(snapshot.signins.last_24h["failure"], 1)
        page = exporter.render(snapshot)
        self.assertIn('homelab_authentik_signins_15m{outcome="failure"} 1', page)
        self.assertIn('homelab_authentik_signins_24h{outcome="failure"} 1', page)

    def test_a_wide_window_that_cannot_be_walked_keeps_counting_the_narrow_one(self):
        # The narrow window is what the sign-in rule reads. A day too large to walk
        # must not take it down during the burst that made it large — the wide value
        # is carried and the freshness stamp stops, which is the signal for it.
        exporter = self.load()
        previous = exporter.Snapshot(
            1, 0, 0, 0, 100.0,
            exporter.SignIns({"success": 0, "failure": 0, "other": 0},
                             {"success": 7, "failure": 1, "other": 0}),
        )
        calls = {"n": 0}

        def opener(request, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return self.Answer({"results": [
                    {"action": "login_failed", "created": "2026-11-25T11:59:00Z"}],
                    "pagination": {"next": None}})
            return self.Answer({}, status=500)

        with unittest.mock.patch.dict(os.environ, {"AUTHENTIK_TOKEN": "token"}), \
             unittest.mock.patch.object(exporter, "fetch", return_value={"items": []}):
            snapshot = exporter.measure(set(), now=self.now(), previous=previous, opener=opener)
        self.assertEqual(snapshot.signins.last_15m["failure"], 1)
        self.assertEqual(snapshot.signins.last_24h["success"], 7)
        self.assertEqual(snapshot.last_success, 100.0)
        page = exporter.render(snapshot)
        self.assertIn('homelab_authentik_signins_15m{outcome="failure"} 1', page)
        self.assertIn('homelab_authentik_signins_24h{outcome="success"} 7', page)

    def test_a_first_poll_with_no_wide_window_publishes_nothing(self):
        # Nothing to carry: a partly read stack has no measurement to publish, so
        # the poll is a failure like any other and the page stays at freshness only.
        exporter = self.load()

        calls = {"n": 0}

        def first_narrow_then_fail(request, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return self.Answer({"results": [], "pagination": {"next": None}})
            return self.Answer({}, status=500)

        with unittest.mock.patch.dict(os.environ, {"AUTHENTIK_TOKEN": "token"}), \
             unittest.mock.patch.object(exporter, "fetch", return_value={"items": []}):
            with self.assertRaises(RuntimeError):
                exporter.measure(set(), now=self.now(), previous=None, opener=first_narrow_then_fail)

    def test_a_salt_publishes_the_per_source_family_and_its_absence_does_not(self):
        # The salt is a Secret key read once per poll, like the token. Without it
        # the two aggregate windows still publish: a missing salt must cost the
        # per-source view, not the sign-in count the existing rule reads.
        exporter = self.load()

        def opener(request, timeout=None):
            return self.Answer({"results": [
                {"action": "login_failed", "created": "2026-11-25T11:59:00Z",
                 "client_ip": "203.0.113.9"}],
                "pagination": {"next": None}})

        environment = {"AUTHENTIK_TOKEN": "token", "AUTHENTIK_ADDRESS_SALT": "salt"}
        with unittest.mock.patch.dict(os.environ, environment), \
             unittest.mock.patch.object(exporter, "fetch", return_value={"items": []}):
            salted = exporter.measure(set(), now=self.now(), opener=opener)
        self.assertIsNotNone(salted.signins.sources)
        self.assertIn("homelab_authentik_signins_15m_by_source", exporter.render(salted))

        environment["AUTHENTIK_ADDRESS_SALT"] = ""
        with unittest.mock.patch.dict(os.environ, environment), \
             unittest.mock.patch.object(exporter, "fetch", return_value={"items": []}):
            plain = exporter.measure(set(), now=self.now(), opener=opener)
        self.assertIsNone(plain.signins.sources)
        page = exporter.render(plain)
        self.assertNotIn("homelab_authentik_signins_15m_by_source", page)
        self.assertIn('homelab_authentik_signins_15m{outcome="failure"} 1', page)

    @unittest.skipUnless(shutil.which("helm"), "helm is not installed")
    def test_the_chart_keeps_the_token_optional(self):
        # Losing `optional: true` turns the no-token state from "healthy,
        # posture-only" into `CreateContainerConfigError` — a failure nobody notices
        # until the Secret is deleted.
        import subprocess
        chart = ROOT / "cluster/helm/security/security-exporter"
        rendered = subprocess.run(
            ["helm", "template", "test", str(chart), "-f", str(chart / "values.yaml")],
            capture_output=True, text=True, check=True).stdout
        documents = [d for d in yaml.safe_load_all(rendered) if d and d.get("kind") == "Deployment"]
        containers = documents[0]["spec"]["template"]["spec"]["containers"]
        token = next(e for e in containers[0]["env"] if e["name"] == "AUTHENTIK_TOKEN")
        self.assertIs(token["valueFrom"]["secretKeyRef"]["optional"], True)

    @unittest.skipUnless(shutil.which("helm"), "helm is not installed")
    def test_the_chart_reads_the_salt_from_the_same_secret_and_keeps_it_optional(self):
        # The salt is a pseudonym secret, so it is a Secret key and never a chart
        # value: a salt in git is the same as no salt. It is optional for the same
        # reason the token is — a missing key costs the per-source family, not the
        # pod.
        import subprocess
        chart = ROOT / "cluster/helm/security/security-exporter"
        rendered = subprocess.run(
            ["helm", "template", "test", str(chart), "-f", str(chart / "values.yaml")],
            capture_output=True, text=True, check=True).stdout
        values = yaml.safe_load((chart / "values.yaml").read_text())["securityExporter"]
        documents = [d for d in yaml.safe_load_all(rendered) if d and d.get("kind") == "Deployment"]
        containers = documents[0]["spec"]["template"]["spec"]["containers"]
        salt = next(e for e in containers[0]["env"] if e["name"] == "AUTHENTIK_ADDRESS_SALT")
        reference = salt["valueFrom"]["secretKeyRef"]
        self.assertEqual(reference["name"], values["authentik"]["secretName"])
        self.assertEqual(reference["key"], values["authentik"]["saltKey"])
        self.assertIs(reference["optional"], True)
        self.assertNotIn("value", salt)
        self.assertNotIn("salt", {k.lower() for k in values if k != "authentik"})
