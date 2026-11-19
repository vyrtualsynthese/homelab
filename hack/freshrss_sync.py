#!/usr/bin/env python3
"""Replay the versioned FreshRSS feed list against the live instance.

`hack/freshrss/feeds.opml` is the source of truth; the FreshRSS database is a cache of it.

    apply [--prune] [--dry-run]   subscribe what the file lists, and nothing else
    check                         exit 1 when the instance is missing a feed the file lists,
                                  or holds a feed whose content selectors differ from the file
    verify                        fetch every URL; exit 1 when a native feed answers badly

`check` does not fail on a feed the instance holds and the file does not: apply never removes one
without --prune, so an extra is not drift, and it is printed instead.

Credentials come from FRESHRSS_USER and FRESHRSS_API_PASSWORD in the environment. The per-feed
settings are behind the web login, which the API password does not open: set FRESHRSS_WEB_PASSWORD
too and the selectors are checked with the rest, left out and the settings half is skipped.
"""

from __future__ import annotations

import datetime
import email.utils
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Mapping, Sequence

DEFAULT_BASE = "https://freshrss.kub.home.ashudev.com"
API = "/api/greader.php"
OPML_PATH = Path(__file__).resolve().parent / "freshrss" / "feeds.opml"
USER_AGENT = "homelab-freshrss-sync/1"
# The site root is a static page that only points at the app; every web route lives under it.
WEB = "/i"
FRSS = "{https://freshrss.org/opml}"

# The OPML attribute FreshRSS writes, and the field the web form it feeds reads. The names drifted
# between releases (`keep_history` became `archiving`, the CSS selector became `path_entries`), so
# this map is the one place the spelling is written down.
SETTING_FIELDS = {
    "cssFullContent": "path_entries",
    "cssFullContentConditions": "path_entries_conditions",
    "cssContentFilter": "path_entries_filter",
    "cssFullContentFilter": "path_entries_filter",
    "xPathItem": "xPathItem",
    "xPathItemTitle": "xPathItemTitle",
    "xPathItemContent": "xPathItemContent",
    "xPathItemUri": "xPathItemUri",
    "xPathItemAuthor": "xPathItemAuthor",
    "xPathItemTimestamp": "xPathItemTimestamp",
    "xPathItemTimeFormat": "xPathItemTimeFormat",
    "xPathItemThumbnail": "xPathItemThumbnail",
    "xPathItemCategories": "xPathItemCategories",
    "xPathItemUid": "xPathItemUid",
}
KIND_HTML_XPATH = 10


@dataclass(frozen=True)
class Feed:
    category: str
    title: str
    url: str
    kind: str = "native"
    settings: tuple[tuple[str, str], ...] = ()


def normalize_url(url: str) -> str:
    """Compare URLs the way a person does: one trailing slash is not a different feed."""
    return url[:-1] if url.endswith("/") else url


def parse_opml(text: str) -> list[Feed]:
    """Read an OPML export into feeds, collapsing duplicates on their normalized URL."""
    root = ET.fromstring(text)
    feeds: list[Feed] = []
    seen: set[str] = set()

    def walk(node: ET.Element, category: str) -> None:
        for child in node.findall("outline"):
            url = child.get("xmlUrl") or child.get("htmlUrl") or ""
            if url:
                key = normalize_url(url)
                if key in seen:
                    continue
                seen.add(key)
                kind = "xpath" if (
                    child.get("kind") == "xpath" or "xpath" in (child.get("type") or "").lower()
                ) else "native"
                settings: dict[str, str] = {}
                for attribute, field in SETTING_FIELDS.items():
                    value = child.get(FRSS + attribute)
                    if value:
                        settings[field] = value
                feeds.append(
                    Feed(
                        category=category,
                        title=child.get("text") or child.get("title") or url,
                        url=url,
                        kind=kind,
                        settings=tuple(settings.items()),
                    )
                )
            else:
                walk(child, child.get("text") or child.get("title") or category)

    walk(root.find("body") if root.find("body") is not None else root, "Uncategorized")
    return feeds


@dataclass(frozen=True)
class Plan:
    to_subscribe: tuple[Feed, ...]
    to_prune: tuple[tuple[str, str], ...]
    unchanged: tuple[Feed, ...]


