"""Single source of truth for every filesystem path the GUI needs.

The GUI is meant to be **portable**: it must run from wherever the
``mpe_bluesky`` project is checked out, on any machine, without hard-coded
absolute paths and without depending on the current working directory.  Every
path below is derived from *this file's own location* (``__file__``), so the GUI
always knows where it lives and can map the rest of the project relative to that
— even when the whole ``mpe_bluesky`` folder is moved between workstations.

Two anchors:

* **GUI bundle** — :data:`GUI_DIR` (the ``gui_qt`` package) and its parent
  :data:`BUNDLE_DIR`.  Files shipped *next to* the GUI (its config, the device
  manifest, the embedded-kernel starter) live here and travel with the GUI if
  the folder is relocated.
* **Project root** — :data:`PROJECT_ROOT`, the ``mpe_bluesky`` directory that
  holds ``instrument/``, ``user/``, ``blueskyStarter.sh`` etc.  It is found by
  walking *up* from the GUI looking for those markers, so it stays correct even
  if the GUI is moved to a different depth inside the project.  Beamline runtime
  code (``from instrument.collection import *``) resolves against this.

Import this module everywhere instead of recomputing ``os.path.dirname(...)``
chains locally.
"""
from __future__ import annotations

import os


def _abs(*parts: str) -> str:
    """Join + normalize into an absolute, canonical path."""
    return os.path.normpath(os.path.join(*parts))


# ── GUI bundle (relative to this file) ───────────────────────────────────────
GUI_DIR = os.path.dirname(os.path.abspath(__file__))   # .../<bundle>/gui_qt
BUNDLE_DIR = os.path.dirname(GUI_DIR)                   # parent of gui_qt (e.g. gui/)

# Files shipped alongside the GUI package — they move *with* the GUI bundle:
CONFIG_PATH = _abs(BUNDLE_DIR, "gui_config.json")
DEVICE_MANIFEST = _abs(BUNDLE_DIR, "device_manifest.yml")
TEST_PLANS_DIR = _abs(BUNDLE_DIR, "test_plans")  # unused by default; kept for back-compat
EMBEDDED_STARTER = _abs(BUNDLE_DIR, "embedded_kernel_starter.sh")
SESSION_RECORDER = _abs(GUI_DIR, "session_recorder.py")

# Directory to put on sys.path so ``import gui_qt`` works when a module is run as
# a plain script (``python gui_qt/app.py``) rather than ``python -m gui_qt``.
PKG_PARENT = BUNDLE_DIR


# ── Project root (found by walking up for markers) ───────────────────────────
_ROOT_MARKER_DIRS = ("instrument",)                        # must all be present
_ROOT_MARKER_FILES = ("blueskyStarter.sh", "qserver.sh")   # at least one present


def _find_project_root(start: str) -> str:
    """Walk up from ``start`` to the ``mpe_bluesky`` project root.

    A directory qualifies when it contains an ``instrument/`` subdirectory *and*
    at least one of the known root scripts.  Falls back to two levels above the
    GUI (the ``<root>/B-PILOT/gui_qt`` layout) if no marker is found, so the GUI
    still works before the project is fully in place.
    """
    cur = start
    while True:
        has_dirs = all(os.path.isdir(os.path.join(cur, d)) for d in _ROOT_MARKER_DIRS)
        has_file = any(os.path.isfile(os.path.join(cur, f)) for f in _ROOT_MARKER_FILES)
        if has_dirs and has_file:
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:          # reached the filesystem root — stop
            break
        cur = parent
    return os.path.dirname(BUNDLE_DIR)   # fallback: <root>/B-PILOT/gui_qt


PROJECT_ROOT = _find_project_root(GUI_DIR)

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
    """Put :data:`PKG_PARENT` on ``sys.path`` so ``import gui_qt`` resolves.

    Safe to call from a script-mode entry point before the package is importable.
    """
    import sys

    if PKG_PARENT not in sys.path:
        sys.path.insert(0, PKG_PARENT)
