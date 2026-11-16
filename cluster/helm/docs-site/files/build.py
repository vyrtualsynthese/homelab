#!/usr/bin/env python3
"""Discover repositories, notice which branch heads moved, and publish their docs.

The builder runs in a CronJob every minute inside the `squidfunk/mkdocs-material`
image: Python and MkDocs, but no `git`, no `jq`, no `curl`. Everything below is
therefore the standard library.

The cost of a poll is the request, so every (repository, ref) pair keeps the ETag
of the last answer and sends it back as `If-None-Match`. A `304 Not Modified`
means "nothing moved" and is not counted against the GitHub rate limit; that is
the whole reason this can run once a minute.

This is the whole builder: discovery, the conditional head check, the tarball
fetch, and the MkDocs build that turns one checkout into one site. A pair is
downloaded, built into a staging directory outside the served tree, and only a
finished site is moved into `SITE_ROOT`.

Environment:

    GITHUB_TOKEN       required; fine-grained token with Contents: read
    REPO_OWNER         default vyrtualsynthese
    REFS               default main,develop
    SITE_ROOT          default /data/site; served by nginx, holds finished sites only
    WORK_ROOT          default <SITE_ROOT>/../work; checkouts and staging, never served
    STATE              default /data/state.json
    FULL_REBUILD_AGE   default 86400, in seconds

Usage:

    build.py --dry-run          print the plan, write nothing
    build.py                    run for real: fetch what moved, build it, persist the state
    build.py --full             ignore the age check
    build.py --repo homelab     restrict the run to one repository

Nothing transient is written under `SITE_ROOT`: nginx serves that directory as
its document root with no authentication and no index filtering, so a checkout
left there — a private repository's `keys.json` and all — is downloadable by
anyone who can reach the route. `SITE_ROOT` and `WORK_ROOT` must therefore be
disjoint in *either* direction, and the state file must live outside the served
tree as well: a state file under it is downloadable, and it names repositories,
shas and the text of their build errors. All three are refused at startup.

A repository owns its own documentation, so a checkout that carries
`mkdocs.yml` at its root is built with that file verbatim. **That file is
trusted to run code in this process**: MkDocs executes `hooks:` as Python, and
a config can point `docs_dir` at any directory it likes. The trust boundary is
"repositories under the operator's account" — `REPO_OWNER` and `REFS` decide
what runs here, and nothing else does. Within that boundary the builder still
gives the build no network identity: `mkdocs` runs with every credential-shaped
variable removed from its environment (`GITHUB_TOKEN` included), and a
`docs_dir` that resolves outside the checkout fails the pair before MkDocs
starts.

The token is never printed and never written to a file. Any message that could
carry it is scrubbed before it reaches a log line or the state file.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
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

WORK_DIR_DEFAULT_NAME = "work"  # sibling of the served tree, never inside it
# The pre-fix layout put checkouts under `SITE_ROOT/.work`, where nginx served
# them. A run never writes there again; a tree left by an older run is removed
# on sight rather than after the age check, because it is a live disclosure.
LEGACY_WORK_DIR_NAME = ".work"
STAGING_DIR_NAME = ".staging"  # under WORK_ROOT: a site while it is being built
FAILED_DIR_NAME = ".failed"  # under WORK_ROOT: what a failed build produced
REPLACED_DIR_NAME = ".replaced"  # under WORK_ROOT: the site being swapped out

# Leftovers younger than this belong to a run that may still be working. The
# CronJob's own deadline is 900s, so anything older than that is a corpse.
WORK_SWEEP_SECONDS = 900

# A pair that has failed this many times in a row is retried only when its head
# moves. A broken `mkdocs.yml` fails identically every minute, and retrying it
# forever costs a fetch and a build each time; the head moving is the only thing
# that can change the answer without a human. No schedule, just a counter.
FAILURE_LIMIT = 5

# Variables a build has no use for and must never inherit. `hooks:` is Python
# running with this process's environment, so `GITHUB_TOKEN` in reach means a
# repository can push with the builder's identity. The names are matched, not
# an allowlist, because an allowlist breaks the image on its next update.
CREDENTIAL_ENV = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|APIKEY|API_KEY|PRIVATE_KEY|ACCESS_KEY|AUTH|_KEY$)",
    re.IGNORECASE,
)

ENV_DEFAULTS = {
    "REPO_OWNER": "vyrtualsynthese",
    "REFS": "main,develop",
    "SITE_ROOT": "/data/site",
    "WORK_ROOT": "",
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
    work_root: Path
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

    site_root = Path(env["SITE_ROOT"])
    work_setting = env["WORK_ROOT"].strip()
    work_root = Path(work_setting) if work_setting else site_root.parent / WORK_DIR_DEFAULT_NAME
    state_path = Path(env["STATE"])

    # A work root inside the served tree is the exposure this layout exists to
    # prevent, and it is one environment edit away from coming back. Refuse it
    # at startup: a run that cannot keep its scratch private is not a usable run.
    served = site_root.resolve()
    scratch = work_root.resolve()
    if scratch == served or scratch.is_relative_to(served):
        raise ConfigError(
            f"WORK_ROOT {work_root} is inside SITE_ROOT {site_root}; "
            "nginx would serve every checkout and staging tree"
        )
    # The other direction is just as destructive: a work root that *contains*
    # the served tree (`WORK_ROOT=/data`, `SITE_ROOT=/data/site`) passes the
    # check above, and then the sweep sees `site/` as one old entry under the
    # work root and deletes the whole published tree, state file and all.
    if served == scratch or served.is_relative_to(scratch):
        raise ConfigError(
            f"SITE_ROOT {site_root} is inside WORK_ROOT {work_root}; "
            "the stale sweep would delete the published tree"
        )
    # The state file names every repository, its sha and its build errors, and
    # the served tree is answered without authentication, so a state file under
    # it is a publication, not housekeeping.
    state_resolved = state_path.resolve()
    if state_resolved == served or state_resolved.is_relative_to(served):
        raise ConfigError(
            f"STATE {state_path} is inside SITE_ROOT {site_root}; "
            "it names repositories, shas and build errors, and nginx would serve it"
        )

    _SETTINGS = Settings(
        token=token,
        owner=owner,
        refs=refs,
        site_root=site_root,
        work_root=work_root,
        state_path=state_path,
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
# Building one site
# --------------------------------------------------------------------------- #


class BuildError(Exception):
    """One (repository, ref) could not be turned into a site.

    `detail` is already short enough for the state file. `swap` tells the caller
    which half failed: producing the site (the previous one keeps serving, the
    run is still a success) or publishing it (the served tree may now be missing
    a pair, so the run exits non-zero and Kubernetes shows the failure).
    """

    def __init__(self, detail: str, swap: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.swap = swap


TEMPLATE_PATH = Path(__file__).resolve().with_name("mkdocs.yml.tpl")


def _yaml_escaped(value: str) -> str:
    """`value` as the inside of a double-quoted YAML scalar.

    The template wraps every substitution in quotes, and one of them is a path
    taken from the environment. A backslash or a quote there would end the
    scalar early and turn a site build into a parse error nobody can read.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_config(destination: Path, site_name: str, docs_dir: Path, site_dir: Path) -> Path:
    """Render `mkdocs.yml.tpl` to `destination` and return it.

    Used only for a repository that carries no `mkdocs.yml` of its own: the
    common case, where a `docs/` folder is all a repository has to provide.
    """
    try:
        body = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise BuildError(f"cannot read {TEMPLATE_PATH.name}: {exc}") from None

    for placeholder, value in (
        ("{{site_name}}", site_name),
        ("{{docs_dir}}", str(docs_dir)),
        ("{{site_dir}}", str(site_dir)),
    ):
        body = body.replace(placeholder, _yaml_escaped(value))
    if "{{" in body:
        raise BuildError(f"{TEMPLATE_PATH.name} has a placeholder this code does not fill")

    try:
        destination.write_text(body, encoding="utf-8")
    except OSError as exc:
        # The path is the whole message: the caller sees one line in the state
        # file and needs to know which file could not be written.
        raise BuildError(f"cannot write {destination}: {exc}") from None
    return destination


