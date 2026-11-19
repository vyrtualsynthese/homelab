#!/usr/bin/env python3
"""Replay the versioned FreshRSS feed list against the live instance.

`hack/freshrss/feeds.opml` is the source of truth; the FreshRSS database is a cache of it.

    apply [--prune] [--dry-run]   subscribe what the file lists, and nothing else
    check                         exit 1 when the instance is missing a feed the file lists,
                                  or holds a feed whose content selectors differ from the file
    verify                        fetch every URL; exit 1 when a feed answers badly or a
                                  full-content selector extracts nothing

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
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterator, Mapping, NamedTuple, Sequence

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
    to_prune: tuple[tuple[str, tuple[str, ...]], ...]
    unchanged: tuple[Feed, ...]


def plan(local: Sequence[Feed], remote: Sequence[tuple[str, Sequence[str]]]) -> Plan:
    """Diff the file against what the instance reports, as (url, labels) pairs.

    A feed can carry more than one label: re-subscribing with another `a=` adds one and keeps the
    old, so the file's category counts as present when it is among them, not only when it is first.

    Entries whose id is not a URL are FreshRSS's own internal feeds (its release feed is `1`); the
    file can never list them, so they are not drift and must never be pruned.
    """
    remote = [(url, tuple(labels)) for url, labels in remote if "://" in url]
    by_url = {normalize_url(f.url): f for f in local}
    remote_by_url = {normalize_url(url): labels for url, labels in remote}

    def in_place(feed: Feed) -> bool:
        return feed.category in remote_by_url.get(normalize_url(feed.url), ())

    to_subscribe = tuple(f for f in local if not in_place(f))
    to_prune = tuple((url, labels) for url, labels in remote if normalize_url(url) not in by_url)
    unchanged = tuple(f for f in local if in_place(f))
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

    def subscriptions(self) -> list[tuple[str, tuple[str, ...]]]:
        """Every subscription as (url, labels); a feed can carry more than one label."""
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
            reported = sub.get("categories") or [{"label": "Uncategorized"}]
            labels = tuple(
                label.get("label") or "Uncategorized" for label in reported) or ("Uncategorized",)
            pairs.append((url, labels))
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
            held = {normalize_url(u): labels for u, labels in self.subscriptions()}
            current = held.get(normalize_url(feed.url))
            if current is not None and feed.category in current:
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


class Row(NamedTuple):
    """One line of `verify`: what answered, and whether that is a defect."""

    status: str
    title: str
    items: int
    last: str
    note: str = ""
    bad: bool = False


VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                 "param", "source", "track", "wbr"}
IGNORED_TEXT = {"script", "style"}


class Node:
    """One element of a page, with the text that sits directly inside it."""

    __slots__ = ("tag", "attrs", "parts", "children")

    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag, self.attrs, self.parts, self.children = tag, attrs, [], []

    def text(self) -> str:
        # An explicit stack: a page with thousands of unclosed tags nests as deep as it likes.
        pieces, pending = [], [self]
        while pending:
            node = pending.pop()
            pieces.append("".join(node.parts))
            pending.extend(reversed(node.children))
        return " ".join("".join(pieces).split())


class Page(HTMLParser):
    """Just enough of an HTML tree to ask whether a CSS selector still points at the article."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_ELEMENTS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(
            Node(tag, {key: value or "" for key, value in attrs}))

    def handle_endtag(self, tag):
        # HTML leaves elements unclosed; close back to the nearest matching one and carry on.
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if any(node.tag in IGNORED_TEXT for node in self.stack):
            return
        self.stack[-1].parts.append(data)


def _walk(page: str) -> Iterator[Node]:
    """Every element of a page, in document order: descend before moving sideways."""
    tree = Page()
    tree.feed(page)
    tree.close()
    pending = [tree.root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.children))


SELECTOR = re.compile(r"^([A-Za-z][\w-]*)?((?:[.#][\w-]+)*)$")


def parse_selector(selector: str) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """Read a comma list of simple selectors: `tag`, `#id`, `.class`, `tag.class#id`.

    A descendant combinator, an attribute test or a pseudo-class is refused rather than guessed at,
    so `verify` reports the selector it cannot read instead of passing it silently.
    """
    matchers = []
    for part in selector.split(","):
        part = part.strip()
        match = SELECTOR.match(part)
        if not part or not match:
            raise ValueError(selector)
        tests = tuple(
            ("id" if token[0] == "#" else "class", token[1:])
            for token in re.findall(r"[.#][\w-]+", match.group(2)))
        matchers.append(((match.group(1) or "").lower(), tests))
    return matchers


def _matches(node: Node, matcher: tuple[str, tuple[tuple[str, str], ...]]) -> bool:
    tag, tests = matcher
    if tag and node.tag != tag:
        return False
    if not tests:
        return True
    classes = node.attrs.get("class", "").split()
    for kind, value in tests:
        if kind == "id" and node.attrs.get("id") != value:
            return False
        if kind == "class" and value not in classes:
            return False
    return True


def select_text(page: str, selector: str) -> int:
    """Characters of text inside the first element the selector picks, 0 when it picks none.

    "First" is document order, which is the order a browser would reach it in.

    Raises ValueError for a selector this reader cannot evaluate.
    """
    matchers = parse_selector(selector)
    for node in _walk(page):
        if node.tag != "#document" and any(_matches(node, m) for m in matchers):
            return len(node.text())
    return 0


def _items(root: ET.Element) -> list[ET.Element]:
    """A feed's items, whichever format it is: RSS 2.0 leaves `item` bare, RSS 1.0 namespaces it."""
    return [element for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] in ("item", "entry")]


