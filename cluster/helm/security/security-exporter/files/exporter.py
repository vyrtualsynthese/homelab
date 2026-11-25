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
so `SecurityStackBlind` reads a number that only moves on success. The timestamp
covers every configured source: the cluster read always, and the authentik sign-in
read when a token is present, so an authentik outage stops it rather than leaving
stale counts looking live. With no token the exporter is posture-only and
publishes no sign-in family at all — a zero there would read as a quiet day.

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
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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
# From values.yaml, so the endpoint is a chart decision and not a constant in code.
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "https://authentik.kub.home.ashudev.com")
# The server caps `page_size` at 100 however large a value is asked for, so a
# busy day is several pages. Twenty is far past the measured window and is a cap
# rather than a promise: reaching it raises, because a partial list would publish
# a sign-in count that is silently too small.
AUTHENTIK_PAGE_CAP = 20
# The 24-hour window is a report, not a trigger, and a day can hold far more events
# than a quarter of an hour: 200 pages is 20,000 events, which a slow spray against
# the sign-in page can still exceed. Past it the narrow window keeps counting and
# the wide one keeps its last value, see `measure()`.
AUTHENTIK_WIDE_PAGE_CAP = 200


class Posture(NamedTuple):
    routes: int
    ungated: int
    public: int


class SignIns(NamedTuple):
    """Both windows, bucketed. Present only after a successful authentik read.

    `last_24h` is optional on purpose: the narrow window is the one the sign-in
    rule reads, so a wide window too large to walk carries the previous value
    forward (or nothing) instead of taking the narrow count down with it.
    """

    last_15m: dict[str, int]
    last_24h: dict[str, int] | None = None


class Snapshot(NamedTuple):
    routes: int
    ungated: int
    public: int
    privileged: int
    last_success: float
    # None means "not read": no token configured, or no successful authentik read
    # yet. `render()` publishes no sign-in family in that case, never a zero.
    signins: SignIns | None = None


def _middleware_names(route):
    """Every middleware name one router references, normalised.

    The CRD carries `{name, namespace}` objects; a rendered chart can also carry
    the legacy `namespace-name@kubernetescrd` string. Both name the same object,
    so both are reduced to the bare name. A prefix strip, not a replace: a
    middleware whose own name contains `kube-system-` must not be mangled.
    """
    names = []
    for middleware in route.get("middlewares") or []:
        name = middleware if isinstance(middleware, str) else middleware.get("name", "")
        names.append(str(name).split("@")[0].removeprefix("kube-system-"))
    return names


def hosts_of(route) -> list[str]:
    """Every backticked `Host()` one router matches. Only that shape is counted.

    A `HostRegexp(...)` or `HostSNI(...)` match, or a double-quoted `Host("...")`,
    contributes nothing and its route leaves the count silently — no chart in this
    repository uses those forms, and a router shape this cannot read is a change to
    make deliberately rather than to guess at.
    """
    out = []
    match = route.get("match") or ""
    for chunk in match.split("Host(`")[1:]:
        out.append(chunk.split("`")[0])
    return out


def gated(document) -> bool:
    """True when every router of an IngressRoute carries a middleware named `lan-only`.

    The gate is per router, so this is the document-wide reading: a document is
    only fully gated when none of its routes is open. `posture()` works per route
    for that reason; this helper exists for the one-place check and for tests.
    """
    routes = (document.get("spec") or {}).get("routes") or []
    return bool(routes) and all(LAN_GATE in _middleware_names(route) for route in routes)


