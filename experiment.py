"""research/experiment.py — run a pipeline with config overrides, into its own folder.

The point is to make trying something cheap and non-destructive: every run lands in
`research/output/runs/<run_id>/` with the config that produced it recorded alongside,
so two runs can be compared without either clobbering the other.

    from research import experiment as ex

    ex.run("baseline")                          # current config
    ex.run("wider", max_per_source=8)           # override any web_search.config setting
    ex.run("rsb-hi", pipeline="rsb", hindi_only=True)
    ex.list_runs()
    ex.compare("baseline", "wider")

Overrides are applied to the config module for the duration of the run and restored
afterwards, so nothing leaks between experiments.
"""

import importlib
import inspect
import json
import os
import sys
from contextlib import contextmanager

_RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_RESEARCH_DIR), _RESEARCH_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rsb_search import config as config_rsb        # noqa: E402
from shared import grounding                       # noqa: E402
from shared import provenance as pv                # noqa: E402
from web_search import config as config_web        # noqa: E402

RUNS_DIR = os.path.join(_RESEARCH_DIR, "output", "runs")

_PIPELINES = {
    "web": {"config": config_web, "module": "web_search.pipeline", "entry": "run_hindi_crawl"},
    "rsb": {"config": config_rsb, "module": "rsb_search.pipeline", "entry": "run_rsb_crawl"},
}


@contextmanager
def overridden(module, **kwargs):
    """Temporarily set config attributes, restoring them afterwards.

    Unknown names raise rather than silently doing nothing — a typo'd override that
    quietly had no effect would invalidate the experiment.
    """
    unknown = [k for k in kwargs if not hasattr(module, k.upper()) and not hasattr(module, k)]
    if unknown:
        raise AttributeError("%s has no setting(s): %s. Available: %s"
                             % (module.__name__, unknown,
                                sorted(k for k in dir(module) if k.isupper())))
    saved = {}
    try:
        for k, v in kwargs.items():
            name = k if hasattr(module, k) else k.upper()
            saved[name] = getattr(module, name)
            setattr(module, name, v)
        yield
    finally:
        for name, v in saved.items():
            setattr(module, name, v)


def run(label, pipeline="web", crawl=True, score=True, **overrides):
    """Run one experiment. Returns the run directory.

    Keyword arguments are either config settings (case-insensitive) or arguments of
    the pipeline's entry function — whichever matches. So both of these work:

        ex.run("wider", max_per_source=8)                  # config_web.MAX_PER_SOURCE
        ex.run("sweep", pipeline="rsb", zone_codes=[...])  # run_rsb_crawl(zone_codes=)

    An argument matching neither raises, rather than silently having no effect.
    """
    if pipeline not in _PIPELINES:
        raise ValueError("pipeline must be one of %s" % list(_PIPELINES))
    spec = _PIPELINES[pipeline]
    cfg = spec["config"]

    run_id = pv.new_run_id(label)
    run_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)
    crawl_path = os.path.join(run_dir, "crawl.json")
    scored_path = os.path.join(run_dir, "scored.json")

    # importlib, not __import__: the latter returns the top-level package, so
    # __import__("web_search.pipeline") would hand back `web_search` and the
    # getattr below would miss the entry point.
    mod = importlib.import_module(spec["module"])
    entry = getattr(mod, spec["entry"])

    # Split kwargs: anything the entry function accepts goes to it, the rest are
    # treated as config settings.
    entry_params = set(inspect.signature(entry).parameters)
    entry_kwargs = {k: v for k, v in overrides.items() if k in entry_params}
    cfg_overrides = {k: v for k, v in overrides.items() if k not in entry_params}

    with overridden(cfg, **cfg_overrides):
        articles = entry(out_file=crawl_path, verbose=True, **entry_kwargs) if crawl else []
        meta = pv.build_meta(run_id, pipeline, cfg,
                             extra={"label": label, "overrides": overrides,
                                    "entry_args": entry_kwargs,
                                    "config_overrides": cfg_overrides,
                                    "article_count": len(articles)})
        pv.write_with_meta(crawl_path, articles, meta)

        if score and articles:
            for a in articles:
                a.update(grounding.score_article(a))
            pv.write_with_meta(scored_path, articles, dict(meta, stage="scored"))

    print("\nrun %s -> %s" % (run_id, run_dir))
    return run_dir


