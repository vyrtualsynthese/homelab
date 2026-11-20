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
python3 hack/freshrss_sync.py state             # what the instance holds, per feed; fetches nothing
python3 hack/freshrss_sync.py drop <url>…       # unsubscribe, so an import can re-create with settings
```

`state` reads the instance and nobody else: one line per feed, either the date the instance last
stored an item and that item's title, or `never` when every poll has failed. It is the command to
reach for while a publisher rate-limits, because `verify` fetches from the runner and spends the
budget the pod needs — see "KBDfans" below.

`apply` and `check` also cover the per-feed settings the file carries: the CSS selector that pulls
the whole article, an XPath feed's selectors and kind, and a poll interval (`frss:ttl`, in seconds)
where a host must not be asked often. Those live behind the web login, so they are only checked when
`FRESHRSS_WEB_PASSWORD` is set; without it the run prints `settings: not checked` and does the rest.
An instance that signs in through OIDC has no form login to open at all, and its settings half is
skipped the same way, with the reason printed. See "The settings the API cannot reach" below.

`check` fails on three things: a feed the file lists and the instance lacks, a listed feed sitting in
another category, and a listed feed whose content selectors differ from the file. A feed the instance
holds and the file does not is printed with a `-` and does not fail the run: `apply` never removes
one without `--prune`, so an extra is not drift. A second row for an address the file already lists
is an extra too — the instance can hold one feed twice, once with `#force_feed` and once without,
and only the row that spells the file's address exactly is kept. A label counts as in place when it
is one of the labels the instance reports, so a feed that also carries a second, stale label is not
drift either. Two things are left out of the comparison: an address is compared without its
fragment, so the `#force_feed` marker is not a different feed, and the instance's own release feed
(`https://github.com/FreshRSS/FreshRSS/releases.atom`), which FreshRSS subscribes itself to and the
file can never list.
Exit codes are `0` clean, `1` any of the above, `2` credentials missing or refused — which includes
the web login.

`verify` fetches every URL and exits 1 when a feed does not answer, or when a full-content selector
picks nothing out of the newest article.

A feed whose items come from an XPath expression is printed `OK` with no item count: its raw URL is
a page, not a feed. When such a feed carries a full-content selector, `verify` follows the first link
its item expression picks and reads that page. An item expression is read when it tests `@href` with
`contains` or `starts-with`, optionally narrowed to a container class and to links that must not
carry a fragment; any other expression prints `sel … unchecked` rather than being guessed at.

`FRESHRSS_BASE` overrides the instance URL, which is how a scratch instance gets tested.

`apply` never unsubscribes anything unless `--prune` is passed, so a feed added by hand in the web
UI survives a replay — and a prune the instance refuses is reported with a `!` and does not stop the
prunes after it. It does remove a label a listed feed carries besides its category: the file gives
each feed one, and a feed can report several. One `ac=edit` per stale label, named against the file's
category, leaves the feed with the one label the file gives it; a feed the file does not list is
never touched that way. A feed that has just been subscribed with a different category gets its stale
labels cleared on the next run, not the same one.
Credentials are `FRESHRSS_USER` and `FRESHRSS_API_PASSWORD` from
`~/.config/secrets/homelab.env`, plus `FRESHRSS_WEB_PASSWORD` for the settings half — which an
OIDC-signed-in instance does not have, so there the run covers subscriptions only.

`FRESHRSS_USER` is the username FreshRSS holds, which after the move to OIDC is the identity
provider's claim (`vyrtualsynthese`), not the local account name it replaced. A stale value answers
`401 Unauthorized!` on every command, and the API password is unchanged by the rename.

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
| KBDfans — new arrivals | native | `https://kbdfans.com/collections/new-arrival.atom` |

KBDfans' *restock* collection was in the set until 2026-11-19 and stays out: it re-lists products
coming back rather than new ones. The *new arrivals* collection is in, asked once every six hours
while the shop refuses the pod's fetches — see "KBDfans: one collection, the shop refuses the pod"
below.

An old Rise of Gunpla kit coming back in stock is **not** covered. FreshRSS drops an item it
already holds, so the same product cannot fire twice. That needs a change tracker, which is a
separate decision.

### Veille

| Feed | How | URL |
|---|---|---|
| selfh.st | native | `https://selfh.st/rss/` |

