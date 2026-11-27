# Internal zone rename: `*.kub.local` to `*.kub.internal` — implementation plan

**Spec:** [`../specs/2026-11-27-internal-zone-kub-internal-design.md`](../specs/2026-11-27-internal-zone-kub-internal-design.md)
**Date:** 2026-11-27 · **Branch:** `feature/internal-zone-kub-internal` from `develop`

## Goal

Every Traefik-served internal name answers as `<app>.kub.internal` from the LAN, with a certificate
from the ashulab internal CA, while every saved `*.kub.local` URL still reaches its application
through one wildcard redirect until the cleanup.

## How the pieces fit

1. DNS: Pi-hole answers `*.kub.internal` with `192.168.1.46`; CoreDNS forwards the zone to the Pis.
2. Certificate: the same `*.kub.internal` leaf carries `*.kub.local` for the soak.
3. Routes: each chart's `ingressRoute.host` becomes the new name.
4. Safety net: `traefik-gate` gains a wildcard router that 302s `*.kub.local` to the same name
   under `.kub.internal`, upgrading to `https` in the same hop except for the two plain-HTTP names
   (`pgadmin`, `prometheus`), which keep their scheme because they have no TLS route.

Order matters. The redirect is applied **before** any chart is renamed: an exact `Host` route
outranks the wildcard (which carries `priority: 1`), so applying it early changes nothing, and each
renamed Application falls through to it during ArgoCD's per-Application sync.

## Stack

- Helm charts under `cluster/helm/`, rendered by ArgoCD from `develop`.
- Traefik `traefik.io/v1alpha1` CRDs, chart `42.4.1` / Traefik `3.7.19`.
- Pi-hole on `rbp0`/`rbp1`, configured by `ansible/pi/hosts-pi.sh`.
- Python 3 tests under `hack/tests/`, run with `uv`.
- The agent's shell is read-only on the cluster. Steps marked **[operator]** are run by the human.

## Before you start

The design and this plan live on `feature/internal-zone-kub-internal`, cut from `develop` when they
were committed. Confirm you are on it; create it only if the branch is missing:

```bash
cd /home/vsynthese/.local/share/opencode/worktree/117be0/misty-squid
git branch --show-current          # feature/internal-zone-kub-internal
git switch -c feature/internal-zone-kub-internal  # only if the line above is empty
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```

The suite must be green before the first change. Record the count; every later task compares to it.

---

## Task 1 — Measure the resolver chain and write the record

The rename makes `kub.internal` resolvable; it does not make a device ask the Pi-hole. This task
establishes, with evidence, which of those two problems the phone has.

**Files:**
- Create `docs/audit/internal-zone-preflight-2026-11-27.md`
- Edit `mkdocs.yml` (nav, after the Livebox entry, line ~176)

**Step 1: measure, from a session**

```bash
dig +short grafana.kub.local @192.168.1.22
dig +short grafana.kub.local @192.168.1.38
dig +short grafana.kub.local @192.168.1.1
dig +short kub.internal @192.168.1.22
for h in nuc0 nas streaming rbp2; do
  echo "$h: $(ssh "$h" 'cat /etc/resolv.conf | grep -v "^#"' 2>/dev/null | tr '\n' ' ')"
done
```

Expected before the change: the Pis answer `grafana.kub.local`, the Livebox answers nothing, and
`kub.internal` answers nothing yet. The resolver lines are evidence for card 889.

**Step 2: measure the phone [operator]**

On the Pixel, with the WireGuard tunnel **off** and joined to the house Wi-Fi:

1. Settings → Network & internet → Internet → the network → the gear icon. Note the IP settings
   ("DHCP" or "Static") and, when static, the DNS 1/DNS 2 fields.
2. Settings → Network & internet → Private DNS. Note `Off`, `Automatic`, or a hostname.
3. Open `https://grafana.kub.local` and note whether it times out (mDNS, the known failure) or
   answers with a certificate warning.

Record the three answers.

**Step 3: write the page**

```markdown
# Internal zone pre-flight, measured — resolvers, phone and DHCP

> **In one line.** What answers `*.kub.local` on 2026-11-27, which resolver the Android phone asks,
> and whether the Livebox hands out the Pi-holes — the three facts the `.kub.internal` rename
> depends on.
>
> **Use it when** the phone cannot open an internal name, or a LAN client resolves one name and not
> another · **Verified** 2026-11-27 · **Kind:** record
>
> **Sources** `dig` against `192.168.1.22`, `.38` and `.1`; `/etc/resolv.conf` on the four nodes;
> the phone's Settings screens, read by the operator.

| Where | Resolver | `grafana.kub.local` | `kub.internal` |
|---|---|---|---|
| `rbp0` Pi-hole | itself | <answer> | <answer> |
| `rbp1` Pi-hole | itself | <answer> | <answer> |
| Livebox `192.168.1.1` | upstream | <answer> | <answer> |
| Phone, Wi-Fi | <DhcpInfo.dns1/dns2> | <timeout or answer> | n/a |

## The phone

- IP settings: <DHCP or Static>
- Private DNS: <Off, Automatic, or the hostname>
- `grafana.kub.local`, tunnel off: <timeout or certificate warning>

## The nodes

| Node | `/etc/resolv.conf` nameservers |
|---|---|
| `nuc0` | <...> |
| `nas` | <...> |
| `streaming` | <...> |
| `rbp2` | <...> |

## What this decides

- Phone resolver is a Pi-hole, Private DNS off or automatic → the phone is fixed by this change.
- Phone resolver is the Livebox → the rename still ships (decided 2026-11-27); the phone stays
  unverified until card 894 hands out the Pi-holes, or the operator sets a static DNS on the phone.
- Private DNS names a hostname → it overrides the network and must be turned off for the gate to
  pass.
- A node still names a public resolver → `ansible/roles/resolver` runs against it in Task 10.
```

**Step 4: add the nav entry**

In `mkdocs.yml`, directly after the Livebox line:

```yaml
      - Internal zone pre-flight (2026-11-27): audit/internal-zone-preflight-2026-11-27.md
```

**Step 5: verify and commit**

```bash
hack/check-docs.sh --base origin/develop
git add docs/audit/internal-zone-preflight-2026-11-27.md mkdocs.yml
git commit -m "📝 docs: measure the internal zone before the rename"
```

---

## Task 2 — Pi-hole answers the new zone

**Files:**
- Edit `hack/tests/test_pi_script.py`
- Edit `ansible/playbooks/templates/runners/02-my-wildcard-dns.conf`
- Edit `ansible/pi/README.md`

**Step 1: write the failing test**

Add after `test_each_pihole_name_wins_over_the_wildcard`:

```python
def test_the_new_internal_zone_answers_with_the_cluster_address():
    """The Android phone resolves `*.kub.local` as mDNS and never asks a DNS server on Wi-Fi.
    `.kub.internal` is an ordinary unicast name, so the wildcard has to exist before any chart is
    renamed; without it the new hosts are NXDOMAIN on every LAN client."""
    dns = (
        REPO / "ansible/playbooks/templates/runners/02-my-wildcard-dns.conf"
    ).read_text()
    assert "address=/kub.internal/192.168.1.46" in dns
    # The old zone keeps answering through the soak: the edge Pi names and the redirect need it.
    assert "address=/kub.local/192.168.1.46" in dns
```

**Step 2: run it, expect failure**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k new_internal_zone
```

**Step 3: add the line**

In `02-my-wildcard-dns.conf`, directly below `address=/kub.local/192.168.1.46`:

```
address=/kub.internal/192.168.1.46
```

Keep the two lines adjacent and put the reason above them:

```
# Two internal zones during the soak: `kub.internal` is what every route serves from 2026-11-27,
# `kub.local` stays for the edge Pi names and for the wildcard redirect that keeps old URLs alive.
```

**Step 4: update the Pi README**

In `ansible/pi/README.md`, the wildcard row (§ around line 37) and the note at line 126 name
`*.kub.local` as the only zone. Add `*.kub.internal` beside it, and say the old zone remains until
the soak ends.

**Step 5: run the Pi tests**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py
```

**Step 6: commit**

```bash
git add hack/tests/test_pi_script.py ansible/playbooks/templates/runners/02-my-wildcard-dns.conf ansible/pi/README.md
git commit -m "🚀 feat(dns): answer kub.internal from both Pi-holes"
```

---

## Task 3 — CoreDNS forwards the new zone

**Files:**
- Edit `hack/tests/test_coredns_custom.py`
- Edit `cluster/infrastructure/coredns/custom.yaml`

**Step 1: change the expectation first**

Replace the forward assertion in `test_the_internal_wildcard_is_forwarded_to_the_two_pis` with:

```python
    assert forward == [
        f"forward kub.local {' '.join(edge_addresses())}",
        f"forward kub.internal {' '.join(edge_addresses())}",
    ], "both internal zones go to the two Pis in inventory order and nothing else"
```

Keep the docstring and add one sentence: a public resolver answers `kub.internal` with NXDOMAIN
exactly as it does `kub.local`.

**Step 2: run it, expect failure**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_coredns_custom.py
```

**Step 3: add the forward**

In `custom.yaml`, under the existing `forward kub.local ...` line, add:

```
    forward kub.internal 192.168.1.22 192.168.1.38
```

Extend the key's comment by one paragraph: `kub.internal` is the zone every route serves from
2026-11-27, and a public resolver in the node's list answers it with NXDOMAIN just as it did for
`kub.local`.

**Do not rename the ConfigMap key.** `kub-local.override` is applied by hand, and `kubectl apply`
would leave a stale key behind; the key name is cosmetic, the forward lines are the contract.

**Step 4: verify and commit**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_coredns_custom.py
helm template test cluster/helm/platform/traefik-gate >/dev/null
git add hack/tests/test_coredns_custom.py cluster/infrastructure/coredns/custom.yaml
git commit -m "🚀 feat(dns): forward kub.internal in CoreDNS"
```

---

## Task 4 — The leaf certificate carries both wildcards

**Files:**
- Create `hack/tests/test_internal_cert.py`
- Edit `hack/internal-cert.sh`

**Step 1: write the failing test**

```python
"""The cluster leaf is issued by hand, so nothing renders it. These checks pin the two names it has
to carry: the zone every route serves, and the old one the wildcard redirect still answers."""

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]


def san_line():
    text = (REPO / "hack/internal-cert.sh").read_text()
    match = re.search(r'^SAN="([^"]+)"', text, re.MULTILINE)
    assert match, "the SAN list is one line the script passes to openssl"
    return match.group(1).split(",")


def test_the_leaf_covers_both_internal_wildcards():
    san = san_line()
    assert "DNS:*.kub.internal" in san
    assert "DNS:*.kub.local" in san, "the redirect answers old names over TLS during the soak"


def test_the_leaf_keeps_the_names_the_script_already_serves():
    san = san_line()
    assert "DNS:*.app.local" in san
    assert "DNS:localhost" in san
    assert "IP:127.0.0.1" in san


def test_the_help_text_no_longer_claims_a_single_zone():
    text = (REPO / "hack/internal-cert.sh").read_text()
    header = text.split("set -euo pipefail", 1)[0]
    assert "kub.internal" in header
```

**Step 2: run it, expect failure**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_internal_cert.py
```

**Step 3: change the SAN and the header**

```bash
SAN="DNS:*.kub.internal,DNS:*.kub.local,DNS:*.app.local,DNS:localhost,IP:127.0.0.1"
```

The common name stays `kub.local` for this run: the leaf file names stay too, and renaming them is
cosmetic. In the header, replace "the private `*.kub.local` one" with a sentence naming both zones
and saying `*.kub.local` stays until the soak ends.

**Step 4: verify and commit**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_internal_cert.py
bash -n hack/internal-cert.sh
git add hack/tests/test_internal_cert.py hack/internal-cert.sh
git commit -m "🚀 feat(pki): sign the internal leaf for kub.internal and kub.local"
```

