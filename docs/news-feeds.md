# News feeds

FreshRSS at `https://freshrss.kub.home.ashudev.com` is the main news intake. This page describes the
feed set, the sources that have no feed, and how to replay the list onto a rebuilt instance.

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
python3 hack/freshrss_sync.py check             # exit 1 when instance and file disagree
python3 hack/freshrss_sync.py verify            # fetch every URL, print items and newest date
```

`apply` never unsubscribes anything unless `--prune` is passed, so a feed added by hand in the web
UI survives a replay. Credentials are `FRESHRSS_USER` and `FRESHRSS_API_PASSWORD` from
`~/.config/secrets/homelab.env`.

`verify` fetches each URL from the machine that runs it, not from inside the cluster, so a 403 or
429 there can be the runner's IP rather than the feed being broken.

## The feeds

*Native* means the publisher ships RSS or Atom. *XPath* means FreshRSS reads the official page and
picks items out of the HTML, which needs the selectors below set once by hand.

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

Set once per feed in FreshRSS: Feed management → the feed → *Advanced* → feed type `HTML + XPath`.
The item selector picks one article; title and link are read relative to it.

| Feed | Item | Title | Link |
|---|---|---|---|
| Star Citizen comm-link | `//a[contains(@href,'/comm-link/transmission/')]` | `normalize-space(.)` | `@href` |
| Final Fantasy XIV | `//a[contains(@href,'/lodestone/news/detail/')]` | `normalize-space(.)` | `@href` |
| Elite Dangerous | `//a[starts-with(@href,'/news/')]` | `normalize-space(.)` | `@href` |
| Anthropic / Claude | `//a[starts-with(@href,'/news/')]` | `normalize-space(.)` | `@href` |

Counts observed on 2026-11-19, from the live pages: Star Citizen 9, Final Fantasy XIV 9, Elite
Dangerous 57, Anthropic 26.

These are the brittle part of the set. A page redesign breaks them silently, and `verify` reports
`NOT-XML` for an XPath feed because the raw URL is HTML — that is expected, not a failure. Check
the folder in the web UI instead.

## Categories

`Jeux`, `Dev`, `IA`, `Boutiques`, `Veille`. Nothing else; `Uncategorized` stays empty.
