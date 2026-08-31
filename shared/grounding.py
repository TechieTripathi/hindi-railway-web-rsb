"""research/shared/grounding.py — deterministic, source-grounded quality metrics.

Research-only. Pure functions, no I/O, no API calls, no webapp imports. Modelled on
`engine._compute_plagiarism`: every score ships with the raw counts that produced it,
so an editor can audit the number instead of trusting it.

Why this exists
---------------
`fact_score`, `impact_level` and `topics` are all produced by asking the same Claude call
that writes the story to also grade it (engine.py:18-54). Nothing verifies them against
the source. Measured on 25 real bilingual articles:

    fact_score        5 distinct values, 88% clustered in 92-95, range 30-95
    plagiarism_score  13 distinct values      <- the one calculated metric
    topics            no vocabulary enforcement at all (engine.py:109)
    impact_level      Low never used: 16 Medium / 9 High / 0 Low

Design note — why COVERAGE and not PRECISION
--------------------------------------------
The obvious metric is "do the numbers in the story appear in the source". Measured, that
is saturated and useless as a score:

    numeric precision   3 distinct values, range 92-100
    numeric recall     16 distinct values, range 3-100, Spearman +0.09 vs fact_score

An earlier reading suggested ~12% of story numbers were unsupported. That was a
tokenisation artefact: normalising Devanagari digits, splitting composite date/time
literals, and comparing as int drops it from 12.4% to 0.8%. The story writes
"रात 8 बजे" where the body writes a "20:00" form — formatting, not hallucination.

So precision becomes a GATE (a hallucination guard), and recall becomes `coverage_score`
— the metric that actually carries information the self-reported score does not.

Coverage is published with its raw counts and deliberately NOT folded into a single
"fact score", because it is confounded by source shape: a timetable body with 200+
numerals justifiably yields low coverage for a correct summary.
"""

import re

from shared.hindi_text import (  # noqa: E402
    normalize_hi, is_railway_relevant, tokenize_hi, term_in,
)


# ---------------------------------------------------------------- numbers

_DEV_DIGITS = "०१२३४५६७८९"
_DEV_MAP = {ord(d): str(i) for i, d in enumerate(_DEV_DIGITS)}
# A numeral plus any separators that may glue several numerals together
# ("28/29.08", "00:40", "2025-26"). Split on those to compare components.
_NUM_RUN = re.compile(r"\d[\d,\.:\-/]*")


def extract_numbers(text):
    """Every numeric value in `text`, script- and format-normalised.

    Devanagari digits fold to ASCII; commas are stripped as digit grouping (Indian
    grouping means "1,53,000" and "153000" are the same figure and must extract the
    same); remaining composite literals ("28/29.08", "00:40") split into components.
    Values are compared as int so leading zeros do not create false mismatches.

    Both the whole grouped figure AND its components are emitted, so a story writing
    "1.53 lakh crore" still matches a source writing "153000".
    """
    if not text:
        return set()
    text = text.translate(_DEV_MAP)
    out = set()
    for run in _NUM_RUN.findall(text):
        run = run.replace(",", "")          # digit grouping, not a separator
        if run.isdigit():
            out.add(int(run))
            continue
        for part in re.split(r"[\.:\-/]", run):
            if part.isdigit():
                out.add(int(part))
    return out


def compute_coverage(source_text, ai_text):
    """How much of the source's factual detail the story actually carried over.

    coverage_score = |story ∩ source| / |source|   (recall — the discriminating signal)
    precision      = |story ∩ source| / |story|    (hallucination guard, near-saturated)
    """
    src, sto = extract_numbers(source_text), extract_numbers(ai_text)
    if not src or not sto:
        return {
            "coverage_score": None, "number_precision": None,
            "source_numbers": len(src), "story_numbers": len(sto),
            "numbers_carried": 0, "numbers_unsupported": [], "numbers_missed": [],
            "coverage_detail": "Insufficient numeric content for comparison.",
        }
    carried = sto & src
    coverage = round(100 * len(carried) / len(src))
    precision = round(100 * len(carried) / len(sto))
    if coverage >= 60:
        level = "Comprehensive"
    elif coverage >= 30:
        level = "Selective"
    else:
        level = "Sparse"
    return {
        "coverage_score": coverage,
        "number_precision": precision,
        "source_numbers": len(src),
        "story_numbers": len(sto),
        "numbers_carried": len(carried),
        "numbers_unsupported": sorted(sto - src),
        "numbers_missed": sorted(src - sto),
        "coverage_detail": "{}: story carries {} of {} source figures.".format(
            level, len(carried), len(src)),
    }


