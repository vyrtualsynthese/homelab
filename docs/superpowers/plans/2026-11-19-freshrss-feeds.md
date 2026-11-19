# FreshRSS feed set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the approved feed set into FreshRSS from a versioned list, with a drift check, so the instance can be rebuilt from git.

**Architecture:** `hack/freshrss/feeds.opml` is the source of truth. `hack/freshrss_sync.py` parses it, diffs it against the instance through the GReader API, and applies the difference. A `check` mode exits non-zero on drift; a `verify` mode fetches every URL and prints what came back, so a dead scraper is visible.

**Tech Stack:** Python 3.12 standard library only (`urllib`, `xml.etree`, `unittest`), FreshRSS 1.30 GReader API at `/api/greader.php`.

**Spec:** `docs/superpowers/specs/2026-11-19-freshrss-feeds-design.md`

## Global Constraints

- Official sources only. No Reddit, no Google News, no aggregator of any kind.
- Blog and news only. No changelog, release-note or patch-note feed.
- Five categories, exact names: `Jeux`, `Dev`, `IA`, `Boutiques`, `Veille`.
- Credentials come from `FRESHRSS_USER` and `FRESHRSS_API_PASSWORD` in the environment. Never a
  literal in a file, never on a command line.
- Default base URL `https://freshrss.kub.home.ashudev.com`.
- `apply` never unsubscribes unless `--prune` is passed.
- Python standard library only: no new dependency for a script the operator has to keep working.
- Tests run with `python3 -m unittest discover -s hack/tests -v`.

## Review Focus

- **Re-running `apply` on a clean instance** must change nothing: no duplicate feed, no category
  churn. A sync tool that drifts on every run is worse than none.
- **A URL carrying a query string** (`.../feed?post_type=product`) must survive OPML parsing and
  the API round trip; the `&` and `?` are where naive string handling loses the feed.
- **`apply` without `--prune`** must never remove a feed, even one the operator added by hand in
  the UI. Losing a feed to a typo in the OPML is the failure that matters.
- **A source that answers 429 or 403 during `verify`** must be reported and skipped, not crash the
  run: KBDfans rate-limits and Gateron sits behind Cloudflare.
- **A duplicate URL listed twice in the OPML** must appear once, in the first category that lists
  it.

---

### Task 0: Branch and commit the spec

**Files:**
- Commit: `docs/superpowers/specs/2026-11-19-freshrss-feeds-design.md`

- [ ] **Step 1: Create the working branch**

```bash
cd "$(git rev-parse --show-toplevel)"
git switch -c feature/freshrss-feeds
```

- [ ] **Step 2: Commit the approved spec**

```bash
git add docs/superpowers/specs/2026-11-19-freshrss-feeds-design.md
git commit -m "📝 docs: add the FreshRSS feed set design"
```

---

### Task 1: The feed list and its parser

**Files:**
- Create: `hack/freshrss/feeds.opml`
- Create: `hack/freshrss_sync.py`
- Test: `hack/tests/test_freshrss_sync.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Feed` — frozen dataclass, fields `category: str`, `title: str`, `url: str`, `kind: str`
    (`"native"` or `"xpath"`).
  - `parse_opml(text: str) -> list[Feed]` — OPML text in, feeds out, duplicates collapsed.
  - `normalize_url(url: str) -> str` — strips one trailing `/`, leaves query and path case alone.
  - `DEFAULT_BASE = "https://freshrss.kub.home.ashudev.com"`.

- [ ] **Step 1: Write `hack/freshrss/feeds.opml`**

