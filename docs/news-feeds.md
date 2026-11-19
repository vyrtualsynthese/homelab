# News feeds

FreshRSS at `https://freshrss.kub.home.ashudev.com` is the main news intake. This page describes the
feed set, the sources that have no feed, how much of each article you get, and how to replay the
list onto a rebuilt instance.

Two rules decide what is in the set:

- **Official sources only.** No Reddit, no Google News, no re-post. If the publisher does not
  publish it, it is not in the set.
- **Blog and news only.** No changelog, no release notes, no patch notes. A topic whose only
  official output is a changelog has no feed here.

## Where the list lives

`hack/freshrss/feeds.opml` is the source of truth. The FreshRSS database is a cache of it: a
rebuilt instance gets the same set by replaying the file.

```bash
python3 hack/freshrss_sync.py apply --dry-run   # show what would change
python3 hack/freshrss_sync.py apply             # subscribe what the file lists
python3 hack/freshrss_sync.py check             # exit 1 when a listed feed is missing
python3 hack/freshrss_sync.py verify            # fetch every URL, print items and newest date
```

`check` fails only on a feed the file lists and the instance lacks. A feed the instance holds and
the file does not is printed with a `-` and does not fail the run: `apply` never removes one without
`--prune`, so an extra is not drift.

`verify` fetches every URL and exits 1 when a native feed answers badly; an XPath feed reports
`NOT-XML` by design, because the raw URL is a page.

`FRESHRSS_BASE` overrides the instance URL, which is how a scratch instance gets tested.

`apply` never unsubscribes anything unless `--prune` is passed, so a feed added by hand in the web
UI survives a replay. Credentials are `FRESHRSS_USER` and `FRESHRSS_API_PASSWORD` from
`~/.config/secrets/homelab.env`.

`verify` fetches each URL from the machine that runs it, not from inside the cluster, so a 403 or
429 there can be the runner's IP rather than the feed being broken.

## The feeds

*Native* means the publisher ships RSS or Atom. *XPath* means FreshRSS reads the official page and
picks items out of the HTML, with selectors that travel in the OPML (see "XPath selectors").

### Jeux

| Feed | How | URL |
|---|---|---|
| Guild Wars 2 | native | `https://www.guildwars2.com/en/feed/` |
| Steam | native | `https://store.steampowered.com/feeds/news.xml?l=english` |
| Discord blog | native | `https://discord.com/blog/rss.xml` |
| Star Citizen comm-link | XPath | `https://robertsspaceindustries.com/comm-link` |
| Final Fantasy XIV | XPath | `https://na.finalfantasyxiv.com/lodestone/news/` |
| Elite Dangerous | XPath | `https://www.elitedangerous.com/news` |

### Dev

| Feed | How | URL |
|---|---|---|
| Docker blog | native | `https://www.docker.com/blog/feed/` |
| Kubernetes blog | native | `https://kubernetes.io/feed.xml` |
| WSL2 | native | `https://devblogs.microsoft.com/commandline/feed/` |
| Logseq blog | native | `https://blog.logseq.com/rss/` |

The Logseq blog is dormant: its newest article is from August 2024. The feed stays because the
project may resume; it costs one line in the folder.

### IA

| Feed | How | URL |
|---|---|---|
| Mistral | native | `https://mistral.ai/news/rss` |
| Anthropic / Claude | XPath | `https://www.anthropic.com/news` |

Mistral serves its feed as `content-type: text/plain`, so the instance parses it only when the
subscription URL carries FreshRSS's force-feed marker: the file asks for
`https://mistral.ai/news/rss#force_feed`, and that is the subscription the instance holds. The API
also refuses the plain URL at creation (HTTP 500 from `subscription/edit`). See "Feeds the API will
not take" below.

OpenAI and ChatGPT were dropped by decision, not for lack of a feed: its official feed carries 1413
items of backlog.

### Boutiques

| Feed | How | URL |
|---|---|---|
| Rise of Gunpla — new kits | native | `https://riseofgunpla.com/feed?post_type=product` |
| KBDfans — new arrivals | native | `https://kbdfans.com/collections/new-arrival.atom` |
| KBDfans — restock | native | `https://kbdfans.com/collections/restock.atom` |

The KBDfans collections rate-limit (HTTP 429) when polled hard. FreshRSS polls on a schedule, which
is far below the limit; a `verify` run that reports 429 is the runner asking too often.