def plan(local: Sequence[Feed], remote: Sequence[tuple[str, str]]) -> Plan:
    """Diff the file against what the instance reports, as (url, category) pairs.

    Entries whose id is not a URL are FreshRSS's own internal feeds (its release feed is `1`); the
    file can never list them, so they are not drift and must never be pruned.
    """
    remote = [(url, cat) for url, cat in remote if "://" in url]
    by_url = {normalize_url(f.url): f for f in local}
    remote_by_url = {normalize_url(url): cat for url, cat in remote}

    to_subscribe = tuple(
        f for f in local if remote_by_url.get(normalize_url(f.url)) != f.category
    )
    to_prune = tuple((url, cat) for url, cat in remote if normalize_url(url) not in by_url)
    unchanged = tuple(
        f for f in local if remote_by_url.get(normalize_url(f.url)) == f.category
    )
    return Plan(to_subscribe=to_subscribe, to_prune=to_prune, unchanged=unchanged)


class AuthError(Exception):
    """The instance refused the credentials in the environment."""


def http(method: str, url: str, headers: dict[str, str] | None = None,
         data: bytes | None = None) -> tuple[int, str]:
    """The default transport: one request, 30 s, status and decoded body."""
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


class Client:
    """The GReader API, as much of it as replaying a feed list needs."""

    def __init__(self, base: str = DEFAULT_BASE, user: str = "", password: str = "",
                 transport: Callable[..., tuple[int, str]] = http):
        self.base = base.rstrip("/") + API
        self.user = user
        self.password = password
        self.transport = transport
        self.auth = ""

    def _form(self, pairs: dict[str, str]) -> bytes:
        return urllib.parse.urlencode(pairs).encode()

    def _post(self, path: str, pairs: dict[str, str]) -> str:
        status, body = self.transport(
            "POST", self.base + path, {"Authorization": f"GoogleLogin auth={self.auth}"},
            self._form(pairs))
        if status != 200:
            raise RuntimeError(f"{path} answered {status}: {body[:200]}")
        return body

    def login(self) -> None:
        status, body = self.transport(
            "POST", self.base + "/accounts/ClientLogin", {},
            self._form({"Email": self.user, "Passwd": self.password}))
        auth = ""
        for line in body.splitlines():
            if line.startswith("Auth="):
                auth = line[len("Auth="):].strip()
        if status != 200 or not auth:
            raise AuthError(f"login refused ({status}): {body.strip()[:120]}")
        self.auth = auth

    def token(self) -> str:
        if not self.auth:
            self.login()
        status, body = self.transport(
            "GET", self.base + "/reader/api/0/token",
            {"Authorization": f"GoogleLogin auth={self.auth}"}, None)
        if status != 200:
            raise RuntimeError(f"token answered {status}")
        return body.strip()

    def subscriptions(self) -> list[tuple[str, str]]:
        if not self.auth:
            self.login()
        status, body = self.transport(
            "GET", self.base + "/reader/api/0/subscription/list?output=json",
            {"Authorization": f"GoogleLogin auth={self.auth}"}, None)
        if status != 200:
            raise RuntimeError(f"subscription/list answered {status}")
        pairs = []
        for sub in json.loads(body).get("subscriptions", []):
            # 1.30 answers a numeric id ("feed/5") and puts the address in url.
            url = sub.get("url") or sub.get("id", "").removeprefix("feed/")
            categories = sub.get("categories") or [{"label": "Uncategorized"}]
            pairs.append((url, categories[0].get("label", "Uncategorized")))
        return pairs

    def subscribe(self, feed: Feed) -> None:
        try:
            self._post("/reader/api/0/subscription/edit", {
                "ac": "subscribe",
                "s": "feed/" + feed.url,
                "a": "user/-/label/" + feed.category,
                "T": self.token(),
            })
        except RuntimeError:
            # The instance answers 500 for a feed it already holds, and a second subscribe does
            # not move it between categories. Look at what it actually holds before giving up.
            held = {normalize_url(u): cat for u, cat in self.subscriptions()}
            current = held.get(normalize_url(feed.url))
            if current == feed.category:
                return
            if current is not None:
                self._post("/reader/api/0/subscription/edit", {
                    "ac": "edit",
                    "s": "feed/" + feed.url,
                    "a": "user/-/label/" + feed.category,
                    "T": self.token(),
                })
                return
            raise

    def unsubscribe(self, url: str) -> None:
        self._post("/reader/api/0/subscription/edit", {
            "ac": "unsubscribe",
            "s": "feed/" + url,
            "T": self.token(),
        })

    def feed_ids(self) -> dict[str, int]:
        """Feed address to its FreshRSS id, which the web UI addresses each settings page by."""
        if not self.auth:
            self.login()
        status, body = self.transport(
            "GET", self.base + "/reader/api/0/subscription/list?output=json",
            {"Authorization": f"GoogleLogin auth={self.auth}"}, None)
        if status != 200:
            raise RuntimeError(f"subscription/list answered {status}")
        ids: dict[str, int] = {}
        for sub in json.loads(body).get("subscriptions", []):
            url = sub.get("url") or sub.get("id", "").removeprefix("feed/")
            ident = sub.get("id", "")
            if ident.startswith("feed/") and ident[len("feed/"):].isdigit():
                ids[normalize_url(url)] = int(ident[len("feed/"):])
        return ids


