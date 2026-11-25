# Edge DNS parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give both edge Pis one rendered Pi-hole settings file and make each host resolve through Pi-hole, with rbp0 as the source of truth.

**Architecture:** The settings that are identical on both hosts move out of the hand-kept `pihole.env` into a template that `hosts-pi.sh` writes, prints and compares — the treatment `docker-compose.yml` already gets. The password stays the only key in `pihole.env`. Netplan's resolver list becomes the two Pi-hole addresses on both hosts. A workspace-side check compares the two panels through their own API and fails on drift.

**Tech Stack:** bash (`ansible/pi/hosts-pi.sh`), Docker Compose env files, Pi-hole v6 `FTLCONF_*` environment keys, netplan, pytest under `hack/tests/`.

**Spec:** `docs/superpowers/specs/2026-11-25-edge-dns-parity-design.md`

## Global Constraints

- The rendered file holds **no secret**. `FTLCONF_webserver_api_password` stays the only key in `pihole.env`, which is host-local, `0600`, owned by the account that runs compose, and covered by `.gitignore`'s `*.env`.
- Both hosts must end up with **byte-identical** rendered settings; `verify` compares bytes, not lines.
- Values, copied from the spec: `dns.blockTTL=2`, `dns.rateLimit.count=1000`, `dns.listeningMode=ALL`, `dns.revServers=true,192.168.1.0/24,192.168.1.1,local`, `dns.upstreams=86.54.11.100;86.54.11.200`.
- Netplan resolvers, both hosts, in this order: `192.168.1.22`, `192.168.1.38`. Never `127.0.0.1` (a container cannot reach it), never `1.1.1.1`.
- `PIHOLE_DNS_`, `CUSTOM_CACHE_SIZE`, `WEB_PORT` and `SKIPGRAVITYONBOOT` are dropped: a throwaway container on 2026-11-25 showed FTL ignores all four (upstreams stayed at the image default and the cache at 10000).
- No dashboard edit, no teleporter round-trip: the file is the only mechanism.

## Review Focus

- A key that only exists in one panel's `pihole.toml` but not in the file: the next `setup` run must not leave it behind silently — `hack/check-pihole-parity.py` is what names it.
- An empty `FTLCONF_misc_dnsmasq_lines`: the file must clear the toml value the old env set, or the two dnsmasq sources disagree.
- `netplan apply` over SSH: a wrong nameserver list locks the host out of name resolution, so the second address is part of the file, not a comment.
- `pihole.env` disappearing on a reflash: `require_env` must still fail loudly rather than let a stack start with no panel password.
- A stale `pihole.settings` on a host whose compose was updated first: `verify` must report it before the stack runs.

---

### Task 1: One rendered settings file