def posture(documents, public: set[str]) -> Posture:
    """Count the live objects, the exposed hostnames, and the allowlisted ones.

    Three grains, and each number means exactly one thing:

    * `routes` is `IngressRoute` objects that publish at least one host, so it is
      the same unit `kubectl get ingressroute | wc -l` counts and the constant in
      values.yaml is written in. Counting host matches instead would inflate one
      object that routes `/` and `/api` on one name, and one name that two objects
      serve (a `websecure` router and its `web` redirect twin).
    * `published` is the allowlisted **hostnames** that are live: the intersection,
      so a name served by two objects counts once.
    * `ungated` is the **hostnames** nobody declared that have at least one router
      with no `lan-only` middleware. The gate is per router, not per object: an
      object can hold a gated route and an ungated one, and the ungated one answers
      whoever sends its `Host` header. A host is therefore ungated when *any* of
      its routers is, which is the safe reading.

    The two exposed families never count the same name: an allowlisted host is
    `published` whether or not it is gated, because it is meant to be reachable. A
    name is not classified by what it resolves to either — the router chooses by
    `Host` header, so an ungated `.kub.local` route answers whoever sends that
    header, and calling it "internal" because DNS says so is the assumption
    `hack/check_exposure.py` exists to refuse.
    """
    objects = 0
    live: set[str] = set()
    ungated_hosts: set[str] = set()
    for document in documents:
        seen: list[str] = []
        for route in (document.get("spec") or {}).get("routes") or []:
            route_hosts = hosts_of(route)
            seen.extend(route_hosts)
            if LAN_GATE not in _middleware_names(route):
                ungated_hosts.update(route_hosts)
        if seen:
            objects += 1
        live.update(seen)
    return Posture(objects, len(ungated_hosts - public), len(public & live))


def _controller(pod):
    """The workload a pod belongs to, or None when it belongs to nobody.

    The pod's first owner is its controller. A Deployment reaches its pods
    through a ReplicaSet whose name carries the pod-template hash, and that hash
    changes on every rollout, so it is stripped: one Deployment stays one
    workload across a redeploy. A DaemonSet, StatefulSet or Job names itself
    already and is used as it is.

    The identity is the triple `(namespace, kind, name)`: a Deployment `foo` in
    one namespace and a DaemonSet `foo` in another are two workloads, and keying
    on the bare name would quietly count them as one.
    """
    metadata = pod.get("metadata") or {}
    owners = metadata.get("ownerReferences") or []
    if not owners:
        return None
    owner = owners[0]
    name = str(owner.get("name") or "")
    kind = str(owner.get("kind") or "")
    if not name or not kind:
        return None
    if kind == "ReplicaSet":
        name = POD_TEMPLATE_HASH.sub("", name)
    return (metadata.get("namespace") or "", kind, name)


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


def count_outcomes(events):
    """Bucket authentik's event actions into the three outcomes the rules read.

    `login_failed` is a failure and `login` a success; everything else is
    `other`, which keeps `model_updated` noise from being counted as a sign-in.
    `login_success` is not an action the API emits today — it is here for
    forward-compatibility, so a rename upstream does not read as "no sign-ins".

    One deliberate omission: no event carries an address into a label. The counts
    are aggregate; the per-address view is a Loki query against the gate's log.
    """
    counts = {"success": 0, "failure": 0, "other": 0}
    for event in events:
        action = event.get("action") or ""
        if action == "login_failed":
            counts["failure"] += 1
        elif action in ("login", "login_success"):
            counts["success"] += 1
        else:
            counts["other"] += 1
    return counts


def events_url(now, minutes, page=None):
    """One page of the events window that ends at `now`.

    `page_size=1000` is in the URL because it is asked for, not because it is
    received: the server caps a page at 100 whatever the request says, so the
    caller must walk pages rather than trust one answer. `page` is added only when
    following `pagination.next`, which is an integer page number, not a URL.
    """
    since = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    url = (f"{AUTHENTIK_URL}/api/v3/events/events/"
           f"?page_size=1000&ordering=-created&created__gte={urllib.parse.quote(since)}")
    if page is not None:
        url += f"&page={page}"
    return url


