# The Pis' Docker engine in `hosts-pi.sh` — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `hosts-pi.sh setup` installs and pins the Docker engine and the Compose plugin on a reflashed Pi, so runbook step 6 stops being a memory.

**Architecture:** One bash function, `ensure_engine`, called from `cmd_setup` after the env-file check and before the stack starts. It reads the release codename from `/etc/os-release`, writes Docker's deb822 repository and keyring, and installs four packages by version band. It installs only when a package is absent: an engine that is present but outside the band is named, never upgraded in place. `verify` reports the same bands. Under `PI_ROOT` both say so and touch nothing.

**Tech Stack:** bash 5, apt with deb822 sources, pytest driving the script through `PI_ROOT`.

**Spec:** [`docs/superpowers/specs/2026-11-25-pi-docker-engine-design.md`](../specs/2026-11-25-pi-docker-engine-design.md)

## Global Constraints

- **Nothing in the repository bumps these pins.** The bands are `docker-ce=5:29.*`, `docker-ce-cli=5:29.*`, `containerd.io=2.4.*`, `docker-compose-plugin=5:8.*`; the measured versions on 2026-11-25 are `29.10.1`, `29.10.1`, `2.4.2`, `5.8.0`, and they go in the comment, not in the pin.
- **No release codename in the script.** The suite comes from `/etc/os-release`; a missing codename fails the run naming that file.
- **`PI_ROOT` set means no apt, no dpkg, no network, no group change** — the engine step prints one line and returns.
- **A live host is never upgraded in place.** If a package is present outside its band, print a `NOTE` and leave it: an engine upgrade restarts the daemon that answers the house's DNS. `verify` prints the same note and does not fail on it.
- **`setup` stays idempotent**: a second run against a converged host prints `changed=0` and does no network work.
- **The four packages and the `vronteix` group membership are the whole engine scope.** No `docker-buildx-plugin`, no `daemon.json`, no `docker.io`.
- **No secret, no key value, in the tree.** The signing key is downloaded, never committed.

## Review Focus

- **A live Pi at Docker 28.3.2 runs `setup`** — the daemon must not restart and the engine must not move. Owned by Task 1 (the `NOTE` path); proved on a host by the operator, and by `verify` printing the same note without failing.
- **A suite Docker's repository does not carry yet** — `apt-get update`/`install` fails; the failure must name the suite and the pin rather than "unable to locate package". Owned by Task 1's two error messages.
- **`/etc/os-release` with neither `UBUNTU_CODENAME` nor `VERSION_CODENAME`** — the run stops before writing a repository with an empty suite. Owned by Task 1 (`suite_name`); not unit-testable without touching the runner's own `/etc/os-release`, so the message is the contract.
- **The key download fails or is truncated halfway** — no half-written keyring is left for apt to trust. Owned by Task 1 (`mktemp` + `cmp` + `install`).
- **`setup` is interrupted between the install and the group change** — the second run finishes it: the group check is a read, not a write, and the packages are already in band. Owned by Task 1.

---

### Task 1: `ensure_engine` in `setup`

