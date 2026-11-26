# Edge Pi Backups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each edge Pi a nightly restic backup of its own state straight to the OVH bucket, retire the dead 04:00 cron that currently pretends to do it, and make the cluster's verifier fail when either stream goes thin or stale.

**Architecture:** `ansible/pi/hosts-pi.sh` gains `stream` and `prune` verbs, a pinned restic install, systemd units and the retirement of the legacy job in `setup`. The chart gains two `external` streams: `backup-verify` checks their age, budget and required files; `backup-prune` leaves them alone because each Pi prunes itself.

**Tech Stack:** bash (`ansible/pi/hosts-pi.sh`), Python 3.10+ (`cluster/helm/backup/files/verify.py`, test fixtures), Helm (`cluster/helm/backup`), pytest (`hack/tests`).

**Spec:** `docs/superpowers/specs/2026-11-26-edge-pi-backups-design.md`

## Global Constraints

- Branch `feature/edge-pi-backups`, base `develop`. Rebase on `origin/develop` before the PR; never merge a feature branch.
- restic **0.19.1**, installed as a static arm64 release with its SHA-256 checked; the same tag the chart pins.
- Repositories: `edge-rbp0`, `edge-rbp1` under bucket `ashudev-homelab-backup`; the bucket's existing `RESTIC_PASSWORD`; a scoped OVH user (operator's step).
- Exclusions, exactly: `pihole-FTL.db*`, `listsCache`, `gravity_old.db`.
- Staging path: `/var/lib/pi-backup/tree` (under `PI_ROOT` in tests). Env file: `/etc/restic/edge.env`, mode 0600.
- Schedule: backup `04:30` local (the Pis are UTC), prune Sundays `05:30`. Retention 7 daily / 4 weekly / 12 monthly.
- Legacy retirement: root cron line matching `scripts/backup.sh`; the four keys `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` in `/etc/environment`.
- No new package besides the pinned restic binary; `python3` is assumed (3.10 floor).
- YAML 2 spaces. `cluster/argocd/**` is CRLF — not touched here.
- Tests: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`. Charts: `./hack/check-charts.sh`. Docs: `./hack/check-docs.sh --base origin/develop`.
- Commit prefixes: `🚀 feat:`, `🐛 fix:`, `📝 docs:`, `🔧 chore:`.
- Card 862 moves to `Validation` with `percent_done` 1.0 and a `PR:`/`Cards:` handover comment when the branch is pushed.

## Review Focus

- **A live SQLite database is copied while its container writes it.** The restored `gravity.db` must hold every committed row, with no `-wal` left beside it. Pinned by Task 2's WAL test.
- **`/etc/restic/edge.env` missing, unreadable or mode 0644.** `stream` must refuse before it contacts the bucket, naming the path. Pinned by Task 3's test.
- **A fixture run on a developer machine.** No download, no `apt`, no `systemctl`, no write outside `PI_ROOT`. Pinned by Task 5's tests.
- **A fresh host with no root crontab file at all.** `setup` must converge, not fail. Pinned by Task 5's test.
- **A fresh snapshot that carries only a fragment.** The verifier must fail it, not pass it. Pinned by Task 7's tests.

---

### Task 1: `stream` stages the tree with the exclusions

**Files:**
- Modify: `ansible/pi/hosts-pi.sh` (constants near the top; `cmd_backup` refactor; new `cmd_stream`; `usage`; `main` dispatch)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Produces: `copy_state <dest> [exclude...]`, `STAGING_DIR`, `BACKUP_ENV`, `BACKUP_EXCLUDES`, `cmd_stream <host>`, and `run(*args, root=None, env=None)` in the test module.

- [ ] **Step 1: Write the failing tests**

Add to `hack/tests/test_pi_script.py`:

```python
BACKUP_EXCLUDED = ("pihole-FTL.db", "pihole-FTL.db-wal", "listsCache", "gravity_old.db")
BACKUP_KEPT = ("gravity.db", "pihole.env", "config/wg-easy.db")


