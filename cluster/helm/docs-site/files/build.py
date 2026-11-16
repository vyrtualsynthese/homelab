#!/usr/bin/env python3
"""Discover repositories, notice which branch heads moved, and fetch what moved.

The builder runs in a CronJob every minute inside the `squidfunk/mkdocs-material`
image: Python and MkDocs, but no `git`, no `jq`, no `curl`. Everything below is
therefore the standard library.

The cost of a poll is the request, so every (repository, ref) pair keeps the ETag
of the last answer and sends it back as `If-None-Match`. A `304 Not Modified`
means "nothing moved" and is not counted against the GitHub rate limit; that is
the whole reason this can run once a minute.

This is part one of the builder: discovery, the conditional head check and the
tarball fetch. A pair selected for a build is downloaded and unpacked, then
discarded. Part two replaces that step with an MkDocs build into a staging
directory.

Environment:

    GITHUB_TOKEN       required; fine-grained token with Contents: read
    REPO_OWNER         default vyrtualsynthese
    REFS               default main,develop
    SITE_ROOT          default /data/site
    STATE              default /data/state.json
    FULL_REBUILD_AGE   default 86400, in seconds

Usage:

    build.py --dry-run          print the plan, write nothing
    build.py                    run for real: fetch what moved, persist the state
    build.py --full             ignore the age check
    build.py --repo homelab     restrict the run to one repository

The token is never printed and never written to a file. Any message that could
carry it is scrubbed before it reaches a log line or the state file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com"
CODELOAD_ROOT = "https://codeload.github.com"
USER_AGENT = "docs-site-builder"
API_VERSION = "2022-11-28"

# Repository names come from the API and become directory names, so they are
# checked before any path is built out of one. `.` and `..` match the pattern
# but are not names anybody can fetch, and they are the ones that escape.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

JSON_TIMEOUT = 30  # seconds, one small API answer
DOWNLOAD_TIMEOUT = 300  # seconds, a whole tarball
MAX_LIST_PAGES = 50  # 50 pages of 100 repositories; more than this owner has
DETAIL_LIMIT = 400  # characters kept from an error, so the index stays readable

WORK_DIR_NAME = ".work"  # scratch under the site root, one subdirectory per run
# Leftovers younger than this belong to a run that may still be working. The
# CronJob's own deadline is 900s, so anything older than that is a corpse.
WORK_SWEEP_SECONDS = 900

ENV_DEFAULTS = {
    "REPO_OWNER": "vyrtualsynthese",
    "REFS": "main,develop",
    "SITE_ROOT": "/data/site",
    "STATE": "/data/state.json",
    "FULL_REBUILD_AGE": "86400",
}

# Filled in from response headers so a run can report what it spent.
RATE_LIMIT: dict[str, int] = {}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


class ConfigError(Exception):
    """The environment cannot produce a usable run."""


@dataclass(frozen=True)
class Settings:
    token: str
    owner: str
    refs: tuple[str, ...]
    site_root: Path
    state_path: Path
    full_rebuild_age: int


_SETTINGS: Settings | None = None
_RUN_ID: str | None = None


def check_name(kind: str, value: str) -> str:
    """Return `value` when it is safe to use in a path or a URL, else raise."""
    if not isinstance(value, str) or not NAME_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ConfigError(f"refusing unsafe {kind} name {value!r}")
    return value


def load_settings() -> Settings:
    """Read the environment once and cache the result."""
    global _SETTINGS
    if _SETTINGS is not None:
        return _SETTINGS

    env = {key: os.environ.get(key, default) for key, default in ENV_DEFAULTS.items()}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise ConfigError("GITHUB_TOKEN is empty or unset")

    owner = check_name("owner", env["REPO_OWNER"].strip())

    refs = tuple(part.strip() for part in env["REFS"].split(",") if part.strip())
    if not refs:
        raise ConfigError("REFS names no branch")
    for ref in refs:
        check_name("ref", ref)

    try:
        age = int(env["FULL_REBUILD_AGE"])
    except ValueError:
        raise ConfigError(f"FULL_REBUILD_AGE is not a number: {env['FULL_REBUILD_AGE']!r}") from None
    if age < 0:
        raise ConfigError("FULL_REBUILD_AGE is negative")

    _SETTINGS = Settings(
        token=token,
        owner=owner,
        refs=refs,
        site_root=Path(env["SITE_ROOT"]),
        state_path=Path(env["STATE"]),
        full_rebuild_age=age,
    )
    return _SETTINGS


def scrub(text: str) -> str:
    """Remove the token from anything about to be printed or stored."""
    # The token is stripped where it is read (`load_settings`), so the guard
    # must strip too: otherwise a token with surrounding whitespace in the
    # environment passes through every message untouched.
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return text.replace(token, "***")
    return text


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


class GithubError(Exception):
    """A GitHub call that did not answer with a status its caller expects."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def api_headers(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if extra:
        headers.update(extra)
    return headers


