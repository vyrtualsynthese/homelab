import contextlib
import importlib.util
import io
import json
import re
import urllib.parse
import pathlib
import sys
import unittest

try:
    import bcrypt
except ImportError:  # the login tests need it; the rest of the suite does not
    bcrypt = None

HACK = pathlib.Path(__file__).resolve().parents[1]
SYNC = HACK / "freshrss_sync.py"
OPML_PATH = HACK / "freshrss" / "feeds.opml"

spec = importlib.util.spec_from_file_location("freshrss_sync", SYNC)
mod = importlib.util.module_from_spec(spec)
sys.modules["freshrss_sync"] = mod
spec.loader.exec_module(mod)

RSS = ('<?xml version="1.0"?><rss><channel><title>t</title>'
       '<item><title>a</title><link>https://a.test/post</link></item></channel></rss>')
ARTICLE = ('<html><body><main><article><div id="content">'
           '<p class="entry-content">the body of the article</p>'
           '</div></article></main></body></html>')
DEAD = '<html><body><div class="unrelated">nothing selected here</div></body></html>'


def serve(page=ARTICLE, fail=()):
    """Answer each OPML URL with a document of its kind, and every article URL with `page`."""
    feeds = {f.url: f for f in mod.parse_opml(OPML_PATH.read_text())}

    def transport(method, url, headers=None, data=None):
        if url in fail:
            return 500, ""
        feed = feeds.get(url)
        if feed is None:
            return 200, page
        if feed.kind == "xpath":
            item = dict(feed.settings).get("xPathItem", "")
            match = re.search(r"@href,\s*'([^']*)'", item)
            if not match:
                return 200, page
            return 200, (f'<html><body><div class="__article-info">'
                         f'<a href="{match.group(1)}article-1">newest</a>'
                         '</div></body></html>')
        return 200, RSS
    return transport


