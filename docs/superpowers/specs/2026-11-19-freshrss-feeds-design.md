# FreshRSS feed set — design

Date: 2026-11-19

## Intent

FreshRSS becomes the main news intake. The instance is live
(`freshrss.kub.home.ashudev.com`, user `vronteix`, API working) and holds one feed and no
categories, so this is a fresh build.

Two rules come from the operator and shape everything below:

- **Official sources only.** No Reddit, no aggregators (Google News included), no third-party
  re-posts.
- **Blog and news only.** No changelogs, no release notes, no patch notes.

Notifications already work through the phone app; this design does not touch that.

## Goals

- One feed per topic, in a category that says what it is, so a 10-minute scan clears the day.
- Every feed verified to return items today, with the last article date recorded.
- The list lives in git and can be replayed: FreshRSS's database is a cache, the OPML file is the
  source of truth.
- Gaps are written down, not papered over with a noisy substitute.

## Non-goals

- Notifications, alerting, or push (already handled by the phone app).
- Restock detection (see "Deferred").
- Any feed for a source that only publishes changelogs.

## Anti-goals

- A feed set that needs daily pruning. If a category is unreadable after a week, it is wrong.
- Silent breakage: a scraped feed that returns nothing must be visible, not a quiet empty folder.

## The feeds

Legend: **native** = the site publishes RSS/Atom; **XPath** = FreshRSS reads the official page and
picks items out of the HTML with a selector rule (a one-time setup in the web UI, since the
subscription API cannot carry selectors); **gap** = no official machine-readable source exists.

### Jeux

| Source | How | URL / state |
|---|---|---|
| Guild Wars 2 | native | `https://www.guildwars2.com/en/feed/` — 10 items, last 2026-11-10 |
| Guild Wars 3 | gap | No official site or feed. ArenaNet's announcements land in the Guild Wars 2 feed; nothing else is official. |
| Star Citizen | XPath | `https://robertsspaceindustries.com/comm-link` — server-rendered, 31 comm-link links. (`/comm-link/rss` is advertised on the page but returns HTML.) |
| Final Fantasy XIV | XPath | `https://na.finalfantasyxiv.com/lodestone/news/` — server-rendered, 24 news links. No RSS since the Lodestone redesign. |
| NieR | gap | No active official news source; the last game's site is dormant. |
| Umamusume | gap | `umamusume.com` is a JavaScript app; the page has no server-rendered list. |
| Elite Dangerous | XPath | `https://www.elitedangerous.com/news` — server-rendered, per-article links. |
| Steam | native | `https://store.steampowered.com/feeds/news.xml?l=english` — 20 items, platform news |
| Discord | native | `https://discord.com/blog/rss.xml` — 100 items, last 2026-11-16 |
| Twitch | gap | `blog.twitch.tv` renders client-side and publishes no feed. |

### Dev

| Source | How | URL / state |
|---|---|---|
| Docker | native | `https://www.docker.com/blog/feed/` — 10 items, last 2026-11-18 |
| Kubernetes | native | `https://kubernetes.io/feed.xml` — 50 items, last 2026-11-16 |
| WSL2 | native | `https://devblogs.microsoft.com/commandline/feed/` — 10 items, last 2026-11-04 |
| Logseq | native | `https://blog.logseq.com/rss/` — 8 items, **last 2024-08-31** (blog is dormant) |
| Renovate | XPath | `https://renovatebot.com/blog` — server-rendered listing |
| ZSH | gap | No blog. The official channel is the `zsh-announce` mailing list; no feed. |
| opencode | gap | Only `/changelog`, which is release notes and excluded by the blog-only rule. |

### IA

| Source | How | URL / state |
|---|---|---|
| Mistral | native | `https://mistral.ai/news/rss` — 94 items |
| Anthropic / Claude | XPath | `https://www.anthropic.com/news` — server-rendered, 35 news links |
| DeepSeek | XPath | `https://api-docs.deepseek.com/news/` — server-rendered, `news/newsNNNNNN` items |

