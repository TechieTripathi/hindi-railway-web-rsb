"""The keyword-search pipeline: railway news in Hindi from ANY publisher, by keyword.

The other two pipelines read a fixed list of pages. This one takes the user's keywords
(वंदे भारत, रेल हादसा …), asks open-web news search engines for matching articles, and
then fetches and filters the bodies exactly as the web pipeline does.

    config.py    engines, endpoints, default keywords, limits
    engines.py   one function per search engine, all returning the same hit shape
    pipeline.py  search -> fetch body -> filter -> write
"""

__all__ = ["config", "engines", "pipeline"]