class ParseTests(unittest.TestCase):
    def test_the_shipped_opml_yields_15_feeds_in_five_categories(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        self.assertEqual(len(feeds), 15)
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

    def test_the_xpath_feeds_carry_their_selectors_for_import(self):
        text = OPML_PATH.read_text()
        self.assertEqual(text.count("frss:xPathItem="), 4)
        self.assertIn('xmlns:frss="https://freshrss.org/opml"', text)

    def test_eleven_feeds_carry_a_full_content_selector(self):
        text = OPML_PATH.read_text()
        self.assertEqual(text.count("frss:cssFullContent="), 11)

    def test_a_full_content_selector_does_not_add_or_drop_a_feed(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        self.assertEqual(len(feeds), 15)

    def test_a_full_content_selector_reaches_the_feed_as_path_entries(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        docker = next(f for f in feeds if f.title == "Docker blog")
        self.assertEqual(dict(docker.settings), {"path_entries": ".entry-content"})

    def test_an_xpath_feed_carries_its_selectors_as_form_fields(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        anth = next(f for f in feeds if f.title == "Anthropic / Claude")
        self.assertEqual(dict(anth.settings), {
            "path_entries": "article",
            "xPathItem": "//li[@data-listing-row]//a[contains(@class,'_title-link')]",
            "xPathItemTitle": "normalize-space(.)",
            "xPathItemUri": "@href",
        })

    def test_a_feed_without_frss_attributes_carries_no_settings(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        steam = next(f for f in feeds if f.title == "Steam")
        self.assertEqual(steam.settings, ())

    def test_the_shipped_opml_pins_a_poll_interval_where_a_feed_asks_for_one(self):
        """Only KBDfans carries a ttl today: its host rate-limits by address, so it is polled rarely."""
        feeds = mod.parse_opml(OPML_PATH.read_text())
        kbdfans = next(f for f in feeds if f.title == "KBDfans — new arrivals")
        self.assertEqual(dict(kbdfans.settings), {"ttl": "21600"})

    def test_normalize_url_strips_one_trailing_slash_and_keeps_the_query(self):
        self.assertEqual(mod.normalize_url("https://a.test/feed/"), "https://a.test/feed")
        self.assertEqual(mod.normalize_url("https://a.test/f?x=1&y=2"), "https://a.test/f?x=1&y=2")

    def test_normalize_url_carries_no_fragment(self):
        """`#force_feed` is an instruction to the parser, not part of the address."""
        self.assertEqual(
            mod.normalize_url("https://mistral.ai/news/rss#force_feed"),
            "https://mistral.ai/news/rss")

    def test_two_opml_entries_differing_only_by_their_fragment_both_survive(self):
        """`#force_feed` changes what the parser does, so it is not a duplicate entry."""
        text = """<opml><body>
          <outline text="IA"><outline type="rss" text="A" xmlUrl="https://a.test/rss"/></outline>
          <outline text="IA"><outline type="rss" text="A force" xmlUrl="https://a.test/rss#force_feed"/></outline>
        </body></opml>"""
        self.assertEqual(len(mod.parse_opml(text)), 2)


class PlanTests(unittest.TestCase):
    def feed(self, url="https://a.test/feed", cat="Dev", title="A"):
        return mod.Feed(category=cat, title=title, url=url)

    def test_an_empty_instance_subscribes_everything_and_prunes_nothing(self):
        p = mod.plan([self.feed()], [])
        self.assertEqual(p.to_subscribe, (self.feed(),))
        self.assertEqual(p.to_prune, ())

    def test_a_matching_instance_is_unchanged(self):
        p = mod.plan([self.feed()], [("https://a.test/feed/", ("Dev",))])
        self.assertEqual(p.to_subscribe, ())
        self.assertEqual(p.unchanged, (self.feed(),))

    def test_a_moved_category_is_resubscribed(self):
        p = mod.plan([self.feed(cat="Jeux")], [("https://a.test/feed", ("Dev",))])
        self.assertEqual(p.to_subscribe, (self.feed(cat="Jeux"),))

    def test_a_feed_with_two_labels_is_not_drift_when_one_of_them_matches(self):
        """A re-subscribe adds a label; the file's label is still there, so it is in place."""
        p = mod.plan([self.feed(cat="Dev")], [("https://a.test/feed", ("Ancien", "Dev"))])
        self.assertEqual(p.to_subscribe, ())
        self.assertEqual(p.unchanged, (self.feed(cat="Dev"),))

    def test_a_feed_whose_every_label_is_foreign_is_resubscribed(self):
        p = mod.plan([self.feed(cat="Dev")], [("https://a.test/feed", ("Jeux", "Boutiques"))])
        self.assertEqual(p.to_subscribe, (self.feed(cat="Dev"),))

    def test_a_remote_feed_absent_from_the_file_is_reported_for_pruning(self):
        p = mod.plan([], [("https://b.test/feed", ("Dev",))])
        self.assertEqual(p.to_prune, (("https://b.test/feed", ("Dev",)),))

    def test_an_internal_feed_is_not_reported_as_drift(self):
        p = mod.plan([self.feed()], [("1", ("Uncategorized",)),
                                     ("https://a.test/feed", ("Dev",))])
        self.assertEqual(p.to_prune, ())

    def test_a_force_feed_subscription_matches_the_address_the_file_lists(self):
        """The instance appends `#force_feed`; it is still the feed the file names."""
        p = mod.plan([self.feed(url="https://a.test/rss")],
                     [("https://a.test/rss#force_feed", ("Dev",))])
        self.assertEqual(p.to_subscribe, ())
        self.assertEqual(p.to_prune, ())

    def test_a_second_row_for_the_same_address_is_reported_for_pruning(self):
        """The instance can hold one feed twice, once with the fragment and once without.

        The file names one address, so the spare row is an extra — and the row kept is the one that
        spells the file's address exactly, because that is the row carrying `#force_feed`.
        """
        p = mod.plan([self.feed(url="https://a.test/rss#force_feed")],
                     [("https://a.test/rss", ("Dev",)),
                      ("https://a.test/rss#force_feed", ("Dev",))])
        self.assertEqual(p.to_prune, (("https://a.test/rss", ("Dev",)),))
        self.assertEqual(p.unchanged, (self.feed(url="https://a.test/rss#force_feed"),))

    def test_a_duplicate_without_an_exact_match_keeps_the_first_row(self):
        p = mod.plan([self.feed(url="https://a.test/rss")],
                     [("https://a.test/rss", ("Dev",)),
                      ("https://a.test/rss#force_feed", ("Dev",))])
        self.assertEqual(p.to_prune, (("https://a.test/rss#force_feed", ("Dev",)),))
        self.assertEqual(p.unchanged, (self.feed(url="https://a.test/rss"),))

    def test_freshrss_own_release_feed_is_not_reported_as_an_extra(self):
        """The instance subscribes itself to its release feed; the file can never list it."""
        p = mod.plan([], [("https://github.com/FreshRSS/FreshRSS/releases.atom",
                           ("Uncategorized",))])
        self.assertEqual(p.to_prune, ())

    def test_a_feed_carrying_a_stale_label_asks_for_one_cleanup(self):
        p = mod.plan([self.feed(cat="Dev")], [("https://a.test/feed", ("Ancien", "Dev"))])
        self.assertEqual(p.to_clean, ((self.feed(cat="Dev"), "https://a.test/feed", ("Ancien",)),))

    def test_a_cleanup_names_the_address_the_instance_reported(self):
        """The instance may spell the address differently from the file; the edit goes to its own."""
        p = mod.plan([self.feed(url="https://a.test/rss")],
                     [("https://a.test/rss#force_feed", ("Dev", "Ancien"))])
        self.assertEqual(p.to_clean, ((self.feed(url="https://a.test/rss"),
                                       "https://a.test/rss#force_feed", ("Ancien",)),))

    def test_a_feed_carrying_two_stale_labels_asks_for_both(self):
        p = mod.plan([self.feed(cat="Dev")],
                     [("https://a.test/feed", ("Ancien", "Autre", "Dev"))])
        self.assertEqual(p.to_clean,
                         ((self.feed(cat="Dev"), "https://a.test/feed", ("Ancien", "Autre")),))

    def test_a_feed_carrying_only_its_label_asks_for_no_cleanup(self):
        p = mod.plan([self.feed()], [("https://a.test/feed", ("Dev",))])
        self.assertEqual(p.to_clean, ())

    def test_the_same_label_reported_twice_asks_for_no_cleanup(self):
        """The count says nothing when both labels are the file's; the labels themselves do."""
        p = mod.plan([self.feed()], [("https://a.test/feed", ("Dev", "Dev"))])
        self.assertEqual(p.to_clean, ())

    def test_a_feed_whose_label_is_missing_is_not_cleaned_up_first(self):
        """A feed to subscribe has no wanted label yet; moving it is the subscribe's job."""
        p = mod.plan([self.feed(cat="Jeux")], [("https://a.test/feed", ("Dev", "Ancien"))])
        self.assertEqual(p.to_clean, ())


class FakeTransport:
    def __init__(self, token="TOK", subs=None, login_body="SID=x\nLSID=y\nAuth=AUTH123\n",
                 edit_status=200, refuse=()):
        self.calls = []
        self.token, self.subs, self.login_body = token, list(subs or []), login_body
        self.edit_status = edit_status
        self.refuse = set(refuse)

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers or {}, data))
        if url.endswith("/accounts/ClientLogin"):
            return 200, self.login_body
        if url.endswith("/reader/api/0/token"):
            return 200, self.token
        if "/subscription/list" in url:
            subscriptions = []
            for i, (u, t, labels) in enumerate(self.subs):
                if isinstance(labels, str):
                    labels = (labels,)
                subscriptions.append({
                    "id": f"feed/{i + 1}", "url": u, "title": t,
                    "categories": [{"label": label} for label in labels]})
            return 200, json.dumps({"subscriptions": subscriptions})
        if "/subscription/edit" in url:
            form = urllib.parse.parse_qs((data or b"").decode())
            feed_url = form.get("s", [""])[0].removeprefix("feed/")
            label = form.get("a", ["user/-/label/Uncategorized"])[0].rsplit("/", 1)[-1]
            action = form.get("ac", [""])[0]
            if action == "subscribe" and any(s[0] == feed_url for s in self.subs):
                return 500, ""
            if self.edit_status != 200 or feed_url in self.refuse:
                return 500, ""
            action = form.get("ac", [""])[0]
            if action == "unsubscribe":
                self.subs = [s for s in self.subs if s[0] != feed_url]
            else:
                # The instance measured here replaces a feed's category on `a=` rather than adding
                # to it; `r=` is what names a stale label for the versions that add. The request
                # body is where a test sees both, so the fake models the measured replacement.
                self.subs = [s for s in self.subs if s[0] != feed_url]
                self.subs.append((feed_url, feed_url, (label,)))
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

    def test_subscriptions_returns_url_and_label_tuples(self):
        t = FakeTransport(subs=[("https://a.test/feed", "A", "Dev")])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        self.assertEqual(c.subscriptions(), [("https://a.test/feed", ("Dev",))])

    def test_subscriptions_keeps_every_label_a_feed_carries(self):
        """A re-subscribe adds a label and keeps the old one; the first is not the only one."""
        t = FakeTransport(subs=[("https://a.test/feed", "A", ("Ancien", "Dev"))])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        self.assertEqual(c.subscriptions(), [("https://a.test/feed", ("Ancien", "Dev"))])

    def test_a_null_label_reads_as_uncategorized(self):
        t = FakeTransport(subs=[("https://a.test/feed", "A", (None,))])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        self.assertEqual(c.subscriptions(), [("https://a.test/feed", ("Uncategorized",))])

    def test_a_refused_login_raises_auth_error(self):
        t = FakeTransport(login_body="Error=BadAuthentication\n")
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        with self.assertRaises(mod.AuthError):
            c.login()

    def test_subscriptions_read_the_url_field_not_the_numeric_id(self):
        """FreshRSS 1.30 answers id="feed/5" and puts the real address in url."""
        t = FakeTransport(subs=[("https://a.test/feed", "A", "Dev")])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        self.assertEqual(c.subscriptions(), [("https://a.test/feed", ("Dev",))])

    def test_subscribing_a_feed_the_instance_already_holds_is_not_an_error(self):
        """The instance answers 500 for a duplicate; the feed is present, so the call succeeded."""
        t = FakeTransport(subs=[("https://a.test/feed", "A", "Dev")], edit_status=500)
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        c.subscribe(mod.Feed(category="Dev", title="A", url="https://a.test/feed"))

    def test_subscribing_a_feed_that_stays_absent_still_raises(self):
        t = FakeTransport(subs=[], edit_status=500)
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        with self.assertRaises(RuntimeError):
            c.subscribe(mod.Feed(category="Dev", title="B", url="https://b.test/feed"))

    def test_moving_a_feed_between_categories_uses_edit_not_a_second_subscribe(self):
        """A duplicate subscribe answers 500 and changes nothing; ac=edit is what moves a label."""
        t = FakeTransport(subs=[("https://a.test/feed", "A", "Dev")])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        c.subscribe(mod.Feed(category="Jeux", title="A", url="https://a.test/feed"))
        edit = [call for call in t.calls if "/subscription/edit" in call[1]][-1]
        self.assertIn("ac=edit", edit[3].decode())
        self.assertEqual(t.subs[0][2], ("Jeux",))

    def test_a_feed_already_held_under_a_second_label_is_not_edited_again(self):
        """The 500 from a duplicate subscribe must not turn into a redundant ac=edit."""
        t = FakeTransport(subs=[("https://a.test/feed", "A", ("Ancien", "Dev"))])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        c.subscribe(mod.Feed(category="Dev", title="A", url="https://a.test/feed"))
        self.assertFalse([call for call in t.calls if b"ac=edit" in (call[3] or b"")])

    def test_relabel_drops_the_stale_label_and_keeps_the_wanted_one_in_one_call(self):
        """The address posted is the one the instance reported, which may not be the file's."""
        t = FakeTransport(subs=[("https://a.test/feed", "A", ("Ancien", "Dev"))])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        c.relabel("https://a.test/feed#force_feed", "Dev", "Ancien")
        edits = [call for call in t.calls if b"ac=edit" in (call[3] or b"")]
        self.assertEqual(len(edits), 1)
        body = edits[0][3].decode()
        self.assertIn("s=feed%2Fhttps%3A%2F%2Fa.test%2Ffeed%23force_feed", body)
        self.assertIn("T=TOK", body)
        self.assertIn("a=user%2F-%2Flabel%2FDev", body)
        self.assertIn("r=user%2F-%2Flabel%2FAncien", body)


class SelectorTests(unittest.TestCase):
    def test_an_id_selector_picks_the_text_inside_that_element(self):
        self.assertEqual(mod.select_text('<div id="content"><p>hello</p></div>', "#content"), 5)

    def test_a_class_selector_matches_one_of_several_classes(self):
        self.assertEqual(mod.select_text('<div class="a b">hi</div>', ".b"), 2)

    def test_a_comma_list_takes_the_first_element_in_document_order(self):
        self.assertEqual(
            mod.select_text("<main>aa</main><article>bbb</article>", "article, main"), 2)

    def test_a_missing_element_yields_zero(self):
        self.assertEqual(mod.select_text("<div>hi</div>", "#content"), 0)

    def test_a_script_body_is_not_article_text(self):
        self.assertEqual(
            mod.select_text('<div id="content"><script>var x = 1;</script></div>', "#content"), 0)

    def test_the_first_match_is_the_first_in_document_order_not_the_shallowest(self):
        page = ('<div><section><span id="content">deep</span></section></div>'
                '<div id="content">later but shallower</div>')
        self.assertEqual(mod.select_text(page, "#content"), 4)

    def test_a_descendant_selector_is_reported_as_unsupported(self):
        with self.assertRaises(ValueError):
            mod.select_text("<main><article>hi</article></main>", "main article")


class CliTests(unittest.TestCase):
    ENV = {"FRESHRSS_USER": "u", "FRESHRSS_API_PASSWORD": "p"}

    def test_check_exits_one_when_the_instance_is_missing_a_feed(self):
        t = FakeTransport(subs=[])
        code = mod.main(["check"], transport=t, env=self.ENV)
        self.assertEqual(code, 1)

    def test_check_exits_zero_when_every_feed_matches(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        code = mod.main(["check"], transport=t, env=self.ENV)
        self.assertEqual(code, 0)

    def test_apply_without_prune_never_unsubscribes(self):
        t = FakeTransport(subs=[("https://hand-added.test/feed", "Hand", "Dev")])
        mod.main(["apply"], transport=t, env=self.ENV)
        self.assertFalse([c for c in t.calls if b"ac=unsubscribe" in (c[3] or b"")])

    def test_apply_with_prune_unsubscribes_the_extra_feed(self):
        t = FakeTransport(subs=[("https://hand-added.test/feed", "Hand", "Dev")])
        mod.main(["apply", "--prune"], transport=t, env=self.ENV)
        self.assertTrue([c for c in t.calls if b"ac=unsubscribe" in (c[3] or b"")])

    def test_apply_reports_a_refused_prune_and_still_prunes_the_rest(self):
        t = FakeTransport(subs=[("https://bad.test/feed", "Bad", "Dev"),
                                ("https://good.test/feed", "Good", "Dev")],
                          refuse=("https://bad.test/feed",))
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code = mod.main(["apply", "--prune"], transport=t, env=self.ENV)
        self.assertEqual(code, 1)
        self.assertIn("bad.test", errors.getvalue())
        self.assertNotIn("https://good.test/feed", [s[0] for s in t.subs])

    def test_drop_unsubscribes_every_row_the_address_answers_to(self):
        """A per-feed setting applies at creation, so the row has to go before an import."""
        t = FakeTransport(subs=[("https://a.test/rss", "A", "Dev"),
                                ("https://a.test/rss#force_feed", "A again", "Dev"),
                                ("https://b.test/feed", "B", "Dev")])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = mod.main(["drop", "https://a.test/rss"], transport=t, env=self.ENV)
        self.assertEqual(code, 0)
        self.assertEqual([s[0] for s in t.subs], ["https://b.test/feed"])
        self.assertIn("dropped 2", out.getvalue())

    def test_drop_without_an_address_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = mod.main(["drop"], transport=FakeTransport(), env=self.ENV)
        self.assertEqual(code, 2)

    def test_apply_cleans_the_stale_label_a_moved_feed_keeps(self):
        """A feed can report a label besides its category; apply takes it back off."""
        feeds = mod.parse_opml(OPML_PATH.read_text())
        first = feeds[0]
        stale = "Ancienne" if first.category != "Ancienne" else "Autre"
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds[1:]]
                          + [(first.url, first.title, (first.category, stale))])
        mod.main(["apply"], transport=t, env=self.ENV)
        edits = [c for c in t.calls if b"ac=edit" in (c[3] or b"")]
        self.assertEqual(len(edits), 1)
        self.assertIn(f"r=user%2F-%2Flabel%2F{stale}".encode(), edits[0][3])
        self.assertIn(f"a=user%2F-%2Flabel%2F{first.category}".encode(), edits[0][3])
        held = next(s for s in t.subs if s[0] == first.url)
        self.assertEqual(held[2], (first.category,))

    def test_apply_takes_off_every_stale_label_a_feed_carries(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        first = feeds[0]
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds[1:]]
                          + [(first.url, first.title, ("Ancienne", "Autre", first.category))])
        mod.main(["apply"], transport=t, env=self.ENV)
        edits = [c for c in t.calls if b"ac=edit" in (c[3] or b"")]
        self.assertEqual(len(edits), 2)
        for stale, edit in zip(("Ancienne", "Autre"), edits):
            self.assertIn(f"a=user%2F-%2Flabel%2F{first.category}".encode(), edit[3])
            self.assertIn(f"r=user%2F-%2Flabel%2F{stale}".encode(), edit[3])

    def test_apply_reports_a_refused_cleanup_and_still_cleans_the_rest(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        first, second = feeds[0], feeds[1]
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds[2:]]
                          + [(first.url, first.title, (first.category, "Ancienne")),
                             (second.url, second.title, (second.category, "Autre"))],
                          refuse=(first.url,))
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code = mod.main(["apply"], transport=t, env=self.ENV)
        self.assertEqual(code, 1)
        self.assertIn(first.title, errors.getvalue())
        held = next(s for s in t.subs if s[0] == second.url)
        self.assertEqual(held[2], (second.category,))

    def test_apply_leaves_a_hand_added_feed_s_own_labels_alone(self):
        """Only feeds the file lists are relabelled; this one is not in the file."""
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds]
                          + [("https://hand-added.test/feed", "Hand", ("Dev", "Perso"))])
        mod.main(["apply"], transport=t, env=self.ENV)
        self.assertFalse([c for c in t.calls if b"ac=edit" in (c[3] or b"")])
        held = next(s for s in t.subs if s[0] == "https://hand-added.test/feed")
        self.assertEqual(held[2], ("Dev", "Perso"))

    def test_a_dry_run_prints_a_stale_label_without_removing_it(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        first = feeds[0]
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds[1:]]
                          + [(first.url, first.title, (first.category, "Ancienne"))])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mod.main(["apply", "--dry-run"], transport=t, env=self.ENV)
        self.assertIn("Ancienne would be removed", out.getvalue())
        self.assertFalse([c for c in t.calls if b"ac=edit" in (c[3] or b"")])

    def test_missing_credentials_exit_two(self):
        self.assertEqual(mod.main(["check"], transport=FakeTransport(), env={}), 2)

    def test_check_exits_zero_when_the_instance_only_has_extra_feeds(self):
        """A feed added by hand is not drift: apply never removes it, so check must not fail on it."""
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds]
                          + [("https://hand-added.test/feed", "Hand", "Dev")])
        self.assertEqual(mod.main(["check"], transport=t, env=self.ENV), 0)

    def test_apply_skips_xpath_feeds_because_the_api_cannot_create_them(self):
        t = FakeTransport(subs=[])
        mod.main(["apply"], transport=t, env=self.ENV)
        bodies = b"".join(c[3] or b"" for c in t.calls if b"ac=subscribe" in (c[3] or b""))
        self.assertNotIn(b"anthropic.com", bodies)
        self.assertNotIn(b"comm-link", bodies)

    def test_apply_twice_sends_no_second_subscribe(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        mod.main(["apply"], transport=t, env=self.ENV)
        before = len(t.calls)
        mod.main(["apply"], transport=t, env=self.ENV)
        self.assertFalse([c for c in t.calls[before:] if b"ac=subscribe" in (c[3] or b"")])

    def test_verify_reports_a_rate_limited_feed_without_raising(self):
        def transport(method, url, headers=None, data=None):
            return 429, ""
        rows = mod.verify([mod.Feed(category="Boutiques", title="KBDfans",
                                    url="https://kbdfans.test/x.atom")], transport)
        self.assertEqual(rows, [("HTTP429", "KBDfans", 0, "", "", True)])

    def test_apply_reports_a_refused_feed_and_still_subscribes_the_rest(self):
        t = FakeTransport(subs=[], refuse=("https://mistral.ai/news/rss#force_feed",))
        code = mod.main(["apply"], transport=t, env=self.ENV)
        self.assertEqual(code, 1)
        bodies = b"".join(c[3] or b"" for c in t.calls if b"ac=subscribe" in (c[3] or b""))
        self.assertIn(b"selfh.st", bodies)

    def test_verify_reads_the_items_of_an_rss_1_0_feed(self):
        """Steam serves RSS 1.0, whose items sit in a namespace rather than bare."""
        rdf = ('<?xml version="1.0"?>'
               '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
               ' xmlns="http://purl.org/rss/1.0/">'
               '<channel><title>t</title></channel>'
               '<item><title>a</title><link>https://a.test/1</link></item>'
               '<item><title>b</title><link>https://a.test/2</link></item></rdf:RDF>')

        def transport(method, url, headers=None, data=None):
            return 200, rdf
        rows = mod.verify([mod.Feed(category="Jeux", title="Steam",
                                    url="https://a.test/rdf")], transport)
        self.assertEqual(rows, [("OK", "Steam", 2, "", "", False)])

    def test_verify_reports_the_text_a_selector_extracts_from_the_newest_article(self):
        feed = mod.Feed(category="Dev", title="Docker", url="https://a.test/feed",
                        settings=(("path_entries", ".entry-content"),))
        fetched = []

        def transport(method, url, headers=None, data=None):
            fetched.append(url)
            return (200, RSS) if url == feed.url else (200, ARTICLE)
        rows = mod.verify([feed], transport)
        self.assertEqual(fetched, [feed.url, "https://a.test/post"])
        self.assertEqual(rows, [("OK", "Docker", 1, "", "sel .entry-content 23 chars", False)])

    def test_verify_reports_a_dead_selector_as_an_empty_extraction(self):
        feed = mod.Feed(category="Dev", title="Docker", url="https://a.test/feed",
                        settings=(("path_entries", ".entry-content"),))

        def transport(method, url, headers=None, data=None):
            return (200, RSS) if url == feed.url else (200, DEAD)
        rows = mod.verify([feed], transport)
        self.assertEqual(rows[0].note, "sel .entry-content empty (/post)")
        self.assertTrue(rows[0].bad)

    def test_verify_reports_a_selector_it_cannot_evaluate(self):
        feed = mod.Feed(category="Dev", title="Docker", url="https://a.test/feed",
                        settings=(("path_entries", "main .entry-content"),))

        def transport(method, url, headers=None, data=None):
            return (200, RSS) if url == feed.url else (200, ARTICLE)
        rows = mod.verify([feed], transport)
        self.assertEqual(rows[0].note, "sel main .entry-content unsupported")
        self.assertTrue(rows[0].bad)

    def test_verify_reads_an_xpath_feed_selector_on_the_article_its_listing_links_to(self):
        feed = mod.Feed(category="Jeux", title="Final Fantasy XIV", url="https://a.test/news/",
                        kind="xpath", settings=(
                            ("path_entries", "article"),
                            ("xPathItem", "//a[contains(@href,'/news/detail/')]"),
                            ("xPathItemUri", "@href")))
        listing = ('<html><body><a href="/news/other">skip</a>'
                   '<a href="/news/detail/42">newest</a></body></html>')
        fetched = []

        def transport(method, url, headers=None, data=None):
            fetched.append(url)
            return (200, listing) if url == feed.url else (200, ARTICLE)
        rows = mod.verify([feed], transport)
        self.assertEqual(fetched, [feed.url, "https://a.test/news/detail/42"])
        self.assertEqual(rows, [("OK", "Final Fantasy XIV", 0, "",
                                 "sel article 23 chars", False)])

    def test_the_linked_article_is_the_first_link_in_document_order(self):
        feed = mod.Feed(category="Jeux", title="Elite Dangerous", url="https://a.test/news",
                        kind="xpath", settings=(
                            ("xPathItem", "//a[starts-with(@href,'/news/')]"),
                            ("xPathItemUri", "@href")))
        listing = ('<div><ul><li><a href="/news/newest">a</a></li></ul></div>'
                   '<a href="/news/older">b</a>')
        self.assertEqual(mod._linked_article(listing, feed), "https://a.test/news/newest")

    def test_the_linked_article_honours_a_container_and_an_exclusion(self):
        """The Elite Dangerous shape: only links inside the card block, and never the patch notes."""
        feed = mod.Feed(category="Jeux", title="Elite Dangerous",
                        url="https://www.elitedangerous.com/news", kind="xpath", settings=(
                            ("xPathItem",
                             "//*[contains(@class,'__article-info')]"
                             "//a[starts-with(@href,'/news/')"
                             " and not(contains(@href,'/news/updates/'))]"),
                            ("xPathItemUri", "@href")))
        listing = ('<a href="/news/ticker">ticker</a>'
                   '<div class="news-card__article-info">'
                   '<a href="/news/updates/patch">patch note</a>'
                   '<a href="/news/thargoids">Thargoids</a>'
                   '</div>'
                   '<a href="/news/read-more">read more</a>')
        self.assertEqual(mod._linked_article(listing, feed),
                         "https://www.elitedangerous.com/news/thargoids")

    def test_verify_reads_the_shipped_elite_dangerous_expression(self):
        feed = next(f for f in mod.parse_opml(OPML_PATH.read_text())
                    if f.title == "Elite Dangerous")
        listing = ('<div class="__article-info"><a href="/news/thargoids">Thargoids</a></div>')

        def transport(method, url, headers=None, data=None):
            return (200, listing) if url == feed.url else (200, ARTICLE)
        rows = mod.verify([feed], transport)
        self.assertEqual(rows, [("OK", "Elite Dangerous", 0, "",
                                 "sel main 23 chars", False)])

    def test_an_item_expression_richer_than_container_and_exclusion_is_still_refused(self):
        """Anthropic tests an attribute on the container, which this reader does not do."""
        feed = mod.Feed(category="IA", title="Anthropic / Claude",
                        url="https://www.anthropic.com/news", kind="xpath", settings=(
                            ("xPathItem",
                             "//li[@data-listing-row]//a[contains(@class,'_title-link')]"),
                            ("xPathItemUri", "@href")))
        listing = '<li data-listing-row><a class="_title-link" href="/news/real">t</a></li>'
        self.assertEqual(mod._linked_article(listing, feed), "")

    def test_verify_says_an_xpath_selector_was_not_checked_when_no_item_links(self):
        feed = mod.Feed(category="Jeux", title="Elite Dangerous", url="https://a.test/news",
                        kind="xpath", settings=(
                            ("path_entries", "#content"),
                            ("xPathItem", "//a[starts-with(@href,'/news/')]"),
                            ("xPathItemUri", "@href")))

        def transport(method, url, headers=None, data=None):
            return 200, DEAD
        rows = mod.verify([feed], transport)
        self.assertEqual(rows, [("OK", "Elite Dangerous", 0, "", "sel #content unchecked", False)])

    def test_verify_checks_the_article_with_the_newest_date_not_the_first_in_the_file(self):
        """A feed that publishes oldest-first would otherwise have an old article checked."""
        rss = ('<?xml version="1.0"?><rss><channel><title>t</title>'
               '<item><title>old</title><link>https://a.test/old</link>'
               '<pubDate>Mon, 02 Jan 2023 10:00:00 +0000</pubDate></item>'
               '<item><title>new</title><link>https://a.test/new</link>'
               '<pubDate>Wed, 18 Nov 2026 10:00:00 +0000</pubDate></item>'
               '</channel></rss>')
        feed = mod.Feed(category="Dev", title="Docker", url="https://a.test/feed",
                        settings=(("path_entries", "article"),))
        fetched = []

        def transport(method, url, headers=None, data=None):
            fetched.append(url)
            return (200, rss) if url == feed.url else (200, ARTICLE)
        rows = mod.verify([feed], transport)
        self.assertEqual(fetched, [feed.url, "https://a.test/new"])
        self.assertEqual(rows[0].items, 2)
        self.assertEqual(rows[0].last, "2026-11-18")

    def test_verify_reads_an_atom_feed_and_follows_its_entry_link(self):
        atom = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
                '<title>t</title><entry><title>a</title>'
                '<link rel="alternate" href="https://a.test/post"/>'
                '<updated>2026-11-18T10:00:00Z</updated></entry></feed>')
        feed = mod.Feed(category="Veille", title="Simon Willison",
                        url="https://a.test/atom", settings=(("path_entries", "article"),))
        fetched = []

        def transport(method, url, headers=None, data=None):
            fetched.append(url)
            return (200, atom) if url == feed.url else (200, ARTICLE)
        rows = mod.verify([feed], transport)
        self.assertEqual(fetched, [feed.url, "https://a.test/post"])
        self.assertEqual(rows, [("OK", "Simon Willison", 1, "2026-11-18",
                                 "sel article 23 chars", False)])

    def test_verify_fails_when_an_xpath_listing_does_not_answer(self):
        """Without the listing the feed produces nothing, so a dead one is a failure."""
        feed = mod.Feed(category="IA", title="Anthropic / Claude", url="https://a.test/news",
                        kind="xpath", settings=(
                            ("path_entries", "article"),
                            ("xPathItem", "//a[starts-with(@href,'/news/')]"),
                            ("xPathItemUri", "@href")))

        def transport(method, url, headers=None, data=None):
            return 403, ""
        rows = mod.verify([feed], transport)
        self.assertEqual(rows, [("HTTP403", "Anthropic / Claude", 0, "",
                                 "sel article unchecked", True)])

    def test_verify_exits_one_when_a_feed_answers_badly(self):
        transport = serve(fail=("https://www.guildwars2.com/en/feed/",))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(["verify"], transport=transport, env={}), 1)

    def test_verify_exits_one_when_a_full_content_selector_is_dead(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(["verify"], transport=serve(page=DEAD), env={}), 1)

    def test_verify_exits_zero_when_every_fetch_answers(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(["verify"], transport=serve(), env={}), 0)


