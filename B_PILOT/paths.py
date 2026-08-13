"""Single source of truth for every filesystem path the GUI needs.

The GUI is meant to be **portable**: it must run from wherever the
``mpe_bluesky`` project is checked out, on any machine, without hard-coded
absolute paths and without depending on the current working directory.  Every
path below is derived from *this file's own location* (``__file__``), so the GUI
always knows where it lives and can map the rest of the project relative to that
— even when the whole ``mpe_bluesky`` folder is moved between workstations.

Two anchors:

* **GUI bundle** — :data:`GUI_DIR` (the ``B_PILOT`` package) and its parent
  :data:`BUNDLE_DIR`.  Files shipped *next to* the GUI (its config, the device
  manifest, the embedded-kernel starter) live here and travel with the GUI if
  the folder is relocated.
* **Project root** — :data:`PROJECT_ROOT`, the ``mpe_bluesky`` directory that
  holds ``instrument/``, ``user/``, ``blueskyStarter.sh`` etc.  By default it
  is found by walking *up* from the GUI looking for those markers, so it stays
  correct even if the GUI is moved to a different depth inside the project.
  Beamline runtime code (``from instrument.collection import *``) resolves
  against this.  If the active profile sets a ``project_root`` override (see
  :mod:`config`), that path is used instead — so B-PILOT can live *anywhere*,
  with the real ``mpe_bluesky`` checkout pointed to explicitly rather than
  found by walking up from B-PILOT's own location. No override (the default)
  means "assume B-PILOT is nested inside the project like today."

Import this module everywhere instead of recomputing ``os.path.dirname(...)``
chains locally.
"""
from __future__ import annotations

import json
import os


def _abs(*parts: str) -> str:
    """Join + normalize into an absolute, canonical path."""
    return os.path.normpath(os.path.join(*parts))


# ── GUI bundle (relative to this file) ───────────────────────────────────────
GUI_DIR = os.path.dirname(os.path.abspath(__file__))   # .../<bundle>/B_PILOT
BUNDLE_DIR = os.path.dirname(GUI_DIR)                   # parent of B_PILOT (e.g. gui/)

# Files shipped alongside the GUI package — they move *with* the GUI bundle:
CONFIG_PATH = _abs(BUNDLE_DIR, "gui_config.json")  # tiny pointer: {"active_profile": name}
PROFILES_DIR = _abs(BUNDLE_DIR, "profiles")         # one JSON file per beamline profile
TEST_PLANS_DIR = _abs(BUNDLE_DIR, "test_plans")  # unused by default; kept for back-compat
EMBEDDED_STARTER = _abs(BUNDLE_DIR, "embedded_kernel_starter.sh")
SESSION_RECORDER = _abs(GUI_DIR, "session_recorder.py")

# Directory to put on sys.path so ``import B_PILOT`` works when a module is run as
# a plain script (``python B_PILOT/app.py``) rather than ``python -m B_PILOT``.
PKG_PARENT = BUNDLE_DIR


# ── Project root (an explicit profile override, else found by walking up) ────
_ROOT_MARKER_DIRS = ("instrument",)                        # must all be present
_ROOT_MARKER_FILES = ("blueskyStarter.sh", "qserver.sh")   # at least one present


def _is_project_root(path: str) -> bool:
    """True if ``path`` looks like the ``mpe_bluesky`` project root: an
    ``instrument/`` subdirectory plus at least one of the known root scripts.

    Shared by the auto-detect walk below and by validation of an explicit
    ``project_root`` profile override, so both use the same definition of "a
    real project root" — an override that doesn't pass this check is
    rejected rather than silently trusted.
    """
    has_dirs = all(os.path.isdir(os.path.join(path, d)) for d in _ROOT_MARKER_DIRS)
    has_file = any(os.path.isfile(os.path.join(path, f)) for f in _ROOT_MARKER_FILES)
    return has_dirs and has_file


