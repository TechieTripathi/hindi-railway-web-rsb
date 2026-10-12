# crawler.py — Railway Press Release Scraper: the CRIS page parsers only.
# Vendored from the webapp; see vendor/__init__.py. run_crawl and its zone list,
# status file and persistent dedup are left out — rsb_search/pipeline.py owns those.

import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .config import USER_AGENT, TIMEOUT

HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}


def _get_base(url):
    p = urlparse(url)
    return "{}://{}/".format(p.scheme, p.netloc)


def _fetch(session, url, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
            r.raise_for_status()
            return r.text
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def _parse_date_sortkey(date_str):
    """Convert date like '18-05-2026' or '29-06-2012' to sortable tuple (year,month,day)."""
    try:
        parts = date_str.strip().split("-")
        if len(parts) == 3:
            return (int(parts[2]), int(parts[1]), int(parts[0]))
    except (ValueError, IndexError):
        pass
    return (0, 0, 0)


def _parse_listing(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    for link in soup.find_all("a", href=re.compile(r"view_detail\.jsp")):
        href = link.get("href", "").replace("&amp;", "&")
        if href in seen:
            continue
        seen.add(href)
        tr = link.find_parent("tr")
        if not tr:
            continue
        cells = tr.find_all("td")
        if len(cells) < 3:
            continue
        items.append({
            "date": cells[1].get_text(strip=True),
            "title": link.get_text(strip=True),
            "detail_url": urljoin(base_url, href),
        })
    # Sort newest first — some zones list oldest first
    items.sort(key=lambda x: _parse_date_sortkey(x["date"]), reverse=True)
    return items


def _parse_detail(html):
    """Extract content from CRIS detail page. Handles tables and PDF links."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", id="ViEtDeVdIvId")
    if not content:
        content = soup.find("div", class_="texts")
    if not content:
        # Try broader selectors
        content = soup.find("div", class_="view_detail")
        if not content:
            content = soup.find("td", class_="view_detail")
    if not content:
        return ""

    parts = []

    # Check for PDF links first
    pdf_links = content.find_all("a", href=re.compile(r'\.pdf', re.I))
    if pdf_links:
        for pl in pdf_links:
            href = pl.get("href", "")
            text = pl.get_text(strip=True) or "PDF document"
            parts.append("[PDF attached: {}]".format(text))

    # Process tables into readable text
    for table in content.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        table_text = []
        for row in rows:
            cells = row.find_all(["td", "th"])
            cell_texts = [c.get_text(strip=True) for c in cells]
            # Skip empty rows
            if any(t for t in cell_texts):
                table_text.append(" | ".join(cell_texts))
        if table_text:
            parts.append("\n".join(table_text))
        # Replace table with marker so get_text doesn't duplicate
        table.decompose()

    # Get remaining text
    text = content.get_text(separator="\n", strip=True)
    text = text.replace("\xad", "").replace("\u00a0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)

    if text.strip():
        parts.insert(0, text.strip())

    body = "\n\n".join(parts)

    # If body is almost entirely a PDF reference with no real text
    if not body or (len(body) < 50 and pdf_links):
        pdf_note = "[This press release contains a PDF attachment. "
        for pl in pdf_links:
            href = pl.get("href", "")
            if href and not href.startswith("http"):
                href = "https://nr.indianrailways.gov.in/" + href.lstrip("/")
            pdf_note += "PDF: {} ".format(href)
        pdf_note += "]"
        body = pdf_note

    return body[:15000]
