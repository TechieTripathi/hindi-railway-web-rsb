# web_agent.py — the webapp's title dedup key, tokenizer and similarity grouping.
# Vendored from the webapp; see vendor/__init__.py. Kept as the baseline that
# shared/hindi_patches.py replaces and verify_patches() measures against. The
# Google News / Brave search path (run_web_search) is left out; web_search/pipeline.py
# crawls publishers directly instead.

import hashlib
import re
from datetime import datetime
from urllib.parse import urlparse

from .config import SIMILARITY_THRESHOLD, SIMILARITY_MIN_WORDS


def _make_dedup_key(title):
    t = re.sub(r'[^a-z0-9\s]', '', title.lower().strip())
    t = re.sub(r'\s+', ' ', t)
    return hashlib.md5(t.encode('utf-8')).hexdigest()


def _title_words(title):
    """Extract significant words from a title for similarity comparison."""
    # Remove source suffix like "- The Economic Times"
    title = re.sub(r'\s*[-–—|]\s*[A-Z][\w\s,.]+$', '', title)
    words = set(re.findall(r'[a-z]{4,}', title.lower()))  # 4+ chars only
    # Remove common stop words AND railway-generic terms
    stops = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
             'had', 'her', 'was', 'one', 'our', 'out', 'has', 'from', 'that',
             'this', 'with', 'will', 'what', 'how', 'its', 'than', 'also',
             'been', 'have', 'more', 'after', 'into', 'over', 'says', 'said',
             'amid', 'among', 'about', 'being', 'some', 'when', 'here',
             # Railway-generic (appear in almost every article)
             'news', 'india', 'indian', 'railway', 'railways', 'train', 'trains',
             'check', 'report', 'reports', 'update', 'latest', 'today',
             'crore', 'lakh', 'million', 'year', 'quarter', 'fiscal',
             'share', 'shares', 'stock', 'price', 'market', 'company',
             'limited', 'international', 'corporation',
             'new', 'set', 'gets', 'give', 'take', 'make', 'know', 'need',
             }
    return words - stops


def _group_similar_articles(articles, threshold=None):
    """Group articles with similar titles. Returns articles with similar_articles field.
    Also merges brave_related_articles into the grouping for unified source validation.
    
    Uses SIMILARITY_THRESHOLD from config (default 0.70 = moderate strictness).
    """
    if threshold is None:
        threshold = SIMILARITY_THRESHOLD
    min_words = SIMILARITY_MIN_WORDS
    n = len(articles)
    if n <= 1:
        if n == 1:
            art = articles[0]
            art.setdefault('similar_articles', [])
            art.setdefault('similar_count', 1)
            art.setdefault('validated_sources', [art.get('source', '')])
            # Merge Brave results into similar articles
            _merge_brave_into_article(art)
        return articles

    groups = []  # list of lists
    used = [False] * n

    for i in range(n):
        if used[i]:
            continue
        group = [i]
        words_i = _title_words(articles[i].get('title', ''))
        if len(words_i) < min_words:
            groups.append(group)
            continue
        for j in range(i + 1, n):
            if used[j]:
                continue
            words_j = _title_words(articles[j].get('title', ''))
            if len(words_j) < min_words:
                continue
            overlap = len(words_i & words_j)
            union = len(words_i | words_j)
            if union > 0 and overlap / union >= threshold:
                group.append(j)
                used[j] = True
        used[i] = True
        groups.append(group)

    result = []
    for group in groups:
        primary = articles[group[0]]
        if len(group) > 1:
            similar = []
            # Pick the one with most body content as primary
            best_idx = group[0]
            best_len = len(primary.get('body', ''))
            for idx in group[1:]:
                art = articles[idx]
                bl = len(art.get('body', ''))
                if bl > best_len:
                    best_len = bl
                    best_idx = idx
                similar.append({
                    'title': art.get('title', ''),
                    'source': art.get('source', ''),
                    'date': art.get('date', ''),
                    'link': art.get('link', ''),
                })
            primary = articles[best_idx]
            # Add the original primary to similar list if it wasn't the best
            if best_idx != group[0]:
                similar.append({
                    'title': articles[group[0]].get('title', ''),
                    'source': articles[group[0]].get('source', ''),
                    'date': articles[group[0]].get('date', ''),
                    'link': articles[group[0]].get('link', ''),
                })
            primary['similar_articles'] = similar
            primary['similar_count'] = len(group)
            sources = [primary.get('source', '')] + [s.get('source', '') for s in similar]
            primary['validated_sources'] = list(set(s for s in sources if s))
        else:
            primary['similar_articles'] = []
            primary['similar_count'] = 1
            primary['validated_sources'] = [primary.get('source', '')]

        # Merge Brave results into this article's grouping
        _merge_brave_into_article(primary)

        result.append(primary)

    return result


def _merge_brave_into_article(article):
    """Merge brave_related_articles into similar_articles and validated_sources.
    Only merges articles genuinely about the SAME specific topic.
    Limits to top 5 most relevant to avoid noise."""
    brave_arts = article.get('brave_related_articles', [])
    if not brave_arts:
        return

    existing_urls = set()
    existing_urls.add(article.get('link', ''))
    for sa in article.get('similar_articles', []):
        existing_urls.add(sa.get('link', ''))

    primary_title = article.get('title', '') or article.get('ai_headline', '')
    primary_words = _title_words(primary_title)

    # Skip domains that are never news articles
    skip_domains = [
        'wikipedia.org', 'britannica.com', 'quora.com', 'reddit.com',
        'bharatiyajanataparty', 'bjp.org', 'inc.in', 'congress.org',
        'gov.in/ppp', 'linkedin.com', 'facebook.com', 'twitter.com',
    ]

    candidates = []
    for ba in brave_arts:
        url = ba.get('url', '')
        if url in existing_urls:
            continue
        url_lower = url.lower()
        if any(skip in url_lower for skip in skip_domains):
            continue

        ba_title = ba.get('title', '')
        ba_words = _title_words(ba_title)
        overlap = primary_words & ba_words
        union = primary_words | ba_words

        # Require at least 3 word overlap OR 40%+ Jaccard for short titles
        jaccard = len(overlap) / max(len(union), 1)
        if len(overlap) < 2:
            continue
        if len(overlap) < 3 and jaccard < 0.25:
            continue

        # Skip old articles (>30 days)
        ba_date = ba.get('page_age', '')
        if ba_date:
            try:
                ba_dt = datetime.strptime(ba_date[:10], "%Y-%m-%d")
                if (datetime.now() - ba_dt).days > 30:
                    continue
            except (ValueError, TypeError):
                pass

        source_name = ba.get('source_name', '')
        if not source_name:
            try:
                source_name = urlparse(url).netloc.replace('www.', '')
            except Exception:
                source_name = ''

        sim_pct = round(jaccard * 100)
        candidates.append({
            'title': ba_title,
            'source': source_name,
            'date': ba_date[:10] if ba_date else '',
            'link': url,
            'similarity': sim_pct,
            '_sort_score': len(overlap),
        })

    # Sort by overlap count (descending), take top 5
    candidates.sort(key=lambda x: x['_sort_score'], reverse=True)
    added = 0
    for c in candidates[:5]:
        if c['link'] in existing_urls:
            continue
        existing_urls.add(c['link'])
        del c['_sort_score']
        article['similar_articles'].append(c)
        added += 1
        source_name = c['source']
        if source_name and source_name not in article.get('validated_sources', []):
            article['validated_sources'].append(source_name)

    if added > 0:
        article['similar_count'] = 1 + len(article['similar_articles'])