class Form:
    """One HTML form, as the pairs a browser would submit it with."""

    def __init__(self, action: str, fields: list[tuple[str, str]]):
        self.action = action
        self.fields = fields

    def value(self, name: str) -> str | None:
        for key, value in self.fields:
            if key == name:
                return value
        return None

    def as_pairs(self, overrides: Mapping[str, str]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        replaced: set[str] = set()
        for key, value in self.fields:
            if key in overrides:
                pairs.append((key, overrides[key]))
                replaced.add(key)
            else:
                pairs.append((key, value))
        for key, value in overrides.items():
            if key not in replaced:
                pairs.append((key, value))
        return pairs


class _FormParser(HTMLParser):
    """Collect every form and its submit-able fields: checkboxes only when checked, one option per
    select, and a textarea's text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._form: dict | None = None
        self._select: str | None = None
        self._options: list[dict] = []
        self._option: dict | None = None
        self._textarea: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "form":
            self._form = {"action": attributes.get("action", ""), "fields": []}
            self.forms.append(self._form)
            return
        if self._form is None:
            return
        if tag == "input":
            name = attributes.get("name", "")
            if not name or "disabled" in attributes:
                return
            kind = attributes.get("type", "text").lower()
            if kind == "submit":
                return
            if kind in ("checkbox", "radio"):
                if "checked" in attributes:
                    self._form["fields"].append((name, attributes.get("value", "on")))
            else:
                self._form["fields"].append((name, attributes.get("value", "")))
        elif tag == "select":
            self._select = attributes.get("name", "")
            self._options = []
        elif tag == "option" and self._select:
            self._option = {"value": attributes.get("value"), "text": [],
                            "selected": "selected" in attributes}
            self._options.append(self._option)
        elif tag == "textarea":
            self._textarea = attributes.get("name", "")
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "option":
            self._option = None
        elif tag == "select" and self._select and self._form is not None:
            chosen = next((o for o in self._options if o["selected"]),
                          self._options[0] if self._options else None)
            value = ""
            if chosen is not None:
                # A browser sends the option's text when it carries no value attribute.
                value = chosen["value"] if chosen["value"] is not None else "".join(chosen["text"]).strip()
            self._form["fields"].append((self._select, value))
            self._select, self._options = None, []
        elif tag == "textarea" and self._textarea and self._form is not None:
            self._form["fields"].append((self._textarea, "".join(self._text)))
            self._textarea, self._text = None, []
        elif tag == "form":
            self._form = None

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self._text.append(data)
        elif self._option is not None:
            self._option["text"].append(data)


def form_with(html: str, field: str) -> Form:
    """The first form on a page that carries a given field."""
    parser = _FormParser()
    parser.feed(html)
    for collected in parser.forms:
        form = Form(collected["action"], collected["fields"])
        if form.value(field) is not None:
            return form
    raise RuntimeError(f"no form carrying {field} on the page")


def settings_form(html: str) -> Form:
    """The feed settings form on a subscription page, told apart by its content selector field."""
    parser = _FormParser()
    parser.feed(html)
    candidates = [Form(collected["action"], collected["fields"])
                  for collected in parser.forms
                  if Form(collected["action"], collected["fields"]).value("path_entries") is not None]
    if not candidates:
        raise RuntimeError("no feed settings form on the page")
    for form in candidates:
        if _is_feed_route(form.action):
            return form
    return candidates[0]


def _is_feed_route(action: str) -> bool:
    """True for the feed settings route, and not for a page that merely mentions it."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(action).query)
    return query.get("c") == ["subscription"] and query.get("a") == ["feed"]


def settings_drift(form: Form, wanted: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """The fields a page displays differently from the file, as {field: (shown, wanted)}."""
    drift = {}
    for field, value in wanted.items():
        shown = form.value(field) or ""
        if shown != value:
            drift[field] = (shown, value)
    return drift


def cookied_transport() -> Callable[..., tuple[int, str, dict]]:
    """A transport that remembers cookies, which the web login needs to carry its nonce."""
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def transport(method: str, url: str, headers: dict[str, str] | None = None,
                  data: bytes | None = None) -> tuple[int, str, dict]:
        request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with opener.open(request, timeout=30) as response:
                return (response.status, response.read().decode("utf-8", "replace"),
                        dict(response.headers))
        except urllib.error.HTTPError as error:
            return (error.code, error.read().decode("utf-8", "replace"), dict(error.headers))

    return transport


class WebClient:
    """The web UI, as much of it as reading and writing a feed's settings needs.

    The login is a bcrypt challenge: the browser asks for a nonce, hashes the password against the
    salt half of the stored hash, hashes that plus the nonce, and posts the result against the
    session cookie. The API password does not open this door; the web password does.
    """

    def __init__(self, base: str = DEFAULT_BASE, user: str = "", password: str = "",
                 transport: Callable[..., tuple[int, str, dict]] | None = None):
        self.base = base.rstrip("/")
        self.user = user
        self.password = password
        self.transport = transport or cookied_transport()

    def _request(self, method: str, path: str,
                 pairs: dict[str, str] | None = None) -> tuple[int, str]:
        headers = {"User-Agent": USER_AGENT}
        data = None
        if pairs is not None:
            data = urllib.parse.urlencode(pairs).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        status, body, _ = self.transport(method, self.base + path, headers, data)
        return status, body

    def get(self, path: str) -> str:
        status, body = self._request("GET", path)
        if status != 200:
            raise RuntimeError(f"{path} answered {status}")
        return body

    def login(self) -> None:
        # Every POST is refused with [CSRF] unless it carries the session's token, the login too,
        # and the only place that hands it out is a page whose form embeds it.
        login_page = self.get(WEB + "/?c=auth&a=formLogin")
        token = form_with(login_page, "_csrf").value("_csrf") or ""
        status, body = self._request(
            "GET", WEB + "/?c=javascript&a=nonce&user=" + urllib.parse.quote(self.user))
        if status != 200:
            raise RuntimeError(f"the nonce endpoint answered {status}")
        try:
            payload = json.loads(body)
            salt1, nonce = payload["salt1"], payload["nonce"]
        except (ValueError, KeyError, TypeError) as error:
            raise RuntimeError(f"the nonce endpoint answered {body[:120]}") from error
        if not (isinstance(salt1, str) and len(salt1) == 29 and salt1.startswith("$2")):
            raise AuthError(f"the instance gave no bcrypt salt for {self.user}")
        try:
            import bcrypt
        except ImportError as error:  # the one dependency this script does not already carry
            raise RuntimeError("the web login needs the bcrypt package: pip install bcrypt") from error
        try:
            salted = bcrypt.hashpw(self.password.encode(), salt1.encode())
            challenge = bcrypt.hashpw(salted + nonce.encode(), bcrypt.gensalt(rounds=4)).decode()
        except ValueError as error:
            raise AuthError(f"the challenge could not be built: {error}") from error
        status, body = self._request("POST", WEB + "/?c=auth&a=formLogin", {
            "username": self.user,
            "challenge": challenge,
            "_csrf": token,
        })
        # The refusal is a 403 error page; a page that still carries the login form means the same.
        if status != 200 or "formLogin" in body:
            raise AuthError(f"the web password was refused for {self.user}")

    def feed_form(self, feed_id: int) -> Form:
        return settings_form(self.get(f"{WEB}/?c=subscription&a=feed&id={feed_id}"))

    def set_settings(self, feed_id: int, wanted: Mapping[str, str]) -> dict[str, tuple[str, str]]:
        """Resubmit a feed's settings form with the file's values, and read back what took.

        Returns what still differs afterwards, as {field: (shown, wanted)}; empty means the page
        agrees with the file.
        """
        page = f"{WEB}/?c=subscription&a=feed&id={feed_id}"
        form = self.feed_form(feed_id)
        before = dict(form.fields)
        if not settings_drift(form, wanted):
            return {}
        url = urllib.parse.urljoin(self.base + page, form.action)
        body = urllib.parse.urlencode(form.as_pairs(wanted)).encode()
        status, _, _ = self.transport("POST", url, {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        }, body)
        if status not in (200, 302):
            raise RuntimeError(f"the settings page answered {status}")
        after = self.feed_form(feed_id)
        remaining = settings_drift(after, wanted)
        # The form carries ttl, mute and the archiving rules too: a field the submit changed
        # without being asked is the damage a partial or misparsed form would do, so report it.
        for field, was in before.items():
            if field in wanted or field == "_csrf":
                continue  # the token is regenerated on every page, by design
            now = after.value(field)
            if now != was:
                remaining[f"{field} (changed by the submit)"] = (was, now or "(empty)")
        return remaining


def _newest(root: ET.Element) -> str:
    """The newest date the feed publishes, as YYYY-MM-DD, or an empty string."""
    best = None
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in ("pubDate", "updated", "published") or not element.text:
            continue
        raw = element.text.strip()
        try:
            if raw[:4].isdigit() and raw[4:5] == "-":
                when = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            else:
                when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        if best is None or when > best:
            best = when
    return best.date().isoformat() if best else ""


def verify(feeds: Sequence[Feed],
           transport: Callable[..., tuple[int, str]] = http) -> list[tuple[str, str, int, str]]:
    """Fetch every URL and say what came back, without ever raising on a bad answer."""
    rows = []
    for feed in feeds:
        try:
            status, body = transport("GET", feed.url, {"User-Agent": USER_AGENT}, None)
        except Exception:
            rows.append(("ERR", feed.title, 0, ""))
            continue
        if status != 200:
            rows.append((f"HTTP{status}", feed.title, 0, ""))
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            rows.append(("NOT-XML", feed.title, 0, ""))
            continue
        items = root.findall(".//item") or root.findall(
            ".//{http://www.w3.org/2005/Atom}entry")
        if not items:
            rows.append(("EMPTY", feed.title, 0, ""))
            continue
        rows.append(("OK", feed.title, len(items), _newest(root)))
    return rows


USAGE = """usage: freshrss_sync.py <apply|check|verify> [--prune] [--dry-run]

  apply    subscribe every feed the OPML lists and set the content selectors it carries;
           --prune also drops the extras, --dry-run only prints
  check    exit 1 when the instance is missing a feed the file lists, or holds one whose
           content selectors differ from the file
  verify   fetch every URL and print status, items and newest date;
           exit 1 when a native feed answers badly

FRESHRSS_USER and FRESHRSS_API_PASSWORD are read from the environment,
FRESHRSS_WEB_PASSWORD opens the web UI for the per-feed settings (without
it the settings half is skipped), and FRESHRSS_BASE overrides the instance
URL (default %s).

Exit codes: 0 clean, 1 drift or a failed feed, 2 credentials missing or
refused, including the web login.
""" % DEFAULT_BASE


def settings_wanted(feeds: Sequence[Feed]) -> list[tuple[Feed, dict[str, str]]]:
    """The per-feed settings the file asks for, and the feed kind an XPath feed needs with them."""
    wanted = []
    for feed in feeds:
        settings = dict(feed.settings)
        if feed.kind == "xpath":
            settings["feed_kind"] = str(KIND_HTML_XPATH)
        if settings:
            wanted.append((feed, settings))
    return wanted


def main(argv: list[str] | None = None,
         transport: Callable[..., tuple[int, str]] = http,
         env: dict[str, str] | None = None,
         web_transport: Callable[..., tuple[int, str, dict]] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ) if env is None else env
    if not args or args[0] not in ("apply", "check", "verify"):
        print(USAGE, file=sys.stderr)
        return 2
    command, flags = args[0], {a for a in args[1:] if a.startswith("-")}

    feeds = parse_opml(OPML_PATH.read_text())

    if command == "verify":
        rows = verify(feeds, transport)
        failed = 0
        for (status, title, items, last), feed in zip(rows, feeds):
            print(f"{status:9} {title:28} {items:4} {last}")
            if feed.kind == "native" and status != "OK":
                failed += 1
        return 1 if failed else 0

    user = env.get("FRESHRSS_USER", "")
    password = env.get("FRESHRSS_API_PASSWORD", "")
    if not user or not password:
        print("FRESHRSS_USER and FRESHRSS_API_PASSWORD must be set", file=sys.stderr)
        return 2

    base = env.get("FRESHRSS_BASE", DEFAULT_BASE)
    client = Client(base, user, password, transport)

    def settings_pass(apply: bool) -> tuple[int, bool]:
        """Read every feed's settings page; print what differs, and fix it when apply is set.

        Returns how many feeds still differ, and whether the web UI refused to answer at all.
        """
        wanted = settings_wanted(feeds)
        if not wanted:
            return 0, False
        web_password = env.get("FRESHRSS_WEB_PASSWORD", "")
        if not web_password:
            print("settings: not checked (set FRESHRSS_WEB_PASSWORD)")
            return 0, False
        web = WebClient(base, user, web_password, web_transport)
        try:
            web.login()
            ids = client.feed_ids()
        except AuthError as error:
            print(str(error), file=sys.stderr)
            return 0, True
        except (RuntimeError, OSError) as error:
            print(f"the web UI did not answer: {error}", file=sys.stderr)
            return 0, True
        differing = 0
        for feed, settings in wanted:
            feed_id = ids.get(normalize_url(feed.url))
            if feed_id is None:
                continue  # not subscribed yet; apply converges on a second run
            try:
                if apply:
                    drift = web.set_settings(feed_id, settings)
                else:
                    drift = settings_drift(web.feed_form(feed_id), settings)
            except (RuntimeError, OSError) as error:
                print(f"! {feed.category}/{feed.title}: {error}", file=sys.stderr)
                differing += 1
                continue
            for field, (shown, value) in sorted(drift.items()):
                print(f"~ {feed.category}/{feed.title}: {field} {shown or '(empty)'} -> {value}")
            if drift:
                differing += 1
        checked = sum(1 for feed, _ in wanted if ids.get(normalize_url(feed.url)) is not None)
        missing = len(wanted) - checked
        note = f", {missing} not on the instance" if missing else ""
        print(f"settings: {checked - differing} of {checked} feeds match the file{note}")
        return differing, False

    try:
        diff = plan(feeds, client.subscriptions())
    except AuthError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as error:
        print(f"the instance did not answer: {error}", file=sys.stderr)
        return 2

    for feed in diff.to_subscribe:
        print(f"+ {feed.category}/{feed.title}")
    for url, category in diff.to_prune:
        print(f"- {url} ({category})")
    xpath = [f for f in diff.to_subscribe if f.kind == "xpath"]
    print(f"{len(diff.to_subscribe)} to subscribe, {len(diff.to_prune)} extra, "
          f"{len(diff.unchanged)} unchanged, {len(xpath)} XPath (OPML import)")
    for feed in xpath:
        print(f"~ {feed.category}/{feed.title}: subscribe by importing the OPML, "
              f"the API refuses a page that is not a feed")

    if command == "check":
        differing, refused = settings_pass(apply=False)
        if refused:
            return 2
        return 1 if (diff.to_subscribe or differing) else 0
    if "--dry-run" in flags:
        _, refused = settings_pass(apply=False)
        return 2 if refused else 0

    failed = []
    for feed in diff.to_subscribe:
        if feed.kind == "xpath":
            continue
        try:
            client.subscribe(feed)
        except (RuntimeError, OSError) as error:
            print(f"! {feed.category}/{feed.title}: {error}", file=sys.stderr)
            failed.append(feed)
    if "--prune" in flags:
        for url, _ in diff.to_prune:
            client.unsubscribe(url)
    differing, refused = settings_pass(apply=True)
    if refused:
        return 2
    return 1 if (failed or differing) else 0


if __name__ == "__main__":
    sys.exit(main())