OpenAI and ChatGPT are dropped by decision: the only official feed carries 1413 items of history and
the topic is not worth the noise.

### Boutiques

| Source | How | URL / state |
|---|---|---|
| Rise of Gunpla — new kits | native | `https://riseofgunpla.com/feed?post_type=product` — 10 items, last 2026-11-12 |
| KBDfans — new arrivals | native | `https://kbdfans.com/collections/new-arrival.atom` — answered 429 (rate limit) from this host; needs a check from inside the cluster |
| KBDfans — restock | native | `https://kbdfans.com/collections/restock.atom` — same |
| Gateron | gap | Cloudflare answers 403 to non-browser clients on `/blog` and `/collections`. |

### Veille (suggestions, operator picks)

Original publishers, not aggregators:

| Source | URL |
|---|---|
| selfh.st (self-hosting weekly) | `https://selfh.st/rss/` |
| Phoronix (Linux, GPU, kernel) | `https://www.phoronix.com/rss.php` |
| Simon Willison (AI, practical) | `https://simonwillison.net/atom/everything/` |
| Hacker News front page | `https://hnrss.org/frontpage` |
| Lobsters | `https://lobste.rs/rss` |

## Decisions

1. **Native first, XPath second, nothing third.** A source without a feed gets an XPath rule only
   if its page is server-rendered today. A source that fails both is a documented gap, not a
   workaround through an aggregator.
2. **XPath feeds are a one-time manual step.** The GReader subscription API sets a URL and a
   category; it cannot set selectors. The exact selectors ship in this repo, applied once per feed
   in the web UI. Re-running the sync script must not reset them.
3. **Source of truth is `hack/freshrss/feeds.opml`.** The sync script replays it: create the
   category, subscribe, idempotent. Its `check` mode reports drift in both directions (feed in
   FreshRSS but not the file, and the reverse).
4. **No per-feed hacks in the script.** Anything the OPML cannot express goes in the docs page,
   with the reason.
5. **Five categories: Jeux, Dev, IA, Boutiques, Veille.** Uncategorized is emptied.
6. **OpenAI/ChatGPT is not in the set.** Its official feed is 1413 items of backlog for a topic the
   operator chose to drop.

## Resolved with the operator (2026-11-19)

- The seven gaps stay as documented holes; no topic is dropped.
- "mykbdfan" is **KBDfans** (`kbdfans.com`), confirmed.
- OpenAI/ChatGPT is dropped, not capped.

## Deferred

- **Restock tracking** (Rise of Gunpla, and old kits returning). FreshRSS drops an item it already
  has, so it cannot fire twice for the same product. This is the one genuinely stateful need and it
  waits for `changedetection.io` (option 2), which was explicitly postponed.
- **Gateron and Twitch blog.** Both need a browser-grade fetcher, which is the same
  `changedetection.io` phase.
- **Umamusume.** Its JavaScript app calls an API; wiring that would be a custom bridge, not a feed.

## Deliverables

- `hack/freshrss/feeds.opml` — the list, categories as OPML outline groups.
- `hack/freshrss_sync.py` — `apply` replays the file, `check` reports drift, `verify` fetches every
  URL and prints what came back. Credentials from `FRESHRSS_USER` / `FRESHRSS_API_PASSWORD`.
- `hack/tests/test_freshrss_sync.py` — the parser, the diff and the client, without network.
- `docs/news-feeds.md` — the table above plus the XPath selectors, linked from `mkdocs.yml`.

## Verification

- Every feed returns items after sync; the script prints title, item count and latest date.
- `hack/freshrss_sync.py check` exits 0 on a clean replay.
- `verify` prints item count and newest date per feed, so a scraper that has gone quiet is visible
  the next time the operator runs it. There is no automatic staleness alert: the instance exposes no
  per-feed date through the API, and a background job for it is not worth the moving part.

## Open questions

None. The three questions raised on 2026-11-19 are settled above.