**Files:**
- Modify: `ansible/pi/hosts-pi.sh`
- Modify: `ansible/pi/README.md`
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Produces: `ensure_engine` (no arguments; reads the globals `ROOT`, `CHANGED`, and the four pin constants), called by `cmd_setup` between `require_env` and the first `write_from_file`.
- Produces: `write_abs <absolute-path> <content>`; `write_text` becomes `write_abs "$ROOT$1" "$2"` so the engine can write `/etc/...` without a `PI_ROOT` prefix.
- Produces: `suite_name` (prints the codename, exits 1 with a message when absent), `installed_version <package>` (prints `dpkg-query`'s version or nothing), `version_in_band <version> <glob-band>` (exit status only), `engine_present` (exit status only: all four packages installed, keyring and sources file present).

- [ ] **Step 1: Write the failing tests**

```python
def test_render_prints_the_engine_it_would_install():
    out = run("render", "rbp0")
    assert out.returncode == 0, out.stderr
    for pin in ("docker-ce=5:29.*", "docker-ce-cli=5:29.*",
                "containerd.io=2.4.*", "docker-compose-plugin=5:8.*"):
        assert pin in out.stdout
    assert "/etc/apt/keyrings/docker.asc" in out.stdout


def test_setup_leaves_the_engine_alone_in_a_fixture(tmp_path):
    root = tmp_path
    (root / "home/vronteix/dockers").mkdir(parents=True)
    (root / "home/vronteix/dockers/pihole.env").write_text("WEBPASSWORD=x\n")
    (root / "home/vronteix/dockers/wg-easy.env").write_text("WG_HOST=x\n")
    (root / "proc").mkdir()
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")
    out = run("setup", "rbp1", root=root)
    assert out.returncode == 0, out.stderr
    assert "engine: not touched (PI_ROOT is set)" in out.stdout
    assert not (root / "etc/apt").exists()


def test_the_engine_suite_is_read_not_hard_coded():
    text = SCRIPT.read_text()
    assert "/etc/os-release" in text
    for codename in ("resolute", "noble", "jammy"):
        assert codename not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest -q hack/tests/test_pi_script.py -k "engine"`
Expected: FAIL — the render test on the first missing pin, the fixture test on the missing line, the source test on `/etc/os-release`.

- [ ] **Step 3: Add the constants and the helpers**

At the top of `ansible/pi/hosts-pi.sh`, beside `STACK_DIR`:

```bash
# The engine and the compose plugin, from Docker's own repository, pinned by
# band because nothing in this repository bumps an apt pin: an exact version
# ages silently, which is what 28.3.2 on both Pis was. Measured 2026-11-25 in
# the `resolute` suite for arm64: docker-ce 29.10.1, containerd.io 2.4.2,
# docker-compose-plugin 5.8.0.
DOCKER_PIN="5:29.*"
CONTAINERD_PIN="2.4.*"
COMPOSE_PIN="5:8.*"
DOCKER_KEYRING="/etc/apt/keyrings/docker.asc"
DOCKER_SOURCES="/etc/apt/sources.list.d/docker.sources"
DOCKER_GROUP_USER="vronteix"
```

Then, next to `write_text`, split the writer so an absolute path can bypass `PI_ROOT`:

```bash
write_text() {
    write_abs "$ROOT$1" "$2"
}

write_abs() {
    local dest="$1" content="$2"
    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest" ] && [ "$(cat "$dest")" = "$content" ]; then
        return 0
    fi
    printf '%s\n' "$content" > "$dest"
    CHANGED=$((CHANGED + 1))
}
```

Add these three helpers before `cmd_render`:

```bash
# The release's own suite name, read from the running image. A reflash onto the
# next LTS then names its own suite instead of pointing apt at a codename that
# no longer exists; an unreadable file stops the run rather than writing an
# empty suite into a repository line.
suite_name() {
    local codename=""
    if [ -r /etc/os-release ]; then
        codename="$(. /etc/os-release && printf '%s' "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}")"
    fi
    if [ -z "$codename" ]; then
        echo "cannot read a release codename from /etc/os-release; not writing an apt suite without one" >&2
        exit 1
    fi
    printf '%s\n' "$codename"
}

installed_version() {
    dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true
}

# The band is a shell glob on purpose: 5:29.10.1-1~ubuntu.26.04~resolute is
# inside 5:29.*, and 5:30.0 is not. Do not quote $2.
version_in_band() {
    case "$1" in
        ${2}) return 0 ;;
        *) return 1 ;;
    esac
}
```

Add `engine_present` (all four installed **and** the keyring and sources file exist) and `ensure_engine`. `ensure_engine` in order:

1. `if [ -n "$ROOT" ]; then echo "engine: not touched (PI_ROOT is set)"; return 0; fi`
2. Root guard: `if [ "$(id -u)" -ne 0 ]; then echo "the engine install needs root; run this with sudo" >&2; exit 1; fi`
3. `engine_present` → `echo "engine: the pinned $DOCKER_PIN / $COMPOSE_PIN set is installed"` and return 0, before any network call.
4. If every package is installed but a version is outside its band → print one `NOTE` line per package (`engine: NOTE docker-ce 28.3.2 is outside the pinned 5:29.*; this script does not upgrade an engine in place`) and return 0. A runtime upgrade restarts the daemon that answers DNS, so it is the operator's call, not `setup`'s.
5. Otherwise install: `suite="$(suite_name)"`; `install -d -m 0755 /etc/apt/keyrings`; download `https://download.docker.com/linux/ubuntu/gpg` with `curl -fsSL --max-time 30` into `mktemp`, compare with `cmp -s`, `install -m 0644` only when different, `rm -f` the temp copy either way; `write_abs "$DOCKER_SOURCES"` with exactly:

```
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: <the suite, interpolated>
Components: stable
Architectures: arm64
Signed-By: /etc/apt/keyrings/docker.asc
```

then `DEBIAN_FRONTEND=noninteractive apt-get update -qq` and `DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "docker-ce=$DOCKER_PIN" "docker-ce-cli=$DOCKER_PIN" "containerd.io=$CONTAINERD_PIN" "docker-compose-plugin=$COMPOSE_PIN"`. Both apt calls are wrapped so a failure prints `the engine install failed: check that download.docker.com carries the <suite> suite and that the pins above still match it` before exiting 1.
6. Count one change and print `engine: installed $DOCKER_PIN / $COMPOSE_PIN (suite <suite>)`.
7. Group membership: `id -nG "$DOCKER_GROUP_USER" | tr ' ' '\n' | grep -qx docker`; when absent, `usermod -aG docker "$DOCKER_GROUP_USER"`, count one change, print the line.

Call `ensure_engine` in `cmd_setup` directly after `require_env`.

- [ ] **Step 4: Print the engine in `render`**

After the compose block's `--- ... (where the block above is written)` line, before `/etc/hostname`:

```bash
echo "--- engine, installed by setup on a real host (not under PI_ROOT)"
echo "repository: https://download.docker.com/linux/ubuntu, suite read from /etc/os-release"
echo "signed by: $DOCKER_KEYRING"
echo "pinned: docker-ce=$DOCKER_PIN docker-ce-cli=$DOCKER_PIN containerd.io=$CONTAINERD_PIN docker-compose-plugin=$COMPOSE_PIN"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS, 15 tests (12 existing + 3 new).

- [ ] **Step 6: Document it in the script's README**

In `ansible/pi/README.md`: add a row to the "What it owns" table for the engine and the compose plugin, naming the band and the repository; add a short `### The engine` section saying a fresh image has none, `setup` installs the pinned set, an existing engine outside the band is named and left alone (the reflash is what moves it), and the suite comes from `/etc/os-release`. Mention the `vronteix` group membership in the same place.

- [ ] **Step 7: Commit**

```bash
git add ansible/pi/hosts-pi.sh ansible/pi/README.md hack/tests/test_pi_script.py
git commit -m "🚀 feature: the Pi script installs and pins the Docker engine"
```

---

### Task 2: `verify` names engine drift

**Files:**
- Modify: `ansible/pi/hosts-pi.sh`
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: the pins, `installed_version`, `version_in_band`, `DOCKER_GROUP_USER` from Task 1.
- Produces: `engine_drift` (no arguments; prints one line per difference and returns 1 when it printed any), called from `cmd_verify` as `engine_drift || problems=1`.

- [ ] **Step 1: Write the failing test**

```python
def test_verify_says_it_cannot_check_the_engine_in_a_fixture(tmp_path):
    (tmp_path / "home/vronteix/dockers").mkdir(parents=True)
    out = run("verify", "rbp0", root=tmp_path)
    assert "engine: not checked (PI_ROOT is set)" in out.stdout
    assert out.returncode == 1  # the stack is still missing, as before
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_pi_script.py -k "cannot_check_the_engine"`
Expected: FAIL — the line is absent.

- [ ] **Step 3: Implement `engine_drift`**

Under `PI_ROOT`, print `engine: not checked (PI_ROOT is set)` and return 0. Otherwise, for each of the four `"<package> <band>"` pairs: `MISSING package <pkg>` when `installed_version` is empty, `DIFFERS package <pkg>: <version> is outside the pinned <band>` when outside; `command -v docker` empty → `MISSING the docker command`; `DOCKER_GROUP_USER` not in the `docker` group → `DIFFERS <user> is not in the docker group`. Split each pair with `read -r pkg band <<< "$spec"` — do not split on `:`, the band carries the epoch. Return 1 when any line was printed, and print nothing that does not name a difference.

Call it in `cmd_verify` beside the other checks, before the `problems` exit. Outside `PI_ROOT`, an out-of-band version prints the same `NOTE` Task 1 prints and **does not** set `problems` — the engine is not upgraded in place, so there is no action for the operator to take from `verify`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add ansible/pi/hosts-pi.sh hack/tests/test_pi_script.py
git commit -m "🚀 feature: verify names an engine the script would not install"
```

---

### Task 3: The runbook stops installing Docker by hand

**Files:**
- Modify: `docs/runbooks/pi-reflash.md`
- Modify: `docs/runbooks/README.md:37` (only if the summary line needs it)

**Interfaces:**
- Consumes: `setup`'s engine behaviour from Tasks 1 and 2.
- Produces: step 6 as a check, steps renumbered; no by-hand apt commands left in the runbook.

- [ ] **Step 1: Replace step 6**

Delete the current step 6 and renumber the following steps 7→6, 8→7, 9→8, in the headings and in every cross-reference inside the page ("step 1", "step 5", the restore paragraph). The replacement, as step 6 "The engine, which `setup` installs":

- a fresh image has no Docker, so `setup` installs `docker-ce`, `docker-ce-cli`, `containerd.io` and `docker-compose-plugin` from `download.docker.com`, pinned to the band the script declares, and puts `vronteix` in the `docker` group;
- the suite name comes from the image's `/etc/os-release`; measured 2026-11-25, Docker's repository carries `resolute` for arm64 with `docker-ce` 29.10.1 and `docker-compose-plugin` 5.8.0, so Ubuntu's `docker.io` is not used and the fallback question is closed;
- it needs the network, and it runs before the stack starts, so no separate order is needed;
- an engine already present outside the band is named and left alone: the script never upgrades a runtime in place, and on these Pis a reflash is what moves it.

- [ ] **Step 2: Fix the prose that says otherwise**

- The "Before you start" bullet at lines 75–76 ("Docker is not in the image and nothing in this repository installs it yet (card #709 for the layer). Step 6 does it by hand.") becomes one sentence: the image has no Docker and `setup` installs it, so nothing is installed by hand.
- The "What the first run has to answer" list loses its first bullet (which package set), because step 6 now records the answer.
- Any step number written in prose is checked against the renumbering.

- [ ] **Step 3: Run the documentation and script checks**

Run: `hack/check-docs.sh --base origin/develop`
Expected: exit 0; the staleness report may print and never fails.

Run: `python3 -m pytest -q hack/tests/test_pi_script.py hack/tests/test_ansible_layer.py`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/pi-reflash.md docs/runbooks/README.md
git commit -m "📝 docs: the reflash runbook lets the script install the engine"
```

---

## Self-review notes

- The spec's "`verify` names a missing package or a version outside its band" is Task 2; the spec's "a converged host does no network work" is Task 1 step 3; the spec's "root is required" and "suite is read" are Task 1 step 3. No spec section is unowned.
- **One deviation from the approved design, deliberate:** the design implied `setup` converges an out-of-band engine; this plan has it name the version and leave it, because upgrading restarts the daemon that answers the house's DNS. `setup` and `verify` therefore agree, and the pin governs what a fresh install and a reflash get.
- The README and the runbook are folded into the task whose code they describe, not a documentation task of their own.
