# Network state collector — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only shell collector that prints the current configuration of the two MikroTik switches, plus what can be read of the Livebox without a credential, as one Markdown page a person can commit and an agent can read.

**Architecture:** `ansible/collectors/network-audit.sh` mirrors `host-audit.sh`: no arguments, Markdown on stdout, nothing written anywhere. It reaches each switch over SSH with a key as a RouterOS `read` user, runs `/export` and a short status set, passes every captured line through a RouterOS-specific redactor, and prints one page. A fixture mode (`NET_AUDIT_FROM_FIXTURE`) renders the whole page with no device, which is what the tests use.

**Tech Stack:** Bash 5, awk, `ssh`, `curl`, Python 3 `unittest` via `pytest`.

**Spec:** [`docs/superpowers/specs/2026-11-24-network-state-collector-design.md`](../specs/2026-11-24-network-state-collector-design.md)

## Global Constraints

- Nothing is written to a device. The RouterOS account carries `policy=read,ssh` (SSH access is a RouterOS policy of its own); the script never passes `show-sensitive` and never omits `hide-sensitive` on RouterOS 6.
- No secret value may reach stdout. The page is committed to git, so a leak is permanent.
- The script writes no file: no temp file, no log, no cache.
- Markdown only on stdout; diagnostics go to stderr.
- Exit `0` when at least one MikroTik answered, `1` when none did, `2` on a usage error.
- Every SSH call is bounded: `ConnectTimeout=$NET_AUDIT_CONNECT_TIMEOUT` (default 5), `NET_AUDIT_CMD_TIMEOUT` (default 20), `BatchMode=yes`.
- Commit messages use a gitmoji prefix, per `AGENTS.md` §4 (`🚀 feature:`, `🐛 fix:`, `📝 docs:`, `🔧 chore:`).
- The full suite must pass before any completion claim: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`.

## Review Focus

Inputs the spec implies but no happy-path test covers. Each has a test in the task that owns the code.

1. **A RouterOS 6 device.** `/export` on v6 shows sensitive values by default; the script must add `hide-sensitive` there, never `terse`. Wrong guess = a committed page with Wi-Fi keys. (Task 3.)
2. **A quoted value containing spaces.** `comment="uplink to the NAS"` must survive redaction intact, and a sensitive value in quotes must lose the whole quoted string, not just its first word. (Task 2.)
3. **A device that accepts the connection and rejects the key.** It must be reported as unreachable, not hang, not loop, and the run must still print a page. (Task 3.)
4. **An export larger than a terminal screen.** Thousands of lines must reach the page whole; no `head` cut, no truncation mid-line. (Task 4.)
5. **A sensitive attribute that is not the first `key=value` on its line.** `... private-key="k" listen-port=51820` must lose the key and keep the port. (Task 2.)

---

### Task 1: Script skeleton — usage, targets, configuration

**Files:**
- Create: `ansible/collectors/network-audit.sh`
- Create: `hack/tests/test_network_audit.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the executable script, `SCRIPT_VERSION="2026-11-24.1"`, and these environment variables read once at the top: `NET_AUDIT_TARGETS` (default `crs326=192.168.1.11 crs309=192.168.1.43`), `NET_AUDIT_USER` (default `audit`), `NET_AUDIT_KEY` (default `$HOME/.ssh/network-audit`), `NET_AUDIT_DATE` (default `date +%F`), `NET_AUDIT_FROM_FIXTURE` (default empty), `NET_AUDIT_CONNECT_TIMEOUT` (default 5), `NET_AUDIT_CMD_TIMEOUT` (default 20), `LIVEBOX_HOST` (default `192.168.1.1`). Mode flags: `--help`, `--list-targets`, `--redact-routeros` (added in Task 2).

- [ ] **Step 1: Write the failing tests**

```python
"""The network collector's contract: read-only, redacted, testable without a device."""

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ansible" / "collectors" / "network-audit.sh"


def run(args=(), env=None, stdin=None):
    merged = {k: v for k, v in os.environ.items() if not k.startswith("NET_AUDIT_")}
    merged.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin, text=True, capture_output=True, env=merged,
    )


class UsageTests(unittest.TestCase):
    def test_the_script_parses(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)

    def test_help_names_the_read_only_contract(self):
        done = run(["--help"])
        self.assertEqual(done.returncode, 0)
        self.assertIn("read-only", done.stdout)
        self.assertIn("NET_AUDIT_FROM_FIXTURE", done.stdout)

    def test_list_targets_prints_the_defaults(self):
        done = run(["--list-targets"])
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.split(), ["crs326", "192.168.1.11", "crs309", "192.168.1.43"])

    def test_list_targets_honours_the_override(self):
        done = run(["--list-targets"], env={"NET_AUDIT_TARGETS": "only=203.0.113.9"})
        self.assertEqual(done.stdout.split(), ["only", "203.0.113.9"])

    def test_an_unknown_flag_is_a_usage_error(self):
        self.assertEqual(run(["--nope"]).returncode, 2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py`
