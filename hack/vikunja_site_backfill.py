#!/usr/bin/env python3
"""One-off backfill of the ``site:`` line on the board's open tickets.

Cards written before the renderer learned the line carry ``docs:`` with no
``site:`` under it, so a reader has to build the site URL themselves. This
script adds the line where it is missing, and only there:

  * ``Backlog``, ``Doing`` and ``Validation`` are read; ``Idea`` and ``Done``
    are never touched, so a card the operator is shaping and a card that is
    finished are both left alone;
  * a ``docs:`` path that is not on ``origin/develop`` gets ``site: none``,
    because the site builds ``develop`` and an unmerged page renders nowhere;
    the run refuses to start unless that ref was just fetched and still matches
    the commit the remote advertises, since a stale copy would stamp every card
    ``none`` and the idempotence guard would defend the mistake;
  * a card whose description is HTML with no fixed block is reported
    ``skipped`` and never written: it predates the block, and this script does
    not invent one;
  * a card that already has a ``site:`` line is reported ``unchanged``, so a
    second run writes nothing. The body under the block is copied byte for
    byte.

The API is v2 and the default rich-text exchange, never ``format=markdown``:
the default round-trips the description as stored, newlines included, while the
Markdown mode converts the block to one paragraph of HTML on write.

``--dry-run`` is the default and prints every decision; ``--apply`` writes.

Usage:
    VIKUNJA_API_TOKEN=... python3 hack/vikunja_site_backfill.py --dry-run
    VIKUNJA_API_TOKEN=... python3 hack/vikunja_site_backfill.py --apply
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from typing import Any

from vikunja_migrate import Board, site_url

OPEN_COLUMNS = ("Backlog", "Doing", "Validation")
BLOB = "/blob/develop/"
DEVELOP = "origin/develop"
# The bucket read returns at most this many cards per bucket; hitting it means
# the read is truncated, not that the column is that size.
BUCKET_CAP = 100


# ---------------------------------------------------------------------------
# Pure helpers, tested in hack/tests/test_vikunja_site_backfill.py.
# ---------------------------------------------------------------------------
def docs_path(docs_line: str) -> str:
    """The repo-relative path a ``docs:`` line points at, or ``""``.

    The line is written as a bare URL by the renderer and as a labelled link in
    the protocol, so both shapes are read. Anything else (``none``, a path
    outside the repo) comes back as it was written and ``site_url`` rejects it.
    """
    rest = docs_line.split(":", 1)[1].strip()
    link = re.search(r"\((https?://[^)\s]+)\)", rest)
    target = link.group(1) if link is not None else rest
    if BLOB in target:
        return target.split(BLOB, 1)[1].split()[0].strip(")")
    return target.split()[0].strip("`") if target else ""


def fixed_block(description: str) -> list[str]:
    """The lines that open the card, up to the first blank line."""
    lines = description.split("\n")
    end = next((i for i, line in enumerate(lines) if not line.strip()), len(lines))
    return lines[:end]


def looks_html(description: str) -> bool:
    """Whether the description was written in the board's editor, not as text."""
    return re.match(r"\s*<(p|h[1-6]|ul|ol|div|br|table|blockquote)\b", description) is not None


def add_site_line(description: str, on_develop: bool = True) -> str:
    """The same description with a ``site:`` line after ``docs:``, or unchanged.

    ``on_develop`` is the caller's knowledge, not this function's: it is read
    from git, because the site builds ``develop``. The line goes into the fixed
    block that opens the card; the body below the first blank line is never
    parsed and comes back byte for byte, so running this twice equals running
    it once.
    """
    lines = description.split("\n")
    block = fixed_block(description)
    if any(line.strip().startswith("site:") for line in block):
        return description
    index = next((i for i, line in enumerate(block)
                  if line.strip().startswith("docs:")), None)
    if index is None:
        return description
    url = site_url(docs_path(block[index]))
    if url is None:
        return description
    lines.insert(index + 1,
                 f"site: [Rendered page]({url})" if on_develop else "site: none")
    return "\n".join(lines)