def test_stream_stages_the_stack_without_the_three_excluded_paths(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    for name in ("pihole-FTL.db", "pihole-FTL.db-wal", "gravity_old.db"):
        (root / "home/vronteix/dockers/etc-pihole" / name).write_bytes(b"x")
    (root / "home/vronteix/dockers/etc-pihole/listsCache").mkdir()
    (root / "home/vronteix/dockers/etc-pihole/listsCache/a.txt").write_text("x")

    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode == 0, out.stdout + out.stderr
    tree = root / "var/lib/pi-backup/tree"
    assert (tree / "dockers/etc-pihole/gravity.db").exists()
    assert (tree / "dockers/pihole.env").exists()
    assert (tree / "dockers/config/wg-easy.db").exists()
    for name in BACKUP_EXCLUDED:
        assert not (tree / "dockers/etc-pihole" / name).exists(), name


def test_stream_leaves_no_tree_behind_after_a_successful_run(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode == 0, out.stderr
    assert not (root / "var/lib/pi-backup/tree").exists()


def test_the_manual_copy_still_holds_the_query_log(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    (root / "home/vronteix/dockers/etc-pihole/pihole-FTL.db").write_bytes(b"x")
    dest = tmp_path / "usb"
    out = run("backup", str(dest), "rbp0", root=root)
    assert out.returncode == 0, out.stderr
    assert (dest / "dockers/etc-pihole/pihole-FTL.db").exists(), (
        "the exclusions belong to the nightly stream; the pre-reflash USB copy keeps everything"
    )
```

with a helper:

```python
def fake_restic(tmp_path, exit_code=0, keep=None):
    """A restic that records its argv instead of contacting a bucket.

    `keep` copies the last argument (the tree) before exiting, which is how a test
    reads what the run staged after the run removed it."""
    binary = tmp_path / "restic"
    log = tmp_path / "restic-argv"
    keep_cmd = (
        f'for arg in "$@"; do last="$arg"; done; cp -a "$last" {keep}'
        if keep else ":"
    )
    binary.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\n{keep_cmd}\nexit {exit_code}\n'
    )
    binary.chmod(0o755)
    return {"PI_RESTIC": str(binary), "PI_RESTIC_LOG": str(log)}
```

and extend `run`:

```python
def run(*args, root=None, env=None):
    merged = dict(os.environ)
    if root:
        merged["PI_ROOT"] = str(root)
    merged.update(env or {})
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=merged)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k stream`
Expected: FAIL — `unknown verb 'stream'`.

- [ ] **Step 3: Implement**

In `ansible/pi/hosts-pi.sh`:

- Constants beside `STACK_DIR`:
  ```bash
  BACKUP_ENV="/etc/restic/edge.env"
  STAGING_DIR="/var/lib/pi-backup"
  BACKUP_EXCLUDES="pihole-FTL.db* listsCache gravity_old.db"
  RESTIC_BIN="${PI_RESTIC:-/usr/local/bin/restic}"
  ```
- Split `cmd_backup`'s copy loop into `copy_state <dest> [exclude...]`: the same tar-or-cp of
  `$ROOT$STACK_DIR` into `$dest/dockers` with each argument an exclusion applied **while copying**
  (`tar --exclude`), then `etc/hostname`, `boot/firmware/{config,cmdline}.txt`, `bootloader.txt`,
  `host.txt`, `docker-ps.txt`. `cmd_backup` calls it with no arguments and keeps its same-filesystem
  refusal; `cmd_stream` calls it with `$BACKUP_EXCLUDES` and skips that refusal (transient tree,
  remote repository — comment why).
- `cmd_stream <host>`:
  ```bash
  if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
      echo "stream reads files the containers own; run it with sudo" >&2; exit 1
  fi
  rm -rf "$ROOT$STAGING_DIR/tree"
  mkdir -p "$ROOT$STAGING_DIR/tree"
  # shellcheck disable=SC2086 — the exclusions are words, on purpose
  copy_state "$ROOT$STAGING_DIR/tree" $BACKUP_EXCLUDES
  ...
  rm -rf "$ROOT$STAGING_DIR/tree"
  ```
  The middle step is Task 2's database copy and Task 3's restic call; this task ends with the staging
  and the removal only, leaving a placeholder comment `# databases and restic follow`.
- `usage` gains `hosts-pi.sh stream <host>`; `main` dispatches it (`prune` arrives in Task 4).
- Add the assertion that keeps the manual copy whole: `backup` must still write
  `dockers/etc-pihole/pihole-FTL.db`. The exclusions belong to `stream` alone.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_pi_script.py -k "stream or backup_then_restore"`
Expected: PASS — the old `backup` test still passes against the refactor.

- [ ] **Step 5: Commit**

```bash
git add ansible/pi/hosts-pi.sh hack/tests/test_pi_script.py
git commit -m "🚀 feat(pi): stage the nightly tree, excluding the query log and caches"
```

---

### Task 2: the staged databases are consistent copies

**Files:**
- Modify: `ansible/pi/hosts-pi.sh` (`stage_sqlite`, called from `cmd_stream`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: `copy_state`, `STAGING_DIR`, `BACKUP_EXCLUDES`.
- Produces: `stage_sqlite <source-stack> <dest-stack>` — for each `*.db`/`*.sqlite`/`*.sqlite3` in `dest-stack`, replace it with a copy taken through the source's SQLite backup API; drop `-wal`/`-shm`/`-journal` siblings in the destination; exit 1 naming a database that is present and cannot be read.

- [ ] **Step 1: Write the failing test**

```python
def _make_wal_database(path):
    import sqlite3
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE domainlist (id INTEGER PRIMARY KEY, domain TEXT)")
    con.execute("INSERT INTO domainlist (domain) VALUES ('ads.example')")
    con.commit()
    con.close()  # leaves gravity.db-wal beside it on a Pi, not here; the copy must be consistent


def test_stream_copies_a_live_database_through_the_sqlite_api(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    live = root / "home/vronteix/dockers/etc-pihole/gravity.db"
    _make_wal_database(live)
    (live.parent / "gravity.db-wal").write_bytes(b"stale wal bytes")

    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path, keep=tmp_path / "kept"))
    assert out.returncode == 0, out.stdout + out.stderr
    copy = tmp_path / "kept/dockers/etc-pihole/gravity.db"
    import sqlite3
    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    assert con.execute("SELECT domain FROM domainlist").fetchall() == [("ads.example",)]
    con.close()
    assert not (copy.parent / "gravity.db-wal").exists()
    assert not (copy.parent / "gravity.db-shm").exists()