def _remember_rate_limit(headers: Any) -> None:
    if headers is None:
        return
    for header, key in (("X-RateLimit-Remaining", "remaining"), ("X-RateLimit-Limit", "limit")):
        value = headers.get(header)
        if value is None:
            continue
        try:
            RATE_LIMIT[key] = int(value)
        except ValueError:
            pass


def http_get(
    url: str, headers: dict[str, str], timeout: int = JSON_TIMEOUT
) -> tuple[int, Any, bytes]:
    """GET a URL and return (status, headers, body) instead of raising on 4xx.

    A transport failure (DNS, timeout, connection reset — all `OSError`) is
    raised as `GithubError(None, …)` rather than escaping: one unreachable
    moment must cost one (repo, ref) pair, not the whole run.
    """
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            _remember_rate_limit(response.headers)
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        # HTTPError is an OSError, so it is caught before the broad clause.
        _remember_rate_limit(exc.headers)
        return exc.code, exc.headers, exc.read()
    except OSError as exc:
        raise GithubError(None, f"{url}: {type(exc).__name__}: {exc}") from None


def api_message(body: bytes, status: int) -> str:
    """Prefer GitHub's own `message` field; fall back to the status code."""
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return f"HTTP {status}"


# --------------------------------------------------------------------------- #
# Discovery and change detection
# --------------------------------------------------------------------------- #


def authenticated_login(token: str) -> str | None:
    """The login the token acts as, or None when GitHub will not say."""
    status, _, body = http_get(f"{API_ROOT}/user", api_headers(token))
    if status != 200:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    login = payload.get("login") if isinstance(payload, dict) else None
    return login if isinstance(login, str) else None


def list_repos(owner: str) -> list[str]:
    """Every non-archived repository owned by `owner` that the token can see.

    `/users/{owner}/repos` answers with public repositories only, even when the
    caller is that user, so a private repository would silently vanish from the
    site. When the token acts as the owner, the authenticated catalogue is used
    instead: it carries the private repositories and is still restricted to
    repositories this owner owns (`affiliation=owner`).
    """
    settings = load_settings()
    names: list[str] = []
    seen: set[str] = set()

    login = authenticated_login(settings.token)
    # GitHub logins are case-insensitive, so `REPO_OWNER=VyrtualSynthèse`-style
    # spelling must not fall back to the public-only catalogue.
    if login is not None and login.casefold() == owner.casefold():
        catalogue = f"{API_ROOT}/user/repos?per_page=100&affiliation=owner&visibility=all"
    else:
        catalogue = f"{API_ROOT}/users/{owner}/repos?per_page=100&type=owner"

    for page in range(1, MAX_LIST_PAGES + 1):
        url = f"{catalogue}&page={page}"
        status, _, body = http_get(url, api_headers(settings.token))
        if status == 404:
            raise GithubError(status, f"owner {owner!r} not found")
        if status != 200:
            raise GithubError(status, f"repository listing page {page}: {api_message(body, status)}")

        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            raise GithubError(status, f"repository listing page {page}: not JSON") from None
        if not isinstance(payload, list):
            raise GithubError(status, f"repository listing page {page}: unexpected JSON shape")
        if not payload:
            break

        for item in payload:
            if not isinstance(item, dict) or item.get("archived"):
                continue
            name = item.get("name")
            if not isinstance(name, str):
                continue
            try:
                check_name("repository", name)
            except ConfigError:
                print(f"warning: skipping repository with unusable name {name!r}", file=sys.stderr)
                continue
            if name not in seen:
                seen.add(name)
                names.append(name)
    else:
        raise GithubError(None, f"repository listing did not end within {MAX_LIST_PAGES} pages")

    return sorted(names)