def _item_link(item: ET.Element) -> str:
    """The page an item points at: RSS gives link text, Atom an alternate link's href."""
    for child in item:
        if child.tag.rsplit("}", 1)[-1] != "link":
            continue
        if (child.text or "").strip():
            return child.text.strip()
        if (child.get("rel") or "alternate") == "alternate" and child.get("href"):
            return child.get("href").strip()
    return ""


def _article_url(root: ET.Element) -> str:
    """The newest item's page: what FreshRSS reads the full content out of.

    The item is picked by date, not by file position: a feed that publishes oldest-first would
    otherwise have a stale article checked.
    """
    best, best_date = "", ""
    for item in _items(root):
        link = _item_link(item)
        if not link:
            continue
        date = _newest(item)
        if not best or date > best_date:
            best, best_date = link, date
    return best


XPATH_LINK = re.compile(
    r"^//a\[(contains|starts-with)\(@href,\s*(['\"])(.*?)\2\)\]$")


def _linked_article(page: str, feed: Feed) -> str:
    """The newest article of an XPath feed: the first link its item expression picks.

    Only the two item expressions the OPML uses are read (`//a[contains(@href,'…')]` and
    `//a[starts-with(@href,'…')]`, whose URI is `@href`). Anything else answers "" and `verify`
    says the selector was not checked, rather than checking the wrong page. The pick is the first
    link in document order, which is the order FreshRSS sees the same expression in.
    """
    match = XPATH_LINK.match(_setting(feed, "xPathItem"))
    if not match or _setting(feed, "xPathItemUri") not in ("", "@href"):
        return ""
    test, fragment = match.group(1), match.group(3)
    for node in _walk(page):
        href = node.attrs.get("href", "") if node.tag == "a" else ""
        if href and (test == "contains" and fragment in href
                     or test == "starts-with" and href.startswith(fragment)):
            return urllib.parse.urljoin(feed.url, href)
    return ""


def _verdict(selector: str, page: str, article: str) -> tuple[str, bool]:
    """What the selector extracts from one page, and whether an empty extraction is a defect."""
    try:
        chars = select_text(page, selector)
    except ValueError:
        return f"sel {selector} unsupported", True
    if not chars:
        return f"sel {selector} empty ({urllib.parse.urlsplit(article).path})", True
    return f"sel {selector} {chars} chars", False


def _unchecked(selector: str) -> str:
    return f"sel {selector} unchecked" if selector else ""


def _setting(feed: Feed, attribute: str) -> str:
    """The value the file gives one OPML setting, looked up under the form's field name."""
    return dict(feed.settings).get(SETTING_FIELDS[attribute], "")


def verify(feeds: Sequence[Feed],
           transport: Callable[..., tuple[int, str]] = http) -> list[Row]:
    """Fetch every URL and say what came back, without ever raising on a bad answer.

    A feed that carries a full-content selector gets one more request: the newest article, read the
    way the selector reads it. A selector that picks nothing is the failure this command exists to
    catch, because the feed itself still answers 200 when it happens.
    """
    rows = []
    for feed in feeds:
        selector = _setting(feed, "cssFullContent")
        try:
            status, body = transport("GET", feed.url, {"User-Agent": USER_AGENT}, None)
        except Exception:
            rows.append(Row("ERR", feed.title, 0, "", _unchecked(selector), True))
            continue
        if status != 200:
            rows.append(Row(f"HTTP{status}", feed.title, 0, "", _unchecked(selector), True))
            continue
        items, last, article = 0, "", ""
        if feed.kind == "xpath":
            # The raw URL is a listing page; the selector reads the article it links to.
            if selector:
                article = _linked_article(body, feed)
        else:
            try:
                root = ET.fromstring(body)
            except ET.ParseError:
                rows.append(Row("NOT-XML", feed.title, 0, "", _unchecked(selector), True))
                continue
            found = _items(root)
            if not found:
                rows.append(Row("EMPTY", feed.title, 0, "", _unchecked(selector), True))
                continue
            items = len(found)
            last = _newest(root)
            if selector:
                article = _article_url(root)
        if not article:
            rows.append(Row("OK", feed.title, items, last, _unchecked(selector)))
            continue
        try:
            page_status, page = transport("GET", article, {"User-Agent": USER_AGENT}, None)
        except Exception:
            page_status, page = 0, ""
        if page_status != 200:
            where = "ERR" if page_status == 0 else f"HTTP{page_status}"
            rows.append(Row("OK", feed.title, items, last, f"sel {selector} page {where}"))
            continue
        note, dead = _verdict(selector, page, article)
        rows.append(Row("OK", feed.title, items, last, note, dead))
    return rows


USAGE = """usage: freshrss_sync.py <apply|check|verify> [--prune] [--dry-run]

  apply    subscribe every feed the OPML lists and set the content selectors it carries;
           --prune also drops the extras, --dry-run only prints
  check    exit 1 when the instance is missing a feed the file lists, or holds one whose
           content selectors differ from the file
  verify   fetch every URL and print status, items and newest date; a feed with a full-content
           selector also has its newest article read through that selector;
           exit 1 when a feed answers badly or a selector extracts nothing

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
        failed = sum(1 for row in rows if row.bad)
        for row in rows:
            print(f"{row.status:9} {row.title:28} {row.items:4} {row.last:10} {row.note}".rstrip())
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
    for url, labels in diff.to_prune:
        print(f"- {url} ({', '.join(labels)})")
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