Expected: FAIL — `ansible/collectors/network-audit.sh` does not exist.

- [ ] **Step 3: Create the script**

Header comment lines 1–20 carry the usage text, printed verbatim by `--help` (`sed -n '2,20p' "${BASH_SOURCE[0]}"`), in the style of `host-audit.sh`. It must state: read-only, nothing written, the RouterOS `read` account, the fixture variable, and that the Livebox half is partly remembered.

```bash
#!/usr/bin/env bash
# network-audit.sh — read-only configuration dump of the network hardware, printed as Markdown.
#
#   ./network-audit.sh > /tmp/opencode/network-audit.md
#   NET_AUDIT_FROM_FIXTURE=hack/fixtures/network ./network-audit.sh   # no device touched
#   ./network-audit.sh --list-targets
#   ./network-audit.sh --redact-routeros < some-export
#   ./network-audit.sh --help
#
# Reads only: the account used on the MikroTiks carries the RouterOS `read` policy, every command
# is a `print` or an `/export`, and no file is written anywhere.
set -uo pipefail

SCRIPT_VERSION="2026-11-24.1"
NET_AUDIT_TARGETS="${NET_AUDIT_TARGETS:-crs326=192.168.1.11 crs309=192.168.1.43}"
NET_AUDIT_USER="${NET_AUDIT_USER:-audit}"
NET_AUDIT_KEY="${NET_AUDIT_KEY:-$HOME/.ssh/network-audit}"
NET_AUDIT_DATE="${NET_AUDIT_DATE:-$(date +%F)}"
NET_AUDIT_FROM_FIXTURE="${NET_AUDIT_FROM_FIXTURE:-}"
NET_AUDIT_CONNECT_TIMEOUT="${NET_AUDIT_CONNECT_TIMEOUT:-5}"
NET_AUDIT_CMD_TIMEOUT="${NET_AUDIT_CMD_TIMEOUT:-20}"
LIVEBOX_HOST="${LIVEBOX_HOST:-192.168.1.1}"

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0; }
die() { printf 'network-audit.sh: %s\n' "$1" >&2; exit 2; }

# One "name host" pair per line, from NET_AUDIT_TARGETS.
targets() {
  local pair name host
  for pair in $NET_AUDIT_TARGETS; do
    name="${pair%%=*}"; host="${pair#*=}"
    [ "$name" != "$host" ] || die "target '$pair' is not name=host"
    printf '%s %s\n' "$name" "$host"
  done
}

case "${1:-}" in
  --help|-h) usage ;;
  --list-targets) targets; exit 0 ;;
  "") ;;
  *) die "unknown option '$1' (try --help)" ;;
esac
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
chmod +x ansible/collectors/network-audit.sh
git add ansible/collectors/network-audit.sh hack/tests/test_network_audit.py
git commit -m "🚀 feature: a read-only network collector, skeleton and targets"
```

---

### Task 2: The RouterOS redactor

**Files:**
- Modify: `ansible/collectors/network-audit.sh` (add `redact_routeros`, `--redact-routeros`)
- Modify: `hack/tests/test_network_audit.py`

**Interfaces:**
- Consumes: Task 1's script and test harness (`run()`).
- Produces: `redact_routeros()` — stdin to stdout, one line in, one line out; and the `--redact-routeros` mode that calls it and exits 0. Every later task pipes captured text through it.

- [ ] **Step 1: Write the failing tests**