---

## Task 5 — The wildcard redirect in `traefik-gate`

**Files:**
- Create `hack/tests/test_traefik_gate_redirect.py`
- Create `cluster/helm/platform/traefik-gate/templates/legacy-redirect.yaml`

**Step 1: write the failing test**

```python
"""The legacy redirect is the only route that answers `*.kub.local` once the charts are renamed.
It is rendered, so it can be read without a cluster; it must stay gated, and it must upgrade to TLS
in one hop for every name that has a `websecure` router, while leaving the two plain-HTTP names on
`http`."""

import pathlib
import subprocess
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CHART = REPO / "cluster/helm/platform/traefik-gate"


def render():
    out = subprocess.run(
        ["helm", "template", "test", str(CHART), "-f", str(CHART / "values.yaml")],
        check=True, capture_output=True, text=True,
    )
    return [d for d in yaml.safe_load_all(out.stdout) if d]


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.docs = render()
        self.routes = {
            tuple(d["spec"]["entryPoints"]): d
            for d in self.docs
            if d["kind"] == "IngressRoute"
        }
        self.middlewares = {d["metadata"]["name"]: d for d in self.docs if d["kind"] == "Middleware"}

    def test_one_router_per_entry_point(self):
        self.assertEqual(set(self.routes), {("web",), ("websecure",)})

    def test_the_plain_names_have_their_own_rule_and_win(self):
        rules = self.routes[("web",)]["spec"]["routes"]
        plain = [r for r in rules if "pgadmin" in r["match"]]
        self.assertEqual(len(plain), 1)
        self.assertEqual(
            plain[0]["match"],
            "HostRegexp(`^pgadmin\\.kub\\.local$|^prometheus\\.kub\\.local$`)",
        )
        general = [r for r in rules if r is not plain[0]]
        self.assertEqual(len(general), 1)
        self.assertGreater(plain[0]["priority"], general[0]["priority"])

    def test_the_general_rule_is_an_exact_one_label_wildcard(self):
        for entrypoint in (("web",), ("websecure",)):
            rules = [
                r for r in self.routes[entrypoint]["spec"]["routes"] if "pgadmin" not in r["match"]
            ]
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]["match"], "HostRegexp(`^[a-z0-9-]+\\.kub\\.local$`)")

    def test_the_general_rules_sit_below_every_exact_route(self):
        for entrypoint in (("web",), ("websecure",)):
            for rule in self.routes[entrypoint]["spec"]["routes"]:
                if "pgadmin" not in rule["match"]:
                    self.assertEqual(rule["priority"], 1)

    def test_every_rule_is_gated_before_it_redirects(self):
        for router in self.routes.values():
            for rule in router["spec"]["routes"]:
                names = [m["name"] for m in rule["middlewares"]]
                self.assertEqual(names[0], "kube-system-lan-only@kubernetescrd")
                self.assertIn(
                    names[1],
                    {
                        "kube-system-redirect-to-internal-tls@kubernetescrd",
                        "kube-system-redirect-to-internal-plain@kubernetescrd",
                    },
                )

    def test_the_plain_rule_uses_the_plain_middleware(self):
        plain = [
            r for r in self.routes[("web",)]["spec"]["routes"] if "pgadmin" in r["match"]
        ][0]
        self.assertEqual(
            [m["name"] for m in plain["middlewares"]][1],
            "kube-system-redirect-to-internal-plain@kubernetescrd",
        )

    def test_the_tls_router_uses_the_default_certificate(self):
        self.assertNotIn("tls", self.routes[("web",)]["spec"])
        tls = self.routes[("websecure",)]["spec"]["tls"]
        self.assertNotIn("secretName", tls)
        self.assertNotIn("certResolver", tls)

    def test_the_redirects_upgrade_to_tls_in_one_hop(self):
        """`http://sonarr.kub.local` must land on `https://sonarr.kub.internal` directly: replaying
        the scheme would send it to `http`, and the app's own router would add a second hop."""
        for name, scheme in (
            ("redirect-to-internal-tls", "https"),
            ("redirect-to-internal-plain", "http"),
        ):
            redirect = self.middlewares[name]["spec"]["redirectRegex"]
            self.assertEqual(redirect["regex"], r"^https?://([a-z0-9-]+)\.kub\.local(.*)$")
            self.assertEqual(redirect["replacement"], f"{scheme}://${{1}}.kub.internal${{2}}")
            self.assertFalse(
                redirect["permanent"], "a POST client must not be taught to resend as GET"
            )
```

**Step 2: run it, expect failure**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_traefik_gate_redirect.py
```

**Step 3: write the template**

`cluster/helm/platform/traefik-gate/templates/legacy-redirect.yaml`:

```yaml
{{- /*
The old internal zone. From 2026-11-27 every chart serves `*.kub.internal`; this pair is what keeps
a bookmark, an alert link and a browser history entry written for `*.kub.local` working until the
soak ends.

Priority 1 on the general rules: Traefik ranks a rule by its length, and an exact `Host` route is
longer. Applying this before the charts are renamed therefore changes nothing, and each name falls
through to it during ArgoCD's per-Application sync instead of being dead in between.

Two middlewares, because the upgrade is not the same for every name. `http://sonarr.kub.local` must
land on `https://sonarr.kub.internal` in one hop: replaying the scheme would send it to `http`, and
the application's own `web` router would then redirect a second time. `pgadmin` and `prometheus` are
the exception — they have no `websecure` router, so they keep `http` and get a rule of their own that
outranks the general one.