The API refuses both KBDfans collections at creation: `subscription/edit` answers 500 while the same
URLs answer 200 from a workstation. The OPML import subscribes them anyway, and as of 2026-11-19
the instance holds them with no entries — the fetch from the pod is what fails. See "Feeds the API
will not take" below.

An old Rise of Gunpla kit coming back in stock is **not** covered. FreshRSS drops an item it
already holds, so the same product cannot fire twice. That needs a change tracker, which is a
separate decision.

### Veille

| Feed | How | URL |
|---|---|---|
| selfh.st | native | `https://selfh.st/rss/` |
| Phoronix | native | `https://www.phoronix.com/rss.php` |
| Simon Willison | native | `https://simonwillison.net/atom/everything/` |
| Hacker News front page | native | `https://hnrss.org/frontpage` |
| Lobsters | native | `https://lobste.rs/rss` |

## Sources with no feed

These were asked for and cannot be read officially today. Each one is a hole, not a substitute
through an aggregator.

| Source | Why there is no feed |
|---|---|
| Guild Wars 3 | No official site yet. ArenaNet announcements arrive in the Guild Wars 2 feed. |
| NieR | No active official news channel; the last game's site is dormant. |
| Umamusume | `umamusume.com` is a JavaScript app with no server-rendered list. |
| Twitch blog | `blog.twitch.tv` renders client-side and publishes no feed. |
| Gateron | Cloudflare answers 403 to non-browser clients on `/blog` and `/collections`. |
| ZSH | No blog. The official channel is the `zsh-announce` mailing list. |
| opencode | Only `/changelog`, which is release notes and excluded by the blog-only rule. |
| DeepSeek | `api-docs.deepseek.com/news/` exposes one article link; `deepseek.com/en/news` refuses non-browser clients. |
| Renovate | `renovatebot.com/blog` has no feed and now renders Mend's corporate blog. |

Renovate has a near miss worth knowing about: `https://www.mend.io/feed/` is a working official
feed, but it carries Mend's company blog, not Renovate's. It is not in the set; add it by hand if
that trade is worth it.

## XPath selectors

The four XPath feeds carry their selectors in the OPML itself, as `frss:xPathItem*` attributes —
the format FreshRSS writes on export and reads on import. So they are configured by one import, not
by four visits to the feed settings.

As of 2026-11-19 all four are on the live instance. The check is the instance's own export
(`/reader/api/0/subscription/export`): it carries the same four `frss:xPathItem` and twelve
`frss:cssFullContent` attributes as the file, feed for feed.

An import skips a feed the instance already holds, which is also the catch: changing a selector in
the file does not reach an existing feed. Unsubscribe that feed, then import again. The unsubscribe
is reachable with the API password alone — `subscription/edit` with `ac=unsubscribe`, one call per
URL, the call `apply --prune` already makes — so only the import needs the web UI: *Subscription
management* → *Import / export* → *OPML* → choose `hack/freshrss/feeds.opml` → *Upload*.

| Feed | Item | Title | Link |
|---|---|---|---|
| Star Citizen comm-link | `//a[contains(@href,'/comm-link/transmission/')]` | `normalize-space(.)` | `@href` |
| Final Fantasy XIV | `//a[contains(@href,'/lodestone/news/detail/')]` | `normalize-space(.)` | `@href` |
| Elite Dangerous | `//a[starts-with(@href,'/news/')]` | `normalize-space(.)` | `@href` |
| Anthropic / Claude | `//a[starts-with(@href,'/news/')]` | `normalize-space(.)` | `@href` |

Entries the instance held on 2026-11-19: Star Citizen 9, Final Fantasy XIV 9, Elite Dangerous 42,
Anthropic 48.

These are the brittle part of the set. A page redesign breaks them silently, and `verify` reports
`NOT-XML` for an XPath feed because the raw URL is HTML — that is expected, not a failure. Check
the folder in the web UI instead.

## Images and full articles

Two settings decide how much of a story you see, and they are separate.

**Thumbnails in the list** are a display preference of the account, not of the feed:
*Configuration* → *Display* → *Show article thumbnails* (`topline_thumbnail` in the user config,
one of `none`, `portrait`, `square`, `landscape`). It is on unless it was set to `none`; the
default is to show them. No API reaches it, so it is the one setting on this page that is not in
git.