def build_env() -> dict[str, str]:
    """This process's environment with every credential-shaped variable gone.

    A repository's `mkdocs.yml` may declare `hooks:`, which MkDocs imports and
    runs in the build process; so the checkout is code and the environment it
    sees is part of the attack surface. The build needs none of these: no
    network identity, no registry login, no cloud key.
    """
    return {name: value for name, value in os.environ.items() if not CREDENTIAL_ENV.search(name)}


def _unquote(value: str) -> str:
    """The scalar inside a YAML `key: value` line, comment and quotes removed."""
    if value[:1] in {'"', "'"}:
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    return value.split("#", 1)[0].strip()


def check_docs_dir(config: Path, src: Path) -> None:
    """Refuse a repository config whose `docs_dir` leaves the checkout.

    MkDocs reads the key itself, so this only has to answer one question: does
    the directory it names resolve inside the checkout it came from? An absolute
    `docs_dir: /etc`, or a relative one that walks out with `..`, would publish
    whatever this container can read. The line scan is deliberately shallow —
    a top-level key, no YAML library available — and it is a refusal only: a
    config this function cannot read is left for MkDocs to report.

    A path value may be quoted, and a comment may follow it; both forms appear
    in real configs. Only the value MkDocs would see is compared.
    """
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return  # unreadable here means MkDocs reports it, with a better message
    declared: str | None = None
    for line in text.splitlines():
        if not line or line[0] in " \t#-":
            continue  # nested, commented or a list item: not the top-level key
        key, separator, rest = line.partition(":")
        if separator and key.strip() == "docs_dir":
            declared = _unquote(rest.strip())
    if not declared:
        return  # no key, or an empty one: MkDocs' own default, `docs`

    root = Path(src).resolve()
    try:
        resolved = (Path(declared) if Path(declared).is_absolute() else root / declared).resolve()
    except OSError as exc:
        raise BuildError(f"docs_dir {declared!r}: {exc}") from None
    if resolved != root and not resolved.is_relative_to(root):
        raise BuildError(
            f"docs_dir {declared!r} in the repository's mkdocs.yml resolves outside the "
            f"checkout ({resolved}); refusing to publish anything from it"
        )


