# Product Requirements Document — Hindi Research Pipeline

**Status:** implemented, in evaluation · **Repo:** `TechieTripathi/hindi-railway-web-rsb`
**Relationship to [PRD.md](PRD.md):** that document covers the Flask webapp and remains
valid for it. This one covers `research/`, which exists because the webapp does not work
for Hindi. Everything measured below is from the current corpus (31 articles, 22 web + 9
RSB) and is reproducible with `research/shared/score_report.py`.

---

## 1. Product overview

A Hindi-first news pipeline for Indian Railways, plus a way to tell whether its output is
any good. It crawls Hindi publishers and the Hindi edition of the official zone portals,
generates bilingual stories, and scores them against their sources with checks that do not
rely on the model grading itself.

It ships as a separate package, imported read-only by nothing in the webapp. Where webapp
behaviour must change for a research run it is rebound at runtime
(`shared/hindi_patches.py`), never edited on disk.

---

## 2. Problem statement

The webapp was built for English and fails on Hindi in five measured ways. Each is a
requirement below.

| # | Failure | Measured |
|---|---|---|
| P1 | Google News RSS returns encrypted redirect URLs that must be resolved before the body can be scraped | resolution failed **18 of 18** across the six Hindi sources; every article fell through to the RSS snippet, giving **150–220 char** bodies |
| P2 | `dedup._normalise` and `web_agent._make_dedup_key` run `re.sub(r'[^a-z0-9\s]', '', text)` — every Devanagari codepoint is deleted | 16 distinct titles collapsed to **12** dedup keys; **5 unrelated articles** normalised to a single space and shared one key |
| P3 | `web_agent._title_words` extracts with `[a-z]{4,}`, Latin only | **14 of 16** titles produced an empty word set, so similarity grouping never ran |
| P4 | `config.ZONES` points every zone at `view_section.jsp?lang=0`, the **English** edition | Central Railway `lang=0`: 18 releases, **0** Hindi titles. `lang=1`: same 18, **17** Hindi titles |
| P5 | `fact_score`, `impact_level` and `topics` are produced by the same Claude call that writes the story; nothing verifies them | `fact_score` has **5 distinct values**, 88% in 92–95, and scored a misfiled **sports** release **92** |

P2 is the most damaging: `seen_articles.json` persists, so four of those five articles
would be discarded as duplicates permanently.

---

## 3. Goals

1. Extract **full Hindi article bodies**, not search snippets.
2. Make Devanagari survive deduplication and similarity grouping.
3. Read the **Hindi** edition of the official portals, and measure what is actually Hindi
   rather than trusting the advertised language.
4. Produce quality scores that are **computed from the source**, not self-reported, and
   that ship with the counts that produced them so an editor can audit rather than trust.
5. Make the whole thing runnable, stage by stage, by someone who did not build it.

### Non-goals

- Publishing to a CMS or social platforms.
- Authentication, permissions, editorial approval workflow.
- Production infrastructure or scaling.
- **Fixing the webapp.** These changes live in `research/` and are applied by runtime
  rebinding. Adopting them in the product is a separate decision.
- **Multi-source consolidation.** Each story is generated from exactly one article body
  (`engine.py:385` passes one title and one body). See §8.

---

## 4. Users

| User | Needs |
|---|---|
| **Hindi newsroom editor** | Full-length Hindi source text; a story plus translation and social posts; a quality signal they can act on |
| **Researcher / engineer** | To run one stage at a time, see exactly what it printed, and compare runs |
| **Reviewer of the approach** | Evidence that the new metrics discriminate better than the self-reported ones |

---

## 5. User stories

- As an editor, I want the **full article text** in Hindi so the generated story has
  something to work from.
- As an editor, I want unrelated Hindi articles **not** silently discarded as duplicates.
- As an editor, I want to know **which figures from the source the story actually carried
  over**, not just a score the model gave itself.
- As an editor, I want an off-topic article **flagged** even when the model rated it highly.
- As a researcher, I want to run crawl, generate and score **separately**, and read
  everything each stage printed.
- As a researcher, I want a run's config and code version recorded so two runs are
  comparable.

---

## 6. Functional requirements

### 6.1 Crawling — web (`web_search/`)

- **FR-1** The system shall discover articles by reading each publisher's own tag/topic
  page directly, without a search engine or API key. *(addresses P1)*
- **FR-2** The system shall identify article links per domain via configured URL patterns,
  and exclude listing, media and navigation URLs.
