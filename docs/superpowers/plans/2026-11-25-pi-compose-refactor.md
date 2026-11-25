# One compose template for the two edge Pis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two per-host compose files with one template rendered per host, and have `hosts-pi.sh` place the two config files the stack bind-mounts.

**Architecture:** `ansible/playbooks/templates/runners/docker-compose.yml` becomes the single template, with `@WG_UDP_PORT@` where the WireGuard UDP port goes. `ansible/pi/hosts-pi.sh` keeps the port per host in `wg_udp_port()` and renders the file through `desired_compose()` for `render`, `setup` and `verify`. `setup` also writes `etc-pihole-updatelists/pihole-updatelists.conf` and `etc-dnsmasq.d/02-my-wildcard-dns.conf` from the templates already in the repo, refuses a destination that is a directory, and restarts only `pihole` when one of its mounted files changed.

**Tech Stack:** bash (`hosts-pi.sh`, no new dependency), Python pytest (`hack/tests/test_pi_script.py`), Docker Compose file format, no `version:` key.

**Spec:** `docs/superpowers/specs/2026-11-25-pi-compose-refactor-design.md`

## Global Constraints

- Every file under `ansible/playbooks/templates/runners/` is CRLF. Preserve it; a line-ending change makes every host's next `setup` rewrite a functionally identical file.
- The script keeps its four verbs and its interface: `render|setup|verify <host>`, `backup|restore <dir> <host>`, `PI_ROOT` redirects every path, `STACK_DIR=/home/vronteix/dockers`.
- `setup` stays idempotent: a second run on a correct host prints `changed=0` and recreates nothing.
- `setup` never deletes anything, never writes an env file, never writes a boot file.
- Tests: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py`.
- Commits use a `♻️` prefix (refactor); one logical change per commit.

## Review Focus

- A destination that is already a **directory** (Docker's artefact of a missing bind-mount source): `setup` must name the path and exit 1, and must leave the directory in place.
- A host where nothing changed: `setup` must print `changed=0` and must not recreate a container — the up command in that case is the plain one.
- A changed pihole-mounted file: pihole must be force-recreated, because a bind mount is resolved at container start and a file written afterwards is invisible to the running container.
- The port: `51821` must never appear in `rbp1`'s rendered file and `51822` never in `rbp0`'s — the router forwards one public UDP port to each, and the wrong one leaves a Pi unreachable over the tunnel.
- Line endings: the file written on the Pi was CRLF before this change and must still be CRLF after it, or `verify` reports noise on a correct host.

---

### Task 1: One template, port rendered per host

**Files:**
- Create: `ansible/playbooks/templates/runners/docker-compose.yml`
- Delete: `ansible/playbooks/templates/runners/rbp0/`, `ansible/playbooks/templates/runners/rbp1/`
- Modify: `ansible/pi/hosts-pi.sh` (`compose_src` → `wg_udp_port` + `desired_compose`; `cmd_render`, `cmd_setup`, `cmd_verify`)
- Modify: `ansible/pi/README.md` (the compose row of the owned-path table)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Produces: `wg_udp_port <host>` prints `51821` or `51822`; `desired_compose <host>` prints the template with `@WG_UDP_PORT@` replaced. Every later task reads the file through `desired_compose`.

- [ ] **Step 1: Write the failing test**

In `hack/tests/test_pi_script.py`, add:

```python
def rendered_compose(host):
    """The compose block as text, newline-normalised, ending in one newline —
    the same shape `read_text()` gives the file written on the Pi."""
    out = run("render", host)
    assert out.returncode == 0, out.stderr
    block = out.stdout.split("--- /home/vronteix/dockers/docker-compose.yml\n", 1)[1]
    return block.split("\n--- ", 1)[0] + "\n"


def test_the_two_hosts_render_the_same_stack_apart_from_the_wireguard_port():
    rbp0 = rendered_compose("rbp0").replace("51821", "PORT")
    rbp1 = rendered_compose("rbp1").replace("51822", "PORT")
    assert rbp0 == rbp1


def test_each_host_gets_its_own_wireguard_port():
    assert "51822" not in rendered_compose("rbp0")
    assert "51821" not in rendered_compose("rbp1")
    assert "@WG_UDP_PORT@" not in rendered_compose("rbp0")