```python
class RouterOSRedactionTests(unittest.TestCase):
    def redact(self, text):
        done = run(["--redact-routeros"], stdin=text)
        self.assertEqual(done.returncode, 0)
        return done.stdout

    def test_wifi_keys_are_removed_wherever_they_sit(self):
        out = self.redact(
            '/interface wireless security-profiles set [ find default=yes ] '
            'authentication-types=wpa2-psk mode=dynamic-keys wpa2-pre-shared-key="hunter2" '
            'wpa-pre-shared-key=oldkey\n'
        )
        self.assertNotIn("hunter2", out)
        self.assertNotIn("oldkey", out)
        self.assertIn("wpa2-pre-shared-key=<redacted>", out)
        self.assertIn("wpa-pre-shared-key=<redacted>", out)

    def test_a_secret_that_is_not_the_first_pair_is_removed(self):
        out = self.redact('/interface wireguard set wg1 private-key="k3y" listen-port=51820\n')
        self.assertNotIn("k3y", out)
        self.assertIn("private-key=<redacted>", out)
        self.assertIn("listen-port=51820", out)

    def test_a_password_mid_line_is_removed_and_the_policy_kept(self):
        out = self.redact('/user set [ find name=audit ] password="p4ss word" policy=read\n')
        self.assertNotIn("p4ss word", out)
        self.assertIn("password=<redacted>", out)
        self.assertIn("policy=read", out)

    def test_snmp_community_is_removed(self):
        out = self.redact('/snmp community set [ find default=yes ] name=public community=s3cr3t\n')
        self.assertNotIn("s3cr3t", out)

    def test_non_secrets_survive(self):
        line = ('/interface wireguard peers add public-key="AAAAB3NzaC1yc2E=" allowed-address=10.8.0.2/32 '
                'comment="uplink to the NAS" name=peer1\n')
        out = self.redact(line)
        self.assertIn('public-key="AAAAB3NzaC1yc2E="', out)
        self.assertIn('comment="uplink to the NAS"', out)
        self.assertIn("name=peer1", out)

    def test_a_dynamic_keys_mode_survives_after_a_psk(self):
        out = self.redact("authentication-types=wpa2-psk mode=dynamic-keys\n")
        self.assertIn("mode=dynamic-keys", out)

    def test_addresses_and_ports_survive(self):
        line = "/ip address add address=192.168.1.11/24 interface=bridge comment=mgmt\n"
        self.assertEqual(self.redact(line), line)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py -k RouterOS`
Expected: FAIL — `--redact-routeros` is an unknown option, exit 2.

- [ ] **Step 3: Implement `redact_routeros`**

Add the mode to the `case` block (`--redact-routeros) redact_routeros; exit 0 ;;`) and the function above it. The algorithm is the part the tests do not determine, so it is given here:

```awk
# A RouterOS line is a command followed by name=value pairs, some quoted and containing spaces.
# A key is sensitive when, with - turned to _, it ends in one of the secret words or in _key, and
# the match starts the key or follows an underscore; public_key is the one exemption, because a
# peer's public key is not a secret. The whole value, quotes included, becomes <redacted>.
function sensitive(k,   t, i) {
  t = tolower(k); gsub(/-/, "_", t)
  if (t == "public_key") return 0
  i = match(t, /(password|passwd|secret|passphrase|psk|token|community|key)$/)
  if (!i) return 0
  return i == 1 || substr(t, i - 1, 1) == "_"
}
{
  out = ""; line = $0
  while (match(line, /[A-Za-z0-9._-]+=/)) {
    key = substr(line, RSTART, RLENGTH - 1)
    out = out substr(line, 1, RSTART - 1) key "="
    line = substr(line, RSTART + RLENGTH)
    if (sensitive(key)) {
      if (substr(line, 1, 1) == "\"") {
        if (match(line, /^"[^"]*"/)) line = substr(line, RLENGTH + 1)
        else line = ""
      } else {
        sub(/^[^[:space:]]+/, "", line)
      }
      out = out "<redacted>"
    }
  }
  print out line
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add ansible/collectors/network-audit.sh hack/tests/test_network_audit.py
git commit -m "🚀 feature: a redactor written for RouterOS attributes"
```

---

### Task 3: Fetching — SSH, fixture mode, version detection, unreachable devices

**Files:**
- Modify: `ansible/collectors/network-audit.sh`
- Modify: `hack/tests/test_network_audit.py`
- Create: `hack/fixtures/network/crs326.resource.txt`, `crs326.routerboard.txt`, `crs326.package.txt`, `crs326.identity.txt`, `crs326.ethernet.txt`, `crs326.address.txt`, `crs326.route.txt`, `crs326.export.txt` (same eight for `crs309`)