302, not 301: a permanent redirect would teach a client to replay a POST as a GET.
*/ -}}
apiVersion: {{ .Values.traefik.apiVersion }}
kind: IngressRoute
metadata:
  name: legacy-internal-redirect
  namespace: kube-system
  labels:
    app.kubernetes.io/name: traefik-gate
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  entryPoints:
    - web
  routes:
    - kind: Rule
      match: HostRegexp(`^pgadmin\.kub\.local$|^prometheus\.kub\.local$`)
      priority: 2
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
        - name: kube-system-redirect-to-internal-plain@kubernetescrd
      services:
        - name: noop@internal
          kind: TraefikService
    - kind: Rule
      match: HostRegexp(`^[a-z0-9-]+\.kub\.local$`)
      priority: 1
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
        - name: kube-system-redirect-to-internal-tls@kubernetescrd
      services:
        - name: noop@internal
          kind: TraefikService
---
apiVersion: {{ .Values.traefik.apiVersion }}
kind: IngressRoute
metadata:
  name: legacy-internal-redirect-tls
  namespace: kube-system
  labels:
    app.kubernetes.io/name: traefik-gate
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  entryPoints:
    - websecure
  routes:
    - kind: Rule
      match: HostRegexp(`^[a-z0-9-]+\.kub\.local$`)
      priority: 1
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
        - name: kube-system-redirect-to-internal-tls@kubernetescrd
      services:
        - name: noop@internal
          kind: TraefikService
  tls: {}
---
apiVersion: {{ .Values.traefik.apiVersion }}
kind: Middleware
metadata:
  name: redirect-to-internal-tls
  namespace: kube-system
  labels:
    app.kubernetes.io/name: traefik-gate
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  redirectRegex:
    regex: ^https?://([a-z0-9-]+)\.kub\.local(.*)$
    replacement: https://${1}.kub.internal${2}
    permanent: false
---
apiVersion: {{ .Values.traefik.apiVersion }}
kind: Middleware
metadata:
  name: redirect-to-internal-plain
  namespace: kube-system
  labels:
    app.kubernetes.io/name: traefik-gate
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  redirectRegex:
    regex: ^https?://([a-z0-9-]+)\.kub\.local(.*)$
    replacement: http://${1}.kub.internal${2}
    permanent: false
```

If the CRD rejects `noop@internal` at render time, use the same service shape as the historical
`dashy-local` router in `cluster/infrastructure/traefik/snapshot/2026-11-21/objects.yaml` — read it,
do not invent a third form. `api@internal`, which the dashboard's own redirect uses, would serve the
dashboard instead of redirecting and is wrong here.

**Step 4: run the test and the gate check**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_traefik_gate_redirect.py
hack/check-exposure.sh --only traefik-gate
helm lint cluster/helm/platform/traefik-gate
```

**Step 5: bump the chart version and commit**

`Chart.yaml`: `version: 0.2.0` → `0.3.0` (a new template is a feature).

```bash
git add hack/tests/test_traefik_gate_redirect.py cluster/helm/platform/traefik-gate
git commit -m "🚀 feat(traefik): redirect the old internal zone to kub.internal"
```

---

## Task 6 — Rename the chart hosts

**Files:**
- Create `hack/tests/test_internal_zone.py`
- Edit every `cluster/helm/**/values.yaml` that carries an internal host (22 files)
- Edit `cluster/helm/dashy/templates/ingress-redirect.yaml` (the host is typed in the template)

**Step 1: write the failing guard**

```python
"""No chart may declare a `*.kub.local` host again.

The zone is `*.kub.internal` from 2026-11-27. `.local` is reserved for mDNS by RFC 6762, and Android
sends every `.local` name to 224.0.0.251 and never to the configured DNS, so a chart that reintroduces
the suffix reintroduces the bug for every phone on the LAN. The old zone is answered by one wildcard
redirect in `traefik-gate`, not by a route of its own.
"""

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
HELM = REPO / "cluster" / "helm"

# The two names the cluster dials on the Pis; they keep their per-host leaves and their Pi-hole
# records, and they are the only `*.kub.local` values left in the charts.
EDGE_NAMES = ("pihole-rbp0.kub.local", "pihole-rbp1.kub.local")


def active_lines(path):
    """Configuration only: a comment may explain the old zone, a value may not carry it."""
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        yield number, line


def test_no_chart_value_names_the_old_zone():
    offenders = [
        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
        for path in sorted(HELM.rglob("values.yaml"))
        for number, line in active_lines(path)
        if ".kub.local" in line and not any(edge in line for edge in EDGE_NAMES)
    ]
    assert offenders == [], "\n".join(offenders)


def test_the_edge_pi_names_stay_on_the_old_zone():
    """`pihole-rbp0`/`rbp1` are served by the Pis themselves, with a per-host leaf, and only the
    cluster's exporter dials them. They are not Traefik hosts and they do not move."""
    exporter = (HELM / "monitoring/pihole-exporter/values.yaml").read_text()
    for name in EDGE_NAMES:
        assert name in exporter


def test_the_start_page_keeps_one_internal_name():
    """Dashy's own host is typed in a template rather than a value: two rules, one entry point
    each, both redirecting to the public page."""
    text = (HELM / "dashy/templates/ingress-redirect.yaml").read_text()
    assert text.count("Host(`dashy.kub.internal`)") == 2
    assert "dashy.kub.local" not in text
```