def test_the_placed_file_is_what_render_printed(tmp_path):
    root = tmp_path
    stack = root / "home/vronteix/dockers"
    stack.mkdir(parents=True)
    (stack / "pihole.env").write_text("WEBPASSWORD=x\n")
    (stack / "wg-easy.env").write_text("WG_HOST=x\n")
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")

    assert run("setup", "rbp0", root=root).returncode == 0
    assert (stack / "docker-compose.yml").read_text() == rendered_compose("rbp0")
    assert b"\r\n" in (stack / "docker-compose.yml").read_bytes()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k "render or placed" -v`
Expected: FAIL — the rbp0 and rbp1 blocks differ by their comment blocks, and the header is still the template path.

- [ ] **Step 3: Create the template**

```bash
cd ansible/playbooks/templates/runners
sed 's/51821:51821/@WG_UDP_PORT@:@WG_UDP_PORT@/' rbp0/docker-compose.yml > docker-compose.yml
git rm -r rbp0 rbp1
```

Then edit the new file: keep CRLF, and reword the task-480 note so it holds for both hosts — replace "on rbp0", "this copy" and "`docker ps` on rbp0 has not shown it running for three months" with a wording that says the router was dropped here by decision (task 480, 2026-11-24), the cluster's Traefik on `streaming` owns 80/443 and the only certificate store, and keeping one declared is how a second router comes back by accident. The note must not name `docker ps` on a single host.

Also remove the "More info at https://github.com/pi-hole/docker-pi-hole/" line if it survived the `sed`; the image is `jacklul/pihole`.

- [ ] **Step 4: Replace `compose_src` with the two functions**

In `ansible/pi/hosts-pi.sh`, delete `compose_src()` and add beside `desired_hostname()`:

```bash
wg_udp_port() {
    case "$1" in
        rbp0) printf '51821\n' ;;
        rbp1) printf '51822\n' ;;
    esac
}

desired_compose() {
    sed "s/@WG_UDP_PORT@/$(wg_udp_port "$1")/g" "$TEMPLATES/docker-compose.yml"
}
```

Both hosts are already validated by `check_host`, so `wg_udp_port` needs no default arm.

- [ ] **Step 5: Point the three verbs at `desired_compose`**

- `cmd_render`: print the destination path, not the template path, then the rendered file:
  `echo "--- $STACK_DIR/docker-compose.yml"` followed by `desired_compose "$host"`.
- `cmd_setup`: `write_text "$ROOT$STACK_DIR/docker-compose.yml" "$(desired_compose "$host")"`. Delete `write_from_file` if `grep -n write_from_file ansible/pi/hosts-pi.sh` now finds only its definition.
- `cmd_verify`: compare the host's file with the rendered bytes rather than the template —
  `if ! cmp -s <(printf '%s\n' "$(desired_compose "$host")") "$ROOT$STACK_DIR/docker-compose.yml"; then` and diff the same way (`diff -u "$ROOT$STACK_DIR/docker-compose.yml" <(printf '%s\n' "$(desired_compose "$host")") || true`).

- [ ] **Step 6: Move the no-second-router test to the template**

Replace `test_the_rbp0_template_does_not_start_a_second_router` with one that reads `ansible/playbooks/templates/runners/docker-compose.yml` (path updated) and asserts `"traefik" not in compose["services"]`, plus a second assertion that `"traefik" not in rendered_compose(host)` for both hosts. Keep the reason text.

- [ ] **Step 7: Run the whole test file**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -v`
Expected: PASS, including the pre-existing idempotency, backup/restore and missing-env tests. If `test_setup_is_idempotent` fails with `changed=2`, the comparison in `write_text` is not seeing the CRLF the same way the template writes it — fix the writer, not the test.

- [ ] **Step 8: Update the README row**

In `ansible/pi/README.md`'s owned-path table, change the compose row's Why to say it is one template in `playbooks/templates/runners/` rendered per host, with the WireGuard port the only per-host value.

- [ ] **Step 9: Commit**

```bash
git add ansible/playbooks/templates/runners ansible/pi docs/superpowers
git commit -m "♻️ refactor: render one compose template for both edge Pis"
```

---

### Task 2: Place the two config files the stack mounts

**Files:**
- Modify: `ansible/pi/hosts-pi.sh` (`write_text` guard, `CHANGED_THIS_WRITE`, `cmd_setup`, `cmd_verify`, `cmd_render`)
- Modify: `ansible/pi/README.md` (two rows in the owned-path table)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: `desired_compose <host>` from Task 1.
- Produces: globals `CHANGED_THIS_WRITE` (0/1, set by `write_text`) and the two `STACK_DIR` destinations. No new script verb.

- [ ] **Step 1: Write the failing tests**

