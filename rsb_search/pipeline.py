"""research/rsb_search/pipeline.py — Hindi-first crawler for the official RSB zone portals.

Research-only. Does NOT modify any webapp module: `crawler` is imported read-only for
its proven listing/detail parsers, and nothing here writes to project-root JSON or to
the persistent `seen_articles.json`.

Why this exists
---------------
`config.ZONES` points every zone at `view_section.jsp?lang=0`, which is the **English**
edition. Measured on Central Railway: `lang=0` yields 18 releases with 0 Hindi titles,
while `lang=1` yields the same 18 with 17 Hindi titles and 100%-Devanagari bodies. The
webapp therefore never sees the Hindi press releases that exist.

Language coverage is not uniform across zones, so every article records what was
actually found rather than assuming:

    CR  lang=1 -> Hindi titles AND ~100% Devanagari bodies
    NR  lang=1 -> Hindi titles but ENGLISH bodies

`hindi_ratio` is stored per article so downstream scoring can filter on real content
rather than on the zone's advertised language.

Usage
-----
    from research.rsb_search import pipeline as rp
    articles = rp.run_rsb_crawl(zone_codes=['CR'], per_zone=3)
"""

import json
import os
import re
import sys
import time
import warnings
from datetime import datetime

import requests

# One level down from research/ now, so climb twice.
_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_RESEARCH_DIR)
# Bootstrap order matters: _PROJECT_ROOT must precede this file's own directory on
# sys.path. Run as a script, sys.path[0] is this package dir, which contains a
# config.py — and the webapp's crawler.py/engine.py import `config` by bare name.
# Inserting _PROJECT_ROOT at 0 keeps them pointing at the webapp's config.py.
for _p in (_PROJECT_ROOT, _RESEARCH_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Read-only imports — we reuse the webapp's parsers, never mutate them.
# The ONLY remaining webapp dependency, and a deliberate one: crawler's CRIS parsers
# already handle the zone portals' table markup and PDF-attachment releases. Forking
# them would mean maintaining two copies of that logic.
import crawler                                    # noqa: E402

from rsb_search import config as CFG              # noqa: E402
from shared import hindi_text                     # noqa: E402

# crawler._fetch uses verify=False; silence the resulting InsecureRequestWarning.
warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")

OUTPUT_DIR = CFG.OUTPUT_DIR
DEFAULT_OUT_FILE = CFG.CRAWL_OUTPUT

_DEVA = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")


def hindi_zone_url(url):
    """Rewrite a config.ZONES listing URL to its Hindi edition.

    Retained for callers holding a webapp-style (lang=0) URL. config_rsb.ZONES is
    already lang=1, so the normal path does not need this.
    """
    return re.sub(r"lang=\d", "lang=%d" % CFG.HINDI_LANG_PARAM, url)


def hindi_zones(zone_codes=None):
    """The Hindi (lang=1) zone listings from config_rsb, optionally filtered."""
    zones = CFG.ZONES if not zone_codes else [z for z in CFG.ZONES
                                              if z["c"] in zone_codes]
    return [dict(z, u=hindi_zone_url(z["u"])) for z in zones]


def script_ratio(text):
    """Fraction of cased/scripted characters that are Devanagari (0.0-1.0)."""
    if not text:
        return 0.0
    d = len(_DEVA.findall(text))
    a = len(_LATIN.findall(text))
    return d / (d + a) if (d + a) else 0.0


def crawl_zone(session, zone, per_zone=None, date_from=None, date_to=None,
               min_body=None, verbose=True, delay=None):
    """Crawl one zone's Hindi listing and return article dicts.

    Reuses crawler._parse_listing / _parse_detail, which already handle the CRIS
    table markup, PDF-only releases, and the newest-first sort.
    """
    code, name, url = zone["c"], zone["n"], zone["u"]
    base = crawler._get_base(url)
    per_zone = CFG.PER_ZONE if per_zone is None else per_zone
    min_body = CFG.MIN_BODY_CHARS if min_body is None else min_body
    if delay is None:
        delay = CFG.SLEEP_DETAIL if CFG.USE_CRAWLER_DELAYS else 1.0
    out = []
    try:
        items = crawler._parse_listing(crawler._fetch(session, url), base)
    except Exception as e:
        if verbose:
            print("  %-5s listing failed: %s" % (code, str(e)[:70]))
        return out

    df = crawler._parse_date_sortkey(date_from) if date_from else None
    dt = crawler._parse_date_sortkey(date_to) if date_to else None
    if df or dt:
        keep = []
        for it in items:
            k = crawler._parse_date_sortkey(it["date"])
            if k == (0, 0, 0):
                continue
            if df and k < df:
                continue
            if dt and k > dt:
                continue
            keep.append(it)
        items = keep

    if verbose:
        print("  %-5s %d releases available" % (code, len(items)))

    for it in items[:per_zone]:
        try:
            body = crawler._parse_detail(crawler._fetch(session, it["detail_url"]))
        except Exception as e:
            if verbose:
                print("     skip (fetch failed) %s" % str(e)[:50])
            continue
        if len(body) < min_body:
            # Distinguish the three failure modes rather than lumping them together:
            # an untranslated template, a PDF-only release, and a genuinely thin page
            # need different responses.
            if CFG.UNTRANSLATED_MARKER in body:
                reason = "untranslated template"
            elif CFG.PDF_MARKER in body:
                reason = "PDF-only (no inline text)"
            else:
                reason = "%d chars" % len(body)
            if verbose:
                print("     skip (%s) %s" % (reason, it["title"][:40]))
            time.sleep(delay)
            continue
        t_ratio, b_ratio = script_ratio(it["title"]), script_ratio(body)
        out.append({
            "zone_code": code,
            "zone_name": name,
            "title": it["title"],
            "link": it["detail_url"],
            "listing_url": url,          # the zone listing this release came off
            "date": it["date"],
            "body": body,
            "source": "%s (RSB Hindi)" % name,
            "lang_edition": "hi",
            "title_hindi_ratio": round(t_ratio, 2),
            "body_hindi_ratio": round(b_ratio, 2),
            "is_hindi_body": b_ratio >= CFG.HINDI_BODY_THRESHOLD,
            "fetched_at": datetime.now().isoformat(),
            "brave_related_articles": [],
        })
        if verbose:
            print("     OK %5d chars  title_hi=%3.0f%% body_hi=%3.0f%%  %s"
                  % (len(body), t_ratio * 100, b_ratio * 100, it["title"][:40]))
        time.sleep(delay)
    return out


def research_dedup(articles):
    """Dedup within this run only — never touches the persistent seen_articles.json.

    Uses the Devanagari-aware key from hindi_text; the webapp's dedup._normalise
    deletes Devanagari outright and would collapse unrelated Hindi titles.
    """
    seen, unique, dups = set(), [], 0
    for art in articles:
        key = "%s||%s" % (art.get("zone_code", ""),
                          hindi_text.normalise_key(art.get("title", "")))
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        unique.append(art)
    return unique, dups


def run_rsb_crawl(zone_codes=None, per_zone=None, date_from=None, date_to=None,
                  hindi_only=None, out_file=None, verbose=True):
    """Crawl the Hindi edition of the RSB zone portals and write research JSON.

    hindi_only=True keeps only articles whose BODY is majority Devanagari, which
    excludes zones like NR that publish Hindi headlines over English text.
    """
    zone_codes = CFG.DEFAULT_ZONE_CODES if zone_codes is None else zone_codes
    per_zone = CFG.PER_ZONE if per_zone is None else per_zone
    hindi_only = CFG.HINDI_ONLY if hindi_only is None else hindi_only
    zones = hindi_zones(zone_codes)
    out_file = out_file or CFG.CRAWL_OUTPUT
    os.makedirs(os.path.dirname(out_file), exist_ok=True)

    session = requests.Session()
    articles = []
    for z in zones:
        if verbose:
            print("\n=== %s — %s ===" % (z["c"], z["n"]))
        articles.extend(crawl_zone(session, z, per_zone=per_zone,
                                   date_from=date_from, date_to=date_to,
                                   verbose=verbose))
        time.sleep(CFG.SLEEP_LISTING)

    articles, dups = research_dedup(articles)
    if hindi_only:
        before = len(articles)
        articles = [a for a in articles if a["is_hindi_body"]]
        if verbose:
            print("\nhindi_only: kept %d of %d (dropped English-body zones)"
                  % (len(articles), before))

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)
    if verbose:
        hi = sum(1 for a in articles if a["is_hindi_body"])
        print("\nWrote %d articles (%d Hindi-body, %d dups removed) -> %s"
              % (len(articles), hi, dups, out_file))
    return articles


if __name__ == "__main__":
    run_rsb_crawl()