**Step 2: run it, expect a long list**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_internal_zone.py
```

**Step 3: rename**

Replace `.kub.local` with `.kub.internal` on every non-comment line the test reports. The host in
each chart is one string; do not touch a comment that explains the old zone, and do not touch a
public name.

Expected files (one `host:` each unless noted):

| File | Key |
|---|---|
| `docs-site/values.yaml` | `traefik.host` |
| `databases/pgadmin/values.yaml` | `ingressRoute.host` |
| `vikunja/values.yaml` | `ingressRoute.host` |
| `monitoring/grafana/values.yaml` | `ingressRoute.host` |
| `monitoring/prometheus/values.yaml` | `ingressRoute.host` |
| `monitoring/loki/values.yaml` | `ingressRoute.host` |
| `media-stack/{radarr,radarr-anime,lidarr,prowlarr,sonarr,sonarr-anime,bazarr,bazarr-anime,qbittorrent,autobrr,tautulli,maintainerr}/values.yaml` | `ingressRoute.host` |
| `media-stack/calibre-web-automated/values.yaml` | `ingressRoute.host` (line ~293, named `calibre`) |
| `security/crowdsec-web-ui/values.yaml` | `ingressRoute.host` |
| `argocd/values.yaml` | `global.domain` and the URL at line ~96 |
| `platform/traefik/values.yaml` | the dashboard `matchRule` |
| `dashy/templates/ingress-redirect.yaml` | two `Host(...)` matches |

`monitoring/pihole-exporter/values.yaml` keeps `pihole-rbp0.kub.local` and `pihole-rbp1.kub.local`:
those are the PIs' own names, with their own leaves, and only the exporter dials them. The test's
`EDGE_NAMES` allowlist is what keeps that deliberate.

`dashy` the application keeps `dashy.ashudev.com` as its public route; the `kub.internal` name is
the LAN entry that redirects to it, and it has no `values.yaml` host to rename.

**Step 4: verify**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_internal_zone.py
hack/check-charts.sh
```

**Step 5: commit**

```bash
git add hack/tests/test_internal_zone.py cluster/helm
git commit -m "🚀 feat(zone): serve every internal application on kub.internal"
```

---

## Task 7 — Rename the stored URLs and probe headers

The chart host is only half of it. A URL stored in a value is what an app tells another app.

**Files:**
- `cluster/helm/argocd/values.yaml` — `global.domain`; the CLI and dex callback follow it
- `cluster/helm/monitoring/grafana/values.yaml` — `root_url`, `domain`
- `cluster/helm/vikunja/values.yaml` — `VIKUNJA_AUTH_OPENID_REDIRECTURL`, `publicurl`
- `cluster/helm/media-stack/autobrr/values.yaml` — `AUTOBRR__OIDC_REDIRECT_URL`
- `cluster/helm/media-stack/{radarr,radarr-anime,prowlarr}/values.yaml` — three probe `Host` headers each

**Step 1: confirm the guard catches them**

The test from Task 6 already scans every non-comment line, so these are the rest of its list. Run it
and read the output rather than searching by hand:

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_internal_zone.py -x
```

**Step 2: rename**

Each value becomes the same name under `kub.internal`. For grafana, `root_url` must end with a
slash (`https://grafana.kub.internal/`) and `domain` must not.

**Step 3: verify**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_internal_zone.py
hack/check-charts.sh
```

**Step 4: commit**

```bash
git add cluster/helm
git commit -m "🚀 feat(zone): point stored URLs and probes at kub.internal"
```

---

## Task 8 — authentik providers and applications

A forward-auth provider's `external_host` is the scheme and host the outpost builds its cookie and
its redirect from. A native OIDC application's `redirect_uris` must match what the app sends.

**Files:**
- Edit `hack/tests/test_authentik_blueprints.py`
- Edit `cluster/helm/authentik/files/app-*.yaml` (12 files)

**Step 1: write the failing test**

Add to `hack/tests/test_authentik_blueprints.py`:

```python
OLD_ZONE = ".kub.local"


def test_no_blueprint_names_the_old_zone():
    """Every `external_host`, `meta_launch_url` and `redirect_uris` entry is what the outpost and
    the provider compare against the browser. A leftover `.kub.local` value is a login loop, not a
    broken link, so it cannot wait for the redirect."""
    offenders = []
    for path in sorted(FILES.glob("*.yaml")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if OLD_ZONE in line and not line.strip().startswith("#"):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)
```

**Step 2: run it, expect failure**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_authentik_blueprints.py
```

**Step 3: rename**

Every `external_host: https://<app>.kub.local`, `meta_launch_url: https://<app>.kub.local/` and
`- https://<app>.kub.local/...` under `redirect_uris` becomes the same name under `kub.internal`.
`app-syncthing.yaml`, `app-dashy.yaml` and the authentik sign-in page itself name public hosts and
are untouched — the test will say so if one is missed or wrongly changed.

**Step 4: verify**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_authentik_blueprints.py
helm lint cluster/helm/authentik
```

**Step 5: commit**

```bash
git add hack/tests/test_authentik_blueprints.py cluster/helm/authentik/files
git commit -m "🚀 feat(authentik): move the internal providers to kub.internal"
```

---

## Task 9 — Start page, docs site, dashboard, scripts, exported dashboard

**Files:**
- `cluster/helm/dashy/files/conf.yml` — the internal links (20)
- `cluster/helm/platform/traefik/values.yaml` — the dashboard `matchRule` (also in Task 6)
- `cluster/infrastructure/traefik/dashboard/redirect.yaml` — the hand-applied `web` twin
- `cluster/helm/monitoring/grafana/export/traefik/dashboard.json` — the one in-git dashboard link
- `hack/vikunja_site_backfill.py`, `hack/vikunja_migrate.py`, `hack/vikunja-seed.sh` — default URLs
- `docs-site` host already done in Task 6

**Step 1: extend the guard to the non-values files**

In `hack/tests/test_internal_zone.py`, add:

```python
EXTRA = [
    REPO / "cluster/helm/dashy/files/conf.yml",
    REPO / "cluster/infrastructure/traefik/dashboard/redirect.yaml",
    REPO / "cluster/helm/monitoring/grafana/export/traefik/dashboard.json",
]


def test_the_hand_maintained_copies_agree():
    offenders = [
        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
        for path in EXTRA
        for number, line in active_lines(path)
        if ".kub.local" in line
    ]
    assert offenders == [], "\n".join(offenders)
```

`conf.yml` and the JSON have no YAML comments; `active_lines` treats a `#` at the start of a trimmed
line as a comment, so the `conf.yml` header comment is skipped. If a JSON line legitimately carries
`#`, drop the JSON from `EXTRA` and assert it with an explicit `json.load` instead.

