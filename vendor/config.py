# config.py — settings for the vendored webapp modules.
# Only the values engine/crawler/web_agent read, at the webapp's values. Unlike the
# webapp's config.py this loads no .env and creates no data/ or logs/ directories:
# the API key comes from the process environment, and shared/config_env.py binds
# research/.env onto engine before a run.

import os

_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUTPUT_DIR = os.path.join(_RESEARCH_DIR, "output")

# --- Anthropic API ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-haiku-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MAX_INPUT_CHARS = 6000
MAX_OUTPUT_TOKENS = 8000   # 3000 cut off Hindi answers mid-JSON (Hindi costs ~2.8x the tokens)
AI_CALL_DELAY = 2

# --- Crawler ---
TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

# --- engine.run_generate defaults ---
# Research always passes explicit input/output paths and rebinds engine.STATUS_FILE,
# so these only matter if engine is called bare. They point inside research/output/.
CRAWL_OUTPUT = os.path.join(_OUTPUT_DIR, "crawl_output.json")
AGENCY_OUTPUT = os.path.join(_OUTPUT_DIR, "agency_output.json")
STATUS_FILE = os.path.join(_OUTPUT_DIR, "pipeline_status.json")

# --- Similarity Grouping ---
SIMILARITY_THRESHOLD = 0.70  # Jaccard threshold for grouping (0.5=loose, 0.7=moderate, 0.85=strict)
SIMILARITY_MIN_WORDS = 3     # Minimum significant words to compare
