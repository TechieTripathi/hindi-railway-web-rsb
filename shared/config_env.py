"""Load research secrets from `research/.env` and bind them onto the generation engine.

Why this exists
---------------
`vendor/config.py` reads `ANTHROPIC_API_KEY` from the process environment once, at
import time, and loads no `.env` file. This module supplies the key from
`research/.env` without anything having to be exported first.

Precedence, highest first:

    1. a genuine shell export       (export ANTHROPIC_API_KEY=... ; jupyter ...)
    2. research/.env

Nothing else writes these names into `os.environ`, so a value already there when this
module runs is a shell export, and it outranks the file.

An empty or absent value in `research/.env` deliberately does NOT clobber an exported
key -- so committing a placeholder-only `research/.env` is safe.

Nothing on disk is edited. Keys are rebound as module globals on `engine`, which is
safe because `generate_news` reads `ANTHROPIC_API_KEY` as a global *inside* the
function rather than capturing it at definition. Same technique as `hindi_patches.py`.

Usage
-----
    from shared import config_env
    from vendor import engine
    if not config_env.apply_to_engine(engine):
        print('Generation would return empty results.')

Values are never printed -- only which source won, and the length.
"""

from __future__ import annotations

import os

# One level down from research/ now, so climb twice.
_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV_PATH = os.path.join(_RESEARCH_DIR, ".env")

#: Names this module knows how to load and bind.
KNOWN_KEYS = ("ANTHROPIC_API_KEY", "BRAVE_API_KEY")

#: Set by load_env(): name -> human-readable source, for reporting only.
_source: dict[str, str] = {}


def parse_env(text: str) -> dict[str, str]:
    """Parse `KEY=VALUE` lines. Dependency-free, so research/ needs no python-dotenv.

    Skips blanks, comments and lines without `=`; strips one layer of matching
    single or double quotes; tolerates an `export ` prefix.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name] = value
    return out


def _read_env_file(path: str) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_env(fh.read())
    except FileNotFoundError:
        return {}


def load_env(path: str | None = None, override: bool = False) -> dict[str, str]:
    """Load `path` (default `research/.env`) into `os.environ`.

    A value already in `os.environ` is a shell export and wins, unless
    `override=True`. Empty values are ignored, so a placeholder line never blanks
    out an exported key. Safe to call repeatedly.
    Returns the mapping of names this call actually set.
    """
    path = path or DEFAULT_ENV_PATH
    local = _read_env_file(path)
    if not local:
        return {}
    rel = os.path.relpath(path, _RESEARCH_DIR)

    applied: dict[str, str] = {}
    for name, value in local.items():
        if not value:
            continue
        current = os.environ.get(name)
        if current == value:
            # Already in place: set by an earlier call, or exported with the same value.
            _source.setdefault(name, rel)
            continue
        if current and not override:
            _source[name] = "shell export"
            continue
        os.environ[name] = value
        applied[name] = value
        _source[name] = rel
    return applied


def get(name: str, path: str | None = None) -> str:
    """Value of `name` after loading `research/.env`; `""` if unset anywhere."""
    load_env(path)
    return os.environ.get(name, "")


def anthropic_key(path: str | None = None) -> str:
    return get("ANTHROPIC_API_KEY", path)


def _source_of(name: str, module_value: str) -> str:
    if name in _source:
        return _source[name]
    if os.environ.get(name):
        return "shell export"
    if module_value:
        return "module default"
    return "nowhere"


def describe(name: str, module_value: str = "", path: str | None = None) -> dict:
    """Reportable state of one key: is it set, how long, and where it came from.

    For UIs and status output. Never returns the value itself.
    """
    load_env(path)
    value = os.environ.get(name, "") or module_value
    return {
        "name": name,
        "set": bool(value),
        "length": len(value),
        "source": _source_of(name, module_value),
    }


def apply_to_module(module, names=KNOWN_KEYS, path=None, verbose=True) -> bool:
    """Rebind `names` as globals on `module` from the environment.

    Only names the module already defines are touched, and only when a non-empty
    value is available -- a module global that is already set is never blanked.
    Returns True if every name the module defines ended up non-empty.
    """
    load_env(path)
    ok = True
    for name in names:
        if not hasattr(module, name):
            continue
        current = getattr(module, name) or ""
        value = os.environ.get(name, "") or current
        setattr(module, name, value)
        if verbose:
            label = getattr(module, "__name__", module)
            if value:
                print("%s.%s <- %s (%d chars)"
                      % (label, name, _source_of(name, current), len(value)))
            else:
                print("%s.%s is NOT set -- checked the process environment and "
                      "research/.env" % (label, name))
        if not value:
            ok = False
    return ok


def apply_to_engine(engine, verbose: bool = True) -> bool:
    """Bind ANTHROPIC_API_KEY onto `engine`. True if a key is present."""
    return apply_to_module(engine, ("ANTHROPIC_API_KEY",), verbose=verbose)


if __name__ == "__main__":
    load_env()
    for _name in KNOWN_KEYS:
        _v = os.environ.get(_name, "")
        print("%-18s %s  (%s)"
              % (_name, "set, %d chars" % len(_v) if _v else "NOT set",
                 _source_of(_name, "")))
