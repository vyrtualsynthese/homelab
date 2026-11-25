#!/usr/bin/env python3
"""Posture and identity in one /metrics page.

Two rules shaped it. Nothing here may put a visitor's address in a Prometheus
label: Prometheus answers unauthenticated on the LAN, so the per-address views
live in the gated log store instead. And every number is either read from the
cluster under a read-only ServiceAccount or compared against a value this
repository already states — no second policy, and the one copied list has a test
that fails the moment it disagrees with `hack/check_exposure.py`.

The poll loop and the HTTP server are separate threads on purpose: a poll that
hangs on the API server must not stop `/metrics` answering, and a failed poll
leaves both the last snapshot and the success timestamp exactly where they were,
so `SecurityStackBlind` reads a number that only moves on success.

Deployed from the chart's ConfigMap; the account reads two lists and nothing
else, see `templates/rbac.yaml`.
"""

import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

PUBLIC_HOSTS = {h.strip() for h in os.environ.get("PUBLIC_HOSTS", "").split(",") if h.strip()}
INGRESS_ROUTE_URL = "/apis/traefik.io/v1alpha1/ingressroutes"
PODS_URL = "/api/v1/pods"
LAN_GATE = "lan-only"
API = "https://kubernetes.default.svc"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
PORT = int(os.environ.get("PORT", "9105"))
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "60"))
REQUEST_TIMEOUT = 10
POD_TEMPLATE_HASH = re.compile(r"-[a-z0-9]{5,10}$")


class Posture(NamedTuple):
    routes: int
    ungated: int
    public: int


class Snapshot(NamedTuple):
    routes: int
    ungated: int
    public: int
    privileged: int
    last_success: float


def _middleware_names(document):
    """Every middleware name an IngressRoute's routes reference, normalised.

    The CRD carries `{name, namespace}` objects; a rendered chart can also carry
    the legacy `namespace-name@kubernetescrd` string. Both name the same object,
    so both are reduced to the bare name.
    """
    names = []
    for route in (document.get("spec") or {}).get("routes") or []:
        for middleware in route.get("middlewares") or []:
            name = middleware if isinstance(middleware, str) else middleware.get("name", "")
            names.append(name.split("@")[0].replace("kube-system-", ""))
    return names


def gated(document) -> bool:
    """True when any route in an IngressRoute carries a middleware named `lan-only`."""
    return LAN_GATE in _middleware_names(document)


def hosts_of(document) -> list[str]:
    """Every `Host()` a document matches. One IngressRoute can carry several."""
    out = []
    for route in (document.get("spec") or {}).get("routes") or []:
        match = route.get("match") or ""
        for chunk in match.split("Host(`")[1:]:
            out.append(chunk.split("`")[0])
    return out


def posture(documents, public: set[str]) -> Posture:
    """Count routes, the ungated ones, and the ones that answer the internet.

    A route is ungated when its IngressRoute carries no `lan-only` and its host is
    not on the public list. The two families answer two different questions and
    are never both incremented by one route: `published` counts the hosts on the
    allowlist that are live, so it moves when a declared public name loses or
    gains its route, and `ungated` counts the hosts nobody declared that carry no
    gate, so it moves when a route is exposed. A name is not classified by what it
    resolves to: the router chooses by `Host` header, so an ungated `.kub.local`
    route answers whoever sends that header, and calling it "internal" because DNS
    says so is the assumption `hack/check_exposure.py` exists to refuse.
    """
    total = published = ungated = 0
    for document in documents:
        gate = gated(document)
        for host in hosts_of(document):
            total += 1
            if host in public:
                published += 1
            elif not gate:
                ungated += 1
    return Posture(total, ungated, published)


def _controller(pod):
    """The workload a pod belongs to, or None when it belongs to nobody.

    The pod's first owner is its controller. A Deployment reaches its pods
    through a ReplicaSet whose name carries the pod-template hash, and that hash
    changes on every rollout, so it is stripped: one Deployment stays one
    workload across a redeploy. A DaemonSet, StatefulSet or Job names itself
    already and is used as it is.
    """
    owners = (pod.get("metadata") or {}).get("ownerReferences") or []
    if not owners:
        return None
    owner = owners[0]
    name = str(owner.get("name") or "")
    if not name:
        return None
    if owner.get("kind") == "ReplicaSet":
        name = POD_TEMPLATE_HASH.sub("", name)
    return name


