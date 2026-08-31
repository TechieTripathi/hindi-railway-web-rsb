# Setup

Every command below was run against this checkout. Where a command prints something
worth checking, the real output is shown.

Run everything from the **repository root**.

---

## 0. Prerequisites

| | |
|---|---|
| Python | **3.13.12** is what this checkout is verified on. `research/` uses `from __future__ import annotations`, so 3.9+ should work — untested. (The root README's "Python 3.6+" is stale.) |
| git | any recent version |
| API keys | Anthropic (required for story generation) · Brave (only for the webapp's search path) |

```bash
python3 -V          # -> Python 3.13.12
```

---

## 1. Get the code

```bash
git clone https://github.com/aimonitors25/railway-agent.git
cd railway-agent
```

---

## 2. Create the virtual environment and install

```bash
python3 -m venv .venv
.venv/bin/python3 -m pip install --upgrade pip
.venv/bin/python3 -m pip install -r requirements.txt                    # the webapp
.venv/bin/python3 -m pip install -r research/requirements-research.txt  # research/ + its UI
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

Both requirement files resolve cleanly in a fresh venv; `research/requirements-research.txt`
is deliberately standalone, so `research/` installs without the root file.

There is no need to `source .venv/bin/activate`. Calling `.venv/bin/python3` directly is
equivalent and cannot be forgotten halfway through a session.

---

## 3. Add your API keys

```bash
cp .env.example .env
$EDITOR .env            # fill in ANTHROPIC_API_KEY and BRAVE_API_KEY
```

`config.py` calls `load_dotenv()` on this file at import time, so **every** entry point
picks the keys up — `app.py`, `engine.py`, `research/ui.py` and the notebooks alike. You do
not need to export anything.

`.env` is gitignored. Verify it before your first commit:

```bash
git check-ignore -v .env
# -> .gitignore:22:.env	.env
```

**Optional — a separate key for research runs.** `research/.env` overrides the root `.env`
for research code only, so experiments can run on a different (or rate-limited) key without
touching the webapp. Leave its values empty and the root key is used. See
`research/shared/config_env.py`. Check which key is actually in play:

```bash
.venv/bin/python3 research/shared/config_env.py
# ANTHROPIC_API_KEY  set, 108 chars  (research/.env)
# BRAVE_API_KEY      set, 31 chars   (research/.env)
```

Precedence, highest first: a shell `export` → `research/.env` → the root `.env`.

---

## 4. Check the install — no network, no API spend

```bash
.venv/bin/python3 research/test_hindi_text.py
# -> 10 passed, 0 failed

.venv/bin/python3 research/shared/hindi_patches.py
# Backend: indic-nlp-library
#   distinct dedup keys      2 -> 5   (of 5 titles)
#   titles with <3 words     5 -> 0
#   grouping similar_count   2
#   nukta variants hash equal True

.venv/bin/python3 -c "import app; print('webapp imports OK')"
```

`Backend: indic-nlp-library` is the line to look for. If it says
`unicodedata-NFC (fallback)`, `indic-nlp-library` did not install — Devanagari still
normalises, but zero-width joiners survive and some dedup keys will differ.

If a corpus already exists under `research/output/`, this also works offline and is
repeatable:

```bash
.venv/bin/python3 research/shared/score_report.py
```

---

## 5. Run the webapp

```bash
.venv/bin/python3 app.py
```

→ **http://127.0.0.1:5000**

---

## 6. Run the research control panel

```bash
.venv/bin/python3 research/ui.py
```

→ **http://127.0.0.1:5001** · run history at `/logs` · articles at `/data/web`

Different port, so it runs alongside `app.py`. It writes only under `research/output/` and
never touches the project-root JSON files. Nothing runs until you click; only step 2
("Write stories with AI") spends money.

> `debug=False` is deliberate — Flask's reloader imports the module twice and would
> double-apply the Devanagari patches. **Restart the process to pick up code changes.**

---

## 7. Notebooks (optional)

In **VS Code**, just open a `.ipynb` and select `.venv` as the kernel — `ipykernel` is
enough, no extra install.

For a browser frontend, note that `jupyter` alone is a metapackage; a partial install
leaves only `jupyter-core` and `jupyter lab` reports "command not found":

```bash
.venv/bin/python3 -m pip install jupyterlab
.venv/bin/python3 -m jupyter lab
```

All four `RUN_*` flags default to `False`, so opening a notebook fires no network or API
calls.

---

## 8. Optional: the webapp's own test

`pytest` is not in either requirements file and is not installed here:

```bash
.venv/bin/python3 -m pip install pytest
.venv/bin/python3 -m pytest -q tests/
```

(The root README's pytest command hard-codes an absolute path from a different machine —
use the line above instead.)

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `bad interpreter: … No such file or directory` | A venv console script with a stale path. Use `.venv/bin/python3 -m <tool>`. |
| `Address already in use` / `Port 5001 is in use` | An instance is already running. `ss -ltnp \| grep 5001` to find it. |
| `ModuleNotFoundError: flask` (or `indicnlp`, `bs4`) | Requirements not installed into **this** venv. Re-run step 2 with the `.venv/bin/python3 -m pip` form. |
| `ANTHROPIC_API_KEY is not set` | No key in the shell, `research/.env` or the root `.env`. Run `research/shared/config_env.py` to see which sources were checked. |
| `Backend: unicodedata-NFC (fallback)` | `indic-nlp-library` missing. `.venv/bin/python3 -m pip install indic-nlp-library`. |
| `jupyter lab` → command not found | Metapackage without a frontend. Install `jupyterlab` (step 7). |
| UI code edits do nothing | `debug=False`; restart the process. |
| `git add -A` → `research/ does not have a commit checked out` | A nested `research/.git` exists, so git treats `research/` as a submodule and refuses to index it. Either fold it into this repo (`rm -rf research/.git`) or commit inside it separately (`git -C research add -A && git -C research commit`). |
| Crawl returned short bodies, no error | A publisher redesign. Check the `extractor` column in `/data/web` — `generic` means that site's selector has drifted. |

---

## Where things live

```
app.py config.py engine.py …   the webapp (flat, at the repo root)
templates/                     its Flask templates
research/                      the research package and its own UI on :5001
research/docs/                 all documentation (this file included)
tests/                         pytest suite, with fixtures/
data/    logs/                 generated state — gitignored
archive/                       superseded source, kept for reference
```

Commands in this guide are still run from the **repo root** (`railway-agent/`), one level
above `research/`.

`config.py` defines **every** webapp file path (`DATA_DIR`, `LOG_DIR` and the constants
built from them). Nothing else builds these paths, so relocating generated state is a
one-file change.

---

## What is and is not committed

Ignored (see `.gitignore`): `.venv/`, `__pycache__/`, `.ipynb_checkpoints/`, `*.log`,
`data/`, `logs/`, and **`.env` at any depth** — the no-slash pattern covers
`research/.env` too.

`research/.gitignore` additionally ignores `research/output/`, which holds the scraped
corpus, the AI output, the grounded scores, per-run logs and pre-crawl backups.

> `research/output/` is the only copy of the crawled corpus and has been lost once. The
> control panel copies the crawl file to `research/output/backups/` before each new crawl
> (last 10 kept), but that directory is gitignored too — keep a backup elsewhere before
> anything destructive.

**Never commit a key.** Before your first push:

```bash
git grep -nE 'sk-ant|BSA[A-Za-z0-9_-]{20}' $(git rev-list --all) || echo "history clean"
```