**Step 2: run it, expect failure**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_internal_zone.py
```

**Step 3: rename the three files**

- `conf.yml`: every `url: https://<app>.kub.local` → `.kub.internal`; `http://prometheus...` and
  `http://pgadmin...` keep their scheme.
- `dashboard/redirect.yaml`: the `web` router's `Host(...)` → `traefik.kub.internal`.
- `dashboard.json`: the link to the dashboard → `https://traefik.kub.internal/dashboard/`.

**Step 4: rename the script defaults**

```bash
grep -rn 'kub\.local' hack/*.py hack/*.sh
```

Replace the default `VIKUNJA_URL`/base URL in each with `https://vikunja.kub.internal`. These are
defaults for a seed or a migration, not live data; the tests in `hack/tests/test_vikunja_*.py` take
their URL from the environment or a temp tree, so they must still pass.

**Step 5: verify and commit**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
hack/check-charts.sh
git add hack/tests/test_internal_zone.py cluster/helm cluster/infrastructure/traefik/dashboard/redirect.yaml hack
git commit -m "🚀 feat(zone): move the start page, dashboard and script defaults"
```

---

## Task 10 — The docs generator and the routes page

The `routes` page is generated from the rendered charts. Two names in its prose are typed, and the
`IngressRoute` count moves by two when Task 5 lands.

**Files:**
- `hack/gen_docs.py`
- `docs/reference/routes.md` (generated)
- `cluster/helm/security/security-exporter/values.yaml`

**Step 1: teach the generator the new zone**

```python
    if host.endswith(".kub.internal") or host.endswith(".kub.local"):
        return "internal CA"
