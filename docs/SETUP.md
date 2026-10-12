# Setup

Every command below was run against this checkout. Where a command prints something
worth checking, the real output is shown.

Run everything from the **repository root** — the folder holding `ui.py`.

---

## 0. Prerequisites

| | |
|---|---|
| Python | **3.13.12** is what this checkout is verified on. The code uses `from __future__ import annotations`, so 3.9+ should work — untested. |
| git | any recent version |
| API keys | Anthropic (required for story generation). Brave is optional and unused by both pipelines. |

```bash
python3 -V          # -> Python 3.13.12
```

---

## 1. Get the code

This repo is self-contained: clone it anywhere, under any directory name.

```bash
git clone https://github.com/TechieTripathi/hindi-railway-web-rsb.git research
cd research
```

It started inside the Flask webapp (`aimonitors25/railway-agent`) and imported that
webapp's `engine`, `crawler`, `dedup` and `web_agent` modules. Those are now copied into
[`vendor/`](../vendor/__init__.py), so nothing outside this folder is needed. The copies
**no longer track the webapp** — a prompt change in the webapp's `engine.py` or a parser fix
in its `crawler.py` has to be copied into `vendor/` by hand to reach research runs.

---

## 2. Create the virtual environment and install

```bash
python3 -m venv .venv
.venv/bin/python3 -m pip install --upgrade pip
.venv/bin/python3 -m pip install -r requirements-research.txt
```

> **Use `.venv/bin/python3 -m pip`, never `.venv/bin/pip3`.** A venv's console scripts
> hard-code an absolute interpreter path, so they break the moment the checkout moves or
> the drive remounts. That has already happened here — `.venv/bin/pip3` currently starts
> with a path that no longer exists:
>
> ```
> $ .venv/bin/pip3 --version
> bad interpreter: /media/ai-centre-01/DATA-4TB1/.../.venv/bin/python: No such file or directory
> ```
>
> `python3 -m pip` has no such problem. The same applies to `jupyter`, `flask` and
> `pytest` — always invoke them as `.venv/bin/python3 -m <tool>`.

There is no need to `source .venv/bin/activate`. Calling `.venv/bin/python3` directly is
equivalent and cannot be forgotten halfway through a session.

---

## 3. Add your API key

```bash
cp .env.example .env
$EDITOR .env            # fill in ANTHROPIC_API_KEY
```

[`shared/config_env.py`](../shared/config_env.py) reads `.env` and binds the key onto the
generation engine before every AI run — from `ui.py` and the notebooks alike. You do not
need to export anything, and `python-dotenv` is not required. Check which key is in play:

```bash
.venv/bin/python3 shared/config_env.py
# ANTHROPIC_API_KEY  set, 108 chars  (.env)
# BRAVE_API_KEY      set, 31 chars  (.env)
```

Precedence, highest first: a shell `export` → `.env`.

`.env` is the first entry in `.gitignore`. An `.env` holding live API keys reached a local
commit once, back when this repo was nested inside the webapp and people assumed the
webapp's `.gitignore` covered it. Verify before your first commit:

```bash
git check-ignore -v .env
# -> .gitignore:<line>:.env	.env      (any output means it is ignored)
```

---

## 4. Check the install — no network, no API spend

```bash
.venv/bin/python3 test_hindi_text.py
# -> 10 passed, 0 failed      (on a fresh clone; see the note below)

.venv/bin/python3 -m unittest test_article_selection
# -> Ran 14 tests ... OK

.venv/bin/python3 shared/hindi_patches.py
# Backend: indic-nlp-library
#   distinct dedup keys      2 -> 5   (of 5 titles)
#   titles with <3 words     5 -> 0
#   grouping similar_count   2
#   nukta variants hash equal True
```

`Backend: indic-nlp-library` is the line to look for. If it says
`unicodedata-NFC (fallback)`, `indic-nlp-library` did not install — Devanagari still
normalises, but zero-width joiners survive and some dedup keys will differ.