def _load(run_ref):
    """Accept a run_id, a label (newest match), or a path."""
    if os.path.isdir(run_ref):
        d = run_ref
    else:
        cands = [r for r in sorted(os.listdir(RUNS_DIR)) if r == run_ref or r.endswith("-" + run_ref)] \
            if os.path.isdir(RUNS_DIR) else []
        if not cands:
            raise FileNotFoundError("no run matching %r in %s" % (run_ref, RUNS_DIR))
        d = os.path.join(RUNS_DIR, cands[-1])
    path = os.path.join(d, "scored.json")
    if not os.path.exists(path):
        path = os.path.join(d, "crawl.json")
    arts, meta = pv.read_articles(path)
    return arts, meta, d


def list_runs(verbose=True):
    """Every run on disk, newest last."""
    if not os.path.isdir(RUNS_DIR):
        if verbose:
            print("no runs yet")
        return []
    rows = []
    for name in sorted(os.listdir(RUNS_DIR)):
        try:
            arts, meta, _ = _load(name)
        except Exception:
            continue
        rows.append({"run_id": name, "pipeline": meta.get("pipeline"),
                     "articles": len(arts), "overrides": meta.get("overrides") or {},
                     "config_hash": meta.get("config_hash"),
                     "code_hash": meta.get("code_hash")})
    if verbose:
        print("%-30s %-5s %-5s %-13s %s" % ("run_id", "pipe", "n", "config", "overrides"))
        print("-" * 88)
        for r in rows:
            print("%-30s %-5s %-5d %-13s %s" % (
                r["run_id"], r["pipeline"], r["articles"], r["config_hash"], r["overrides"]))
    return rows


def _summary(articles):
    def avg(key):
        v = [a[key] for a in articles if isinstance(a.get(key), (int, float))]
        return round(sum(v) / len(v), 1) if v else None
    bodies = sorted(len(a.get("body", "")) for a in articles)
    return {
        "articles": len(articles),
        "median_body": bodies[len(bodies) // 2] if bodies else 0,
        "coverage_score": avg("coverage_score"),
        "number_precision": avg("number_precision"),
        "fact_score": avg("fact_score"),
        "plagiarism_score": avg("plagiarism_score"),
        "gates_failed": sum(1 for a in articles if a.get("gates_passed") is False),
        "sources": len({a.get("source") or a.get("zone_code") for a in articles}),
    }


def compare(*run_refs):
    """Print two or more runs side by side, and what differs in their config."""
    loaded = [_load(r) for r in run_refs]
    summaries = [_summary(a) for a, _, _ in loaded]
    keys = list(summaries[0])

    print("%-18s %s" % ("metric", "  ".join("%-22s" % r for r in run_refs)))
    print("-" * (18 + 24 * len(run_refs)))
    for k in keys:
        print("%-18s %s" % (k, "  ".join("%-22s" % s.get(k) for s in summaries)))

    metas = [m for _, m, _ in loaded]
    diff = {}
    for k in set().union(*[set((m.get("config") or {})) for m in metas]):
        vals = [(m.get("config") or {}).get(k) for m in metas]
        if len({json.dumps(v, sort_keys=True, default=repr) for v in vals}) > 1:
            diff[k] = vals
    print("\nconfig differences:" if diff else "\nconfig identical across runs")
    for k, vals in sorted(diff.items()):
        print("  %-24s %s" % (k, "  ".join(str(v)[:28] for v in vals)))
    if any(m.get("code_hash") != metas[0].get("code_hash") for m in metas):
        print("\n  NOTE: code_hash differs — the source changed between these runs,")
        print("        so a metric difference is not necessarily caused by the config.")
    return summaries


if __name__ == "__main__":
    list_runs()
