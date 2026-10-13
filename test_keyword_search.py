"""research/test_keyword_search.py — offline tests for the keyword-search pipeline.

Run:  .venv/bin/python3 -m unittest test_keyword_search

No network: engines and article fetches are replaced with fakes. The parser fixtures
are trimmed copies of real Brave News and Bing News RSS responses (2026-10-12).
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from keyword_search import config as CFG          # noqa: E402
from keyword_search import engines as ENG         # noqa: E402
from keyword_search import pipeline as KP         # noqa: E402

TODAY = date.today().isoformat()
OLD = (date.today() - timedelta(days=30)).isoformat()

BING_ITEM = (
    "<rss><channel><item><title>राजधानी एक्सप्रेस को टक्कर देने आ रही 24 कोच वाली वंदे भारत"
    "</title><link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;tid=6acc"
    "&amp;url=https%3a%2f%2fwww.jagran.com%2fbusiness%2fbiz-vande-bharat-40402583.html"
    "&amp;c=166&amp;mkt=en-in</link><description>भारतीय रेलवे 24 कोच वाली वंदे भारत स्लीपर"
    "</description><pubDate>Mon, 12 Oct 2026 06:43:00 GMT</pubDate>"
    "<News:Source>दैनिक जागरण</News:Source></item></channel></rss>"
)

BRAVE_RESULTS = [{
    "title": "रुद्रपुर: पटरी से उतरी मालगाड़ी की दो बोगियां",
    "url": "https://www.etvbharat.com/hi/state/rudrapur-goods-train-derail",
    "description": "<strong>रेलवे</strong> यातायात 6 घंटे बाधित",
    "page_age": "2026-10-12T05:10:00",
    "meta_url": {"hostname": "www.etvbharat.com"},
}]

RAIL_BODY = "भारतीय रेलवे ने आज नई वंदे भारत ट्रेन चलाने की घोषणा की। " * 20


def hit(url, title, engine="bing", day=TODAY, query="वंदे भारत"):
    return {"url": url, "title": title, "source": ENG.host_of(url), "date": day,
            "snippet": "", "engine": engine, "query": query, "listing_url": "L"}


class ParserTests(unittest.TestCase):
    def test_bing_click_through_decodes_to_publisher_url(self):
        link = ("http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;tid=1"
                "&amp;url=https%3a%2f%2fwww.amarujala.com%2fa%2fb.html&amp;c=2")
        self.assertEqual(ENG.bing_real_url(link), "https://www.amarujala.com/a/b.html")
        self.assertEqual(ENG.bing_real_url("https://x.com/a"), "https://x.com/a")

    def test_bing_rss_item_parses(self):
        [h] = ENG.parse_bing_rss(BING_ITEM, "वंदे भारत")
        self.assertEqual(h["url"], "https://www.jagran.com/business/biz-vande-bharat-40402583.html")
        self.assertEqual(h["source"], "दैनिक जागरण")
        self.assertEqual(h["date"], "2026-10-12")
        self.assertEqual(h["engine"], "bing")
        self.assertTrue(h["title"].startswith("राजधानी एक्सप्रेस"))

    def test_brave_result_parses(self):
        [h] = ENG.parse_brave(BRAVE_RESULTS, "रेलवे")
        self.assertEqual(h["source"], "etvbharat.com")
        self.assertEqual(h["date"], "2026-10-12")
        self.assertEqual(h["snippet"], "रेलवे यातायात 6 घंटे बाधित")
        self.assertIn("search.brave.com/news?q=", h["listing_url"])

    def test_brave_freshness_follows_the_window(self):
        self.assertEqual([ENG.brave_freshness(d) for d in (None, 0, 1, 3, 7, 10, 31, 90)],
                         [None, None, "pd", "pw", "pw", "pm", "pm", "py"])

    def test_brave_without_key_is_skipped_without_a_request(self):
        session = Mock()
        with patch.object(ENG.config_env, "get", return_value=""):
            hits, err = ENG.search_brave_news(session, "रेलवे")
        self.assertEqual(hits, [])
        self.assertIn("BRAVE_API_KEY", err)
        session.get.assert_not_called()

    def test_url_key_ignores_presentation_differences(self):
        a = KP.url_key("https://www.jagran.com/a/b.html?utm_source=bing#top")
        b = KP.url_key("http://jagran.com/a/b.html/")
        self.assertEqual(a, b)
        self.assertNotEqual(KP.url_key("https://x.com/a?id=1"), KP.url_key("https://x.com/a?id=2"))

    def test_aggregators_are_skipped(self):
        self.assertTrue(KP.skipped_domain("https://www.msn.com/hi-in/news/x"))
        self.assertTrue(KP.skipped_domain("https://m.youtube.com/watch?v=1"))
        self.assertFalse(KP.skipped_domain("https://www.amarujala.com/x"))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out.json")
        for p in (patch.object(CFG, "SEARCH_DELAY", 0),
                  patch.object(KP, "extract_body", side_effect=self.fake_fetch)):
            p.start()
            self.addCleanup(p.stop)
        self.fetched = []

    def fake_fetch(self, session, url):
        self.fetched.append(url)
        body = "" if "empty" in url else ("क्रिकेट मैच " * 60 if "cricket" in url else RAIL_BODY)
        return {"body": body, "real_url": url, "page_date": "", "extractor": "generic"}

    def run_with(self, results, **kw):
        def fake_search(engine, session, query, days=None, count=None):
            return results.get((engine, query), ([], None))
        with patch.object(KP.ENG, "search", side_effect=fake_search):
            return KP.run_keyword_search(out_file=self.out, delay=0, verbose=False, **kw)

    def test_same_story_from_two_engines_is_kept_once(self):
        arts = self.run_with({
            ("brave", "वंदे भारत"): ([hit("https://a.com/1", "वंदे भारत स्लीपर ट्रेन", "brave")], None),
            ("bing", "वंदे भारत"): ([hit("https://www.a.com/1/", "वंदे भारत स्लीपर ट्रेन"),
                                     hit("https://b.com/9", "वंदे भारत स्लीपर ट्रेन")], None),
        }, keywords=["वंदे भारत"])
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["search_engine"], "brave")
        self.assertEqual(self.fetched, ["https://a.com/1"])   # neither duplicate fetched

    def test_failing_engine_does_not_stop_the_others(self):
        arts = self.run_with({
            ("brave", "रेलवे"): ([], "HTTP 429: rate limited"),
            ("bing", "रेलवे"): ([hit("https://c.com/2", "रेलवे की नई ट्रेन", query="रेलवे")], None),
        }, keywords=["रेलवे"])
        self.assertEqual([a["search_engine"] for a in arts], ["bing"])

    def test_filters_old_short_off_topic_and_aggregator_results(self):
        arts = self.run_with({
            ("bing", "वंदे भारत"): ([
                hit("https://d.com/old", "पुरानी वंदे भारत खबर", day=OLD),
                hit("https://d.com/empty", "वंदे भारत खाली पेज"),
                hit("https://d.com/cricket", "भारत की क्रिकेट टीम जीती"),
                hit("https://www.msn.com/x", "वंदे भारत एमएसएन"),
                hit("https://d.com/good", "वंदे भारत की नई सेवा"),
            ], None),
        }, keywords=["वंदे भारत"], engines=["bing"], days=7)
        self.assertEqual([a["link"] for a in arts], ["https://d.com/good"])
        self.assertNotIn("https://d.com/old", self.fetched)    # rejected by engine date
        self.assertNotIn("https://www.msn.com/x", self.fetched)

    def test_per_keyword_cap_and_output_schema(self):
        many = [hit("https://e.com/%d" % i, "वंदे भारत खबर नंबर %d" % i) for i in range(5)]
        arts = self.run_with({("bing", "वंदे भारत"): (many, None)},
                             keywords=["वंदे भारत"], engines=["bing"], max_per_keyword=2)
        self.assertEqual(len(arts), 2)
        with open(self.out, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved, arts)
        for field in ("title", "link", "date", "source", "body", "search_keyword",
                      "search_engine", "extractor", "brave_related_articles"):
            self.assertIn(field, saved[0])

    def test_empty_keywords_search_the_defaults(self):
        asked = []

        def fake_search(engine, session, query, days=None, count=None):
            asked.append(query)
            return [], None
        with patch.object(KP.ENG, "search", side_effect=fake_search):
            KP.run_keyword_search(engines=["bing"], out_file=self.out, verbose=False)
        self.assertEqual(asked, CFG.DEFAULT_KEYWORDS)

    def test_unknown_engine_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown engine"):
            KP.run_keyword_search(engines=["google"], out_file=self.out, verbose=False)


if __name__ == "__main__":
    unittest.main()
