import importlib.util
import urllib.parse
import json
import pathlib
import sys
import unittest

HACK = pathlib.Path(__file__).resolve().parents[1]
SYNC = HACK / "freshrss_sync.py"
OPML_PATH = HACK / "freshrss" / "feeds.opml"

spec = importlib.util.spec_from_file_location("freshrss_sync", SYNC)
mod = importlib.util.module_from_spec(spec)
sys.modules["freshrss_sync"] = mod
spec.loader.exec_module(mod)


class ParseTests(unittest.TestCase):
    def test_the_shipped_opml_yields_20_feeds_in_five_categories(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        self.assertEqual(len(feeds), 20)
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

    def test_normalize_url_strips_one_trailing_slash_and_keeps_the_query(self):
        self.assertEqual(mod.normalize_url("https://a.test/feed/"), "https://a.test/feed")
        self.assertEqual(mod.normalize_url("https://a.test/f?x=1&y=2"), "https://a.test/f?x=1&y=2")


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

    def test_an_internal_feed_is_not_reported_as_drift(self):
        p = mod.plan([self.feed()], [("1", "Uncategorized"), ("https://a.test/feed", "Dev")])
        self.assertEqual(p.to_prune, ())


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
            return 200, json.dumps({"subscriptions": [
                {"id": f"feed/{i + 1}", "url": u, "title": t,
                 "categories": [{"label": c}]}
                for i, (u, t, c) in enumerate(self.subs)]})
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
                self.subs = [s for s in self.subs if s[0] != feed_url]
                self.subs.append((feed_url, feed_url, label))
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

    def test_subscriptions_read_the_url_field_not_the_numeric_id(self):
        """FreshRSS 1.30 answers id="feed/5" and puts the real address in url."""
        t = FakeTransport(subs=[("https://a.test/feed", "A", "Dev")])
        c = mod.Client(base="https://x.test", user="u", password="p", transport=t)
        self.assertEqual(c.subscriptions(), [("https://a.test/feed", "Dev")])

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
        self.assertEqual(t.subs[0][2], "Jeux")


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
        self.assertEqual(rows, [("HTTP429", "KBDfans", 0, "")])

    def test_apply_reports_a_refused_feed_and_still_subscribes_the_rest(self):
        t = FakeTransport(subs=[], refuse=("https://mistral.ai/news/rss",))
        code = mod.main(["apply"], transport=t, env=self.ENV)
        self.assertEqual(code, 1)
        bodies = b"".join(c[3] or b"" for c in t.calls if b"ac=subscribe" in (c[3] or b""))
        self.assertIn(b"lobste.rs", bodies)

    def test_verify_exits_one_when_a_feed_answers_badly(self):
        rss = (b'<?xml version="1.0"?><rss><channel><title>t</title>'
               b'<item><title>a</title></item></channel></rss>')

        def transport(method, url, headers=None, data=None):
            if "guildwars2" in url:
                return 500, ""
            return 200, rss.decode()
        self.assertEqual(mod.main(["verify"], transport=transport, env={}), 1)

    def test_verify_exits_zero_when_every_fetch_answers(self):
        rss = (b'<?xml version="1.0"?><rss><channel><title>t</title>'
               b'<item><title>a</title></item></channel></rss>')

        def transport(method, url, headers=None, data=None):
            return 200, rss.decode()
        self.assertEqual(mod.main(["verify"], transport=transport, env={}), 0)
