"""Research package for the Railway Agent.

Nothing here modifies the webapp: its modules are imported read-only, and where
behaviour must change for a research run it is rebound at runtime
(`shared/hindi_patches.py`).

Layout
------
    web_search/     Hindi news from six commercial publishers  (config, pipeline)
    rsb_search/     official press releases, Hindi edition     (config, pipeline)
    shared/         everything both pipelines need — Devanagari handling, the
                    webapp patches, the grounded metrics, key resolution, provenance
    ui.py           control panel driving BOTH pipelines, on :5001
    experiment.py   run either pipeline with config overrides, into a stamped run dir
    docs/           all documentation                          (start at docs/README.md)
    output/         scraped corpora, scores, logs, backups     (gitignored)

Entry points
------------
    experiment.run("baseline")                 # crawl + score, into a stamped run dir
    experiment.run("wider", max_per_source=8)  # same, with config overrides
    experiment.compare("baseline", "wider")    # side-by-side metrics

Or click through the stages one at a time, with full captured output:

    .venv/bin/python3 research/ui.py           # http://127.0.0.1:5001
"""

__all__ = ["web_search", "rsb_search", "shared", "experiment"]
