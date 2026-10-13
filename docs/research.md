# Research Notebooks & Modules

Research code for the Railway Agent, in its own standalone repository
(`TechieTripathi/hindi-railway-web-rsb`), separate from the Flask webapp
(`aimonitors25/railway-agent`) it grew out of. See [SETUP.md](SETUP.md).

**Nothing here needs the webapp checked out.** The webapp code research builds on —
`engine.py`, the CRIS parsers from `crawler.py`, and the Latin-only normalizers from
`dedup.py` / `web_agent.py` — is copied into `vendor/` (see `vendor/__init__.py` for the
snapshot it came from). Where that behaviour must change for a research run, it is rebound
at runtime (see `shared/hindi_patches.py`) rather than edited, so `vendor/` stays a faithful
baseline. All output goes to `research/output/`.

Every metric the pipeline attaches to an article — formula, who computes it, and what it
is evidence of — is documented in **[METRICS.md](METRICS.md)**.

```bash
.venv/bin/python3 -m pip install -r requirements-research.txt
```

First time in this repo? See **[SETUP.md](SETUP.md)** for the whole sequence.

> Use `.venv/bin/python3 -m pip`, not `.venv/bin/pip3` — the venv's console scripts have a
> stale shebang from a drive remount and will not execute.

---

## Layout

Split three ways, so the pipeline-specific and the shared parts are visible from the
file tree:

```
research/
├── web_search/          Hindi news from six commercial publishers
│   ├── config.py        where and how to crawl (patterns, selectors, limits)
│   └── pipeline.py      the crawling logic
├── rsb_search/          official press releases, Hindi edition
│   ├── config.py        zone URLs, per-zone limits, measured Hindi coverage
│   └── pipeline.py      the crawling logic
├── keyword_search/      open-web news search by keyword, any publisher
│   ├── config.py        engines, endpoints, default keywords, limits
│   ├── engines.py       Brave News API, Bing News RSS — one hit shape
│   └── pipeline.py      search → fetch body → filter → write
├── shared/              everything the pipelines share
│   ├── hindi_text.py    Devanagari normalising, tokenising, relevance
│   ├── hindi_patches.py rebinds vendor/'s Latin-only normalizers at runtime
│   ├── grounding.py     deterministic, source-checked scores
│   ├── score_report.py  applies grounding to an engine output file
│   ├── config_env.py    where the API keys come from
│   └── provenance.py    git hash / config snapshot for a run
├── vendor/              snapshot of the webapp code research builds on
│   ├── engine.py        AI generation (Claude), verbatim but for one import
│   ├── crawler.py       CRIS listing/detail parsers
│   ├── dedup.py, web_agent.py   the webapp's Latin-only normalizers — the baseline
│   └── config.py        the settings those modules read
├── notebooks/           the four research notebooks
├── ui.py, ui_templates/ control panel driving all three pipelines, on :5001
├── experiment.py        run any pipeline with overrides, into a stamped run dir
├── docs/                all documentation
├── output/              scraped corpora, scores, logs, backups   (gitignored)
└── hindi_railway_sources.json    both source groups, one file
```

`ui.py` and `experiment.py` sit at the top because they import from all three packages
— they drive either pipeline, so they belong to neither.

**Imports are absolute and package-qualified** (`from shared.hindi_text import …`,
`from web_search import config as CFG`), not relative, so every module still runs
directly as a script:

```bash
.venv/bin/python3 shared/score_report.py
.venv/bin/python3 shared/hindi_patches.py
.venv/bin/python3 test_hindi_text.py
```

> `web_search/`, `rsb_search/` and `vendor/` each contain a `config.py`. The vendored
> modules import theirs package-relatively (`from .config import …`), so a bare
> `import config` never has to be disambiguated by `sys.path` order — the trap that the
> old two-repo layout had to work around.

---

## Modules

### `web_search/pipeline.py` — direct crawler for the Hindi web sources

Replaces Stage 1 of the web pipeline for the six Hindi publishers.

**Why it exists.** The webapp discovers articles via Google News RSS, which returns
encrypted redirect URLs (`news.google.com/rss/articles/CBMie0FVX3lx…`) that must be
resolved before the body can be scraped. Measured across the six Hindi sources, that
resolution **failed 18 times out of 18** — every article fell through the whole fallback
ladder (Brave → source RSS → site search → webcache) to the RSS snippet, yielding
150–220 character bodies.