def settings_page(feed_id, fields=None, action=None):
    """One feed's settings page, shaped like the one FreshRSS 1.30 renders."""
    fields = fields or {}
    action = action or f"/i/?c=subscription&amp;a=feed&amp;id={feed_id}"

    def text(name):
        return fields.get(name, "")

    def select(name, options, default):
        chosen = str(fields.get(name, default))
        return "".join(
            '<option value="%s"%s>%s</option>'
            % (value, ' selected="selected"' if value == chosen else "", value)
            for value in options)

    return f"""<html><body>
<form method="post" action="/i/?c=feed&amp;a=clearCache&amp;id={feed_id}">
  <input type="hidden" name="_csrf" value="CSRF" />
  <input type="hidden" name="id" value="{feed_id}" />
</form>
<form method="post" action="{action}" enctype="multipart/form-data">
  <input type="hidden" name="_csrf" value="CSRF" />
  <input type="hidden" name="id" value="{feed_id}" />
  <input type="hidden" name="from" value="subscription" />
  <select name="feed_kind">{select("feed_kind", ("0", "10"), "0")}</select>
  <select name="ttl">{select("ttl", ("0", "1800", "21600"), "1800")}</select>
  <input type="checkbox" name="mute" value="1" checked="checked" />
  <input type="checkbox" name="clear_cache" value="1" />
  <textarea name="path_entries_conditions">{text("path_entries_conditions")}</textarea>
  <input type="text" name="path_entries" value="{text("path_entries")}" />
  <input type="text" name="xPathItem" value="{text("xPathItem")}" />
  <input type="text" name="xPathItemTitle" value="{text("xPathItemTitle")}" />
  <input type="text" name="xPathItemUri" value="{text("xPathItemUri")}" />
</form>
</body></html>"""