def test_stream_refuses_a_database_it_cannot_read(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    (root / "home/vronteix/dockers/etc-pihole/broken.db").write_text("not a database")
    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode != 0
    assert "broken.db" in out.stderr
```

- [ ] **Step 2: Run to verify it fails**

Run: `... -k "sqlite_api or cannot_read"`
Expected: FAIL — the WAL marker survives, or the row is missing.

- [ ] **Step 3: Implement `stage_sqlite`**

A `python3 - "$src" "$dst" <<'PY'` heredoc: walk `dst` for `*.db`, `*.sqlite`, `*.sqlite3`; for each,
open the matching path under `src` read-only (`file:...?mode=ro`), open the destination file as a
new SQLite database, `src.backup(dst)`, close both, delete `dst + "-wal"`, `"-shm"`, `"-journal"`;
on any exception print `database <relative path> cannot be staged` to stderr and exit 1. Call it
after `copy_state` and before the restic placeholder. Comment that an excluded database never reaches
this step because `copy_state` never wrote it.

- [ ] **Step 4: Run the tests**

Run: `... -k "stream"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "🚀 feat(pi): stage SQLite databases through the backup API"
```

---

### Task 3: the restic run, its prerequisites and its exit code

**Files:**
- Modify: `ansible/pi/hosts-pi.sh` (`require_backup_env`, the restic call in `cmd_stream`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Produces: `require_backup_env` (checks `$ROOT$BACKUP_ENV` exists and is mode 0600 on a real host,
  sources it when `RESTIC_REPOSITORY` is unset, and requires all four keys).
- Consumes: `stage_sqlite`, `STAGING_DIR`.

- [ ] **Step 1: Write the failing tests**

```python
def test_stream_runs_restic_as_the_host_with_its_stream_tag(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode == 0, out.stderr
    argv = (tmp_path / "restic-argv").read_text()
    assert "backup" in argv
    assert "--host rbp0" in argv
    assert "--tag stream=edge-rbp0" in argv
    assert "--retry-lock 2h" in argv
    assert str(root / "var/lib/pi-backup/tree") in argv


def test_stream_fails_when_the_restic_run_fails(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path, exit_code=1))
    assert out.returncode != 0


def test_stream_refuses_without_the_env_file(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    (root / "etc/restic/edge.env").unlink()
    out = run("stream", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode != 0
    assert "/etc/restic/edge.env" in out.stderr
```

`_pi_fixture` must now write the env file (add to the helper):

```python
env_file = root / "etc/restic/edge.env"
env_file.parent.mkdir(parents=True, exist_ok=True)
env_file.write_text(
    "RESTIC_REPOSITORY=s3:https://example.invalid/bucket/edge-rbp0\n"
    "RESTIC_PASSWORD=test\nAWS_ACCESS_KEY_ID=test\nAWS_SECRET_ACCESS_KEY=test\n"
)
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL — no restic invocation, no refusal.

- [ ] **Step 3: Implement**

`require_backup_env`:
- `[ -f "$ROOT$BACKUP_ENV" ] || { echo "... is missing ..."; exit 1; }`
- on a real host (`[ -z "$ROOT" ]`), `stat -c %a` must be `600`, else refuse.
- source it with `set -a; . "$ROOT$BACKUP_ENV"; set +a` when `RESTIC_REPOSITORY` is unset; require the
  four variables with `${VAR:?}`.

In `cmd_stream`, after `stage_sqlite`:
```bash
require_backup_env
"$RESTIC_BIN" backup \
    --retry-lock 2h \
    --host "$host" \
    --tag "stream=edge-$host" \
    "$ROOT$STAGING_DIR/tree"
```
and keep `rm -rf` after it only when the run succeeded (`&&` chain), so a failed run leaves the tree
for the next look — state that in a comment.

- [ ] **Step 4: Run the tests**

Run: `... -k "stream"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "🚀 feat(pi): run restic with the host's stream, refusing without the credential"
```

---

### Task 4: `prune` and the retention numbers

**Files:**
- Modify: `ansible/pi/hosts-pi.sh` (constants, `cmd_prune`, dispatch, `usage`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Produces: `RETENTION_DAILY=7`, `RETENTION_WEEKLY=4`, `RETENTION_MONTHLY=12`, `cmd_prune <host>`.

- [ ] **Step 1: Write the failing tests**

```python
def test_prune_forgets_with_the_charts_retention(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    out = run("prune", "rbp0", root=root, env=fake_restic(tmp_path))
    assert out.returncode == 0, out.stderr
    argv = (tmp_path / "restic-argv").read_text()
    for flag in ("forget", "--keep-daily 7", "--keep-weekly 4", "--keep-monthly 12", "--prune",
                 "--retry-lock 2h"):
        assert flag in argv, flag


def test_the_pi_retention_matches_the_chart():
    import yaml
    values = yaml.safe_load((REPO / "cluster/helm/backup/values.yaml").read_text())["retention"]
    out = subprocess.run(
        ["bash", "-c", f'. "{SCRIPT}"; echo "$RETENTION_DAILY $RETENTION_WEEKLY $RETENTION_MONTHLY"'],
        capture_output=True, text=True,
    )
    assert out.stdout.split() == [str(values["keepDaily"]), str(values["keepWeekly"]),
                                  str(values["keepMonthly"])]
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL — `unknown verb 'prune'`.

- [ ] **Step 3: Implement**

`cmd_prune <host>`: same root and env requirements as `stream`, then
`"$RESTIC_BIN" forget --retry-lock 2h --keep-daily $RETENTION_DAILY --keep-weekly $RETENTION_WEEKLY
--keep-monthly $RETENTION_MONTHLY --prune`. Dispatch in `main`; `usage` gains it. A comment says the
numbers are the chart's shared policy, checked by the test above.

- [ ] **Step 4: Run the tests**

Run: `... -k "prune or retention"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "🚀 feat(pi): prune each host's own repository with the chart's retention"
```

---

### Task 5: `setup` installs the pinned restic, the units, and retires the legacy job

**Files:**
- Modify: `ansible/pi/hosts-pi.sh` (`RESTIC_VERSION`, `RESTIC_SHA256`, `desired_backup_service`, `desired_backup_timer`, `desired_prune_service`, `desired_prune_timer`, `install_restic`, `retire_legacy_backup`, `cmd_setup`, `cmd_render`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Produces: the two unit pairs, `retire_legacy_backup`, and a `setup` that leaves every fixture-only
  action printed rather than performed.

- [ ] **Step 1: Write the failing tests**

```python
def test_setup_writes_the_backup_units_for_its_host(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    assert run("setup", "rbp1", root=root).returncode == 0
    service = (root / "etc/systemd/system/pi-backup.service").read_text()
    assert "ExecStart=/usr/local/sbin/hosts-pi.sh stream rbp1" in service
    assert "EnvironmentFile=/etc/restic/edge.env" in service
    timer = (root / "etc/systemd/system/pi-backup.timer").read_text()
    assert "OnCalendar=*-*-* 04:30:00" in timer
    assert "RandomizedDelaySec=15min" in timer
    assert "Persistent=true" in timer
    prune = (root / "etc/systemd/system/pi-backup-prune.timer").read_text()
    assert "OnCalendar=Sun *-*-* 05:30:00" in prune


def test_setup_installs_the_script_where_root_will_run_it(tmp_path):
    ...
    installed = root / "usr/local/sbin/hosts-pi.sh"
    assert installed.exists() and (installed.stat().st_mode & 0o777) == 0o555
    assert installed.read_text() == SCRIPT.read_text()


def test_setup_retires_the_legacy_cron_and_its_dead_keys(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    cron = root / "var/spool/cron/crontabs/root"
    cron.parent.mkdir(parents=True)
    cron.write_text("0 4 * * * /home/vronteix/scripts/backup.sh\nMAILTO=root\n")
    env = root / "etc/environment"
    env.write_text("PATH=/usr/bin:/bin\nRESTIC_PASSWORD=x\nAWS_ACCESS_KEY_ID=x\n"
                   "AWS_SECRET_ACCESS_KEY=x\nRESTIC_REPOSITORY=x\n")
    assert run("setup", "rbp1", root=root).returncode == 0
    assert "backup.sh" not in cron.read_text()
    assert "MAILTO=root" in cron.read_text()
    assert env.read_text() == "PATH=/usr/bin:/bin\n"


def test_setup_converges_a_host_that_never_had_the_legacy_job(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)  # no crontab, no /etc/environment additions
    assert run("setup", "rbp0", root=root).returncode == 0


def test_setup_is_idempotent_for_the_retirement(tmp_path):
    ...
    assert run("setup", "rbp1", root=root).returncode == 0  # twice, no failure, same text


def test_setup_installs_nothing_and_touches_no_service_under_pi_root(tmp_path):
    """A developer machine must not gain a restic binary or a systemd unit."""
    root = tmp_path / "pi"
    _pi_fixture(root)
    out = run("setup", "rbp0", root=root)
    assert out.returncode == 0, out.stderr
    assert "restic: not installed under PI_ROOT" in out.stdout
    assert "systemd: not touched under PI_ROOT" in out.stdout


def test_render_prints_the_units_it_would_write(tmp_path):
    out = run("render", "rbp0")
    assert out.returncode == 0, out.stderr
    assert "--- /etc/systemd/system/pi-backup.service" in out.stdout
    assert "stream rbp0" in out.stdout
    assert "0.19.1" in out.stdout
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL — no unit files.

- [ ] **Step 3: Implement**

- `RESTIC_VERSION="0.19.1"`; `RESTIC_SHA256` from the upstream `SHA256SUMS`:
  `curl -fsSL https://github.com/restic/restic/releases/download/v0.19.1/SHA256SUMS | grep linux_arm64`
  — put the 64-hex value in the constant, and add a test asserting it matches `^[0-9a-f]{64}$`.
- `install_restic`: on a real host only. If `/usr/local/bin/restic version` already reports
  `restic $RESTIC_VERSION`, print `restic: already $RESTIC_VERSION` and return. Otherwise download
  the `.bz2` to a temporary file, `sha256sum -c`, `bunzip2 -c > /usr/local/bin/restic.tmp`, `chmod
  0555`, `mv`. Under `PI_ROOT`, print `restic: not installed under PI_ROOT` and return.
- `desired_backup_service <host>`: the unit text with `ExecStart=/usr/local/sbin/hosts-pi.sh stream
  <host>`, `EnvironmentFile=$BACKUP_ENV`, `Type=oneshot`, `Nice=10`, `TimeoutStartSec=1h`.
  `desired_backup_timer`: `OnCalendar=*-*-* 04:30:00`, `RandomizedDelaySec=15min`, `Persistent=true`,
  `WantedBy=timers.target`. `desired_prune_service <host>` (`prune <host>`), `desired_prune_timer`
  (`OnCalendar=Sun *-*-* 05:30:00`, `Persistent=true`).
- `cmd_setup`: `install -D -m 0555 "$HERE/hosts-pi.sh" "$ROOT/usr/local/sbin/hosts-pi.sh"`, write the
  four unit files with `write_abs`, then `retire_legacy_backup`; on a real host `systemctl
  daemon-reload`, `systemctl enable --now pi-backup.timer pi-backup-prune.timer`; under `PI_ROOT`
  print `systemd: not touched under PI_ROOT`. `cmd_render` prints the four files and the install line.
- `retire_legacy_backup`: rewrite `$ROOT/var/spool/cron/crontabs/root` without lines matching
  `LEGACY_CRON_MATCH` (keep the rest, and an empty result stays an empty file); drop the four
  `LEGACY_ENV_KEYS` lines from `$ROOT/etc/environment`, keeping every other line; both steps are
  no-ops when the file does not exist, and both print a line only when they change something.
- `cmd_verify` (Task 6) checks them; this task's tests only cover setup.

- [ ] **Step 4: Run the tests**

Run: `... -k "setup"`
Expected: PASS, including the existing setup tests.

- [ ] **Step 5: Commit**

```bash
git commit -am "🚀 feat(pi): install the pinned restic, the backup timers and retire the dead cron"
```

---

### Task 6: `verify` names any drift in the new pieces

**Files:**
- Modify: `ansible/pi/hosts-pi.sh` (`cmd_verify`)
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: `desired_backup_service`, `desired_backup_timer`, `desired_prune_service`, `desired_prune_timer`, `BACKUP_ENV`, `LEGACY_CRON_MATCH`, `LEGACY_ENV_KEYS`.

- [ ] **Step 1: Write the failing tests**

```python
def test_verify_names_each_new_file_that_differs(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    run("setup", "rbp0", root=root)
    (root / "etc/systemd/system/pi-backup.timer").write_text("[Timer]\nOnCalendar=daily\n")
    out = run("verify", "rbp0", root=root)
    assert out.returncode == 1
    assert "pi-backup.timer" in out.stdout


def test_verify_names_a_missing_env_file_and_a_legacy_cron(tmp_path):
    root = tmp_path / "pi"
    _pi_fixture(root)
    run("setup", "rbp1", root=root)
    (root / "etc/restic/edge.env").unlink()
    cron = root / "var/spool/cron/crontabs/root"
    cron.parent.mkdir(parents=True, exist_ok=True)
    cron.write_text("0 4 * * * /home/vronteix/scripts/backup.sh\n")
    out = run("verify", "rbp1", root=root)
    assert out.returncode == 1
    assert "/etc/restic/edge.env" in out.stdout
    assert "scripts/backup.sh" in out.stdout
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL — verify does not mention them.

- [ ] **Step 3: Implement**

Extend `cmd_verify` with the same `check_text` shape it already uses for the other files: the four
units, the installed script (compare bytes with `$HERE/hosts-pi.sh`), `$BACKUP_ENV` present (and 0600
on a real host), `python3` on `PATH`, no `LEGACY_CRON_MATCH` in the root crontab, no
`LEGACY_ENV_KEYS` in `/etc/environment`. Under `PI_ROOT`, print
`systemd: the timers are not checked under PI_ROOT` (same wording as the engine note) rather than
asking `systemctl`.

- [ ] **Step 4: Run the tests**

Run: `... -k verify`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "🚀 feat(pi): have verify name drift in the timer, the key and the retired cron"
```

---

### Task 7: the chart verifies the two external streams and never prunes them

**Files:**
- Modify: `cluster/helm/backup/values.yaml`, `cluster/helm/backup/templates/configmap-streams.yaml`, `cluster/helm/backup/templates/configmap-scripts.yaml` (prune), `cluster/helm/backup/files/verify.py`, `cluster/helm/backup/README.md`
- Test: `hack/tests/test_backup_chart.py`, `hack/tests/test_backup_verify.py`

**Interfaces:**
- Produces: values rows `edge-rbp0`/`edge-rbp1` with `external: true`, `budgetGiB: 1`, `mustMatch`; `streams.json` entries with `external` and `mustMatch`; `check_external(name, must_match, entries) -> tuple[list[str], list[str]]`.

- [ ] **Step 1: Write the failing tests**

`hack/tests/test_backup_chart.py` — update the two existing assertions that count every stream, so
the intent stays visible:

```python
    def test_every_repository_is_checked(self):
        cms = {d["metadata"]["name"]: d["data"] for d in render() if d["kind"] == "ConfigMap"}
        streams = json.loads(cms["backup-streams"]["streams.json"])
        self.assertEqual({s["name"] for s in streams},
                         {"nodes-nas", "nodes-nuc0", "nodes-streaming", "nfs",
                          "edge-rbp0", "edge-rbp1"})
        edge = next(s for s in streams if s["name"] == "edge-rbp0")
        self.assertTrue(edge["external"])
        self.assertEqual(edge["mustMatch"], ["dockers/etc-pihole/gravity.db",
                                             "dockers/config/wg-easy.db"])
```

and in `PruneTests`:

```python
    def test_the_prune_leaves_the_pi_streams_to_their_hosts(self):
        script = scripts()["prune.sh"]
        self.assertNotIn("edge-rbp0", script)
        self.assertNotIn("edge-rbp1", script)
        # the count-based assertions above follow the non-external streams
        external = [s for s in values()["streams"] if s.get("external")]
        self.assertEqual(len(external), 2)
```

Update `test_prune_finishes_every_repository_before_failing` and
`test_prune_runs_one_repository_at_a_time` to count `[s for s in values()["streams"] if not
s.get("external")]` instead of `values()["streams"]`.

`hack/tests/test_backup_verify.py`:

```python
    def test_an_external_stream_must_hold_its_required_files(self):
        entries = [{"path": "/var/lib/pi-backup/tree/dockers/etc-pihole/gravity.db"},
                   {"path": "/var/lib/pi-backup/tree/dockers/config/wg-easy.db"}]
        lines, problems = load().check_external(
            "edge-rbp0", ["dockers/etc-pihole/gravity.db", "dockers/config/wg-easy.db"], entries)
        self.assertEqual(problems, [])
        self.assertEqual(lines, ["files stream=edge-rbp0 path=dockers/etc-pihole/gravity.db matched=1",
                                 "files stream=edge-rbp0 path=dockers/config/wg-easy.db matched=1"])

    def test_an_external_stream_missing_one_required_file_is_a_problem(self):
        entries = [{"path": "/var/lib/pi-backup/tree/dockers/etc-pihole/gravity.db"}]
        lines, problems = load().check_external(
            "edge-rbp0", ["dockers/etc-pihole/gravity.db", "dockers/config/wg-easy.db"], entries)
        self.assertEqual(problems, ["edge-rbp0 is missing dockers/config/wg-easy.db"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `... -k "external or prune or every_repository"`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `values.yaml`: two rows after `nfs`, with comments carrying the measured sizes (~60 MB per host:
  56 MB `gravity.db`, 97 KB `wg-easy.db`, 385 MB of query log excluded) and why `external`.
- `configmap-streams.yaml`: emit `"external": true` and `"mustMatch": [...]` for a stream that has
  them (`toJson` for the list).
- `verify.py`: `check_external(name, must_match, entries)` returning one `files stream=<name>
  path=<pattern> matched=<n>` line per pattern and a problem `f"{name} is missing {pattern}"` when
  `matched == 0`; match by `entry["path"].endswith("/" + pattern)`; wire it in `main` beside
  `check_staged`, and let the `read_snapshot_files` condition fire for `stream.get("mustMatch")` too.
  The lower bound is the point: freshness alone passes a night that copied a fragment.
- `prune.sh`: wrap the rendered block in `{{- if not .external }} … {{- end }}`; in the template,
  build `$pruned` as the streams without `external` and print its length in
  `<name>: daily …`'s count line, so the printed count matches the loop (4, not 6).
- `README.md`: a contract line: an `external` stream is written by a host outside the cluster; this
  chart reads it and never prunes it; its `mustMatch` paths are the lower bound.

- [ ] **Step 4: Run the tests**

Run: `... -k "backup"` and `./hack/check-charts.sh`
Expected: PASS, chart lints and renders.

- [ ] **Step 5: Commit**

```bash
git commit -am "🚀 feat(backup): verify the two edge streams and leave their pruning to the Pis"
```

---

### Task 8: documentation, the drill section and the handover

**Files:**
- Modify: `docs/how-it-works/backups.md`, `docs/runbooks/pi-reflash.md`, `docs/runbooks/restore.md`, `docs/secrets.md`, `docs/audit/ansible-hosts.md`, `cluster/helm/monitoring/prometheus/values.yaml`

**Interfaces:**
- Consumes: the numbers from the spec (60 MB per host, 385/26/57 MB exclusions, 04:30, 7/4/12).

- [ ] **Step 1: Write the pages**

- `docs/how-it-works/backups.md`: replace the "edge Pis' own state | deferred" row with the new
  section and the two streams; name the retired cron and the dead keys; name the three exclusions and
  what losing each costs; state that the cluster verifies and never prunes.
- `docs/runbooks/pi-reflash.md`: step 1 becomes "confirm last night's snapshot" with the restore
  command; the fresh-host step says the env file comes from the password manager before the first
  timer run; the rollback points at the repository.
- `docs/runbooks/restore.md`: section 5, the Pi restore, with the four pass criteria, and the
  rotation line becomes 1–5.
- `docs/secrets.md`: `/etc/restic/edge.env` on both Pis (four keys, 0600, not a Kubernetes Secret),
  the scoped OVH user, the removal of the dead `/etc/environment` keys.
- `docs/audit/ansible-hosts.md`: the correction note from the spec.
- `cluster/helm/monitoring/prometheus/values.yaml`: a `restoreDrills` entry `{stream: edge-rbp0,
  drill: pi-restore, at: <epoch>}` — **the timestamp is the operator's drill result**, given before
  the PR merges; leave the entry out until it exists and say so in the PR body.

- [ ] **Step 2: Run the documentation checks**

Run: `./hack/check-docs.sh --base origin/develop`
Expected: exit 0 (or only the staleness report, which never fails).

- [ ] **Step 3: Run the whole suite**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
Expected: 0 failures.

- [ ] **Step 4: Commit and hand over**

```bash
git add docs cluster/helm/monitoring/prometheus/values.yaml
git commit -m "📝 docs(backup): document the edge streams, their exclusions and the restore drill"
git fetch origin && git rebase origin/develop && git push origin feature/edge-pi-backups
```

Open the PR against `develop` with a body that lists: the measured sizes, the retired cron and its
dead keys, the operator steps below in order, and the fact that the chart rows must land after
`restic init`. Then move card 862 to `Validation` with `percent_done` 1.0 and the handover comment.

---

## Operator steps, in order (before the merge)

1. `infra-main/ovh/backup.tf`: a second user + `s3_credential` + `s3_policy` over
   `ashudev-homelab-backup/edge-rbp0/*` and `.../edge-rbp1/*` (ListBucket on the bucket, as the
   existing policy has it); apply; `terraform output`.
2. Copy `ansible/` to each Pi as the reflash runbook does; run `setup`; write `/etc/restic/edge.env`
   (0600 root) with the bucket password and the scoped pair; store the file verbatim as a Bitwarden
   secure note (`bw unlock` first — the vault is locked).
3. `restic init` both repositories; run `hosts-pi.sh stream <host>` once on each; run
   `hosts-pi.sh prune <host>` once.
4. Run the restore drill (restore.md section 5) and report its timestamp for the `restoreDrills` row.
5. Add `edge-rbp0`/`edge-rbp1` to the Grafana stale-backup rule if it names streams one by one.
6. Reboot-test one Pi after the first night: `systemctl list-timers pi-backup*` and
   `journalctl -u pi-backup` show the last run.
