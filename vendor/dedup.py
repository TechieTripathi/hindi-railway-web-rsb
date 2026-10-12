# dedup.py — the webapp's title normaliser, Latin-only.
# Vendored from the webapp; see vendor/__init__.py. Kept as the baseline that
# shared/hindi_patches.py replaces and verify_patches() measures against. The
# persistent seen_articles.json functions are left out.

import hashlib
import re


def _normalise(text):
    """Lowercase, strip whitespace, remove punctuation for comparison."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def _make_key(zone_code, title):
    """Create a unique hash key for an article."""
    raw = "{}||{}".format(zone_code, _normalise(title))
    return hashlib.md5(raw.encode('utf-8')).hexdigest()
