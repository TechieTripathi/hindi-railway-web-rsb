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
import io
import json
import os
import re
import sys
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
            "seconds": round(((self.finished or _dt.datetime.now())
                              - self.started).total_seconds(), 1),
        }


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
    """Articles with a real story -- engine writes '[error…]' placeholders too."""
    data = _read_json(path) or []
    return sum(1 for a in data
               if a.get("ai_news_story") and not a["ai_news_story"].startswith("["))


BACKUP_DIR = os.path.join(config_web.OUTPUT_DIR, "backups")
KEEP_BACKUPS = 10


def _backup(path: str, job: "Job") -> None:
    """Copy a crawl output aside before a new crawl overwrites it.

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
    job.log("backed up %d article(s) -> %s"
            % (len(_read_json(path) or []), os.path.relpath(dest, _PROJECT_ROOT)))

    prefix = base[:-5] if base.endswith(".json") else base
    old = sorted(n for n in os.listdir(BACKUP_DIR) if n.startswith(prefix + "."))
    for n in old[:-KEEP_BACKUPS]:
        os.remove(os.path.join(BACKUP_DIR, n))


PIPELINES = {
    "web": {
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
            {"key": "max_per_source", "label": "articles to fetch per publisher",
             "type": "number", "default": config_web.MAX_PER_SOURCE},
        ],
    },
    "rsb": {
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
            {"key": "per_zone", "label": "releases to fetch per zone",
             "type": "number", "default": config_rsb.PER_ZONE},
            {"key": "hindi_only", "label": "skip releases whose text is not actually Hindi",
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
        "verb": "Checking…",
        "what": "Compares each story against its source — which figures it carried "
                "over, whether its topics have evidence. Offline and repeatable.",
        "produces": "coverage, precision and gate results per article",
        "time": "a second or two",
        "costs_money": False,
    },
}

#: Sources/zones a crawl will visit, so the progress bar has a denominator.
def _crawl_units(pipeline: str) -> int:
    if pipeline == "rsb":
        return len(config_rsb.DEFAULT_ZONE_CODES)
    cfg = _read_json(config_web.SOURCE_CONFIG_FILE) or {}
    group = cfg.get("source_groups", {}).get(config_web.SOURCE_GROUP, {})
    return len(group.get("sources", []))


# ------------------------------------------------------------- data browser

STAGES = {"crawl": "CRAWL_OUTPUT", "agency": "AGENCY_OUTPUT", "scored": "SCORED_OUTPUT"}
STAGE_LABEL = {"crawl": "crawled", "agency": "AI generated", "scored": "scored"}
STEP_STAGE = {"crawl": "crawl", "generate": "agency", "score": "scored"}
SNIPPET_CHARS = 170


def _stage_path(pipeline: str, stage: str) -> str:
    return getattr(PIPELINES[pipeline]["cfg"], STAGES[stage])


def _listing_fallback(pipeline: str):
    """source/zone -> listing URL, for articles crawled before `listing_url` existed."""
    out = {}
    if pipeline == "rsb":
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
    story = article.get("ai_news_story") or ""
    return bool(story) and not story.startswith("[")


def _rows(pipeline: str, stage: str) -> list[dict]:
    arts = _read_json(_stage_path(pipeline, stage)) or []
    fallback = _listing_fallback(pipeline)
    rows = []
    for i, a in enumerate(arts):
        listing, exact = _listing_for(a, fallback)
        body = a.get("body") or ""
        rows.append({
            "i": i,
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
            "gates_passed": a.get("gates_passed"),
        })
    return rows


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
        out.append({"key": stage, "label": STAGE_LABEL[stage],
                    "count": len(data) if data else 0})
    return out


def _state(name: str) -> dict:
    cfg = PIPELINES[name]["cfg"]
    crawl = _artifact(cfg.CRAWL_OUTPUT)
    agency = _artifact(cfg.AGENCY_OUTPUT)
    scored = _artifact(cfg.SCORED_OUTPUT)
    running = CURRENT.pipeline == name if CURRENT else False
    busy = CURRENT is not None
    counts = {"crawl": crawl["count"], "generate": agency["count"],
              "score": scored["count"]}
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
            prereq = {"crawl": True, "generate": bool(counts["crawl"]),
                      "score": bool(counts["generate"])}[key]
            if prereq:
                next_step = key
            break
    return {
        "name": name,
        "title": PIPELINES[name]["title"],
        "blurb": PIPELINES[name]["blurb"],
        "friendly": PIPELINES[name]["friendly"],
        "params": PIPELINES[name]["params"],
        "running_step": CURRENT.step if running else None,
        "next_step": next_step,
        "crawl_units": _crawl_units(name),
        "steps": [
            {"key": "crawl", "label": "1 · Crawl",
             "enabled": not busy, "why": "",
             "artifact": crawl,
             "detail": "%s articles" % crawl["count"] if crawl["count"] is not None else "no crawl yet",
             "view": crawl["count"] or 0,
             "meta": meta("crawl"), "is_next": next_step == "crawl"},
            {"key": "generate", "label": "2 · AI Generate",
             "enabled": not busy and bool(crawl["count"]),
             "why": "" if crawl["count"] else "do step 1, Fetch articles, first",
             "artifact": agency,
             "detail": ("%s of %s articles have a story"
                        % (_generated_count(cfg.AGENCY_OUTPUT), agency["count"]))
                       if agency["count"] is not None else "not generated yet",
             "view": agency["count"] or 0,
             "meta": meta("generate"), "is_next": next_step == "generate"},
            {"key": "score", "label": "3 · Grounded Score",
             "enabled": not busy and bool(agency["count"]),
             "why": "" if agency["count"] else "do step 2, Write stories with AI, first",
             "artifact": scored,
             "detail": "%s scored" % scored["count"] if scored["count"] is not None else "not scored yet",
             "view": scored["count"] or 0,
             "meta": meta("score"), "is_next": next_step == "score"},
        ],
    }


# ---------------------------------------------------------------- the steps

def _step_web_crawl(job: Job, p: dict) -> str:
    n = int(p.get("max_per_source") or config_web.MAX_PER_SOURCE)
    job.log("run_hindi_crawl(max_per_source=%d) -> %s"
            % (n, os.path.relpath(config_web.CRAWL_OUTPUT, _PROJECT_ROOT)))
    _backup(config_web.CRAWL_OUTPUT, job)
    arts = web_pipeline.run_hindi_crawl(
        max_per_source=n, out_file=config_web.CRAWL_OUTPUT, verbose=True)
    return _crawl_summary(job, arts)


def _step_rsb_crawl(job: Job, p: dict) -> str:
    n = int(p.get("per_zone") or config_rsb.PER_ZONE)
    hindi_only = bool(p.get("hindi_only"))
    job.log("run_rsb_crawl(per_zone=%d, hindi_only=%s, zones=%s) -> %s"
            % (n, hindi_only, ",".join(config_rsb.DEFAULT_ZONE_CODES),
               os.path.relpath(config_rsb.CRAWL_OUTPUT, _PROJECT_ROOT)))
    _backup(config_rsb.CRAWL_OUTPUT, job)
    arts = rsb_pipeline.run_rsb_crawl(
        per_zone=n, hindi_only=hindi_only,
        out_file=config_rsb.CRAWL_OUTPUT, verbose=True)
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


def _step_generate(job: Job, p: dict, name: str) -> str:
    cfg = PIPELINES[name]["cfg"]
    key = config_env.describe("ANTHROPIC_API_KEY", engine.ANTHROPIC_API_KEY)
    if not config_env.apply_to_engine(engine):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — checked the shell environment, "
            "research/.env and the project-root .env. Generation would return "
            "empty results, so it was not started.")
    job.log("key: %d chars from %s" % (key["length"], key["source"]))
    engine.STATUS_FILE = cfg.STATUS_FILE
    before = _generated_count(cfg.AGENCY_OUTPUT)
    job.log("engine.run_generate(%s -> %s); resumes by title, %d already done"
            % (os.path.basename(cfg.CRAWL_OUTPUT),
               os.path.basename(cfg.AGENCY_OUTPUT), before))
    arts = engine.run_generate(cfg.CRAWL_OUTPUT, cfg.AGENCY_OUTPUT)
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


# ----------------------------------------------------------------- the app

app = Flask(__name__, template_folder=os.path.join(_RESEARCH_DIR, "ui_templates"))


@app.get("/")
def index():
    return render_template(
        "index.html",
        nav="panel", pipelines=[_state("web"), _state("rsb")],
        key=config_env.describe("ANTHROPIC_API_KEY", engine.ANTHROPIC_API_KEY),
        recent=[JOBS[i].brief() for i in JOB_ORDER[:8]],
    )


@app.post("/run/<pipeline>/<step>")
def run(pipeline, step):
    if (pipeline, step) not in STEPS:
        return jsonify(error="unknown stage %s/%s" % (pipeline, step)), 404
    if CURRENT is not None:
        return jsonify(error="%s is still running" % CURRENT.id), 409
    job = _run_async(pipeline, step, request.get_json(silent=True) or {})
    return jsonify(job_id=job.id)


@app.get("/api/state")
def api_state():
    return jsonify(
        pipelines=[_state("web"), _state("rsb")],
        current=CURRENT.id if CURRENT else None,
        recent=[JOBS[i].brief() for i in JOB_ORDER[:8]],
    )


@app.get("/api/log/<job_id>")
def api_log(job_id):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify(error="no such job"), 404
    return jsonify(**job.tail(int(request.args.get("offset", 0))))


@app.get("/logs")
def logs():
    files = []
    if os.path.isdir(LOG_DIR):
        for n in sorted(os.listdir(LOG_DIR), reverse=True):
            if not n.endswith(".log"):
                continue
            full = os.path.join(LOG_DIR, n)
            files.append({
                "name": n, "size": os.path.getsize(full),
                "when": _dt.datetime.fromtimestamp(
                    os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M:%S"),
            })
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
                           body=body, warnings=warnings)


@app.get("/data/<pipeline>")
def data(pipeline):
    if pipeline not in PIPELINES:
        return "unknown pipeline", 404
    stage = request.args.get("stage", "crawl")
    if stage not in STAGES:
        return "unknown stage", 400
    rows = _rows(pipeline, stage)
    q = (request.args.get("q") or "").strip()
    if q:
        needle = q.lower()
        rows = [r for r in rows
                if needle in r["title"].lower() or needle in r["snippet"].lower()
                or needle in r["source"].lower()]
    return render_template(
        "data.html", nav="data", pipeline=pipeline, title=PIPELINES[pipeline]["title"],
        stage=stage, stage_label=STAGE_LABEL[stage], stages=_available_stages(pipeline),
        rows=rows, q=q, file=os.path.relpath(_stage_path(pipeline, stage), _PROJECT_ROOT),
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
        return "no article %d in %s (%d present)" % (
            index, STAGE_LABEL[stage], len(arts)), 404
    art = arts[index]
    listing, exact = _listing_for(art, _listing_fallback(pipeline))
    long_keys = {k for k, _ in _TEXT_FIELDS}
    return render_template(
        "detail.html", nav="data", pipeline=pipeline, title=PIPELINES[pipeline]["title"],
        stage=stage, stage_label=STAGE_LABEL[stage], index=index, count=len(arts),
        art=art, listing=listing, listing_exact=exact,
        texts=[(label, art[k]) for k, label in _TEXT_FIELDS if art.get(k)],
        fields=[(k, v) for k, v in art.items()
                if k not in long_keys and not isinstance(v, (dict,))],
        file=os.path.relpath(_stage_path(pipeline, stage), _PROJECT_ROOT))


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    k = config_env.describe("ANTHROPIC_API_KEY", engine.ANTHROPIC_API_KEY)
    print("research UI  ->  http://127.0.0.1:5001")
    print("logs         ->  http://127.0.0.1:5001/logs   (files in %s)"
          % os.path.relpath(LOG_DIR, _PROJECT_ROOT))
    print("ANTHROPIC_API_KEY: %s (%s)"
          % ("set, %d chars" % k["length"] if k["set"] else "NOT set", k["source"]))
    # debug=False: the reloader would run this module twice and duplicate the patches.
    app.run(debug=False, host="127.0.0.1", port=5001)
