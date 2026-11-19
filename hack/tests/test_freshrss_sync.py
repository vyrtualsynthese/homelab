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
