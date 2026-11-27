# Edge VPN failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One WireGuard client config keeps working when either edge Pi dies, with both hosts running the same wg-easy panel behind one address that keepalived moves.

**Architecture:** `rbp0` is the authority. Both hosts run wg-easy on port `51821`; keepalived moves `192.168.1.9` between them over unicast VRRP; `rbp0` snapshots its SQLite database every minute with `sqlite3 .backup` and `rbp1` pulls it through a restricted account, refusing when either side was edited. Each script publishes a textfile metric so a stopped timer or a divergence alerts.

**Tech Stack:** bash (`ansible/pi/hosts-pi.sh`), Ansible (firewall role), systemd units and timers, keepalived 2.x, sqlite3 CLI, Docker Compose, Prometheus node-exporter textfile collector, Grafana unified alerting API.

**Spec:** [`docs/superpowers/specs/2026-11-26-edge-vpn-failover-design.md`](../specs/2026-11-26-edge-vpn-failover-design.md)

## Global Constraints

- `ansible/pi/hosts-pi.sh` is the only thing that puts a Pi back together: no Ansible on a fresh host, so every new file it needs is rendered and written by this script.
- The script never writes a secret: `pihole.env`, `certs/` and the new sync key are required and named when absent, never invented.
- `PI_ROOT` makes every verb work against another directory; every new path goes through `$ROOT`. Every new *script* takes the same seam as `WG_ROOT` (default `/`).
- A file this script owns is written only when its bytes differ (`write_abs`), and `verify` compares the same bytes (`check_text`). No write is unconditional.
- Tunnel port `51821` on both hosts; VIP `192.168.1.9`; VRRP `virtual_router_id 51`, `advert_int 1`, `preempt_delay 300`; `rbp0` MASTER priority 150, `rbp1` BACKUP priority 100.
- VRRP is IP protocol 112 with no port. A rule with `port: 112` would match nothing.
- A published container port is DNAT'd before INPUT: `51821/udp`, `8443/tcp` and `9902/tcp` never reach ufw and get no rule.
- Textfile metrics are timestamps of the last good run, never an age: `0644` files under `/var/lib/node-exporter/textfile`, written temp-then-`mv`.
- The standby never restarts its panel while it holds the VIP.
- `hack/tests/test_pi_script.py` is the gate for all of it; `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests` must end at zero failures.

## Review Focus

- **A client on the LAN dials a Pi's own address** (`192.168.1.22:51821`) and bypasses the pair: it keeps working until that host dies, then never fails over. Only the shared address is supported.
- **The timer runs while the host is the MASTER**: installing a snapshot then restarts wg-easy on the host that is carrying the tunnel.
- **The two databases disagree because someone edited the standby's panel**: the sync must refuse and say which table moved, not overwrite the edit.
- **`sqlite3 .backup` is replaced by a file copy** (by hand or by a later edit): a copy taken mid-write is a torn database, and the standby then serves a corrupt peer set.
- **A stopped timer**: the metric must age from the last good run, so the alert fires; an age written by the script would freeze instead.
- **`ufw show added` echoes the protocol-only rule differently** from the line the script builds: `verify` fails on the first real host and names the difference.

---

### Task 1: One tunnel port for both hosts