```

The `.kub.local` branch stays only while the redirect exists; it renders no rows (the router has no
`Host(`), so it is harmless.

**Step 2: fix the typed prose**

Lines ~359 and ~370: `traefik.kub.local` → `traefik.kub.internal`, `loki.kub.local` →
`loki.kub.internal`.

**Step 3: set the count**

`objects` rises by 2 (the two redirect routers), so `LIVE_INGRESS_ROUTES` becomes `77`. Update the
same number and its note in `security-exporter/values.yaml`:

```yaml
    ingressRoutes: 77
  expectedNote: "... 77 routes with the legacy-redirect pair; see docs/reference/routes.md"
```

`hack/tests/test_security_exporter.py` fails if the two disagree.

**Step 4: regenerate and check**

```bash
python3 hack/gen_docs.py --write --only routes
python3 hack/gen_docs.py --check --only routes
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_gen_docs.py hack/tests/test_security_exporter.py
```

**Step 5: read the regenerated page**

Open `docs/reference/routes.md` and confirm: 23 rows now end `.kub.internal`, the public rows are
unchanged, and no row ends `.kub.local`.

**Step 6: commit**

```bash
git add hack/gen_docs.py docs/reference/routes.md cluster/helm/security/security-exporter/values.yaml
git commit -m "🚀 feat(docs): generate the routes page for kub.internal"
```

---

## Task 11 — Living documentation and the decision record

**Files:**
- Create `docs/decisions/2026-11-27-internal-zone-is-kub-internal.md`
- Edit `mkdocs.yml` (Decisions nav, after the DNS4EU line at ~163)
- Edit `docs/decisions/README.md` (the records table)
- Edit every living page that names `*.kub.local` (the list below)
- Edit `docs/how-it-works/network.md` (a section on `.internal` and Android)
- Edit the chart prose that still asserts the old zone: `cluster/helm/**/README.md` and the
  `*.yaml` comments found by Step 2b

**Step 1: list the living pages**

```bash
grep -rl 'kub\.local' docs --include='*.md' \
  | grep -vE 'docs/(audit|decisions|superpowers)/' | sort
```

Historical records (`docs/audit/`, dated `docs/decisions/` other than the new one, and
`docs/superpowers/`) keep the old names; they describe what was true then. `docs/reference/routes.md`
is generated and must not be hand-edited.

**Step 2: sweep, then review the exceptions**

A blanket replacement is correct for the hostname tables, links and commands:

```bash
for f in $(grep -rl 'kub\.local' docs --include='*.md' \
             | grep -vE 'docs/(audit|decisions|superpowers)/' \
             | grep -v 'docs/reference/routes.md'); do
  perl -pi -e 's/\.kub\.local/.kub.internal/g' "$f"
done
```

Then read the pages where the old zone is the subject rather than a name:

- `docs/runbooks/internal-ca.md` — the leaf now carries both wildcards; the script name and file
  names are unchanged; the old SAN is described as the soak.
- `docs/runbooks/traefik-release.md` — its history describes past releases; those sentences keep
  `.kub.local`.
- `docs/secrets.md` — the Bitwarden item and the Secret name are unchanged.
- `docs/reference/sso-applications.md` — the host column is new names; add one sentence saying the
  old names redirect during the soak.
- `docs/observability-conventions.md`, `docs/agent-access.md`, `docs/qbittorrent-webui-stalls.md` —
  hostnames only.

**Step 2b: sweep the chart prose**

The charts carry prose that names the served zone: `# --` doc comments in `values.yaml`, header
comments in templates, and each chart's `README.md`. A comment may explain the old zone; a sentence
that says the cluster serves `*.kub.local` is now false, and the guard test skips comments by design.

```bash
grep -rn 'kub\.local' cluster/helm --include='*.yaml' --include='*.md'
```

Rename a line only when it asserts current behaviour — "`web` is plain HTTP on kub.local", "the
cluster serves `*.kub.local` from the default certificate", "the host is a `.kub.local` name", "one
certificate and one policy for `*.kub.local`". Leave alone:

- `cluster/helm/platform/traefik-gate/templates/legacy-redirect.yaml` — the redirect is about the old
  zone by design, and its regexes must keep the suffix.
- `cluster/helm/monitoring/pihole-exporter/**` — `pihole-rbp0.kub.local` and `pihole-rbp1.kub.local`
  are the Pis' own names, outside the cluster's zone.
- `cluster/helm/authentik/files/**` — Task 8 owns the blueprints.
- Any sentence about the past ("did until 2026-11-17", "was", "used to", the soak).

Edit the chart `README.md` files by hand rather than with a blanket `sed`: they mix history with
current state, and the same sentence may be true in one and false in the other.

**Step 3: add the network section**

In `docs/how-it-works/network.md`, after the existing name-resolution material, add:

```markdown
## Why the internal zone is `kub.internal`

Android 12 and later resolve every name ending in `.local` through mDNS: the query goes to
`224.0.0.251:5353` and never to the DNS server of the Wi-Fi network. AOSP documents that VPN and
mobile-data connections are excluded from that treatment, which is why the same name worked over
WireGuard and timed out on the house Wi-Fi. RFC 6762 reserves `.local` for exactly that use.

`.internal` is reserved for private use by ICANN and is not delegated, so no public resolver answers
it and no client special-cases it. A device resolves it by asking its configured DNS server, which
must be a Pi-hole. The old zone still answers through one wildcard redirect in `traefik-gate` until
the soak ends.
```

**Step 4: write the decision record**

```markdown
# The internal zone is `kub.internal`, and `*.kub.local` redirects until 2027-01-01

Date: 2026-11-27 · Decided by: the operator · Status: accepted

## The decision

Every Traefik-served internal name is `<app>.kub.internal`, served by the ashulab internal CA. The
Pi-holes answer the wildcard and CoreDNS forwards it. `*.kub.local` is answered by one wildcard
redirect for a 30-day soak, then removed with the old SAN and the old Pi-hole wildcard.

## Why

An Android phone on the house Wi-Fi could not open any `*.kub.local` name, while the same names
worked over WireGuard. Android sends `.local` to mDNS `224.0.0.251:5353` and never to the configured
DNS; VPN networks are excluded from that. No mDNS responder exists on the LAN, so the name timed out.
RFC 6762 reserves the suffix; `kub.home.arpa` was rejected as residential-CPE framing around a name
that must stay short, and the public `*.kub.home.ashudev.com` would make every internal app
internet-resolvable.

The measurements are in `docs/audit/internal-zone-preflight-2026-11-27.md`.

## What it costs if it is wrong

A rename across 23 names, 20 charts, the authentik blueprints and the internal leaf: the rollback is
`git revert` until the cleanup, so the cost is one branch, not a rebuild. The soak keeps every saved
URL alive; the cleanup is a separate change with its own review. If the phone still asks the Livebox
after the change, the zone is right and the resolver is wrong, which is card 894.
```

**Step 5: nav and the records table**

`mkdocs.yml`, after the DNS4EU line:

```yaml
      - Internal zone is kub.internal: decisions/2026-11-27-internal-zone-is-kub-internal.md
```

`docs/decisions/README.md`: add one row to the records table, same shape as the others.

**Step 6: run the documentation checks**

```bash
hack/check-docs.sh --base origin/develop
```

Every failure is a page this change touched that is missing its banner, has a broken relative link,
cites a path that does not exist, is missing from the nav, or is stale against its generator. Fix
each in place; do not silence the check.

**Step 7: commit**

```bash
git add docs mkdocs.yml cluster/helm
git commit -m "📝 docs: describe the kub.internal zone and record the decision"
```

---

## Task 12 — Apply: DNS, certificate, redirect, sync

Every step here is **[operator]** except the reads. The agent's shell is read-only.

**Step 1: DNS on both Pis [operator]**

```bash
cd ansible/pi
./hosts-pi.sh setup rbp0
./hosts-pi.sh setup rbp1
./hosts-pi.sh verify rbp0
./hosts-pi.sh verify rbp1
```

Then from a LAN host:

```bash
dig +short grafana.kub.internal @192.168.1.22
dig +short grafana.kub.internal @192.168.1.38
```

Both must answer `192.168.1.46`. The old names must still answer as before.

**Step 2: the leaf [operator]**

```bash
hack/internal-cert.sh
openssl x509 -in ~/.config/secrets/kub-local/kub.local.pem -noout -text | grep -A1 'Subject Alternative Name'
kubectl -n kube-system get secret local-cert -o jsonpath='{.type}{"\n"}'
```

If the type is `Opaque`:

```bash
kubectl -n kube-system delete secret local-cert
kubectl -n kube-system create secret tls local-cert --save-config \
  --cert="$HOME/.config/secrets/kub-local/kub.local.pem" \
  --key="$HOME/.config/secrets/kub-local/kub.local-key.pem"
```

If it is `kubernetes.io/tls`, patch `data` in place instead; there is no reason to accept the window.

Copy the new leaf into the secrets store and refresh the Bitwarden item
`homelab-k8s-kube-system-local-cert`.

**Step 3: CoreDNS [operator]**

```bash
kubectl apply -f cluster/infrastructure/coredns/custom.yaml
kubectl -n kube-system rollout restart deploy/coredns
kubectl -n kube-system rollout status deploy/coredns
```

**Step 4: the redirect, while it is dormant [operator]**

Merge the branch to `develop`. ArgoCD renders `traefik-gate` with the new template. Sync it:

```bash
argocd app sync traefik-gate
kubectl -n kube-system get ingressroute legacy-internal-redirect legacy-internal-redirect-tls
```

At this point every live `*.kub.local` host still reaches its application, because its exact `Host`
route outranks the wildcard.

**Step 5: sync the renamed charts**

ArgoCD syncs each Application as its commit is seen. Watch them:

```bash
kubectl get applications -n argocd -o wide | grep -v Synced
```

A host that is renamed in git but not yet synced answers on its old name; one that is synced answers
on the new name and the old one redirects. No state has neither.

**Step 6: the hand-applied dashboard redirect [operator]**

```bash
kubectl apply -f cluster/infrastructure/traefik/dashboard/redirect.yaml
```

---

## Task 13 — Verify

Run every line and keep the output; a summary without the output is not evidence.

**DNS**

```bash
for h in argocd autobrr bazarr bazarr-anime calibre crowdsec dashy docs grafana lidarr loki \
         maintainerr pgadmin prometheus prowlarr qbittorrent radarr radarr-anime sonarr \
         sonarr-anime tautulli traefik vikunja; do
  a=$(dig +short "$h.kub.internal" @192.168.1.22)
  b=$(dig +short "$h.kub.internal" @192.168.1.38)
  [ "$a" = 192.168.1.46 ] && [ "$b" = 192.168.1.46 ] || echo "FAIL $h: $a / $b"
done
```

**TLS**

```bash
openssl s_client -connect 192.168.1.46:443 -servername grafana.kub.internal </dev/null 2>/dev/null \
  | openssl x509 -noout -issuer -ext subjectAltName
```

The SAN must name both wildcards.

**The redirect**

```bash
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'Host: sonarr.kub.local' http://192.168.1.46/
curl -sSk -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'Host: ausente.kub.local' https://192.168.1.46/
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'Host: pgadmin.kub.local' http://192.168.1.46/
```

The first is a 302 straight to `https://sonarr.kub.internal/` — one hop, not two. The second is a
302 to `https://ausente.kub.internal/`. The third is a 302 to `http://pgadmin.kub.internal/`, not to
https: `pgadmin` has no `websecure` router, and neither has `prometheus`. Count the hops with
`curl -sSL -o /dev/null -w '%{num_redirects}'` if a browser behaves differently.

**One sign-in per mechanism**

| App | Mechanism | What passing looks like |
|---|---|---|
| `sonarr.kub.internal` | forward-auth | the authentik login, then Sonarr |
| `grafana.kub.internal` | native OIDC | redirect to authentik and back, no `redirect_uri` error |
| `vikunja.kub.internal` | native OIDC | same |
| `radarr.kub.internal` | native OIDC | same, and the pod is Ready (probe `Host`) |
| `prometheus.kub.internal` | basic auth | 401 without the password, page with it |
| `pgadmin.kub.internal` | no gate | the login page over **http** |

**The phone [operator], tunnel off**

`https://grafana.kub.internal` opens with no certificate warning, and no timeout. If it times out,
read Task 1's record: the phone is asking a resolver that does not know the zone, which is card 894,
not a defect in this change.

**The cluster's own resolvers**

```bash
for h in nuc0 nas streaming rbp2; do ssh "$h" 'dig +short grafana.kub.internal'; done
```

Each answers `192.168.1.46`. If `nuc0` answers nothing and its `/etc/resolv.conf` names a public
resolver, run the resolver role against it before recording the result. The role has no tag in
`playbooks/site.yml`, so a temporary playbook is the narrow way to run one role on one host:

```bash
cd ansible
cat > /tmp/opencode/resolver.yml <<'YAML'
- hosts: nuc0
  become: true
  roles: [resolver]
YAML
ANSIBLE_ROLES_PATH="$PWD/roles" ansible-playbook -i inventory/hosts.yml /tmp/opencode/resolver.yml
```

Then re-read `/etc/resolv.conf` on `nuc0` and repeat the `dig`.

**The suite**

```bash
hack/check-charts.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
hack/check-exposure.sh
hack/check-docs.sh --base origin/develop
hack/check-app-version.sh
hack/check-config-keys.sh
```

Zero failures. Any output that is not zero stops the handover.

---

## Task 14 — The cleanup card

The soak is 30 days, so cleanup is its own change and must not depend on anyone remembering.

| Field | Value |
|---|---|
| Project | `homelab` |
| Title | Remove the `*.kub.local` redirect and the old wildcard once the soak ends |
| Kind label | `task` |
| Priority | 2 |
| Bucket | `Backlog` |
| Status | blocked by this card until the soak ends |

Description (Markdown, under 12 lines):

```markdown
Docs: docs/decisions/2026-11-27-internal-zone-is-kub-internal.md

The 30-day soak for the `kub.internal` rename ends here. Read the Pi-hole query log on both Pis and
the Traefik access log in Loki for the 30 days: no query for one of the 23 old names and no redirect
hit from a real client. A host still appearing is migrated, or the soak is extended.

Then remove, in this order: the wildcard redirect template in `traefik-gate` (bump the chart), the
`address=/kub.local/192.168.1.46` wildcard from the Pi-hole file, and `DNS:*.kub.local` from the leaf
SAN — then reissue and replace the Secret.

Keep, because they are the PIs' own names and not the cluster's: the per-host
`address=/pihole-rbp*.kub.local/...` and `address=/vpn-rbp*.kub.local/...` records, the
`address=/rbp*.local` and `address=/livebox.local` records, and the CoreDNS `forward kub.local`
line that lets a pod resolve those names. Removing the forward would break the Pi-hole exporter.

Verification: `dig +short anything.kub.local @192.168.1.22` answers nothing while
`dig +short pihole-rbp0.kub.local @192.168.1.22` still answers `192.168.1.22`; the suite and
`check-exposure.sh` pass; `openssl s_client` shows the leaf with one wildcard.
```

The card's relation is `blocked` by this change's card (894 stays open for the phone resolver; it is
a different subject).

---

## Handover checklist

- [ ] The redirect is applied **before** any chart is renamed, and the ordering is visible in the
      commit history.
- [ ] Every one of the 23 names answers from both Pis and serves the new SAN.
- [ ] `pgadmin` and `prometheus` still work over plain HTTP, including through the redirect.
- [ ] One sign-in per mechanism passes (forward-auth, native OIDC, basic auth, no gate).
- [ ] The phone's state is reported as measured, not assumed: fixed, or unverified with card 894.
- [ ] `hack/check-charts.sh`, the full `hack/tests` suite, `check-exposure.sh`, `check-docs.sh`,
      `check-app-version.sh` and `check-config-keys.sh` all exit 0.
- [ ] The cleanup card exists with a date and a blocking relation.
- [ ] The PR body names every outside consumer the operator confirmed, and every one they could not.
