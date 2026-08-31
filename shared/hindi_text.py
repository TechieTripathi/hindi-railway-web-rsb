"""research/shared/hindi_text.py — Devanagari-aware text preprocessing.

Research-only. Pure text utilities: no crawling, no I/O, no webapp imports.
`research/shared/hindi_patches.py` is what binds these onto the webapp modules at runtime.

Why this exists
---------------
The webapp's normalizers were written for English and delete Devanagari outright:

    re.sub(r'[^a-z0-9\\s]', '', text)      # dedup._normalise, web_agent._make_dedup_key
    re.findall(r'[a-z]{4,}', text)         # web_agent._title_words

Measured against 16 real Hindi articles: 16 distinct titles collapsed to 12 md5 keys
(5 unrelated articles normalised to a single space), and 14 of 16 titles produced an
empty word set, so similarity grouping never ran.

Regex alone is not enough. The corpus contains the same word at two different code
point sequences — हावड़ा (Howrah) appears both precomposed (U+095C) and as base +
combining nukta (U+093C), plus 24 ZWJ characters. Those still hash differently after a
regex fix. `indic-nlp-library`'s normalizer collapses them; NFC handles the nukta but
not the ZWJ, so it is the fallback rather than the primary path.
"""

import re
import unicodedata

# --------------------------------------------------------------- backend

try:
    from indicnlp.normalize.indic_normalize import IndicNormalizerFactory
    _NORMALIZER = IndicNormalizerFactory().get_normalizer("hi")
    BACKEND = "indic-nlp-library"
except Exception:                                    # pragma: no cover
    _NORMALIZER = None
    BACKEND = "unicodedata-NFC (fallback)"

DEVANAGARI = r"ऀ-ॿ"
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿­"), None)


def normalize_hi(text):
    """Canonicalise Devanagari so identical words compare equal.

    Unifies nukta encodings, strips zero-width joiners, and applies NFC. Latin text
    passes through unchanged apart from whitespace collapsing.
    """
    if not text:
        return ""
    text = text.translate(_ZERO_WIDTH)
    if _NORMALIZER is not None:
        try:
            text = _NORMALIZER.normalize(text)
        except Exception:
            text = unicodedata.normalize("NFC", text)
    else:
        text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------ stopwords

# Hindi function words. Devanagari has no case, so these are matched as-is.
HINDI_STOPWORDS = {
    "का", "की", "के", "को", "में", "से", "पर", "और", "है", "हैं", "हुआ", "हुई",
    "ने", "यह", "वह", "इस", "उस", "कि", "भी", "तक", "हो", "कर", "लिए", "साथ",
    "एक", "अब", "जो", "था", "थी", "थे", "गया", "गई", "गए", "रहा", "रही", "रहे",
    "होगा", "होगी", "होंगे", "सकता", "सकती", "वाले", "वाला", "वाली", "कोई",
    "क्या", "कैसे", "कहां", "क्यों", "जब", "तब", "यदि", "अगर", "लेकिन", "या",
    "बाद", "पहले", "दौरान", "बीच", "ओर", "अपनी", "अपने", "उन्होंने", "जाएगा",
}

# Terms so common in railway news they carry no discriminating signal. Mirrors the
# English list already in web_agent._title_words.
GENERIC_TERMS = {
    "रेल", "रेलवे", "ट्रेन", "ट्रेनें", "भारतीय", "भारत", "समाचार", "न्यूज",
    "खबर", "अपडेट", "जानिए", "देखें", "बड़ी", "नई", "नया", "आज", "करोड़", "लाख",
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her",
    "was", "one", "our", "out", "has", "from", "that", "this", "with", "will",
    "what", "how", "its", "than", "also", "been", "have", "more", "after",
    "into", "over", "says", "said", "amid", "among", "about", "being", "some",
    "when", "here", "news", "india", "indian", "railway", "railways", "train",
    "trains", "check", "report", "reports", "update", "latest", "today",
    "crore", "lakh", "million", "year", "quarter", "fiscal", "share", "shares",
    "stock", "price", "market", "company", "limited", "international",
    "corporation", "new", "set", "gets", "give", "take", "make", "know", "need",
}

# Publisher suffix: "… - अमर उजाला", "… | Navbharat Times". The webapp's version
# requires [A-Z] after the dash, so Hindi suffixes are never stripped.
#
# Whitespace on BOTH sides of the separator is required. Without it this eats
# hyphenated compounds — "…बिछाएगा 4-लेन ट्रैक, इन 7 रूटों पर चलेगा काम" would lose
# everything from "-लेन" onward.
_SUFFIX = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,30}$")


def has_devanagari(text):
    return bool(text) and re.search(r"[%s]" % DEVANAGARI, text) is not None


def _legacy_title_words(title):
    """Verbatim copy of web_agent._title_words, for English parity."""
    title = re.sub(r"\s*[-–—|]\s*[A-Z][\w\s,.]+$", "", title)
    words = set(re.findall(r"[a-z]{4,}", title.lower()))
    return words - GENERIC_TERMS