def _created(event):
    """The event's own timestamp, or None when the payload has none.

    authentik writes RFC 3339 with a `Z`; `fromisoformat` reads it on 3.11+.
    """
    stamp = event.get("created")
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    # authentik always writes an offset. A serialiser that ever writes a naive
    # one would otherwise raise `TypeError` on the comparison below, and that
    # exception escapes the per-event loop: one bad event would freeze every
    # family for as long as the event stays in the retention period.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fetch_authentik(token, now, minutes, cap=AUTHENTIK_PAGE_CAP, opener=urllib.request.urlopen):
    """Every event in the window, walking pages until the window is exhausted.

    The server has no date filter: its OpenAPI schema offers pagination,
    `ordering` and `search` on `/events/events/` and nothing else, and a measured
    `created__gte` in the last 24 hours came back with events eight days old. The
    bound stays in the URL because it is the shape of the request, but the window
    is applied here, on each event's own `created`, and the walk stops at the first
    event that is older than the window. Without that the "15m" family would
    publish every event in the retention period.

    A non-200 raises, and so does a window that still has pages after `cap`: a
    partial list would undercount a burst while looking exactly like a quiet day,
    which is the one thing a sign-in count must not do. The cap is a parameter
    because the two windows are sized apart: a busy day must not blind the
    fifteen-minute view that `AuthentikSignInFailures` reads. The opener is
    injectable so the tests prove the transport without a network.
    """
    cutoff = now - timedelta(minutes=minutes)
    events = []
    page = None
    for _ in range(cap):
        request = urllib.request.Request(
            events_url(now, minutes, page),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with opener(request, timeout=REQUEST_TIMEOUT) as answer:
            if answer.status != 200:
                raise RuntimeError(f"authentik events: HTTP {answer.status}")
            body = json.loads(answer.read())
        for event in body.get("results") or []:
            created = _created(event)
            # Newest first, so the first old event ends the window. An event with
            # no readable timestamp is kept: a missing count is invisible, and
            # this is the only place an event could be lost.
            if created is not None and created < cutoff:
                return events
            events.append(event)
        page = (body.get("pagination") or {}).get("next")
        if not page:
            return events
    raise RuntimeError(
        f"authentik events: still more pages after {cap}; refusing a partial count"
    )


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


def measure(public: set[str], now: datetime | None = None, previous: Snapshot | None = None,
            opener=urllib.request.urlopen) -> Snapshot:
    """One full poll. Raising here is how a failure keeps the old timestamp.

    The cluster is always read; authentik only when `AUTHENTIK_TOKEN` is set. A
    token that is present but cannot be read raises, so both the previous counts
    and the success timestamp stay where they were — a failed read must never
    look like a quiet day.

    The two authentik windows are not symmetrical. The narrow one is what
    `AuthentikSignInFailures` reads, so it is read first and a failure there is
    fatal. The wide one is a report, and a day too large to walk must not take the
    narrow count down during the very burst that made it large: on failure it keeps
    its last value (or none) and the freshness stamp stops, which says "the
    24-hour view is stale" without pretending the 15-minute one is. On a first poll
    with nothing to carry, the wide failure is fatal like any other, because a
    partly-read stack has no measurement to publish.
    """
    now = now or datetime.now(timezone.utc)
    documents = fetch(INGRESS_ROUTE_URL).get("items") or []
    counted = posture(documents, public)
    pods = fetch(PODS_URL).get("items") or []
    token = os.environ.get("AUTHENTIK_TOKEN", "").strip()
    signins = None
    stamp = now.timestamp()
    if token:
        narrow = count_outcomes(fetch_authentik(token, now, 15, opener=opener))
        try:
            wide = count_outcomes(
                fetch_authentik(token, now, 24 * 60, cap=AUTHENTIK_WIDE_PAGE_CAP, opener=opener)
            )
        except Exception as error:  # noqa: BLE001 — reported, and the narrow count stands
            if previous is None or previous.signins is None:
                raise
            print(f"authentik 24h window not read, keeping its last value: {error}",
                  file=sys.stderr, flush=True)
            wide = previous.signins.last_24h
            stamp = previous.last_success
        signins = SignIns(narrow, wide)
    return Snapshot(
        counted.routes, counted.ungated, counted.public, privileged(pods), stamp, signins
    )


FRESHNESS = [
    "# HELP homelab_security_exporter_last_success_timestamp_seconds Unix time of the last successful poll.",
    "# TYPE homelab_security_exporter_last_success_timestamp_seconds gauge",
]


def render(state: Snapshot) -> str:
    """The page, seven families at most, and no labels: Prometheus is open on the LAN.

    Before the first successful poll only the freshness family is published. A
    zero for `ungated_routes` or `privileged_workloads` is not "nothing found", it
    is "nothing read", and a security count must never carry that ambiguity: an
    operator who sees the numbers absent reaches for the freshness family, while
    one who sees a zero believes the cluster. Publishing only the timestamp also
    keeps `up == 1` from meaning anything it does not.

    The sign-in families follow the same rule one step further: they appear only
    when `signins` is set, which is only after a successful authentik read. With
    no token there is no read, so there is no family — never three zeros that
    would read as a quiet day. Once the read succeeds, a zero is a measurement and
    all three outcomes are published.
    """
    if state.last_success <= 0:
        return "\n".join([*FRESHNESS, "homelab_security_exporter_last_success_timestamp_seconds 0", ""])
    lines = [
        "# HELP homelab_security_ingress_routes Live IngressRoute objects that publish a host.",
        "# TYPE homelab_security_ingress_routes gauge",
        f"homelab_security_ingress_routes {state.routes}",
        "# HELP homelab_security_ungated_routes Live hosts with no LAN gate that are not on the allowlist.",
        "# TYPE homelab_security_ungated_routes gauge",
        f"homelab_security_ungated_routes {state.ungated}",
        "# HELP homelab_security_public_hosts Declared public hosts that are live.",
        "# TYPE homelab_security_public_hosts gauge",
        f"homelab_security_public_hosts {state.public}",
        "# HELP homelab_security_privileged_workloads Distinct Running controllers with a privileged container.",
        "# TYPE homelab_security_privileged_workloads gauge",
        f"homelab_security_privileged_workloads {state.privileged}",
    ]
    if state.signins is not None:
        for name, window, counts in (
            ("homelab_authentik_signins_15m", "the last 15 minutes", state.signins.last_15m),
            ("homelab_authentik_signins_24h", "the last 24 hours", state.signins.last_24h),
        ):
            # A window whose fetch was never completed is absent, never zero.
            if counts is None:
                continue
            lines += [
                f"# HELP {name} Sign-in attempts in {window}, by outcome.",
                f"# TYPE {name} gauge",
            ]
            for outcome in ("failure", "success", "other"):
                lines.append(f'{name}{{outcome="{outcome}"}} {counts[outcome]}')
    lines += [
        *FRESHNESS,
        f"homelab_security_exporter_last_success_timestamp_seconds {int(state.last_success)}",
        "",
    ]
    return "\n".join(lines)


# Zero is the honest first answer: nothing has been read yet, and a timestamp of
# 0 is what the silence rule is written to catch.
STATE = Snapshot(0, 0, 0, 0, 0.0)
LOCK = threading.Lock()


def poll_forever(public: set[str], interval: int) -> None:
    """Refresh the snapshot on a timer, and never touch it on a failure."""
    global STATE
    while True:
        with LOCK:
            previous = STATE
        try:
            fresh = measure(public, previous=previous)
        except Exception as error:  # noqa: BLE001 — a failed poll is logged, not fatal
            print(f"poll failed, keeping the last snapshot: {error}", file=sys.stderr, flush=True)
        else:
            with LOCK:
                STATE = fresh
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — the name is the HTTP method
        path = self.path.split("?")[0]
        if path == "/healthz":
            # Ready only once something has actually been read: a pod that cannot
            # reach the API must not be a Ready endpoint serving stale zeros, and
            # an unready pod is absent from the service rather than plausible in it.
            with LOCK:
                ready = STATE.last_success > 0
            self.send_response(200 if ready else 503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path != "/metrics":
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
    # One line, once, and not once a poll: the Application syncs before the
    # operator creates the token Secret, and that pod is healthy and posture-only
    # rather than crashing on a token that is not there yet.
    if not os.environ.get("AUTHENTIK_TOKEN", "").strip():
        print("AUTHENTIK_TOKEN is empty: sign-in counting is off until its Secret exists; "
              "posture only", file=sys.stderr, flush=True)
    threading.Thread(target=poll_forever, args=(PUBLIC_HOSTS, INTERVAL), daemon=True).start()
    # Threaded so a slow scrape cannot hold the next one; the state is a single
    # immutable tuple behind a lock, so there is nothing else to coordinate.
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