def on_develop(path: str, ref: str = DEVELOP) -> bool:
    """Whether ``path`` exists on ``ref``; a git question, asked of the repo."""
    if not path:
        return False
    done = subprocess.run(["git", "cat-file", "-e", f"{ref}:{path}"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return done.returncode == 0


# ---------------------------------------------------------------------------
# The board.
# ---------------------------------------------------------------------------
def open_cards(board: Board, project: int, view: int) -> list[dict[str, Any]]:
    """Every card in the open columns, and nothing else.

    One v2 read returns the view's buckets with their cards, so the six columns
    cost one request. A bucket that comes back at the server's cap is truncated
    and this refuses to guess the rest.
    """
    answer = board.call("GET", f"/api/v2/projects/{project}/views/{view}/buckets/tasks")
    buckets = (answer or {}).get("items") or []
    cards: list[dict[str, Any]] = []
    for bucket in buckets:
        if bucket.get("title") not in OPEN_COLUMNS:
            continue
        tasks = bucket.get("tasks") or []
        # The server caps the cards it returns per bucket and states the true
        # count beside them: either sign of a short read aborts the run.
        if len(tasks) >= BUCKET_CAP or bucket.get("count", 0) > len(tasks):
            raise SystemExit(f"column {bucket['title']} returned {len(tasks)} of "
                             f"{bucket.get('count', 'an unknown number of')} cards; "
                             "the read is truncated, not complete")
        cards.extend(tasks)
    seen = {card["id"] for card in cards}
    if len(seen) != len(cards):
        raise SystemExit("the bucket read returned the same card in two columns")
    return cards


def check_develop(ref: str = DEVELOP) -> str:
    """Refuse to run unless the site's branch is present *and* current.

    A missing or stale ``origin/develop`` makes ``on_develop`` answer False for
    every path, which would stamp ``site: none`` on every open card — and the
    idempotence guard would then defend the mistake on every later run. So this
    is a pre-flight, before the board is read, and it proves freshness rather
    than assuming it: a quiet ``git fetch`` of ``ref``'s remote branch, then a
    comparison of the updated ref with the commit the remote advertises. A fetch
    that fails stops the run; so does a ref that still disagrees with the remote.
    """
    remote, _, branch = ref.partition("/")
    if not (remote and branch):
        raise SystemExit(f"{ref} is not a remote-tracking ref; pass `<remote>/<branch>`, "
                         f"the default being {DEVELOP}.")
    fetched = subprocess.run(["git", "fetch", "--quiet", remote, branch],
                             capture_output=True, text=True)
    if fetched.returncode != 0:
        reason = fetched.stderr.strip() or f"exit {fetched.returncode}"
        raise SystemExit(f"`git fetch {remote} {branch}` failed ({reason}), so {ref} cannot "
                         f"be shown to be current. The site builds {ref}, and a stale copy "
                         "would stamp `site: none` on every open card.")
    done = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"`git fetch {remote} {branch}` succeeded but {ref} is not in this "
                         f"checkout; the fetched ref has nowhere to land. The site builds "
                         f"{ref}, and without it every open card would be stamped `site: none`.")
    local = done.stdout.strip()
    advertised = subprocess.run(["git", "ls-remote", remote, f"refs/heads/{branch}"],
                                capture_output=True, text=True)
    if advertised.returncode != 0:
        reason = advertised.stderr.strip() or f"exit {advertised.returncode}"
        raise SystemExit(f"`git ls-remote {remote} refs/heads/{branch}` failed ({reason}), so "
                         f"the freshness of {ref} cannot be proved.")
    head = advertised.stdout.split()[0] if advertised.stdout.split() else ""
    if head != local:
        raise SystemExit(f"{ref} is {local} but {remote} advertises {head}; the local ref is "
                         f"stale and would stamp `site: none` on every open card. Run "
                         f"`git fetch {remote} {branch}` by hand and retry.")
    return local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=int, default=2)
    parser.add_argument("--view", type=int, default=13)
    parser.add_argument("--base-url",
                        default=os.environ.get("VIKUNJA_URL", "https://vikunja.kub.local"))
    parser.add_argument("--ref", default=DEVELOP,
                        help="the ref the docs site builds (default: origin/develop)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print every decision and write nothing (the default)")
    parser.add_argument("--apply", action="store_true", help="write the missing lines")
    args = parser.parse_args(argv)
    # --apply is the only write switch; --dry-run is accepted for symmetry and
    # both together are a mistake, not a request to write.
    writing = args.apply and not args.dry_run
    if args.dry_run and args.apply:
        parser.error("pass at most one of --dry-run or --apply")

    token = os.environ.get("VIKUNJA_API_TOKEN", "")
    if not token:
        parser.error("VIKUNJA_API_TOKEN is not set")

    check_develop(args.ref)
    board = Board(args.base_url, token)
    cards = open_cards(board, args.project, args.view)
    known: dict[str, bool] = {}
    updated = unchanged = skipped = stale = failed = 0
    verb = "updated" if writing else "would update"
    for card in cards:
        description = card.get("description") or ""
        block = fixed_block(description)
        line = next((text for text in block if text.strip().startswith("docs:")), None)
        if line is None:
            skipped += 1
            why = "no fixed block; HTML, writes nothing" if looks_html(description) \
                else "no fixed block, writes nothing"
            print(f"skipped         #{card['id']}  {card['title']}  ({why})")
            continue
        path = docs_path(line)
        if path not in known:
            known[path] = on_develop(path, args.ref)
        site = next((text.strip() for text in block if text.strip().startswith("site:")), None)
        if site is not None:
            if site.lower() == "site: none" and known[path]:
                # A page that was unmerged when the card was written may have
                # landed since. Say so; the rewrite is the operator's call.
                stale += 1
                print(f"stale none      #{card['id']}  {card['title']}  "
                      f"({path} is on {args.ref} now)")
                continue
            unchanged += 1
            print(f"unchanged       #{card['id']}  {card['title']}  (already has one)")
            continue
        after = add_site_line(description, known[path])
        if after == description:
            unchanged += 1
            print(f"unchanged       #{card['id']}  {card['title']}  (no site page applies)")
            continue
        lines = after.split("\n")
        index = next(i for i, text in enumerate(lines) if text.strip().startswith("docs:"))
        label = f"#{card['id']}  {card['title']}"
        planned = f"{label}  ->  {lines[index + 1]}"
        if not writing:
            print(f"{verb:15} {planned}")
            updated += 1
            continue
        try:
            board.call("PATCH", f"/api/v2/tasks/{card['id']}", {"description": after})
        except SystemExit as error:
            # An HTTP error on one rejected card must not hide the rest of the
            # run: name it, count it, keep going, and let the exit status say it
            # happened. A transport error is not caught here and still aborts.
            failed += 1
            print(f"failed          {label}  {error}")
        else:
            print(f"{verb:15} {planned}")
            updated += 1

    summary = (f"{verb}: {updated} card(s); unchanged: {unchanged}; "
               f"skipped: {skipped}; stale none: {stale}")
    if failed:
        summary += f"; failed: {failed}"
    print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