def tokenize_hi(text, min_hi=3, min_en=4):
    """Split normalised text into comparable Devanagari and Latin tokens."""
    if not text:
        return set()
    text = normalize_hi(text)
    hi = set(re.findall(r"[%s]{%d,}" % (DEVANAGARI, min_hi), text))
    en = set(re.findall(r"[a-z]{%d,}" % min_en, text.lower()))
    return hi | en


def title_words(title):
    """Drop-in replacement for web_agent._title_words — bi-script.

    Pure-Latin titles are delegated to a verbatim copy of the webapp's own logic, so
    the existing English pipeline and its persisted hashes are untouched. Only titles
    actually containing Devanagari take the new path.
    """
    if not title:
        return set()
    if not has_devanagari(title):
        return _legacy_title_words(title)
    title = _SUFFIX.sub("", normalize_hi(title))
    return tokenize_hi(title) - HINDI_STOPWORDS - GENERIC_TERMS


def normalise_key(text):
    """Drop-in replacement for dedup._normalise — keeps Devanagari.

    Mirrors the original contract (lowercase, punctuation stripped, whitespace
    collapsed) but preserves the U+0900–U+097F range and canonicalises first so the
    two encodings of a word hash identically.
    """
    if not text:
        return ""
    text = normalize_hi(text).lower()
    text = re.sub(r"[^a-z0-9%s\s]" % DEVANAGARI, "", text)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------ relevance

# Unambiguous — any single hit admits the article.
STRONG_TERMS = [
    "रेलवे", "ट्रेन", "रेलगाड़ी", "आईआरसीटीसी", "वंदे भारत", "अमृत भारत",
    "रेल मंत्रालय", "रेलयात्री", "railway", "railways", "irctc", "vande bharat",
    "indian rail",
]

# Ambiguous outside a railway context — require two.
#   कोच  = railway coach AND sports coach   (matched "मुख्य कोच गौतम गंभीर")
#   यात्री = rail passenger AND any traveller/pilgrim
#   स्टेशन = railway station AND police/TV station
WEAK_TERMS = [
    "रेल", "ट्रैक", "एक्सप्रेस", "स्टेशन", "लोको", "डिब्बे", "कोच", "यात्री",
    "प्लेटफॉर्म", "मालगाड़ी", "train", "rail", "coach", "platform",
]


# Devanagari has no case and no boundary `\b` handles reliably. Match whole tokens
# instead of substrings, comparing light stems.
#
# Two failures make this necessary, and they pull in opposite directions:
#   over-match: ट्रेन  inside ट्रेनिंग ("training") -> an HR article reads as railway news
#               कोच   inside "मुख्य कोच गौतम गंभीर" -> a cricket coach reads as a carriage
#   under-match: रेलगाड़ी vs रेलगाड़ियों -- Hindi inflection CHANGES the stem (ी -> ि),
#               so neither substring nor a suffix whitelist links them, and a real
#               cancellation notice gets rejected as off-topic.
#
# A plain length allowance cannot separate these: Devanagari matras are separate
# codepoints, so ट्रेन -> ट्रेनिंग adds exactly as many as दुर्घटना -> दुर्घटनाओं.
# Stemming both sides and comparing for equality does.

# Longest first — order matters, these are stripped greedily.
_SUFFIXES = ("ाओं", "ाएँ", "ाएं", "ियों", "ियाँ", "ियां", "ों", "ें", "ओं",
             "यों", "याँ", "ुओं", "ी", "ि", "े", "ा", "ो", "ु", "ू", "ं", "ें")


def light_stem(word):
    """Strip one Hindi inflectional suffix, then any trailing matra.

    Deliberately crude — linguistic correctness matters less than applying the SAME
    transformation to both the lexicon term and the text token.
    """
    w = word
    for suf in _SUFFIXES:
        if len(w) - len(suf) >= 2 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    while len(w) > 2 and w[-1] in "ािीुूेैोौंँृ":
        w = w[:-1]
    return w


def term_in(term, tokens, blob):
    """True if `term` occurs as a token, comparing light stems on both sides."""
    term = normalize_hi(term).lower()
    if " " in term:                     # multi-word phrase: substring is safe enough
        return term in blob
    stem = light_stem(term)
    for tok in tokens:
        if tok == term or light_stem(tok) == stem:
            return True
    return False


def is_railway_relevant(*texts):
    """Two-tier railway topicality test.

    One strong term is sufficient; ambiguous terms need corroboration. Matching is
    token-bounded — the substring version admitted a PIB sports release on 'कोच' and
    an HR training article on 'ट्रेन' inside 'ट्रेनिंग'.
    """
    raw = " ".join(t for t in texts if t)
    blob = normalize_hi(raw).lower()
    if not blob:
        return False
    tokens = {w.lower() for w in tokenize_hi(raw, min_hi=2, min_en=3)}
    if any(term_in(t, tokens, blob) for t in STRONG_TERMS):
        return True
    return sum(1 for t in WEAK_TERMS if term_in(t, tokens, blob)) >= 2