def probe_repo(repo: str) -> bool:
    """True when the token may read `repo`, False when GitHub refuses it.

    A repository the token cannot read answers 404 and is indistinguishable from
    one that does not exist; a 403 means the same thing for a fine-grained token
    without `Metadata: read`. Both are recorded as "not readable by the token".
    """
    settings = load_settings()
    check_name("repository", repo)
    url = f"{API_ROOT}/repos/{settings.owner}/{repo}"
    status, _, body = http_get(url, api_headers(settings.token))
    if status == 200:
        return True
    if status in (403, 404):
        return False
    raise GithubError(status, f"{repo}: repository probe: {api_message(body, status)}")


def head_sha(repo: str, ref: str, etag: str | None) -> tuple[str | None, str | None, str | None]:
    """Ask GitHub for the head of `ref`, conditionally.

    Returns `(sha, new_etag, status)` with status one of:

        "changed"    HTTP 200; `sha` is the head now
        "unchanged"  HTTP 304; nothing moved and no rate-limit budget was spent
        "absent"     no such branch; the ref does not exist yet

    GitHub answers a ref it cannot resolve with **422 "No commit found for SHA:
    <ref>"**, not 404 — measured against the live API on 2026-11-16. A genuinely
    missing repository is the 404, and an empty one is 409. All three mean the
    same thing to the caller: there is no head to build.

    Anything else raises `GithubError`, because a 403 from a token problem or a
    5xx is not something the caller can interpret as a branch state.
    """
    settings = load_settings()
    check_name("repository", repo)
    check_name("ref", ref)

    url = f"{API_ROOT}/repos/{settings.owner}/{repo}/commits/{ref}"
    extra = {"If-None-Match": etag} if etag else None
    status, headers, body = http_get(url, api_headers(settings.token, extra))

    if status == 304:
        return None, etag, "unchanged"
    if status in (404, 409):
        return None, None, "absent"
    if status == 422:
        message = api_message(body, status).lower()
        if "no commit found" in message or "repository is empty" in message:
            return None, None, "absent"
        raise GithubError(status, f"{repo}/{ref}: {api_message(body, status)}")
    if status != 200:
        raise GithubError(status, f"{repo}/{ref}: {api_message(body, status)}")

    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        raise GithubError(status, f"{repo}/{ref}: commit response is not JSON") from None
    sha = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not sha:
        raise GithubError(status, f"{repo}/{ref}: commit response carries no sha")

    new_etag = headers.get("ETag") if headers is not None else None
    return sha, new_etag, "changed"


# --------------------------------------------------------------------------- #
# Tarball fetch
# --------------------------------------------------------------------------- #


def _checked_members(tar: tarfile.TarFile, destination: Path) -> list[tarfile.TarInfo]:
    """Every member, after refusing links and paths that leave `destination`."""
    root = destination.resolve()
    members: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        if member.islnk() or member.issym():
            raise GithubError(None, f"refusing link member {member.name!r} in tarball")
        target = (destination / member.name).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise GithubError(None, f"refusing out-of-tree member {member.name!r} in tarball") from None
        members.append(member)
    return members