**Files:**
- Modify: `ansible/playbooks/templates/runners/docker-compose.yml:103-106`
- Modify: `ansible/pi/hosts-pi.sh:88-96` (`wg_udp_port`), `:151-153` (`desired_compose`), `:846` (render's "never reaches ufw" line)
- Test: `hack/tests/test_pi_script.py:143-160`

**Interfaces:**
- Produces: `WG_UDP_PORT="51821"` in `hosts-pi.sh`, used by task 4's render text and task 8's docs. `wg_udp_port()` is deleted; nothing else may call it.

- [ ] **Step 1: Replace the port tests with one-port assertions**

Replace `test_the_two_hosts_render_the_same_stack_apart_from_the_wireguard_port` and
`test_each_host_gets_its_own_wireguard_port` with:

```python
def test_the_two_hosts_render_the_same_stack():
    """One database, one port, one endpoint: the two hosts must render the same
    bytes, or a config minted on one names something the other does not serve."""
    assert rendered_compose("rbp0") == rendered_compose("rbp1")


def test_both_hosts_publish_the_wireguard_port():
    for host in ("rbp0", "rbp1"):
        block = rendered_compose(host)
        assert '"51821:51821/udp"' in block, block
        assert "51822" not in block, block
        assert "@WG_UDP_PORT@" not in block, block
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k "render_the_same_stack or publish_the_wireguard_port"`
Expected: FAIL — rbp1 renders `51822`.

- [ ] **Step 3: Make the template literal and delete the per-host function**

In `docker-compose.yml`, replace the `ports:` entry with `- "51821:51821/udp"` and keep a comment
saying both hosts answer the same port because one database issues both panels' configs.
In `hosts-pi.sh`, delete `wg_udp_port()` and add `WG_UDP_PORT="51821"` beside `VPN_CIDR`; make
`desired_compose` a plain `cat "$TEMPLATES/docker-compose.yml"`; in `cmd_render`, print
`$WG_UDP_PORT/udp` for the "never reaches ufw" line.

- [ ] **Step 4: Run the whole file's tests**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible/pi/hosts-pi.sh ansible/playbooks/templates/runners/docker-compose.yml hack/tests/test_pi_script.py
git commit -m "🚀 feat(edge): both Pis answer the tunnel on 51821"
```

---

### Task 2: Drop the unused OAuth block and `wg-easy.env`

**Files:**
- Modify: `ansible/playbooks/templates/runners/docker-compose.yml:34-70` (the `OAUTH_*` block and `env_file`)
- Modify: `ansible/pi/hosts-pi.sh:207-224` (`require_env`) and `:744-748` (render's `wg-easy.env` block)
- Test: `hack/tests/test_pi_script.py:192-203`, `:223-228`, `:426-453` (`_pi_fixture`)

**Interfaces:**
- Produces: `require_env` names four files (`pihole.env`, `certs/tls.pem`, `certs/tls-key.pem`, `certs/pihole.pem`). No later task adds a required file to that list; task 6 adds its own check (`sync_key_drift`).

- [ ] **Step 1: Write the failing test**

```python
def test_the_panel_carries_no_oauth_block():
    """The edge-auth decision: no provider exists, sign-in is local, and the
    compose file's next touch is when the unused block goes."""
    block = rendered_compose("rbp0")
    assert "OAUTH" not in block, block
    assert "wg-easy.env" not in block, block
```

and in `test_setup_refuses_without_the_panel_secret_or_its_certificate`, drop the
`wg-easy.env` line from the fixture and from the expected stderr; in `_pi_fixture`, delete the
`(stack / "wg-easy.env").write_text(...)` line.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k "no_oauth_block or refuses_without_the_panel_secret"`
Expected: FAIL — the block is present.

- [ ] **Step 3: Remove the block, the env file and its check**

Delete the five `OAUTH_*` lines and the `env_file:` block from the wg-easy service (leave the
comment that says why there is no auto-launch as a one-line note on `TRUSTED_PROXIES`), delete
`wg-easy.env` from `require_env`'s list and from its message, and replace render's `wg-easy.env`
paragraph with one line saying the panel signs in with its own account.

- [ ] **Step 4: Run the file's tests**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible/pi/hosts-pi.sh ansible/playbooks/templates/runners/docker-compose.yml hack/tests/test_pi_script.py
git commit -m "🔧 chore(edge): drop the panel's unused OIDC block"
```

---

### Task 3: A port-less VRRP rule, in both records

**Files:**
- Modify: `ansible/roles/firewall/vars/main.yml` (`firewall_rules_edge`)
- Modify: `ansible/roles/firewall/tasks/resolve.yml:11-32`, `tasks/main.yml:19-27`
- Modify: `ansible/pi/hosts-pi.sh:531-600` (`firewall_rule_table`, `firewall_source_addresses`, `ufw_rule_line`), `main()`
- Test: `hack/tests/test_pi_script.py:930-1010`

**Interfaces:**
- Consumes: nothing new.
- Produces: a rule shape with no `port` key, `source: peer`; `FIREWALL_HOST` (set by `main()` from the verb's host argument) and the globals `EDGE_ADDRESSES`, `host_address()`, `peer_address()`. `firewall_source_addresses peer` resolves to the other Pi's address only.

- [ ] **Step 1: Write the failing tests**

```python
VRRP_LINE = "ufw allow from %s proto vrrp comment 'VRRP advertisements from the other edge host'"


def test_the_vrrp_rule_names_a_protocol_not_a_port():
    """VRRP is IP protocol 112. `port 112 proto udp` matches nothing, the default
    deny drops every advertisement, and both hosts claim the address."""
    block = rendered_section("rbp0", FIREWALL_HEADER)
    assert VRRP_LINE % "192.168.1.38" in block, block
    assert "192.168.1.22 proto vrrp" not in block, block
    assert "112/udp" not in block, block
    assert VRRP_LINE % "192.168.1.22" in rendered_section("rbp1", FIREWALL_HEADER)


def test_the_vrrp_rule_is_declared_without_a_port():
    rules = yaml.safe_load(
        (REPO / "ansible/roles/firewall/vars/main.yml").read_text()
    )["firewall_rules_edge"]
    vrrp = [rule for rule in rules if rule["proto"] == "vrrp"]
    assert len(vrrp) == 1
    assert "port" not in vrrp[0]
    assert vrrp[0]["source"] == "peer"
```

Extend `expected_firewall_commands()` with `peer` in `sources` (the edge inventory's other address)
and make it emit the port-less form for a rule that has no `port`; extend the ufw stub so a call
without `to any port` is written as `ufw allow from $3 proto $5 comment '$7'`. Change
`test_the_edge_rule_set_opens_piholes_https_port` to `rule.get("port") == 443`, because the new
rule carries no `port` key.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k vrrp`
Expected: FAIL — no such line is rendered.

- [ ] **Step 3: Grow the rule shape in both records**

In `vars/main.yml`, append to `firewall_rules_edge` the one entry with `proto: vrrp`,
`source: peer` and that comment, preceded by a comment explaining 112 is a protocol.
In `resolve.yml`, add `'peer': groups['edge'] | map('extract', hostvars, 'ansible_host') | reject('equalto', ansible_host) | list`
to `sources` and `'port': rule.port | default(none)` to the appended dict; in `tasks/main.yml`, use
`port: "{{ item.port | default(omit, true) }}"` and label
`"{{ item.port | default('proto', true) }}/{{ item.proto }} from {{ item.from }}"` — the `true` is what
makes a rule's `port: None` (from `resolve.yml`) reach ufw as omitted rather than as a port.
In `hosts-pi.sh`: add `EDGE_ADDRESSES="192.168.1.22 192.168.1.38"`, `FIREWALL_HOST=""`,
`host_address()` and `peer_address()`; add the table row `|vrrp|peer|VRRP advertisements from the other edge host`
(empty port field); teach `firewall_source_addresses` the `peer` case; and branch `ufw_rule_line` on an
empty port.

- [ ] **Step 4: Run the file's tests, including the parity test**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS, including `test_the_pi_and_the_ansible_layer_allow_the_same_ports` and the stub test
(`first=20` now: 19 rules, the defaults, the enable — set `FIREWALL_HOST=rbp0` in that test's env).

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/firewall ansible/pi/hosts-pi.sh hack/tests/test_pi_script.py
git commit -m "🚀 feat(edge): open VRRP between the two Pis as a protocol, not a port"
```

---

### Task 4: keepalived, the address, and its state metric

**Files:**
- Create: `ansible/playbooks/templates/runners/keepalived.conf`
- Create: `ansible/playbooks/templates/runners/wg-vrrp-notify.sh`
- Modify: `ansible/pi/hosts-pi.sh` (constants, `desired_keepalived`, `desired_vrrp_notify`, `ensure_keepalived`, `keepalived_drift`, `cmd_render`, `cmd_setup`, `cmd_verify`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: `WG_UDP_PORT`, `host_address()`, `peer_address()` from tasks 1 and 3.
- Produces: `VIP_ADDRESS="192.168.1.9"`, `KEEPALIVED_CONF="/etc/keepalived/keepalived.conf"`, `WG_LIB="/usr/local/lib/wg-vpn"`, `VRRP_STATE_FILE="/run/wg-vpn/vrrp.state"`, `TEXTFILE_DIR="/var/lib/node-exporter/textfile"`, `desired_keepalived <host>`, `desired_vrrp_notify`; `keepalived` and `sqlite3` installed; the state file and the series `wg_vpn_vrrp_master` that tasks 6 and 9 read.

- [ ] **Step 1: Write the failing tests**

```python
KEEPALIVED_HEADER = "--- /etc/keepalived/keepalived.conf\n"


def test_the_master_and_the_backup_differ_only_in_state_and_priority():
    rbp0 = rendered_section("rbp0", KEEPALIVED_HEADER)
    rbp1 = rendered_section("rbp1", KEEPALIVED_HEADER)
    for line, block in (("state MASTER", rbp0), ("priority 150", rbp0),
                        ("unicast_src_ip 192.168.1.22", rbp0),
                        ("state BACKUP", rbp1), ("priority 100", rbp1),
                        ("unicast_src_ip 192.168.1.38", rbp1)):
        assert line in block, (line, block)
    assert "192.168.1.38" in rbp0 and "192.168.1.22" in rbp1  # the unicast peer
    for block in (rbp0, rbp1):
        assert "192.168.1.9/24" in block
        assert "virtual_router_id 51" in block
        assert "advert_int 1" in block
        assert "preempt_delay 300" in block
        assert "wg_easy_running" in block
        assert "@VRRP_STATE@" not in block


def test_setup_places_keepalived_and_refuses_to_guess_its_values(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    out = run("setup", "rbp0", root=root)
    assert out.returncode == 0, out.stderr
    conf = root / "etc/keepalived/keepalived.conf"
    assert "state MASTER" in conf.read_text()
    assert (root / "usr/local/lib/wg-vpn/wg-vrrp-notify.sh").exists()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k "master_and_the_backup or places_keepalived"`
Expected: FAIL — the header is not in `render`'s output.

- [ ] **Step 3: Write the two templates**

`keepalived.conf`, with `@HOST@`, `@VRRP_STATE@`, `@VRRP_PRIORITY@`, `@OWN_ADDRESS@`,
`@PEER_ADDRESS@` and the literal `192.168.1.9/24`:

```
global_defs {
  router_id @HOST@
  enable_script_security
  script_user root
}
vrrp_script wg_easy_running {
  script "/usr/bin/docker inspect -f '{{.State.Running}}' wg-easy | grep -qx true"
  interval 2
  timeout 2
  fall 3
  rise 2
}
vrrp_instance edge_vpn {
  state @VRRP_STATE@
  interface eth0
  virtual_router_id 51
  priority @VRRP_PRIORITY@
  advert_int 1
  preempt_delay 300
  unicast_src_ip @OWN_ADDRESS@
  unicast_peer {
    @PEER_ADDRESS@
  }
  virtual_ipaddress {
    192.168.1.9/24
  }
  track_script {
    wg_easy_running
  }
  notify_master "@WG_LIB@/wg-vrrp-notify.sh master"
  notify_backup "@WG_LIB@/wg-vrrp-notify.sh backup"
  notify_fault  "@WG_LIB@/wg-vrrp-notify.sh fault"
  notify_stop   "@WG_LIB@/wg-vrrp-notify.sh stop"
}
```

`wg-vrrp-notify.sh` is four lines of work: `WG_ROOT`, take `$1` as the state, `mkdir -p` the state
directory and the textfile directory, write the state file, and write
`wg_vpn_vrrp_master 1` for `master` and `0` for anything else, both files temp-then-`mv`.

- [ ] **Step 4: Wire it into the script**

Add the constants above, `desired_keepalived` (sed of the template with `vrrp_state`/`vrrp_priority`
returning MASTER/150 for `rbp0` and BACKUP/100 for `rbp1`), and an `ensure_sync_packages` that
installs `keepalived` and `sqlite3` from Ubuntu when either is missing (the `ensure_firewall` apt
pattern). Then `ensure_keepalived`: write the conf `0644`, the notify script `0755`, and on a real host
`systemctl daemon-reload` and `systemctl enable --now keepalived`, `restart` when either file changed.
`keepalived_drift` compares the two files byte for byte, and `verify` also names a `keepalived` unit
that is not enabled. Hook all of it into `cmd_render`, `cmd_setup` and `cmd_verify`.

- [ ] **Step 5: Run the file's tests**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ansible/pi/hosts-pi.sh ansible/playbooks/templates/runners/keepalived.conf ansible/playbooks/templates/runners/wg-vrrp-notify.sh hack/tests/test_pi_script.py
git commit -m "🚀 feat(edge): keepalived moves one address between the Pis"
```

---

### Task 5: The authority's snapshot

**Files:**
- Create: `ansible/playbooks/templates/runners/wg-snapshot.sh`
- Create: `ansible/playbooks/templates/runners/systemd/wg-vpn-snapshot.service`
- Create: `ansible/playbooks/templates/runners/systemd/wg-vpn-snapshot.timer`
- Modify: `ansible/pi/hosts-pi.sh` (`ensure_snapshot`, drift, render/setup/verify), `docker-compose.yml` (node_exporter's textfile collector)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: `TEXTFILE_DIR`, `WG_LIB`, `STACK_DIR`, `WG_DB`.
- Produces: `/var/lib/wg-easy-sync/wg-easy.db` (`0640 root:wg-sync`), `/var/lib/wg-easy-sync`,
  the series `wg_vpn_snapshot_timestamp_seconds` and `wg_vpn_snapshot_ok`, the unit names
  `wg-vpn-snapshot.service`/`.timer`, and the `WG_ROOT` seam every script shares.

- [ ] **Step 1: Write the failing tests**

```python
SNAPSHOT_HEADER = "--- /usr/local/lib/wg-vpn/wg-snapshot.sh\n"


def snapshot_script(tmp_path):
    block = rendered_section("rbp0", SNAPSHOT_HEADER)
    path = tmp_path / "wg-snapshot.sh"
    path.write_text(block)
    path.chmod(0o755)
    return path


SQLITE_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$SQLITE_LOG"
case "$*" in
  *integrity_check*) echo "${SQLITE_INTEGRITY:-ok}" ;;
  *".backup"*) out="${2#*.backup }"; out="${out#\\'}"; out="${out%\\'}"; : > "$out" ;;
esac
"""


def test_the_snapshot_uses_sqlite_backup_and_renames_atomically(tmp_path):
    """A copy taken while wg-easy writes can tear; .backup is the consistent read."""
    ...  # build a fixture DB file, a stub sqlite3 on PATH, SQLITE_LOG, run the script with WG_ROOT
    assert ".backup" in log
    assert (wg_root / "var/lib/wg-easy-sync/wg-easy.db").exists()
    assert "wg_vpn_snapshot_ok 1" in prom.read_text()


def test_a_failed_integrity_check_publishes_ok_zero_and_keeps_the_old_snapshot(tmp_path):
    ...  # SQLITE_INTEGRITY=corrupt
    assert "wg_vpn_snapshot_ok 0" in prom.read_text()
    assert script.returncode == 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k snapshot`
Expected: FAIL — no such section in `render`.

- [ ] **Step 3: Write the script and the units**

`wg-snapshot.sh`: `set -euo pipefail`; `WG_ROOT="${WG_ROOT:-}"`, `STACK_DIR=/home/vronteix/dockers`,
`DB="$WG_ROOT$STACK_DIR/config/wg-easy.db"`, `OUT="$WG_ROOT/var/lib/wg-easy-sync/wg-easy.db"`,
`PROM="$WG_ROOT/var/lib/node-exporter/textfile/wg-vpn-snapshot.prom"`. `.backup` into `$OUT.tmp`,
require `pragma integrity_check` to print `ok`, `chmod 0640`, `chown root:wg-sync`, `mv -f` over
`$OUT`; write `wg_vpn_snapshot_timestamp_seconds $(date +%s)` and `wg_vpn_snapshot_ok 1`, or
`ok 0` and `exit 1` on any failure. The unit is `Type=oneshot` running the script; the timer is
`OnCalendar=*:0/1` (every minute) with `Persistent=true`.

- [ ] **Step 4: Wire it in, and give node-exporter the collector**

`ensure_snapshot` writes the script `0755`, both unit files `0644`, `systemctl daemon-reload`, and
`systemctl enable --now wg-vpn-snapshot.timer`; `snapshot_drift` compares the three files, and
`verify` also names a timer that `systemctl is-enabled` does not report as enabled. In
`docker-compose.yml`, node_exporter's `command` gains
`--collector.textfile.directory=/host/var/lib/node-exporter/textfile`.

- [ ] **Step 5: Run the file's tests**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS. Add one assertion to the compose test that the textfile directory is named.

- [ ] **Step 6: Commit**

```bash
git add ansible/pi/hosts-pi.sh ansible/playbooks/templates/runners/wg-snapshot.sh ansible/playbooks/templates/runners/systemd ansible/playbooks/templates/runners/docker-compose.yml hack/tests/test_pi_script.py
git commit -m "🚀 feat(edge): snapshot the authority's database every minute"
```

---

### Task 6: The standby's copy, its refusal, and its key

**Files:**
- Create: `ansible/playbooks/templates/runners/wg-sync.sh`
- Create: `ansible/playbooks/templates/runners/systemd/wg-vpn-sync.service`
- Create: `ansible/playbooks/templates/runners/systemd/wg-vpn-sync.timer`
- Modify: `ansible/pi/hosts-pi.sh` (`ensure_sync_account`, `sync_key_drift`, `cmd_vpn_sync`, `main`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: the snapshot file and the `wg-sync` account from task 5's host; `VRRP_STATE_FILE` from task 4.
- Produces: `/etc/wg-sync/id_ed25519` (`0600 root:root`) on the standby,
  `/var/lib/wg-sync/.ssh/authorized_keys` on the authority — one line,
  `restrict,command="/bin/cat /var/lib/wg-easy-sync/wg-easy.db",from="192.168.1.38" ssh-ed25519 … wg-sync@rbp1` —
  the verb `hosts-pi.sh vpn-sync <host> [--force]`, the series `wg_vpn_sync_timestamp_seconds`,
  `wg_vpn_sync_ok`, `wg_vpn_sync_diverged`, and the stamp file
  `/var/lib/wg-easy-sync/last-installed` (`<table> <sha256>` per line). The account is created
  `--system` with its own group, which is the group the snapshot's `0640` and the directory's `0750`
  name.

- [ ] **Step 1: Write the failing tests**

```python
SYNC_HEADER = "--- /usr/local/lib/wg-vpn/wg-sync.sh\n"


def test_the_sync_refuses_when_the_standby_was_edited(tmp_path):
    """A change made on the standby is named, not erased: the operator decides."""
    ...  # local DB differs from the stamp, snapshot matches the authority
    assert script.returncode == 1
    assert "wg_vpn_sync_diverged 1" in prom.read_text()
    assert "docker" not in docker_log.read_text()
    assert "clients_table" in stderr  # the table that moved


def test_the_sync_installs_a_changed_snapshot_and_records_it(tmp_path):
    ...  # stamp matches local, snapshot differs
    assert "docker" in docker_log.read_text()          # stop/start around the swap
    assert stamp.read_text().split()[0] == "clients_table"
    assert "wg_vpn_sync_ok 1" in prom.read_text()


def test_the_sync_does_nothing_when_nothing_changed(tmp_path):
    ...  # stamp == local fingerprint == snapshot fingerprint
    assert "docker" not in docker_log.read_text()


def test_the_sync_defers_while_this_host_holds_the_address(tmp_path):
    ...  # /run/wg-vpn/vrrp.state reads master
    assert "docker" not in docker_log.read_text()
    assert script.returncode == 0


def test_the_forced_copy_overwrites_a_diverged_standby(tmp_path):
    ...  # same fixture as the refusal, WG_SYNC_FORCE=1
    assert script.returncode == 0
    assert "docker" in docker_log.read_text()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k sync`
Expected: FAIL — no such section in `render`.

- [ ] **Step 3: Write the script**

`wg-sync.sh` with `WG_ROOT`, the same paths as task 5 plus `KEY=/etc/wg-sync/id_ed25519`,
`STAMP=/var/lib/wg-easy-sync/last-installed`, `DL=/var/lib/wg-easy-sync/incoming.db`. The five tables
and their ordering keys, fingerprinted with

```bash
sqlite3 -readonly "$db" "select * from clients_table order by id;" | sha256sum
```

for `clients_table(id)`, `hooks_table(id)`, `interfaces_table(name)`, `user_configs_table(id)`,
`users_table(id)`, one `<table> <hash>` line each. Then, in order: defer when the state file reads
`master`; fetch with `ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new wg-sync@192.168.1.22 > "$DL"`;
integrity check; refuse when the stamp is missing or the local fingerprint differs from it (naming
the differing tables on stderr, `diverged 1`); stop when the snapshot's fingerprint equals the
stamp's; otherwise `docker compose stop wg-easy`, install `"$DL"` over the database with mode `0600`
root, `docker compose start wg-easy`, and write the new stamp. `WG_SYNC_FORCE=1` skips the two
refusals. The service is `Type=oneshot` with `After=docker.service`; the timer runs every minute.

- [ ] **Step 4: Wire the account, the key and the verb**

`ensure_sync_account` creates the system account once (`useradd --system --create-home --home-dir /var/lib/wg-sync --shell /bin/sh wg-sync`
when `getent passwd wg-sync` is empty), makes `/var/lib/wg-easy-sync` (`0750 root:wg-sync`) and
`TEXTFILE_DIR` (`0755`), and on a real host fixes ownership of the key (`0600 root:root`) without
writing it. `sync_key_drift` names the missing half: `/etc/wg-sync/id_ed25519` on `rbp1`,
`/var/lib/wg-sync/.ssh/authorized_keys` on `rbp0`. `cmd_vpn_sync <host> [--force]` runs the rendered
script with `WG_ROOT="$ROOT"` and `WG_SYNC_FORCE=1` for the flag; add `vpn-sync` to `usage` and
`main` (one or two arguments).

- [ ] **Step 5: Run the file's tests**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ansible/pi/hosts-pi.sh ansible/playbooks/templates/runners/wg-sync.sh ansible/playbooks/templates/runners/systemd hack/tests/test_pi_script.py
git commit -m "🚀 feat(edge): the standby pulls the authority's database and refuses to guess"
```

---

### Task 7: The sync key survives a reflash

**Files:**
- Modify: `ansible/pi/hosts-pi.sh` (`cmd_backup`, `cmd_restore`)
- Test: `hack/tests/test_pi_script.py:324-346` (`test_backup_then_restore_puts_the_stack_back`)

**Interfaces:**
- Consumes: the two key paths named in task 6.
- Produces: `$dir/wg/` in a backup, holding `etc-wg-sync/` and `wg-sync/` (the account's `.ssh`).

- [ ] **Step 1: Extend the round-trip test**

In `test_backup_then_restore_puts_the_stack_back`, add both files to the fixture before the backup
and assert after the restore that `etc/wg-sync/id_ed25519` is back with mode `0600` and
`var/lib/wg-sync/.ssh/authorized_keys` holds the same line.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k backup_then_restore`
Expected: FAIL — the key is not in the backup.

- [ ] **Step 3: Copy both paths**

`cmd_backup` gains `mkdir -p "$dir/wg"` and copies `$ROOT/etc/wg-sync` and `$ROOT/var/lib/wg-sync/.ssh`
when they exist; `cmd_restore` copies them back, then `chmod 0600` on the private key and
`chmod 0700` on the `.ssh` directory.

- [ ] **Step 4: Run the file's tests**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible/pi/hosts-pi.sh hack/tests/test_pi_script.py
git commit -m "🐛 fix(edge): keep the standby's sync key in the reflash backup"
```

---

### Task 8: The pages a person reads at 2 a.m.

**Files:**
- Modify: `ansible/pi/README.md`, `docs/how-it-works/network.md`, `docs/runbooks/pi-reflash.md`, `docs/reference/alerts.md`, `docs/reference/metrics.md`, `mkdocs.yml`
- Create: `docs/runbooks/edge-vpn-failover.md`

**Interfaces:**
- Consumes: every path, port, unit name and series the earlier tasks produced — copied, not paraphrased.

- [ ] **Step 1: Write the runbook**

`docs/runbooks/edge-vpn-failover.md` with the banner (`In one line` / `Use it when` / `Verified` /
`Kind: how-to` / `Sources`), then: what holds the address (`cat /run/wg-vpn/vrrp.state`,
`ip -4 addr show eth0`), the acceptance test (stop keepalived on the holder, time a busy and an idle
client, record both), recovering a divergence (`hosts-pi.sh vpn-sync <host> --force`, after copying
the standby's edit to `rbp0` or accepting its loss), and the router's two calls with the read-back.

- [ ] **Step 2: Add it to the nav and correct the three pages**

`mkdocs.yml` gains the runbook under Runbooks. `network.md`'s forward table says `51821` → the
address and drops `51822`. `pi-reflash.md` gains the order: `restore` → `setup` → `vpn-sync --force`
→ `verify`, and says the key comes back with the backup. `alerts.md` gains the four rules and their
no-data behaviour; `metrics.md` gains the three textfile families and names node-exporter's collector
on the Pis. `ansible/pi/README.md` gains the pair, the authority rule and the key.

- [ ] **Step 3: Run the documentation checks**

Run: `bash hack/check-docs.sh`
Expected: `0 broken relative links`, `0 documentation problem(s)`; the staleness report prints and
does not fail.

- [ ] **Step 4: Commit**

```bash
git add ansible/pi/README.md docs mkdocs.yml
git commit -m "📝 docs(edge): the failover pair, its test and its alerts"
```

---

### Task 9: The live rollout, from here

**Files:** none — this task changes hosts and services, and records what it measured in
`docs/runbooks/edge-vpn-failover.md`.

**Interfaces:**
- Consumes: everything above, deployed.
- Produces: the measured recovery times in the runbook, four alert rules in Grafana, and a router
  whose table reads `51821 → 192.168.1.9` with no `51822` row.

- [ ] **Step 1: Mint the sync key, then migrate the peers**

Mint the pair on `rbp1` (`sudo ssh-keygen -t ed25519 -N '' -C wg-sync@rbp1 -f /etc/wg-sync/id_ed25519`)
and put its public line, with the options above, in `rbp0`'s
`/var/lib/wg-sync/.ssh/authorized_keys`. Both need `sudo`, so this is the one step that waits for the
operator's password. Then set `rbp0`'s panel default keepalive to 10 s, add `elevensky2` at `10.8.0.6`,
and reissue one config per device from `rbp0`, importing each. This is the only reissue.

- [ ] **Step 2: Apply the change to the authority, then the standby**

Merge, then `sudo ansible/pi/hosts-pi.sh setup rbp0` and `verify`, then the same on `rbp1`, then the
forced first copy on the standby (`sudo ansible/pi/hosts-pi.sh vpn-sync rbp1 --force`). Confirm both
panels list the same peers and `192.168.1.22` holds `192.168.1.9`.

- [ ] **Step 3: Create the four alert rules through Grafana's API**

`POST /api/v1/provisioning/alert-rules` with `GRAFANA_TOKEN`, group `homelab-core`, interval 300 s,
`for: 10m`, `noDataState: Alerting`, one rule per series named in the spec's D13. Read them back and
confirm each queries the series that `curl` shows on Prometheus.

- [ ] **Step 4: Change the router, last**

Two sysbus calls (spec D14): `setPortForwarding` on `webui_wireguard0` with
`destinationIPAddress: 192.168.1.9`, then `deletePortForwarding` on `webui_wireguard1` (`id` **and**
`origin`), then `getPortForwarding` read back. Applied before step 2 completes, the public port lands
on an address nothing answers.

- [ ] **Step 5: Measure the acceptance and write it down**

From a client that dials `80.14.57.209:51821` through the box, stop keepalived on the holder and time
the reconnect of a busy client and an idle one. Record both numbers in the runbook, with the panel's
keepalive value and the date. The card's target is "in use ≤15 s, idle ≤30 s"; a number outside it is
written down as measured, not rounded to the target.

- [ ] **Step 6: Commit the record**

```bash
git add docs/runbooks/edge-vpn-failover.md
git commit -m "📝 docs(edge): the failover measured on the pair"
```
