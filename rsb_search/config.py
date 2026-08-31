"""research/rsb_search/config.py — configuration for the official RSB (railway zone) pipeline.

Everything `rsb_search/pipeline.py` needs to know about the CRIS zone portals. Kept separate
from `web_search/config.py`: the two pipelines share no operational settings.

The central fact this file encodes
----------------------------------
`config.ZONES` (the webapp's) points every zone at `view_section.jsp?lang=0` — the
ENGLISH edition. `lang=1` is Hindi and it exists. Measured on Central Railway:

    lang=0  ->  18 releases,  0 Hindi titles
    lang=1  ->  18 releases, 17 Hindi titles, ~100% Devanagari bodies

So the webapp has never seen the Hindi press releases. `HINDI_LANG_PARAM` is the rewrite.

Coverage is NOT uniform across zones — see HINDI_COVERAGE. Trust the measured
`body_hindi_ratio` on each article rather than the zone's advertised language.
"""

import os
import re

# One level down from research/ now, so climb twice.
_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------- http

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
TIMEOUT = 30

# Pacing against gov.in hosts. Kept at the product's values so research is never
# more aggressive than the webapp.
SLEEP_LISTING = 3
SLEEP_DETAIL = 2

# ------------------------------------------------------------------ zones

# The 17 CRIS zone portals, with lang=1 (Hindi) already applied. Owned here rather
# than imported from the webapp's config.ZONES, which hardcodes lang=0 (English).
_ZONE_URLS = {
    "CR":   ("Central Railway",            "cr",             "0,4,268"),
    "ER":   ("Eastern Railway",            "er",             "0,4,268"),
    "ECoR": ("East Coast Railway",         "eastcoastrail",  "0,4,268"),
    "ECR":  ("East Central Railway",       "ecr",            "0,4,268"),
    "NR":   ("Northern Railway",           "nr",             "0,4,268"),
    "NCR":  ("North Central Railway",      "ncr",            "0,4,268"),
    "NER":  ("North Eastern Railway",      "ner",            "0,4,268"),
    "NFR":  ("Northeast Frontier Railway", "nfr",            "0,4,268"),
    "NWR":  ("North Western Railway",      "nwr",            "0,4,268"),
    "SR":   ("Southern Railway",           "sr",             "0,4,268"),
    "SCR":  ("South Central Railway",      "scr",            "0,5,268"),
    "SECR": ("South East Central Railway", "secr",           "0,1443,268"),
    "SER":  ("South Eastern Railway",      "ser",            "0,4,268"),
    "SWR":  ("South Western Railway",      "swr",            "0,4,268"),
    "WR":   ("Western Railway",            "wr",             "0,4,268"),
    "WCR":  ("West Central Railway",       "wcr",            "0,4,268"),
    "MTP":  ("Metro Railway",              "mtp",            "0,4,268"),
}

# ------------------------------------------------------------------ paths

OUTPUT_DIR = os.path.join(_RESEARCH_DIR, "output")
SOURCE_CONFIG_FILE = os.path.join(_RESEARCH_DIR, "hindi_railway_sources.json")
SOURCE_GROUP = "rsb_sources"

CRAWL_OUTPUT = os.path.join(OUTPUT_DIR, "rsb_crawl_output_research.json")
AGENCY_OUTPUT = os.path.join(OUTPUT_DIR, "rsb_agency_output_research.json")
SCORED_OUTPUT = os.path.join(OUTPUT_DIR, "rsb_agency_output_research_scored.json")
STATUS_FILE = os.path.join(OUTPUT_DIR, "rsb_pipeline_status_research.json")

# ------------------------------------------------------- Hindi edition

# view_section.jsp?lang=N  —  0 = English, 1 = Hindi.
HINDI_LANG_PARAM = 1

ZONES = [
    {"c": code, "n": name,
     "u": "https://%s.indianrailways.gov.in/view_section.jsp?lang=%d&id=%s"
          % (host, HINDI_LANG_PARAM, sid)}
    for code, (name, host, sid) in _ZONE_URLS.items()
]

# Zones worth crawling by default. Ordered by measured Hindi yield, not alphabetically.
# The four zones measured to publish real Hindi bodies. NR is excluded by default —
# its bodies are usually English; add it with hindi_only=False if you want them.
DEFAULT_ZONE_CODES = ["CR", "WR", "NCR", "NER"]

# ------------------------------------------------------- crawl defaults

