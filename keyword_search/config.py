"""research/keyword_search/config.py — configuration for the keyword-search pipeline.

Everything `keyword_search/pipeline.py` needs: which search engines to ask, how to ask
them, and how strict to be with what comes back. Attribute names that the UI and
experiment.py read (CRAWL_OUTPUT, DAYS_BACK, …) match web_search/config.py, so this
pipeline needs no special cases there.

Engines, measured 2026-10-12 with the query रेलवे
-------------------------------------------------
    brave   Brave News API. 10 Hindi results, most under an hour old, direct publisher
            URLs. Needs BRAVE_API_KEY and is billed per query.
    bing    Bing News RSS. Free, no key. ~10 results; the real article URL is in the
            `url=` parameter of Bing's click-through link, so it decodes locally.
            7 of 8 bodies extracted in full with web_search.pipeline.extract_body.

Not used: Google News RSS (encrypted redirects, 0 of 18 Hindi URLs resolved) and
GDELT (refused every request here with its one-per-5-seconds limit).
"""

import os

from web_search import config as _web

_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------- http

# Article pages are fetched by web_search.pipeline.extract_body, with its headers.
HEADERS = dict(_web.HEADERS)
TIMEOUT = _web.TIMEOUT

# ---------------------------------------------------------------- engines

#: Asked in this order for every keyword. When two engines return the same story,
#: the earlier engine's copy is kept.
ENGINES = ["brave", "bing"]

ENGINE_LABELS = {"brave": "Brave News", "bing": "Bing News RSS"}

BRAVE_NEWS_URL = "https://api.search.brave.com/res/v1/news/search"
BRAVE_PARAMS = {"country": "IN", "search_lang": "hi"}
#: Human-readable search page, recorded as each article's `listing_url` so a result
#: can be traced back to the query that found it. The API URL needs a key to open.
BRAVE_PUBLIC_URL = "https://search.brave.com/news?q={query}"

BING_RSS_URL = "https://www.bing.com/news/search"
BING_PARAMS = {"format": "rss", "setlang": "hi", "cc": "IN"}

#: Results requested per engine per keyword. Brave honours it; Bing RSS returns
#: about 10 regardless.
RESULTS_PER_QUERY = 20

# ------------------------------------------------------------------ paths

OUTPUT_DIR = os.path.join(_RESEARCH_DIR, "output")
CRAWL_OUTPUT = os.path.join(OUTPUT_DIR, "search_crawl_output_research.json")
AGENCY_OUTPUT = os.path.join(OUTPUT_DIR, "search_agency_output_research.json")
SCORED_OUTPUT = os.path.join(OUTPUT_DIR, "search_agency_output_research_scored.json")
STATUS_FILE = os.path.join(OUTPUT_DIR, "search_pipeline_status_research.json")

# --------------------------------------------------------- crawl defaults

#: Searched when the user gives no keywords.
DEFAULT_KEYWORDS = ["रेलवे", "भारतीय रेल", "वंदे भारत", "ट्रेन रद्द", "रेल हादसा"]

# Per-keyword cap on KEPT articles, across all engines. Off by default: each engine
# already returns at most RESULTS_PER_QUERY results per keyword.
LIMIT_PER_KEYWORD = False
MAX_PER_KEYWORD = 5         # only applied when LIMIT_PER_KEYWORD is True

MIN_BODY_CHARS = _web.MIN_BODY_CHARS
REQUEST_DELAY = _web.REQUEST_DELAY   # seconds between article fetches
SEARCH_DELAY = 1.0                   # seconds between search-engine calls
RELEVANCE_FILTER = True

#: Results from these hosts are dropped before fetching. They are aggregators,
#: social sites or reference pages: either JavaScript shells with no body to extract
#: (msn.com returned 0 chars) or not news at all.
SKIP_DOMAINS = [
    "msn.com", "news.google.com", "bing.com", "youtube.com", "facebook.com",
    "instagram.com", "x.com", "twitter.com", "wikipedia.org",
]

# --------------------------------------------------------- date window

DAYS_BACK = 7
KEEP_UNDATED = True
