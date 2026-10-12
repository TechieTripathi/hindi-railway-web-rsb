# Hindi Railway News — Research

Research pipelines for Hindi railway news. Two crawlers collect articles: one from six
Hindi news publishers, the other from the official Indian Railways zone press-release
portals (the Hindi editions). Claude rewrites each story bilingually, and every story is
then scored against its source with deterministic checks, not by asking the model to grade
itself. A small web control panel (`ui.py`) runs every step with one click.

This repo is self-contained. It runs on **Windows, macOS and Linux**, and needs nothing
outside this folder.

---

## What you need

| | |
|---|---|
| **Python 3.9 or newer** | Tested on 3.9, 3.10, 3.12 and 3.13. 3.13 is recommended. |
| **git** | To clone the repo. |
| **An Anthropic API key** | Only for the "Write stories with AI" step. Crawling, scoring and the tests work without one. Get a key at [console.anthropic.com](https://console.anthropic.com/). |

---

## 1. Install Python and git

<details open>
<summary><b>Windows</b></summary>

Install both with `winget` (built into Windows 10 and 11), from **PowerShell**:

```powershell
winget install Python.Python.3.13
winget install Git.Git
```

Or download Python from [python.org](https://www.python.org/downloads/windows/). In the
installer, **tick "Add python.exe to PATH"**.

Close the terminal and open a new one, then check:

```powershell
py --version        # Python 3.13.x
git --version
```

</details>

<details>
<summary><b>macOS</b></summary>

With [Homebrew](https://brew.sh/):

```bash
brew install python@3.13 git
python3 --version
```

Or use the installer from [python.org](https://www.python.org/downloads/macos/).

</details>

<details>
<summary><b>Ubuntu / Debian</b></summary>

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip git
python3 --version   # 3.10 on Ubuntu 22.04, 3.12 on 24.04 — both work
```

`python3-venv` is a separate package on Debian and Ubuntu. Without it, step 3 fails with
`ensurepip is not available`.

</details>

<details>
<summary><b>Fedora / RHEL</b></summary>

```bash
sudo dnf install python3 git
```

</details>

---

## 2. Get the code

Same on every OS:

```bash
git clone https://github.com/TechieTripathi/hindi-railway-web-rsb.git research
cd research
```

The folder can have any name. **Run every command below from inside it.**

---

## 3. Create a virtual environment and install

A virtual environment (`.venv`) keeps this project's packages separate from the rest of
your system.

**Create it** (once):

| OS | Command |
|---|---|
| Windows | `py -m venv .venv` |
| macOS / Linux | `python3 -m venv .venv` |

**Activate it.** Do this in every new terminal you open for this project:

| OS / shell | Command |
|---|---|
| Windows — PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows — Command Prompt | `.venv\Scripts\activate.bat` |
| macOS / Linux | `source .venv/bin/activate` |

Your prompt now starts with `(.venv)`. From here on, **commands are the same on every OS**:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-research.txt
```

> **PowerShell says "running scripts is disabled on this system"?** Allow local scripts
> once, then activate again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

> **Prefer not to activate?** Call the venv's Python directly instead of `python`:
> `.venv\Scripts\python` on Windows, `.venv/bin/python` on macOS and Linux.

Always use `python -m pip`, not plain `pip`. It is guaranteed to install into the
Python you are actually running.

---

## 4. Add your API key

Copy the template:

| OS / shell | Command |
|---|---|
| Windows — PowerShell | `Copy-Item .env.example .env` |
| Windows — Command Prompt | `copy .env.example .env` |
| macOS / Linux | `cp .env.example .env` |

Open `.env` in any text editor and fill in the key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is gitignored, so the key is never committed. A key exported in your shell takes
priority over `.env`. Check which key will be used (the value itself is never printed):

```bash
python shared/config_env.py
# ANTHROPIC_API_KEY  set, 108 chars  (.env)
```

`BRAVE_API_KEY` in the template is not used by either pipeline. Leave it empty.

---

## 5. Check the install

No network access and no API spend:

```bash
python test_hindi_text.py                  # -> 10 passed, 0 failed
python -m unittest test_article_selection  # -> Ran 14 tests ... OK
python shared/hindi_patches.py             # first line: Backend: indic-nlp-library
```

`10 passed` is for a fresh clone. Once you have crawled data in `output/`, one test,
`test_real_corpus_gate`, also checks it and can fail after a re-crawl. That reflects the
data, not your install (see [docs/SETUP.md](docs/SETUP.md#4-check-the-install--no-network-no-api-spend)).

If the last command prints `Backend: unicodedata-NFC (fallback)`, the
`indic-nlp-library` package did not install. Hindi text still works, but deduplication is
slightly less accurate. Re-run step 3.

---

## 6. Run the control panel

```bash
python ui.py
```

Open **<http://127.0.0.1:5001>** in your browser. Pick a pipeline and run the steps in
order. Nothing runs until you click, and only **"Write stories with AI"** uses your API
key (and costs money). Everything is written to `output/`.

Stop the server with **Ctrl+C**. After editing any code, restart it — auto-reload is off
on purpose.

---

## 7. Notebooks (optional)

**VS Code:** open any file in `notebooks/` and choose `.venv` as the kernel. Nothing else
to install.

**In the browser:**

```bash
python -m jupyter lab
```

Every `RUN_*` flag at the top of a notebook defaults to `False`, so opening one makes no
network or API calls.

---

## Windows notes

- **Turn on UTF-8 mode.** The pipelines print a lot of Hindi. When their output is
  redirected to a file, Windows Python falls back to a legacy encoding and can fail with
  `UnicodeEncodeError`. Set this once and open a new terminal:
  ```powershell
  setx PYTHONUTF8 1
  ```
  The control panel copes either way. This matters when you run a pipeline script
  yourself, e.g. `python rsb_search\pipeline.py > crawl.log`.
- **Use Windows Terminal** (the default on Windows 11). The old console window may show
  Devanagari as empty boxes. That is a font problem only; the saved data is fine.
- Forward slashes in paths (`python shared/config_env.py`) work in PowerShell and Command
  Prompt alike.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` / `py` / `python3` not found | Python is not on your PATH. Windows: reinstall and tick "Add python.exe to PATH", then open a new terminal. |
| `ensurepip is not available` (Ubuntu / Debian) | `sudo apt install python3-venv`, delete `.venv`, and repeat step 3. |
| `ModuleNotFoundError: No module named 'flask'` (or `bs4`, `httpx`, `indicnlp`) | The venv is not active, or the install went to another Python. Activate `.venv` and re-run `python -m pip install -r requirements-research.txt`. |
| `ANTHROPIC_API_KEY is not set` | Check that `.env` exists in this folder and contains the key. `python shared/config_env.py` shows what was found. |
| `Address already in use` / port 5001 busy | The panel is already running in another terminal. Find the process: Windows `netstat -ano \| findstr :5001` · macOS `lsof -i :5001` · Linux `ss -ltnp \| grep 5001`. |
| `UnicodeEncodeError` on Windows | Turn on UTF-8 mode (see Windows notes). |
| `jupyter lab`: command not found | Use `python -m jupyter lab`, with the venv active. |
| Code edits have no effect in the panel | Restart `python ui.py`. |

---

## Project layout

```
web_search/        crawler for the six Hindi news publishers
rsb_search/        crawler for the railway zone press-release portals (Hindi edition)
shared/            Hindi text handling, grounded scoring, API-key loading, run provenance
vendor/            copies of the original webapp code this project builds on
notebooks/         four research notebooks
ui.py              the control panel (http://127.0.0.1:5001)
experiment.py      run a pipeline with config overrides into its own run folder
docs/              full documentation
output/            crawled data, AI output, scores, logs (gitignored, created on first run)
```

## More documentation

- [docs/SETUP.md](docs/SETUP.md): a fuller setup guide, with expected output for each step
- [docs/research.md](docs/research.md): every module, and why it exists
- [docs/METRICS.md](docs/METRICS.md): how every score is calculated
- [docs/README.md](docs/README.md): index of all documents
