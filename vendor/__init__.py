"""Copies of the webapp code research depends on, so research/ runs as a standalone repo.

Snapshot of the railway-agent webapp working tree at commit 46ad508 (with its
uncommitted edits to engine.py and config.py), taken 2026-10-12. Only what research
calls is kept; everything else in those files is left out.

    engine.py     AI generation — verbatim apart from the config import, so line
                  references into it (docs/METRICS.md) match the webapp's engine.py
    crawler.py    CRIS listing/detail parsers, used by rsb_search/pipeline.py
    dedup.py      Latin-only title normaliser   } the baselines shared/hindi_patches.py
    web_agent.py  Latin-only tokenizer/grouping } replaces and verify_patches() measures
    config.py     the settings those modules read, at the webapp's values

These no longer track the webapp. A fix made there (a prompt change in engine.py,
a parser fix in crawler.py) has to be copied here by hand to reach research runs.

Imports are package-relative (`from .config import ...`), so the vendored `config`
can never be confused with web_search/config.py or rsb_search/config.py.
"""

__all__ = ["engine", "crawler", "dedup", "web_agent", "config"]
