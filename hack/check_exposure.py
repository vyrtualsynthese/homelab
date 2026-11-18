"""Check that every non-public route is gated to the LAN.

The internet reaches every Traefik route by hostname: the router forwards 80 and
443 to the cluster, and `*.kub.home.ashudev.com` resolves to the public address.
The only thing that keeps an internal app internal is the `lan-only` middleware,
so a route that forgets it is a door left open.

Input is a list of parsed YAML documents (from `helm template`, or read from a
file). Output is a list of human-readable violations; empty means clean.
"""

import re

PUBLIC_HOSTS = frozenset(
    {
        "plex.kub.home.ashudev.com",
        "seerr.kub.home.ashudev.com",
        "ashuvidz.com",
        "forum.ashuvidz.com",
    }
)

GATE = "kube-system-lan-only@kubernetescrd"
GATE_NAME = "lan-only"
GATE_NAMESPACE = "kube-system"
MIDDLEWARE_ANNOTATION = "traefik.ingress.kubernetes.io/router.middlewares"

HOST_RE = re.compile(r"Host\(`([^`]+)`\)")


def _has_key(obj, key):
    if isinstance(obj, dict):
        return key in obj or any(_has_key(value, key) for value in obj.values())
    if isinstance(obj, list):
        return any(_has_key(value, key) for value in obj)
    return False


def _where(doc):
    metadata = doc.get("metadata") or {}
    kind = doc.get("kind", "object")
    namespace = metadata.get("namespace", "-")
    name = metadata.get("name", "-")
    return f"{kind} {namespace}/{name}"


def _route_has_gate(route):
    for middleware in route.get("middlewares") or []:
        if isinstance(middleware, str):
            name, namespace = middleware, None
        elif isinstance(middleware, dict):
            name, namespace = middleware.get("name"), middleware.get("namespace")
        else:
            continue
        if name == GATE:
            return True
        if name == GATE_NAME and namespace == GATE_NAMESPACE:
            return True
    return False


def _check_route(where, match, gated, violations):
    hosts = HOST_RE.findall(match or "")
    if not hosts:
        if not gated:
            violations.append(f"{where}: (no Host matcher): missing {GATE}")
        return
    # Evaluate every host on its own: a route can name a public host and an internal
    # one in the same matcher, and the internal one must still be gated.
    for host in hosts:
        if host in PUBLIC_HOSTS:
            if gated:
                violations.append(f"{where}: {host}: public host gated, it would become unreachable")
        elif not gated:
            violations.append(f"{where}: {host}: missing {GATE}")


def _check_ingress_route(doc, violations):
    where = _where(doc)
    for route in (doc.get("spec") or {}).get("routes") or []:
        _check_route(where, route.get("match"), _route_has_gate(route), violations)


def _check_core_ingress(doc, violations):
    where = _where(doc)
    annotations = (doc.get("metadata") or {}).get("annotations") or {}
    middlewares = [item.strip() for item in (annotations.get(MIDDLEWARE_ANNOTATION) or "").split(",")]
    gated = GATE in middlewares
    for rule in (doc.get("spec") or {}).get("rules") or []:
        _check_route(where, f"Host(`{rule.get('host')}`)" if rule.get("host") else "", gated, violations)


def _check_helm_chart_config(doc, violations):
    where = _where(doc)
    values = (doc.get("spec") or {}).get("valuesContent") or ""
    try:
        parsed = __import__("yaml").safe_load(values)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        dashboard = ((parsed.get("ingressRoute") or {}).get("dashboard") or {})
        gated = any(
            middleware.get("name") == GATE_NAME and middleware.get("namespace") == GATE_NAMESPACE
            for middleware in dashboard.get("middlewares") or []
            if isinstance(middleware, dict)
        )
    else:
        # traefik-config.yaml carries a mis-indented key the Helm controller tolerates
        # and PyYAML does not; the gate is still checkable as text.
        gated = f"name: {GATE_NAME}" in values
    if not gated:
        violations.append(f"{where}: traefik dashboard route: missing {GATE}")


def _check_gate_middleware(doc, violations):
    if (doc.get("metadata") or {}).get("name") != GATE_NAME:
        return
    ranges = ((doc.get("spec") or {}).get("ipWhiteList") or {}).get("sourceRange") or []
    ranges = [item for item in ranges if item]
    if len(ranges) < 2:
        violations.append(
            f"{_where(doc)}: sourceRange must hold the LAN and WireGuard ranges "
            f"(found {len(ranges)}); an empty range allows everyone"
        )


def check_documents(docs):
    violations = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if _has_key(doc, "ipAllowList"):
            violations.append(
                f"{_where(doc)}: ipAllowList is pruned by the installed CRD; "
                "the allowlist would be empty and allow everyone. Use ipWhiteList until W5."
            )
            continue
        api_version = doc.get("apiVersion", "")
        kind = doc.get("kind")
        if kind == "IngressRoute" and api_version.startswith("traefik."):
            _check_ingress_route(doc, violations)
        elif kind == "Ingress" and api_version.startswith("networking.k8s.io/"):
            _check_core_ingress(doc, violations)
        elif kind == "Middleware" and api_version.startswith("traefik."):
            _check_gate_middleware(doc, violations)
        elif kind == "HelmChartConfig":
            _check_helm_chart_config(doc, violations)
    return violations


def main(argv):
    import sys

    import yaml

    if not argv:
        print("usage: check_exposure.py <file> [<file>...]  ('-' reads stdin)", file=sys.stderr)
        return 2
    violations = []
    for path in argv:
        stream = sys.stdin if path == "-" else open(path, encoding="utf-8")
        try:
            violations.extend(check_documents(list(yaml.safe_load_all(stream))))
        finally:
            if path != "-":
                stream.close()
    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
