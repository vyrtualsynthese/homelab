import importlib.util
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