**Files:**
- Create: `ansible/playbooks/templates/runners/pihole.settings`
- Modify: `ansible/playbooks/templates/runners/docker-compose.yml` (pihole `env_file`)
- Modify: `ansible/pi/hosts-pi.sh` (`desired_pihole_settings`, `cmd_render`, `cmd_setup`, `cmd_verify`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `desired_pihole_settings()` in `hosts-pi.sh`, printing the file's bytes; the host path `$STACK_DIR/pihole.settings`.

- [ ] **Step 1: Write the failing test** — `test_the_shared_pihole_settings_are_one_rendered_file`: render `rbp0` and `rbp1` through the script, assert both print a `pihole.settings` section, that the bytes are equal, that the text carries `FTLCONF_dns_upstreams=86.54.11.100;86.54.11.200` and `FTLCONF_dns_blockTTL=2`, and that it does not carry `FTLCONF_webserver_api_password` or `PIHOLE_DNS_`.
- [ ] **Step 2: Run it** — `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k shared_pihole` → FAIL (no such section).
- [ ] **Step 3: Implement** — the template; `desired_pihole_settings(){ cat "$TEMPLATES/pihole.settings"; }`; `cmd_render` prints it; `cmd_setup` writes it with `write_abs` and marks `pihole_changed`; `cmd_verify` compares it with `check_text`; compose reads `./pihole.settings` then `./pihole.env`.
- [ ] **Step 4: Run the whole file of tests** → PASS.
- [ ] **Step 5: Commit** — `🐛 fix: give both edge piholes one rendered settings file`.

### Task 2: Own the resolvers

**Files:**
- Modify: `ansible/playbooks/templates/runners/50-cloud-init.yaml`
- Modify: `ansible/pi/hosts-pi.sh` (`desired_netplan`, `cmd_setup`, `cmd_verify`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: Task 1's script structure.
- Produces: `desired_netplan()`; `setup` writes `/etc/netplan/50-cloud-init.yaml` and runs `netplan apply` only when the bytes changed.

- [ ] **Step 1: Write the failing test** — `test_both_pis_resolve_through_pihole`: assert the rendered netplan carries `192.168.1.22` and `192.168.1.38`, and neither `1.1.1.1` nor `127.0.0.1`.
- [ ] **Step 2: Run it** → FAIL.
- [ ] **Step 3: Implement** — nameservers list in the template; `desired_netplan(){ cat "$TEMPLATES/50-cloud-init.yaml"; }`; `cmd_render` prints it; `cmd_setup` writes it under `$ROOT/etc/netplan/`, and when `CHANGED_THIS_WRITE` is 1 and `PI_ROOT` is empty, runs `netplan apply`; `cmd_verify` compares it with `check_text`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `🐛 fix: resolve through pihole on both edge Pis`.

### Task 3: The parity check

**Files:**
- Create: `hack/check-pihole-parity.py`
- Test: `hack/tests/test_pihole_parity.py`

**Interfaces:**
- Consumes: `PIHOLE_RBP0_API_KEY`, `PIHOLE_RBP1_API_KEY` from the environment; the v6 API at `POST /api/auth` (session `sid`) and `GET /api/config`, `GET /api/lists`.
- Produces: `diff_panels(rbp0, rbp1) -> list[str]` where each entry is a dotted key path; `main()` exits 1 listing them, 0 when empty.

- [ ] **Step 1: Write the failing test** — `test_diff_names_the_keys_that_differ` compares two nested dicts differing in `dns.blockTTL` and in one adlist address, asserts both paths appear; `test_identical_panels_produce_no_diff` asserts an empty list.
- [ ] **Step 2: Run** → FAIL (module missing).
- [ ] **Step 3: Implement `hack/check-pihole-parity.py`** — flatten both config trees and the adlist addresses, ignore keys whose name contains `hash`/`password`/`totp`, print one line per difference with both values, exit non-zero when any.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `✨ feat: check that both edge piholes answer the same config`.

### Task 4: The runbook and the README

**Files:**
- Modify: `docs/runbooks/pi-reflash.md`
- Modify: `ansible/pi/README.md`

- [ ] **Step 1: Write** — the panel section: `pihole.settings` is rendered and compared, `pihole.env` holds only the password, the resolvers point at both Pis, and a reflash without a restored volume needs the password set again.
- [ ] **Step 2: Verify** — `hack/check-docs.sh --base origin/develop` → rc 0.
- [ ] **Step 3: Commit** — `📝 docs: describe how the two piholes are kept identical`.

### Task 5: The rollout on the two hosts

**Files:** none in git.

- [ ] **Step 1** — on each host: back up `pihole.env`, write `pihole.settings` and the rendered netplan through `setup`, run `netplan apply`.
- [ ] **Step 2** — rotate the shared panel password on both (app password minted for the API, panel password set through the API), store it in `~/.config/secrets/homelab.env`.
- [ ] **Step 3** — `verify` on both hosts → `ok: <host> matches the repository`.
- [ ] **Step 4** — `hack/check-pihole-parity.py` → no differences; a query from each host appears in its own panel.
- [ ] **Step 5** — open the pull request against `develop`, move card #748 to Validation.