```python
CONFIG_FILES = (
    ("etc-pihole-updatelists/pihole-updatelists.conf", "pihole-updatelists.conf"),
    ("etc-dnsmasq.d/02-my-wildcard-dns.conf", "02-my-wildcard-dns.conf"),
)


def test_setup_places_the_two_mounted_config_files(tmp_path):
    stack = _pi_fixture(tmp_path / "pi")
    assert run("setup", "rbp1", root=tmp_path / "pi").returncode == 0
    for relative, template in CONFIG_FILES:
        placed = stack / relative
        assert placed.is_file(), relative
        assert placed.read_text() == (
            REPO / "ansible/playbooks/templates/runners" / template
        ).read_text()


def test_verify_names_each_config_file_that_differs(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    for relative, _ in CONFIG_FILES:
        placed = stack / relative
        kept = placed.read_text()
        placed.write_text("address=/wrong.local/10.0.0.1\n")
        out = run("verify", "rbp1", root=root)
        assert out.returncode == 1
        assert f"DIFFERS {placed}" in out.stdout
        placed.write_text(kept)


def test_a_directory_in_place_of_a_config_file_is_refused_not_deleted(tmp_path):
    root = tmp_path / "pi"
    stack = _pi_fixture(root)
    victim = stack / "etc-dnsmasq.d/02-my-wildcard-dns.conf"
    victim.mkdir(parents=True)

    out = run("setup", "rbp1", root=root)

    assert out.returncode == 1
    assert str(victim) in out.stderr
    assert victim.is_dir(), "the script deletes nothing"
```

`_pi_fixture` already exists in the file and creates the stack, `pihole.env`, `wg-easy.env` and the proc fixture; reuse it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k "config_file or directory" -v`
Expected: FAIL — the files are never written, and `setup` exits non-zero with a `printf` error instead of the refusal.

- [ ] **Step 3: Add the directory guard and the change flag**

```bash
CHANGED=0
CHANGED_THIS_WRITE=0
```

```bash
refuse_directory() {
    if [ -d "$1" ]; then
        echo "$1 is a directory: Docker creates one when a bind mount's source is missing." >&2
        echo "Remove it and run setup again; this script deletes nothing." >&2
        exit 1
    fi
}
```

In `write_text`, after `mkdir -p`, call `refuse_directory "$dest"`, set `CHANGED_THIS_WRITE=0` at the top and `CHANGED_THIS_WRITE=1` beside `CHANGED=$((CHANGED + 1))`.

- [ ] **Step 4: Write the two files in `cmd_setup`**

After the compose write and before the hostname, add:

```bash
    write_text "$ROOT$STACK_DIR/etc-pihole-updatelists/pihole-updatelists.conf" \
        "$(cat "$TEMPLATES/pihole-updatelists.conf")"
    pihole_changed="$CHANGED_THIS_WRITE"
    write_text "$ROOT$STACK_DIR/etc-dnsmasq.d/02-my-wildcard-dns.conf" \
        "$(cat "$TEMPLATES/02-my-wildcard-dns.conf")"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        pihole_changed=1
    fi

    if [ -z "$ROOT" ]; then
        if [ "$pihole_changed" -eq 1 ]; then
            # A bind mount is resolved when the container starts: the file is
            # invisible to the running pihole until it restarts.
            ( cd "$STACK_DIR" && docker compose up -d --remove-orphans --force-recreate pihole >/dev/null )
            echo "stack: docker compose up -d --remove-orphans --force-recreate pihole"
        else
            ( cd "$STACK_DIR" && docker compose up -d --remove-orphans >/dev/null )
            echo "stack: docker compose up -d --remove-orphans"
        fi
    else
        echo "stack: not started (PI_ROOT is set)"
    fi
```

Declare `pihole_changed=0` beside `current_name` so the `else` branch is safe when neither write changed anything.

- [ ] **Step 5: Show and check the two files**

`cmd_render` gains, in table order:

```bash
    echo "--- $STACK_DIR/etc-pihole-updatelists/pihole-updatelists.conf"
    cat "$TEMPLATES/pihole-updatelists.conf"
    echo "--- $STACK_DIR/etc-dnsmasq.d/02-my-wildcard-dns.conf"
    cat "$TEMPLATES/02-my-wildcard-dns.conf"
```

`cmd_verify` gains a helper and two calls:

```bash
check_text() {
    local dest="$1" content="$2"
    if [ ! -f "$dest" ]; then
        echo "MISSING $dest"
        return 1
    fi
    if ! cmp -s <(printf '%s\n' "$content") "$dest"; then
        echo "DIFFERS $dest"
        diff -u "$dest" <(printf '%s\n' "$content") || true
        return 1
    fi
    return 0
}
```

called as `if ! check_text "$ROOT$STACK_DIR/etc-pihole-updatelists/pihole-updatelists.conf" "$(cat "$TEMPLATES/pihole-updatelists.conf")"; then problems=1; fi` and likewise for the dnsmasq file. Leave the existing compose comparison as Task 1 left it.

- [ ] **Step 6: Run the whole test file**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -v`
Expected: PASS. `test_setup_is_idempotent` still prints `changed=0` — the two new writes must not count on a second run.