# ------------------------------------------------------------------ gates

# Precision below this means figures appear in the story that are not in the source.
_PRECISION_FLOOR = 90
_MIN_BODY = 300


def compute_gates(article):
    """Hard checks that cap how trustworthy an article can be, whatever the LLM said.

    Returns a ceiling rather than a score, so it composes with any other metric.
    """
    body = article.get("body", "") or ""
    story = article.get("ai_news_story", "") or ""
    title = article.get("title", "") or ""
    failures, ceiling = [], 100

    if not is_railway_relevant(title, body[:600]):
        failures.append("off-topic source (no railway content)")
        ceiling = 0
    if len(body) < _MIN_BODY:
        failures.append("source body under %d chars" % _MIN_BODY)
        ceiling = min(ceiling, 20)
    if not story or len(story) < 30 or story.startswith(("Generation failed", "[Not generated")):
        failures.append("degenerate story")
        ceiling = 0

    cov = compute_coverage(body, story)
    prec = cov["number_precision"]
    if prec is not None and prec < _PRECISION_FLOOR:
        failures.append("%d%% numeric precision — %d unsupported figure(s)"
                        % (prec, len(cov["numbers_unsupported"])))
        ceiling = min(ceiling, 100 - 15 * len(cov["numbers_unsupported"]))

    return {"gates_passed": not failures,
            "grounding_ceiling": max(0, ceiling),
            "gate_failures": failures}


# ----------------------------------------------------------------- topics

# The closed vocabulary the prompt specifies (engine.py:45-46) but never enforces.
TOPIC_VOCABULARY = [
    "Safety", "Infrastructure", "Passenger-Service", "Operations", "Technology",
    "Policy", "Finance", "Environment", "HR", "Tenders", "Delays", "New-Trains",
    "Station-Development", "Freight", "Tourism",
]

# Bilingual evidence terms. A topic survives only if one of these appears in the source.
TOPIC_LEXICON = {
    "Safety":              ["सुरक्षा", "दुर्घटना", "टक्कर", "पटरी से उतर", "आग", "मौत", "घायल",
                            "safety", "accident", "derail", "collision", "fire", "injur"],
    "Infrastructure":      ["ट्रैक", "लाइन", "पुल", "विद्युतीकरण", "दोहरीकरण", "निर्माण", "परियोजना",
                            "track", "bridge", "electrification", "doubling", "construction", "project"],
    "Passenger-Service":   ["यात्री", "सुविधा", "आरक्षण", "टिकट", "कोच", "बर्थ",
                            "passenger", "amenity", "reservation", "ticket", "berth"],
    "Operations":          ["परिचालन", "समय", "मार्ग", "रद्द", "डायवर्ट", "ब्लॉक",
                            "operation", "schedule", "route", "cancel", "divert", "block"],
    "Technology":          ["तकनीक", "कवच", "सिग्नल", "स्वचालित", "डिजिटल", "ऐप",
                            "technolog", "kavach", "signal", "automat", "digital", "app"],
    "Policy":              ["नीति", "मंत्रालय", "मंजूरी", "बोर्ड", "नियम", "आदेश",
                            "policy", "ministry", "approv", "board", "rule", "regulation"],
    "Finance":             ["करोड़", "लाख", "राजस्व", "निवेश", "बजट", "आय", "जुर्माना",
                            "crore", "lakh", "revenue", "investment", "budget", "fine"],
    "Environment":         ["पर्यावरण", "हरित", "सौर", "प्रदूषण", "स्वच्छ",
                            "environment", "green", "solar", "pollution", "clean"],
    "HR":                  ["भर्ती", "कर्मचारी", "नियुक्ति", "प्रशिक्षण", "पदोन्नति",
                            "recruit", "employee", "staff", "appointment", "training"],
    "Tenders":             ["निविदा", "ठेका", "अनुबंध", "tender", "contract", "bid"],
    "Delays":              ["विलंब", "देरी", "लेट", "रद्दीकरण", "delay", "late", "cancel"],
    "New-Trains":          ["नई ट्रेन", "नई रेलगाड़ी", "वंदे भारत", "अमृत भारत", "स्पेशल ट्रेन", "उद्घाटन",
                            "new train", "vande bharat", "amrit bharat", "special train", "inaugurat"],
    "Station-Development": ["स्टेशन", "अमृत भारत स्टेशन", "पुनर्विकास", "प्लेटफॉर्म",
                            "station", "redevelop", "platform"],
    "Freight":             ["माल", "मालगाड़ी", "ढुलाई", "लदान", "freight", "cargo", "goods", "loading"],
    "Tourism":             ["पर्यटन", "तीर्थ", "भारत गौरव", "tourism", "tourist", "pilgrim"],
}