class FormTests(unittest.TestCase):
    def test_the_settings_form_is_the_one_carrying_path_entries(self):
        form = mod.settings_form(settings_page(5, {"path_entries": ".entry-content"}))
        self.assertIn("id=5", form.action)
        self.assertEqual(form.value("path_entries"), ".entry-content")

    def test_a_checked_box_is_sent_and_an_unchecked_one_is_not(self):
        form = mod.settings_form(settings_page(5))
        self.assertEqual(form.value("mute"), "1")
        self.assertIsNone(form.value("clear_cache"))

    def test_a_select_sends_its_selected_option(self):
        form = mod.settings_form(settings_page(5))
        self.assertEqual(form.value("ttl"), "1800")

    def test_a_textarea_sends_its_text(self):
        form = mod.settings_form(settings_page(5, {"path_entries_conditions": "a\nb"}))
        self.assertEqual(form.value("path_entries_conditions"), "a\nb")

    def test_a_page_without_a_settings_form_is_an_error(self):
        with self.assertRaises(RuntimeError):
            mod.settings_form("<html><body>nothing</body></html>")

    def test_the_form_whose_action_is_the_feed_route_wins(self):
        page = """<html><body>
        <form action="/i/?c=feed&amp;a=feed&amp;id=5">
          <input type="text" name="path_entries" value="article" />
        </form>
        <form action="/i/?c=subscription&amp;a=feed&amp;id=5">
          <input type="text" name="path_entries" value="#content" />
        </form>
        </body></html>"""
        self.assertEqual(mod.settings_form(page).value("path_entries"), "#content")

    def test_an_option_without_a_value_sends_its_text(self):
        page = """<html><body>
        <form action="/i/?c=subscription&amp;a=feed&amp;id=5">
          <input type="text" name="path_entries" value="article" />
          <select name="ttl"><option>0</option><option selected="selected">1800</option></select>
        </form></body></html>"""
        self.assertEqual(mod.settings_form(page).value("ttl"), "1800")


