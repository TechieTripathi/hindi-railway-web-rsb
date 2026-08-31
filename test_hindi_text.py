"""research/test_hindi_text.py — regression tests for Hindi text handling.

Run:  .venv/bin/python3 research/test_hindi_text.py     (or: pytest research/)

These exist because three bugs of the SAME class shipped in a row, each invisible in
review because the wrong answer looked plausible:

  1. Latin-only regex silently deleted all Devanagari    (16 dedup keys -> 12)
  2. Substring matching fired on word fragments          (कोच in "मुख्य कोच गौतम गंभीर")
  3. Fixing (2) too aggressively caused false negatives  (रेलगाड़ियों rejected as off-topic)

Every case below is a real defect that reached the corpus.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_HERE), _HERE):          # project root, then research/
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.hindi_text import (  # noqa: E402
    normalise_key, title_words, is_railway_relevant, light_stem, normalize_hi,
)
from shared.grounding import extract_numbers, verify_impact  # noqa: E402


def test_devanagari_survives_normalisation():
    """The webapp's [^a-z0-9\\s] deleted every Devanagari codepoint."""
    assert normalise_key("रेलवे समाचार") != ""
    a = normalise_key("आज भी मथुरा तक जाएंगी रक्षाबंधन पर बढ़ी ट्रेन")
    b = normalise_key("देव की डायरी: भारतीय रेल के यात्री")
    assert a != b, "distinct Hindi titles must not collapse to one key"


def test_nukta_encodings_unify():
    """हावड़ा appears precomposed (U+095C) AND as base+nukta (U+093C) in one corpus."""
    pre, comb = "हावड़ा", "हावड़ा"
    assert normalize_hi(pre) == normalize_hi(comb)
    assert normalise_key(pre) == normalise_key(comb)


def test_hindi_titles_tokenise():
    """[a-z]{4,} yielded an empty set for 14 of 16 Hindi titles, so grouping never ran."""
    for t in ["अमृत भारत 2.0 से बदलेगी रेल यात्रा की तस्वीर",
              "आज भी मथुरा तक जाएंगी रक्षाबंधन पर बढ़ी ट्रेन"]:
        assert len(title_words(t)) >= 3, t


def test_english_parity_preserved():
    """Pure-Latin titles must behave exactly as the webapp does, or hashes break."""
    import web_agent
    for t in ["Indian Railways reforms boost cement movement",
              "Railways to run 2 new trains for Ahmedabad, Bengaluru routes"]:
        assert title_words(t) == web_agent._title_words(t), t


def test_relevance_rejects_word_fragments():
    """कोच (carriage) must not fire inside 'मुख्य कोच' (sports coach);
    ट्रेन must not fire inside ट्रेनिंग (training)."""
    assert is_railway_relevant("प्रशिक्षण ट्रेनिंग कार्यक्रम आयोजित") is False
    assert is_railway_relevant("भारतीय क्रिकेट टीम के मुख्य कोच गौतम गंभीर") is False


def test_relevance_accepts_inflected_forms():
    """Over-tightening rejected a real cancellation notice — Hindi inflection
    changes the stem (रेलगाड़ी -> रेलगाड़ियों), so suffix rules alone are not enough."""
    assert is_railway_relevant("रेलगाड़ियों का आंशिक रद्दीकरण / पुनर्निर्धारण") is True
    assert is_railway_relevant("दुर्घटनाओं की जांच के आदेश रेलवे ने दिए") is True
    assert is_railway_relevant("रेलवे ने नई ट्रेन शुरू की") is True


def test_light_stem_separates_inflection_from_new_words():
    assert light_stem("रेलगाड़ियों") == light_stem("रेलगाड़ी")
    assert light_stem("ट्रेनों") == light_stem("ट्रेन")
    assert light_stem("ट्रेनिंग") != light_stem("ट्रेन")


def test_numbers_normalise_across_formats():
    """Devanagari digits, Indian digit grouping, and composite date/time literals
    must all reduce to comparable integers — otherwise 'unsupported number' counts
    are formatting noise, not hallucination."""
    assert extract_numbers("रात ८ बजे") == {8}
    assert extract_numbers("1,53,000") == extract_numbers("153000") == {153000}
    assert 29 in extract_numbers("28/29.08")


def test_impact_rule_ignores_word_fragments():
    """मृत (dead) must not fire inside अमृत भारत."""
    r = verify_impact("अमृत भारत 2.0 से बदलेगी रेल यात्रा की तस्वीर", "Medium")
    assert r["impact_rule_level"] != "High", r["impact_evidence"]


def test_real_corpus_gate():
    """The PIB sports release must stay out; every genuine railway article must stay in."""
    import json
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "output", "web_agency_output_research.json")
    if not os.path.exists(p):
        return
    arts = json.load(open(p, encoding="utf-8"))
    flags = [is_railway_relevant(a["title"], a["body"][:600]) for a in arts]
    assert flags[0] is False, "PIB sports release must be rejected"
    assert all(flags[1:]), "genuine railway articles must be kept"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print("  PASS  %s" % name)
        except AssertionError as e:
            failed += 1
            print("  FAIL  %s -> %s" % (name, e))
        except Exception as e:
            failed += 1
            print("  ERROR %s -> %s: %s" % (name, type(e).__name__, e))
    print("\n%d passed, %d failed" % (len(fns) - failed, failed))
    sys.exit(1 if failed else 0)
