"""The RSB pipeline: official press releases from Indian Railways zone portals.

Same listings the webapp's crawler.py reads, but the Hindi edition (`lang=1`).
config.ZONES in the webapp points at `lang=0`, the English edition.

    config.py    zone URLs, per-zone limits, measured Hindi coverage
    pipeline.py  the crawling logic
"""

__all__ = ["config", "pipeline"]