class DriftTests(unittest.TestCase):
    def test_a_differing_selector_is_drift(self):
        form = mod.settings_form(settings_page(5, {"path_entries": "#content"}))
        self.assertEqual(mod.settings_drift(form, {"path_entries": ".entry-content"}),
                         {"path_entries": ("#content", ".entry-content")})

    def test_a_matching_selector_is_not_drift(self):
        form = mod.settings_form(settings_page(5, {"path_entries": ".entry-content"}))
        self.assertEqual(mod.settings_drift(form, {"path_entries": ".entry-content"}), {})

    def test_a_differing_poll_interval_is_drift(self):
        form = mod.settings_form(settings_page(5, {"ttl": "1800"}))
        self.assertEqual(mod.settings_drift(form, {"ttl": "21600"}), {"ttl": ("1800", "21600")})


class TransportTests(unittest.TestCase):
    def test_the_cookie_keeping_transport_can_be_built(self):
        self.assertTrue(callable(mod.cookied_transport()))


@unittest.skipIf(bcrypt is None, "the login needs the bcrypt package")
class WebWriteTests(unittest.TestCase):
    def test_a_relative_form_action_is_posted_under_the_app_root(self):
        """The settings page posts to ./?c=subscription&a=feed&id=N; the app root is not the site root."""
        web = FakeWeb(relative_actions=True)
        client = mod.WebClient("https://x.test", "u", "pw", transport=web)
        client.login()
        client.set_settings(5, {"path_entries": "#content"})
        self.assertEqual(web.posts()[-1][1],
                         "https://x.test/i/?c=subscription&a=feed&id=5")