def verify_topics(source_text, topics):
    """Enforce the closed vocabulary and require source evidence for each topic."""
    topics = topics or []
    blob = normalize_hi(source_text or "").lower()
    tokens = {w.lower() for w in tokenize_hi(source_text or "", min_hi=2, min_en=3)}
    invalid = [t for t in topics if t not in TOPIC_VOCABULARY]
    valid = [t for t in topics if t in TOPIC_VOCABULARY]
    supported, unsupported, evidence = [], [], {}
    for t in valid:
        hits = [k for k in TOPIC_LEXICON.get(t, []) if term_in(k, tokens, blob)]
        if hits:
            supported.append(t)
            evidence[t] = hits[:4]
        else:
            unsupported.append(t)
    return {
        "topics_verified": supported,
        "topics_unsupported": unsupported,
        "topics_off_vocabulary": invalid,
        "topic_evidence": evidence,
    }


# ----------------------------------------------------------------- impact

# Rubric from engine.py:44, made bilingual and checkable. Default is Low when nothing
# fires — engine.py:113 defaults to Medium instead, which is why Low is never seen.
IMPACT_TRIGGERS = {
    # "आग" (fire) and "मृत" (dead) are omitted: even token-matched they collide with
    # आगे / आगरा and with अमृत भारत. Use unambiguous compounds instead.
    "High": ["दुर्घटना", "टक्कर", "पटरी", "मौत", "मृतक", "घायल", "आगजनी", "रद्द", "रद्दीकरण",
             "हादसा", "accident", "derail", "collision", "death", "fatal",
             "injur", "cancelled", "cancellation", "emergency"],
    "Medium": ["नई ट्रेन", "परियोजना", "उद्घाटन", "विद्युतीकरण", "दोहरीकरण", "निर्माण", "विस्तार",
               "मंजूरी", "नई सुविधा", "new train", "project", "inaugurat", "electrification",
               "construction", "expansion", "approv", "launch", "upgrade"],
}


def verify_impact(source_text, impact_level):
    """Derive an impact level from source evidence and compare with the LLM's label."""
    blob = normalize_hi(source_text or "").lower()
    tokens = {w.lower() for w in tokenize_hi(source_text or "", min_hi=2, min_en=3)}
    matched = {}
    for level in ("High", "Medium"):
        hits = [t for t in IMPACT_TRIGGERS[level] if term_in(t, tokens, blob)]
        if hits:
            matched[level] = hits[:5]
    rule_level = "High" if "High" in matched else ("Medium" if "Medium" in matched else "Low")
    return {
        "impact_rule_level": rule_level,
        "impact_agrees": rule_level == impact_level,
        "impact_evidence": matched.get(rule_level, []),
    }


# --------------------------------------------------------------- combined

def score_article(article):
    """All deterministic checks for one article, as a flat dict of new fields."""
    body = article.get("body", "") or ""
    story = article.get("ai_news_story", "") or ""
    out = {}
    out.update(compute_coverage(body, story))
    out.update(compute_gates(article))
    out.update(verify_topics(body, article.get("topics")))
    out.update(verify_impact(body, article.get("impact_level")))
    return out