PER_ZONE = 3                # releases taken per zone per run

# CRIS pages are terser than news sites, so this is lower than config_web's 300.
# Do NOT lower it further to admit zones like NWR: their short "bodies" are the title
# plus "[PDF attached: <same title>]", i.e. the headline twice with no content. The
# real text is inside the PDF. Admitting them would feed the LLM a duplicated headline
# and produce exactly the 150-220 char garbage this pipeline exists to avoid.
MIN_BODY_CHARS = 200
HINDI_ONLY = False          # True drops articles whose BODY is not majority Devanagari
HINDI_BODY_THRESHOLD = 0.5  # body_hindi_ratio at or above this counts as Hindi

# Request pacing comes from the webapp's crawler (SLEEP_LISTING / SLEEP_DETAIL) so the
# research crawl is no more aggressive against gov.in hosts than the product is.
USE_CRAWLER_DELAYS = True

# --------------------------------------------- measured Hindi coverage

# ALL 17 zones measured 2026-08-31 by crawling lang=1. Re-run to refresh:
#   experiment.run("zone-sweep", pipeline="rsb", zone_codes=[...], per_zone=2)
#
# Headline result: only 4 of 17 zones publish usable Hindi press releases (~24%).
# An empty Hindi page does NOT mean an idle zone — WCR lists 248 releases, SR 223,
# SECR 111, MTP 81, all with substantial English output behind an untranslated shell.
# The content exists; the Hindi translation does not.
HINDI_COVERAGE = {
    # --- usable: real Hindi titles AND bodies -------------------------------
    "CR":   {"usable": True,  "bodies": "hindi 95-100%"},
    "WR":   {"usable": True,  "bodies": "hindi 97-99%"},
    "NCR":  {"usable": True,  "bodies": "hindi, but terse (243-394 chars)"},
    "NER":  {"usable": True,  "bodies": "hindi (1050-2340 chars)"},

    # --- partial: Hindi headlines over English text -------------------------
    "NR":   {"usable": "partial", "bodies": "mixed — 1 of 3 Hindi, rest English"},

    # --- pdf_only: body is the title + '[PDF attached: <same title>]' --------
    #     Not a short article. There is no extractable text; the release is a PDF.
    "NWR":  {"usable": "pdf_only", "bodies": "178-194 chars = title repeated twice"},

    # --- empty: Hindi page is an unfilled template --------------------------
    "ER":   {"usable": False, "bodies": "placeholder 'Add Hindi content here'"},
    "NFR":  {"usable": False, "bodies": "placeholder"},
    "SR":   {"usable": False, "bodies": "placeholder (223 English releases exist)"},
    "SECR": {"usable": False, "bodies": "placeholder (111 English releases exist)"},
    "SWR":  {"usable": False, "bodies": "placeholder (41 English releases exist)"},
    "WCR":  {"usable": False, "bodies": "placeholder (248 English releases exist)"},
    "MTP":  {"usable": False, "bodies": "placeholder (81 English releases exist)"},
    "ECoR": {"usable": False, "bodies": "placeholder"},
    "SCR":  {"usable": False, "bodies": "placeholder"},

    # --- no Hindi listing at all --------------------------------------------
    "ECR":  {"usable": None,  "bodies": "0 releases on lang=1"},
    "SER":  {"usable": None,  "bodies": "0 releases on lang=1"},
}

USABLE_ZONES = [z for z, v in HINDI_COVERAGE.items() if v["usable"] is True]
UNUSABLE_ZONES = [z for z, v in HINDI_COVERAGE.items() if v["usable"] in (False, None)]
PDF_ONLY_ZONES = [z for z, v in HINDI_COVERAGE.items() if v["usable"] == "pdf_only"]
UNMEASURED_ZONES = []           # all 17 measured

# ------------------------------------------------------------ detail pages

# Content containers on CRIS detail pages, tried in order by crawler._parse_detail.
# Listed here for reference — the parsing itself is reused from the webapp's crawler
# rather than reimplemented, so this pipeline inherits its PDF and table handling.
DETAIL_CONTAINERS = ["ViEtDeVdIvId", "texts", "view_detail"]

# A detail page returning only this is a zone that has not translated its releases.
UNTRANSLATED_MARKER = "Add Hindi content here"

# crawler._parse_detail emits this when the release is a PDF with no inline text.
# A body consisting only of the title plus this marker carries no information.
PDF_MARKER = "[PDF attached:"