class FakeWeb:
    """The FreshRSS web UI: the nonce endpoint, the login form, and the feed pages."""

    def __init__(self, password="pw", fields=None, login_status=200, store=True,
                 relative_actions=False, salt1=None, after_post=None, page_status=200):
        self.calls = []
        self.logged_in = False
        self.nonce = "N0NCE123"
        self.login_status = login_status
        self.page_status = page_status
        self.store = store
        self.relative_actions = relative_actions
        self.salt1 = salt1
        self.after_post = after_post or {}
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
        self.fields = {k: dict(v) for k, v in (fields or {}).items()}

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers or {}, data))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if query.get("a") == ["nonce"]:
            salt1 = self.password_hash[:29] if self.salt1 is None else self.salt1
            return 200, json.dumps({"salt1": salt1, "nonce": self.nonce}), {}
        if query.get("a") == ["formLogin"]:
            if method == "GET":
                # A gate in front of the app (OIDC) answers instead of the form.
                return self.page_status, self.login_page() if self.page_status == 200 else "", {}
            sent = dict(urllib.parse.parse_qsl((data or b"").decode()))
            if sent.get("_csrf") != "CSRF-LOGIN":
                return 403, "<html>[CSRF] Login</html>", {}
            self.logged_in = self._challenge_ok(sent)
            if self.logged_in:
                return 200, "<html>ok</html>", {}
            if self.login_status != 200:
                return self.login_status, "<html>invalid login</html>", {}
            return 200, self.login_page(), {}
        if not self.logged_in:
            return 200, self.login_page(), {}
        feed_id = (query.get("id") or [""])[0]
        if query.get("a") == ["feed"]:
            if method == "POST" and self.store:
                sent = dict(urllib.parse.parse_qsl((data or b"").decode()))
                self.fields[feed_id] = {**self.fields.get(feed_id, {}), **sent,
                                        **self.after_post}
            action = (f"./?c=subscription&a=feed&id={feed_id}" if self.relative_actions
                      else None)
            return 200, settings_page(feed_id, self.fields.get(feed_id, {}), action), {}
        return 404, "", {}

    @staticmethod
    def login_page():
        return ('<html><form method="post" action="/i/?c=auth&amp;a=formLogin">'
                '<input type="hidden" name="_csrf" value="CSRF-LOGIN" />'
                '<input type="text" name="username" /></form></html>')

    def _challenge_ok(self, sent):
        try:
            return (sent.get("username") == "u" and
                    bcrypt.checkpw((self.password_hash + self.nonce).encode(),
                                   sent.get("challenge", "").encode()))
        except ValueError:
            return False

    def posts(self):
        return [c for c in self.calls if c[0] == "POST" and "a=feed" in c[1]]