def fetch_tarball(repo: str, ref: str, workdir: Path) -> Path:
    """Download and unpack one (repo, ref); return the directory holding the root.

    Works on private repositories: codeload accepts the same bearer token as the
    API, and it is used directly so there is no redirect to reason about.
    """
    settings = load_settings()
    check_name("repository", repo)
    check_name("ref", ref)

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    unpack = Path(tempfile.mkdtemp(prefix=f".{repo}__{ref}.", dir=workdir))
    handle, archive_name = tempfile.mkstemp(prefix=f".{repo}__{ref}.", suffix=".tar.gz", dir=workdir)
    os.close(handle)
    archive = Path(archive_name)

    try:
        url = f"{CODELOAD_ROOT}/{settings.owner}/{repo}/tar.gz/refs/heads/{ref}"
        headers = api_headers(settings.token, {"Accept": "application/x-gzip"})
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
                _remember_rate_limit(response.headers)
                with archive.open("wb") as sink:
                    shutil.copyfileobj(response, sink)
        except urllib.error.HTTPError as exc:
            raise GithubError(
                exc.code, f"{repo}/{ref}: tarball download: {api_message(exc.read(), exc.code)}"
            ) from None

        with tarfile.open(archive, "r:gz") as tar:
            members = _checked_members(tar, unpack)
            try:
                tar.extractall(unpack, members=members, filter="data")
            except TypeError:  # Python < 3.12 has no extraction filter
                tar.extractall(unpack, members=members)

        entries = list(unpack.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise GithubError(None, f"{repo}/{ref}: tarball did not hold a single top directory")
        return entries[0]
    except BaseException:
        shutil.rmtree(unpack, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

def entry_digest(sha: str | None, etag: str | None) -> str:
    """A short fingerprint binding a stored sha to the ETag it came with.

    A `304` proves the head still matches the ETag, but it does not carry the
    sha, so the run has to trust the sha it recorded earlier. The digest makes
    that trust checkable: when the two no longer agree the entry is not the one
    this ETag was issued for, and it is re-read unconditionally instead of being
    skipped on it. This guards a torn or hand-edited state file, not somebody
    deliberately rewriting both fields.
    """
    material = f"{sha or ''}\n{etag or ''}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def empty_state() -> dict[str, Any]:
    return {"refs": {}, "last_full": None, "invisible": []}


def clean_state(raw: Any) -> dict[str, Any]:
    """Accept an old or partly written state file without trusting its shape.

    Names are checked here, not only where they are used. A hand-edited key such
    as `/main`, or a bogus string in `invisible`, would otherwise raise
    `ConfigError` while the plan is built — before `save_state` runs — so the
    offending entry would never be rewritten and every future run would die the
    same way. One bad entry costs that entry.
    """
    state = empty_state()
    if not isinstance(raw, dict):
        return state

    refs = raw.get("refs")
    if isinstance(refs, dict):
        for key, entry in refs.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            repo, _, ref = key.partition("/")
            try:
                check_name("repository", repo)
                check_name("ref", ref)
            except ConfigError as exc:
                print(f"warning: dropping state entry {key!r} ({exc})", file=sys.stderr)
                continue
            state["refs"][key] = {
                "sha": entry.get("sha") if isinstance(entry.get("sha"), str) else None,
                "etag": entry.get("etag") if isinstance(entry.get("etag"), str) else None,
                "digest": entry.get("digest") if isinstance(entry.get("digest"), str) else None,
                "built_at": entry.get("built_at") if isinstance(entry.get("built_at"), str) else None,
                "status": entry.get("status") if entry.get("status") in {"ok", "absent", "failed"} else "failed",
                "detail": entry.get("detail") if isinstance(entry.get("detail"), str) else "",
            }

    if isinstance(raw.get("last_full"), str):
        state["last_full"] = raw["last_full"]

    invisible = raw.get("invisible")
    if isinstance(invisible, list):
        kept: set[str] = set()
        for name in invisible:
            if not isinstance(name, str):
                continue
            try:
                check_name("repository", name)
            except ConfigError as exc:
                print(f"warning: dropping invisible entry {name!r} ({exc})", file=sys.stderr)
                continue
            kept.add(name)
        state["invisible"] = sorted(kept)

    return state


def load_state(path: Path) -> dict[str, Any]:
    try:
        return clean_state(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as exc:
        print(f"warning: state file unusable, starting fresh ({scrub(str(exc))})", file=sys.stderr)
        return empty_state()


def run_id() -> str:
    """A per-process namespace: two runs never share a work or temp path."""
    global _RUN_ID
    if _RUN_ID is None:
        _RUN_ID = f"{os.getpid()}-{os.urandom(4).hex()}"
    return _RUN_ID


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Write through a temporary file so a half-written state never exists.

    The temporary is named after the run, so two runs writing at the same moment
    each finish a complete JSON document and the last `os.replace` wins; neither
    can observe or truncate the other's half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{run_id()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sweep_stale(settings: Settings, max_age: int = WORK_SWEEP_SECONDS) -> None:
    """Delete work trees and state temporaries left by runs that are gone.

    A run killed mid-download would otherwise leave a full checkout of a private
    repository on the volume for good. Only entries older than `max_age` are
    removed, so a concurrent run — which is younger than any run's deadline — is
    never touched.
    """
    cutoff = time.time() - max_age

    work_root = settings.site_root / WORK_DIR_NAME
    try:
        entries = list(work_root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            if entry.lstat().st_mtime >= cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except OSError:
            continue

    try:
        temporaries = list(settings.state_path.parent.glob(f"{settings.state_path.name}.*.tmp"))
    except OSError:
        temporaries = []
    for entry in temporaries:
        try:
            if entry.lstat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            continue


def repo_of(key: str) -> str:
    return key.split("/", 1)[0]


def full_rebuild_due(state: dict[str, Any], age: int, forced: bool) -> bool:
    if forced:
        return True
    last = state.get("last_full")
    if not isinstance(last, str):
        return True
    try:
        moment = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds() > age


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Decision:
    repo: str
    ref: str
    action: str  # "build" | "skip" | "absent" | "failed"
    sha: str | None = None
    etag: str | None = None
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.repo}/{self.ref}"

    def line(self) -> str:
        if self.action == "build":
            return f"build {self.key} {self.sha}"
        if self.action == "failed":
            return f"failed {self.key} {self.detail}"
        return f"{self.action} {self.key}"


def decide(
    repo: str, ref: str, stored: dict[str, Any], full: bool
) -> Decision:
    """One conditional head check, turned into one action."""
    # `If-None-Match` is only worth sending for an entry whose sha and etag were
    # recorded together: a 304 answers with nothing but "still the same", so a
    # sha that lost its etag would be handed straight back without ever being
    # compared to a head. An entry without a digest, or with a mismatched one,
    # is read unconditionally instead.
    trusted = bool(stored) and stored.get("digest") == entry_digest(
        stored.get("sha"), stored.get("etag")
    )
    try:
        sha, etag, status = head_sha(repo, ref, stored.get("etag") if trusted else None)
    except GithubError as exc:
        return Decision(repo, ref, "failed", detail=scrub(exc.message)[:DETAIL_LIMIT])

    if status == "absent":
        return Decision(repo, ref, "absent")

    if status == "unchanged":
        # 304: the head is the one already recorded, so the recorded sha stands.
        sha = stored.get("sha")

    rebuild = (
        full
        or not trusted
        or not isinstance(sha, str)
        or stored.get("sha") != sha
        or stored.get("status") in {"absent", "failed"}
    )
    return Decision(repo, ref, "build" if rebuild else "skip", sha=sha, etag=etag)


def collect_plan(repos: list[str], refs: tuple[str, ...], state: dict[str, Any], full: bool) -> list[Decision]:
    decisions: list[Decision] = []
    for repo in repos:
        for ref in refs:
            stored = state["refs"].get(f"{repo}/{ref}") or {}
            decisions.append(decide(repo, ref, stored, full))
    return decisions


def resolve_invisible(state: dict[str, Any], listed: list[str]) -> list[str]:
    """Repos we used to know that the listing no longer returns.

    The listing is filtered by the token's own visibility, so a repository the
    token lost access to simply disappears. Every name that disappeared is
    probed once: readable means it is still ours (a `type=owner` nuance), a
    refusal means it goes in `invisible` and the index can say so.
    """
    known = {repo_of(key) for key in state["refs"] if isinstance(key, str)}
    known.update(state.get("invisible") or [])
    vanished = sorted(known - set(listed))
    invisible: list[str] = []
    readable_again: list[str] = []

    for name in vanished:
        try:
            usable = probe_repo(name)
        except (GithubError, ConfigError) as exc:
            # A rate limit or an outage says nothing about the token's visibility,
            # so the previous classification is kept. An unusable name should
            # already have been dropped by `clean_state`; this is the second line
            # of defence, so it cannot end the run either.
            detail = getattr(exc, "message", None) or str(exc)
            print(f"warning: could not probe {name} ({scrub(detail)})", file=sys.stderr)
            if name in (state.get("invisible") or []):
                invisible.append(name)
            continue
        if usable:
            readable_again.append(name)
        else:
            invisible.append(name)

    state["invisible"] = sorted(invisible)
    return readable_again


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


def execute(decisions: list[Decision], state: dict[str, Any], settings: Settings) -> int:
    """Carry out the plan: fetch what was selected, then persist the state.

    Part one has nothing to render, so a successful fetch is the whole job and
    is recorded as `ok`. Part two replaces the fetch with a build and keeps the
    same state shape.
    """
    # Every run unpacks under its own directory, so a run that overruns cannot
    # have its tree removed by the run that follows it.
    work_root = settings.site_root / WORK_DIR_NAME / run_id()

    for decision in decisions:
        key = decision.key
        stored = state["refs"].get(key) or {}

        if decision.action == "absent":
            state["refs"][key] = {
                "sha": None,
                "etag": None,
                "digest": entry_digest(None, None),
                "built_at": stored.get("built_at"),
                "status": "absent",
                "detail": "no such branch",
            }
            continue

        if decision.action != "build":
            # A skip leaves the entry untouched: nothing was asked of it.
            continue

        pair_work = work_root / f"{decision.repo}__{decision.ref}"
        try:
            fetched = fetch_tarball(decision.repo, decision.ref, pair_work)
            detail = f"fetched {fetched.name}"
            state["refs"][key] = {
                "sha": decision.sha,
                "etag": decision.etag,
                "digest": entry_digest(decision.sha, decision.etag),
                "built_at": now_iso(),
                "status": "ok",
                "detail": detail,
            }
        except Exception as exc:  # noqa: BLE001 - one repository never stops the run
            # The previous sha and etag stay, so the next run sees a difference
            # and tries again instead of accepting a head it never built.
            state["refs"][key] = {
                "sha": stored.get("sha"),
                "etag": stored.get("etag"),
                "digest": entry_digest(stored.get("sha"), stored.get("etag")),
                "built_at": stored.get("built_at"),
                "status": "failed",
                "detail": scrub(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT],
            }
            # A 403 or 404 from codeload is the repository talking, not the ref:
            # the head check already ruled the ref out. The index says so
            # instead of showing a hole.
            if isinstance(exc, GithubError) and exc.status in (403, 404):
                state["invisible"] = sorted(set(state.get("invisible") or []) | {decision.repo})
        finally:
            shutil.rmtree(pair_work, ignore_errors=True)
            try:
                work_root.rmdir()
            except OSError:
                pass  # another pair in this same run still has work under it

    try:
        save_state(settings.state_path, state)
    except OSError as exc:
        print(f"error: could not write state ({scrub(str(exc))})", file=sys.stderr)
        return 1

    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover repositories and rebuild what moved.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")
    parser.add_argument("--full", action="store_true", help="ignore FULL_REBUILD_AGE")
    parser.add_argument("--repo", metavar="NAME", help="restrict the run to one repository")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.repo is not None:
        try:
            repos = [check_name("repository", args.repo)]
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        whole_catalogue = False
    else:
        try:
            repos = list_repos(settings.owner)
        except GithubError as exc:
            print(f"error: repository listing failed: {scrub(exc.message)}", file=sys.stderr)
            return 1
        whole_catalogue = True

    if not args.dry_run:
        # A dry run writes nothing, so it does not sweep either. A real run
        # clears what a killed predecessor left behind before unpacking anything.
        sweep_stale(settings)

    state = load_state(settings.state_path)

    if not args.repo:
        readable_again = resolve_invisible(state, repos)
        if readable_again:
            repos = sorted(set(repos) | set(readable_again))

    forced = args.full
    full = full_rebuild_due(state, settings.full_rebuild_age, forced)

    if full:
        reason = "--full" if forced else "FULL_REBUILD_AGE elapsed or no previous full run"
        print(f"# full rebuild: {reason}")

    decisions = collect_plan(repos, settings.refs, state, full)
    for decision in decisions:
        print(decision.line())

    if args.dry_run:
        # Nothing is written: the state records what was built, and a dry run
        # builds nothing, so recording it would make the next real run skip.
        print(f"# dry run: {len(repos)} repositories, nothing written")
        return 0

    if full:
        # A --repo run covers one repository, so it cannot speak for the catalogue.
        if whole_catalogue:
            state["last_full"] = now_iso()

    code = execute(decisions, state, settings)

    counts: dict[str, int] = {}
    for decision in decisions:
        counts[decision.action] = counts.get(decision.action, 0) + 1
    summary = ", ".join(f"{name} {counts[name]}" for name in ("build", "skip", "absent", "failed") if name in counts)
    spent = ""
    if "remaining" in RATE_LIMIT:
        spent = f", API calls left {RATE_LIMIT['remaining']}/{RATE_LIMIT.get('limit', '?')}"
    print(f"# {summary or 'nothing to do'}{spent}")
    return code


if __name__ == "__main__":
    sys.exit(main())
