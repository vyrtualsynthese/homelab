#!/usr/bin/env python3
"""Replay the versioned FreshRSS feed list against the live instance.

`hack/freshrss/feeds.opml` is the source of truth; the FreshRSS database is a cache of it.

    apply [--prune] [--dry-run]   subscribe what the file lists, and nothing else
    check                         exit 1 when the instance and the file disagree
    verify                        fetch every URL and print what came back today

Credentials come from FRESHRSS_USER and FRESHRSS_API_PASSWORD in the environment.
"""

from __future__ import annotations

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