**Interfaces:**
- Consumes: `redact_routeros` from Task 2.
- Produces:
  - `ros <host> <routeros-command…>` — prints the command's output on stdout, returns the ssh exit status; it never prints a password.
  - `host_of <device>` — prints the target's address, returns 1 for an unknown name.
  - `fetch <device> <slug>` — prints the redacted output for that slug, or the single line `(unreachable: <reason>)`; slugs are `resource routerboard package identity ethernet address route export`.
  - `slug_command <slug> <major>` — the RouterOS command for that slug; `export` is `/export hide-sensitive` on major `6`, `/export terse` on `7`, and nothing at all for any other value (it returns non-zero, so `fetch` fails closed rather than guessing).
  - `routeros_major <device>` — prints `6` or `7` from the `resource` block, empty when unknown.
  - Internal modes, documented in the header as "for tests": `--dump <device> <slug>`, `--major <device>`, `--export-command <major>`.
  - Fixture layout: `$NET_AUDIT_FROM_FIXTURE/<device>.<slug>.txt`.

- [ ] **Step 1: Create the fixture files**

`hack/fixtures/network/crs326.resource.txt`:

```
                   uptime: 3w2d1h
                  version: 7.16.1 (stable)
              free-memory: 201.3MiB
             total-memory: 256.0MiB
                      cpu: MIPS 98Kc
                cpu-count: 2
           architecture-name: mipsbe
                  board-name: CRS326-24G-2S+
                    platform: MikroTik
```

`crs326.identity.txt`: `                   name: CR326`
`crs326.routerboard.txt`:

```
       routerboard: yes
             model: CRS326-24G-2S+
     serial-number: HE908ABCD
     firmware-type: ar7240
  current-firmware: 7.16.1
```

`crs326.package.txt`: a three-line `routeros` package block naming `7.16.1`.
`crs326.ethernet.txt`: five ports in the shape `/interface ethernet monitor [find] once` prints — one `key: value` block per port, blank line between blocks, `name:`, `status:` and `rate:` — with one `status: no-link` port that reports no rate.
`crs326.address.txt`: `address=192.168.1.11/24 interface=bridge network=192.168.1.0`.
`crs326.route.txt`: two lines, one static `0.0.0.0/0` via `192.168.1.1`.
`crs326.export.txt`: an export with a Wi-Fi profile carrying `wpa2-pre-shared-key="fixture-psk-not-real"`, a wireguard peer with `public-key="AAAAB3NzaC1yc2E="` and `preshared-key="fixture-wg-psk"`, a bridge VLAN table line, and as its **last** line
`/ip route add dst-address=10.0.0.0/8 gateway=192.168.1.254 comment="fixture-last-line"` — the marker Task 4 uses to prove nothing was truncated. Both fixture secrets are asserted absent from the rendered page in Task 4.

`crs309.*` mirrors them with `version: 6.49.10`, `name: MikroTik`, `board-name: CRS309-1G-8S+`, and an export that uses v6 attribute spelling.

- [ ] **Step 2: Write the failing tests**

```python
FIXTURE = ROOT / "hack" / "fixtures" / "network"


class FetchTests(unittest.TestCase):
    def env(self, **extra):
        merged = {"NET_AUDIT_FROM_FIXTURE": str(FIXTURE)}
        merged.update(extra)
        return merged

    def test_a_fixture_is_read_for_a_known_slug(self):
        done = run(["--dump", "crs326", "identity"], env=self.env())
        self.assertEqual(done.returncode, 0)
        self.assertIn("CR326", done.stdout)

    def test_a_missing_fixture_is_reported_as_unreachable(self):
        done = run(["--dump", "ghost", "identity"], env=self.env())
        self.assertEqual(done.returncode, 0)
        self.assertIn("(unreachable:", done.stdout)

    def test_the_routeros_major_is_read_from_the_resource_block(self):
        self.assertEqual(run(["--major", "crs326"], env=self.env()).stdout.strip(), "7")
        self.assertEqual(run(["--major", "crs309"], env=self.env()).stdout.strip(), "6")

    def test_the_export_flag_follows_the_routeros_major(self):
        six = run(["--export-command", "6"])
        self.assertEqual(six.returncode, 0)
        self.assertEqual(six.stdout.strip(), "/export hide-sensitive")
        seven = run(["--export-command", "7"])
        self.assertEqual(seven.returncode, 0)
        self.assertEqual(seven.stdout.strip(), "/export terse")

    def test_an_unknown_major_sends_no_export_at_all(self):
        # An unreadable version fails closed: no export is sent, so a RouterOS 6 device can
        # never receive `/export terse`. Ruling R9 in the SDD ledger.
        done = run(["--export-command", "unknown"])
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")
        self.assertIn("export", done.stderr)

    def test_an_export_with_no_readable_version_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "crs326.export.txt").write_text(
                '/ip route add dst-address=10.0.0.0/8 gateway=192.168.1.254\n'
            )
            done = run(["--dump", "crs326", "export"],
                       env=self.env(NET_AUDIT_FROM_FIXTURE=str(root)))
        self.assertEqual(done.returncode, 0)
        self.assertIn("(unreachable: RouterOS version unknown, export skipped)", done.stdout)
        self.assertNotIn("10.0.0.0/8", done.stdout)

    def test_a_device_that_refuses_the_key_is_unreachable_and_fast(self):
        done = run(
            ["--dump", "ghost", "identity"],
            env={"NET_AUDIT_TARGETS": "ghost=203.0.113.9", "NET_AUDIT_CONNECT_TIMEOUT": "1",
                 "NET_AUDIT_CMD_TIMEOUT": "2"},
        )
        self.assertIn("(unreachable:", done.stdout)
```

