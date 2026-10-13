"""research/keyword_search/engines.py — one function per search engine.

Every engine returns `(hits, error)`. `hits` is a list of dicts of one shape:

    {"url", "title", "source", "date", "snippet", "engine", "query", "listing_url"}

`date` is ISO (YYYY-MM-DD) or "". `error` is a short message or None. Engines never
raise: one engine failing — no key, HTTP 429, a changed feed — must not stop the run
from using the others.
"""

import html
import re
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, urlparse

from keyword_search import config as CFG
from shared import config_env


def host_of(url):
    """`www.amarujala.com` -> `amarujala.com`."""
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def brave_freshness(days):
    """Brave's `freshness` value for a window of `days`, or None for no limit."""
    if not days:
        return None
    if days <= 1:
        return "pd"
    if days <= 7:
        return "pw"
    if days <= 31:
        return "pm"
    return "py"


def search_brave_news(session, query, days=None, count=None):
    """Brave News API. Needs BRAVE_API_KEY (shell export or research/.env)."""
    key = config_env.get("BRAVE_API_KEY")
    if not key:
        return [], "no BRAVE_API_KEY in the environment or research/.env"
    params = dict(CFG.BRAVE_PARAMS, q=query, count=count or CFG.RESULTS_PER_QUERY)
    fresh = brave_freshness(days)
    if fresh:
        params["freshness"] = fresh
    try:
        r = session.get(CFG.BRAVE_NEWS_URL, params=params, timeout=CFG.TIMEOUT,
                        headers={"Accept": "application/json",
                                 "X-Subscription-Token": key})
    except Exception as e:
        return [], "request failed: %s" % str(e)[:100]
    if r.status_code != 200:
        return [], "HTTP %d: %s" % (r.status_code, r.text[:120].replace("\n", " "))
    try:
        results = r.json().get("results") or []
    except ValueError:
        return [], "response was not JSON"
    return parse_brave(results, query), None


def parse_brave(results, query):
    listing = CFG.BRAVE_PUBLIC_URL.format(query=quote_plus(query))
    hits = []
    for a in results:
        url = a.get("url") or ""
        if not url:
            continue
        meta = a.get("meta_url") or {}
        hits.append({
            "url": url,
            "title": html.unescape(a.get("title") or "").strip(),
            "source": (meta.get("hostname") or host_of(url)).removeprefix("www."),
            "date": (a.get("page_age") or "")[:10],
            "snippet": html.unescape(re.sub(r"<[^>]+>", "", a.get("description") or "")),
            "engine": "brave",
            "query": query,
            "listing_url": listing,
        })
    return hits


def search_bing_rss(session, query, count=None):
    """Bing News RSS. Free, no key; about 10 results per query."""
    params = dict(CFG.BING_PARAMS, q=query)
    try:
        r = session.get(CFG.BING_RSS_URL, params=params, timeout=CFG.TIMEOUT,
                        headers=CFG.HEADERS)
    except Exception as e:
        return [], "request failed: %s" % str(e)[:100]
    if r.status_code != 200:
        return [], "HTTP %d" % r.status_code
    hits = parse_bing_rss(r.text, query, listing_url=r.url)
    if not hits and "<rss" not in r.text[:500]:
        return [], "response was not an RSS feed"
    return hits[:count or CFG.RESULTS_PER_QUERY], None


def bing_real_url(link):
    """The publisher URL inside Bing's `apiclick.aspx?…&url=<encoded>` link.

    Unlike Google News, Bing passes the destination in clear, so no request is
    needed to resolve it. Links that are not click-throughs come back unchanged.
    """
    link = html.unescape(link or "").strip()
    target = parse_qs(urlparse(link).query).get("url")
    return target[0] if target else link


def _tag(item, name):
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), item, re.S)
    if not m:
        return ""
    text = m.group(1).strip()
    if text.startswith("<![CDATA["):
        text = text[9:-3]
    return html.unescape(text).strip()


def _rss_date(value):
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def parse_bing_rss(xml, query, listing_url=""):
    hits = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        url = bing_real_url(_tag(item, "link"))
        if not url.startswith("http"):
            continue
        hits.append({
            "url": url,
            "title": _tag(item, "title"),
            "source": _tag(item, "News:Source") or host_of(url),
            "date": _rss_date(_tag(item, "pubDate")),
            "snippet": _tag(item, "description"),
            "engine": "bing",
            "query": query,
            "listing_url": listing_url,
        })
    return hits


def search(engine, session, query, days=None, count=None):
    """Dispatch to one engine by name."""
    if engine == "brave":
        return search_brave_news(session, query, days=days, count=count)
    if engine == "bing":
        return search_bing_rss(session, query, count=count)
    return [], "unknown engine %r (known: %s)" % (engine, ", ".join(CFG.ENGINES))
