"""Press-release crawl: an unreachable portal is an error, not "nothing found"."""
import os
import tempfile
import unittest
from unittest.mock import patch

from rsb_search import pipeline as rp

LISTING = """<table><tr><td>1</td><td>14-10-2026</td>
<td><a href="view_detail.jsp?id=1">रेल समाचार</a></td></tr></table>"""


class ZoneFailureTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.out = os.path.join(d.name, "rsb.json")
        sleep = patch.object(rp.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_every_zone_failing_raises(self):
        with patch.object(rp.crawler, "_fetch", side_effect=OSError("handshake failure")):
            with self.assertRaisesRegex(RuntimeError, "Could not reach any railway zone"):
                rp.run_rsb_crawl(zone_codes=["CR", "NER"], days=7,
                                 out_file=self.out, verbose=False)
        self.assertFalse(os.path.exists(self.out))    # no empty result written

    def test_empty_window_reports_the_newest_release(self):
        def fetch(session, url, retries=3):
            if "cr." in url:
                raise OSError("handshake failure")
            return LISTING
        report = {}
        with patch.object(rp.crawler, "_fetch", side_effect=fetch), \
                patch.object(rp.crawler, "_parse_listing", return_value=[
                    {"title": "रेल समाचार", "date": "14-10-2026",
                     "detail_url": "https://ner.example/view_detail.jsp?id=1"}]):
            arts = rp.run_rsb_crawl(zone_codes=["CR", "NER"],
                                    date_from="01-09-2026", date_to="30-09-2026",
                                    out_file=self.out, verbose=False, report=report)
        self.assertEqual(arts, [])
        self.assertEqual(list(report["failed"]), ["CR"])
        self.assertEqual(report["newest"], ("NER", "14-10-2026"))

    def test_zone_session_offers_the_legacy_cipher(self):
        adapter = rp.zone_session().get_adapter("https://ner.indianrailways.gov.in/")
        ctx = adapter.poolmanager.connection_pool_kw["ssl_context"]
        self.assertIn("AES128-GCM-SHA256", {c["name"] for c in ctx.get_ciphers()})


if __name__ == "__main__":
    unittest.main()