@unittest.skipIf(bcrypt is None, "the login needs the bcrypt package")
class WebLoginTests(unittest.TestCase):
    def test_login_asks_for_the_nonce_under_the_app_root(self):
        web = FakeWeb()
        mod.WebClient("https://x.test", "u", "pw", transport=web).login()
        self.assertIn("/i/?c=javascript&a=nonce", [c[1] for c in web.calls][1])

    def test_login_reads_the_csrf_token_from_the_login_page_and_sends_it(self):
        """Every POST is refused with [CSRF] without the session's token, the login included."""
        web = FakeWeb()
        mod.WebClient("https://x.test", "u", "pw", transport=web).login()
        self.assertIn("/i/?c=auth&a=formLogin", web.calls[0][1])
        posted = dict(urllib.parse.parse_qsl(web.calls[-1][3].decode()))
        self.assertEqual(posted["_csrf"], "CSRF-LOGIN")

    def test_login_solves_the_nonce_challenge(self):
        web = FakeWeb()
        mod.WebClient("https://x.test", "u", "pw", transport=web).login()
        self.assertTrue(web.logged_in)

    def test_a_wrong_password_raises_auth_error(self):
        web = FakeWeb()
        with self.assertRaises(mod.AuthError):
            mod.WebClient("https://x.test", "u", "wrong", transport=web).login()

    def test_a_login_answer_that_is_not_a_page_raises_auth_error(self):
        """The instance answers 403 with an error page when the challenge does not match."""
        web = FakeWeb(login_status=403)
        with self.assertRaises(mod.AuthError):
            mod.WebClient("https://x.test", "u", "wrong", transport=web).login()

    def test_a_salt_that_is_not_bcrypt_raises_auth_error_not_a_traceback(self):
        """An instance storing argon2, or answering an empty salt, must read as a credential problem."""
        for salt in ("$argon2id$v=19$m=65536", ""):
            with self.subTest(salt=salt):
                with self.assertRaises(mod.AuthError):
                    mod.WebClient("https://x.test", "u", "pw",
                                  transport=FakeWeb(salt1=salt)).login()