`--dump <device> <slug>` and `--major <device>` are internal modes used by the tests and by nothing else; document them in the header as "for tests". They keep the fetch layer testable without asserting on the whole page.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py -k Fetch`
Expected: FAIL — `--dump` unknown.

- [ ] **Step 4: Implement the fetch layer**

```bash
ros() {                                  # ros <host> <routeros command...>
  local host="$1"; shift
  timeout "$NET_AUDIT_CMD_TIMEOUT" ssh \
    -i "$NET_AUDIT_KEY" -o BatchMode=yes \
    -o ConnectTimeout="$NET_AUDIT_CONNECT_TIMEOUT" \
    -o StrictHostKeyChecking=accept-new \
    "$NET_AUDIT_USER@$host" "$*" 2>&1
}

slug_command() {                         # slug_command <slug> <major>
  case "$1" in
    resource)    printf '/system resource print' ;;
    routerboard) printf '/system routerboard print' ;;
    package)     printf '/system package print' ;;
    identity)    printf '/system identity print' ;;
    ethernet)    printf '/interface ethernet monitor [find] once' ;;
    address)     printf '/ip address print' ;;
    route)       printf '/ip route print' ;;
    export)      case "$2" in
                   6) printf '/export hide-sensitive' ;;
                   7) printf '/export terse' ;;
                   *) return 1 ;;
                 esac ;;
    *) return 1 ;;
  esac
}

fetch() {                                # fetch <device> <slug>
  local device="$1" slug="$2" host major cmd rc out
  host="$(host_of "$device")" || { printf '(unreachable: no such target)\n'; return 0; }
  # The export command follows the major, and an unknown major fails closed: `/export terse`
  # against a RouterOS 6 device prints its Wi-Fi keys and the SNMP community in clear, so when
  # the version cannot be read no export is sent at all and the page says why.
  major="$(routeros_major "$device")"
  if ! cmd="$(slug_command "$slug" "$major")"; then
    if [ "$slug" = "export" ]; then
      printf '(unreachable: RouterOS version unknown, export skipped)\n'
    else
      printf '(unreachable: unknown slug %s)\n' "$slug"
    fi
    return 0
  fi
  if [ -n "$NET_AUDIT_FROM_FIXTURE" ]; then
    if [ -f "$NET_AUDIT_FROM_FIXTURE/$device.$slug.txt" ]; then
      redact_routeros < "$NET_AUDIT_FROM_FIXTURE/$device.$slug.txt"; return 0
    fi
    printf '(unreachable: no fixture for %s.%s)\n' "$device" "$slug"; return 0
  fi
  out="$(ros "$host" "$cmd")"; rc=$?
  if [ "$rc" -ne 0 ] || printf '%s' "$out" | grep -q '^Bad command name\|^failure:\|Permission denied\|Connection refused\|Connection timed out\|No route to host'; then
    printf '(unreachable: %s)\n' "$(printf '%s' "$out" | head -1)"
    return 0
  fi
  printf '%s\n' "$out" | redact_routeros
}

routeros_major() {                       # routeros_major <device> → 6, 7, or empty
  local device="$1" host
  host="$(host_of "$device")" || return 0
  if [ -n "$NET_AUDIT_FROM_FIXTURE" ]; then
    awk '/^ *version:/{print $2; exit}' "$NET_AUDIT_FROM_FIXTURE/$device.resource.txt" 2>/dev/null | cut -d. -f1
    return 0
  fi
  ros "$host" "/system resource print" | awk '/^ *version:/{print $2; exit}' | cut -d. -f1
}
```

`routeros_major` calls `ros` and the fixture file directly, never `fetch`, so the two cannot recurse.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py`
Expected: every test in the file passes (27 at the time of writing; fix rounds add tests, so read the result, not the count).

- [ ] **Step 6: Commit**