- [ ] **Step 7: Assert the recreate rule is in the script**

Add, beside the existing `--remove-orphans` assertion:

```python
def test_setup_restarts_pihole_when_a_mounted_file_changed():
    text = SCRIPT.read_text()
    assert "--force-recreate pihole" in text, (
        "a bind mount is resolved at container start; writing the file while "
        "pihole runs leaves the old mount in place"
    )
    assert "PI_ROOT is set" in text, "the docker branch stays behind the PI_ROOT guard"
```

Run it: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k restarts_pihole -v` → PASS.

The docker branch only runs when `PI_ROOT` is unset, so a fixture cannot reach it without writing to `/`; the repository's established proof for that path is a text assertion (`test_setup_removes_a_service_the_template_no_longer_declares` does the same for `--remove-orphans`). Task 4 and the first real `setup` are where it is exercised for real.

- [ ] **Step 8: Add the two README rows**

In the owned-path table of `ansible/pi/README.md`, add:

| `etc-pihole-updatelists/pihole-updatelists.conf` | yes | the bind mount Pi-hole's list updater reads; without it Docker makes the source a directory |
| `etc-dnsmasq.d/02-my-wildcard-dns.conf` | yes | the wildcard names the LAN resolves through this Pi-hole |

and one line under the table saying both live inside the stack directory, so `backup` and `restore` already carry them.

- [ ] **Step 9: Commit**

```bash
git add ansible/pi hack/tests/test_pi_script.py
git commit -m "🐛 fix: place the config files the Pi stack mounts"
```

---

### Task 3: Correct the reflash runbook

**Files:**
- Modify: `docs/runbooks/pi-reflash.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing; documentation only.

- [ ] **Step 1: Fix the boot-file claim**

Step 7 says "`setup` rewrites `/boot/firmware/config.txt` and `cmdline.txt` only because the image already put them there". `setup` writes neither. Replace that clause with what the script does: `setup` writes the compose file, the two mounted configs, `/etc/hostname` and `/etc/hosts`, and leaves the boot files to the image; `restore` puts them back only when the backup's `cmdline.txt` names this disk.

- [ ] **Step 2: Update the sources line**

In the front-matter block, keep `ansible/playbooks/templates/runners/` and add `ansible/pi/README.md`, so the page points at the table that now lists every file the script owns.

- [ ] **Step 3: Check the doc gates**

Run: `hack/check-docs.sh --base origin/develop` (or `main` if `develop` is not fetched).
Expected: exit 0, and the touched page carries its banner.

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/pi-reflash.md
git commit -m "📝 docs: the Pi runbook says what setup writes"
```

---

### Task 4: Verify on the branch

**Files:** none.

- [ ] **Step 1: Render both hosts and read one**

Run: `bash ansible/pi/hosts-pi.sh render rbp0 | sed -n '1,60p'` and the same for `rbp1`.
Expected: one `--- /home/vronteix/dockers/docker-compose.yml` block whose wg-easy line reads `51821` on rbp0 and `51822` on rbp1, then the two config blocks, then `/etc/hostname` and `/etc/hosts`.

- [ ] **Step 2: Prove the rendered YAML parses and the env handles are intact**

```bash
d=$(mktemp -d)
bash ansible/pi/hosts-pi.sh render rbp0 | awk '/^--- .*docker-compose.yml\r?$/{f=1;next} /^--- /{f=0} f' > "$d/docker-compose.yml"
printf 'WEBPASSWORD=x\n' > "$d/pihole.env"; printf 'WG_HOST=x\n' > "$d/wg-easy.env"
mkdir -p "$d/etc-pihole-updatelists"; : > "$d/etc-pihole-updatelists/pihole-updatelists.conf"
( cd "$d" && docker compose config --services )
```

Expected: `node_exporter`, `pihole`, `smartctl-exporter`, `wg-easy`; no error. Empty the temp directory afterwards.

- [ ] **Step 3: Run the full test suite**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
Expected: zero failures.

- [ ] **Step 4: Hand the branch over**

`git fetch origin && git rebase origin/develop`, rerun Step 3, `git push origin feature/pi-compose-refactor`, then open the pull request against `develop` with `github_create_pull_request`. The body names the spec, the two behaviour changes (config placement, pihole-only recreate) and the verification output.
