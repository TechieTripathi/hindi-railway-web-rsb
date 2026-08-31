"""Load research-local secrets from `research/.env` and bind them onto the webapp.

Why this exists
---------------
`config.py` already calls `load_dotenv()` on the project-root `.env`, and `engine.py`
does `from config import ANTHROPIC_API_KEY` at import time. So the root `.env` alone is
enough to make generation work. This module adds a *research-local* override so a run
can use a different key (a scratch key, a rate-limited key, a key you can rotate without
touching the webapp) without editing any webapp file.

Precedence, highest first:

    1. a genuine shell export       (export ANTHROPIC_API_KEY=... ; jupyter ...)
    2. research/.env
    3. the project-root .env, as loaded by config.py

Note on (1) vs (3): `config.py` calls `load_dotenv()`, which *writes the root .env into
`os.environ`*. So "is it in os.environ?" cannot by itself tell a shell export apart from
the root .env -- and a naive "exported wins" rule would make research/.env dead code.
This module therefore parses the root .env itself: a value in `os.environ` that matches
the root .env is treated as coming from the root .env (overridable), and only a value
that differs is treated as a genuine export (not overridden).

An empty or absent value in `research/.env` deliberately does NOT clobber a key that the
root `.env` already provided -- so committing a placeholder-only `research/.env` is safe.

Nothing on disk is edited. Keys are rebound as module globals on `engine` /
`brave_search`, which is safe because both modules read them as globals *inside* the
functions that use them (`engine.py:275,288`, `brave_search.py:24,237`) rather than
capturing them at call-site definition. Same technique as `hindi_patches.py`.

Usage
-----
    import config_env, engine
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

_PROJECT_ROOT = os.path.dirname(_RESEARCH_DIR)
ROOT_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")


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

    A non-empty value in this file overrides one that `config.py` loaded from the
    project-root `.env`, but not a genuine shell export -- see the module docstring
    for how the two are told apart. `override=True` overrides both. Empty values are
    ignored, so a placeholder line never blanks out a key from elsewhere.
    Returns the mapping of names this call actually set.
    """
    path = path or DEFAULT_ENV_PATH
    local = _read_env_file(path)
    if not local:
        return {}
    root = _read_env_file(ROOT_ENV_PATH)
    rel = os.path.relpath(path, _PROJECT_ROOT)

    applied: dict[str, str] = {}
    for name, value in local.items():
        if not value:
            continue
        current = os.environ.get(name)
        if current and not override:
            # In os.environ but NOT equal to the root .env value -> a real shell
            # export, which outranks this file. Equal (or root .env absent) -> it
            # came from config.py's load_dotenv, so this file wins.
            if name not in root or current != root[name]:
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
        root = _read_env_file(ROOT_ENV_PATH)
        if root.get(name) == os.environ[name]:
            return "project-root .env (via config.py)"
        return "shell export"
    if module_value:
        return "webapp module default"
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
    value is available -- a module global loaded by `config.py` is never blanked.
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
                print("%s.%s is NOT set -- checked process env, research/.env, "
                      "project-root .env" % (label, name))
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