**What it does instead.** Skips search entirely and reads each publisher's own tag/topic
page, which is directly fetchable. No Google, no Brave, no API key.

```
publisher tag page → article links (LINK_PATTERNS) → fetch → per-domain body selector
```

Key pieces:

| Function | Role |
|---|---|
| `discover_article_urls` | Article links off a listing page; handles both `<a href>` and RSS `<link>` |
| `normalize_article_url` | PIB's `PressReleaseDetail.aspx` is a JS shell — rewrites the PRID to the server-rendered `PressReleasePage.aspx` |
| `extract_body` | Dispatches to a per-domain extractor, falls back to a generic `<p>` sweep |
| `run_hindi_crawl` | Full crawl → `research/output/web_crawl_output_research.json` |

Measured improvement (chars of body text):

| Source | Via Google News | Direct | Extractor |
|---|---|---|---|
| AajTak | 219 | **2318** | `story-with-main-sec` |
| Jansatta | 265 | **2918–10857** | `wp-block-post-content` |
| Navbharat Times | 255 | **1843–2482** | `story-card-container` |
| Amar Ujala | 1143 | **1252–5924** | `article-desc` |
| Live Hindustan | 2251 | **3098–4273** | `storyArticle` |
| PIB Hindi | 68 | **3059–4017** | content pane `<p>` children |

**Fragility to watch.** The per-domain CSS selectors break silently when a publisher
redesigns — you get short bodies, not an error. Each article records which path produced
it in `extractor`; any source showing `generic` means its dedicated selector has drifted.
This has already happened once: `_x_livehindustan` originally named three classes that do
not exist on the page, and the generic fallback masked it.

**Relevance filtering** is delegated to `hindi_text.is_railway_relevant` — the tag pages
for AajTak/NBT/Jansatta are roughly half off-topic (Nepal floods, jokes, stock prices).

```python
from research.web_search import pipeline as hp
articles = hp.run_hindi_crawl(max_per_source=3)
```

---

### `shared/hindi_patches.py` — bind Devanagari-aware preprocessing onto the webapp

Runtime rebinding of three webapp functions that silently destroy Hindi. **No file on disk
is edited** — this is the same technique the notebooks already use for
`web_agent.WEB_CRAWL_OUTPUT` and `web_agent.load_web_settings`.

**What it fixes.** `dedup._normalise` and `web_agent._make_dedup_key` both run
`re.sub(r'[^a-z0-9\s]', '', text)`, which keeps only `a-z0-9` — every Devanagari codepoint
is deleted. `web_agent._title_words` extracts with `[a-z]{4,}`, Latin only.

Measured on 16 real Hindi titles:

| | Before | After |
|---|---|---|
| Distinct dedup keys (16 titles) | **12** | **16** |
| Titles yielding <3 comparable words | **14** | **0** |
| Two nukta encodings of हावड़ा hash equal | no | **yes** |

Five unrelated Hindi titles were collapsing onto one key — all normalised to a single
space — so four of five would be silently discarded as duplicates. Because
`seen_articles.json` persists, that poisoning is permanent.

| Function | Purpose |
|---|---|
| `apply_hindi_patches()` | Rebinds the three functions. Idempotent; stores originals |
| `revert_hindi_patches()` | Restores the webapp's own implementations |
| `verify_patches()` | Proves the rebind worked **and reached internal callers** |

The rebind surface:

```
dedup._normalise          -> hindi_text.normalise_key
web_agent._make_dedup_key -> hindi_text.normalise_key + md5
web_agent._title_words    -> hindi_text.title_words
```

**Why the rebind reaches internal callers** — `_group_similar_articles` and
`_dedup_by_source_overlap` look `_title_words` up as a module global at call time, and
`dedup._make_key` does the same for `_normalise`. So `deduplicate_articles` picks up the
fix even though `crawler.py` imported it by direct reference. `verify_patches()` asserts
this by feeding two near-identical Hindi titles through `_group_similar_articles` and
checking `similar_count == 2` — that assertion is the one that actually matters.

**English is untouched.** `hindi_text.title_words` delegates pure-Latin titles to a
verbatim copy of the webapp's own logic, so existing English hashes stay valid. Verified
identical across all English titles in `web_crawl_output.json`.

```python
from hindi_patches import apply_hindi_patches, verify_patches
apply_hindi_patches()
verify_patches()
```

