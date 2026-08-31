# Metrics Reference

Every number this pipeline attaches to an article: what computes it, the exact formula,
and what it is and is not evidence of.

Measured values throughout are from the current corpus — **31 articles** (22 web + 9 RSB)
in `research/output/*_scored.json`, as of **2026-08-31**. Reproduce with:

```bash
.venv/bin/python3 research/shared/score_report.py        # the discrimination table
.venv/bin/python3 research/ui.py                  # per-article, in a browser
```

Metrics fall into four groups, and the distinction is the point of this document:

| group | computed by | verified against the source? |
|---|---|---|
| [1. Crawl](#1-crawl-stage) | `web_search.pipeline` / `rsb_search.pipeline` / `hindi_text` | n/a — describes the scrape |
| [2. LLM self-reported](#2-llm-self-reported) | the same Claude call that writes the story | **no** |
| [3. Webapp-computed](#3-webapp-computed) | `engine._compute_plagiarism` | yes, mechanically |
| [4. Grounded](#4-grounded-research) | `research/shared/grounding.py` | yes, deterministically |

---

## 1. Crawl stage

Produced while scraping. No model involved. These are the ones that catch a **silent**
failure — a publisher redesign yields a short body, not an exception.

### `chars` (body length)

Not stored as a field; derived as `len(article["body"])`. Compared against
`config_web.MIN_BODY_CHARS = 300` / `config_rsb.MIN_BODY_CHARS = 200`, below which the
crawler drops the article and the UI flags it.

Measured improvement of direct crawling over the Google-News route, per source:

| Source | via Google News | direct |
|---|---|---|
| AajTak | 219 | **2318** |
| Jansatta | 265 | **2918–10857** |
| Navbharat Times | 255 | **1843–2482** |
| Amar Ujala | 1143 | **1252–5924** |
| Live Hindustan | 2251 | **3098–4273** |
| PIB Hindi | 68 | **3059–4017** |

### `extractor`

Which body-extraction path won: a domain key from `config_web.BODY_SELECTORS`, or
`generic` for the fallback `<p>` sweep. **This is a drift detector, not a quality score.**
A domain reporting `generic` means its dedicated CSS selector no longer matches — which
has already happened once, to `livehindustan`, masked by the fallback.

```python
Counter(a["extractor"] for a in articles)     # any "generic" = investigate
```

### `title_hindi_ratio`, `body_hindi_ratio`, `is_hindi_body` (RSB only)

[`rsb_pipeline.script_ratio`](../rsb_search/pipeline.py#L80) — the share of *scripted* characters
that are Devanagari, ignoring digits, punctuation and whitespace:

```
script_ratio = devanagari_chars / (devanagari_chars + latin_chars)      # 0.0 – 1.0
is_hindi_body = body_hindi_ratio >= config_rsb.HINDI_BODY_THRESHOLD     # 0.5
```

Measured per zone, not trusted from the zone's advertised language — the reason this
exists is that `lang=1` does not guarantee Hindi *body* text:

- **CR, WR** — full Hindi, titles and bodies. Usable.
- **NR** — Hindi titles over mostly English bodies. `body_hindi_ratio` is what separates them.
- **ECoR, SCR** — Hindi pages return the literal placeholder `"Add Hindi content here"`.

### `is_railway_relevant` (admission test, not a stored score)

[`hindi_text.is_railway_relevant`](../shared/hindi_text.py#L213). **Two-tier**, because a single
tier over-admits:

```
one STRONG term                        -> relevant     (रेलवे, ट्रेन, आईआरसीटीसी, वंदे भारत, …)
two or more WEAK terms                 -> relevant     (कोच, यात्री, स्टेशन, प्लेटफॉर्म, …)
otherwise                              -> dropped
```

Matching is **token-bounded with light stemming**, not substring
([`term_in`](../shared/hindi_text.py#L201) + [`light_stem`](../shared/hindi_text.py#L185)). Both directions of
failure are real and pull against each other:

| failure | example | consequence |
|---|---|---|
| over-match | `ट्रेन` inside `ट्रेनिंग` ("training") | an HR article reads as railway news |
| over-match | `कोच` inside `मुख्य कोच गौतम गंभीर` | a **cricket coach** reads as a carriage |
| under-match | `रेलगाड़ी` vs `रेलगाड़ियों` | a real cancellation notice rejected as off-topic |

A length allowance cannot separate these — Devanagari matras are separate codepoints, so
`ट्रेन → ट्रेनिंग` adds exactly as many as `दुर्घटना → दुर्घटनाओं`. Stemming both sides
and comparing for equality does.

### Dedup preprocessing (measured once, not per article)

[`hindi_patches.verify_patches()`](../shared/hindi_patches.py) reports these; they justify the
runtime rebinding rather than scoring an article. On 16 real Hindi titles:

| | before | after |
|---|---|---|
| distinct dedup keys (16 titles) | **12** | **16** |
| titles yielding <3 comparable words | **14** | **0** |
| two nukta encodings of हावड़ा hash equal | no | **yes** |

Five unrelated titles collapsed onto one key — all normalised to a single space — so four
of five would be silently discarded as duplicates. `seen_articles.json` persists, so that
poisoning is permanent.

---

## 2. LLM self-reported

`fact_score`, `impact_level`, `topics`, `themes`, `fact_details` and `impact_reason` all
come from **the same Claude call that writes the story** (the system prompt at
[engine.py:18-54](../../engine.py#L18-L54)). Nothing verifies them. Group 4 exists because of
this.

### `fact_score` (0–100)

Asked for in the prompt with a rubric ([engine.py:38-43](../../engine.py#L38-L43)):
`100` = all facts from source, `80-99` = most, `60-79` = core + context, `40-59` = partial,
`20-39` = from headline only, `0-19` = no source body. Clamped to 0–100 at
[engine.py:105](../../engine.py#L105); non-numeric becomes `0`.

**Two properties to know before using it:**

1. **It barely discriminates.** 5 distinct values across 31 articles, median 92.
2. **A zero is silently overwritten by keyword matching**, not by the model
   ([engine.py:409-417](../../engine.py#L409-L417)). If `fact_score == 0` and a story exists:

   ```
   'verified' / 'confirmed' / 'all major' in fact_details   -> 70
   body > 100 chars and 'from source' / 'based on' in fd    -> 50
   body > 100 chars                                         -> 30
   otherwise                                                -> 15
   ```

   So a `fact_score` of exactly 70, 50, 30 or 15 may be a substring match on the model's
   own prose rather than any judgement of the facts.

It also scored a misfiled PIB **sports** release **92**.

### `impact_level` (High / Medium / Low)

Rubric is one line of the prompt ([engine.py:44](../../engine.py#L44)): High = safety /
cancellations / policy / accidents; Medium = infrastructure / new services / tech;
Low = routine / awards / HR.

**`Low` is unreachable in practice**: anything not exactly `High|Medium|Low` is coerced to
`Medium` at [engine.py:113](../../engine.py#L113). Measured: 21 Medium / 10 High / **0 Low**.

### `topics` (list)

The prompt specifies a **closed vocabulary of 15** and asks for 2–4
([engine.py:45-46](../../engine.py#L45-L46)). Nothing enforces either the vocabulary or the
count — the only validation is "is it a list" ([engine.py:109](../../engine.py#L109)).

### `themes`, `fact_details`, `impact_reason`

Free text. Not scores. `fact_details` is load-bearing anyway, because of the
`fact_score` repair above.

---

## 3. Webapp-computed

### `plagiarism_score` (0–100, **lower is better**)

[`engine._compute_plagiarism`](../../engine.py#L200) — the one metric in the product that is
actually calculated. Word 4-gram overlap:

```
words        = re.findall(r'[a-zA-Zऀ-ॿ]{2,}', text.lower())    # bi-script
ngrams(t)    = { tuple(words[i:i+4]) }
score        = round(100 * |ai_ngrams ∩ source_ngrams| / |ai_ngrams|)
```

Bands, reported in `plagiarism_detail` with the raw counts:

| score | label |
|---|---|
| ≤ 10 | Excellent — highly original |
| ≤ 20 | Good — well paraphrased |
| ≤ 35 | Acceptable — consider more paraphrasing |
| > 35 | High — needs rewriting |

Returns `0` (not `None`) when either text is under 30 chars, so a **0 can mean "excellent"
or "not comparable"** — read `plagiarism_detail` to tell them apart. Measured: 14 distinct
values, range 0–25, median 8.

Note the denominator is `|ai_ngrams|`, so it measures *how much of the story is copied*,
not how much of the source was reused.

---

## 4. Grounded (research)

[`research/shared/grounding.py`](../shared/grounding.py) — deterministic, source-checked, no API calls.
Modelled on `_compute_plagiarism`: **every score ships with the raw counts that produced
it**, so an editor can audit the number instead of trusting it. Applied by
[`score_report.rescore_file`](../shared/score_report.py#L29), which writes `*_scored.json` with these
fields *alongside* the LLM's, so old and new are directly comparable.

### The number extractor everything else is built on

[`extract_numbers`](../shared/grounding.py#L55) — script- and format-normalised numeric tokens:

```
Devanagari digits fold to ASCII        १५ -> 15
commas stripped as digit grouping      1,53,000 -> 153000        (Indian grouping)
composite runs split on . : - /        28/29.08 -> {8, 28, 29}
compared as int                        leading zeros cannot mismatch
```

> **Verified discrepancy.** The docstring claims "both the whole grouped figure AND its
> components are emitted, so a story writing `1.53 lakh crore` still matches a source
> writing `153000`". It does not: `1.53 lakh crore` yields `{1, 53}` and `153000` yields
> `{153000}` — no intersection. Only **comma**-grouped figures unify; a run containing
> `. : - /` emits its components and never the whole. The `रात 8 बजे` / `20:00` example in
> the module header likewise does not match (`{8}` vs `{0, 20}`). Comma grouping and
> Devanagari digits do work as described.
>
> Side effect worth knowing: `00:40` and `20:00` both emit `0`, so `0` appears as a
> "figure" in most texts and adds a little matching noise.

### `coverage_score` — the load-bearing metric

```
coverage_score   = round(100 * |story_numbers ∩ source_numbers| / |source_numbers|)   # recall
number_precision = round(100 * |story_numbers ∩ source_numbers| / |story_numbers|)
```

`None` for both when either side has no numbers at all. Shipped with
`source_numbers`, `story_numbers`, `numbers_carried`, `numbers_unsupported`,
`numbers_missed`, and a `coverage_detail` band:

| coverage | band |
|---|---|
| ≥ 60 | Comprehensive |
| ≥ 30 | Selective |
| < 30 | Sparse |

**Why recall and not precision.** Precision is saturated and carries almost no signal
(6 distinct values, median 100). Recall discriminates: **21 distinct values, range 3–100**,
against `fact_score`'s 5. So precision became a gate and recall became the score.

**Coverage is deliberately not folded into a single "fact score"**, because it is
confounded by source shape: a timetable body with 200+ numerals justifiably yields low
coverage for a correct summary. In this corpus `source_numbers` ranges 0–256.

### `grounding_ceiling`, `gates_passed`, `gate_failures`

[`compute_gates`](../shared/grounding.py#L124) returns a **ceiling**, not a score, so it composes
with any other metric. Starting at 100:

| check | ceiling |
|---|---|
| source fails `is_railway_relevant(title, body[:600])` | **0** |
| `len(body) < 300` | ≤ 20 |
| story missing, under 30 chars, or starting `Generation failed` / `[Not generated` | **0** |
| `number_precision < 90` | ≤ `100 − 15 × unsupported_count` |

Floored at 0. `gates_passed` is `True` only when the failure list is empty.

This is the negative control: the misfiled PIB **sports** release carries
`fact_score = 92` and `grounding_ceiling = 0`.

Measured: **4 of 31** articles fail a gate — 1 off-topic source, 3 on numeric precision
(57%, 79%, 86%).

### `topics_verified`, `topics_unsupported`, `topics_off_vocabulary`, `topic_evidence`

[`verify_topics`](../shared/grounding.py#L196) enforces the two things the prompt asks for but never
checks:

1. **Vocabulary** — the 15 terms in [`TOPIC_VOCABULARY`](../shared/grounding.py#L159). Anything else
   lands in `topics_off_vocabulary`.
2. **Source evidence** — each topic needs at least one hit from its bilingual
   [`TOPIC_LEXICON`](../shared/grounding.py#L166) entry in the source body, matched token-bounded via
   `term_in`. The matching terms are returned in `topic_evidence` (up to 4), so the
   judgement is auditable.

Measured: 115 topics assigned, **0 off-vocabulary**, **32 without source evidence** —
`Infrastructure` 11, `Operations` 7, `Passenger-Service` 4, `Technology` 3, `New-Trains` 3,
`Policy` 2.

### `impact_rule_level`, `impact_agrees`, `impact_evidence`

[`verify_impact`](../shared/grounding.py#L235) derives the level from source triggers
([`IMPACT_TRIGGERS`](../shared/grounding.py#L223)) and compares it with the model's label:

```
any High trigger    -> High        (दुर्घटना, टक्कर, पटरी, मौत, रद्द, accident, derail, …)
else any Medium     -> Medium      (नई ट्रेन, परियोजना, उद्घाटन, विद्युतीकरण, project, …)
else                -> Low         <- the default engine.py never reaches
```

`आग` (fire) and `मृत` (dead) are deliberately **excluded** even token-matched: they
collide with आगे / आगरा and with अमृत भारत. Unambiguous compounds (`आगजनी`, `मृतक`) are
used instead.

Measured on 31 articles:

| | High | Medium | Low |
|---|---|---|---|
| LLM `impact_level` | 10 | 21 | **0** |
| rule `impact_rule_level` | 5 | 14 | **12** |

`impact_agrees` on **12 of 31 (39%)**. The disagreement is concentrated where the LLM's
coerced `Medium` default meets a source with no trigger at all.

---

## 5. Bilinguality (notebooks)

Computed in the notebooks, not stored on articles. Both pipeline notebooks and both
`notebooks/*_bilinguality_metrics.ipynb` use the same definitions.

### `bilingual_score` (0–100, four 25-point checks)

```
+25  source_lang in ('hi', 'en')
+25  len(ai_news_story.strip()) > 30
+25  len(ai_news_story_translated.strip()) > 30
+25  direction_ok
```

where `direction_ok` requires the story to be in the source script and the translation in
the other:

```
source_lang == 'hi'  ->  story has Devanagari AND translation has Latin
source_lang == 'en'  ->  story has Latin      AND translation has Devanagari
```

### `hindi_information_score` (0–100 nominally)

```
+35  source (title + body) contains Devanagari
+35  ai_news_story contains Devanagari
+30  ai_news_story_translated contains Devanagari
```

> **Known-broken, kept for continuity.** The third term awards 30 points for Devanagari in
> the *translation*, which for a hi→en article is backwards — a correct English
> translation is penalised. It therefore **caps at 70 by construction** for exactly the
> articles the pipeline is built for. Do not use it as a quality gate; read the component
> booleans instead.

### Counts also reported

`generated` (story present and not an `[…]` / `Generation failed` placeholder),
`bilingual_complete` (both story and translation non-empty), `source_has_hindi`,
`hindi_output_articles`, `hindi_translation_articles`, and `fact_score` /
`plagiarism_score` split into all-articles vs Hindi-touching subsets.

---

## 6. Discrimination, side by side

The whole argument for group 4, on the 31-article corpus:

| metric | who computes it | distinct values | range | median |
|---|---|---|---|---|
| `fact_score` | LLM, self-reported | **5** | 30–95 | 92 |
| `impact_level` | LLM, self-reported | 2 of 3 (`Low` unreachable) | — | — |
| `plagiarism_score` | webapp, calculated | 14 | 0–25 | 8 |
| `number_precision` | research, **gate** | 6 | 57–100 | 100 |
| **`coverage_score`** | research, **score** | **21** | 3–100 | 74 |
| `grounding_ceiling` | research, cap | 5 | 0–100 | 100 |

Two `shared/score_report.py` runs on the same input are byte-identical.

---

## 7. What these metrics do not tell you

- **No ground truth.** Validation is by proxy — discrimination, a negative control (the
  PIB sports release), and determinism. That shows the grounded metrics *behave* better;
  it does not prove them *correct*. A human-labelled gold set is the only way to do that.
- **Grounding catches unsupported tokens, not unsupported meaning.** A fluent paraphrase
  that misstates causation while inventing no figure passes every check here.
- **Coverage is confounded by source shape.** Low coverage on a timetable is not an error.
  Read it with `source_numbers`.
- **Hindi inflection is only partly handled.** `light_stem` is enough for lexicon matching;
  Jaccard similarity over `ट्रेन` / `ट्रेनें` / `ट्रेनों` still fails. No real stemmer.
- **`validated_sources` is always 1** in research output. Direct crawling cannot find a
  story broken by a seventh outlet, so the webapp's multi-source confidence badges carry
  no information here.
- **The webapp itself remains unfixed** — it still deletes Devanagari in dedup, never
  groups Hindi articles, and self-reports `fact_score`. Everything in groups 1 and 4 lives
  only in `research/`.
