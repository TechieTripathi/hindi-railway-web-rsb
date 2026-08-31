"""research/shared/score_report.py — apply deterministic grounding to engine output.

Research-only. No API calls, no webapp edits. Adds new fields alongside the existing
LLM-reported ones so old and new can be compared directly.

Usage
-----
    .venv/bin/python3 research/shared/score_report.py
"""

import json
import os
import sys

# One level down from research/ now, so climb twice.
_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.dirname(_RESEARCH_DIR), _RESEARCH_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import grounding  # noqa: E402

OUTPUT_DIR = os.path.join(_RESEARCH_DIR, "output")
DEFAULT_FILES = [
    os.path.join(OUTPUT_DIR, "web_agency_output_research.json"),
    os.path.join(OUTPUT_DIR, "rsb_agency_output_research.json"),
]


def rescore_file(in_path, out_path=None):
    """Add grounded fields to every article; write *_scored.json. Returns the articles."""
    out_path = out_path or in_path.replace(".json", "_scored.json")
    with open(in_path, encoding="utf-8") as f:
        articles = json.load(f)
    for art in articles:
        art.update(grounding.score_article(art))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)
    return articles, out_path


def _distinct(vals):
    return len({v for v in vals if v is not None})


def report(files=None, verbose=True):
    """Rescore every file and print the proxy-validation table."""
    files = files or [p for p in DEFAULT_FILES if os.path.exists(p)]
    allr = []
    for p in files:
        arts, out = rescore_file(p)
        allr += arts
        if verbose:
            print("  %-42s %2d articles -> %s" % (os.path.basename(p), len(arts),
                                                  os.path.basename(out)))
    if not allr:
        print("No input files found.")
        return allr

    fact = [a.get("fact_score") for a in allr]
    cov = [a.get("coverage_score") for a in allr]
    prec = [a.get("number_precision") for a in allr]

    print("\n=== 1. Discrimination ===")
    print("  %-28s %-10s %s" % ("metric", "distinct", "range"))
    for name, vals in (("fact_score (LLM-reported)", fact),
                       ("coverage_score (measured)", cov),
                       ("number_precision (gate)", prec)):
        v = [x for x in vals if x is not None]
        print("  %-28s %-10d %s" % (name, _distinct(vals),
                                    "%d-%d" % (min(v), max(v)) if v else "-"))

    print("\n=== 2. Negative control (off-topic source) ===")
    failed = [a for a in allr if not a.get("gates_passed")]
    if not failed:
        print("  none flagged")
    for a in failed:
        print("  fact_score=%-3s ceiling=%-3s  %s" % (
            a.get("fact_score"), a.get("grounding_ceiling"), a.get("title", "")[:52]))
        for f in a.get("gate_failures", []):
            print("       - %s" % f)

    print("\n=== 3. Topic vocabulary & evidence ===")
    off = [t for a in allr for t in a.get("topics_off_vocabulary", [])]
    uns = [t for a in allr for t in a.get("topics_unsupported", [])]
    tot = sum(len(a.get("topics") or []) for a in allr)
    print("  %d topics assigned | %d off-vocabulary | %d without source evidence"
          % (tot, len(off), len(uns)))
    if uns:
        from collections import Counter
        print("  unsupported topics:", dict(Counter(uns).most_common(6)))

    print("\n=== 4. Impact: LLM label vs source-derived rule ===")
    from collections import Counter
    print("  LLM :", dict(Counter(a.get("impact_level") for a in allr)))
    print("  rule:", dict(Counter(a.get("impact_rule_level") for a in allr)))
    agree = sum(1 for a in allr if a.get("impact_agrees"))
    print("  agreement: %d/%d (%.0f%%)" % (agree, len(allr), 100 * agree / len(allr)))
    return allr


if __name__ == "__main__":
    report()
