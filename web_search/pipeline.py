"""research/web_search/pipeline.py — direct crawler for the Hindi railway source set.

Research-only and standalone: imports no webapp module at all, and nothing here writes
to the project-root JSON files. HTTP settings come from `web_search/config.py`.

Why this exists
---------------
The webapp discovers articles through Google News RSS, which returns encrypted
redirect URLs. Resolving them currently fails for every one of the six Hindi
sources, so `web_agent` falls all the way back to the RSS snippet and yields
150-220 character bodies. This module skips Google entirely: it reads each
publisher's own tag/topic page, which is directly fetchable, and extracts the
article body with per-domain selectors.

Usage
-----
    from research.web_search import pipeline as hp
    articles = hp.run_hindi_crawl(max_per_source=5)
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

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

from web_search import config as CFG      # noqa: E402

# No webapp import: this module now owns its HTTP settings via config_web, so the
# web crawler is fully standalone.
HEADERS = dict(CFG.HEADERS)
TIMEOUT = CFG.TIMEOUT

OUTPUT_DIR = CFG.OUTPUT_DIR
SOURCE_CONFIG_FILE = CFG.SOURCE_CONFIG_FILE
DEFAULT_OUT_FILE = CFG.CRAWL_OUTPUT


# ---------------------------------------------------------------- relevance

# Delegated to research/shared/hindi_text.py so there is one implementation. That module
# uses a two-tier test: unambiguous terms (रेलवे, ट्रेन, आईआरसीटीसी …) admit on their
# own, ambiguous ones (कोच, यात्री, स्टेशन …) need corroboration.
#
# The single-tier substring match this replaces admitted a PIB *sports* release with
# zero railway content, because 'कोच' matched "मुख्य कोच गौतम गंभीर" (cricket coach).
from shared.datewindow import Window     # noqa: E402
from shared.hindi_text import normalise_key  # noqa: E402
from shared.hindi_text import (  # noqa: E402
    is_railway_relevant,
    passes_topic,
    STRONG_TERMS,
    WEAK_TERMS,
)

RAILWAY_TERMS = STRONG_TERMS + WEAK_TERMS  # kept for callers that introspect it


# ------------------------------------------------------- link discovery

# Crawl behaviour lives in web_search/config.py; re-exported here for callers that
# introspect this module.
LINK_PATTERNS = CFG.LINK_PATTERNS
SKIP_LINK = CFG.SKIP_LINK


def _domain_key(url):
    host = urlparse(url).netloc.lower().replace("www.", "")
    for key in LINK_PATTERNS:
        if key in host or host in key:
            return key
    return host


def discover_article_urls(session, tag_url, limit=25):
    """Return article URLs found on a publisher's tag/topic listing page."""
    key = _domain_key(tag_url)
    pattern = LINK_PATTERNS.get(key)
    r = session.get(tag_url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    host = urlparse(tag_url).netloc.replace("www.", "")

    # RSS/XML listings carry article URLs in <link>, not <a href>.
    raw_links = []
    if "<rss" in r.text[:400] or "<?xml" in r.text[:200]:
        for m in re.finditer(r"<link>\s*([^<\s]+)\s*</link>", r.text):
            raw_links.append(m.group(1))
    if not raw_links:
        soup = BeautifulSoup(r.text, "html.parser")
        raw_links = [a["href"] for a in soup.find_all("a", href=True)]

    urls, seen = [], set()
    for raw in raw_links:
        href = urljoin(tag_url, raw)
        if host not in urlparse(href).netloc.replace("www.", ""):
            continue
        if pattern and not re.search(pattern, href):
            continue
        if SKIP_LINK.search(href):
            continue
        href = href.split("#")[0]
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
        if len(urls) >= limit:
            break
    return urls


# ------------------------------------------------------- body extraction

def normalize_article_url(url):
    """Rewrite listing URLs to the page that actually carries the body.

    Rules live in config_web.URL_REWRITES — e.g. PIB's PressReleaseDetail.aspx is a
    JS shell with an empty content pane; the same PRID under PressReleasePage.aspx
    is server-rendered.
    """
    for host, old, new in CFG.URL_REWRITES:
        if host in url and old in url:
            return url.replace(old, new)
    return url


def _text(node):
    return node.get_text(separator="\n\n", strip=True) if node else ""


def _x_aajtak(soup):
    for sel in CFG.BODY_SELECTORS["aajtak.in"]:
        node = soup.find(class_=sel)
        if node:
            return _text(node)
    return ""


def _x_jansatta(soup):
    return _text(soup.find(class_=CFG.BODY_SELECTORS["jansatta.com"][0]))


def _x_nbt(soup):
    for sel in CFG.BODY_SELECTORS["navbharattimes.indiatimes.com"]:
        node = soup.find("div", class_=re.compile(sel))
        if node:
            for junk in node.find_all(class_=CFG.JUNK_SELECTORS):
                junk.decompose()
            return _text(node)
    return ""


def _x_amarujala(soup):
    sels = CFG.BODY_SELECTORS["amarujala.com"]
    node = (soup.find("div", class_=re.compile(sels[0]))
            or soup.find("div", class_=re.compile(sels[1]))
            or soup.find(attrs={"itemprop": "articleBody"}))
    body = _text(node)
    for pattern, repl in CFG.BOILERPLATE_PATTERNS:
        body = re.sub(pattern, repl, body)
    return body.strip()


def _x_livehindustan(soup):
    # .storyArticle is the real container; .stryCnt is the inner text block. The
    # itemprop/article-body/story-content selectors tried previously do not exist on
    # these pages, so extraction silently fell through to the generic <p> sweep.
    node = (soup.find("div", class_=re.compile("|".join(CFG.BODY_SELECTORS["livehindustan.com"])))
            or soup.find(attrs={"itemprop": "articleBody"}))
    if node:
        return _text(node)
    paras = [p.get_text(strip=True) for p in soup.find_all("p")
             if len(p.get_text(strip=True)) >= CFG.GENERIC_PARA_MIN]
    return "\n\n".join(paras)


def _x_pib(soup):
    """PIB release text. Use the <p> children of the content pane — the pane's
    raw text also carries the language switcher and 'other releases' chrome."""
    sels = CFG.BODY_SELECTORS["pib.gov.in"]
    node = (soup.find("div", class_=re.compile(sels[0]))
            or soup.find("div", class_=re.compile("|".join(sels[1:]))))
    if not node:
        return ""
    paras = [p.get_text(strip=True) for p in node.find_all("p")
             if len(p.get_text(strip=True)) >= CFG.PIB_PARA_MIN]
    return "\n\n".join(paras)


BODY_EXTRACTORS = {
    "aajtak.in": _x_aajtak,
    "jansatta.com": _x_jansatta,
    "navbharattimes.indiatimes.com": _x_nbt,
    "amarujala.com": _x_amarujala,
    "livehindustan.com": _x_livehindustan,
    "pib.gov.in": _x_pib,
}

_DATE_META = CFG.DATE_META


def _page_date(soup):
    for name in _DATE_META:
        meta = (soup.find("meta", attrs={"property": name})
                or soup.find("meta", attrs={"name": name})
                or soup.find("meta", attrs={"itemprop": name}))
        if meta and meta.get("content"):
            return meta["content"][:10]
    tag = soup.find("time")
    if tag and tag.get("datetime"):
        return tag["datetime"][:10]
    return ""


def extract_body(session, url):
    """Fetch one article and extract its body with the per-domain extractor.

    Falls back to a generic <p> sweep. Returns a dict; `body` is '' on failure.
    """
    url = normalize_article_url(url)
    result = {"body": "", "real_url": url, "page_date": "", "extractor": ""}
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        result["real_url"] = r.url
        r.raise_for_status()
        if "html" not in r.headers.get("content-type", ""):
            return result
        soup = BeautifulSoup(r.text, "html.parser")
        result["page_date"] = _page_date(soup)

        for tag in soup.find_all(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()

        key = _domain_key(r.url)
        fn = BODY_EXTRACTORS.get(key)
        if fn:
            body = fn(soup)
            if body and len(body) > 200:
                result["body"], result["extractor"] = body, key
                return result

        paras = [p.get_text(strip=True) for p in soup.find_all("p")
                 if len(p.get_text(strip=True)) >= CFG.GENERIC_PARA_MIN]
        body = "\n\n".join(paras)
        if len(body) > 200:
            result["body"], result["extractor"] = body, "generic"
    except Exception as e:
        result["error"] = str(e)[:120]
    return result


# ------------------------------------------------------------- crawl

def load_sources():
    cfg = json.loads(open(SOURCE_CONFIG_FILE, encoding="utf-8").read())
    return cfg["source_groups"][CFG.SOURCE_GROUP]["sources"]


def crawl_source(session, source, max_articles=None, relevance_filter=None,
                 keywords=None, keyword_mode="narrow",
                 min_body=None, verbose=True, delay=None, window=None,
                 keep_undated=None, stats=None, seen_keys=None):
    """Crawl one source's tag pages directly and return article dicts.

    Unset arguments fall back to web_search/config.py.

    `window` is a shared.datewindow.Window. It is applied AFTER the body fetch,
    because the publication date lives in the article page's meta tags, not on the
    listing — there is nothing to filter on until the page is fetched. Articles
    whose page carries no date at all are kept when `keep_undated`, and counted in
    `stats` either way so the run can report them.
    """
    # max_articles=None means UNCAPPED — take everything on the listing that passes
    # the relevance and date filters. The old default silently capped every run at 3.
    if max_articles is None:
        max_articles = CFG.MAX_PER_SOURCE if CFG.LIMIT_PER_SOURCE else float("inf")
    relevance_filter = CFG.RELEVANCE_FILTER if relevance_filter is None else relevance_filter
    min_body = CFG.MIN_BODY_CHARS if min_body is None else min_body
    delay = CFG.REQUEST_DELAY if delay is None else delay
    keep_undated = CFG.KEEP_UNDATED if keep_undated is None else keep_undated
    window = window or Window.open()
    if stats is None:
        stats = {}
    # Seen-key set for the whole run, not per listing page. PIB carries the same
    # release on both of its start_urls under different URLs, so a per-listing
    # `seen` set stored it twice — a verifier saw the same story twice.
    if seen_keys is None:
        seen_keys = set()
    out = []
    for tag_url in source.get("start_urls", []):
        if len(out) >= max_articles:
            break
        # PIB's listing is all-ministry, so railway releases are sparse — scan
        # far more candidates there and let the relevance filter do the work.
        if "pib.gov.in" in tag_url:
            cand_limit = CFG.PIB_CANDIDATE_LIMIT
        elif max_articles == float("inf"):
            cand_limit = CFG.LISTING_SCAN_LIMIT
        else:
            cand_limit = int(max_articles) * CFG.CANDIDATE_MULTIPLIER
        try:
            urls = discover_article_urls(session, tag_url, limit=cand_limit)
        except Exception as e:
            if verbose:
                print("  {}: listing failed: {}".format(source["name"], str(e)[:70]))
            continue
        if verbose:
            print("  {}: {} candidate URLs from {}".format(
                source["name"], len(urls), urlparse(tag_url).path[:40]))

        for url in urls:
            if len(out) >= max_articles:
                break
            ext = extract_body(session, url)
            body = ext["body"]
            if len(body) < min_body:
                if verbose:
                    print("     skip (body {} chars) {}".format(len(body), url[-55:]))
                time.sleep(delay)
                continue
            title = _title_from(session, url, body)
            # User keywords narrow (or replace) the built-in railway test; with none
            # supplied `passes_topic` is exactly `is_railway_relevant`.
            if relevance_filter and not passes_topic(
                    keywords, keyword_mode, title, body[:600]):
                if verbose:
                    why = "not railway" if not keywords else (
                        "no keyword match" if keyword_mode == "only"
                        else "not railway / no keyword match")
                    print("     skip ({}) {}".format(why, title[:55]))
                stats["off_topic"] = stats.get("off_topic", 0) + 1
                time.sleep(delay)
                continue

            page_date = ext["page_date"] or ""
            verdict = window.contains(page_date)
            if verdict is None:
                stats["undated"] = stats.get("undated", 0) + 1
                if not keep_undated:
                    if verbose:
                        print("     skip (no date on page) {}".format(title[:45]))
                    time.sleep(delay)
                    continue
            elif not verdict:
                stats["too_old"] = stats.get("too_old", 0) + 1
                if verbose:
                    print("     skip (dated {}, outside {}) {}".format(
                        page_date, window.describe(), title[:35]))
                time.sleep(delay)
                continue
            key = normalise_key(title)
            if key and key in seen_keys:
                stats["duplicate"] = stats.get("duplicate", 0) + 1
                if verbose:
                    print("     skip (already have this story) {}".format(title[:42]))
                time.sleep(delay)
                continue
            if key:
                seen_keys.add(key)

            out.append({
                "title": title,
                "link": ext["real_url"],
                "date": ext["page_date"] or "",
                "source": source["name"],
                # Which listing produced this article. Without it, a drifted selector
                # cannot be traced back to the page it came from.
                "listing_url": tag_url,
                "body": body,
                "search_keyword": "[direct] " + source["name"],
                "extractor": ext["extractor"],
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "brave_related_articles": [],
            })
            if verbose:
                print("     OK {:>6} chars  {}".format(len(body), title[:55]))
            time.sleep(delay)
    return out


_TITLE_CACHE = {}


def _title_from(session, url, body):
    """Best-effort title: og:title, then <h1>, then first body line."""
    if url in _TITLE_CACHE:
        return _TITLE_CACHE[url]
    title = ""
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        meta = soup.find("meta", attrs={"property": "og:title"})
        if meta and meta.get("content"):
            title = meta["content"].strip()
        if not title and soup.find("h1"):
            title = soup.find("h1").get_text(strip=True)
    except Exception:
        pass
    if not title:
        title = body.split("\n")[0][:120]
    _TITLE_CACHE[url] = title
    return title


def run_hindi_crawl(sources=None, max_per_source=None, relevance_filter=None,
                    out_file=None, verbose=True, days=None, date_from=None,
                    date_to=None, source_names=None, keep_undated=None,
                    keywords=None, keyword_mode="narrow"):
    """Crawl every Hindi source directly and write research JSON.

    Date window, in order of precedence:
        date_from / date_to   an explicit range
        days                  the last N days, inclusive of today
        (neither)             CFG.DAYS_BACK

    Pass days=0 for no date filter at all.
    `source_names` selects a subset of publishers by name; None means all.

    `keywords` is a list of user terms an article must match to be kept.
    `keyword_mode` is "narrow" (railway AND keyword) or "only" (keyword alone).
    An empty list leaves the built-in railway relevance test as the sole filter.

    Returns the list of article dicts. Never touches project-root files.
    """
    sources = sources if sources is not None else load_sources()
    if source_names:
        wanted = {n.strip().lower() for n in source_names}
        sources = [s for s in sources if s["name"].lower() in wanted]
        if not sources:
            raise ValueError("no source matched %s. Available: %s"
                             % (sorted(wanted), [s["name"] for s in load_sources()]))
    # None propagates to crawl_source, which reads the LIMIT_PER_SOURCE toggle.
    out_file = out_file or CFG.CRAWL_OUTPUT
    os.makedirs(os.path.dirname(out_file), exist_ok=True)

    if date_from or date_to:
        window = Window(start=date_from, end=date_to)
    elif days is not None and int(days) == 0:
        window = Window.open()
    else:
        window = Window.last_days(CFG.DAYS_BACK if days is None else days)

    session = requests.Session()
    articles, stats, seen_keys = [], {}, set()
    if verbose:
        cap = ("%d per publisher" % max_per_source if max_per_source
               else ("%d per publisher (config cap)" % CFG.MAX_PER_SOURCE
                     if CFG.LIMIT_PER_SOURCE else "no per-publisher cap"))
        print("date window: %s | sources: %d | %s"
              % (window.describe(), len(sources), cap))
        if keywords:
            print("keywords (%s): %s" % (keyword_mode, ", ".join(keywords)))
    for src in sources:
        if verbose:
            print("\n=== {} ===".format(src["name"]))
        articles.extend(crawl_source(
            session, src, max_articles=max_per_source,
            relevance_filter=relevance_filter, verbose=verbose,
            keywords=keywords, keyword_mode=keyword_mode,
            window=window, keep_undated=keep_undated, stats=stats,
            seen_keys=seen_keys))

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)
    if verbose:
        print("\nWrote {} articles -> {}".format(len(articles), out_file))
        if stats.get("off_topic"):
            print("  %d dropped as off-topic%s"
                  % (stats["off_topic"],
                     " (keywords: %s)" % ", ".join(keywords) if keywords else ""))
        if stats.get("too_old"):
            print("  %d dropped as outside %s" % (stats["too_old"], window.describe()))
        if stats.get("duplicate"):
            print("  %d duplicate story/stories skipped (same title from another "
                  "listing or publisher)" % stats["duplicate"])
        if stats.get("undated"):
            keep = CFG.KEEP_UNDATED if keep_undated is None else keep_undated
            print("  %d article(s) carry NO date on the page — %s"
                  % (stats["undated"], "kept" if keep else "dropped"))
    return articles


if __name__ == "__main__":
    run_hindi_crawl()