def _find_project_root(start: str) -> str:
    """Walk up from ``start`` to the ``mpe_bluesky`` project root (see
    :func:`_is_project_root`).

    Falls back to two levels above the GUI (the ``<root>/B-PILOT/B_PILOT``
    layout) if no marker is found, so the GUI still works before the project
    is fully in place.
    """
    cur = start
    while True:
        if _is_project_root(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:          # reached the filesystem root — stop
            break
        cur = parent
    return os.path.dirname(BUNDLE_DIR)   # fallback: <root>/B-PILOT/B_PILOT


def _read_json_quiet(path: str) -> dict:
    """Minimal, side-effect-free JSON read: no writes, no migrations.

    Deliberately duplicated rather than imported from :mod:`config` — that
    module imports this one, so importing it back here would be circular.
    This function runs at import time, before ``config``'s profile bootstrap/
    migration logic has had any chance to run, so it must tolerate a missing,
    partial, or pre-migration ``profiles/`` layout and never assume anything
    has been created on disk yet.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001  (missing/malformed file -- no override)
        return {}


def _peek_project_root_override() -> str | None:
    """Best-effort read of the active profile's ``project_root`` override.

    Returns ``None`` (meaning "auto-detect, B-PILOT is nested inside the
    project like today") whenever there's no active-profile pointer yet, no
    ``project_root`` key is set, or the configured path doesn't satisfy
    :func:`_is_project_root` — an explicit override is only honored when it
    actually resolves to a real project tree, so a typo'd path can't
    silently break every other path computed below.

    Mirrors (without importing) ``config.py``'s active-profile and
    active-over-default resolution closely enough to predict what it will
    return, but never writes anything — see :func:`_read_json_quiet`.
    """
    pointer = _read_json_quiet(CONFIG_PATH)
    name = pointer.get("active_profile")
    if not name or not isinstance(name, str):
        return None
    profile_dir = _abs(PROFILES_DIR, name)
    active_path = _abs(profile_dir, "active_config.json")
    cfg_path = active_path if os.path.isfile(active_path) else _abs(profile_dir, "default_config.json")
    cfg = _read_json_quiet(cfg_path)
    root = cfg.get("project_root")
    if not isinstance(root, str) or not root.strip():
        return None
    candidate = os.path.normpath(os.path.abspath(os.path.expanduser(root.strip())))
    return candidate if _is_project_root(candidate) else None


PROJECT_ROOT = _peek_project_root_override() or _find_project_root(GUI_DIR)

INSTRUMENT_DIR = _abs(PROJECT_ROOT, "instrument")
PROJECT_USER_DIR = _abs(PROJECT_ROOT, "user")
ICONFIG = _abs(INSTRUMENT_DIR, "iconfig.yml")
BLUESKY_STARTER = _abs(PROJECT_ROOT, "blueskyStarter.sh")

# The real MPE plan directory, scanned by the plan-runner's file browser.
PLANS_DIR = _abs(INSTRUMENT_DIR, "plans")

# Root the generated ``from <module> import <plan>`` line is resolved against
# (module = path of the plan file relative to this root).  With IMPORT_ROOT =
# PROJECT_ROOT, ``instrument/plans/foo.py`` -> ``instrument.plans.foo``.
IMPORT_ROOT = PROJECT_ROOT

# Default working directory for a launched (embedded) kernel: the project root,
# so the RunEngine's ``from instrument.collection import *`` resolves regardless
# of where the GUI itself was started from.
KERNEL_CWD_DEFAULT = PROJECT_ROOT


# ── Runtime state (per-user, NOT part of the repo) ───────────────────────────
# Kernel connection files, the plan queue, and transcripts.  Home-based so it is
# writable and per-user on shared beamline workstations; overridable via the
# ``session_dir`` config key.
SESSION_DIR_DEFAULT = os.path.expanduser("~/.bluesky_pilot")


def ensure_on_syspath() -> None:
    """Put :data:`PKG_PARENT` on ``sys.path`` so ``import B_PILOT`` resolves.

    Safe to call from a script-mode entry point before the package is importable.
    """
    import sys

    if PKG_PARENT not in sys.path:
        sys.path.insert(0, PKG_PARENT)
