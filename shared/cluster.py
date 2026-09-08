"""shared/cluster.py — group crawled articles so one report can cover one story.

Why this exists
---------------
Generation is 1:1 today — one crawled article in, one story out. That is why every
article reports a single source and `validated_sources` is always 1. If several
publishers cover the same event, the pipeline writes several near-identical reports
and a human verifies the same facts repeatedly.

Clustering makes "how many reports does a run produce" a property of what was
published, not of a hardcoded per-source cap.

Modes
-----
    off      one article -> one report. Current behaviour, kept as the baseline.
    story    articles about the SAME EVENT are one report.
             Single-linkage on Devanagari-safe title-word Jaccard.
    topic    articles sharing a DOMINANT TOPIC are one report.
             Coarse: "Operations" can hold several unrelated events.

Topics are derived from the SOURCE text, not from the model — they have to be,
because clustering happens *before* generation, and the model's `topics` field does
not exist yet. `grounding.TOPIC_LEXICON` already carries bilingual evidence terms
for exactly this, so it is reused rather than duplicated.

The story threshold is not a guess — it was measured, twice.

Raw title words put the one genuinely same-event pair in the corpus (Diwali booking,
carried by Navbharat Times and AajTak) at 0.238, uncomfortably close to unrelated
pairs at 0.071. The near-misses were all inflection: टिकट/टिकटों, बुक/बुकिंग,
कंफर्म/कन्फर्म. Applying `light_stem` to the title words lifts the real pair to
**0.300** while the highest unrelated pair sits at **0.125** — so 0.20 falls in the
middle of a clean gap, and the shared stems are the right ones:

    टिकट · ट्रेन · दिवाल · बुकिंग · शुर · स्पेशल

Anything at 1.00 is the same article twice, which crawl-time dedup now removes
before clustering ever sees it.
"""

from __future__ import annotations

import re

from shared.grounding import TOPIC_LEXICON, TOPIC_VOCABULARY
from shared.hindi_text import (
    light_stem, normalize_hi, term_in, title_words, tokenize_hi,
)

#: Stemmed-title-word overlap at or above which two articles are the same event.
#: See the module docstring for the measurement behind 0.20.
STORY_JACCARD = 0.20

MODES = ("off", "story", "topic")

#: How a merged cluster body is assembled, and therefore how it is read back.
#: `cluster_article` writes with these and `split_merged_body` parses with them,
#: so the format is defined exactly once. A view that hardcoded its own regex
#: would start silently dropping sources the moment either literal changed.
PART_SEPARATOR = "\n\n---\n\n"
PART_HEADER = "[%s — %s]"                    # source, em dash, date

#: The header as a reader. Both halves are non-greedy and the line must END at
#: the bracket, so a "[...]" occurring inside article prose is not mistaken for
#: an attribution header.
_PART_HEADER_RE = re.compile(r"^\[(?P<source>.+?) — (?P<date>.+?)\]$")


def split_merged_body(body):
    """Split a merged cluster body back into its per-source parts.

    Returns ``[{"source": str|None, "date": str|None, "text": str}]``, one entry
    per source, in the order `cluster_article` wrote them (longest body first).

    A body with no attribution headers yields ONE entry with ``source=None``.
    That covers every un-clustered article and every single-member cluster --
    `cluster_article` returns those with the lead body untouched -- so a caller
    never needs to special-case the ordinary one-source article.

    Never raises and never drops text: a part whose first line is not a header
    keeps its full text, header line included, under ``source=None``.
    """
    if not body:
        return [{"source": None, "date": None, "text": ""}]
    parts = []
    for chunk in body.split(PART_SEPARATOR):
        head, sep, rest = chunk.partition("\n")
        m = _PART_HEADER_RE.match(head.strip()) if sep else None
        if m:
            parts.append({"source": m.group("source"), "date": m.group("date"),
                          "text": rest})
        else:
            parts.append({"source": None, "date": None, "text": chunk})
    return parts


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def story_key(title: str) -> set:
    """Comparable title stems. Stemming matters here — see the module docstring."""
    return {light_stem(w) for w in title_words(title or "")}


def derive_topics(article: dict, limit: int = 3) -> list[str]:
    """Topics evidenced by the article's own text, strongest first.

    Ranked by how many distinct lexicon terms fired, so a body that mentions three
    infrastructure words outranks one that mentions a single passenger word.
    """
    blob = normalize_hi("%s %s" % (article.get("title") or "",
                                   article.get("body") or "")).lower()
    tokens = {w.lower() for w in tokenize_hi(
        "%s %s" % (article.get("title") or "", article.get("body") or ""),
        min_hi=2, min_en=3)}
    scored = []
    for topic in TOPIC_VOCABULARY:
        hits = [t for t in TOPIC_LEXICON.get(topic, []) if term_in(t, tokens, blob)]
        if hits:
            scored.append((len(hits), topic))
    scored.sort(key=lambda p: (-p[0], p[1]))
    return [t for _, t in scored[:limit]]


