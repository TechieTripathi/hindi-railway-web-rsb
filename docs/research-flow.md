# Pipeline Flow

Two independent pipelines. They share nothing except the AI generation step and the
scoring step. Read whichever half you care about.

```
  WEB NEWS                              RSB (OFFICIAL)
  6 Hindi news publishers               Railway zone press-release portals
        │                                     │
        ▼                                     ▼
  ┌───────────────┐                     ┌───────────────┐
  │ 1. CRAWL      │  web_search/pipeline.py  │ 1. CRAWL      │  rsb_search/pipeline.py
  └───────┬───────┘                     └───────┬───────┘
          │  web_crawl_output_research.json     │  rsb_crawl_output_research.json
          └──────────────┬──────────────────────┘
                         ▼
                  ┌───────────────┐
                  │ 2. AI GENERATE│  engine.py  (shared, unmodified)
                  └───────┬───────┘
                          │  *_agency_output_research.json
                          ▼
                  ┌───────────────┐
                  │ 3. SCORE      │  grounding.py
                  └───────┬───────┘
                          │  *_scored.json
                          ▼
                    notebooks / metrics
```

---

# Pipeline A — Web News

**Goal:** get today's Hindi railway news from six publishers.

### Stage 1 — Crawl

```
web_search/config.py            ← how to crawl (patterns, selectors, limits)
hindi_railway_sources.json ← where to crawl (6 publisher URLs)
        │
        ▼
  publisher tag page          e.g. aajtak.in/topic/indian-railways
        │
        ▼  discover_article_urls()      keep links matching LINK_PATTERNS
  list of article URLs
        │
        ▼  extract_body()               per-domain CSS selector
  article text                          (falls back to a generic <p> sweep)
        │
        ▼  is_railway_relevant()        drop off-topic articles
  kept articles
        │
        ▼
  research/output/web_crawl_output_research.json
```

**Why no search engine.** The webapp asks Google News, which returns encrypted redirect
URLs, then asks Brave to decode them. That failed 18 times out of 18 on these sources, so
every article fell back to a 150–220 character RSS snippet. Going straight to the
publisher's own page skips both.

**What comes out:** `title`, `body`, `link`, `source`, `date`, `extractor`.

**The fragile part:** the per-domain CSS selectors. A publisher redesign breaks them
*silently* — you get a short body, not an error. The `extractor` field on each article
says which path produced it; a source showing `generic` means its selector has drifted.

---

# Pipeline B — RSB (official press releases)

**Goal:** get Hindi press releases from the railway zone portals.

### Stage 1 — Crawl

```
rsb_search/config.py            ← zones, limits, measured Hindi coverage
        │
        ▼  hindi_zone_url()             rewrite lang=0 → lang=1
  zone listing page             cr.indianrailways.gov.in/view_section.jsp?lang=1
        │
        ▼  crawler._parse_listing()     reused from the webapp
  list of releases (newest first)
        │
        ▼  crawler._parse_detail()      reused — handles tables and PDF-only releases
  release text
        │
        ▼  script_ratio()               measure how much is actually Devanagari
  articles tagged with body_hindi_ratio
        │
        ▼
  research/output/rsb_crawl_output_research.json
```

**The key discovery.** `config.ZONES` points every zone at `lang=0`, which is the
**English** edition. `lang=1` is Hindi and it exists:

```
CR  lang=0 →  18 releases,  0 Hindi titles
CR  lang=1 →  18 releases, 17 Hindi titles, ~100% Devanagari bodies
```

**Coverage is uneven — check before trusting a zone:**

| Zones | Bodies | Usable |
|---|---|---|
| **CR, WR, NCR, NER** | real Hindi | ✅ |
| NR | Hindi titles, mostly **English** bodies | ⚠️ partial |
| NWR | title + `[PDF attached: <same title>]` | ❌ PDF-only |
| ER, NFR, SR, SECR, SWR, WCR, MTP, ECoR, SCR | `"Add Hindi content here"` | ❌ untranslated |
| ECR, SER | no `lang=1` listing | ❌ |

**All 17 zones measured. Only 4 publish usable Hindi** (~24%). An empty Hindi page does
not mean an idle zone — WCR lists 248 releases, SR 223, SECR 111, all in English behind
an untranslated Hindi shell.

Every article carries a measured `body_hindi_ratio`, so you filter on fact rather than on
the zone's advertised language. `run_rsb_crawl(hindi_only=True)` keeps only Hindi bodies.