@unittest.skipIf(bcrypt is None, "the login needs the bcrypt package")
class SettingsCliTests(unittest.TestCase):
    ENV = {"FRESHRSS_USER": "u", "FRESHRSS_API_PASSWORD": "p", "FRESHRSS_WEB_PASSWORD": "pw"}

    def matching_web(self, feeds):
        fields = {}
        for i, feed in enumerate(feeds, start=1):
            settings = dict(feed.settings)
            settings["feed_kind"] = "10" if feed.kind == "xpath" else "0"
            fields[str(i)] = settings
        return FakeWeb(fields=fields)

    def test_check_reports_a_differing_selector_and_exits_one(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = mod.main(["check"], transport=t, env=self.ENV,
                            web_transport=FakeWeb(fields={}))
        self.assertEqual(code, 1)
        self.assertIn("Docker blog", out.getvalue())

    def test_check_exits_zero_when_every_selector_matches(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        with contextlib.redirect_stdout(io.StringIO()):
            code = mod.main(["check"], transport=t, env=self.ENV,
                            web_transport=self.matching_web(feeds))
        self.assertEqual(code, 0)

    def test_check_says_how_many_feeds_it_compared(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        with_settings = sum(1 for f in feeds if f.settings)
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mod.main(["check"], transport=t, env=self.ENV,
                     web_transport=self.matching_web(feeds))
        self.assertIn(f"settings: {with_settings} of {with_settings} feeds match the file",
                      out.getvalue())

    def test_check_says_when_a_feed_with_settings_is_not_on_the_instance(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        docker = next(f for f in feeds if f.title == "Docker blog")
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds if f is not docker])
        web = FakeWeb(fields={str(i + 1): dict(f.settings) for i, f in enumerate(feeds)})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mod.main(["check"], transport=t, env=self.ENV, web_transport=web)
        self.assertIn("1 not on the instance", out.getvalue())

    def test_apply_reports_a_field_the_submit_changed_behind_its_back(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        web = FakeWeb(fields={}, after_post={"ttl": "0"})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = mod.main(["apply"], transport=t, env=self.ENV, web_transport=web)
        self.assertEqual(code, 1)
        self.assertIn("ttl", out.getvalue())

    def test_apply_posts_the_whole_settings_form_with_the_file_selector(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        web = FakeWeb(fields={})
        with contextlib.redirect_stdout(io.StringIO()):
            mod.main(["apply"], transport=t, env=self.ENV, web_transport=web)
        # Pick the feed by its id: a selector is not unique to one feed, and `main` already names
        # two of them.
        docker_id = next(i for i, f in enumerate(feeds, start=1) if f.title == "Docker blog")
        docker = [c for c in web.posts() if f"id={docker_id}" in c[1]]
        self.assertEqual(len(docker), 1)
        body = urllib.parse.parse_qs(docker[0][3].decode())
        self.assertEqual(body["_csrf"], ["CSRF"])
        self.assertEqual(body["ttl"], ["1800"])
        self.assertEqual(body["mute"], ["1"])
        self.assertEqual(body["path_entries"], [".entry-content"])

    def test_apply_twice_posts_no_second_settings_form(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        web = FakeWeb(fields={})
        with contextlib.redirect_stdout(io.StringIO()):
            mod.main(["apply"], transport=t, env=self.ENV, web_transport=web)
            before = len(web.posts())
            mod.main(["apply"], transport=t, env=self.ENV, web_transport=web)
        self.assertEqual(len(web.posts()), before)

    def test_apply_reports_what_the_instance_still_shows_when_a_post_does_not_take(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        fields = {str(i + 1): {"path_entries": "old",
                               "feed_kind": "10" if f.kind == "xpath" else "0"}
                  for i, f in enumerate(feeds)}
        web = FakeWeb(fields=fields, store=False)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = mod.main(["apply"], transport=t, env=self.ENV, web_transport=web)
        self.assertEqual(code, 1)
        self.assertIn("path_entries old -> main", out.getvalue())

    def test_a_dry_run_posts_nothing(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        web = FakeWeb(fields={})
        with contextlib.redirect_stdout(io.StringIO()):
            mod.main(["apply", "--dry-run"], transport=t, env=self.ENV, web_transport=web)
        self.assertEqual(web.posts(), [])

    def test_without_the_web_password_settings_are_not_checked_and_the_run_still_passes(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        web = FakeWeb(fields={})
        env = {k: v for k, v in self.ENV.items() if k != "FRESHRSS_WEB_PASSWORD"}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = mod.main(["check"], transport=t, env=env, web_transport=web)
        self.assertEqual(code, 0)
        self.assertIn("FRESHRSS_WEB_PASSWORD", out.getvalue())
        self.assertEqual(web.calls, [])

    def test_an_instance_without_a_form_login_is_not_checked_and_the_run_passes(self):
        """Behind OIDC the app has no form login: nothing to read or write there."""
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        web = FakeWeb(fields={}, page_status=401)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = mod.main(["check"], transport=t, env=self.ENV, web_transport=web)
        self.assertEqual(code, 0)
        self.assertIn("no form login", out.getvalue())

    def test_a_refused_web_login_exits_two(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        env = dict(self.ENV, FRESHRSS_WEB_PASSWORD="wrong")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = mod.main(["check"], transport=t, env=env, web_transport=FakeWeb(fields={}))
        self.assertEqual(code, 2)
