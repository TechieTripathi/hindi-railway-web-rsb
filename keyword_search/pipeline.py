"""research/keyword_search/pipeline.py — find Hindi railway news on the open web by keyword.

Research-only, standalone, and writes nothing outside `research/output/`.

Why this exists
---------------
The web and RSB pipelines read a fixed list of pages, so they only ever see six
publishers and seventeen zone portals. This one starts from the user's keywords and
asks news search engines (keyword_search/config.py: Brave News, Bing News RSS) for
matching articles from any publisher. Everything after discovery is the web pipeline's
own code: `extract_body` for the text, `passes_topic` for relevance, the shared date
window. The output has the web pipeline's article schema, so AI generation, scoring
and the control panel handle it unchanged.

Each (keyword, engine) pair is searched and its results fetched before the next pair,
so progress is real progress. When two engines return the same story — same URL, or
same normalised Hindi title — the first one's copy is kept.

Usage
-----
    from keyword_search import pipeline as kp
    articles = kp.run_keyword_search(["वंदे भारत", "रेल हादसा"], days=3)
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from urllib.parse import urlparse

import requests

# One level down from research/ now, so climb twice.
_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RESEARCH_DIR not in sys.path:
    sys.path.insert(0, _RESEARCH_DIR)

from keyword_search import config as CFG          # noqa: E402
from keyword_search import engines as ENG         # noqa: E402
from shared.datewindow import Window              # noqa: E402
from shared.hindi_text import normalise_key, passes_topic   # noqa: E402
from web_search.pipeline import (                 # noqa: E402
    _title_from, extract_body, normalize_article_url,
)

DEFAULT_OUT_FILE = CFG.CRAWL_OUTPUT


def url_key(url):
    """One key per article page: scheme, `www.`, fragment, trailing slash and
    `utm_*` tracking parameters do not make a different article."""
    p = urlparse(url)
    host = p.netloc.lower().removeprefix("www.")
    query = "&".join(sorted(kv for kv in p.query.split("&")
                            if kv and not kv.lower().startswith("utm_")))
    return host + p.path.rstrip("/") + ("?" + query if query else "")


def skipped_domain(url):
    host = ENG.host_of(url)
    return any(host == d or host.endswith("." + d) for d in CFG.SKIP_DOMAINS)


def _truncated(title):
    return not title or title.endswith("...") or title.endswith("…")


def _window(days, date_from, date_to):
    """The date window, and the day count to pass to engines that filter by age."""
    if date_from or date_to:
        return Window(start=date_from, end=date_to), None
    if days is not None and int(days) == 0:
        return Window.open(), None
    days = CFG.DAYS_BACK if days is None else int(days)
    return Window.last_days(days), days


def run_keyword_search(keywords=None, engines=None, days=None, date_from=None,
                       date_to=None, max_per_keyword=None, keyword_mode="narrow",
                       relevance_filter=None, keep_undated=None, min_body=None,
                       delay=None, out_file=None, verbose=True):
    """Search each keyword on each engine, fetch the bodies, write research JSON.

    `keywords`  the search terms. Empty -> CFG.DEFAULT_KEYWORDS, and the relevance
                test is then the built-in railway test alone.
    `keyword_mode`  "narrow": keep railway news that also mentions a keyword;
                "only": keep anything that mentions a keyword. Same meaning as the
                keyword box on the other pipelines.
    `engines`   subset of CFG.ENGINES, in the order to ask them. None -> all.
    `days` / `date_from` / `date_to`  as run_hindi_crawl; days=0 means no limit.
    `max_per_keyword`  cap on articles KEPT per keyword across engines; None reads
                the LIMIT_PER_KEYWORD toggle.

    Returns the list of article dicts.
    """
    user_keywords = [k for k in (keywords or []) if k and k.strip()]
    queries = user_keywords or list(CFG.DEFAULT_KEYWORDS)
    engines = list(engines) if engines else list(CFG.ENGINES)
    unknown = [e for e in engines if e not in CFG.ENGINES]
    if unknown:
        raise ValueError("unknown engine(s) %s. Available: %s" % (unknown, CFG.ENGINES))
    if max_per_keyword is None:
        cap = CFG.MAX_PER_KEYWORD if CFG.LIMIT_PER_KEYWORD else float("inf")
    else:
        cap = max_per_keyword
    relevance_filter = CFG.RELEVANCE_FILTER if relevance_filter is None else relevance_filter
    keep_undated = CFG.KEEP_UNDATED if keep_undated is None else keep_undated
    min_body = CFG.MIN_BODY_CHARS if min_body is None else min_body
    delay = CFG.REQUEST_DELAY if delay is None else delay
    out_file = out_file or CFG.CRAWL_OUTPUT
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    window, search_days = _window(days, date_from, date_to)

    session = requests.Session()
    articles, stats = [], {}
    seen_urls, seen_keys = set(), set()
    per_engine = {e: 0 for e in engines}
    if verbose:
        print("date window: %s | engines: %s | %d keyword(s)%s | %s"
              % (window.describe(), ", ".join(CFG.ENGINE_LABELS[e] for e in engines),
                 len(queries), "" if user_keywords else " (defaults)",
                 "no per-keyword cap" if cap == float("inf") else "%d per keyword" % cap))
        print("keywords (%s): %s" % (keyword_mode if user_keywords else "railway test",
                                     ", ".join(queries)))

    def skip(stat, text, why=None):
        """Count under `stat`; print the specific reason, which may carry a value."""
        stats[stat] = stats.get(stat, 0) + 1
        if verbose:
            print("     skip (%s) %s" % (why or stat, text[:55]))

    for query in queries:
        kept = 0
        for engine in engines:
            if verbose:
                print("\n=== %s — %s ===" % (CFG.ENGINE_LABELS[engine], query))
            if kept >= cap:
                if verbose:
                    print("  per-keyword cap of %d reached; not searched" % cap)
                continue
            hits, err = ENG.search(engine, session, query, days=search_days)
            time.sleep(CFG.SEARCH_DELAY)
            if err:
                stats["engine errors"] = stats.get("engine errors", 0) + 1
                if verbose:
                    print("  WARNING %s: %s" % (CFG.ENGINE_LABELS[engine], err))
                continue
            if verbose:
                print("  %d result(s)" % len(hits))

            for hit in hits:
                if kept >= cap:
                    break
                url = normalize_article_url(hit["url"])
                if skipped_domain(url):
                    skip("aggregator or social site", "%s  %s" % (ENG.host_of(url), hit["title"]))
                    continue
                if url_key(url) in seen_urls:
                    skip("already have this URL", hit["title"])
                    continue
                seen_urls.add(url_key(url))
                if hit["title"] and normalise_key(hit["title"]) in seen_keys:
                    skip("already have this story", hit["title"])
                    continue
                # The engine's own date is enough to reject an old result without
                # fetching it; an undated or in-window hit is judged after the fetch.
                if hit["date"] and window.contains(hit["date"]) is False:
                    skip("outside the date window", hit["title"],
                         "dated %s, outside %s" % (hit["date"], window.describe()))
                    continue

                ext = extract_body(session, url)
                seen_urls.add(url_key(ext["real_url"]))
                body = ext["body"]
                if len(body) < min_body:
                    skip("body too short", "%s  %s" % (ENG.host_of(url), hit["title"]),
                         "body %d chars" % len(body))
                    time.sleep(delay)
                    continue
                title = hit["title"]
                if _truncated(title):
                    title = _title_from(session, ext["real_url"], body)
                if relevance_filter and not passes_topic(
                        user_keywords, keyword_mode, title, body[:600]):
                    skip("off-topic", title,
                         "not railway" if not user_keywords else
                         "no keyword match" if keyword_mode == "only"
                         else "not railway / no keyword match")
                    time.sleep(delay)
                    continue
                page_date = ext["page_date"] or hit["date"]
                verdict = window.contains(page_date)
                if verdict is None:
                    stats["undated"] = stats.get("undated", 0) + 1
                    if not keep_undated:
                        skip("no date on page", title)
                        time.sleep(delay)
                        continue
                elif not verdict:
                    skip("outside the date window", title,
                         "dated %s, outside %s" % (page_date, window.describe()))
                    time.sleep(delay)
                    continue
                key = normalise_key(title)
                if key and key in seen_keys:
                    skip("already have this story", title)
                    time.sleep(delay)
                    continue
                if key:
                    seen_keys.add(key)

                articles.append({
                    "title": title,
                    "link": ext["real_url"],
                    "date": page_date or "",
                    "source": hit["source"],
                    "listing_url": hit["listing_url"],
                    "body": body,
                    "search_keyword": query,
                    "search_engine": engine,
                    "extractor": ext["extractor"],
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "brave_related_articles": [],
                })
                kept += 1
                per_engine[engine] += 1
                if verbose:
                    print("     OK {:>6} chars  {}  {}".format(
                        len(body), ENG.host_of(ext["real_url"])[:22], title[:45]))
                time.sleep(delay)

    fd, tmp = tempfile.mkstemp(suffix=".json", dir=os.path.dirname(out_file))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(articles, f, indent=2, ensure_ascii=False)
        os.replace(tmp, out_file)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    if verbose:
        print("\nWrote {} articles -> {}".format(len(articles), out_file))
        print("  by engine: %s" % ", ".join("%s %d" % (CFG.ENGINE_LABELS[e], n)
                                            for e, n in per_engine.items()))
        print("  publishers: %d distinct" % len({a["source"] for a in articles}))
        for why, n in sorted(stats.items()):
            if why != "undated":
                print("  %d × %s" % (n, why))
        if stats.get("undated"):
            print("  %d article(s) carry NO date — %s"
                  % (stats["undated"], "kept" if keep_undated else "dropped"))
    return articles


if __name__ == "__main__":
    from shared.hindi_text import parse_keywords
    run_keyword_search(parse_keywords(",".join(sys.argv[1:])) or None)