- **FR-3** The system shall extract bodies with a per-domain extractor, falling back to a
  generic `<p>` sweep, and shall **record which path produced each body** in an
  `extractor` field.
- **FR-4** The system shall rewrite URLs whose listing form carries no server-rendered body
  (PIB `PressReleaseDetail.aspx` → `PressReleasePage.aspx`).
- **FR-5** The system shall drop off-topic articles using a two-tier relevance test:
  unambiguous terms admit alone, ambiguous terms require corroboration.
- **FR-6** The system shall record the **listing page** each article was discovered on.

**Acceptance:** median body ≥ 1500 chars across the six sources; 0 articles under 300
chars without a warning. *Measured:* AajTak 219 → **2318**, PIB 68 → **3059–4017**,
Jansatta 265 → **2918–10857**.

### 6.2 Crawling — official portals (`rsb_search/`)

- **FR-7** The system shall read the Hindi edition (`lang=1`) of each zone listing.
  *(addresses P4)*
- **FR-8** The system shall compute and store `body_hindi_ratio` per article and shall not
  infer language from the zone's advertised edition.
- **FR-9** The system shall distinguish the three thin-body failure modes — untranslated
  placeholder, PDF-only release, genuinely short page — rather than reporting one error.
- **FR-10** The system shall dedupe **within a run only**, never touching the webapp's
  persistent `seen_articles.json`.

**Acceptance:** zones classified by measured Hindi coverage. *Measured:* CR/WR full Hindi;
NR Hindi titles over English bodies; ECoR/SCR return the literal placeholder
`"Add Hindi content here"`.

### 6.3 Devanagari preprocessing (`shared/`)

- **FR-11** The system shall normalise Devanagari so identical words compare equal —
  unifying nukta encodings and stripping zero-width joiners. *(addresses P2)*
- **FR-12** The system shall tokenise bi-script and shall delegate pure-Latin titles to a
  verbatim copy of the webapp's own logic, so existing English hashes stay valid.
- **FR-13** The system shall apply these by **runtime rebinding**, editing no webapp file,
  and shall provide a verification that the rebind reached the webapp's internal callers.

**Acceptance:** 16 titles → **16** distinct dedup keys (was 12); titles with <3 comparable
words **0** (was 14); the two nukta encodings of हावड़ा hash equal; English titles
unchanged.

### 6.4 Generation

- **FR-14** The system shall generate via the same `engine.run_generate()` the webapp uses,
  reading and writing research files only.
- **FR-15** The system shall resolve the API key with a documented precedence — shell
  export → `research/.env` → the webapp's `.env` — and shall never print the key.
- **FR-16** The system shall report how many articles hold an error placeholder rather than
  a story, and shall state explicitly when a run produced **zero** new stories because
  generation resumes by title.

### 6.5 Grounded scoring (`shared/grounding.py`)

- **FR-17** The system shall compute `coverage_score` — the share of the source's figures
  the story carried over — from normalised numeric tokens. *(addresses P5)*
- **FR-18** The system shall use numeric **precision as a gate**, not a score, because it
  is near-saturated.
- **FR-19** The system shall publish a `grounding_ceiling` capping trustworthiness
  independently of the model's own score, with the failing checks named.
- **FR-20** The system shall enforce the closed topic vocabulary the prompt specifies but
  never checks, and shall require **source evidence** for each topic, returning the
  matching terms.
- **FR-21** The system shall derive an impact level from source triggers and compare it
  with the model's label.
- **FR-22** Every score shall ship with the raw counts that produced it.

**Acceptance:** `coverage_score` discriminates better than `fact_score`, the gate catches a
known off-topic article, and two runs are byte-identical. *Measured:* coverage **21
distinct values** (range 3–100) vs `fact_score`'s **5**; the PIB sports release carries
`fact_score 92` and `grounding_ceiling 0`; **32 of 115** topics have no source evidence;
impact agreement **12/31 (39%)**; two `score_report.py` runs byte-identical.

### 6.6 Control panel (`ui.py`)

- **FR-23** The system shall run each stage on an explicit click, one at a time, on a port
  separate from the webapp.
- **FR-24** The system shall gate stages on their prerequisite and state which step to do
  first when one is unavailable.
- **FR-25** The system shall stream each stage's full output live and persist it per run.
- **FR-26** The system shall surface the two silent-failure checks on every crawl — the
  `extractor` histogram and a count of short bodies.
- **FR-27** The system shall mark the stage that spends money and confirm before running it.
- **FR-28** The system shall provide a browsable table of crawled articles — source,
  listing page, article URL, date, title, content excerpt — with a full-article view.