```bash
git add ansible/collectors/network-audit.sh hack/tests/test_network_audit.py hack/fixtures/network
git commit -m "🚀 feature: fetch RouterOS state over ssh, with a fixture mode"
```

---

### Task 4: The page — banner, device sections, status, export

**Files:**
- Modify: `ansible/collectors/network-audit.sh`
- Modify: `hack/tests/test_network_audit.py`

**Interfaces:**
- Consumes: `fetch`, `routeros_major`, `targets`.
- Produces: the default (no-argument) run prints the whole page; the pieces are `page_header`, `page_device <device> <host>`, `page_livebox`, `page_footer`, plus `section <title>` and `kv <key> <value>` copied from `host-audit.sh` (`section` prints `\n## <title>\n\n`, `kv` prints `<key>: <value>`). Task 5 adds the Livebox body only.

- [ ] **Step 1: Write the failing tests**

```python
import importlib.util

spec = importlib.util.spec_from_file_location("check_docs", ROOT / "hack" / "check_docs.py")
check_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_docs)


class PageTests(unittest.TestCase):
    def page(self):
        done = run(env={"NET_AUDIT_FROM_FIXTURE": str(FIXTURE), "NET_AUDIT_DATE": "2026-11-24"})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_the_banner_passes_the_docs_checker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "network-2026-11-24.md").write_text(self.page())
            self.assertEqual(check_docs.banner_problems([root / "docs/network-2026-11-24.md"], root), [])

    def test_the_page_carries_no_secret_from_the_fixtures(self):
        page = self.page()
        self.assertNotIn("fixture-psk-not-real", page)
        self.assertNotIn("fixture-wg-psk", page)
        self.assertIn("AAAAB3NzaC1yc2E=", page)

    def test_a_section_per_device_with_its_identity(self):
        page = self.page()
        self.assertIn("## crs326 — 192.168.1.11", page)
        self.assertIn("## crs309 — 192.168.1.43", page)
        self.assertIn("CR326", page)
        self.assertIn("7.16.1", page)

    def test_a_large_export_is_not_truncated(self):
        page = self.page()
        self.assertIn("/ip address add address=192.168.1.11/24", page)
        self.assertIn("fixture-last-line", page)
        self.assertNotIn("(timed out", page)

    def test_the_page_says_what_it_does_not_cover(self):
        self.assertIn("## What this record does not cover", self.page())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py -k Page`
Expected: FAIL — the default run prints nothing.

- [ ] **Step 3: Implement the page**

Layout, printed by `page_header` then one `page_device` per target then `page_livebox` then `page_footer`:

````markdown
# Audit — the network hardware, measured

> **In one line.** What the two switches are configured to do and what the Livebox answers,
> read from the devices on 2026-11-24 rather than from the pages that describe them.
>
> **Use it when** you need the real state of a port, a VLAN, a firewall filter or the WAN edge ·
> **Verified** 2026-11-24 · **Kind:** record
>
> **Sources** `ansible/collectors/network-audit.sh` (version 2026-11-24.1), run read-only as the
> RouterOS `audit` user; the Livebox section says which of its lines were measured.

## How it was read

- CRS326 `192.168.1.11` and CRS309 `192.168.1.43`: SSH as a RouterOS user with the `read` policy,
  `/export` and a status set, every line through the collector's redactor.
- Livebox `192.168.1.1`: the login page only; no credential was used.

## crs326 — 192.168.1.11

### Status

```
identity: CR326
model: CRS326-24G-2S+
serial: HE908ABCD
routeros: 7.16.1
uptime: 3w2d1h
```

### Ethernet

| port | status | speed |
|---|---|---|
| ether1 | link-ok | 1Gbps |

### Configuration

```
/export text
```

## What this record does not cover
````

`page_device` reads identity, model, serial, RouterOS version and uptime out of the `resource`,
`routerboard` and `identity` blocks with `awk`; the Ethernet table is built from the `ethernet`
block's `name:`, `status:` and `rate:` fields, one row per port, `unknown` for a field a port does
not report. The export block is printed whole, with no `head`.

This task's default run ends with `page_footer` and does **not** call `page_livebox`; Task 5 inserts
that call together with the function. The exit-code test for a run with nothing reachable also
belongs to Task 5, because it counts the Livebox's failure line.

