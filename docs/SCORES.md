# How the scores are calculated

Written for whoever is checking an article in the verification window. Five figures appear
there; two are trustworthy, one is not, and two are gates. This page says which is which.

[METRICS.md](METRICS.md) has the full treatment — every field, every threshold, and the
measured behaviour of each. This is the short version.

---

## The one-line version

| Score | Who computes it | Trust it? |
|---|---|---|
| **Fact score** | the AI, about its own work | **No** — it is not a measurement |
| **Plagiarism** | calculated locally from the text | Yes, mechanically |
| **Coverage** | measured against the source | Yes — this is the useful one |
| **Number precision** | measured against the source | As a gate, not a score |
| **Trust ceiling** | hard checks on the source and story | Yes — a 0 means stop |

---

## Fact score (0–100, higher looks better)

**Asked for in the same Claude call that writes the story.** The prompt gives a rubric —
100 means every fact came from the source, 20–39 means it was written from the headline —
and the model fills in a number about its own output. Nothing checks it.

Two reasons not to lean on it:

- **It barely varies.** Across 31 articles it takes **5 distinct values**, with 88% sitting
  between 92 and 95. A number that is almost always 92–95 cannot separate good from bad.
- **A zero gets overwritten by keyword matching, not by judgement.** If the model returns
  0, the code looks for words in the model's *own explanation* and substitutes a score:
  `verified` / `confirmed` / `all major` → 70; `from source` / `based on` → 50; a body over
  100 characters → 30; otherwise 15. So a score of exactly **70, 50, 30 or 15** may be a
  substring match on prose, not an assessment of facts.

It also gave **92** to a press release about a cricket coach that had nothing to do with
railways.

## Plagiarism (0–100, **lower is better**)

Calculated locally, no model involved. Both texts are cut into overlapping **four-word
phrases**, and the score is the share of the story's phrases that also appear in the source:

```
score = 100 × (phrases in both) ÷ (phrases in the story)
```

| Score | Reading |
|---|---|
| ≤ 10 | Excellent — highly original |
| ≤ 20 | Good — well paraphrased |
| ≤ 35 | Acceptable — consider more paraphrasing |
| > 35 | High — needs rewriting |

Two things to know. The denominator is the *story*, so this measures how much of the story
was copied, not how much of the source was reused. And when either text is under 30
characters it returns **0** — so a 0 can mean "excellent" or "could not be compared". The
detail line next to it says which.

## Coverage (0–100, higher is better) — the one worth reading

**The share of the source's figures that the story actually carried over.**

Every number in both texts is extracted and normalised first: Devanagari digits fold to
ASCII (`१५` → `15`), commas are stripped so `1,53,000` and `153000` match, and composite
values like `28/29.08` split into their parts.

```
coverage = 100 × (figures in both) ÷ (figures in the source)
```

| Coverage | Band |
|---|---|
| ≥ 60 | Comprehensive |
| ≥ 30 | Selective |
| < 30 | Sparse |

This is the figure that carries information the fact score does not: **21 distinct values
across the corpus, against the fact score's 5**.

**Low coverage is not automatically a fault.** A source packed with numbers — a timetable,
a list of cancellations — will give a correct three-paragraph summary a low score simply
because it could not carry 200 figures. Read it next to the source-figure count. That is
also why coverage is deliberately *not* folded into a single overall quality number.

## Number precision (0–100) — a gate, not a score

The mirror image: the share of the **story's** figures that appear in the source. A figure
in the story that is nowhere in the source is either a hallucination or a formatting
difference.

It is not useful as a score because it is almost always 100 — 6 distinct values across the
corpus. It is useful as an **alarm**: below **90**, the article is flagged and its trust
ceiling is capped at `100 − 15 × (unsupported figures)`.

## Trust ceiling (0–100)

A cap on how trustworthy an article can be **regardless of what the model said about
itself**. Starts at 100 and is lowered by hard checks:

| Check | Ceiling becomes |
|---|---|
| The source has no railway content at all | **0** |
| The source body is under 300 characters | at most 20 |
| The story is missing, under 30 characters, or an error placeholder | **0** |
| Number precision below 90 | at most `100 − 15 × unsupported figures` |

This is what catches the cricket-coach release: **fact score 92, trust ceiling 0.**

A `0` here means the article should not be published without a human rewriting it, whatever
the other numbers say. The failing checks are listed by name under the scores.

---

## Topics

The model is asked to pick 2–4 topics from a fixed list of 15. Nothing in the app checks
either the list or the count, so the verification window does both:

- **✓ green** — a supporting term for that topic appears in the source.
- **⚠ amber, "no source evidence"** — the model assigned the topic and nothing in the
  source backs it up. Currently **32 of 115** assigned topics.

Matching is whole-word with light stemming, not substring — `मृत` (dead) must not fire
inside अ**मृत** भारत, and `आग` (fire) must not fire inside `आगे`.

## Impact

The model labels each article High / Medium / Low. Separately, the level is derived from
trigger words in the source and the two are compared. They agree on **12 of 31**
articles (39%).

The model's `Low` is effectively unreachable — anything not exactly `High|Medium|Low` is
coerced to `Medium`, so across 31 articles it used Low **zero** times while the rule used
it 12 times.

---

## What none of these can tell you

- **They check tokens, not meaning.** A fluent paraphrase that misstates cause and effect,
  while inventing no figure, passes every check on this page. That is what your read of the
  left-hand column is for.
- **There is no ground truth.** These metrics are validated by proxy — they discriminate,
  they catch a known bad article, they are repeatable. That shows they behave better than
  the self-reported score; it does not prove them right.
- **One source.** The story is written from the single body shown on the left. Where no
  other publisher covered the same story, there is nothing to cross-check against.
