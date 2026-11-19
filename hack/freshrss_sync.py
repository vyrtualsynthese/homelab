#!/usr/bin/env python3
"""Replay the versioned FreshRSS feed list against the live instance.

`hack/freshrss/feeds.opml` is the source of truth; the FreshRSS database is a cache of it.

    apply [--prune] [--dry-run]   subscribe what the file lists, and nothing else
    check                         exit 1 when the instance and the file disagree
    verify                        fetch every URL and print what came back today

Credentials come from FRESHRSS_USER and FRESHRSS_API_PASSWORD in the environment.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

DEFAULT_BASE = "https://freshrss.kub.home.ashudev.com"
API = "/api/greader.php"
OPML_PATH = Path(__file__).resolve().parent / "freshrss" / "feeds.opml"
USER_AGENT = "homelab-freshrss-sync/1"


@dataclass(frozen=True)
class Feed:
    category: str
    title: str
    url: str
    kind: str = "native"


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
            if child.get("type") == "rss":
                url = child.get("xmlUrl") or child.get("htmlUrl") or ""
                key = normalize_url(url)
                if not url or key in seen:
                    continue
                seen.add(key)
                feeds.append(
                    Feed(
                        category=category,
                        title=child.get("text") or child.get("title") or url,
                        url=url,
                        kind=child.get("kind") or "native",
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
    """Diff the file against what the instance reports, as (url, category) pairs."""
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
            url = sub.get("id", "").removeprefix("feed/")
            categories = sub.get("categories") or [{"label": "Uncategorized"}]
            pairs.append((url, categories[0].get("label", "Uncategorized")))
        return pairs

    def subscribe(self, feed: Feed) -> None:
        self._post("/reader/api/0/subscription/edit", {
            "ac": "subscribe",
            "s": "feed/" + feed.url,
            "a": "user/-/label/" + feed.category,
            "T": self.token(),
        })

    def unsubscribe(self, url: str) -> None:
        self._post("/reader/api/0/subscription/edit", {
            "ac": "unsubscribe",
            "s": "feed/" + url,
            "T": self.token(),
        })