The spec's verification step 3 asked for a render compared against a checked-in expected page. This
plan asserts structure and hands the page to `hack/check_docs.py` instead, because the banner carries
`SCRIPT_VERSION` and a golden file would fail on every version bump while proving nothing about
truncation. Say so in the pull request.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py`
Expected: every test in the file passes.

- [ ] **Step 5: Commit**

```bash
git add ansible/collectors/network-audit.sh hack/tests/test_network_audit.py
git commit -m "🚀 feature: the network page, banner and per-device sections"
```

---

### Task 5: The Livebox section

**Files:**
- Modify: `ansible/collectors/network-audit.sh`
- Modify: `hack/tests/test_network_audit.py`
- Create: `hack/fixtures/network/livebox-root.html`

**Interfaces:**
- Consumes: `LIVEBOX_HOST`, `NET_AUDIT_FROM_FIXTURE`.
- Produces: `page_livebox` prints the measured facts, then the remembered sentence, then the label.

- [ ] **Step 1: Create the fixture**

`hack/fixtures/network/livebox-root.html` — the real page's first 40 lines, enough to carry
`<title data-translation="common.headtitlelogin">` and `loader.js?sah=v1.0.161_20260923`.

- [ ] **Step 2: Write the failing tests**

```python
class LiveboxTests(unittest.TestCase):
    def page(self):
        return run(env={"NET_AUDIT_FROM_FIXTURE": str(FIXTURE)}).stdout

    def test_the_firmware_build_is_read_from_the_login_page(self):
        self.assertIn("v1.0.161_20260923", self.page())

    def test_the_remembered_half_is_labelled(self):
        page = self.page()
        self.assertIn("not read from the device", page)
        self.assertIn("192.168.1.46", page)

    def test_an_unreachable_livebox_still_prints_a_page(self):
        done = run(env={"NET_AUDIT_FROM_FIXTURE": "/nonexistent"})
        self.assertIn("(unreachable:", done.stdout)
        self.assertIn("## What this record does not cover", done.stdout)

    def test_a_run_with_no_reachable_device_exits_1(self):
        done = run(env={"NET_AUDIT_TARGETS": "ghost=203.0.113.9",
                        "NET_AUDIT_FROM_FIXTURE": "/nonexistent"})
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout.count("(unreachable:"), 2)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py -k Livebox`
Expected: FAIL — no Livebox section.

- [ ] **Step 4: Implement `page_livebox`**

Call it from the default run, between the last device section and `page_footer`. The function below
starts the section; the remembered block is printed after it.

```bash
page_livebox() {
  local body build status out rc reason
  if [ -n "$NET_AUDIT_FROM_FIXTURE" ]; then
    if [ -f "$NET_AUDIT_FROM_FIXTURE/livebox-root.html" ]; then
      # The fixture branch contacts nothing, so it has no status to report. Saying so is the point:
      # a 200 printed here would be a guess wearing the same "http:" label as a measured one.
      body="$(cat "$NET_AUDIT_FROM_FIXTURE/livebox-root.html")"
      status="not measured (fixture)"
    else
      body=""; status="unreachable: no fixture for livebox-root.html"
    fi
  else
    # One unauthenticated GET, bounded. `curl -sS` exits 0 for any HTTP status, so the exit code
    # cannot stand in for the answer; `-w '\n%{http_code}'` appends the real code, the shell strips
    # the final newline, and the last line is the code with the body above it. On a failure the
    # captured text is curl's own message, so its first line is the reason.
    out="$(curl -sS -m 6 -w '\n%{http_code}' "http://$LIVEBOX_HOST/" 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
      reason="$(printf '%s\n' "$out" | head -1)"; body=""
      status="unreachable: ${reason:-curl exit $rc}"
    else
      status="$(printf '%s\n' "$out" | tail -1)"
      body="$(printf '%s\n' "$out" | sed '$d')"
    fi
  fi
  # The whole `sah=` value, printed only when it is a well-formed version: a match that stopped at
  # a hyphen would print `v1.0.161` from `v1.0.161-20260923`, a plausible number the box never ran.
  build="$(printf '%s' "$body" | grep -o 'sah=[^"&<>[:space:]]*' | head -1 | cut -d= -f2)"
  if ! printf '%s\n' "$build" | grep -Eq '^v[0-9]+([._][0-9]+)*$'; then build=""; fi
  section "Livebox — $LIVEBOX_HOST"
  case "$status" in
    unreachable:*) printf '(%s)\n\n' "$status" ;;
    *) printf '```\n'; kv http "$status"; kv firmware "${build:-unknown}"; printf '```\n\n' ;;
  esac
  # then the remembered block below
}
```

The section prints: the measured HTTP answer — or `not measured (fixture)` when the page came from a file — the build string (or `unknown`), then

```markdown
### Remembered, not read from the device

