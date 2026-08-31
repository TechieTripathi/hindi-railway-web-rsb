"""research/shared/provenance.py — record what produced a result.

Without this you cannot tell whether a metric moved because the code changed or
because the corpus did. Every output file gets a `_meta` block naming the code
revision, the effective config, and the library backend in use.
"""

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime

# One level down from research/ now, so climb twice.
_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_RESEARCH_DIR)


def git_revision():
    """Short SHA plus a dirty flag, or None outside a git checkout."""
    try:
        sha = subprocess.check_output(
            ["git", "-C", _PROJECT_ROOT, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
        dirty = subprocess.check_output(
            ["git", "-C", _PROJECT_ROOT, "status", "--porcelain", "--", "research"],
            stderr=subprocess.DEVNULL, text=True).strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return None


def config_snapshot(module):
    """Every public scalar/collection setting on a config module, JSON-safe."""
    out = {}
    for k in dir(module):
        if k.startswith("_") or k.isupper() is False:
            continue
        v = getattr(module, k)
        if isinstance(v, (str, int, float, bool, list, dict, tuple)):
            out[k] = v
        else:
            out[k] = repr(v)          # compiled regexes etc.
    return out


def config_hash(module):
    """Stable digest of a config module — changes when any setting changes."""
    blob = json.dumps(config_snapshot(module), sort_keys=True, ensure_ascii=False,
                      default=repr)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


#: Package directories walked by code_hash, relative to research/.
_CODE_DIRS = ("", "shared", "web_search", "rsb_search")


def code_hash(filenames=None):
    """Digest of the research source files, so a code edit is visible in the output.

    Walks the subpackages as well as research/ itself. A flat `os.listdir` here would
    silently hash only ui.py / experiment.py once the pipelines moved into
    web_search/, rsb_search/ and shared/ — and a changed crawler would produce an
    unchanged `code_hash`, which is precisely the failure this field exists to catch.
    """
    if filenames:
        names = list(filenames)
    else:
        names = []
        for sub in _CODE_DIRS:
            d = os.path.join(_RESEARCH_DIR, sub) if sub else _RESEARCH_DIR
            if not os.path.isdir(d):
                continue
            names += sorted(os.path.join(sub, f) for f in os.listdir(d)
                            if f.endswith(".py"))
    h = hashlib.sha256()
    for name in sorted(names):
        p = os.path.join(_RESEARCH_DIR, name)
        if os.path.isfile(p):
            h.update(name.encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()[:12]


def new_run_id(label=None):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return "%s-%s" % (stamp, label) if label else "%s-%s" % (stamp, uuid.uuid4().hex[:6])


def build_meta(run_id, pipeline, config_module=None, extra=None):
    """The `_meta` block written alongside every result set."""
    meta = {
        "run_id": run_id,
        "pipeline": pipeline,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_revision": git_revision(),
        "code_hash": code_hash(),
        "python": sys.version.split()[0],
    }
    if config_module is not None:
        meta["config_module"] = config_module.__name__
        meta["config_hash"] = config_hash(config_module)
        meta["config"] = config_snapshot(config_module)
    try:
        import hindi_text
        meta["hindi_backend"] = hindi_text.BACKEND
    except Exception:
        pass
    if extra:
        meta.update(extra)
    return meta


def write_with_meta(path, articles, meta):
    """Write results plus provenance. Articles stay a plain list under `articles`."""
    payload = {"_meta": meta, "articles": articles}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def read_articles(path):
    """Read either a bare list (legacy) or a {_meta, articles} payload."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "articles" in data:
        return data["articles"], data.get("_meta", {})
    return data, {}