def build_clusters(articles: list, mode: str = "story",
                   threshold: float = STORY_JACCARD) -> list[dict]:
    """Group `articles` into clusters. Returns one dict per cluster.

    Every article lands in exactly one cluster, so the member counts always sum to
    len(articles) — a cluster set that silently dropped articles would be worse than
    no clustering at all.
    """
    if mode not in MODES:
        raise ValueError("mode must be one of %s, got %r" % (list(MODES), mode))

    topics = [derive_topics(a) for a in articles]

    if mode == "off":
        groups = [[i] for i in range(len(articles))]
    elif mode == "topic":
        buckets: dict[str, list] = {}
        for i, tl in enumerate(topics):
            buckets.setdefault(tl[0] if tl else "Unclassified", []).append(i)
        groups = [buckets[k] for k in sorted(buckets)]
    else:
        words = [story_key(a.get("title") or "") for a in articles]
        parent = list(range(len(articles)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(articles)):
            for j in range(i + 1, len(articles)):
                if jaccard(words[i], words[j]) >= threshold:
                    parent[find(i)] = find(j)          # single linkage
        merged: dict[int, list] = {}
        for i in range(len(articles)):
            merged.setdefault(find(i), []).append(i)
        groups = [merged[k] for k in sorted(merged, key=lambda k: min(merged[k]))]

    out = []
    for members in groups:
        # The longest body leads: it is the most complete account of the event and
        # gives the generator the most to work from.
        members = sorted(members, key=lambda i: -len(articles[i].get("body") or ""))
        lead = articles[members[0]]
        seen, all_topics = set(), []
        for i in members:
            for t in topics[i]:
                if t not in seen:
                    seen.add(t)
                    all_topics.append(t)
        out.append({
            "members": members,
            "size": len(members),
            "lead": members[0],
            "title": lead.get("title") or "",
            "sources": sorted({articles[i].get("source") or "" for i in members}),
            "topics": all_topics,
            "chars": sum(len(articles[i].get("body") or "") for i in members),
        })
    return sorted(out, key=lambda c: (-c["size"], -c["chars"]))


def cluster_article(articles: list, cluster: dict, max_chars: int = None) -> dict:
    """One synthetic article representing a cluster, ready for generation.

    The bodies are concatenated with a source attribution header each, so the model
    sees which outlet said what and the grounding check can still compare the story
    against the union of the sources.

    `max_chars` matters more than it looks. `engine.generate_news` truncates its
    input at `config.MAX_INPUT_CHARS` (6000) — so a 6-source cluster whose combined
    body is 20k characters would reach the model as source 1 and half of source 2,
    while the output still claimed six sources. Passing the same budget here splits
    it **evenly across members** instead, so every source in a cluster is actually
    represented. Each member's share is cut at a paragraph break where possible.

    Single-member clusters pass through essentially unchanged, so `mode="off"` is
    byte-comparable with the un-clustered pipeline.
    """
    members = cluster["members"]
    lead = dict(articles[members[0]])
    if len(members) == 1:
        lead["cluster_size"] = 1
        lead["cluster_sources"] = [lead.get("source") or ""]
        lead["cluster_links"] = [lead.get("link") or ""]
        lead["derived_topics"] = cluster["topics"]
        return lead

    share = None
    if max_chars:
        # Leave room for the "[source — date]" headers and the --- separators.
        overhead = len(members) * 60
        share = max(400, (max_chars - overhead) // len(members))

    parts, links, sources, trimmed = [], [], [], 0
    for i in members:
        a = articles[i]
        src = a.get("source") or "unknown"
        body = (a.get("body") or "").strip()
        if share and len(body) > share:
            cut = body.rfind("\n\n", 0, share)
            body = body[:cut if cut > share // 2 else share].rstrip() + " […]"
            trimmed += 1
        parts.append("%s\n%s" % (PART_HEADER % (src, a.get("date") or "no date"), body))
        links.append(a.get("link") or "")
        sources.append(src)
    lead["body"] = PART_SEPARATOR.join(parts)
    lead["cluster_trimmed"] = trimmed
    lead["cluster_size"] = len(members)
    lead["cluster_sources"] = sources
    lead["cluster_links"] = links
    lead["cluster_titles"] = [articles[i].get("title") or "" for i in members]
    lead["derived_topics"] = cluster["topics"]
    return lead


def summarise(articles: list, clusters: list) -> str:
    multi = [c for c in clusters if c["size"] > 1]
    return ("%d article(s) -> %d report(s); %d cluster(s) hold more than one source"
            % (len(articles), len(clusters), len(multi)))
