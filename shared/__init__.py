"""Code both pipelines depend on.

Devanagari text handling, the runtime patches for the webapp, the grounded metrics,
and the plumbing (API key resolution, run provenance). Nothing here knows which
pipeline is calling it.

    hindi_text.py     Devanagari normalisation, tokenising, relevance
    hindi_patches.py  rebinds the webapp's Latin-only normalizers at runtime
    grounding.py      deterministic, source-checked scores
    score_report.py   applies grounding to an engine output file
    config_env.py     where the ANTHROPIC_API_KEY comes from
    provenance.py     git hash / config snapshot for a run
"""

__all__ = ["hindi_text", "hindi_patches", "grounding", "score_report",
           "config_env", "provenance"]
