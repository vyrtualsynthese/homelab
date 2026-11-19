import contextlib
import importlib.util
import io
import json
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


class ParseTests(unittest.TestCase):
    def test_the_shipped_opml_yields_18_feeds_in_five_categories(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        self.assertEqual(len(feeds), 18)
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

    def test_twelve_feeds_carry_a_full_content_selector(self):
        text = OPML_PATH.read_text()
        self.assertEqual(text.count("frss:cssFullContent="), 12)

    def test_a_full_content_selector_does_not_add_or_drop_a_feed(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        self.assertEqual(len(feeds), 18)

    def test_a_full_content_selector_reaches_the_feed_as_path_entries(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        docker = next(f for f in feeds if f.title == "Docker blog")
        self.assertEqual(dict(docker.settings), {"path_entries": ".entry-content"})

    def test_an_xpath_feed_carries_its_selectors_as_form_fields(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        anth = next(f for f in feeds if f.title == "Anthropic / Claude")
        self.assertEqual(dict(anth.settings), {
            "path_entries": "article",
            "xPathItem": "//a[starts-with(@href,'/news/')]",
            "xPathItemTitle": "normalize-space(.)",
            "xPathItemUri": "@href",
        })

    def test_a_feed_without_frss_attributes_carries_no_settings(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        steam = next(f for f in feeds if f.title == "Steam")
        self.assertEqual(steam.settings, ())

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
        t = FakeTransport(subs=[], refuse=("https://mistral.ai/news/rss#force_feed",))
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
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(["verify"], transport=transport, env={}), 1)

    def test_verify_exits_zero_when_every_fetch_answers(self):
        rss = (b'<?xml version="1.0"?><rss><channel><title>t</title>'
               b'<item><title>a</title></item></channel></rss>')

        def transport(method, url, headers=None, data=None):
            return 200, rss.decode()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(["verify"], transport=transport, env={}), 0)


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
  <select name="ttl">{select("ttl", ("0", "1800"), "1800")}</select>
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
                 relative_actions=False, salt1=None, after_post=None):
        self.calls = []
        self.logged_in = False
        self.nonce = "N0NCE123"
        self.login_status = login_status
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
                return 200, self.login_page(), {}
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
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mod.main(["check"], transport=t, env=self.ENV,
                     web_transport=self.matching_web(feeds))
        self.assertIn("settings: 13 of 13 feeds match the file", out.getvalue())

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
        docker = [c for c in web.posts() if b"path_entries=main" in (c[3] or b"")]
        self.assertEqual(len(docker), 1)
        body = urllib.parse.parse_qs(docker[0][3].decode())
        self.assertEqual(body["_csrf"], ["CSRF"])
        self.assertEqual(body["ttl"], ["1800"])
        self.assertEqual(body["mute"], ["1"])

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

    def test_a_refused_web_login_exits_two(self):
        feeds = mod.parse_opml(OPML_PATH.read_text())
        t = FakeTransport(subs=[(f.url, f.title, f.category) for f in feeds])
        env = dict(self.ENV, FRESHRSS_WEB_PASSWORD="wrong")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = mod.main(["check"], transport=t, env=env, web_transport=FakeWeb(fields={}))
        self.assertEqual(code, 2)