All 17 are now measured; `config_rsb.USABLE_ZONES` is the shortlist.

---

# Stage 2 — AI generation (shared)

Both pipelines hand off to the **same** `engine.run_generate()` the webapp uses. Same
prompt, same model, same retry logic — only the file paths differ, so research results
transfer to the product.

```
  *_crawl_output_research.json
        │
        ▼  one Claude call per article
  story in the source language
  + translation into the other language
  + 6 social posts (Twitter / Facebook / general × 2 languages)
  + impact level, topics, fact_score
        │
        ▼  _compute_plagiarism()   ← calculated locally, not by the model
  *_agency_output_research.json
```

The output file is a **superset** of the crawl file: the 9 scraped fields plus 20 AI
fields. `body` is what the publisher wrote; `ai_news_story` is what Claude wrote from it.

Two things to know:

- It **resumes by title.** Articles already generated are skipped. Delete the agency file
  to force a clean regeneration after changing the prompt.
- Hindi costs **~2.8× the tokens** of equivalent English text, so `MAX_INPUT_CHARS` and
  `MAX_OUTPUT_TOKENS` are much tighter in practice for Hindi.

---

# Stage 3 — Grounded scoring

`engine.py` asks the model to grade its own story. Over 25 articles that produced only
**5 distinct `fact_score` values, 88% of them in 92–95** — including **92** for a sports
press release with no railway content. `shared/grounding.py` adds checks against the source.

```
  *_agency_output_research.json
        │
        ▼  score_report.rescore_file()
  coverage_score        share of the source's figures the story carried  (16 distinct values)
  number_precision      hallucination guard — near-saturated, used as a gate
  grounding_ceiling     hard cap: off-topic source, thin body, degenerate story
  topics_verified       closed vocabulary + evidence in the source
  impact_rule_level     impact derived from source triggers, vs the LLM's label
        │
        ▼
  *_scored.json
```

Runs in seconds, no API calls, byte-identical across runs.

---

# Running it

Both notebooks default every flag to `False`, so opening one never hits the network.

```python
# research/notebooks/web_news_pipeline_research.ipynb
RUN_DIRECT_CRAWL = True    # Stage 1
RUN_AI           = True    # Stage 2

# research/notebooks/rsb_pipeline_research.ipynb
RUN_CRAWL   = True         # Stage 1
HINDI_ONLY  = True         # drop English-body zones like NR
RUN_AI      = True         # Stage 2
```

Or from the shell:

```bash
.venv/bin/python3 research/web_search/pipeline.py    # web crawl
.venv/bin/python3 research/rsb_search/pipeline.py      # RSB crawl
.venv/bin/python3 research/shared/score_report.py      # Stage 3 over both
.venv/bin/python3 research/test_hindi_text.py   # regression tests
```

Both notebooks call `apply_hindi_patches()` before anything Hindi runs. Without it the
webapp's own functions delete Devanagari during deduplication and collapse unrelated
Hindi articles onto one key.

---

# What each file does

| File | Role |
|---|---|
| `web_search/config.py` / `rsb_search/config.py` | How to crawl — patterns, selectors, limits, paths |
| `hindi_railway_sources.json` | Where to crawl — source URLs and keywords |
| `web_search/pipeline.py` | Web crawler |
| `rsb_search/pipeline.py` | RSB crawler |
| `shared/hindi_text.py` | Devanagari-safe text handling (normalise, tokenise, stem, relevance) |
| `shared/hindi_patches.py` | Binds the above onto the webapp at runtime |
| `shared/grounding.py` | Deterministic source-checked scores |
| `shared/score_report.py` | Applies Stage 3 and prints the comparison table |
| `test_hindi_text.py` | 10 regression tests |

**Nothing here modifies the webapp.** Its modules are imported read-only; where behaviour
must change, it is rebound at runtime.

---

# Known limits

- **Discovery is fixed** to the configured sources. A story broken by a seventh outlet is
  invisible — that needs search. Multi-source confidence is therefore always 1.
- **Scoring catches unsupported numbers, not unsupported meaning.** A fluent paraphrase
  that misstates causation with no checkable figure still passes.
- **`coverage_score` is confounded by source shape.** One RSB article scores 3% because
  its source is a 12-train timetable — summarising that down is correct editing.
- **The webapp is still unfixed.** Every correction here lives only in `research/`.