- **FR-29** The system shall back up the existing crawl output before a crawl overwrites it.

### 6.7 Reproducibility

- **FR-30** The system shall write only under `research/output/`; project-root files are
  never modified.
- **FR-31** The system shall record, per run, the config that produced it and a hash of the
  research source, so a metric change can be attributed to a config change or a code change.

---

## 7. Non-functional requirements

- **NFR-1** Runs offline for everything except crawling and generation; scoring and tests
  require no network and no API key.
- **NFR-2** Nothing runs on page load or notebook open — all run flags default to `False`.
- **NFR-3** Devanagari normalisation degrades gracefully to `unicodedata` NFC when
  `indic-nlp-library` is absent, and reports which backend is active.
- **NFR-4** Missing, empty and malformed JSON are treated alike; an interrupted run leaving
  a 0-byte file must not raise.
- **NFR-5** Secrets are never committed. `research/` is a nested repository and therefore
  does **not** inherit the parent's `.gitignore`; its own must list `.env`.
- **NFR-6** The package installs standalone from `requirements-research.txt`.

---

## 8. Explicitly out of scope, with reasons

**Multi-source consolidation.** The final story is generated from **one** source body.
`engine.py` never reads `similar_articles`, `validated_sources` or
`brave_related_articles`; when duplicates are grouped, the longest body wins and the others
are discarded, not merged. `validated_sources` is a corroboration count driving confidence
badges, not an input to the writing. In research output these fields are empty for all 31
articles, because direct crawling skips search. Consolidation would need both a prompt
change and a widening of the grounding comparison to the union of sources — otherwise a
figure taken from a second outlet reads as a hallucination and the precision gate caps the
ceiling.

**Meaning-level verification.** Grounding catches unsupported *tokens*. A fluent paraphrase
that misstates causation while inventing no figure passes every check here.

**Hindi inflection.** `light_stem` is sufficient for lexicon matching; Jaccard similarity
over ट्रेन / ट्रेनें / ट्रेनों still fails. No real stemmer is in use.

---

## 9. Success metrics

| Metric | Target | Current |
|---|---|---|
| Body length vs the search path | ≥ 5× | 3–45× per source |
| Distinct dedup keys, 16 Hindi titles | 16 | **16** (was 12) |
| Titles with <3 comparable words | 0 | **0** (was 14) |
| `coverage_score` distinct values vs `fact_score` | > 2× | **21 vs 5** |
| Known off-topic article capped | ceiling 0 | **0**, against `fact_score` 92 |
| Topics without source evidence | surfaced | **32 of 115** |
| Determinism | byte-identical | **yes** |
| Stage runnable by a new user without reading code | yes | control panel |

---

## 10. Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| Per-domain selectors break on a publisher redesign | **Silent** — short bodies, not an error | `extractor` recorded per article; `generic` and <300-char bodies flagged on every crawl |
| No ground truth | Validation is by proxy — discrimination, negative control, determinism. Shows the metrics *behave* better; does not prove them *correct* | A human-labelled gold set is the only fix |
| `coverage_score` confounded by source shape | A timetable with 200+ numerals yields low coverage for a correct summary | Published with `source_numbers`; deliberately not folded into a single score |
| `crawler.py:43` fetches with `verify=False` | TLS verification disabled for every zone request, warning suppressed; silently accepts any certificate | **Open.** Inherited by `rsb_search/` via `crawler._fetch` |
| `research/` is a nested repo | It does not inherit the parent `.gitignore`; an `.env` with live keys reached a local commit once | `.env` is now the first entry in `research/.gitignore`; documented in [SETUP.md](SETUP.md) |
| `fact_score` repaired by keyword matching | A score of exactly 70/50/30/15 may be a substring match on the model's own prose (`engine.py:409-417`), not a judgement | Documented in [METRICS.md](METRICS.md); `grounding_ceiling` is independent of it |

---

## 11. Open questions

1. **Adoption** — do the Devanagari fixes move into the webapp, or stay as a research-time
   rebinding? The dedup poisoning is permanent while they stay here.
2. **Multi-source** — is single-source generation acceptable, or is consolidation a
   requirement? §8 has the cost.
3. **Gold set** — who labels it, and how many articles? Nothing here is validated against
   human judgement.
4. **Zone coverage** — ECoR and SCR publish an untranslated placeholder. Do we drop them,
   or fall back to their English edition?
5. **TLS** — pin a CA bundle, or scope the `verify=False` exemption to named hosts?
