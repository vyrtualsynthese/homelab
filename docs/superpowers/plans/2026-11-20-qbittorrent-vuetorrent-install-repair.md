# qBittorrent VueTorrent installer — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `init-qbittorrent` installer repair what it installs: a verified tree, the `AlternativeUIEnabled` preference it now leaves behind, and a cached zip for an offline retry.

**Architecture:** No new workload and no image. The whole change lives in the existing init container's shell script in `templates/deployment.yaml`, plus the values/README prose that describes it. A pytest module in `hack/tests/` renders the chart, extracts that script, and runs it against fixtures with stub `apk`/`wget`/`unzip`/`chown`, which is the only way to exercise it without a cluster.

**Tech Stack:** Helm 3 templates, POSIX `sh` + `awk`/`sed`, pytest, `helm template`.

**Spec:** `docs/superpowers/specs/2026-11-20-qbittorrent-vuetorrent-install-repair-design.md`

## Global Constraints

- `qBittorrent.conf` path: `/config/qBittorrent/qBittorrent.conf`.
- UI tree: `/config/ui/version.txt`, `/config/ui/public/index.html`; the released zip carries `vuetorrent/version.txt` and `vuetorrent/public/index.html`.
- The installer must never fail the pod: every failure path logs `WARNING` and exits 0, leaving `/config/ui` as it was.
- Preference name in the file is `WebUI\AlternativeUIEnabled` (literal backslash); the key lives under `[Preferences]`.
- No image tag changes, so the Renovate annotations in `values.yaml` stay untouched.
- Existing pinned images stay pinned: `alpine:{{ .Values.vueTorrent.initTag }}` is what the script runs in.

## Review Focus

- **A config file with no `[Preferences]` section** — the key must not be appended into `[BitTorrent]` or `[LegalNotice]`; a new section is written instead.
- **A deliberate operator toggle-off** — the installer writes the preference only after it installs, so a version that has not changed never re-enables the UI behind the operator's back.
- **A truncated zip** — must leave the previous UI and must not set the preference.
- **A read-only or full config claim** — every write path is guarded and ends in `give_up`, never in a non-zero exit.
- **Version equal but `public/index.html` gone** — the installer must reinstall rather than trust `version.txt`.

---

### Task 1: The installer script and its tests

**Files:**
- Create: `hack/tests/test_qbittorrent_vuetorrent_installer.py`
- Modify: `cluster/helm/media-stack/qbittorrent/templates/deployment.yaml:115-175`

**Interfaces:**
- Consumes: the rendered init container named `init-qbittorrent`, whose `command[2]` is the script; env `VUETORRENT_REPOSITORY`, `VUETORRENT_VERSION`, plus the new `PUID`, `PGID`.
- Produces: script constants the tests assert on — cache path `/config/vuetorrent-<version>.zip`, preference line `WebUI\AlternativeUIEnabled=true`.

- [ ] **Step 1: Write the test harness and the failing tests**

`hack/tests/test_qbittorrent_vuetorrent_installer.py` gets one helper that renders the chart and prepares a sandbox, and one test per behaviour:

```python
REPO = Path(__file__).resolve().parents[2]
CHART = REPO / "cluster/helm/media-stack/qbittorrent"
VERSION = "2.37.0"

def rendered_script() -> str:
    """helm template the chart, return init-qbittorrent's shell script."""

def sandbox(tmp_path, *, conf: str | None, zip_ok: bool, cached_zip: bool) -> dict:
    """Return env + cwd for running `script.replace('/config', str(tmp_path/'config'))`.

    stubs/ holds apk (exit 0), wget (copies a real zip into place, or exits 1),
    unzip (python3 -m zipfile -e), chown (appends its argv to chown.log).
    The zip is built with zipfile, carrying vuetorrent/version.txt and
    vuetorrent/public/index.html.
    """
```

Tests, all asserting `returncode == 0`:

| Test | Fixture | Assertion |
|---|---|---|
| `test_no_conf_is_created_with_pref` | no conf | conf contains a `[Preferences]` line and `WebUI\AlternativeUIEnabled=true` |
| `test_false_pref_is_flipped` | conf with `WebUI\AlternativeUIEnabled=false` | value is `true`, key count is 1 |
| `test_absent_key_lands_in_preferences` | `[Preferences]` then `[BitTorrent]`, no key | key sits between the two headers |
| `test_no_preferences_section_is_added` | only `[BitTorrent]` | a `[Preferences]` section exists carrying the key |
| `test_matching_version_with_index_is_a_noop` | version.txt + public/index.html present, wget stub writes `wget.called` | `wget.called` absent |
| `test_matching_version_without_index_reinstalls` | version.txt only | index.html present afterwards |
| `test_failed_download_uses_cached_zip` | no UI, `/config/vuetorrent-2.37.0.zip` present, wget exits 1 | index.html present |
| `test_failed_download_without_cache_keeps_ui` | no UI, wget exits 1 | `/config/ui` absent, stdout has `WARNING` |
| `test_truncated_zip_leaves_ui_and_pref` | working UI, zip without `public/index.html`, pref false | old `version.txt` intact, pref still `false`, `chown.log` empty or only touching unchanged files |
| `test_success_chowns_conf` | fresh conf | `chown.log` contains `1100:1100` and the conf path |
| `test_read_only_claim_exits_zero` | `/config/ui` chmod 500, no UI | exit 0, stdout has `WARNING`, the pod is never blocked |

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_qbittorrent_vuetorrent_installer.py`
Expected: FAIL — no `[Preferences]` line written, `PUID` missing from the init container, cache file never created.

- [ ] **Step 3: Edit the script**

In `templates/deployment.yaml`, give the init container `PUID`/`PGID` env from `.Values.env`, then in the script:

1. No-op branch: `if [ "$installed" = "$want" ] && [ -f /config/ui/public/index.html ]; then ... exit 0; fi`; when the version matches and the file does not, echo why and continue.
2. Define `cache="/config/vuetorrent-${want}.zip"` next to `url`.
3. Download step becomes: `wget -q -T 20 -O "$tmp/vuetorrent.zip" "$url"`; on failure, if `[ -f "$cache" ]` copy it into `$tmp/vuetorrent.zip` and log that the cache is used, otherwise `give_up`.
4. After `cp -a "$tmp/vuetorrent" /config/ui.next`, require `[ -f /config/ui.next/public/index.html ]` or `give_up` with the staging directory removed.
5. After the swap succeeds: `cp "$tmp/vuetorrent.zip" "$cache"` (before `rm -rf "$tmp"`), then `set_pref`.
6. `set_pref()` writes `WebUI\AlternativeUIEnabled=true` into `/config/qBittorrent/qBittorrent.conf`: create the file with a `[Preferences]` header when absent; replace the value with `sed` when the key exists; otherwise insert the key inside `[Preferences]` with `awk`, adding the section at the end when there is none; then `chown "$PUID:$PGID" "$conf"`. A failed write is a `WARNING`, never a failure.

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
Expected: PASS, and every pre-existing test still passes.

- [ ] **Step 5: Render the chart and re-read the live pod's contract**

Run: `helm template qbittorrent cluster/helm/media-stack/qbittorrent --namespace media-stack | sed -n '/init-qbittorrent/,/volumeMounts/p'`
Expected: the script renders with no `{{` left unresolved, `PUID`/`PGID` env present, and the failure text still says qBittorrent starts on its stock WebUI.

- [ ] **Step 6: Commit**

```bash
git add hack/tests/test_qbittorrent_vuetorrent_installer.py cluster/helm/media-stack/qbittorrent/templates/deployment.yaml
git commit -m "🐛 fix(qbittorrent): make the VueTorrent installer repair what it installs"
```

---

### Task 2: The prose that promises the behaviour

**Files:**
- Modify: `cluster/helm/media-stack/qbittorrent/README.md` (`## VueTorrent`, `## After a merge`)
- Modify: `cluster/helm/media-stack/qbittorrent/values.yaml:242-297` (the comment block above `vueTorrent`)

**Interfaces:**
- Consumes: what Task 1 made true.
- Produces: nothing code reads; the statements the next operator will act on.

- [ ] **Step 1: Replace the recovery-only story in both files**

State, in each place, only what the script does now: the installer reinstalls a tree that is missing `public/index.html`; it sets `WebUI\AlternativeUIEnabled=true` after an install and chowns the file to `PUID`/`PGID`; it keeps `/config/vuetorrent-<version>.zip` and falls back to it when GitHub is unreachable; a first boot with no network still serves the stock WebUI. Keep the existing `curl` recovery in `values.yaml` as the manual path for a UI deleted while the app runs, and say that is what it is now for.

- [ ] **Step 2: Check the docs links and the exposure script**

Run: `python3 hack/check_docs_links.py` and `./hack/check-exposure.sh`
Expected: both pass; no output naming the qbittorrent files.

- [ ] **Step 3: Commit**

```bash
git add cluster/helm/media-stack/qbittorrent/README.md cluster/helm/media-stack/qbittorrent/values.yaml
git commit -m "📝 docs(qbittorrent): the installer repairs its own fallback now"
```