def missing_docs(src: Path) -> str | None:
    """Why `src` has nothing to publish, or None when it has documentation.

    A repository with its own `mkdocs.yml` decides for itself where its pages
    live — `docs_dir: documentation` is legal and common — so the gate steps
    aside and lets MkDocs answer. Only a repository without a config is held to
    the `docs/` convention the shared template uses.
    """
    if (Path(src) / "mkdocs.yml").is_file():
        return None
    docs = Path(src) / "docs"
    if not docs.is_dir():
        return "no docs/ directory"
    if next(docs.rglob("*.md"), None) is None:
        return "no Markdown file under docs/"
    return None


def _run_mkdocs(config: Path, staging: Path, src: Path) -> None:
    """Build `config` into `staging`, or raise `BuildError` with what MkDocs said.

    `--strict` is deliberately absent: a repository that wants warnings to fail
    its build sets `strict: true` in its own `mkdocs.yml`. Putting it here would
    let one broken link in one repository stop that repository from publishing
    for good, with no way to opt out.

    The environment is `build_env()`, never the inherited one: the config may
    run hooks, and the token is not part of a site build.
    """
    command = ["mkdocs", "build", "-f", str(config), "-d", str(staging), "--quiet"]
    try:
        subprocess.run(
            command,
            cwd=str(src),  # hooks and extra assets resolve as the repository expects
            env=build_env(),
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise BuildError("mkdocs is not on PATH") from None
    except subprocess.CalledProcessError as exc:
        text = f"{exc.stderr or ''}\n{exc.stdout or ''}"
        detail = " ".join(text.split()) or f"mkdocs build exited {exc.returncode}"
        raise BuildError(detail[:DETAIL_LIMIT]) from None
    except OSError as exc:
        raise BuildError(f"could not run mkdocs: {exc}") from None


LANDING_MARKER = "docs-site-landing"


def _page_links(staging: Path) -> list[str]:
    """Every page MkDocs produced, as a link relative to the site root.

    MkDocs writes `use_directory_urls: true` by default (`notes.md` becomes
    `notes/index.html`, linked as `notes/`) and a repository's own config may
    turn that off (`notes.html`). Both are read here so the generated landing
    page works either way. The 404 page is MkDocs' own furniture and is not a
    document.
    """
    links: list[str] = []
    for page in staging.rglob("*.html"):
        relative = page.relative_to(staging)
        if relative.name == "404.html":
            continue
        if relative.name == "index.html":
            if relative.parent == Path("."):
                continue  # the landing page itself, if one exists
            links.append(f"{relative.parent.as_posix()}/")
        else:
            links.append(relative.as_posix())
    return sorted(links)


def _ensure_index(staging: Path, repo: str, ref: str) -> None:
    """Give a finished site a root `index.html`, generating one if MkDocs did not.

    MkDocs only writes `index.html` when the documentation root holds
    `index.md` or `README.md`. Most repositories here have neither: their
    `docs/` opens on `README.md` at best and often on nothing, so the site root
    has no page. Two things then break at once. The served route `/<repo>/<ref>/`
    answers 403 instead of a page, and `published_missing` — which asks whether
    that same file exists — reads a perfectly good site as lost and rebuilds it
    on every run.

    So the builder guarantees the file rather than assuming it. A repository
    that ships its own index keeps it untouched; the generated page only stands
    in, and says so.
    """
    index = staging / "index.html"
    if index.is_file():
        return

    title = html.escape(f"{repo} {ref}")
    links = _page_links(staging)
    if links:
        body = "\n".join(
            f'<li><a href="{html.escape(link)}">{html.escape(link)}</a></li>' for link in links
        )
        content = f"<ul>\n{body}\n</ul>"
    else:
        content = "<p>This site has no pages.</p>"
    page = (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f'<meta name="{LANDING_MARKER}" content="no index page in this documentation">\n'
        f"<title>{title} — documentation</title>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>{title}</h1>\n"
        "<p>This set of pages has no <code>index.md</code> of its own, so this is the list of "
        "what it contains.</p>\n"
        f"{content}\n"
        "</body>\n"
        "</html>\n"
    )
    try:
        index.write_text(page, encoding="utf-8")
    except OSError as exc:
        raise BuildError(f"cannot write the landing page {index}: {exc}") from None


def _prune_empty_dir(path: Path) -> None:
    """Remove `path` when nothing is left in it.

    The staging and replaced directories exist only to hold one run's
    subdirectories. Once the last of those has been moved out or deleted the
    parent is empty, and `rmdir` is the only call that removes it without ever
    touching a sibling run's contents: it fails on a directory that is not
    empty, and that failure is the expected case, not an error.
    """
    try:
        path.rmdir()
    except OSError:
        pass  # already gone, or another run still has work in it


def _park_failure(
    work_root: Path, pair: str, staging: Path, config: Path | None, rendered: Path | None
) -> None:
    """Keep what a failed build produced under `.failed/`, until the sweep.

    The staging tree is the only place the warning lines and the half-written
    site can be looked at, and the configuration is what most often caused the
    failure, so both are kept. A repository's own `mkdocs.yml` is copied rather
    than moved: the checkout is the caller's and never anything to dispose of on
    the way out. Keeping the wreckage is a convenience; it must never turn into
    a second failure.

    A failure before MkDocs ran — an unrenderable template, a refused
    `docs_dir` — has nothing to keep. Saying so is better than creating an empty
    directory and printing that the wreckage was kept.
    """
    has_site = staging.exists()
    own_config = config is not None and config.is_file()
    has_config = (rendered is not None and rendered.exists()) or own_config
    if not has_site and not has_config:
        print(f"# nothing to keep for {pair}: the build stopped before MkDocs wrote anything")
        return

    failed = work_root / FAILED_DIR_NAME / pair
    try:
        shutil.rmtree(failed, ignore_errors=True)
        failed.mkdir(parents=True, exist_ok=True)
        if has_site:
            os.replace(staging, failed / "site")
        if rendered is not None and rendered.exists():
            os.replace(rendered, failed / "mkdocs.yml")
        elif own_config:
            shutil.copy2(config, failed / "mkdocs.yml")
    except OSError as exc:
        print(f"warning: could not keep the failed build: {exc}", file=sys.stderr)
        return
    print(f"# kept the failed build in {failed}", file=sys.stderr)


def _publish(repo: str, ref: str, staging: Path, site_root: Path, work_root: Path) -> Path:
    """Move a finished site into the served tree; return where it landed.

    The previous site is renamed aside under `WORK_ROOT` first, so a failed
    `os.replace` can put it back and the route keeps answering. Renaming a
    directory is only atomic within one filesystem, which is why the default
    work root is the site root's sibling.
    """
    target = site_root / repo / ref
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BuildError(f"could not create {target.parent}: {exc}", swap=True) from None

    aside: Path | None = None
    if target.exists() or target.is_symlink():
        aside = work_root / REPLACED_DIR_NAME / f"{repo}__{ref}.{run_id()}"
        try:
            aside.parent.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(aside, ignore_errors=True)
            os.replace(target, aside)
        except OSError as exc:
            raise BuildError(f"could not move the previous site aside: {exc}", swap=True) from None

    try:
        os.replace(staging, target)
    except OSError as exc:
        if aside is not None:
            try:
                os.replace(aside, target)
            except OSError:
                pass  # both failed; the state records the pair as failed
        raise BuildError(
            f"could not publish {target}: {exc} (WORK_ROOT and SITE_ROOT must be on one filesystem)",
            swap=True,
        ) from None

    if aside is not None:
        shutil.rmtree(aside, ignore_errors=True)
        _prune_empty_dir(work_root / REPLACED_DIR_NAME)
    return target


def _make_dir(path: Path) -> None:
    """`mkdir -p`, or a `BuildError` naming the directory that could not be made."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BuildError(f"cannot create {path}: {exc}") from None


def build_site(repo: str, ref: str, src: Path, site_root: Path, work_root: Path) -> None:
    """Render `src` into `<site_root>/<repo>/<ref>/`, atomically.

    A repository that carries `mkdocs.yml` at its root is built with it, verbatim:
    it owns its documentation. Everything else gets the shared template, whose
    only inputs are the checkout's `docs/` directory and the staging path. A
    repository's config is trusted to run code (MkDocs executes `hooks:`), so it
    is given an environment without credentials and its `docs_dir` must resolve
    inside the checkout.

    The build happens entirely under `work_root`, which nginx never serves. Only
    a complete site is moved into place, so a failure leaves the previous site
    answering and raises `BuildError` with MkDocs' own first lines.
    """
    check_name("repository", repo)
    check_name("ref", ref)
    src, site_root, work_root = Path(src), Path(site_root), Path(work_root)

    pair = f"{repo}__{ref}"
    staging_parent = work_root / STAGING_DIR_NAME / run_id()
    staging = staging_parent / pair
    rendered: Path | None = None
    config: Path | None = None

    try:
        _make_dir(staging_parent)
        own_config = src / "mkdocs.yml"
        if own_config.is_file():
            config = own_config  # the repository's own configuration, used as it stands
            # A config is code (hooks), but a `docs_dir` is also a filesystem
            # read, and MkDocs would happily publish /etc. Checked before the
            # build, so the pair is `failed` and nothing is written.
            check_docs_dir(config, src)
        else:
            rendered = render_config(
                staging_parent / f"{pair}.yml",
                site_name=f"{repo} {ref}",
                docs_dir=src / "docs",
                site_dir=staging,
            )
            config = rendered

        _run_mkdocs(config, staging, src)
        _ensure_index(staging, repo, ref)
        _publish(repo, ref, staging, site_root, work_root)
    except BuildError as exc:
        _park_failure(work_root, pair, staging, config, rendered)
        raise
    finally:
        # The per-run staging directory holds this pair and nothing else once
        # the site has been moved out of it. `.staging` itself stays behind
        # empty after the last run directory goes, so it is pruned here rather
        # than leaving one empty level per build in the work root.
        shutil.rmtree(staging_parent, ignore_errors=True)
        _prune_empty_dir(work_root / STAGING_DIR_NAME)


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


def failure_count(entry: dict[str, Any]) -> int:
    """How many runs in a row have failed on this pair, as the entry records it."""
    value = entry.get("failures")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


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
                "failures": failure_count(entry),
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
    never touched, with the caveat that a run which outlives `max_age` is exactly
    the case the CronJob's deadline is there to prevent.
    """
    cutoff = time.time() - max_age

    # The earlier layout put the work tree under the served directory, where a
    # kill mid-run published a private checkout. Nothing writes there now, so
    # whatever is found is a corpse and goes on sight rather than on age.
    legacy = settings.site_root / LEGACY_WORK_DIR_NAME
    if legacy.exists() or legacy.is_symlink():
        if legacy.is_dir() and not legacy.is_symlink():
            shutil.rmtree(legacy, ignore_errors=True)
        else:
            legacy.unlink(missing_ok=True)
        print(f"# removed legacy work tree left under the site root: {legacy}")

    try:
        entries = list(settings.work_root.iterdir())
    except OSError:
        entries = []
    # Second line of defence. `load_settings` refuses a work root that holds the
    # served tree, but the sweep is the operation that turns a bad path into a
    # deleted site, so it also refuses to touch anything named like the served
    # tree or the state file, whatever the roots say.
    served = settings.site_root.resolve()
    state_file = settings.state_path.resolve()
    guarded_names = {served.name, state_file.name}
    for entry in entries:
        try:
            if entry.name in guarded_names:
                continue
            resolved = entry.resolve()
            if resolved == served or resolved == state_file or resolved.is_relative_to(served):
                continue
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


def published_missing(repo: str, ref: str) -> bool:
    """True when the pair's published index page is not in the served tree.

    A swap renames the old site aside and then moves the new one in. A process
    killed between the two leaves the pair with no site at all, and the aside
    under `WORK_ROOT/.replaced` is erased by the next sweep — so the loss is
    permanent until somebody notices. Nothing else would notice it: the state
    still says `ok` with the same sha, so a daily `--full` rebuild skips the
    pair on the second pass. The served index is therefore the fact the state is
    checked against at the start of every run, which also heals a site somebody
    deleted by hand and costs one `stat` per pair.

    `index.html` is the marker because `build_site` guarantees one: a site whose
    documentation has no index page of its own gets a generated one before it is
    published. Looking for any other file would make the answer depend on the
    repository's documentation layout.
    """
    try:
        settings = load_settings()
        check_name("repository", repo)
        check_name("ref", ref)
    except ConfigError:
        return False
    return not (settings.site_root / repo / ref / "index.html").is_file()


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

    # `absent` no longer means only "no such branch": a branch that carries no
    # `docs/` is absent too, with its head recorded. Forcing a rebuild on every
    # `absent` entry would therefore re-download every repository without docs
    # once a minute, forever. A branch that appears is caught by the sha
    # comparison instead (the stored sha is None), and a repository that adds
    # `docs/` does so in a commit, which moves the sha.
    #
    # `failed` is retried at once — a network blip heals on the next run — but
    # only for the first `FAILURE_LIMIT` attempts. Past that the failure is the
    # repository's (a config MkDocs cannot parse, a docs_dir that was refused),
    # and nothing but the head moving can change the answer.
    stuck = stored.get("status") == "failed" and failure_count(stored) >= FAILURE_LIMIT
    lost = stored.get("status") == "ok" and published_missing(repo, ref)
    rebuild = (
        full
        or not trusted
        or not isinstance(sha, str)
        or stored.get("sha") != sha
        or (stored.get("status") == "failed" and not stuck)
        or lost
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
    """Carry out the plan: fetch and build what was selected, then persist the state.

    One repository's breakage never stops the run, and a build that fails is not
    a run failure: the previous site keeps serving and the state says why. The
    return value is non-zero only when the state could not be written or a
    finished site could not be moved into place, because those are the two ways
    the served tree stops matching what the state claims.
    """
    # Every run unpacks under its own directory, so a run that overruns cannot
    # have its tree removed by the run that follows it.
    run_root = settings.work_root / run_id()
    swap_failed = False

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
                "failures": 0,
            }
            continue

        if decision.action != "build":
            # A skip leaves the entry untouched: nothing was asked of it.
            continue

        pair_work = run_root / f"{decision.repo}__{decision.ref}"
        try:
            fetched = fetch_tarball(decision.repo, decision.ref, pair_work)
            absent = missing_docs(fetched)
            if absent is not None:
                # The ref exists, so its head is recorded: the pair is skipped
                # until it moves, and the index can say why there is no site.
                state["refs"][key] = {
                    "sha": decision.sha,
                    "etag": decision.etag,
                    "digest": entry_digest(decision.sha, decision.etag),
                    "built_at": stored.get("built_at"),
                    "status": "absent",
                    "detail": absent,
                    "failures": 0,
                }
                print(f"# absent {key}: {absent}")
                continue

            build_site(
                decision.repo, decision.ref, fetched, settings.site_root, settings.work_root
            )
            state["refs"][key] = {
                "sha": decision.sha,
                "etag": decision.etag,
                "digest": entry_digest(decision.sha, decision.etag),
                "built_at": now_iso(),
                "status": "ok",
                "detail": f"built {decision.repo}/{decision.ref}",
                "failures": 0,
            }
            print(f"# built {key} -> {settings.site_root / decision.repo / decision.ref}")
        except Exception as exc:  # noqa: BLE001 - one repository never stops the run
            # The head that was attempted is recorded, not the one before it:
            # the failure counter can only bound the retries if the comparison
            # it feeds converges. `decide` still retries a `failed` entry at
            # once for the first `FAILURE_LIMIT` runs, because the status says
            # so; the sha is what lets the sixth run wait for the head to move.
            sha = decision.sha if isinstance(decision.sha, str) else stored.get("sha")
            etag = decision.etag if isinstance(decision.etag, str) else stored.get("etag")
            state["refs"][key] = {
                "sha": sha,
                "etag": etag,
                "digest": entry_digest(sha if isinstance(sha, str) else None, etag),
                "built_at": stored.get("built_at"),
                "status": "failed",
                "detail": scrub(_failure_detail(exc))[:DETAIL_LIMIT],
                "failures": failure_count(stored) + 1,
            }
            swap_failed = swap_failed or bool(getattr(exc, "swap", False))
            print(f"# failed {key}: {scrub(_failure_detail(exc))}", file=sys.stderr)
            # A 403 or 404 from codeload is the repository talking, not the ref:
            # the head check already ruled the ref out. The index says so
            # instead of showing a hole.
            if isinstance(exc, GithubError) and exc.status in (403, 404):
                state["invisible"] = sorted(set(state.get("invisible") or []) | {decision.repo})
        finally:
            shutil.rmtree(pair_work, ignore_errors=True)
            try:
                run_root.rmdir()
            except OSError:
                pass  # another pair in this same run still has work under it

    try:
        save_state(settings.state_path, state)
    except OSError as exc:
        print(f"error: could not write state ({scrub(str(exc))})", file=sys.stderr)
        return 1

    return 1 if swap_failed else 0


def _failure_detail(exc: BaseException) -> str:
    """What the state records for one failed pair.

    A `BuildError` already carries MkDocs' own words, cut to length; anything
    else gets its type, which is the part that says where to look.
    """
    if isinstance(exc, BuildError):
        return exc.detail
    return f"{type(exc).__name__}: {exc}"


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