---

### `shared/hindi_text.py` — Devanagari-safe text utilities

Pure functions, no I/O. Backed by `indic-nlp-library`, falling back to `unicodedata` NFC
if it is unavailable (`BACKEND` reports which is active).

`normalize_hi`, `tokenize_hi`, `title_words`, `normalise_key`, `is_railway_relevant`,
`HINDI_STOPWORDS`, `STRONG_TERMS` / `WEAK_TERMS`.

Regex alone is not sufficient: the corpus contains the same word at two different
codepoint sequences — **हावड़ा** (Howrah) appears both precomposed (U+095C) and as base +
combining nukta (U+093C), plus 24 ZWJ characters. Extending the character class makes the
tokens survive but they still hash differently; only normalisation collapses them.

`is_railway_relevant` is **two-tier**: unambiguous terms (`रेलवे`, `ट्रेन`, `आईआरसीटीसी`)
admit on their own; ambiguous ones (`कोच`, `यात्री`, `स्टेशन`) need corroboration. The
single-tier version it replaced admitted a PIB *sports* release because `कोच` matched
"मुख्य कोच गौतम गंभीर" (cricket coach).

---

### `shared/config_env.py` — where the API key comes from

`vendor/config.py` reads `ANTHROPIC_API_KEY` from the process environment once, at
import, and loads no `.env` file. This module supplies the key from `research/.env`
(dependency-free — no `python-dotenv`) and binds it onto `engine` before a run.

Precedence, highest first:

| | source |
|---|---|
| 1 | a genuine shell export — `ANTHROPIC_API_KEY=… jupyter lab` |
| 2 | `research/.env` |

An empty value in `research/.env` changes nothing, so a placeholder-only file is safe.
Nothing else writes these names into `os.environ`, so a value already there is a real
export and wins.

The key is rebound as a module global on `engine`, which works because `generate_news`
reads it as a global *inside* the function. Same technique as `shared/hindi_patches.py`;
no file is edited. Values are never printed — only the length and which source won.

```bash
.venv/bin/python3 shared/config_env.py    # which keys are set, and from where
```

```python
from shared import config_env
from vendor import engine
if not config_env.apply_to_engine(engine):
    print('Generation would return empty results.')
```

> `.env` is the first entry in this repo's own `.gitignore`. Verify with
> `git check-ignore -v .env` before committing anything.

---

### `rsb_search/pipeline.py` — Hindi-first crawler for the official RSB zone portals

`config.ZONES` points every zone at `view_section.jsp?lang=0` — the **English** edition.
`lang=1` is Hindi. Measured on Central Railway: `lang=0` gives 18 releases with 0 Hindi
titles; `lang=1` gives the same 18 with 17 Hindi titles and ~100% Devanagari bodies.

Reuses `crawler._parse_listing` / `_parse_detail` (vendored in `vendor/crawler.py`), dedups within the run only,
and records `body_hindi_ratio` per article rather than trusting the zone's advertised
language. Hindi coverage is very uneven — see
`hindi_railway_sources.json → hindi_coverage_measured_2026_08_31`:

- **CR, WR** — full Hindi, titles and bodies. Usable.
- **NR** — Hindi titles over mostly English bodies. Partial; filter on `body_hindi_ratio`.
- **ECoR, SCR** — Hindi pages return the literal placeholder `"Add Hindi content here"`.

`run_rsb_crawl(hindi_only=True)` drops English-body articles.

---

### `keyword_search/pipeline.py` — open-web news search by keyword

The other two pipelines read a fixed list of pages. This one takes the user's keywords,
asks news search engines for matching articles from **any** publisher, and hands the
results to the web pipeline's own code: `extract_body` for the text, `passes_topic` for
relevance (so the keyword box means the same thing here), the shared date window. The
output uses the web pipeline's article schema plus `search_keyword` and `search_engine`,
so generation, scoring and the control panel need no special handling.

```
keyword × engine → result URLs → drop aggregators / seen URLs / seen titles / too old
                 → extract_body → length, topic, date checks → article
```

Engines, measured 2026-10-12 with the query `रेलवे`:

| engine | endpoint | key | measured |
|---|---|---|---|
| Brave News API | `api.search.brave.com/res/v1/news/search` (`country=IN`, `search_lang=hi`, `freshness`) | `BRAVE_API_KEY`, billed per search | 10 Hindi results, most under an hour old, direct publisher URLs |
| Bing News RSS | `bing.com/news/search?format=rss&setlang=hi&cc=IN` | none | ~10 results; real URL is the `url=` parameter of Bing's click-through link, decoded locally. `extract_body` got full bodies for 7 of 8 |
| GDELT DOC 2.0 | `api.gdeltproject.org/api/v2/doc/doc` | none | **not used** — refused every request here ("one every 5 seconds") |
| Google News RSS | — | none | **not used** — encrypted redirects, 0 of 18 Hindi URLs resolved |

Without a Brave key the run uses Bing alone and logs that Brave was skipped. Results
from `msn.com` and similar aggregators are dropped before fetching (JavaScript shells
with no extractable body). Most open-web publishers have no dedicated selector, so the
generic `<p>` sweep is the normal path here, and the control panel does not flag it as
selector drift.

```python
from keyword_search import pipeline as kp
kp.run_keyword_search(["वंदे भारत", "रेल हादसा"], days=3)     # empty list -> DEFAULT_KEYWORDS
```

A one-keyword trial run (`वंदे भारत`, 3 days, both engines) kept 14 articles from 12
publishers, all with bodies over 300 characters; it dropped 6 off-topic results, 4
aggregator links and 3 out-of-window stories.

---

### `shared/grounding.py` + `shared/score_report.py` — deterministic quality scores

`fact_score`, `impact_level` and `topics` are all produced by the same Claude call that
writes the story; nothing verifies them. Over 25 articles `fact_score` has only **5
distinct values with 88% in 92–95**, and it scored a misfiled sports release **92**.

`shared/grounding.py` adds source-checked fields, in the style of `engine._compute_plagiarism`
(every score ships with the raw counts that produced it):

| Field | Meaning |
|---|---|
| `coverage_score` | Share of the source's figures the story carried over — **16** distinct values vs `fact_score`'s 5 |
| `number_precision` | Hallucination guard. Near-saturated (92–100), so used as a **gate**, not a score |
| `grounding_ceiling` / `gate_failures` | Hard caps: off-topic source, thin body, degenerate story |
| `topics_verified` / `topics_unsupported` / `topics_off_vocabulary` | Closed-vocabulary + source-evidence check |
| `impact_rule_level` / `impact_agrees` | Impact derived from source triggers, compared with the LLM label |

**Why coverage and not precision** — an earlier reading suggested ~12% of story numbers
were unsupported. That was a tokenisation artefact: normalising Devanagari digits,
splitting composite date/time literals and comparing as `int` drops it to **0.8%**. The
story writes `रात 8 बजे` where the body writes a `20:00` form.

Matching is **token-boundary aware**, not substring — `मृत` (dead) must not fire inside
अ**मृत** भारत, and `आग` (fire) must not fire inside `आगे`.

```bash
.venv/bin/python3 shared/score_report.py
```

Full formulas, gate thresholds and current measured values: **[METRICS.md](METRICS.md)**.

Results on the current corpus: coverage 16 distinct values; the PIB sports article gets
`grounding_ceiling 0` against its `fact_score` of 92; 26 of 91 assigned topics have no
source evidence; `Low` impact is used 10 times where the LLM used it 0 times; two runs are
byte-identical.

---

## `ui.py` — click-through control panel

```bash
.venv/bin/python3 ui.py                 # http://127.0.0.1:5001
```

Port 5001, so it runs alongside the webapp's `app.py` on 5000. The home page offers one
card per source type; each opens a page of **four guided steps**, only one of them open
at a time:

```
1 Find articles  ->  2 Choose articles  ->  3 Write stories (+ automatic quality check)  ->  4 Review results
```

**Written for someone who did not build this.** `ui._flow()` decides which step is open
from the files on disk and run history: nothing found → Find; found but nothing written →
Choose (discard inline); partly written → Write; otherwise Review. Finished steps collapse
to one line with **Change**. Write is labelled **Paid step**, states the number of paid AI
requests for the chosen way of combining articles, and asks for confirmation. Writing and
the free quality check run as **one job** (`_step_write_and_check`); a failed check never
fails the paid write. Review shows the three quality numbers in plain words — *Facts kept*
(coverage), *Copied text* (plagiarism, lower is better), *AI's self-rating* (unverified) —
and flags stories that **need a closer look** (a failed gate, or a number in the red).

