"""The web pipeline: railway news in Hindi from six commercial publishers.

Reads each publisher's own tag/topic page directly. The webapp's route for this
goes through Google News RSS, whose encrypted redirects resolved for 0 of 18 Hindi
articles, so this skips search entirely.

    config.py    where and how to crawl (URL patterns, body selectors, limits)
    pipeline.py  the crawling logic
"""

__all__ = ["config", "pipeline"]