> `test_real_corpus_gate` only runs when `output/web_agency_output_research.json` exists,
> and it expects article 0 of that file to be a specific PIB sports release. After a
> re-crawl it fails on the new corpus rather than on the code.

If a corpus already exists under `output/`, this also works offline and is repeatable:

```bash
.venv/bin/python3 shared/score_report.py
```

---

## 5. Run the research control panel

```bash
.venv/bin/python3 ui.py
```

→ **http://127.0.0.1:5001** · run history at `/logs` · articles at `/data/web`

It writes only under `output/`. Nothing runs until you click; only step 2 ("Write stories
with AI") spends money. Port 5001 was chosen so it can run alongside the webapp's `app.py`
on 5000, if you also have that checked out.

> `debug=False` is deliberate — Flask's reloader imports the module twice and would
> double-apply the Devanagari patches. **Restart the process to pick up code changes.**

---

## 6. Notebooks (optional)

In **VS Code**, just open a `.ipynb` and select `.venv` as the kernel — `ipykernel` is
enough, no extra install.

For a browser frontend, note that `jupyter` alone is a metapackage; a partial install
leaves only `jupyter-core` and `jupyter lab` reports "command not found":

```bash
.venv/bin/python3 -m pip install jupyterlab
.venv/bin/python3 -m jupyter lab
```

All `RUN_*` flags default to `False`, so opening a notebook fires no network or API
calls.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `bad interpreter: … No such file or directory` | A venv console script with a stale path. Use `.venv/bin/python3 -m <tool>`. |
| `Address already in use` / `Port 5001 is in use` | An instance is already running. `ss -ltnp \| grep 5001` to find it. |
| `ModuleNotFoundError: flask` (or `indicnlp`, `bs4`, `httpx`) | Requirements not installed into **this** venv. Re-run step 2 with the `.venv/bin/python3 -m pip` form. |
| `ANTHROPIC_API_KEY is not set` | No key in the shell or `.env`. Run `shared/config_env.py` to see which sources were checked. |
| `Backend: unicodedata-NFC (fallback)` | `indic-nlp-library` missing. `.venv/bin/python3 -m pip install indic-nlp-library`. |
| `jupyter lab` → command not found | Metapackage without a frontend. Install `jupyterlab` (step 6). |
| UI code edits do nothing | `debug=False`; restart the process. |
| Crawl returned short bodies, no error | A publisher redesign. Check the `extractor` column in `/data/web` — `generic` means that site's selector has drifted. |
| Research stories differ from the webapp's | `vendor/engine.py` is a snapshot; the webapp's prompt has moved on. Copy the change into `vendor/` if research should follow it. |

---

## Where things live

```
research/                       <- this repo: TechieTripathi/hindi-railway-web-rsb
├── web_search/  rsb_search/      one folder per pipeline (config + pipeline)
├── shared/                       Devanagari, patches, grounded metrics, key loading
├── vendor/                       copies of the webapp's engine, CRIS parsers, normalizers
├── notebooks/                    the four research notebooks
├── ui.py  ui_templates/          control panel on :5001, drives both pipelines
├── experiment.py                 run either pipeline with config overrides
├── docs/                         all documentation (this file included)
└── output/                       scraped corpora, scores, logs — gitignored
```

---

## What is and is not committed

Ignored (see `.gitignore`): `.env`, `.venv/`, `__pycache__/`, `.ipynb_checkpoints/`,
`*.log`, and `output/` — which holds the scraped corpus, the AI output, the grounded
scores, per-run logs and pre-crawl backups.

> `output/` is the only copy of the crawled corpus and has been lost once. The control
> panel copies the crawl file to `output/backups/` before each new crawl (last 10 kept),
> but that directory is gitignored too — keep a backup elsewhere before anything
> destructive.

**Never commit a key.** Before your first push:

```bash
git grep -nE 'sk-ant|BSA[A-Za-z0-9_-]{20}' $(git rev-list --all) || echo "history clean"
```