**Look.** The design follows the Blinkit Campaign Console: its colour tokens (light and
dark), a dark collapsible sidebar (Ctrl+B; a drawer on phones), a 56px header with
breadcrumbs and a status pill, status badges that always pair an icon with a word,
callouts, toasts with Undo, in-page confirm dialogs and a bottom bar for bulk actions.
Everything is plain CSS and JavaScript in `static/` (`ui.css`, `ui.js`,
`theme-init.js`), with no build step. Fonts are self-hosted in `static/fonts/` (Inter,
JetBrains Mono, and Noto Sans Devanagari, because Inter has no Hindi glyphs). Lucide
icons are inlined by the `_icons.html` macro. Statuses come from one map,
`ui.STATUS_WORDS`.

**Technical details** (header switch, off by default, remembered in `localStorage`) shows
everything researchers use: run history, the full articles table, the story-grouping
preview, extractor / character counts, and the raw log. Technical elements carry
`data-adv` and one CSS rule hides them, so the server renders a single page for both
audiences and every old URL still works.

While a step runs, its card shows a progress bar and one plain sentence ("Writing story 3
of 12…", then "Checking quality (free)…"), with running counts of kept / skipped. Those
come from parsing the stage's own stdout — the crawlers already print exactly what a
progress bar needs, so nothing had to be instrumented. The raw output is under
*Technical log*.

**Why stage-by-stage.** The interesting failures in both pipelines are *silent* and live
in a stage's stdout, not its return value:

- a publisher redesign makes `extract_body` fall through to the generic `<p>` sweep — you
  get a short body, not an error;
- a zone serves the literal placeholder `"Add Hindi content here"`;
- `engine.run_generate` resumes by title, so after a prompt change it can generate
  *nothing* and still look successful.

So each stage runs in a worker thread with `sys.stdout` teed into a per-run log, and the
panel streams it live. Every crawl additionally reports the two checks that catch the
silent cases — the `extractor` histogram (any `generic` means a selector has drifted) and
a count of bodies under 300 chars — and every generate reports how many articles hold an
`[error…]` placeholder instead of a story.

| | |
|---|---|
| Step gating | A locked step says what it is waiting for ("Find articles first", "No Anthropic API key found …") |
| One at a time | A second POST while a stage runs gets `409`, so the stdout tee is unambiguous |
| Params | web: articles per publisher · rsb: releases per zone, `hindi_only` |
| Key badge | shows length and which source won — never the key |
| Crawl backup | `run_*_crawl` opens the output with `"w"`. The UI copies the existing crawl to `output/backups/<name>.<stamp>.json` first, keeping the last 10 |
| Scope | writes only `research/output/`; applies `apply_hindi_patches()` at startup |

`/data/<pipeline>` is the **full articles table** (a Technical details view):

| column | |
|---|---|
| source | publisher / zone |
| source page | the listing the article was discovered on — the thing to open when a selector drifts |
| article URL | the fetched page |
| date, title | as extracted |
| content | first 240 chars, whitespace-collapsed |
| chars | flagged when under 300 — extraction likely failed silently |
| extractor (web) / body hi (rsb) | flagged on `generic`, or a non-Hindi body |
| story (agency), fact/cov (scored) | present only for those stages |

Tabs switch between the crawl, AI-generated and scored files; a text box filters on
title / body / source; a collapsible **"What do these columns mean?"** panel explains each
one in plain language. `full →` opens `/data/<pipeline>/<index>`, which shows the
**complete** source body plus the AI story, translation, social posts and every remaining
field as stored, with prev/next navigation.

Source names and URLs are shortened for display (`Live Hindustan Indian Railways` ->
`Live Hindustan`; the query string becomes a trailing `?…`), with the full value in a
tooltip and on the link. Without that, the two URL columns wrapped and every row grew to
around 200px — and keeping the query *tail* printed an identical meaningless hex suffix on
every RSB row, because the distinguishing `dcd` parameter sits mid-query.

`crawl_source` and `crawl_zone` now record **`listing_url`** on each article, because
"which listing produced this?" is unanswerable after the fact and is exactly what you
need when the `extractor` histogram shows drift. Articles crawled before that field
existed fall back to resolving the listing from `hindi_railway_sources.json` /
`config_rsb.ZONES` by name; those are marked with an orange `*` as a guess.

`/logs` is the **all logs** page: every run newest-first, the selected log in full, and a
box at the top pulling out just the lines worth reading (`WARNING`, `SKIP`, `Error`,
`FAILED`, `Traceback`) so a 400-line crawl log does not have to be read end to end.

> Only AI Generate spends money. Nothing runs on page load, and `debug=False` is
> deliberate — the reloader would import the module twice and double-apply the patches.

---

## Notebooks

| Notebook | Purpose |
|---|---|
| `notebooks/web_news_pipeline_research.ipynb` | Web flow: direct crawl → AI generation → bilingual scores → grounded scores |
| `notebooks/rsb_pipeline_research.ipynb` | Official RSB flow, Hindi edition |
| `notebooks/web_bilinguality_metrics.ipynb` | Inspect existing web output only |
| `notebooks/rsb_bilinguality_metrics.ipynb` | Inspect existing RSB output only |

All four live in `research/notebooks/`. Their first cell locates the research package by
walking up the tree, so they work whether the kernel's working directory is
`research/notebooks/`, `research/` or the repo root — verified from all three.

Launch: in VS Code just pick `.venv` as the kernel (`ipykernel` is enough). For a browser
frontend, `jupyter` alone is not sufficient — a partial install leaves only `jupyter-core`
and `jupyter lab` reports "command not found":

```bash
.venv/bin/python3 -m pip install jupyterlab
.venv/bin/python3 -m jupyter lab
```

Flags default to `False` so opening a notebook never triggers network or API calls:

```python
RUN_DIRECT_CRAWL = False   # publisher tag pages (recommended)
RUN_CRAWL        = False   # RSB zone portals, Hindi edition
RUN_AI           = False   # Claude generation
HINDI_ONLY       = False   # RSB: keep only Devanagari-body articles
```

The web notebook runs `verify_patches()` to prove the Devanagari patches reach the vendored
grouping code. The webapp's Google News route (`RUN_WEB_SEARCH`) was dropped when research
became standalone: Google's redirects resolved for none of the six Hindi sources. Both
pipeline notebooks use a `load_json_file()` helper that treats missing, empty and malformed files alike — an
interrupted `engine.run_generate` can leave a 0-byte output behind, and `json.loads("")`
raises.

`RUN_AI = True` **resumes by title** — delete the agency output file first if you want a
clean regeneration after changing the prompt.

---

## Output files

Everything lands in `research/output/`:

```
web_crawl_output_research.json      rsb_crawl_output_research.json      # Stage 1, scraped
web_agency_output_research.json     rsb_agency_output_research.json     # Stage 2, + AI fields
search_crawl_output_research.json   search_agency_output_research.json  # keyword search, same stages
*_scored.json                                                           # Stage 4, + grounded fields
*_pipeline_status_research.json                                         # progress, not data

logs/<stamp>-<pipeline>-<stage>.log        # one file per ui.py stage run, full stdout
backups/<name>.<stamp>.json                # pre-crawl copies, last 10 kept
```

The agency file is a **superset** of the crawl file — 9 scraped fields (`title`, `body`,
`link`, `source`, `date`, `extractor`, …) plus 20 AI fields (`ai_news_story`,
`ai_news_story_translated`, six social posts, scores). `plagiarism_score` is calculated
locally, not by the model.

> `research/output/` is the only copy of the crawled corpus and has been lost once. It is
> worth gitignoring it *and* keeping a backup before any destructive operation.

---

## Known limits

- **Discovery is fixed** to the configured sources. Direct crawling cannot find a story
  broken by a seventh outlet; that needs search. Multi-source validation
  (`validated_sources`, the confidence badges) is therefore always 1 in research output.
- **Grounding catches unsupported tokens, not unsupported meaning.** A fluent paraphrase
  misstating causation with no checkable figure still passes.
- **Hindi inflection is unsolved** — `ट्रेन` / `ट्रेनें` / `ट्रेनों` do not match under
  Jaccard; no stemmer is in use.
- **`hindi_information_score`** in the metrics notebooks awards 30 points for Devanagari in
  the *translation*, which for a hi→en article is backwards. It caps at 70 by construction.
- **No ground truth.** Validation is by proxy (discrimination, negative control,
  determinism). That shows the grounded metrics behave better; it does not prove them
  correct. A human-labelled gold set is the only way to do that.
- **The webapp remains unfixed** — it still deletes Devanagari in dedup, never groups Hindi
  articles, and self-reports `fact_score`. These fixes live only in `research/`.