def privileged(pods) -> int:
    """Distinct Running workloads with a privileged container, in either list.

    Not a pod count: a DaemonSet runs one pod per node, a Deployment one per
    replica, and a rollout replaces every pod of it. Counting pods would make
    the drift alert move on its own, with no decision behind it. Pods with no
    owner are left out (k3s's own jobs), and so is any pod that is not
    `Running`, so a finished job never enters the number.
    """
    controllers = set()
    for pod in pods:
        if (pod.get("status") or {}).get("phase") != "Running":
            continue
        controller = _controller(pod)
        if controller is None:
            continue
        spec = pod.get("spec") or {}
        containers = (spec.get("containers") or []) + (spec.get("initContainers") or [])
        for container in containers:
            if (container.get("securityContext") or {}).get("privileged"):
                controllers.add(controller)
                break
    return len(controllers)


def fetch(path: str):
    """One API read under the pod's own read-only identity.

    A non-200 raises: an empty list on a refusal would publish a zero and read
    as a clean cluster, which is the one mistake this exporter must not make.
    """
    with open(f"{SA}/token", encoding="utf-8") as handle:
        token = handle.read().strip()
    request = urllib.request.Request(
        API + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    context = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT, context=context) as answer:
        if answer.status != 200:
            raise RuntimeError(f"{path}: HTTP {answer.status}")
        return json.loads(answer.read())


def measure(public: set[str], now: datetime | None = None) -> Snapshot:
    """One full poll. Raising here is how a failure keeps the old timestamp."""
    documents = fetch(INGRESS_ROUTE_URL).get("items") or []
    counted = posture(documents, public)
    pods = fetch(PODS_URL).get("items") or []
    stamp = (now or datetime.now(timezone.utc)).timestamp()
    return Snapshot(counted.routes, counted.ungated, counted.public, privileged(pods), stamp)


def render(state: Snapshot) -> str:
    """The page, five families and no labels: Prometheus is open on the LAN."""
    return "\n".join([
        "# HELP homelab_security_ingress_routes Live IngressRoute objects.",
        "# TYPE homelab_security_ingress_routes gauge",
        f"homelab_security_ingress_routes {state.routes}",
        "# HELP homelab_security_ungated_routes Live routes with no LAN gate and a host that is not public.",
        "# TYPE homelab_security_ungated_routes gauge",
        f"homelab_security_ungated_routes {state.ungated}",
        "# HELP homelab_security_public_hosts Declared public hosts that are live.",
        "# TYPE homelab_security_public_hosts gauge",
        f"homelab_security_public_hosts {state.public}",
        "# HELP homelab_security_privileged_workloads Running containers that declare privileged: true.",
        "# TYPE homelab_security_privileged_workloads gauge",
        f"homelab_security_privileged_workloads {state.privileged}",
        "# HELP homelab_security_exporter_last_success_timestamp_seconds Unix time of the last successful poll.",
        "# TYPE homelab_security_exporter_last_success_timestamp_seconds gauge",
        f"homelab_security_exporter_last_success_timestamp_seconds {int(state.last_success)}",
        "",
    ])


# Zero is the honest first answer: nothing has been read yet, and a timestamp of
# 0 is what the silence rule is written to catch.
STATE = Snapshot(0, 0, 0, 0, 0.0)
LOCK = threading.Lock()


def poll_forever(public: set[str], interval: int) -> None:
    """Refresh the snapshot on a timer, and never touch it on a failure."""
    global STATE
    while True:
        try:
            fresh = measure(public)
        except Exception as error:  # noqa: BLE001 — a failed poll is logged, not fatal
            print(f"poll failed, keeping the last snapshot: {error}", file=sys.stderr, flush=True)
        else:
            with LOCK:
                STATE = fresh
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — the name is the HTTP method
        if self.path.split("?")[0] != "/metrics":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        with LOCK:
            body = render(STATE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            # A scraper that gave up early is not worth a traceback in the log.
            pass

    def log_message(self, fmt, *args):  # noqa: A003
        return  # one line per scrape is noise nobody reads


def main() -> int:
    threading.Thread(target=poll_forever, args=(PUBLIC_HOSTS, INTERVAL), daemon=True).start()
    # Threaded so a slow scrape cannot hold the next one; the state is a single
    # immutable tuple behind a lock, so there is nothing else to coordinate.
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