Four feeds left this category on 2026-11-19 by decision: Phoronix, Simon Willison, the Hacker News
front page and Lobsters. Their feeds were fine — they were not read often enough to hold a place,
so the list stays short rather than complete.

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
| Elite Dangerous | `//*[contains(@class,'__article-info')]//a[starts-with(@href,'/news/') and not(contains(@href,'/news/updates/'))]` | `normalize-space(.)` | `@href` |
| Anthropic / Claude | `//li[@data-listing-row]//a[contains(@class,'_title-link')]` | `normalize-space(.)` | `@href` |

Counts observed on 2026-11-19, from the live pages: Star Citizen 9, Final Fantasy XIV 9, Elite
Dangerous 12, Anthropic 24.

Both news pages print the same story several times, so neither can be read with "every link that
looks like an article":

- **Elite Dangerous** repeats a story up to six times — a top ticker, a featured block, a card, a
  picture-only link and a "read more". The picture-only links carry no text, so FreshRSS fell back to
  the item id and showed `urn:sha1:…` as the title; it logged 19 invalid GUIDs. Taking only the links
  inside the card's `__article-info` block gives one entry per story, with its title, and the same
  rule drops `/news/updates/`, the patch notes the blog-and-news-only rule excludes.
- **Anthropic** repeats each card as a title link, a summary link, a category link and a duplicate
  title inside the collapsed details, and one picture-only link had no text (87 invalid GUIDs in the
  log). It also files the product announcement posts at the site root — `/claude-haiku-5-5`,
  `/claude-sonnet-5-5` — so a rule over `/news/` links dropped them without saying so. Each story is
  one `<li data-listing-row>`, and its title link is the only `_title-link` inside it: 24 stories,
  all of them, once each.

`verify` reads Elite Dangerous' expression: a container class (`//*[contains(@class,'…')]//`) and
an exclusion (`and not(contains(@href,'…'))`) are the two extensions it understands on top of the
plain `contains`/`starts-with` test. Anthropic's expression tests an attribute on the `<li>` instead,
which it does not, so that line still ends `sel … unchecked` and the folder needs a look after a
redesign.

These are the brittle part of the set: a redesign empties them quietly, and `check` cannot see that —
the selector still matches the file. `verify` follows the first link the item expression picks and
reads that page through the full-content selector, so a dead selector shows as `sel … empty (path)`.
When the expression stops matching links, or is one this reader does not understand, the same check
prints `sel … unchecked` instead of failing, so the item list itself still needs a look in the web UI
after a redesign.

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

Eleven feeds carry a selector. The column is what `verify` printed on 2026-11-19, read from the
newest article of each feed; Elite Dangerous and Anthropic were the exceptions, read by hand the same
way, because `verify` could not evaluate their item expressions then (see "XPath selectors"). Since
2026-11-20 `verify` reads Elite Dangerous' expression and prints `sel main 2348 chars`; Anthropic's
line still says `sel article unchecked`.

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
| Final Fantasy XIV | `article` | 1435 |
| Elite Dangerous | `main` | 2348 |
| Anthropic / Claude | `article` | 1952 |

What it costs: one extra request per new article, made by the pod, so the source sees the cluster's
address rather than yours. What it breaks on: a redesign makes the selector match nothing, and the
symptom is in the log rather than the UI — FreshRSS writes `CSS content retrieval matched no
elements for feed …` once per article and stores the entry with an empty body. Elite Dangerous did
exactly that: 48 such lines in one refresh, because its article pages have no `#content` element.
`verify` is what catches it: it reads the newest article through the selector and exits 1 when the
extraction is empty, naming the page it read.

`verify` understands one tag with an id and/or classes (`article`, `#content`, `.entry-content`,
`div#main.box`) or a comma list of those. A selector built from a descendant combinator, an
attribute test or a pseudo-class is outside what it reads, so it reports `unsupported` instead of
passing silently — extend the reader before using one.

On 2026-11-19 every one of the eleven reads a body: the smallest is Rise of Gunpla at 461
characters (the newest kit is a short description), the largest Docker blog at 20013.

Seven feeds carry a picture in the item already, so the list shows a thumbnail without a selector:
Discord, Docker, Kubernetes, WSL2, Logseq, selfh.st and Guild Wars 2. Every one of those seven also
carries a selector. The four that carry a selector without a picture in the item are Rise of Gunpla,
Final Fantasy XIV, Elite Dangerous and Anthropic — there the selector is what brings the pictures,
since the feed itself carries none.

Three get nothing, and the reason is the source rather than a missing setting:

| Feed | Why not |
|---|---|
| Steam | the items are store announcements, already the whole text, and 2 images across 20 items |
| Star Citizen comm-link | the article is built by JavaScript, so the HTML the fetcher receives is empty |
| Mistral | served as `text/plain`, so the URL needs the force-feed marker; the publisher's summary is the whole article |

On the instance the eleven feeds carry these selectors, and its copy of them came from the
LinuxServer data root rather than from the file. `apply` used to be what wrote them — it put the
file's `frss:cssFullContent` into the feed's *Retrieve full content* field — but it cannot since the
app moved behind OIDC: that form sits behind a login the script no longer has, and an OPML import
skips a feed it already holds. A selector edited in the file now reaches the instance through a
delete and an import; see "Re-applying a setting after the OIDC switch" below.

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

Under OIDC the form login is not there to open: `auth_type` is `http_auth` and the app trusts the
identity its proxy hands it, so `?c=auth&a=formLogin` answers a redirect or a refusal rather than the
form. The script treats that as a normal state, prints `settings: not checked` with the reason, and
carries on with the subscriptions.

### Re-applying a setting after the OIDC switch

Four things live on the feed row and nowhere else: the XPath expressions of an HTML+XPath feed, the
CSS selector of the article body, the poll interval (`frss:ttl`), and the `#force_feed` marker a
wrong `Content-Type` needs. The GReader API sets none of them, and OPML import sets all of them **but
only for a feed it does not already hold** — `addFeedObject` returns as soon as the address matches.
So a setting that changes in the file reaches an existing feed by neither route.

The way through is to remove the row and let an import create it again:

```bash
python3 hack/freshrss_sync.py drop https://www.elitedangerous.com/news
kubectl -n news exec -i deploy/news-freshrss -- sh -c 'cat > /tmp/feeds.opml' < hack/freshrss/feeds.opml
kubectl -n news exec deploy/news-freshrss -- su www-data -s /bin/sh -c \
  'php /var/www/FreshRSS/cli/import-for-user.php --user vyrtualsynthese --filename /tmp/feeds.opml'
```

- `drop` unsubscribes every row whose address matches, fragment included, so it also clears a
  duplicate. What goes with the row is that feed's entries: the database is a cache of the file, and
  a re-created feed starts empty.
- The import must run as `www-data`. As root it leaves the SQLite file root-owned, and every later
  Apache request fails to write.
- The file has to be the current one. Importing a stale checkout is how the instance came to hold
  twenty feeds, a duplicate Mistral row and the old `#content` selector on 2026-11-19. A stale file
  also adds a second row for a feed whose address differs only by `#force_feed`, since the import
  matches on the address it is given; `check` reports the spare row as an extra and `apply --prune`
  removes it.

Three specimens from that day. Mistral is served as `text/plain`, so its address carries
`#force_feed` and a row without it answers "A feed could not be found". KBDfans answers HTTP 429 to
the instance at every interval, which `frss:ttl="21600"` (six hours) asks for without curing it —
see "What the pod's polls say". Elite Dangerous moved its article body out of `#content`; the file's
`main` reads it.

## Feeds the instance cannot fetch

Three feeds answered a blank HTTP 500 from `subscription/edit` while the same URLs return a valid
feed from a workstation. The 500 was not a refusal: it is what the API answers when its own logger
cannot write. Behind it are two different problems, one solved and one that keeps the KBDfans
collection from loading at all.

### What the empty 500 hid

The 500 carried no message because FreshRSS logs API errors to
`/config/data/users/_/log_api.txt`, and that directory was missing from the data root on 2026-11-19,
although the image carries one. The logger's
write fails, throws, and PHP dies mid-request before the API can answer anything but an empty 500.

Restore it (the data root is `DATA_PATH`, `/config/data`) and the API answers `400 Bad Request`
instead, with the reason in that file:

```bash
kubectl -n news exec deploy/news-freshrss -- mkdir -p /config/data/users/_
kubectl -n news exec deploy/news-freshrss -- chown www-data:www-data /config/data/users/_
```

Check for the directory after the data root is rebuilt, and run those two commands if it is missing.
The PHP fatal that stands in for the message is in the pod log: the official image's Apache sends its
error log to stderr, so PHP messages reach stdout and Loki with everything else.

### Mistral: served as plain text

SimplePie, the parser FreshRSS fetches with, checks the response's `content-type` before parsing and
rejects `text/plain` even when the body is valid RSS. Mistral serves its feed that way, which is why
the same URL parses on a workstation and delivered nothing here. FreshRSS's escape is the
`#force_feed` marker at the end of the URL: it tells the parser to read the body without the check,
and the marker travels in the OPML, so a replay reproduces it. The subscription is
`https://mistral.ai/news/rss#force_feed`, and it held 94 entries on 2026-11-19.

