"""Tests for the Argo CD chart's scrape shapes.

`charts/` is gitignored (pattern D), so the chart is copied to a temporary directory and
its dependency built there before rendering — the same steps ArgoCD runs at sync time.
"""

import pathlib
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "cluster/helm/argocd"

METRICS_SERVICES = {
    "argocd-application-controller-metrics": 8082,
    "argocd-repo-server-metrics": 8084,
    "argocd-server-metrics": 8083,
}

# The chart's own ingress policy for each pod the extra scrape policy selects. If a
# dependency upgrade drops or renames one of these, `argocd-metrics-scrape` becomes the
# only policy on the pod and default-denies its Redis and gRPC traffic while the scrape
# stays green — so the pairing is asserted, not assumed.
CHART_POLICIES = {
    "argocd-application-controller": "argocd-application-controller",
    "argocd-repo-server": "argocd-repo-server",
}

_RENDER = None


def render():
    global _RENDER
    if _RENDER is None:
        _RENDER = _render()
    return _RENDER


def _render():
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "argocd"
        shutil.copytree(CHART, work)
        subprocess.run(
            ["helm", "dependency", "build", str(work)],
            check=True,
            capture_output=True,
            text=True,
        )
        out = subprocess.run(
            [
                "helm", "template", "argocd", str(work),
                "-n", "argocd", "-f", str(work / "values.yaml"),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def by_kind(kind):
    return [doc for doc in render() if doc["kind"] == kind]


class ScrapeShapeTests(unittest.TestCase):
    def test_every_metrics_service_carries_the_scrape_annotation(self):
        services = {d["metadata"]["name"]: d for d in by_kind("Service")}
        for name, port in METRICS_SERVICES.items():
            with self.subTest(service=name):
                annotations = services[name]["metadata"].get("annotations", {})
                self.assertEqual(annotations.get("prometheus.io/scrape"), "true")
                self.assertEqual(annotations.get("prometheus.io/port"), str(port))

    def test_only_the_metrics_services_carry_the_scrape_annotation(self):
        annotated = {
            d["metadata"]["name"]
            for d in by_kind("Service")
            if (d["metadata"].get("annotations") or {}).get("prometheus.io/scrape") == "true"
        }
        self.assertEqual(annotated, set(METRICS_SERVICES))

    def test_the_scrape_policy_allows_the_two_metrics_ports_by_number(self):
        policy = next(
            d for d in by_kind("NetworkPolicy")
            if d["metadata"]["name"] == "argocd-metrics-scrape"
        )
        selector = policy["spec"]["podSelector"]["matchExpressions"][0]
        self.assertEqual(selector["key"], "app.kubernetes.io/name")
        self.assertEqual(
            sorted(selector["values"]),
            ["argocd-application-controller", "argocd-repo-server"],
        )
        rule = policy["spec"]["ingress"][0]
        self.assertEqual(rule["from"], [{"ipBlock": {"cidr": "10.42.0.0/16"}}])
        self.assertEqual(sorted(p["port"] for p in rule["ports"]), [8082, 8084])

    def test_the_chart_still_isolates_the_scraped_pods(self):
        docs = render()
        policies = {d["metadata"]["name"]: d for d in docs if d["kind"] == "NetworkPolicy"}
        workloads = {
            d["metadata"]["name"]: d["spec"]["template"]["metadata"]["labels"]
            for d in docs if d["kind"] in ("Deployment", "StatefulSet")
        }
        for policy_name, pod_name in CHART_POLICIES.items():
            with self.subTest(policy=policy_name):
                self.assertIn(policy_name, policies)
                self.assertTrue(policies[policy_name]["spec"].get("ingress"))
                labels = workloads[pod_name]
                self.assertEqual(labels["app.kubernetes.io/name"], pod_name)
                selector = policies[policy_name]["spec"]["podSelector"]["matchLabels"]
                self.assertEqual(
                    selector["app.kubernetes.io/name"],
                    labels["app.kubernetes.io/name"],
                )

    def test_the_controller_is_told_which_condition_to_publish(self):
        cm = next(
            d for d in by_kind("ConfigMap")
            if d["metadata"]["name"] == "argocd-cmd-params-cm"
        )
        self.assertEqual(
            cm["data"]["controller.metrics.application.conditions"],
            "ComparisonError",
        )


if __name__ == "__main__":
    unittest.main()