Exact content. `kind="xpath"` marks a feed that needs selectors set by hand; everything else is
native. OPML escapes `&` as `&amp;`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>AshuLab FreshRSS feed set</title></head>
  <body>
    <outline text="Jeux" title="Jeux">
      <outline type="rss" text="Guild Wars 2" title="Guild Wars 2" xmlUrl="https://www.guildwars2.com/en/feed/"/>
      <outline type="rss" text="Steam" title="Steam" xmlUrl="https://store.steampowered.com/feeds/news.xml?l=english"/>
      <outline type="rss" text="Discord blog" title="Discord blog" xmlUrl="https://discord.com/blog/rss.xml"/>
      <outline type="rss" text="Star Citizen comm-link" title="Star Citizen comm-link" kind="xpath" htmlUrl="https://robertsspaceindustries.com/comm-link"/>
      <outline type="rss" text="Final Fantasy XIV" title="Final Fantasy XIV" kind="xpath" htmlUrl="https://na.finalfantasyxiv.com/lodestone/news/"/>
      <outline type="rss" text="Elite Dangerous" title="Elite Dangerous" kind="xpath" htmlUrl="https://www.elitedangerous.com/news"/>
    </outline>
    <outline text="Dev" title="Dev">
      <outline type="rss" text="Docker blog" title="Docker blog" xmlUrl="https://www.docker.com/blog/feed/"/>
      <outline type="rss" text="Kubernetes blog" title="Kubernetes blog" xmlUrl="https://kubernetes.io/feed.xml"/>
      <outline type="rss" text="WSL2" title="WSL2" xmlUrl="https://devblogs.microsoft.com/commandline/feed/"/>
      <outline type="rss" text="Logseq blog" title="Logseq blog" xmlUrl="https://blog.logseq.com/rss/"/>
      <outline type="rss" text="Renovate blog" title="Renovate blog" kind="xpath" htmlUrl="https://renovatebot.com/blog"/>
    </outline>
    <outline text="IA" title="IA">
      <outline type="rss" text="Mistral" title="Mistral" xmlUrl="https://mistral.ai/news/rss"/>
      <outline type="rss" text="Anthropic / Claude" title="Anthropic / Claude" kind="xpath" htmlUrl="https://www.anthropic.com/news"/>
      <outline type="rss" text="DeepSeek" title="DeepSeek" kind="xpath" htmlUrl="https://api-docs.deepseek.com/news/"/>
    </outline>
    <outline text="Boutiques" title="Boutiques">
      <outline type="rss" text="Rise of Gunpla — new kits" title="Rise of Gunpla — new kits" xmlUrl="https://riseofgunpla.com/feed?post_type=product"/>
      <outline type="rss" text="KBDfans — new arrivals" title="KBDfans — new arrivals" xmlUrl="https://kbdfans.com/collections/new-arrival.atom"/>
      <outline type="rss" text="KBDfans — restock" title="KBDfans — restock" xmlUrl="https://kbdfans.com/collections/restock.atom"/>
    </outline>
    <outline text="Veille" title="Veille">
      <outline type="rss" text="selfh.st" title="selfh.st" xmlUrl="https://selfh.st/rss/"/>
      <outline type="rss" text="Phoronix" title="Phoronix" xmlUrl="https://www.phoronix.com/rss.php"/>
      <outline type="rss" text="Simon Willison" title="Simon Willison" xmlUrl="https://simonwillison.net/atom/everything/"/>
      <outline type="rss" text="Hacker News front page" title="Hacker News front page" xmlUrl="https://hnrss.org/frontpage"/>
      <outline type="rss" text="Lobsters" title="Lobsters" xmlUrl="https://lobste.rs/rss"/>
    </outline>
  </body>
