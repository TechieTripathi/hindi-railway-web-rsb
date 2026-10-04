"""research/ui.py — a minimal control panel for the two research pipelines.

Why this exists
---------------
Both pipelines are multi-stage and the interesting failures are *silent*: a publisher
redesign makes `extract_body` fall through to the generic <p> sweep and you get a short
body, not an error; a zone serves the literal placeholder "Add Hindi content here";
`engine.run_generate` resumes by title and quietly generates nothing. None of that is
visible from a return value -- it is visible in the stage's stdout. So this UI runs each
stage as a separate, explicit click and captures everything it printed.

Run:
    .venv/bin/python3 research/ui.py          # http://127.0.0.1:5001

Port 5001, so it can run alongside the webapp's `app.py` on 5000.

Safety properties, same as the notebooks
----------------------------------------
* Reads/writes only `research/output/`. The project-root JSON files are never touched.
* Applies `shared.hindi_patches` at startup, so Devanagari survives the webapp's normalizers.
* Nothing runs on page load. Crawl and Generate are POSTs, one click each.
* AI Generate is the only stage that spends money; it is gated on a crawl existing and
  reports which key source it used (never the key).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import traceback

_RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_RESEARCH_DIR)
for _p in (_PROJECT_ROOT, _RESEARCH_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, jsonify, render_template, request

import engine                                        # the webapp, read-only

from rsb_search import config as config_rsb
from rsb_search import pipeline as rsb_pipeline
from shared import cluster as clustering
from shared import hindi_text
from shared import config_env, score_report
from shared.hindi_patches import apply_hindi_patches
from web_search import config as config_web
from web_search import pipeline as web_pipeline

LOG_DIR = os.path.join(config_web.OUTPUT_DIR, "logs")

# The webapp's normalizers delete Devanagari; rebind before anything Hindi runs.
apply_hindi_patches()


# --------------------------------------------------------------------- jobs

class Job:
    """One stage run, with its captured output."""

    _seq = 0
    _seq_lock = threading.Lock()

    def __init__(self, pipeline: str, step: str, params: dict):
        with Job._seq_lock:
            Job._seq += 1
            self.id = "%s-%s-%03d" % (pipeline, step, Job._seq)
        self.pipeline = pipeline
        self.step = step
        self.params = params
        self.state = "running"          # running | ok | error
        self.started = _dt.datetime.now()
        self.finished = None
        self.summary = ""
        self.error = ""
        self.lines: list[str] = []
        self.progress = {"phase": "starting…", "done": 0, "total": 0,
                         "kept": 0, "skipped": 0, "warnings": 0}
        self._buf = ""
        self._lock = threading.Lock()
        os.makedirs(LOG_DIR, exist_ok=True)
        self.log_name = "%s-%s-%s.log" % (
            self.started.strftime("%Y%m%d-%H%M%S"), pipeline, step)
        self._fh = open(os.path.join(LOG_DIR, self.log_name), "w",
                        encoding="utf-8", buffering=1)

    # -- output capture -----------------------------------------------------
    def feed(self, text: str) -> None:
        """Buffer partial writes and commit whole lines."""
        with self._lock:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._commit(line)

    def _commit(self, line: str) -> None:
        self.lines.append(line)
        self._track(line)
        try:
            self._fh.write(line + "\n")
        except ValueError:              # file already closed
            pass

    #: Live counters, parsed from the stage's own output. The stages already print
    #: exactly what a progress bar needs, so nothing had to be instrumented.
    _RE_SOURCE   = re.compile(r"^=== (.+?) ===$")
    _RE_ZONE     = re.compile(r"^=== (\w+) — (.+) ===$")
    _RE_OK       = re.compile(r"^\s+OK\s")
    _RE_SKIP     = re.compile(r"^\s+skip \((.+?)\)")
    _RE_GEN      = re.compile(r"^AI generating (\d+)/(\d+): (.*)$")
    _RE_WROTE    = re.compile(r"^Wrote (\d+) article")

    def _track(self, line: str) -> None:
        m = self._RE_GEN.match(line)
        if m:
            self.progress.update(done=int(m.group(1)), total=int(m.group(2)),
                                 phase="writing story for “%s…”" % m.group(3)[:44])
            return
        m = self._RE_SOURCE.match(line)
        if m:
            self.progress["done"] += 1
            label = m.group(1)
            if " — " in label:
                label = label.split(" — ", 1)[1]
            self.progress["phase"] = "reading %s" % label
            return
        if self._RE_OK.match(line):
            self.progress["kept"] += 1
        elif self._RE_SKIP.match(line):
            self.progress["skipped"] += 1
        if "WARNING" in line or "FAILED" in line or "Traceback" in line:
            self.progress["warnings"] += 1

    def close(self) -> None:
        with self._lock:
            if self._buf:
                self._commit(self._buf)
                self._buf = ""
            try:
                self._fh.close()
            except ValueError:
                pass
        # Outcome sidecar. Until now only the raw log text survived a restart;
        # state, summary, params and duration lived in JOBS (memory) and were
        # gone on the next start — so the landing page and the "Last run" line
        # emptied while /logs still listed the files. `close()` runs in the
        # worker's `finally`, after state/error are settled, so this is final.
        try:
            with open(_run_meta_path(self.log_name), "w", encoding="utf-8") as fh:
                json.dump(self.brief(), fh, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def log(self, line: str) -> None:
        self.feed(line + "\n")

    # -- reporting ----------------------------------------------------------
    def tail(self, offset: int = 0) -> dict:
        with self._lock:
            return {
                "id": self.id, "state": self.state, "offset": len(self.lines),
                "lines": self.lines[offset:], "summary": self.summary,
                "error": self.error, "log_name": self.log_name,
                "progress": dict(self.progress),
                "step_label": STEP_META[self.step]["label"],
                "pipeline": self.pipeline,
                "elapsed": round(((self.finished or _dt.datetime.now())
                                  - self.started).total_seconds(), 1),
            }

    def brief(self) -> dict:
        return {
            "id": self.id, "pipeline": self.pipeline, "step": self.step,
            "step_label": STEP_META[self.step]["label"],
            "state": self.state, "summary": self.summary, "error": self.error,
            "params": self.params, "log_name": self.log_name,
            "started": self.started.strftime("%H:%M:%S"),
            "when": self.started.strftime("%Y-%m-%d %H:%M"),
            "seconds": round(((self.finished or _dt.datetime.now())
                              - self.started).total_seconds(), 1),
        }


def _run_meta_path(log_name: str) -> str:
    return os.path.join(LOG_DIR, log_name[:-4] + ".json")


def _parse_log_name(name: str) -> dict:
    """`YYYYMMDD-HHMMSS-<pipeline>-<step>.log` -> its parts.

    The step is the LAST token and the pipeline is everything between, so a
    future pipeline name containing a hyphen still parses.
    """
    stem = name[:-4] if name.endswith(".log") else name
    parts = stem.split("-")
    if len(parts) < 4:
        return {"date": "", "time": "", "pipeline": "", "step": "", "stem": stem}
    return {"date": parts[0], "time": parts[1], "pipeline": "-".join(parts[2:-1]),
            "step": parts[-1], "stem": stem}


def _run_brief_from_disk(log_name: str) -> dict:
    """A `Job.brief()`-shaped record for a run this process did not perform.

    Prefers the outcome sidecar. A log written before sidecars existed still
    gets a record — parsed from its file name — marked `recorded`, so old runs
    are listed rather than silently dropped.
    """
    try:
        with open(_run_meta_path(log_name), encoding="utf-8") as fh:
            b = json.load(fh)
            b.setdefault("log_name", log_name)
            return b
    except (OSError, ValueError):
        pass
    m = _parse_log_name(log_name)
    started = ""
    when = ""
    try:
        t = _dt.datetime.strptime(m["date"] + m["time"], "%Y%m%d%H%M%S")
        started, when = t.strftime("%H:%M:%S"), t.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass
    return {"id": m["stem"], "pipeline": m["pipeline"], "step": m["step"],
            "step_label": STEP_META.get(m["step"], {}).get("label", m["step"] or "run"),
            "state": "recorded", "summary": "", "error": "", "params": {},
            "log_name": log_name, "started": started, "when": when, "seconds": "?"}


def _recent(limit: int = 8) -> list[dict]:
    """Runs newest first, from DISK, so history survives a restart and is shared
    across processes. In-memory jobs are merged in first because they include
    the one currently running, which has no sidecar yet."""
    out, seen = [], set()
    for jid in JOB_ORDER:
        b = JOBS[jid].brief()
        out.append(b)
        seen.add(b["log_name"])
    if os.path.isdir(LOG_DIR):
        for n in os.listdir(LOG_DIR):
            if n.endswith(".log") and n not in seen:
                out.append(_run_brief_from_disk(n))
    out.sort(key=lambda b: b.get("log_name", ""), reverse=True)   # name leads with the timestamp
    return out[:limit] if limit else out


class _Tee(io.TextIOBase):
    """stdout replacement: to the terminal AND into the running job."""

    def __init__(self, job: Job, real):
        self._job, self._real = job, real

    def write(self, s):
        try:
            self._real.write(s)
        except Exception:
            pass
        self._job.feed(s)
        return len(s)

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass

    def isatty(self):
        return False


JOBS: dict[str, Job] = {}
JOB_ORDER: list[str] = []
_RUN_LOCK = threading.Lock()            # one stage at a time, so the tee is unambiguous
CURRENT: Job | None = None


def _run_async(pipeline: str, step: str, params: dict) -> Job:
    job = Job(pipeline, step, params)
    if step == "crawl":
        # A crawl visits one source/zone per "=== … ===" line, so the count of
        # configured sources is the denominator the progress bar needs. Generate
        # prints its own "i/total"; score is too fast to bother.
        job.progress["total"] = _crawl_units(pipeline)
    JOBS[job.id] = job
    JOB_ORDER.insert(0, job.id)

    def worker():
        global CURRENT
        with _RUN_LOCK:
            CURRENT = job
            real_out, real_err = sys.stdout, sys.stderr
            tee = _Tee(job, real_out)
            sys.stdout = sys.stderr = tee
            try:
                job.summary = STEPS[(pipeline, step)](job, params)
                job.state = "ok"
            except Exception:
                job.state = "error"
                job.error = traceback.format_exc(limit=8).strip()
                job.feed("\n--- FAILED ---\n" + job.error + "\n")
            finally:
                sys.stdout, sys.stderr = real_out, real_err
                job.finished = _dt.datetime.now()
                job.close()
                CURRENT = None

    threading.Thread(target=worker, name=job.id, daemon=True).start()
    return job


# ---------------------------------------------------------------- artifacts

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return None    # missing, empty and malformed are all "nothing usable"


def _artifact(path: str) -> dict:
    data = _read_json(path)
    info = {"path": path, "name": os.path.basename(path),
            "exists": os.path.exists(path), "count": None, "when": "", "note": ""}
    if not info["exists"]:
        return info
    info["when"] = _dt.datetime.fromtimestamp(
        os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    if data is None:
        info["note"] = "unreadable (empty or malformed)"
        return info
    info["count"] = len(data)
    return info


def _generated_count(path: str) -> int:
    """Articles with a real story -- engine writes failure placeholders too.

    Defers to `engine._is_real_story` rather than re-testing the prefix here:
    the UI counting a "Generation failed…" row as a success is what made the
    resume bug invisible from the control panel.
    """
    data = _read_json(path) or []
    return sum(1 for a in data if engine._is_real_story(a.get("ai_news_story")))


BACKUP_DIR = os.path.join(config_web.OUTPUT_DIR, "backups")
KEEP_BACKUPS = 10


def _backup(path: str, job: "Job | None") -> None:
    """Copy an output aside before a new crawl replaces or clears it.

    `research/output/` is the only copy of the corpus and README records it being lost
    once. A crawl is destructive: `run_*_crawl` opens the output file with "w".
    """
    if not os.path.exists(path):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    base = os.path.basename(path)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, "%s.%s.json" % (base[:-5] if base.endswith(".json") else base, stamp))
    with open(path, "rb") as src, open(dest, "wb") as out:
        out.write(src.read())
    (job.log if job else print)("backed up %d article(s) -> %s"
            % (len(_read_json(path) or []), os.path.relpath(dest, _PROJECT_ROOT)))

    prefix = base[:-5] if base.endswith(".json") else base
    old = sorted(n for n in os.listdir(BACKUP_DIR) if n.startswith(prefix + "."))
    for n in old[:-KEEP_BACKUPS]:
        os.remove(os.path.join(BACKUP_DIR, n))


#: Every pipeline declares its own behaviour here. The view code dispatches on
#: these fields and raises on an unknown value — it never falls back to another
#: pipeline's behaviour. Before this, four helpers read `if pipeline == "rsb"`
#: and otherwise did what the web pipeline does, so a third pipeline would have
#: silently crawled with the web pipeline's source list.
#:
#:   source_kind         how `_selectable` lists what can be crawled
#:                       ("publishers" | "zones"; add a branch for a new kind)
#:   source_word_plural  the noun the UI uses for those units
#:   number              display order on the landing page
#:   icon                one glyph, so the cards can be told apart at a glance
PIPELINES = {
    "web": {
        "number": 1, "icon": "📰",
        "source_kind": "publishers", "source_word_plural": "publishers",
        "title": "Hindi news publishers",
        "friendly": "Railway news in Hindi from six publishers — AajTak, Jansatta, "
                    "Navbharat Times, Amar Ujala, Live Hindustan and PIB.",
        "step_what": {"crawl": "Visits each publisher's own topic page and downloads "
                               "the full article text. No AI, no search engine, "
                               "nothing is charged."},
        "blurb": "Direct crawl of six publisher tag pages. Skips Google News entirely, "
                 "whose encrypted redirects resolved for 0 of 18 Hindi articles.",
        "cfg": config_web,
        "params": [
            # One control, not two: the toggle and its number are the same
            # setting ("limit per publisher -> how many"). As separate row items
            # they wrapped onto different lines and read as unrelated.
            {"type": "cap", "toggle_key": "limit_on", "number_key": "max_per_source",
             "label": "Limit how many per publisher", "unit": "per publisher",
             "toggle_default": config_web.LIMIT_PER_SOURCE,
             "number_default": config_web.MAX_PER_SOURCE},
        ],
    },
    "rsb": {
        "number": 2, "icon": "🏛️",
        "source_kind": "zones", "source_word_plural": "zones",
        "title": "Official railway press releases",
        "friendly": "Press releases straight from Indian Railways' own zone websites, "
                    "Hindi edition.",
        "step_what": {"crawl": "Reads the Hindi press-release listing of each railway "
                               "zone and downloads the full release. No AI, no search "
                               "engine, nothing is charged."},
        "blurb": "Hindi edition (lang=1). config.ZONES points at lang=0, the English "
                 "edition: 18 releases, 0 Hindi titles.",
        "cfg": config_rsb,
        "params": [
            {"type": "cap", "toggle_key": "limit_on", "number_key": "per_zone",
             "label": "Limit how many per zone", "unit": "per zone",
             "toggle_default": config_rsb.LIMIT_PER_ZONE,
             "number_default": config_rsb.PER_ZONE},
            {"key": "hindi_only", "label": "Skip releases that are not really Hindi",
             "type": "checkbox", "default": config_rsb.HINDI_ONLY},
        ],
    },
}


# ---------------------------------------------------------- stage copy

#: Plain-language description of each stage, for people who did not write the
#: pipeline. `costs_money` gates a confirmation dialog in the browser.
STEP_META = {
    "crawl": {
        "n": 1,
        "label": "Fetch articles",
        "button": "Fetch",
        "verb": "Fetching…",
        # Overridden per pipeline below — the two crawlers read very different things.
        "what": "Downloads the full text of each article. No AI, no search engine, "
                "nothing is charged.",
        "produces": "a list of articles with their Hindi body text",
        "time": "about 1–3 minutes",
        "costs_money": False,
    },
    "generate": {
        "n": 2,
        "label": "Write stories with AI",
        "button": "Write stories",
        "verb": "Writing…",
        "what": "Sends each article to Claude, which rewrites it as an original story "
                "plus a translation and three social posts.",
        "produces": "a story, a translation and social posts per article",
        "time": "about 10–20 seconds per article",
        "costs_money": True,
    },
    "score": {
        "n": 3,
        "label": "Check quality",
        "button": "Check quality",
        "verb": "Checking…",
        "what": "Compares each story against its source — which figures it carried "
                "over, whether its topics have evidence. Offline and repeatable.",
        "produces": "coverage, precision and gate results per article",
        "time": "a second or two",
        "costs_money": False,
    },
}

#: How many reports a run produces is decided here, not by a fixed number.
#: Labels say what HAPPENS to the articles, in everyday words — a reader who has
#: never seen this tool should not have to know what "cluster" or "topic mode"
#: means. The report/call counts are deliberately NOT in the label: they live in
#: the hint under the field, where they can be styled and can say "no articles
#: yet" instead of three identical zeros.
CLUSTER_MODES = [
    {"value": "off", "label": "Keep every article separate",
     "hint": "No merging. Ten articles become ten reports, and ten AI calls — "
             "the most expensive option, and the right one when each article is "
             "genuinely its own story."},
    {"value": "story", "label": "Merge articles about the same event (recommended)",
     "hint": "When two publishers cover one announcement, you get ONE report with "
             "both articles attached, so you can check the AI's story against both. "
             "Slightly cheaper than keeping everything separate."},
    {"value": "topic", "label": "Merge everything on a topic — makes a digest",
     "hint": "Cheapest, and the bluntest: every article sharing a broad subject is "
             "merged, even when the stories are unrelated. Measured on real data "
             "that put six different stories — ticket booking, seat rules, theft — "
             "into one report. Good for a digest, not for a news article."},
]

#: How user keywords combine with the built-in railway relevance test. Mirrors
#: `hindi_text.KEYWORD_MODES`; the labels are what the operator actually reads.
KEYWORD_MODES = [
    {"value": "narrow", "label": "within railway news (recommended)",
     "hint": "Keep an article only if it is railway news AND matches a keyword. "
             "Use this to follow one topic — e.g. वंदे भारत — inside the usual crawl."},
    {"value": "only", "label": "keywords only — ignore the railway test",
     "hint": "Keep anything matching a keyword, even if the built-in railway test "
             "rejects it. Use when the keywords ARE the topic and the railway "
             "vocabulary would wrongly exclude it."},
]

#: Day-window choices offered in the UI. "" means "use the config default".
DAY_CHOICES = [
    {"value": "", "label": "config default (%d days)"},   # filled in per pipeline
    {"value": "1", "label": "today only"},
    {"value": "3", "label": "last 3 days"},
    {"value": "7", "label": "last 7 days"},
    {"value": "10", "label": "last 10 days"},
    {"value": "30", "label": "last 30 days"},
    {"value": "0", "label": "no date limit"},
]


def _day_choices(pipeline: str) -> list[dict]:
    cfg = PIPELINES[pipeline]["cfg"]
    out = []
    for c in DAY_CHOICES:
        label = c["label"] % cfg.DAYS_BACK if "%d" in c["label"] else c["label"]
        out.append({"value": c["value"], "label": label})
    return out


def _selectable(pipeline: str) -> list[dict]:
    """The publishers / zones a run may be restricted to.

    Both lists come from config, not from a hardcoded set: the web sources from
    hindi_railway_sources.json, the zones from config_rsb.ZONES (all 17, not just
    the 4 in DEFAULT_ZONE_CODES).
    """
    kind = PIPELINES[pipeline]["source_kind"]
    if kind == "zones":
        default = set(config_rsb.DEFAULT_ZONE_CODES)
        cover = getattr(config_rsb, "HINDI_COVERAGE", {})
        out = []
        for z in config_rsb.ZONES:
            info = cover.get(z["c"], {})
            usable = info.get("usable")
            out.append({
                "value": z["c"], "label": "%s — %s" % (z["c"], z["n"]),
                "checked": z["c"] in default,
                "note": "" if usable is True else
                        ("Hindi placeholder only" if usable is False else
                         "PDF only" if usable == "pdf_only" else "unmeasured"),
            })
        return out
    if kind == "publishers":
        cfg = _read_json(config_web.SOURCE_CONFIG_FILE) or {}
        group = cfg.get("source_groups", {}).get(config_web.SOURCE_GROUP, {})
        return [{"value": s["name"], "label": s["name"], "checked": True,
                 "note": "%d listing page(s)" % len(s.get("start_urls") or [])}
                for s in group.get("sources", [])]
    # A new pipeline must declare how its sources are listed. Falling through to
    # the publisher branch would silently hand it the WEB pipeline's config.
    raise NotImplementedError(
        "pipeline %r has source_kind %r with no _selectable branch" % (pipeline, kind))


#: Sources/zones a crawl will visit, so the progress bar has a denominator.
def _crawl_units(pipeline: str) -> int:
    return len(_selectable(pipeline))


# ------------------------------------------------------------- data browser

STAGES = {"crawl": "CRAWL_OUTPUT", "agency": "AGENCY_OUTPUT", "scored": "SCORED_OUTPUT"}
#: One name per stage, mirroring the STEP that produces it ("Fetch articles" ->
#: "fetched"). Before this there were four competing schemes for the same three
#: things — "crawled/AI generated/scored" here, "Crawled/AI written/Quality
#: checked" in STAGE_META, "Fetch/Write/Check" in STEP_META and "1 · Crawl /
#: 2 · AI Generate / 3 · Grounded Score" in `_state` — so a reader could not
#: tell whether they were three stages or a dozen.
STAGE_LABEL = {"crawl": "fetched", "agency": "AI written", "scored": "quality checked"}

#: The three stages as a PIPELINE, for the Browse-articles selector: a step
#: number, a plain-language name, one line saying what the stage actually holds,
#: and a colour. A reviewer who does not know the codebase should be able to read
#: the selector as "1 -> 2 -> 3" and know where the data came from.
STAGE_META = {
    "crawl":  {"n": 1, "name": "Fetched",     "tone": "crawl",
               "what": "the publisher's own text, exactly as downloaded. No AI."},
    "agency": {"n": 2, "name": "AI written",  "tone": "story",
               "what": "each article rewritten by the AI, plus a translation and social posts."},
    "scored": {"n": 3, "name": "Quality checked", "tone": "ok",
               "what": "the AI stories with their fact, coverage and plagiarism scores measured."},
}
STEP_STAGE = {"crawl": "crawl", "generate": "agency", "score": "scored"}
SNIPPET_CHARS = 170


def _stage_path(pipeline: str, stage: str) -> str:
    return getattr(PIPELINES[pipeline]["cfg"], STAGES[stage])


def _listing_fallback(pipeline: str):
    """source/zone -> listing URL, for articles crawled before `listing_url` existed."""
    out = {}
    if PIPELINES[pipeline]["source_kind"] == "zones":
        for z in config_rsb.ZONES:
            out[z["c"]] = z["u"]
            out["%s (RSB Hindi)" % z["n"]] = z["u"]
        return out
    cfg = _read_json(config_web.SOURCE_CONFIG_FILE) or {}
    group = cfg.get("source_groups", {}).get(config_web.SOURCE_GROUP, {})
    for src in group.get("sources", []):
        urls = src.get("start_urls") or []
        if urls:
            out[src["name"]] = urls[0] if len(urls) == 1 else "%s (+%d more)" % (
                urls[0], len(urls) - 1)
    return out


def _listing_for(article: dict, fallback: dict) -> tuple[str, bool]:
    """(listing url, is_recorded). Recorded values are exact; fallbacks are a guess."""
    if article.get("listing_url"):
        return article["listing_url"], True
    guess = fallback.get(article.get("zone_code") or "") \
        or fallback.get(article.get("source") or "")
    return guess or "", False


#: Suffixes that repeat on every row and cost the column four lines of width.
#: Deliberately narrow — stripping a bare "Railway" would turn the zone name
#: "Central Railway" into "Central".
_SOURCE_NOISE = [
    re.compile(r"\s*\(RSB Hindi\)$", re.I),
    re.compile(r"\s+Indian Railways$", re.I),
    re.compile(r"\s+Press Releases?$", re.I),
]


def _short_source(name: str) -> str:
    """"Live Hindustan Indian Railways" -> "Live Hindustan". Full name in a tooltip."""
    short = name or ""
    for pattern in _SOURCE_NOISE:
        short = pattern.sub("", short)
    return short.strip() or (name or "")


def _short_url(url: str, n: int = 44) -> str:
    """Host and path, elided to `n` chars. Full URL goes in the link's title attribute.

    Two reasons this is not a plain truncation:

    * Without shortening, the table's two URL columns wrap and rows grow to ~200px.
    * The query string is dropped rather than elided. Keeping its tail printed an
      identical meaningless hex suffix on every RSB row (the distinguishing `dcd`
      parameter sits mid-query), which read as duplicate links. A trailing "?…"
      marks that parameters were dropped.
    """
    if not url:
        return ""
    trimmed = re.sub(r"^https?://(www\.)?", "", url)
    base, sep, _query = trimmed.partition("?")
    suffix = "?…" if sep else ""
    room = n - len(suffix)
    if len(base) <= room:
        return base + suffix
    host, _, path = base.partition("/")
    if not path:
        return base[:room] + "…"
    keep = max(8, room - len(host) - 2)
    return "%s/…%s%s" % (host, path[-keep:], suffix)


def _snippet(body: str, n: int = SNIPPET_CHARS) -> str:
    text = " ".join((body or "").split())
    return text[:n] + ("…" if len(text) > n else "")


def _has_story(article: dict) -> bool:
    """Same test the pipeline uses, so the table cannot disagree with the run."""
    return engine._is_real_story(article.get("ai_news_story"))


#: `engine._compute_plagiarism` returns a score of 0 in two very different cases —
#: a genuinely original story, and one it could not compare at all (either text
#: under 30 chars, or fewer than 4 words in the story). Only the detail string
#: tells them apart, so the list view has to read it to avoid showing a
#: not-comparable article as a perfect 0.
_PLAGIARISM_NA_DETAILS = (
    "Insufficient text for comparison.",
    "AI text too short for n-gram analysis.",
)


def _plagiarism_not_comparable(article: dict) -> bool:
    """True when the 0 on this article means "could not compare", not "original"."""
    if article.get("plagiarism_score"):
        return False
    return (article.get("plagiarism_detail") or "") in _PLAGIARISM_NA_DETAILS


def _rows(pipeline: str, stage: str) -> list[dict]:
    arts = _read_json(_stage_path(pipeline, stage)) or []
    excluded = _excluded_keys(pipeline, arts) if stage == "crawl" else set()
    fallback = _listing_fallback(pipeline)
    rows = []
    for i, a in enumerate(arts):
        listing, exact = _listing_for(a, fallback)
        body = a.get("body") or ""
        rows.append({
            "i": i,
            "article_key": _article_key(a),
            "excluded": _article_key(a) in excluded,
            "source": a.get("source") or a.get("zone_name") or "",
            "source_label": _short_source(a.get("source") or a.get("zone_name") or ""),
            "listing": listing,
            "listing_label": _short_url(listing, 34),
            "listing_exact": exact,
            "link": a.get("link") or "",
            "link_label": _short_url(a.get("link") or "", 44),
            "date": a.get("date") or "",
            "title": a.get("title") or "",
            "snippet": _snippet(body),
            "chars": len(body),
            "short": len(body) < 300,
            "extractor": a.get("extractor") or "",
            "generic": a.get("extractor") == "generic",
            "body_hindi": a.get("body_hindi_ratio"),
            "is_hindi": a.get("is_hindi_body"),
            "has_story": _has_story(a),
            "fact": a.get("fact_score"),
            "coverage": a.get("coverage_score"),
            "plagiarism": a.get("plagiarism_score"),
            "plagiarism_detail": a.get("plagiarism_detail") or "",
            "plagiarism_na": _plagiarism_not_comparable(a),
            "gates_passed": a.get("gates_passed"),
        })
    return rows


#: Of `_TEXT_FIELDS`, the ones that are SOURCE text rather than model output.
#: Drives which pane each block lands in on the detail page.
_SOURCE_TEXT_KEYS = {"body"}

#: Long text rendered as its own block on the detail page, in this order.
_TEXT_FIELDS = [
    ("body", "source body (as crawled)"),
    ("ai_news_story", "AI story (Hindi)"),
    ("ai_news_story_translated", "AI story (translated)"),
    ("social_twitter", "social · twitter"),
    ("social_facebook", "social · facebook"),
    ("social_general", "social · general"),
    ("impact_reason", "impact reason"),
    ("fact_details", "fact_details (self-reported by the model)"),
    ("plagiarism_detail", "plagiarism_detail (computed locally)"),
]


def _available_stages(pipeline: str) -> list[dict]:
    out = []
    for stage in ("crawl", "agency", "scored"):
        data = _read_json(_stage_path(pipeline, stage))
        out.append(dict(STAGE_META[stage], key=stage, label=STAGE_LABEL[stage],
                        count=len(data) if data else 0))
    return out


_SELECTION_LOCK = threading.RLock()


def _article_key(article: dict) -> str:
    link = (article.get("link") or "").strip()
    identity = ["link", link] if link else [
        "fields", article.get("title") or "",
        article.get("source") or article.get("zone_name") or "",
        article.get("date") or ""]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()


def _selection_path(pipeline: str) -> str:
    return os.path.join(PIPELINES[pipeline]["cfg"].OUTPUT_DIR,
                        "%s_crawl_selection_research.json" % pipeline)


def _write_selection(pipeline: str, excluded: set[str]) -> None:
    path = _selection_path(pipeline)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"excluded_keys": sorted(excluded)}, fh, indent=2)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _excluded_keys(pipeline: str, arts: list, *, after_crawl: bool = False) -> set[str]:
    with _SELECTION_LOCK:
        path = _selection_path(pipeline)
        try:
            with open(path, encoding="utf-8") as fh:
                stored = set(json.load(fh)["excluded_keys"])
        except FileNotFoundError:
            return set()
        current = stored & {_article_key(a) for a in arts}
        job = CURRENT
        crawling = job is not None and job.pipeline == pipeline and job.step == "crawl"
        # A browser refresh can read a partially written crawl while step 1 runs.
        if current != stored and (after_crawl or not crawling):
            _write_selection(pipeline, current)
        return current


def _selection_summary(arts: list, excluded: set[str]) -> dict:
    count = sum(_article_key(a) in excluded for a in arts)
    return {"total": len(arts), "selected": len(arts) - count,
            "excluded": count, "excluded_keys": sorted(excluded)}


def _selected_articles(pipeline: str) -> list[dict]:
    arts = _read_json(_stage_path(pipeline, "crawl")) or []
    excluded = _excluded_keys(pipeline, arts)
    return [a for a in arts if _article_key(a) not in excluded]


def _mode_counts(pipeline: str) -> dict:
    """Reports each grouping mode would produce, from the current crawl file.

    Free: `build_clusters` is pure Python (title-word Jaccard), no API call —
    which is the whole point of showing it before step 2 is run. Shared with the
    `/clusters` route so the panel and the preview can never disagree.
    """
    arts = _selected_articles(pipeline)
    if not arts:
        return {m: 0 for m in clustering.MODES}
    return {m: len(clustering.build_clusters(arts, mode=m)) for m in clustering.MODES}


def _resume_key(title: str) -> str:
    """The key `engine.run_generate` resumes on. Must stay identical to it."""
    return re.sub(r"\s+", " ", (title or "").lower().strip())


def _run_estimate(pipeline: str) -> dict:
    """What step 2 would actually cost right now, per grouping mode.

    Resume is by TITLE, not by count, so this simulates it rather than
    subtracting a total: a mode's cost is the number of its reports whose lead
    title is not already carrying a real story. Subtracting counts gives
    nonsense the moment the agency file holds articles from a different mode —
    55 written against 18 reports would read as "0 calls".

    A cluster keeps its LEAD article's title (`cluster.cluster_article`), which
    is what `build_clusters` reports as `title`, so the same key applies to both
    clustered and un-clustered runs.

    The figure is a FLOOR: a first attempt whose response fails to parse has
    already been billed and is retried once (max 2 attempts per article).
    """
    cfg = PIPELINES[pipeline]["cfg"]
    arts = _selected_articles(pipeline)
    done = {_resume_key(a.get("title"))
            for a in (_read_json(cfg.AGENCY_OUTPUT) or [])
            if engine._is_real_story(a.get("ai_news_story"))}
    scored = {_resume_key(a.get("title")) for a in (_read_json(cfg.SCORED_OUTPUT) or [])}
    counts, calls, skipped, checked = {}, {}, {}, {}
    for m in clustering.MODES:
        groups = clustering.build_clusters(arts, mode=m) if arts else []
        counts[m] = len(groups)
        calls[m] = sum(1 for c in groups if _resume_key(c["title"]) not in done)
        skipped[m] = counts[m] - calls[m]
        checked[m] = sum(1 for c in groups if _resume_key(c["title"]) in done & scored)
    return {"counts": counts, "done": skipped["story"], "calls": calls,
            "skipped": skipped, "checked": checked,
            # `total` is what makes an empty pipeline explainable: zero reports
            # because zero articles is a different message from zero reports
            # because everything is already written.
            "total": len(arts)}


def _state(name: str) -> dict:
    cfg = PIPELINES[name]["cfg"]
    crawl = _artifact(cfg.CRAWL_OUTPUT)
    agency = _artifact(cfg.AGENCY_OUTPUT)
    scored = _artifact(cfg.SCORED_OUTPUT)
    # ONE read of the global. The worker thread sets CURRENT = None in its
    # `finally` the instant a job ends; reading it again lower down — as the
    # busy post-pass did — hit None between the two reads and 500'd /api/state
    # at the end of a 1-second score run. Every use below goes through `cur`.
    cur = CURRENT
    running = cur is not None and cur.pipeline == name
    busy = cur is not None
    estimate = _run_estimate(name)
    counts = {"crawl": crawl["count"], "generate": estimate["skipped"]["story"],
              "score": estimate["checked"]["story"]}
    overrides = PIPELINES[name].get("step_what", {})

    def meta(key):
        m = dict(STEP_META[key])
        if key in overrides:
            m["what"] = overrides[key]
        return m

    # The step to nudge a first-time user toward: the earliest one with no output
    # whose prerequisite is satisfied.
    next_step = None
    for key in ("crawl", "generate", "score"):
        if not counts[key]:
            prereq = {"crawl": True, "generate": bool(estimate["total"]),
                      "score": bool(counts["generate"])}[key]
            if prereq:
                next_step = key
            break
    state = {
        "name": name,
        "title": PIPELINES[name]["title"],
        "blurb": PIPELINES[name]["blurb"],
        "friendly": PIPELINES[name]["friendly"],
        "params": PIPELINES[name]["params"],
        "running_step": cur.step if running else None,
        "next_step": next_step,
        "crawl_units": _crawl_units(name),
        "day_choices": _day_choices(name),
        "cluster_modes": CLUSTER_MODES,
        "estimate": estimate,
        "keyword_modes": KEYWORD_MODES,
        "selectable": _selectable(name),
        "select_label": "%s to crawl" % PIPELINES[name]["source_word_plural"],
        "steps": [
            {"key": "crawl", "label": "1 · Fetch articles",
             "enabled": not busy, "why": "",
             "artifact": crawl,
             "detail": "%s articles" % crawl["count"] if crawl["count"] is not None else "no crawl yet",
             "view": crawl["count"] or 0,
             "meta": meta("crawl"), "is_next": next_step == "crawl"},
            {"key": "generate", "label": "2 · Write stories with AI",
             "enabled": not busy and bool(estimate["total"]),
             "why": ("" if estimate["total"] else "include at least one fetched article"
                     if crawl["count"] else "do step 1, Fetch articles, first"),
             "artifact": agency,
             "detail": "%s of %s selected reports have a story"
                       % (counts["generate"], estimate["counts"]["story"]),
             "view": counts["generate"],
             "meta": meta("generate"), "is_next": next_step == "generate"},
            {"key": "score", "label": "3 · Check quality",
             "enabled": not busy and bool(counts["generate"]),
             "why": "" if counts["generate"] else "do step 2, Write stories with AI, first",
             "artifact": scored,
             "detail": "%s selected reports checked" % counts["score"],
             "view": counts["score"],
             "meta": meta("score"), "is_next": next_step == "score"},
        ],
    }
    # A step blocked by ANOTHER run used to get why="" — a greyed-out button with
    # no reason at all. That is exactly what made a step 2 that was busily
    # generating look broken: disabled, silent, nothing on its own row. Now the
    # running step says so, and every other step names what it is waiting for.
    # A step locked for a real reason (no crawl yet) keeps that reason.
    for st in state["steps"]:
        st["running"] = bool(running and cur and cur.step == st["key"])
        st["busy"] = bool(busy and not st["running"] and not st["why"])
        if st["busy"]:
            st["why"] = "“%s” is running on %s; one step runs at a time" % (
                STEP_META[cur.step]["label"], PIPELINES[cur.pipeline]["title"])
    return state


# ---------------------------------------------------------------- the steps

def _days_param(p: dict):
    """The day window a run asked for. "" / absent means use the config default."""
    raw = p.get("days")
    if raw in (None, "", "default"):
        return None                      # the pipeline falls back to cfg.DAYS_BACK
    return int(raw)


def _keywords(p: dict):
    """The user's keyword terms and how to apply them.

    Parsing lives in `hindi_text` rather than here so the UI and any script calling
    the pipeline directly split a keyword string exactly the same way.
    """
    terms = hindi_text.parse_keywords(p.get("keywords"))
    mode = p.get("keyword_mode") or "narrow"
    if mode not in hindi_text.KEYWORD_MODES:
        raise ValueError("unknown keyword mode %r" % mode)
    return terms, mode


def _cap(p: dict, key: str):
    """The per-source cap, or None for uncapped.

    The number input is only honoured when its companion toggle is on, so an
    untouched form never silently caps a run at the old default of 3.
    """
    if not p.get("limit_on"):
        return None
    raw = p.get(key)
    return int(raw) if raw else None


def _clear_downstream(job: Job | None, pipeline: str) -> None:
    cfg = PIPELINES[pipeline]["cfg"]
    paths = (cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT)
    for path in paths:
        _backup(path, job)
    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    (job.log if job else print)("Previous AI stories and quality checks cleared for this fetch.")


def _clear_stale_outputs(pipeline: str) -> None:
    """Repair results left by older UI processes that did not reset on fetch."""
    cfg = PIPELINES[pipeline]["cfg"]
    if not os.path.exists(cfg.CRAWL_OUTPUT):
        return
    if ((os.path.exists(cfg.AGENCY_OUTPUT)
         and os.path.getmtime(cfg.AGENCY_OUTPUT) < os.path.getmtime(cfg.CRAWL_OUTPUT))
            or (not os.path.exists(cfg.AGENCY_OUTPUT) and os.path.exists(cfg.SCORED_OUTPUT))):
        _clear_downstream(None, pipeline)


def _step_web_crawl(job: Job, p: dict) -> str:
    n = _cap(p, "max_per_source")
    days = _days_param(p)
    picked = p.get("sources") or None
    kw, kw_mode = _keywords(p)
    job.log("run_hindi_crawl(max_per_source=%s, days=%s, publishers=%s, keywords=%s) -> %s"
            % ("uncapped" if n is None else n,
               "config default (%d)" % config_web.DAYS_BACK if days is None else days,
               "all 6" if not picked else "%d selected" % len(picked),
               "none" if not kw else "%s [%s]" % (", ".join(kw), kw_mode),
               os.path.relpath(config_web.CRAWL_OUTPUT, _PROJECT_ROOT)))
    _backup(config_web.CRAWL_OUTPUT, job)
    _clear_downstream(job, "web")
    arts = web_pipeline.run_hindi_crawl(
        max_per_source=n, days=days, source_names=picked,
        keywords=kw, keyword_mode=kw_mode,
        out_file=config_web.CRAWL_OUTPUT, verbose=True)
    _excluded_keys("web", arts, after_crawl=True)
    return _crawl_summary(job, arts)


def _step_rsb_crawl(job: Job, p: dict) -> str:
    n = _cap(p, "per_zone")
    hindi_only = bool(p.get("hindi_only"))
    days = _days_param(p)
    zones = p.get("sources") or config_rsb.DEFAULT_ZONE_CODES
    kw, kw_mode = _keywords(p)
    job.log("run_rsb_crawl(per_zone=%s, hindi_only=%s, days=%s, zones=%s, keywords=%s) -> %s"
            % ("uncapped" if n is None else n, hindi_only,
               "config default (%d)" % config_rsb.DAYS_BACK if days is None else days,
               ",".join(zones), "none" if not kw else "%s [%s]" % (", ".join(kw), kw_mode),
               os.path.relpath(config_rsb.CRAWL_OUTPUT, _PROJECT_ROOT)))
    _backup(config_rsb.CRAWL_OUTPUT, job)
    _clear_downstream(job, "rsb")
    arts = rsb_pipeline.run_rsb_crawl(
        zone_codes=zones, per_zone=n, hindi_only=hindi_only, days=days,
        keywords=kw, keyword_mode=kw_mode,
        out_file=config_rsb.CRAWL_OUTPUT, verbose=True)
    _excluded_keys("rsb", arts, after_crawl=True)
    return _crawl_summary(job, arts)


def _crawl_summary(job: Job, arts: list) -> str:
    """The two silent-failure checks worth surfacing on every crawl."""
    if not arts:
        return "0 articles — nothing matched. See the log for per-source detail."
    from collections import Counter
    ex = Counter(a.get("extractor", "?") for a in arts)
    job.log("\nextractor -> %s" % dict(ex))
    if ex.get("generic"):
        job.log("  WARNING: %d article(s) used the generic <p> sweep — that domain's "
                "dedicated selector has drifted." % ex["generic"])
    short = [a for a in arts if len(a.get("body", "") or "") < 300]
    if short:
        job.log("  WARNING: %d article(s) under 300 chars of body — extraction likely "
                "failed silently." % len(short))
    bodies = sorted(len(a.get("body", "") or "") for a in arts)
    job.log("body chars: min %d / median %d / max %d"
            % (bodies[0], bodies[len(bodies) // 2], bodies[-1]))
    return "%d articles, %d short, extractors %s" % (
        len(arts), len(short), dict(ex))


def _cluster_input(job: Job, cfg, mode: str) -> str:
    """Build the generation input for `mode`, returning the path to feed engine.

    mode="off" writes the selected articles. Other modes write one article per
    cluster, whose body is the members' bodies concatenated under source headers.
    That is what makes one report cover one story instead of one article.
    """
    arts = _selected_articles(job.pipeline)
    if not arts:
        raise ValueError("No articles selected for AI. Include at least one fetched article before step 2.")
    job.log("%d article(s) selected for AI" % len(arts))
    if mode == "off":
        path = cfg.CRAWL_OUTPUT.replace(".json", "_selected.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(arts, fh, indent=2, ensure_ascii=False)
        return path
    groups = clustering.build_clusters(arts, mode=mode)
    # Same budget engine.generate_news truncates at, so every source in a cluster
    # actually reaches the model rather than just the first one or two.
    budget = getattr(engine, "MAX_INPUT_CHARS", None) or 6000
    merged = [clustering.cluster_article(arts, c, max_chars=budget) for c in groups]
    path = cfg.CRAWL_OUTPUT.replace(".json", "_clustered.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)
    multi = [c for c in groups if c["size"] > 1]
    job.log("clustering (%s): %s" % (mode, clustering.summarise(arts, groups)))
    for c in multi:
        job.log("   %d sources -> 1 report: %s | %s"
                % (c["size"], ", ".join(c["sources"]), c["title"][:44]))
    over = [a for a in merged if len(a.get("body") or "") > budget]
    trimmed = sum(a.get("cluster_trimmed") or 0 for a in merged)
    if trimmed:
        job.log("   %d source body/bodies trimmed to fit the %d-char model input, "
                "split evenly so every source is represented" % (trimmed, budget))
    if over:
        job.log("   WARNING: %d cluster(s) still exceed %d chars and will be "
                "truncated by the model" % (len(over), budget))
    job.log("generation input -> %s" % os.path.basename(path))
    return path


def _step_generate(job: Job, p: dict, name: str) -> str:
    cfg = PIPELINES[name]["cfg"]
    if not _selected_articles(name):
        raise ValueError("No articles selected for AI. Include at least one fetched article before step 2.")
    key = config_env.describe("ANTHROPIC_API_KEY", engine.ANTHROPIC_API_KEY)
    if not config_env.apply_to_engine(engine):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — checked the shell environment, "
            "research/.env and the project-root .env. Generation would return "
            "empty results, so it was not started.")
    job.log("key: %d chars from %s" % (key["length"], key["source"]))
    engine.STATUS_FILE = cfg.STATUS_FILE

    mode = p.get("cluster_mode") or "story"
    if mode not in clustering.MODES:
        raise ValueError("unknown cluster mode %r" % mode)
    src = _cluster_input(job, cfg, mode)

    before = _generated_count(cfg.AGENCY_OUTPUT)
    job.log("engine.run_generate(%s -> %s); resumes by title, %d already done"
            % (os.path.basename(src),
               os.path.basename(cfg.AGENCY_OUTPUT), before))
    arts = engine.run_generate(src, cfg.AGENCY_OUTPUT)
    after = _generated_count(cfg.AGENCY_OUTPUT)
    if after == before:
        job.log("\n  NOTE: no new stories. engine skips titles already present in the "
                "output file — delete %s to force a clean regeneration."
                % os.path.basename(cfg.AGENCY_OUTPUT))
    failed = [a for a in (_read_json(cfg.AGENCY_OUTPUT) or [])
              if (a.get("ai_news_story") or "").startswith("[")]
    if failed:
        job.log("  WARNING: %d article(s) hold an error placeholder instead of a story."
                % len(failed))
        for a in failed[:5]:
            job.log("    %s :: %s" % (a.get("ai_news_story", "")[:60],
                                      (a.get("title") or "")[:50]))
    total = len(arts) if arts else len(_read_json(cfg.AGENCY_OUTPUT) or [])
    return "%d articles in file, %d with a story (+%d new), %d failed" % (
        total, after, after - before, len(failed))


def _step_score(job: Job, p: dict, name: str) -> str:
    cfg = PIPELINES[name]["cfg"]
    job.log("rescore_file(%s)" % os.path.basename(cfg.AGENCY_OUTPUT))
    scored, out = score_report.rescore_file(cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT)
    job.log("wrote %s" % os.path.relpath(out, _PROJECT_ROOT))

    fact = [a.get("fact_score") for a in scored if a.get("fact_score") is not None]
    cov = [a.get("coverage_score") for a in scored if a.get("coverage_score") is not None]
    gated = [a for a in scored if not a.get("gates_passed")]
    job.log("\ndistinct values -> fact_score %d | coverage_score %d"
            % (len(set(fact)), len(set(cov))))
    job.log("gate failures: %d of %d" % (len(gated), len(scored)))
    for a in gated[:10]:
        job.log("  fact=%s ceiling=%s  %s" % (
            a.get("fact_score"), a.get("grounding_ceiling"), (a.get("title") or "")[:55]))
        for g in a.get("gate_failures", []):
            job.log("      - %s" % g)
    return "%d scored, %d gate failures, coverage has %d distinct values vs fact_score's %d" % (
        len(scored), len(gated), len(set(cov)), len(set(fact)))


STEPS = {
    ("web", "crawl"): _step_web_crawl,
    ("rsb", "crawl"): _step_rsb_crawl,
    ("web", "generate"): lambda j, p: _step_generate(j, p, "web"),
    ("rsb", "generate"): lambda j, p: _step_generate(j, p, "rsb"),
    ("web", "score"): lambda j, p: _step_score(j, p, "web"),
    ("rsb", "score"): lambda j, p: _step_score(j, p, "rsb"),
}


# ------------------------------------------------------ verification view

#: How much title overlap counts as "the same story" for the other-sources panel.
#: Measured on the current 22-article corpus: cross-publisher pairs top out at 0.10
#: Jaccard (they merely share a generic word like रेलवे), while a genuine duplicate
#: scores 1.00. So anything in between is noise, and 0.45 sits safely in the gap.
SAME_STORY_JACCARD = 0.45

#: The AI fields the verification pane shows, right column, in this order.
#: The AI output, grouped the way a reader thinks about it rather than one block
#: per database field. The two story languages become TABS instead of two stacked
#: blocks — a reviewer reads one language at a time — and each social post keeps
#: the network's own colour, matching the product webapp's `.social-*` cards so
#: the two UIs teach the same visual language.
#:
#: `tone` selects a colour from `.tone-*` in _base.html.
AI_STORY_LANGS = [
    {"key": "hi", "label": "Hindi", "note": "as written, in the source language",
     "headline": "ai_headline", "body": "ai_news_story"},
    {"key": "en", "label": "English", "note": "translation of the Hindi story",
     "headline": "ai_headline_translated", "body": "ai_news_story_translated"},
]

AI_SOCIAL = [
    {"field": "social_twitter", "label": "Twitter / X post", "tone": "tw"},
    {"field": "social_facebook", "label": "Facebook post", "tone": "fb"},
    {"field": "social_general", "label": "General post", "tone": "gen"},
]

#: Anything the model produced that is neither a story nor a social post.
AI_EXTRA = [
    ("impact_reason", "Why this matters", "extra"),
]


def _ai_story_tabs(art: dict) -> list[dict]:
    """One entry per language that actually has text, for the story tab strip.

    A language with neither a headline nor a body is dropped rather than shown as
    an empty tab — translation is a separate model field and is often absent.
    """
    out = []
    for lang in AI_STORY_LANGS:
        headline = art.get(lang["headline"]) or ""
        body = art.get(lang["body"]) or ""
        if headline or body:
            out.append(dict(lang, headline=headline, body=body,
                            chars=len(body)))
    return out


def _ai_social(art: dict) -> list[dict]:
    """The social posts that exist, each carrying its network colour."""
    return [dict(s, text=art[s["field"]]) for s in AI_SOCIAL if art.get(s["field"])]


def _no_article(pipeline: str, stage: str, index: int, count: int, nav: str):
    """A styled dead-end page for "that article is not there".

    Both article views used to `return "no article 0 in crawl (0 present)", 404`
    — accurate, but a bare unstyled string with no navigation, which is where a
    new user's first click lands when nothing has been crawled yet.
    """
    if count:
        heading = "There is no article #%d here" % index
        detail = ("The %s stage holds %d article(s), numbered 0 to %d. This can "
                  "happen after a smaller re-crawl leaves an old link pointing "
                  "past the end." % (STAGE_LABEL[stage], count, count - 1))
        actions = [{"href": "/data/%s?stage=%s" % (pipeline, stage),
                    "label": "← Back to the article list"}]
    else:
        heading = "Nothing has been collected yet"
        detail = ("The %s stage is empty, so there is no article to show. Run "
                  "step 1 on the control panel to fetch articles — that step is "
                  "free and calls no AI." % STAGE_LABEL[stage])
        actions = [{"href": "/", "label": "Go to the control panel →"},
                   {"href": "/data/%s?stage=%s" % (pipeline, stage),
                    "label": "Browse articles"}]
    return render_template("empty.html", nav=nav, heading=heading,
                           detail=detail, actions=actions), 404


def _best_stage(pipeline: str) -> str:
    """The richest stage that actually has data — scored, else agency, else crawl."""
    for stage in ("scored", "agency", "crawl"):
        if (_read_json(_stage_path(pipeline, stage)) or []):
            return stage
    return "crawl"


def _same_story(articles: list, index: int) -> list[dict]:
    """Other articles in the same file that look like the same story.

    Uses the patched `hindi_text.title_words` (Devanagari-safe) and Jaccard overlap.
    Returns [] when there is no corroboration — which is the honest answer for this
    corpus most of the time, since direct crawling gives one source per story.
    """
    from shared.hindi_text import title_words
    target = title_words(articles[index].get("title") or "")
    if not target:
        return []
    out = []
    for i, a in enumerate(articles):
        if i == index:
            continue
        other = title_words(a.get("title") or "")
        if not other:
            continue
        score = len(target & other) / len(target | other)
        if score >= SAME_STORY_JACCARD:
            out.append({"i": i, "score": round(score, 2),
                        "source": a.get("source") or "",
                        "title": a.get("title") or "",
                        "link": a.get("link") or "",
                        "chars": len(a.get("body") or "")})
    return sorted(out, key=lambda r: -r["score"])


def _scores(art: dict) -> list[dict]:
    """The score badges for the verification header, with what each one means."""
    def band(v, good, ok_):
        if v is None:
            return "none"
        return "ok" if v >= good else ("warn" if v >= ok_ else "err")

    fact = art.get("fact_score")
    plag = art.get("plagiarism_score")
    cov = art.get("coverage_score")
    prec = art.get("number_precision")
    ceil = art.get("grounding_ceiling")
    # A plagiarism 0 means "highly original" OR "the texts were too short to
    # compare". The table already distinguishes them (`_rows`); this pane did
    # not, so a not-comparable article showed a confident green 0 here and a
    # dash two clicks away. Same guard, one source of truth.
    plag_na = _plagiarism_not_comparable(art)
    rows = [
        {"key": "fact_score", "label": "Fact score", "value": fact,
         "band": band(fact, 80, 50), "computed": "the model marking its own work",
         "hint": "The AI's own confidence in the story it just wrote. Nothing "
                 "checks it, and it barely varies — treat it as the weakest "
                 "signal on this page."},
        {"key": "plagiarism_score", "label": "Plagiarism", "value":
             None if plag_na else plag,
         # lower is better, so the bands invert
         "band": "none" if (plag is None or plag_na) else
                 ("ok" if plag <= 20 else "warn" if plag <= 35 else "err"),
         "computed": "measured here, not by the AI",
         "hint": ("Both texts were too short to compare, so there is no score."
                  if plag_na else
                  "How much of the story is word-for-word from the source, "
                  "counted in four-word runs. LOWER is better: under 20% is "
                  "good, over 35% needs rewriting."),
         "na": plag_na},
        {"key": "coverage_score", "label": "Coverage", "value": cov,
         "band": band(cov, 60, 30), "computed": "measured against the source",
         "hint": "How many of the source's facts and figures the story kept. "
                 "The most useful number here — it varies far more than the "
                 "AI's own score, so it actually separates good from bad."},
        {"key": "number_precision", "label": "Number precision", "value": prec,
         "band": band(prec, 90, 75), "computed": "measured against the source",
         "hint": "Of the figures in the story, how many really appear in the "
                 "source. Used as a check for invented numbers rather than as "
                 "a quality score — it is almost always high."},
        {"key": "grounding_ceiling", "label": "Trust ceiling", "value": ceil,
         "band": band(ceil, 70, 20), "computed": "automatic safety checks",
         "hint": "The highest this article can be trusted, whatever the AI "
                 "claims about itself. 0 means one of the automatic checks "
                 "below failed outright."},
    ]
    return rows


# ----------------------------------------------------------------- the app

app = Flask(__name__, template_folder=os.path.join(_RESEARCH_DIR, "ui_templates"))


#: Display order for anything that lists pipelines.
def _pipeline_names() -> list[str]:
    return sorted(PIPELINES, key=lambda n: PIPELINES[n]["number"])


@app.context_processor
def _inject_pipelines():
    """Every template can list the pipelines, so no page hardcodes "the other one".

    The old switch links read `'rsb' if pipeline == 'web' else 'web'` — correct
    for exactly two and wrong the moment a third is added.
    """
    return {"all_pipelines": [dict(PIPELINES[n], name=n) for n in _pipeline_names()]}


def _card(name: str) -> dict:
    """The one-glance summary of a pipeline for the landing page."""
    st = _state(name)
    cfg = PIPELINES[name]["cfg"]
    # `_state` keeps the artifacts as locals and only surfaces them inside
    # `steps`, so read them directly — it is what the steps do anyway.
    crawl = _artifact(cfg.CRAWL_OUTPUT)
    agency = _artifact(cfg.AGENCY_OUTPUT)
    scored = _artifact(cfg.SCORED_OUTPUT)
    written = _generated_count(cfg.AGENCY_OUTPUT) if agency["exists"] else 0
    # The most recent artifact's timestamp is "when did anything last happen".
    whens = [a["when"] for a in (crawl, agency, scored) if a.get("when")]
    return {
        "name": name, "title": st["title"], "friendly": st["friendly"],
        "icon": PIPELINES[name]["icon"], "number": PIPELINES[name]["number"],
        "units": _crawl_units(name), "unit_word": PIPELINES[name]["source_word_plural"],
        "fetched": crawl["count"] or 0, "written": written, "checked": scored["count"] or 0,
        "last": max(whens) if whens else "",
        "running": st["running_step"], "next_step": st["next_step"],
        "next_label": STEP_META[st["next_step"]]["label"] if st["next_step"] else "",
    }


@app.get("/")
def index():
    """Landing: one card per pipeline. Pick one to get its own control window.

    Two full control panels side by side were already dense, and a third
    pipeline would have made each column narrower than its own form. Choosing
    first also halves what a newcomer has to read before doing anything.
    """
    return render_template(
        "home.html", nav="panel",
        cards=[_card(n) for n in _pipeline_names()],
        key=config_env.describe("ANTHROPIC_API_KEY", engine.ANTHROPIC_API_KEY),
        recent=_recent(),
    )


@app.get("/panel/<pipeline>")
def panel(pipeline):
    """The full control window for ONE pipeline."""
    if pipeline not in PIPELINES:
        return _unknown_pipeline(pipeline)
    return render_template(
        "index.html",
        nav="panel", pipelines=[_state(pipeline)], pipeline=pipeline,
        key=config_env.describe("ANTHROPIC_API_KEY", engine.ANTHROPIC_API_KEY),
        recent=_recent(),
    )


def _unknown_pipeline(name):
    return render_template(
        "empty.html", nav="panel", heading="There is no pipeline called “%s”" % name,
        detail="The available ones are listed on the landing page.",
        actions=[{"href": "/", "label": "← Choose a pipeline"}]), 404


@app.post("/run/<pipeline>/<step>")
def run(pipeline, step):
    if (pipeline, step) not in STEPS:
        return jsonify(error="unknown stage %s/%s" % (pipeline, step)), 404
    with _SELECTION_LOCK:
        if CURRENT is not None:
            # `web-crawl-001` means nothing to a reader; name the step instead.
            return jsonify(error="“%s” is still running on the %s pipeline. Wait for "
                                 "it to finish — only one step runs at a time."
                                 % (STEP_META[CURRENT.step]["label"],
                                    PIPELINES[CURRENT.pipeline]["title"])), 409
        if step == "generate" and not _selected_articles(pipeline):
            return jsonify(error="No articles selected for AI. Include at least one fetched article before step 2."), 400
        job = _run_async(pipeline, step, request.get_json(silent=True) or {})
    return jsonify(job_id=job.id)


@app.route("/api/selection/<pipeline>", methods=["GET", "POST"])
def api_selection(pipeline):
    if pipeline not in PIPELINES:
        return jsonify(error="unknown pipeline"), 404
    with _SELECTION_LOCK:
        if request.method == "POST" and CURRENT is not None:
            return jsonify(error="Wait for the current run to finish before changing selection."), 409
        arts = _read_json(_stage_path(pipeline, "crawl")) or []
        excluded = _excluded_keys(pipeline, arts)
        if request.method == "POST":
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify(error="Expected a JSON object."), 400
            action = payload.get("action")
            keys = payload.get("keys", [])
            if not isinstance(action, str) or action not in {"exclude", "include", "select_all", "clear_excluded"}:
                return jsonify(error="unknown selection action"), 400
            if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
                return jsonify(error="keys must be a list of article keys"), 400
            valid = {_article_key(a) for a in arts}
            if set(keys) - valid:
                return jsonify(error="Articles changed. Reload the fetched articles and try again."), 400
            if action == "exclude":
                excluded.update(keys)
            elif action == "include":
                excluded.difference_update(keys)
            else:
                excluded.clear()
            _write_selection(pipeline, excluded)
        return jsonify(_selection_summary(arts, excluded))


@app.get("/api/state")
def api_state():
    return jsonify(
        pipelines=[_state(n) for n in _pipeline_names()],
        current=CURRENT.id if CURRENT else None,
        recent=_recent(),
    )


@app.get("/api/log/<job_id>")
def api_log(job_id):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify(error="no such job"), 404
    return jsonify(**job.tail(int(request.args.get("offset", 0))))


@app.get("/logs")
def logs():
    """Past runs, grouped by pipeline then stage, each with its recorded outcome.

    Every .log on disk is listed — there is no cap and never was — but a flat
    list of file names left the reader to decode "web-crawl" and gave no hint
    whether the run succeeded. `?pipeline=` and `?step=` narrow the list.
    """
    filt_p = request.args.get("pipeline") or ""
    filt_s = request.args.get("step") or ""
    files = []
    if os.path.isdir(LOG_DIR):
        for n in sorted(os.listdir(LOG_DIR), reverse=True):
            if not n.endswith(".log"):
                continue
            full = os.path.join(LOG_DIR, n)
            b = _run_brief_from_disk(n)
            if (filt_p and b["pipeline"] != filt_p) or (filt_s and b["step"] != filt_s):
                continue
            files.append(dict(b, name=n, size=os.path.getsize(full),
                              when=b.get("when") or _dt.datetime.fromtimestamp(
                                  os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
                              running=(CURRENT is not None and CURRENT.log_name == n)))
    # pipeline -> step -> runs, in registry / step order
    groups = []
    for pname in _pipeline_names():
        stages = []
        for skey in ("crawl", "generate", "score"):
            runs = [f for f in files if f["pipeline"] == pname and f["step"] == skey]
            if runs:
                stages.append({"key": skey, "label": STEP_META[skey]["label"], "runs": runs})
        if stages:
            groups.append({"name": pname, "title": PIPELINES[pname]["title"],
                           "icon": PIPELINES[pname]["icon"], "stages": stages})
    # anything with an unrecognised pipeline/step still shows, rather than vanishing
    other = [f for f in files if f["pipeline"] not in PIPELINES or f["step"] not in STEP_META]
    want = request.args.get("f") or (files[0]["name"] if files else None)
    body, warnings = "", []
    if want:
        if want != os.path.basename(want) or not want.endswith(".log"):
            return "bad log name", 400          # no path traversal
        try:
            with open(os.path.join(LOG_DIR, want), encoding="utf-8") as f:
                body = f.read()
        except OSError as exc:
            body = "could not read %s: %s" % (want, exc)
        warnings = [ln for ln in body.splitlines()
                    if "WARNING" in ln or "FAILED" in ln or "Traceback" in ln
                    or "SKIP" in ln or "Error" in ln]
    return render_template("logs.html", nav="logs", files=files, current=want,
                           groups=groups, other=other, filt_p=filt_p, filt_s=filt_s,
                           step_meta=STEP_META,
                           body=body, warnings=warnings)


@app.get("/data/<pipeline>")
def data(pipeline):
    if pipeline not in PIPELINES:
        return "unknown pipeline", 404
    stage = request.args.get("stage", "crawl")
    if stage not in STAGES:
        return "unknown stage", 400
    rows = _rows(pipeline, stage)
    selection = None
    selection_view = request.args.get("selection", "all")
    if selection_view not in {"all", "selected", "excluded"}:
        return "unknown selection filter", 400
    if stage == "crawl":
        selection = {"selected": sum(not r["excluded"] for r in rows),
                     "excluded": sum(r["excluded"] for r in rows)}
        if selection_view != "all":
            rows = [r for r in rows if r["excluded"] == (selection_view == "excluded")]
    elif selection_view == "selected":
        mode = request.args.get("mode", "story")
        if mode not in clustering.MODES:
            return "unknown mode", 400
        arts = _selected_articles(pipeline)
        titles = {_resume_key(c["title"]) for c in clustering.build_clusters(arts, mode=mode)}
        rows = [r for r in rows if _resume_key(r["title"]) in titles and r["has_story"]]
    q = (request.args.get("q") or "").strip()
    if q:
        needle = q.lower()
        rows = [r for r in rows
                if needle in r["title"].lower() or needle in r["snippet"].lower()
                or needle in r["source"].lower()]
    return render_template(
        "data.html", nav="data", pipeline=pipeline, title=PIPELINES[pipeline]["title"],
        stage=stage, stage_label=STAGE_LABEL[stage], stages=_available_stages(pipeline),
        rows=rows, q=q, selection=selection, selection_view=selection_view,
        file=os.path.relpath(_stage_path(pipeline, stage), _PROJECT_ROOT),
        total=len(_read_json(_stage_path(pipeline, stage)) or []))


@app.get("/data/<pipeline>/<int:index>")
def data_detail(pipeline, index):
    if pipeline not in PIPELINES:
        return "unknown pipeline", 404
    stage = request.args.get("stage", "crawl")
    if stage not in STAGES:
        return "unknown stage", 400
    arts = _read_json(_stage_path(pipeline, stage)) or []
    if not 0 <= index < len(arts):
        return _no_article(pipeline, stage, index, len(arts), "data")
    art = arts[index]
    listing, exact = _listing_for(art, _listing_fallback(pipeline))
    long_keys = {k for k, _ in _TEXT_FIELDS}
    return render_template(
        "detail.html", nav="data", pipeline=pipeline, title=PIPELINES[pipeline]["title"],
        stage=stage, stage_label=STAGE_LABEL[stage], index=index, count=len(arts),
        art=art, listing=listing, listing_exact=exact,
        source_texts=[(label, art[k]) for k, label in _TEXT_FIELDS
                      if art.get(k) and k in _SOURCE_TEXT_KEYS],
        ai_texts=[(label, art[k]) for k, label in _TEXT_FIELDS
                  if art.get(k) and k not in _SOURCE_TEXT_KEYS],
        fields=[(k, v) for k, v in art.items()
                if k not in long_keys and not isinstance(v, (dict,))],
        file=os.path.relpath(_stage_path(pipeline, stage), _PROJECT_ROOT))


#: Deliberately minimal Markdown -> HTML, covering only what docs/SCORES.md uses:
#: headings, tables, fenced code, bullet lists, blockquotes, ---, **bold**, `code`
#: and links. Pulling in a Markdown dependency for one page was not worth it; the doc
#: stays the single source of truth and this renders it.
def _md_to_html(text: str) -> str:
    import html as _html

    def inline(t):
        t = _html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
        return t

    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(_html.escape(lines[i])); i += 1
            out.append('<pre class="mono">%s</pre>' % "\n".join(buf)); i += 1
        elif ln.startswith("|") and i + 1 < len(lines) and set(lines[i+1].replace("|", "").strip()) <= set("-: "):
            head = [c.strip() for c in ln.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")]); i += 1
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                "".join("<th>%s</th>" % inline(h) for h in head),
                "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r)
                        for r in rows)))
        elif re.match(r"^#{1,4} ", ln):
            lvl = len(ln) - len(ln.lstrip("#"))
            out.append("<h%d>%s</h%d>" % (lvl, inline(ln[lvl:].strip()), lvl)); i += 1
        elif ln.strip() == "---":
            out.append("<hr>"); i += 1
        elif ln.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].startswith("> "):
                buf.append(inline(lines[i][2:])); i += 1
            out.append("<blockquote>%s</blockquote>" % " ".join(buf))
        elif re.match(r"^[-*] ", ln):
            buf = []
            while i < len(lines) and re.match(r"^[-*] ", lines[i]):
                buf.append("<li>%s</li>" % inline(lines[i][2:])); i += 1
            out.append("<ul>%s</ul>" % "".join(buf))
        elif ln.strip() == "":
            i += 1
        else:
            buf = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4} |[-*] |\||```|> |---$)", lines[i]):
                buf.append(inline(lines[i])); i += 1
            out.append("<p>%s</p>" % " ".join(buf))
    return "\n".join(out)


@app.get("/scores")
def scores_doc():
    """Render docs/SCORES.md — how each figure in the verification window is computed."""
    path = os.path.join(_RESEARCH_DIR, "docs", "SCORES.md")
    try:
        with open(path, encoding="utf-8") as fh:
            body = _md_to_html(fh.read())
    except OSError as exc:
        body = "<p>Could not read docs/SCORES.md: %s</p>" % exc
    return render_template("doc.html", nav="scores", body=body,
                           source="research/docs/SCORES.md")


@app.get("/clusters/<pipeline>")
def clusters(pipeline):
    """Preview how many reports a run would produce, before any money is spent.

    Clustering decides the report count, so it is shown against the CRAWLED articles
    with the mode selectable — a dry run you can read before triggering generation.
    """
    if pipeline not in PIPELINES:
        return "unknown pipeline", 404
    mode = request.args.get("mode", "story")
    if mode not in clustering.MODES:
        return "unknown mode", 400
    all_arts = _read_json(_stage_path(pipeline, "crawl")) or []
    excluded = _excluded_keys(pipeline, all_arts)
    indices = [i for i, a in enumerate(all_arts) if _article_key(a) not in excluded]
    arts = [all_arts[i] for i in indices]
    groups = clustering.build_clusters(arts, mode=mode) if arts else []
    rows = []
    for c in groups:
        rows.append({
            "size": c["size"], "chars": c["chars"], "topics": c["topics"],
            "sources": c["sources"], "lead": indices[c["lead"]],
            "members": [{"i": indices[i], "source": _short_source(arts[i].get("source") or ""),
                         "title": arts[i].get("title") or "",
                         "date": arts[i].get("date") or "",
                         "chars": len(arts[i].get("body") or "")}
                        for i in c["members"]],
        })
    counts = _mode_counts(pipeline)
    return render_template(
        "clusters.html", nav="clusters", pipeline=pipeline,
        title=PIPELINES[pipeline]["title"], mode=mode, modes=CLUSTER_MODES,
        rows=rows, counts=counts, total=len(arts),
        multi=sum(1 for r in rows if r["size"] > 1),
        file=os.path.relpath(_stage_path(pipeline, "crawl"), _PROJECT_ROOT))


@app.get("/verify/<pipeline>/<int:index>")
def verify(pipeline, index):
    """Side-by-side verification: original source left, AI output right."""
    if pipeline not in PIPELINES:
        return "unknown pipeline", 404
    stage = request.args.get("stage") or _best_stage(pipeline)
    if stage not in STAGES:
        return "unknown stage", 400
    arts = _read_json(_stage_path(pipeline, stage)) or []
    if not 0 <= index < len(arts):
        return _no_article(pipeline, stage, index, len(arts), "verify")
    art = arts[index]
    listing, exact = _listing_for(art, _listing_fallback(pipeline))
    return render_template(
        "verify.html", nav="verify", pipeline=pipeline,
        title=PIPELINES[pipeline]["title"], stage=stage,
        stage_label=STAGE_LABEL[stage], stages=_available_stages(pipeline),
        index=index, count=len(arts), art=art,
        listing=listing, listing_exact=exact,
        # A merged cluster body carries one "[source — date]" header per source;
        # an ordinary article comes back as a single headerless part, so the
        # template needs no special case for the common one-source article.
        source_parts=clustering.split_merged_body(art.get("body") or ""),
        cluster_links=art.get("cluster_links") or [],
        story_tabs=_ai_story_tabs(art),
        socials=_ai_social(art),
        extras=[(label, art[k], tone) for k, label, tone in AI_EXTRA if art.get(k)],
        scores=_scores(art),
        gate_failures=art.get("gate_failures") or [],
        topics_verified=art.get("topics_verified") or [],
        topics_unsupported=art.get("topics_unsupported") or [],
        same_story=_same_story(arts, index),
        # When the report was generated from a cluster, these are the sources it was
        # actually written from — authoritative, unlike the similarity guess above.
        cluster=[{"source": src,
                  "link": (art.get("cluster_links") or [None] * 99)[k],
                  "title": (art.get("cluster_titles") or [None] * 99)[k]}
                 for k, src in enumerate(art.get("cluster_sources") or [])]
                if (art.get("cluster_size") or 1) > 1 else [],
        has_ai=_has_story(art),
        file=os.path.relpath(_stage_path(pipeline, stage), _PROJECT_ROOT))


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    for name in _pipeline_names():
        _clear_stale_outputs(name)
    k = config_env.describe("ANTHROPIC_API_KEY", engine.ANTHROPIC_API_KEY)
    print("research UI  ->  http://127.0.0.1:5001")
    print("logs         ->  http://127.0.0.1:5001/logs   (files in %s)"
          % os.path.relpath(LOG_DIR, _PROJECT_ROOT))
    print("ANTHROPIC_API_KEY: %s (%s)"
          % ("set, %d chars" % k["length"] if k["set"] else "NOT set", k["source"]))
    # debug=False: the reloader would run this module twice and duplicate the patches.
    app.run(debug=False, host="127.0.0.1", port=5001)
