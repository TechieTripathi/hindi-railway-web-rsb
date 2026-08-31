"""research/shared/hindi_patches.py — bind Devanagari-aware preprocessing onto the webapp.

Research-only. Rebinds module attributes at runtime; no webapp file is edited on disk.
This is the same technique research/notebooks/web_news_pipeline_research.ipynb already uses for
`web_agent.WEB_CRAWL_OUTPUT`, `web_agent.load_web_settings`, etc.

Verified (see verify_patches) that the rebinds reach internal callers: both
`_group_similar_articles` and `_dedup_by_source_overlap` look `_title_words` up as a
module global, and `dedup._make_key` looks up `_normalise` the same way, so
`deduplicate_articles` picks up the fix even though crawler.py imported it by reference.
"""

import hashlib
import os
import sys

# One level down from research/ now, so climb twice.
_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_RESEARCH_DIR)
for _p in (_PROJECT_ROOT, _RESEARCH_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dedup            # noqa: E402
import web_agent        # noqa: E402

from shared import hindi_text   # noqa: E402

_ORIGINALS = {}


def _hindi_dedup_key(title):
    """web_agent._make_dedup_key with Devanagari preserved."""
    return hashlib.md5(hindi_text.normalise_key(title).encode("utf-8")).hexdigest()


def apply_hindi_patches(verbose=True):
    """Replace the Latin-only normalizers with bi-script versions.

    Idempotent. Returns the dict of originals so revert_hindi_patches can restore them.
    """
    if not _ORIGINALS:
        _ORIGINALS["dedup._normalise"] = dedup._normalise
        _ORIGINALS["web_agent._make_dedup_key"] = web_agent._make_dedup_key
        _ORIGINALS["web_agent._title_words"] = web_agent._title_words

    dedup._normalise = hindi_text.normalise_key
    web_agent._make_dedup_key = _hindi_dedup_key
    web_agent._title_words = hindi_text.title_words

    if verbose:
        print("Hindi preprocessing patches applied (backend: %s)" % hindi_text.BACKEND)
        print("  dedup._normalise          -> hindi_text.normalise_key")
        print("  web_agent._make_dedup_key -> hindi_text.normalise_key + md5")
        print("  web_agent._title_words    -> hindi_text.title_words")
    return _ORIGINALS


def revert_hindi_patches():
    """Restore the webapp's own implementations."""
    if not _ORIGINALS:
        return
    dedup._normalise = _ORIGINALS["dedup._normalise"]
    web_agent._make_dedup_key = _ORIGINALS["web_agent._make_dedup_key"]
    web_agent._title_words = _ORIGINALS["web_agent._title_words"]


def verify_patches(titles=None, verbose=True):
    """Prove the patches work and actually reach the internal callers.

    Returns a dict of before/after metrics. Restores whatever patch state it found.
    """
    titles = titles or [
        "आज भी मथुरा तक जाएंगी रक्षाबंधन पर बढ़ी ट्रेन",
        "देव की डायरी: आप भारतीय रेल के यात्री हैं, कृपया अपनी नाक सुरक्षित रखें!",
        "चलती ट्रेन में यात्री की माैत: मिर्गी के दाैरे से गई युवक की जान",
        "यूपी: ट्रेन से यात्रा करने वालों के लिए जरूरी खबर, बदल गया है समय",
        "अमृत भारत 2.0 से बदलेगी रेल यात्रा की तस्वीर",
    ]
    was_patched = bool(_ORIGINALS) and dedup._normalise is hindi_text.normalise_key

    revert_hindi_patches()
    before_keys = len({web_agent._make_dedup_key(t) for t in titles})
    before_empty = sum(1 for t in titles if len(web_agent._title_words(t)) < 3)

    apply_hindi_patches(verbose=False)
    after_keys = len({web_agent._make_dedup_key(t) for t in titles})
    after_empty = sum(1 for t in titles if len(web_agent._title_words(t)) < 3)

    # Does the grouping path actually see the patched tokenizer?
    pair = [
        {"title": "रेलवे बिछाएगा 4-लेन ट्रैक इन 7 रूटों पर चलेगा काम",
         "body": "x" * 200, "source": "A", "link": "u1", "date": ""},
        {"title": "रेलवे बिछाएगा 4-लेन ट्रैक इन 7 रूटों पर काम चलेगा",
         "body": "y" * 100, "source": "B", "link": "u2", "date": ""},
    ]
    grouped = web_agent._group_similar_articles(pair)
    similar_count = grouped[0].get("similar_count", 1) if grouped else 0

    # Nukta: same word, two encodings, must hash equal.
    pre = "हावड़ा"                    # हावड़ा precomposed
    comb = "हावड़ा"             # हावड़ा base + nukta
    nukta_ok = web_agent._make_dedup_key(pre) == web_agent._make_dedup_key(comb)

    if not was_patched:
        revert_hindi_patches()

    result = {
        "backend": hindi_text.BACKEND,
        "titles": len(titles),
        "keys_before": before_keys, "keys_after": after_keys,
        "empty_wordsets_before": before_empty, "empty_wordsets_after": after_empty,
        "grouping_similar_count": similar_count,
        "nukta_variants_hash_equal": nukta_ok,
    }
    if verbose:
        print("Backend: %s" % result["backend"])
        print("  distinct dedup keys      %d -> %d   (of %d titles)"
              % (before_keys, after_keys, len(titles)))
        print("  titles with <3 words     %d -> %d" % (before_empty, after_empty))
        print("  grouping similar_count   %d   (2 = patched tokenizer reached "
              "_group_similar_articles)" % similar_count)
        print("  nukta variants hash equal %s" % nukta_ok)
    return result


if __name__ == "__main__":
    verify_patches()