</opml>
```

- [ ] **Step 2: Write the failing test**

```python
class ParseTests(unittest.TestCase):
    def test_the_shipped_opml_yields_22_feeds_in_five_categories(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        self.assertEqual(len(feeds), 22)
        self.assertEqual({f.category for f in feeds}, {"Jeux", "Dev", "IA", "Boutiques", "Veille"})

    def test_a_query_string_survives_parsing(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        rog = next(f for f in feeds if f.title.startswith("Rise of Gunpla"))
        self.assertEqual(rog.url, "https://riseofgunpla.com/feed?post_type=product")

    def test_an_xpath_feed_uses_its_html_url_and_is_marked(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        anth = next(f for f in feeds if f.title == "Anthropic / Claude")
        self.assertEqual(anth.kind, "xpath")
        self.assertEqual(anth.url, "https://www.anthropic.com/news")

    def test_a_duplicate_url_keeps_the_first_category(self):
        text = """<opml><body>
          <outline text="Jeux"><outline type="rss" text="A" xmlUrl="https://a.test/feed/"/></outline>
          <outline text="Dev"><outline type="rss" text="A again" xmlUrl="https://a.test/feed"/></outline>
        </body></opml>"""
        feeds = mod.parse_opml(text)
        self.assertEqual(len(feeds), 1)
        self.assertEqual(feeds[0].category, "Jeux")

    def test_normalize_url_strips_one_trailing_slash_and_keeps_the_query(self):
        self.assertEqual(mod.normalize_url("https://a.test/feed/"), "https://a.test/feed")
        self.assertEqual(mod.normalize_url("https://a.test/f?x=1&y=2"), "https://a.test/f?x=1&y=2")
```

- [ ] **Step 3: Run the tests, expect failure**

Run: `python3 -m unittest discover -s hack/tests -p 'test_freshrss_sync.py' -v`
Expected: `ModuleNotFoundError` or `AttributeError: module has no attribute 'parse_opml'`.

- [ ] **Step 4: Implement the module head**

`hack/freshrss_sync.py`: `Feed` dataclass, `DEFAULT_BASE`, `normalize_url`, `parse_opml`.
Parsing uses `xml.etree.ElementTree`; a feed is an `outline` with `type="rss"`; its URL is `xmlUrl`
when present, otherwise `htmlUrl`; `kind` comes from the `kind` attribute, defaulting to `"native"`;
the category is the nearest ancestor `outline`'s `text`. Duplicates are collapsed on
`normalize_url`, first occurrence wins.

- [ ] **Step 5: Run the tests, expect pass**

Run: `python3 -m unittest discover -s hack/tests -p 'test_freshrss_sync.py' -v`
Expected: 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add hack/freshrss/feeds.opml hack/freshrss_sync.py hack/tests/test_freshrss_sync.py
git commit -m "🚀 feat: add the versioned FreshRSS feed list and its parser"
```

---

### Task 2: The diff against the instance

**Files:**
- Modify: `hack/freshrss_sync.py`
- Test: `hack/tests/test_freshrss_sync.py`

**Interfaces:**
- Consumes: `Feed`, `normalize_url` from Task 1.
- Produces:
  - `Plan` — frozen dataclass, fields `to_subscribe: tuple[Feed, ...]`,
    `to_prune: tuple[tuple[str, str], ...]` (each item `(url, category)`),
    `unchanged: tuple[Feed, ...]`.
  - `plan(local: Sequence[Feed], remote: Sequence[tuple[str, str]]) -> Plan` — `remote` is
    `(url, category)` as the instance reports them.

- [ ] **Step 1: Write the failing test**

```python
class PlanTests(unittest.TestCase):
    def feed(self, url="https://a.test/feed", cat="Dev", title="A"):
        return mod.Feed(category=cat, title=title, url=url)

    def test_an_empty_instance_subscribes_everything_and_prunes_nothing(self):
        p = mod.plan([self.feed()], [])
        self.assertEqual(p.to_subscribe, (self.feed(),))
        self.assertEqual(p.to_prune, ())

    def test_a_matching_instance_is_unchanged(self):
        p = mod.plan([self.feed()], [("https://a.test/feed/", "Dev")])
        self.assertEqual(p.to_subscribe, ())
        self.assertEqual(p.unchanged, (self.feed(),))

    def test_a_moved_category_is_resubscribed(self):
        p = mod.plan([self.feed(cat="Jeux")], [("https://a.test/feed", "Dev")])
        self.assertEqual(p.to_subscribe, (self.feed(cat="Jeux"),))

    def test_a_remote_feed_absent_from_the_file_is_reported_for_pruning(self):
        p = mod.plan([], [("https://b.test/feed", "Dev")])
        self.assertEqual(p.to_prune, (("https://b.test/feed", "Dev"),))
```

- [ ] **Step 2: Run the tests, expect failure**

Run: `python3 -m unittest discover -s hack/tests -p 'test_freshrss_sync.py' -v`
Expected: `AttributeError: module has no attribute 'plan'`.

- [ ] **Step 3: Implement `Plan` and `plan`**

Compare on `normalize_url`. A remote entry matches when its normalized URL is in the local set *and*
its category equals the local one; a URL present with another category goes to `to_subscribe`
(re-subscribing moves the label). Order: `to_subscribe` follows the OPML order.

- [ ] **Step 4: Run the tests, expect pass**

Run: `python3 -m unittest discover -s hack/tests -p 'test_freshrss_sync.py' -v`
Expected: 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add hack/freshrss_sync.py hack/tests/test_freshrss_sync.py
git commit -m "🚀 feat: diff the feed file against a FreshRSS instance"
```

---

### Task 3: The GReader client

**Files:**
- Modify: `hack/freshrss_sync.py`
- Test: `hack/tests/test_freshrss_sync.py`

**Interfaces:**
- Consumes: `Feed`, `DEFAULT_BASE`.
- Produces:
  - `http(method: str, url: str, headers: dict[str, str] | None, data: bytes | None) -> tuple[int, str]`
    — module-level default transport, `urllib`-based, 30 s timeout.
  - `Client(base: str = DEFAULT_BASE, user: str = "", password: str = "", transport=http)` with
    `login() -> None`, `token() -> str`, `subscriptions() -> list[tuple[str, str]]`,
    `subscribe(feed: Feed) -> None`, `unsubscribe(url: str) -> None`.
  - `AuthError(Exception)` — raised when the instance refuses the credentials.

- [ ] **Step 1: Write the failing test**

```python
class FakeTransport:
    def __init__(self, token="TOK", subs=None, login_body="SID=x\nLSID=y\nAuth=AUTH123\n"):
        self.calls = []
        self.token, self.subs, self.login_body = token, subs or [], login_body

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers or {}, data))
        if url.endswith("/accounts/ClientLogin"):
            return 200, self.login_body
        if url.endswith("/reader/api/0/token"):
            return 200, self.token
        if "/subscription/list" in url:
            return 200, json.dumps({"subscriptions": [
                {"id": f"feed/{u}", "title": t, "categories": [{"label": c}]}
                for u, t, c in self.subs]})
        if "/subscription/edit" in url:
            return 200, "OK"
        return 404, ""


class ClientTests(unittest.TestCase):
    def test_login_posts_the_credentials_and_keeps_the_auth_line(self):
        t = FakeTransport()
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        c.login()
        method, url, _, data = t.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/accounts/ClientLogin"))
        self.assertIn(b"Email=u", data)
        self.assertIn(b"Passwd=p", data)
        self.assertEqual(c.auth, "AUTH123")

    def test_subscribe_sends_the_feed_its_label_and_a_write_token(self):
        t = FakeTransport()
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        c.subscribe(mod.Feed(category="Boutiques", title="ROG",
                             url="https://riseofgunpla.com/feed?post_type=product"))
        edit = [call for call in t.calls if "/subscription/edit" in call[1]][-1]
        body = edit[3].decode()
        self.assertIn("ac=subscribe", body)
        self.assertIn("T=TOK", body)
        self.assertIn("riseofgunpla.com%2Ffeed%3Fpost_type%3Dproduct", body)
        self.assertIn("label%2FBoutiques", body)

    def test_subscriptions_returns_url_and_category_pairs(self):
        t = FakeTransport(subs=[("https://a.test/feed", "A", "Dev")])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        self.assertEqual(c.subscriptions(), [("https://a.test/feed", "Dev")])

    def test_a_refused_login_raises_auth_error(self):
        t = FakeTransport(login_body="Error=BadAuthentication\n")
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        with self.assertRaises(mod.AuthError):
            c.login()
```

- [ ] **Step 2: Run the tests, expect failure**

Run: `python3 -m unittest discover -s hack/tests -p 'test_freshrss_sync.py' -v`
Expected: `AttributeError: module has no attribute 'Client'`.

- [ ] **Step 3: Implement `http`, `AuthError` and `Client`**

Every authenticated call carries `Authorization: GoogleLogin auth=<auth>`. `token()` calls
`GET /reader/api/0/token` and returns the trimmed body; `subscribe` posts
`ac=subscribe&s=feed/<urlencoded url>&a=user/-/label/<urlencoded category>&T=<token>`;
`unsubscribe` posts `ac=unsubscribe&s=feed/<urlencoded url>&T=<token>`. All request bodies are
`application/x-www-form-urlencoded`, and every parameter is `urllib.parse.quote`-encoded, which is
what keeps `?`, `&` and `=` inside the feed URL intact.

- [ ] **Step 4: Run the tests, expect pass**

Run: `python3 -m unittest discover -s hack/tests -p 'test_freshrss_sync.py' -v`
Expected: 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add hack/freshrss_sync.py hack/tests/test_freshrss_sync.py
git commit -m "🚀 feat: talk to the FreshRSS GReader API"
```

---

### Task 4: The command line

**Files:**
- Modify: `hack/freshrss_sync.py`
- Test: `hack/tests/test_freshrss_sync.py`

**Interfaces:**
- Consumes: `parse_opml`, `plan`, `Client`, `AuthError`.
- Produces:
  - `OPML_PATH` — `hack/freshrss/feeds.opml` resolved from `__file__`.
  - `main(argv: list[str] | None = None, transport=http, env: dict[str, str] | None = None) -> int`.
  - `verify(feeds, transport) -> list[tuple[str, str, int, str]]` — `(status, title, items, last)`,
    where `last` is the newest article date as `YYYY-MM-DD` or `""`.

Exit codes: `0` clean, `1` drift or a failed feed, `2` missing credentials or refused login.

- [ ] **Step 1: Write the failing test**

```python
class CliTests(unittest.TestCase):
    def test_check_exits_one_when_the_instance_is_missing_a_feed(self):
        t = FakeTransport(subs=[])
        code = mod.main(["check"], transport=t,
                        env={"FRESHRSS_USER": "u", "FRESHRSS_API_PASSWORD": "p"})
        self.assertEqual(code, 1)

    def test_check_exits_zero_when_every_feed_matches(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        code = mod.main(["check"], transport=t,
                        env={"FRESHRSS_USER": "u", "FRESHRSS_API_PASSWORD": "p"})
        self.assertEqual(code, 0)

    def test_apply_without_prune_never_unsubscribes(self):
        t = FakeTransport(subs=[("https://hand-added.test/feed", "Hand", "Dev")])
        mod.main(["apply"], transport=t,
                 env={"FRESHRSS_USER": "u", "FRESHRSS_API_PASSWORD": "p"})
        self.assertFalse([c for c in t.calls if b"ac=unsubscribe" in (c[3] or b"")])

    def test_apply_with_prune_unsubscribes_the_extra_feed(self):
        t = FakeTransport(subs=[("https://hand-added.test/feed", "Hand", "Dev")])
        mod.main(["apply", "--prune"], transport=t,
                 env={"FRESHRSS_USER": "u", "FRESHRSS_API_PASSWORD": "p"})
        self.assertTrue([c for c in t.calls if b"ac=unsubscribe" in (c[3] or b"")])

    def test_missing_credentials_exit_two(self):
        self.assertEqual(mod.main(["check"], transport=FakeTransport(), env={}), 2)

    def test_apply_twice_sends_no_second_subscribe(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        env = {"FRESHRSS_USER": "u", "FRESHRSS_API_PASSWORD": "p"}
        mod.main(["apply"], transport=t, env=env)
        before = len(t.calls)
        mod.main(["apply"], transport=t, env=env)
        self.assertFalse([c for c in t.calls[before:] if b"ac=subscribe" in (c[3] or b"")])

    def test_verify_reports_a_rate_limited_feed_without_raising(self):
        def transport(method, url, headers=None, data=None):
            return 429, ""
        rows = mod.verify([mod.Feed(category="Boutiques", title="KBDfans",
                                    url="https://kbdfans.test/x.atom")], transport)
        self.assertEqual(rows, [("HTTP429", "KBDfans", 0, "")])
```

- [ ] **Step 2: Run the tests, expect failure**

Run: `python3 -m unittest discover -s hack/tests -p 'test_freshrss_sync.py' -v`
Expected: `AttributeError: module has no attribute 'main'`.

- [ ] **Step 3: Implement `main` and `verify`**

`apply` prints one line per action (`+ category/title` for a subscribe, `- url` for a prune) and a
final count. `check` prints the two drift lists and returns 1 when either is non-empty. `verify`
fetches each URL with a 25 s timeout, parses it with `xml.etree`, and returns `OK` with the item
count and the newest date, `EMPTY` for zero items, `NOT-XML` for HTML, or the HTTP status; it never
raises on a network error. `--dry-run` prints the plan and changes nothing.

- [ ] **Step 4: Run the tests, expect pass**

Run: `python3 -m unittest discover -s hack/tests -p 'test_freshrss_sync.py' -v`
Expected: 20 tests pass.

- [ ] **Step 5: Commit**

```bash
git add hack/freshrss_sync.py hack/tests/test_freshrss_sync.py
git commit -m "🚀 feat: add apply, check and verify commands to the feed sync"
```

---

### Task 5: The docs page and the XPath selectors

**Files:**
- Create: `docs/news-feeds.md`
- Modify: `mkdocs.yml` (add `- News feeds: news-feeds.md` under `Reference:`)

**Interfaces:**
- Consumes: the feed list and the gaps from the spec; the selectors derived here.
- Produces: the selector table the operator applies by hand.

- [ ] **Step 1: Derive each selector and record the count it yields**

For each page, fetch it and count the item anchors. The anchor pattern is the link that identifies
one article; the selector is an XPath over the whole document, with title and link taken relative
to it.

| Feed | Anchor pattern | Expected | If it fails |
|---|---|---|---|
| Star Citizen comm-link | `href` contains `/comm-link/transmission/` | ≥ 5 | record as a gap |
| Final Fantasy XIV | `href` contains `/lodestone/news/` | ≥ 10 | record as a gap |
| Elite Dangerous | `href` contains `/news/` | ≥ 10 | record as a gap |
| Anthropic / Claude | `href` contains `/news/` | ≥ 10 | record as a gap |
| DeepSeek | `href` contains `news/news` | ≥ 3 | record as a gap |
| Renovate blog | `href` contains `/blog/` | ≥ 3 | record as a gap |

Rule: a page that yields fewer items than its Expected column is not a feed. It goes in the gaps
table with the count observed, and no feed is created for it.

- [ ] **Step 2: Write `docs/news-feeds.md`**

Sections, in this order: what the set is (one paragraph, the two rules — official only, blog only);
the table of feeds by category with the URL and how it is read (native or XPath); the gaps table
with the reason for each of the seven; the selector table from Step 1; how to re-sync
(`python3 hack/freshrss_sync.py apply`, `--check` in a cron or by hand, `verify` to see what each URL
returns today). Cite `hack/freshrss/feeds.opml` as the source of truth and say the FreshRSS database
is a cache of it.

- [ ] **Step 3: Add the nav entry**

In `mkdocs.yml`, under `Reference:`, immediately after `- Routes: reference/routes.md`:

```yaml
      - News feeds: news-feeds.md
```

- [ ] **Step 4: Verify the page builds and the links resolve**

Run: `python3 hack/check_docs_links.py`
Expected: no broken relative link reported for `docs/news-feeds.md`.

- [ ] **Step 5: Commit**

```bash
git add docs/news-feeds.md mkdocs.yml
git commit -m "📝 docs: document the feed set, its gaps and its XPath selectors"
```

---

### Task 6: Apply to the live instance and verify

**Files:** none. This task changes the instance, not the repository.

- [ ] **Step 1: Load the credentials**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
test -n "$FRESHRSS_USER" && test -n "$FRESHRSS_API_PASSWORD" && echo credentials-present
```

- [ ] **Step 2: Dry run, then apply**

```bash
python3 hack/freshrss_sync.py apply --dry-run
python3 hack/freshrss_sync.py apply
```

Expected: 22 subscribes and no unsubscribe.

- [ ] **Step 3: Confirm idempotence on the real instance**

```bash
python3 hack/freshrss_sync.py check; echo "exit=$?"
```

Expected: `exit=0` and no drift listed.

- [ ] **Step 4: Verify every feed returns items**

```bash
python3 hack/freshrss_sync.py verify
```

Expected: `OK` for the 16 native feeds. The XPath feeds report `NOT-XML` until their selectors are
set, which is correct and expected at this point.

- [ ] **Step 5: Set the XPath selectors by hand**

In FreshRSS, for each of the six XPath feeds: Feed management → the feed → *Advanced* → feed type
`HTML + XPath`, then paste the item, title and link expressions recorded in
`docs/news-feeds.md`. Feeds that Step 1 of Task 5 demoted to the gaps table are unsubscribed
instead.

- [ ] **Step 6: Confirm the scraped feeds produce items**

```bash
python3 hack/freshrss_sync.py check; echo "exit=$?"
```

Expected: `exit=0`. Then in the web UI each XPath feed shows at least one article, and no feed shows
an error banner.

- [ ] **Step 7: Report the result to the operator**

State, per category, how many feeds are live, which ones are gaps, and the count each XPath feed
produced on its first fetch. Name the instance URL and the user the sync ran as.
