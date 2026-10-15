"""Cut-off model answers: detected, retried, and redone on the next run."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from vendor import engine

STORY = "भारतीय रेलवे अगले साल स्लीपर कोच जोड़ेगा। " * 3


def _answer(**over):
    a = {"source_lang": "hi", "ai_headline": "रेलवे", "ai_news_story": STORY,
         "social_general": "उत्तर रेलवे तीन विशेष ट्रेनें चलाएगा।",
         "impact_reason": "Affects passengers", "topics": ["Operations"],
         "fact_details": "All figures from source", "fact_score": 90}
    a.update(over)
    return a


class CutOffTests(unittest.TestCase):
    def test_repaired_json_is_marked_truncated(self):
        cut = '{"source_lang": "hi", "ai_news_story": "%s", "social_general": "उत्तर रेलवे' % STORY
        result = engine._parse_ai_json(cut)
        self.assertEqual(result["social_general"], "उत्तर रेलवे")
        self.assertTrue(result["truncated"])
        self.assertNotIn("truncated", engine._parse_ai_json(json.dumps(_answer())))

    def test_max_tokens_stop_marks_truncated(self):
        body = {"content": [{"text": json.dumps(_answer())}], "stop_reason": "max_tokens"}
        resp = type("R", (), {"status_code": 200, "json": lambda self: body})()
        with patch.object(engine, "ANTHROPIC_API_KEY", "k"), \
                patch.object(engine.httpx, "post", return_value=resp):
            self.assertTrue(engine.generate_news("t", "b")["truncated"])

    def test_was_cut_off_uses_flag_then_footprint(self):
        self.assertTrue(engine._was_cut_off(_answer(truncated=True)))
        self.assertFalse(engine._was_cut_off(_answer(truncated=False, topics=[], fact_details="")))
        # Generated before the flag existed: the last fields are missing.
        self.assertTrue(engine._was_cut_off(_answer(topics=[], fact_details="")))
        self.assertFalse(engine._was_cut_off(_answer()))

    def test_run_generate_retries_and_replaces_a_cut_off_article(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "in.json"), os.path.join(d, "out.json")
            art = {"title": "Three special trains", "date": "08-10-2026", "body": "x" * 400}
            with open(src, "w", encoding="utf-8") as f:
                json.dump([art], f)
            # Stored from an earlier run: cut off, pre-flag footprint.
            with open(dst, "w", encoding="utf-8") as f:
                json.dump([dict(art, **_answer(topics=[], fact_details="", fact_score=30))], f)
            answers = iter([_answer(truncated=True), _answer()])
            with patch.object(engine, "generate_news", lambda t, b: next(answers)), \
                    patch.object(engine, "STATUS_FILE", os.path.join(d, "s.json")), \
                    patch.object(engine, "AI_CALL_DELAY", 0), \
                    patch.object(engine.time, "sleep"):
                final = engine.run_generate(src, dst)
            self.assertEqual(len(final), 1)              # replaced, not duplicated
            self.assertFalse(final[0]["truncated"])
            self.assertEqual(final[0]["topics"], ["Operations"])


if __name__ == "__main__":
    unittest.main()
