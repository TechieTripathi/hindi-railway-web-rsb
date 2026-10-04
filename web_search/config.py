"""research/web_search/config.py — configuration for the Hindi WEB news pipeline.

Everything `web_search/pipeline.py` needs to know about *where* and *how* to crawl the six
Hindi publishers. Kept separate from `rsb_search/config.py` because the two pipelines share
nothing operationally: one scrapes commercial news sites, the other official CRIS portals.

Split of responsibility
-----------------------
    hindi_railway_sources.json   source URLs and keywords  (editable without Python)
    web_search/config.py                crawl behaviour           (patterns, selectors, limits)
    web_search/pipeline.py            the crawling logic itself

Nothing here imports the webapp beyond its shared HTTP settings.
"""

import os
import re

# One level down from research/ now, so climb twice.
_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------- http

# Owned here rather than imported from web_agent: these are plain constants and
# there is no reason for the research crawler to reach into the product for them.
# Kept identical to web_agent.HEADERS / TIMEOUT so research is no more aggressive
# against publishers than the product is.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "Chrome/120.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "en-IN,en;q=0.9",
}
TIMEOUT = 30

# ------------------------------------------------------------------ paths

OUTPUT_DIR = os.path.join(_RESEARCH_DIR, "output")
SOURCE_CONFIG_FILE = os.path.join(_RESEARCH_DIR, "hindi_railway_sources.json")
SOURCE_GROUP = "web_search_sources"

CRAWL_OUTPUT = os.path.join(OUTPUT_DIR, "web_crawl_output_research.json")
AGENCY_OUTPUT = os.path.join(OUTPUT_DIR, "web_agency_output_research.json")
SCORED_OUTPUT = os.path.join(OUTPUT_DIR, "web_agency_output_research_scored.json")
STATUS_FILE = os.path.join(OUTPUT_DIR, "web_pipeline_status_research.json")
SEEN_FILE = os.path.join(OUTPUT_DIR, "web_seen_articles_research.json")

# --------------------------------------------------------- crawl defaults

# Per-publisher cap. OFF by default: how many reports a run produces should come
# from what the publishers actually published in the date window, not from a number
# hardcoded here. Turn LIMIT_PER_SOURCE on when you deliberately want a small run.
LIMIT_PER_SOURCE = False
MAX_PER_SOURCE = 3          # only applied when LIMIT_PER_SOURCE is True
MIN_BODY_CHARS = 300        # below this the extraction is treated as failed
REQUEST_DELAY = 1.5         # seconds between article fetches, per publisher
CANDIDATE_MULTIPLIER = 6    # listing links per article wanted, when capped

# When uncapped, this is how far down a listing page to scan. Measured: the six tag
# pages offer 6-24 links each, so this is a safety rail rather than a real limit.
LISTING_SCAN_LIMIT = 60

# PIB's listing is all-ministry, so railway releases are sparse. Scan far more
# candidates there and let the relevance filter select.
PIB_CANDIDATE_LIMIT = 60

RELEVANCE_FILTER = True     # drop non-railway articles (tag pages are ~50% off-topic)

# --------------------------------------------------------- date window

# How many days back from today a crawl accepts, when the caller does not say.
# The UI offers this as the default and lets a run override it.
DAYS_BACK = 7

# Articles whose page carries no publishable date at all — Amar Ujala publishes
# none — are KEPT when a date window is in force. Dropping them would silently
# lose real articles to prove a filter is working; they are counted instead.
KEEP_UNDATED = True

# ------------------------------------------------------- link discovery

# Per-domain patterns identifying an article URL on a listing page. Without these,
# navigation and section links are indistinguishable from articles.
LINK_PATTERNS = {
    "aajtak.in": r"/story/",
    "jansatta.com": r"/\d{6,}/?$",
    "navbharattimes.indiatimes.com": r"articleshow",
    "amarujala.com": r"\d{4}-\d{2}-\d{2}$",
    "livehindustan.com": r"/story-",
    "pib.gov.in": r"PressRelease\w*\.aspx\?PRID=\d+",
}

# Listing / section / media URLs that are never article bodies.
SKIP_LINK = re.compile(
    r"/tags?/|/topic/|/about/|articlelist|/photo|/video|/live|/web-stories|"
    r"/author/|/search|/rss|\.rss|/podcast|#",
    re.I,
)

# ------------------------------------------------------- body extraction

# CSS classes carrying the article text, per domain. These are the fragile part of the
# pipeline: a publisher redesign breaks them SILENTLY — extraction falls through to the
# generic <p> sweep and you get a short body, not an error.
#
# `web_search.pipeline` records which path won in each article's `extractor` field, so
#     Counter(a["extractor"] for a in articles)
# showing "generic" for a domain means that domain's selector has drifted.
# This has already happened once: livehindustan's original selectors named three classes
# that do not exist on the page.
BODY_SELECTORS = {
    "aajtak.in": ["story-with-main-sec", "content-area"],
    "jansatta.com": ["wp-block-post-content"],
    "navbharattimes.indiatimes.com": ["story-card-container", "articleshow"],
    "amarujala.com": ["article-desc", "hide_micropay_story"],
    "livehindustan.com": ["storyArticle", "stryCnt"],
    "pib.gov.in": ["innner-page-main-about-us-content-right-part",
                   "content-area", "pressrelease"],
}

# Sub-elements to strip before taking text (bylines, ads, share widgets).
JUNK_SELECTORS = re.compile(r"author-card|ad_|social")

# Publisher chrome that survives extraction and would otherwise reach the LLM.
BOILERPLATE_PATTERNS = [
    (r"^\s*विस्तार\s*", ""),
    (r"Add as a preferred\s*\n*\s*source on google\s*", ""),
]

# Minimum paragraph length when falling back to a generic <p> sweep, and for PIB
# where the content pane also holds navigation text.
GENERIC_PARA_MIN = 30
PIB_PARA_MIN = 40

# Meta tags checked for a publication date, in priority order.
DATE_META = [
    "article:published_time", "og:updated_time", "datePublished",
    "publishdate", "publish-date", "DC.date.issued", "sailthru.date",
]

# ---------------------------------------------------------- URL rewriting

# PIB's PressReleaseDetail.aspx is a JS shell with an empty content pane; the same PRID
# under PressReleasePage.aspx is server-rendered.
URL_REWRITES = [
    ("pib.gov.in", "PressReleaseDetail.aspx", "PressReleasePage.aspx"),
]
