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
python3 hack/freshrss_sync.py verify            # fetch every URL, check each full-content selector
```

`apply` and `check` also cover the per-feed settings the file carries: the CSS selector that pulls
the whole article, and an XPath feed's selectors and kind. Those live behind the web login, so they
are only checked when `FRESHRSS_WEB_PASSWORD` is set; without it the run prints `settings: not
checked` and does the rest. See "The settings the API cannot reach" below.

`check` fails on three things: a feed the file lists and the instance lacks, a listed feed sitting in
another category, and a listed feed whose content selectors differ from the file. A feed the instance
holds and the file does not is printed with a `-` and does not fail the run: `apply` never removes
one without `--prune`, so an extra is not drift. A label counts as in place when it is one of the
labels the instance reports, so a feed that also carries a second, stale label is not drift either.
Exit codes are `0` clean, `1` any of the above, `2` credentials missing or refused — which includes
the web login.

`verify` fetches every URL and exits 1 when a feed does not answer, or when a full-content selector
picks nothing out of the newest article.

A feed whose items come from an XPath expression is printed `OK` with no item count: its raw URL is
a page, not a feed. When such a feed carries a full-content selector, `verify` follows the first link
its item expression picks and reads that page.

`FRESHRSS_BASE` overrides the instance URL, which is how a scratch instance gets tested.

`apply` never unsubscribes anything unless `--prune` is passed, so a feed added by hand in the web
UI survives a replay. Credentials are `FRESHRSS_USER` and `FRESHRSS_API_PASSWORD` from
`~/.config/secrets/homelab.env`, plus `FRESHRSS_WEB_PASSWORD` for the settings half.

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
| Docker blog | native | `https://www.docker.com/feed/` |
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

Mistral serves its feed as `content-type: text/plain`, and the parser FreshRSS uses refuses that
content type before it looks at the body. The subscription therefore carries FreshRSS's force-feed
marker: the file asks for `https://mistral.ai/news/rss#force_feed`, which tells the parser to read
the body without the check. The plain URL is what returns nothing. See "Feeds the instance cannot
fetch" below.

OpenAI and ChatGPT were dropped by decision, not for lack of a feed: its official feed carries 1413
items of backlog.

### Boutiques

| Feed | How | URL |
|---|---|---|
| Rise of Gunpla — new kits | native | `https://riseofgunpla.com/feed?post_type=product` |

Two KBDfans collections — new arrivals and restock — were in the set until 2026-11-19 and left it:
the instance cannot fetch them, for the client-fingerprint reason in "KBDfans: left out" below.

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
the format FreshRSS writes on export and reads on import.

Importing the file is how the feeds are *created*: *Subscription management* → *Import / export* →
*OPML* → choose `hack/freshrss/feeds.opml` → *Upload*. That route skips a feed the instance already
holds, so it cannot change one that exists, and the API refuses to subscribe a page that is not a
feed. A selector edited in the file therefore reaches the instance through the sync:

```bash
python3 hack/freshrss_sync.py check    # exit 1 when a feed's selectors differ from the file
python3 hack/freshrss_sync.py apply    # write the file's values onto the feeds
```

`apply` reads each feed's settings page, resubmits that same form with the file's values, and reads
it again to confirm; `check` does the read without the write. A feed whose kind is wrong for the
file — created as RSS when the file says XPath — is repaired in the same pass, because the kind and
the XPath fields are submitted together.

| Feed | Item | Title | Link |
|---|---|---|---|
| Star Citizen comm-link | `//a[contains(@href,'/comm-link/transmission/')]` | `normalize-space(.)` | `@href` |
| Final Fantasy XIV | `//a[contains(@href,'/lodestone/news/detail/')]` | `normalize-space(.)` | `@href` |
| Elite Dangerous | `//a[starts-with(@href,'/news/')]` | `normalize-space(.)` | `@href` |
| Anthropic / Claude | `//a[starts-with(@href,'/news/')]` | `normalize-space(.)` | `@href` |

Counts observed on 2026-11-19, from the live pages: Star Citizen 9, Final Fantasy XIV 9, Elite
Dangerous 57, Anthropic 26.

These are the brittle part of the set: a redesign empties them without an error, and `check` cannot
see that — the selector still matches the file. `verify` follows the first link the item expression
picks and reads that page through the full-content selector, so a dead selector shows as
`sel … empty (path)`. When the expression stops matching links, the same check prints
`sel … unchecked` instead of failing, so the item list itself still needs a look in the web UI after
a redesign.

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

Twelve feeds carry a selector. The column is what `verify` printed on 2026-11-19, read from the
newest article of each feed:

| Feed | Selector | Text taken from one article (characters) |
|---|---|---|
| Guild Wars 2 | `#content` | 2794 |
| Discord blog | `article` | 2106 |
| Docker blog | `.entry-content` | 20013 |
| Kubernetes blog | `main` | 9336 |
| WSL2 | `.entry-content` | 5693 |
| Logseq blog | `article` | 9519 |
| Rise of Gunpla — new kits | `.entry-content` | 461 |
| selfh.st | `article` | 10899 |
| Phoronix | `article` | 1522 |
| Final Fantasy XIV | `article` | 1435 |
| Elite Dangerous | `#content` | 538 |
| Anthropic / Claude | `article` | 11174 |

What it costs: one extra request per new article, made by the pod, so the source sees the cluster's
address rather than yours. What it breaks on: a redesign empties the article without an error, and
`check` cannot see that — the selector still matches the file, it no longer matches the page.
`verify` is what catches it: it reads the newest article through the selector and exits 1 when the
extraction is empty, naming the page it read.