`docs/how-it-works/network.md`, verified 2026-11-22: the box forwards 80, 443, 25565, 22000
(TCP and UDP) and 21027 (UDP) to `192.168.1.46`. This half is **not read from the device**;
the port-forward table itself needs the admin password and is a separate piece of work.
```

The shipped tests also cover the branch that touches the box, because that is where a hardcoded
status hides: a stub `curl` placed first on `PATH` returns a body plus a code, and the tests pin a
`200`, a non-`200`, a failed curl carrying its own message, and a page with no `sah=` value.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_network_audit.py`
Expected: every test in the file passes.

- [ ] **Step 6: Commit**

```bash
git add ansible/collectors/network-audit.sh hack/tests/test_network_audit.py hack/fixtures/network/livebox-root.html
git commit -m "🚀 feature: the Livebox section, measured and remembered"
```

---

### Task 6: The README section

**Files:**
- Modify: `ansible/README.MD`

**Interfaces:**
- Consumes: the finished script's usage text.
- Produces: nothing code-facing.

- [ ] **Step 1: Choose where the section goes**

Read `ansible/README.MD`. If it already carries a "Seeing a host without touching it" section, the
new section goes directly after it. If it does not — this branch is cut from `origin/develop`, where
the sibling host collector has not landed — append the section at the end of the file. If the file
contains the sentence "the one thing in this directory that works today", reword it to name both
collectors without the exclusivity; if it does not, there is nothing to reword.

- [ ] **Step 2: Add the subsection**

After the host-audit subsection, before the next `##`:

````markdown
## Seeing the network hardware without touching it

`collectors/network-audit.sh` reads the two MikroTik switches and prints one Markdown page: each
switch's own `/export`, its model, serial, RouterOS version, uptime and per-port link state, then
what the Livebox answers on its login page. It writes nothing and changes nothing.

It signs in with a key, as a RouterOS user that can sign in, print and export, and change nothing
else — the `read` policy plus the `ssh` one, because RouterOS counts SSH access as a policy of its own.

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/network-audit
scp ~/.ssh/network-audit.pub admin@192.168.1.11:
ssh admin@192.168.1.11
```

Then in that session, once per switch (the import needs the user to exist):

    /user group add name=audit policy=read,ssh
    /user add name=audit group=audit
    /user ssh-keys import public-key-file=network-audit.pub user=audit

Then, from this directory:

```bash
./collectors/network-audit.sh > /tmp/opencode/network-audit.md
NET_AUDIT_FROM_FIXTURE=../hack/fixtures/network ./collectors/network-audit.sh   # no device touched
```

Do not cite a snapshot page from here until one exists: `hack/check_docs.py` resolves every
`docs/*.md` path in the repository and fails on one that is not there yet.
````

- [ ] **Step 3: Run the docs check**

Run: `hack/check-docs.sh --base origin/develop`
Expected: passes, with the staleness report printed and not failing.

- [ ] **Step 4: Commit**

```bash
git add ansible/README.MD
git commit -m "📝 docs: how to read the network hardware"
```

---

### Task 7: Handover and tracking

**Files:** none.

- [ ] **Step 1: Rebase and run everything**

```bash
git fetch origin
git rebase origin/develop
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
bash -n ansible/collectors/network-audit.sh
hack/check-docs.sh --base origin/develop
```

Expected: the suite passes with zero failures; `check-docs.sh` reports no problem.

- [ ] **Step 2: Push and open the pull request**

```bash
git push origin <branch>
```

Open the PR with `github_create_pull_request`, base `develop`, body stating: what the collector reads, what it cannot (the Livebox's port-forward table, and the first live run, which needs the operator's key), and the verification above.

- [ ] **Step 3: File the cards**

- Collector card: `task`, `priority/medium`, `Doing` while the work runs, then `Validation` with the branch, the PR link and the verification. Body is the five-line `repo / docs / site / branch / pr` block, then what changed, then the verification.
- Livebox card: `task`, `priority/low`, `Backlog`, blocked by nothing, body naming the gap — it needs the admin password, and the SoftAtHome endpoints are unproven on firmware `v1.0.161_20260923`.
- Read both back: creator `bot-opencode`, one `priority/*` label, URLs as `<a href>`, position inside its band.

- [ ] **Step 4: Report the one thing not done**

The first snapshot page (`docs/audit/network-<date>.md`), its `mkdocs.yml` nav row, the `docs/audit/README.md` row and the pointer from `docs/how-it-works/network.md` all wait on the operator's live run. Say so in the report rather than creating a page from fixtures.
