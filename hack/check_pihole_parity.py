"""Check that the two edge Pi-holes answer the same configuration.

rbp0 is the source of truth and rbp1 conforms: when the two disagree, rbp1 is
wrong. Most of the agreement is structural — `pihole.settings` is rendered from
one template — but a setting changed in a panel, or a list added there, lives in
that host's own `pihole.toml` and in no file in this repository. This is the
check that names the difference instead of leaving it to be noticed.

Input is two panel snapshots, shaped `{"config": {...}, "lists": [...]}`, read
from each panel's own API over HTTPS:

    GET /api/config   the settings tree
    GET /api/lists    the blocklists, compared by address

Each connection trusts only that panel's own certificate, kept beside this file
in `pihole/`; see `CERTIFICATES` for why and how it is refreshed.

Output is one line per difference, with both values. Empty means clean.

    PIHOLE_RBP0_API_KEY=… PIHOLE_RBP1_API_KEY=… python3 hack/check_pihole_parity.py

Exit: 0 clean, 1 differences, 2 a panel could not be read.
"""

import json
import os
import pathlib
import ssl
import sys
import urllib.error
import urllib.request

HOSTS = {"rbp0": "192.168.1.22", "rbp1": "192.168.1.38"}

# Pi-hole mints its own self-signed leaf, and its only name is `pi.hole`, so the
# address this check dials could never match it. Each host is pinned to its own
# leaf instead: the PEM beside this file is the only trust anchor its connection
# accepts, which says more than a name check, because a server without that key
# cannot complete the handshake. HTTP answers on the same address, and that is
# how the app password used to cross the LAN readable to any device on it; it
# is not an option here.
#
# A reflash mints a new certificate, and the read then fails until the pin is
# refreshed — deliberately, since an unknown certificate is the one thing this
# must not accept silently. Re-export the pair and commit them:
#
#     for h in 192.168.1.22 192.168.1.38; do
#         echo | openssl s_client -connect "$h:443" 2>/dev/null | openssl x509
#     done
CERTIFICATES = {
    name: pathlib.Path(__file__).resolve().parent / "pihole" / f"{name}.pem"
    for name in HOSTS
}

# Pi-hole stores password hashes and session secrets inside the config tree. A
# hash that differs is not drift worth reporting, and printing one is worse.
SECRET_MARKERS = ("hash", "password", "totp", "sid", "pin")


def snapshot(config: dict, adlists: list) -> dict:
    """The two API answers as one tree, with the adlists keyed by address."""
    return {
        "config": config,
        "lists": sorted(
            entry.get("address", "") for entry in adlists if entry.get("address")
        ),
    }


def flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from flatten(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        yield prefix, node
    else:
        yield prefix, node


def diff_panels(source: dict, replica: dict) -> list[str]:
    """One readable line per setting that differs, source first."""
    left = dict(flatten(source))
    right = dict(flatten(replica))
    lines = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) == right.get(key):
            continue
        if any(marker in key.lower() for marker in SECRET_MARKERS):
            lines.append(f"{key}: both set, contents not compared")
            continue
        lines.append(
            f"{key}: rbp0={json.dumps(left.get(key, '<missing>'))[:80]}"
            f" rbp1={json.dumps(right.get(key, '<missing>'))[:80]}"
        )
    return lines


def count_settings(snapshot: dict) -> int:
    """How many leaf settings and blocklist entries the snapshot holds.

    `flatten` yields (key, value) pairs, and a value can be a list, so the pairs
    go into a dict: a set of them would raise on the first list it met.
    """
    return len(dict(flatten(snapshot)))


def _context(host: str) -> ssl.SSLContext:
    """A TLS context that trusts the panel's pinned leaf, and nothing else."""
    try:
        context = ssl.create_default_context(cafile=str(CERTIFICATES[host]))
    except OSError as error:
        raise RuntimeError(f"{host}: cannot read its pinned certificate: {error}") from error
    # A self-signed leaf is a trust anchor only under the partial-chain rule, and
    # it carries no name for the address: the certificate itself is the pin.
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    context.check_hostname = False
    return context


def _call(
    base: str, method: str, path: str, body=None, sid=None, *, context: ssl.SSLContext
):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if sid:
        request.add_header("X-FTL-SID", sid)
    with urllib.request.urlopen(request, timeout=15, context=context) as answer:
        return json.loads(answer.read() or b"{}")


def read_panel(name: str, api_key: str) -> dict:
    base = f"https://{HOSTS[name]}"
    context = _context(name)
    session = _call(base, "POST", "/api/auth", {"password": api_key}, context=context)
    sid = (session.get("session") or {}).get("sid")
    if not sid:
        raise RuntimeError(f"{name}: the API key did not authenticate")
    try:
        config = _call(base, "GET", "/api/config", sid=sid, context=context).get("config", {})
        adlists = _call(base, "GET", "/api/lists", sid=sid, context=context).get("lists", [])
    finally:
        # A panel keeps a handful of sessions and answers 429 with none left, so
        # a check that ran twice in ten minutes would stop being runnable.
        try:
            _call(base, "DELETE", f"/api/auth/session/{sid}", sid=sid, context=context)
        except (urllib.error.URLError, TimeoutError):
            pass
    return snapshot(config, adlists)


def main() -> int:
    keys = {}
    for name in HOSTS:
        key = os.environ.get(f"PIHOLE_{name.upper()}_API_KEY", "").strip()
        if not key:
            print(f"missing PIHOLE_{name.upper()}_API_KEY in the environment", file=sys.stderr)
            return 2
        keys[name] = key
    try:
        source = read_panel("rbp0", keys["rbp0"])
        replica = read_panel("rbp1", keys["rbp1"])
    except (urllib.error.URLError, RuntimeError, TimeoutError) as error:
        print(f"could not read a panel: {error}", file=sys.stderr)
        return 2

    differences = diff_panels(source, replica)
    if not differences:
        print(f"result: OK ({count_settings(source)} settings and both blocklist sets agree)")
        return 0
    print(f"rbp1 differs from rbp0 in {len(differences)} place(s):")
    for line in differences:
        print(f"  {line}")
    print("result: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