`verify` understands one tag with an id and/or classes (`article`, `#content`, `.entry-content`,
`div#main.box`) or a comma list of those. A selector built from a descendant combinator, an
attribute test or a pseudo-class is outside what it reads, so it reports `unsupported` instead of
passing silently — extend the reader before using one.

On 2026-11-19 every one of the twelve reads a body: the smallest is Rise of Gunpla at 461
characters (the newest kit is a short description), the largest Docker blog at 20013.

Eight feeds carry a picture in the item already, so the list shows a thumbnail without a selector:
Discord, Docker, Kubernetes, WSL2, Logseq, selfh.st, Simon Willison and Guild Wars 2. Seven of those
eight also carry a selector. The five that carry a selector without a picture in the item are
Phoronix, Rise of Gunpla, Final Fantasy XIV, Elite Dangerous and Anthropic — there the selector is
what brings the pictures, since the feed itself carries none.

Five get nothing, and the reason is the source rather than a missing setting:

| Feed | Why not |
|---|---|
| Steam | the items are store announcements, already the whole text, and 2 images across 20 items |
| Star Citizen comm-link | the article is built by JavaScript, so the HTML the fetcher receives is empty |
| Lobsters | the item is a link to someone else's site; fetching it would copy their page |
| Hacker News | same |
| Mistral | served as `text/plain`, so the URL needs the force-feed marker; the publisher's summary is the whole article |

On the live instance the twelve feeds exist already, and the OPML import skips a feed it already
holds, so the selectors do not reach them that way. `apply` is what writes them: it puts the file's
`frss:cssFullContent` into the feed's *Retrieve full content* field, so a selector edited in the
file reaches the feed on the next run instead of needing a delete and a re-import.

## The settings the API cannot reach

The GReader API carries no feed attributes, so nothing in it sets an XPath selector or the CSS
selector of the article body. The web form that does is a plain POST at
`?c=subscription&a=feed&id=<feed>`, and the only thing in front of it is the form login: a bcrypt
challenge, where the browser hashes the password against the salt half of the stored hash and then
hashes that plus a one-time nonce from the session. The API password does not pass it, and nor does
a bearer token.

`hack/freshrss_sync.py` repeats that challenge, which needs the `bcrypt` package —
`pip install bcrypt`, the one dependency the script does not get from the standard library. It
reads the feed id from the subscription list, opens the feed's settings page, resubmits the form
with the file's values, and reads the page back to confirm; a field that still differs is printed
and the run exits 1. Without `FRESHRSS_WEB_PASSWORD` the settings half is skipped and the
subscription half behaves as before.

## Feeds the instance cannot fetch

Three feeds answered a blank HTTP 500 from `subscription/edit` while the same URLs return a valid
feed from a workstation. The 500 was not a refusal: it is what the API answers when its own logger
cannot write. Behind it are two different problems, one solved and one that cost the two KBDfans
collections their place in the set.

### What the empty 500 hid

The 500 carried no message because FreshRSS logs API errors to `data/users/_/log_api.txt`, and that
directory was missing from the claim on 2026-11-19, although the image carries one. The logger's
write fails, throws, and PHP dies mid-request before the API can answer anything but an empty 500.

Restore it and the API answers `400 Bad Request` instead, with the reason in that file:

```bash
kubectl -n news exec deploy/news-freshrss -- mkdir -p /config/www/freshrss/data/users/_
kubectl -n news exec deploy/news-freshrss -- chown abc:abc /config/www/freshrss/data/users/_
```

Check for the directory after a claim is rebuilt, and run those two commands if it is missing. The
PHP fatal that stands in for the message is in `/config/log/nginx/error.log` in the pod, which is
where every PHP message lands — none of them reach the pod's stdout, and so none reach Loki.

### Mistral: served as plain text

SimplePie, the parser FreshRSS fetches with, checks the response's `content-type` before parsing and
rejects `text/plain` even when the body is valid RSS. Mistral serves its feed that way, which is why
the same URL parses on a workstation and delivered nothing here. FreshRSS's escape is the
`#force_feed` marker at the end of the URL: it tells the parser to read the body without the check,
and the marker travels in the OPML, so a replay reproduces it. The subscription is
`https://mistral.ai/news/rss#force_feed`, and it held 94 entries on 2026-11-19.

### KBDfans: left out

Both collections are valid Atom feeds and a workstation fetches them with `200`; the instance cannot
have them. Cloudflare in front of kbdfans answers `429 local_rate_limited` — sometimes `403` — to the
pod's requests, while the same request from a workstation on the same public IP is served `200`.
The difference is the client, not the URL, the network or the polling cadence.

Measured on 2026-11-19 from the pod, one request each, a minute apart:

| Client | Answer |
|---|---|
| `curl` 8.23.0, the image's libcurl, which is what FreshRSS fetches with | `429 local_rate_limited` |
| `openssl s_client`, same pod and same minute | `200 OK` |

That points at the TLS fingerprint (the handshake details a bot filter matches against a list of
known clients) rather than a header: the per-feed user agent the feed settings offer, set to a
Chrome string and tried against the live feed, did not change the answer, and no feed setting
touches the TLS stack or the HTTP version. So the two collections left the set on 2026-11-19: the
file no longer lists them and the two empty subscriptions were removed from the instance.

Re-test when the image's curl changes. `lscr.io/linuxserver/freshrss` moves its libcurl with each
base image, and a client fingerprint Cloudflare tolerates is the whole requirement — the feeds are
fine.

One detail worth knowing if a shop feed is ever added again: after a `429` the instance stores a
per-domain backoff (25 minutes by default) in `data/Retry-After/<domain>.txt` and refuses to touch
that domain until it expires, logging `For that domain, will first retry after …`. A retry inside
that window never reaches the site.

## Categories

`Jeux`, `Dev`, `IA`, `Boutiques`, `Veille`. Nothing else; `Uncategorized` stays empty.