### KBDfans: one collection, the shop refuses the pod

Only the *new arrivals* collection is in the set; *restock* left it on 2026-11-19 because it re-lists
products that are back, which is not what "new products" means here.

The feed itself is fine: a valid Atom feed of 25 entries, and a workstation fetches it with `200`.
The instance's requests are the problem. Shopify answers `429` with the body `local_rate_limited`,
and the Cloudflare layer in front of the shop refuses a bare handshake outright.

So the one lever the instance has is to ask less often: `frss:ttl="21600"` in the file sets the
feed's *Do not automatically refresh more often than* field to six hours, and `apply` writes it.
Left at the default the feed is asked on every refresh cycle, minutes apart, and nearly all of those
are refused. The interval only starts to count once a poll has succeeded, so a feed that has never
loaded keeps retrying on every cron run until one does. After a refusal FreshRSS also stores a
25-minute per-domain backoff in `data/Retry-After/<domain>.txt` and logs
`For that domain, will first retry after …`; a retry inside that window never reaches the site.

Measured on 2026-11-19 from the workstation and the pod, alternating, one request each per round:

| Round | `openssl s_client`, raw HTTP/1.1 | `curl`, the client FreshRSS fetches with |
|---|---|---|
| 1 | `403` | `429` |
| 2 | `403` | `200` |
| 3 | `403` | `429` |
| 4 | `403` | `429` |
| 5 | `403` | `429` |

The bare handshake is refused `403` every time — the bot filter rejects it on sight, so it was never
the control it looked like. The `curl` column is the one that moves: one `200` in five, in the same
minutes. That reading was taken while one probe followed another, and it does not describe the
instance's own experience — see "What the pod's polls say" below.

### What the pod's polls say

Seven scheduled polls answered `429` between 23:31 and 02:31 local time on 2026-11-19/20, thirty
minutes apart, and not one answered `200`. The same refresh runs logged `200` or `304` for every
other feed in the file, Rise of Gunpla included — another Shopify shop — so the pod's egress and
Shopify's normal path both worked, and this collection's own filter refused it. The instance held
nothing for it: `python3 hack/freshrss_sync.py state` printed `never`.

A refusal every thirty minutes is not a rate limit, and the six-hour interval cannot help it: what
the shop refuses is the way the pod asks. Measured from the workstation during that same night,
minutes after the pod's 02:31 refusal:

| Request | Answer |
|---|---|
| `curl --http2`, the `FreshRSS/1.30.4` user agent | `200`, 25 entries, 1.2 MB |
| `curl --http1.1`, same user agent and `Accept` header | `429`, 18 bytes (`local_rate_limited`) |
| `curl --http1.1` again, after 90 seconds of quiet | `429` |
| `curl --http2` eight seconds after that | `200` |

Every one of those HTTP/1.1 requests was refused, and a single HTTP/2 request after a quiet gap was
admitted every time — four times over that night.

The protocol was the only property that changed in those pairs, and every HTTP/1.1 request was
refused while the HTTP/2 request seconds later was admitted. One more measurement keeps that honest:
earlier the same night, two HTTP/2 requests six seconds apart answered `200` and then `429`. So the
shop also refuses a burst, which is what the 2026-11-19 reading saw; what it does not explain is a
single request every thirty minutes being refused seven times running.

Two limits on the reading: every varying request came from the workstation, and the 2026-11-19 table
above recorded neither the protocol nor the order of its rounds, so it neither confirms nor refutes
this. Whether the pod's own fetcher speaks HTTP/1.1 has not been observed — the read-only service
account cannot exec into the pod.

So try the one lever that tests it: FreshRSS hands its system configuration's `curl_options` to every
feed fetch, and `CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_2TLS` in `data/config.php` (behind
*Administration* → *System configuration*) is that setting. It is on the data root, so it is not in
git. If the polls still answer `429`, this feed needs the route the design already keeps for restock
tracking — an in-cluster fetch that carries the `_shopify_essential` cookie the `200` sets, or
`changedetection.io` — and no poll interval will substitute for it.

Do not measure this feed by hand: `verify` fetches from wherever it runs and takes the slot the pod's
next poll wants, and `state` costs nothing.

## Categories

`Jeux`, `Dev`, `IA`, `Boutiques`, `Veille`. Nothing else; `Uncategorized` stays empty.