**The article body** is per feed: FreshRSS fetches the article page and replaces the feed's summary
with the part of that page a CSS selector picks (a CSS selector is the rule a stylesheet uses to
target one element — `.entry-content` means "the element carrying the `entry-content` class"). The
selector travels in the OPML as `frss:cssFullContent`, so a rebuilt instance gets it from the file.

Twelve feeds carry a selector, each checked against the newest article on 2026-11-19:

| Feed | Selector | Text taken from one article |
|---|---|---|
| Guild Wars 2 | `#content` | 3.4 kB |
| Discord blog | `article` | 6.9 kB |
| Docker blog | `.entry-content` | 13.9 kB |
| Kubernetes blog | `main` | 9.9 kB |
| WSL2 | `.entry-content` | 10.0 kB |
| Logseq blog | `article` | 10.4 kB |
| Rise of Gunpla — new kits | `.entry-content` | 4.0 kB |
| selfh.st | `article` | 8.4 kB |
| Phoronix | `article` | 5.3 kB |
| Final Fantasy XIV | `article` | 5.4 kB |
| Elite Dangerous | `#content` | 34.0 kB |
| Anthropic / Claude | `article` | 3.4 kB |

What it costs: one extra request per new article, made by the pod, so the source sees the cluster's
address rather than yours. What it breaks on: a redesign empties the article without an error, and
`check` cannot see it — the API does not expose feed settings, so a broken selector looks like a
feed with short entries. Check the reading view after a site redesign.

Eight feeds carry a picture in the item already, so the list shows a thumbnail without a selector:
Discord, Docker, Kubernetes, WSL2, Logseq, selfh.st, Simon Willison and Guild Wars 2. For the other
five that have a selector — Phoronix, Rise of Gunpla, Final Fantasy XIV, Elite Dangerous and
Anthropic — the selector is what brings the pictures, since the feed itself carries none.

Four get nothing, and the reason is the source rather than a missing setting:

| Feed | Why not |
|---|---|
| Steam | the items are store announcements, already the whole text, and 2 images across 20 items |
| Star Citizen comm-link | the article is built by JavaScript, so the HTML the fetcher receives is empty |
| Lobsters | the item is a link to someone else's site; fetching it would copy their page |
| Hacker News | same |

Mistral and the two KBDfans collections carry no selector either, for a different reason: the file
gives them none. Mistral arrives as the publisher's summary; the KBDfans feeds have delivered
nothing so far (see "Feeds the API will not take").

The twelve are on the live instance as of 2026-11-19. They got there by unsubscribing the ten feeds
the instance held and importing the file once — an import cannot change a feed it already holds. The
evidence is the instance's own export, which carries the twelve `frss:cssFullContent` values, and
the newest entries themselves: between 1 kB and 80 kB of the publisher's HTML, where the feed alone
carries a summary.

## Feeds the API will not take

Three feeds are in the file and `subscription/edit` answers HTTP 500 for each, while the same URLs
return a valid feed from a workstation. The OPML import subscribes all three anyway, because it does
not ask the instance to fetch the URL first, so the feeds exist and the open question is whether
they deliver.

| Feed | URL | State on 2026-11-19 |
|---|---|---|
| Mistral | `https://mistral.ai/news/rss#force_feed` | 94 entries as the publisher's summary; the plain URL delivered nothing, the marker is what changed it |
| KBDfans — new arrivals | `https://kbdfans.com/collections/new-arrival.atom` | subscribed, zero entries after a refresh run |
| KBDfans — restock | `https://kbdfans.com/collections/restock.atom` | same |

`check` passes on all three: the file asks for them and the instance holds them. It cannot see that
the two KBDfans feeds are silent.

What was ruled out for KBDfans: rate limiting (the URLs answered 200 from a workstation at the time
of the retry) and a typo (the URLs are the ones verified in the design). What is still unchecked is
the pod's own log, because the read-only ServiceAccount cannot exec into it.

Next step when someone has cluster access: read
`/config/www/freshrss/data/users/<user>/log.txt` in the `news` namespace for the fetch error, then
set the per-feed user agent or the feed type in the UI accordingly.

## Categories

`Jeux`, `Dev`, `IA`, `Boutiques`, `Veille`. Nothing else; `Uncategorized` stays empty.
