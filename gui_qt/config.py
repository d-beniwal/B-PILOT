"""Persistent GUI configuration (JSON on disk).

Small, dependency-free settings store for things the user should be able to
change without editing code:

* **Files** — which plan files the plan-runner shows (the search scope):
  ``plans_dir`` (folder scanned), ``import_root`` (root the generated
  ``from <module> import <plan>`` line is resolved against),
  ``default_plan_file`` (checked on startup), and ``visible_plan_files`` (an
  explicit whitelist of ``plans_dir``-relative paths that are even shown as
  rows in the file browser — edited via the Configuration dialog's Plan
  visibility card).
* **Launch** — ``bluesky_startup``: the command(s) run in the console when the
  user clicks *Load Bluesky*.

Defaults come from :mod:`plan_parser` (paths) so there is a single source of
truth for the built-in scope; the JSON file only stores user overrides.  The
Configuration dialog reads/writes through here, and the panels read it live, so
changes apply without a restart.
"""
from __future__ import annotations

import json

from . import paths as _paths
from . import plan_parser as _P

# Where the user overrides live — in the GUI bundle dir, next to the manifest.
CONFIG_PATH = _paths.CONFIG_PATH

# Built-in defaults.  Only keys listed here are persisted / accepted.
DEFAULTS: dict = {
    "plans_dir": _P.USER_DIR,
    "import_root": _P.SRC_DIR,
    "default_plan_file": _P.DEFAULT_PLAN_FILE,
    # Whitelist of plans_dir-relative paths (forward-slash separated) shown as
    # rows in the plan-runner's file browser. Explicit, not "empty = show all"
    # — Select-all/Deselect-all in the Configuration dialog cover both extremes.
    "visible_plan_files": [_P.DEFAULT_PLAN_FILE],
    "bluesky_startup": "from instrument.collection import *",
    # Console session persistence / reattach:
    "keep_kernel_on_exit": True,          # leave the kernel running when the GUI closes
    "last_kernel_connection_file": "",    # runtime state — path to reattach to
    # Single-instance kernel (see kernel_session.py):
    "beamline": "20ide",                  # identifies the one-kernel-per-beamline session
    "use_screen": True,                   # host the kernel in a named screen session
    "session_dir": _paths.SESSION_DIR_DEFAULT,  # fixed per-beamline runtime paths
    # Launch mode — how the "Launch IPython" button starts a session:
    #   "embedded" = GUI-managed ipykernel (attach/recorder/queue work)
    #   "script"   = run an external launcher (blueskyStarter.sh) in a screen
    "launch_mode": "embedded",
    "launch_script": _paths.BLUESKY_STARTER,
    # Starter for the EMBEDDED kernel: activates env + records experiment like
    # blueskyStarter.sh, but starts a connectable ipykernel. Empty = launch a
    # bare ipykernel directly (no env activation / collection import).
    "embedded_starter_script": _paths.EMBEDDED_STARTER,
    # Arguments passed to the launch script:  <dm_experiment> <setup_file> <mode>
    "dm_experiment": "",
    "setup_file": "exp_setup.yml",
    "script_run_mode": "screen",          # screen | console | lab
    # Display-only multiplier applied to every font/widget/window size at
    # startup (see style.SCALE) — for high-DPI screens (e.g. 4K). Takes
    # effect on the next launch, not live.
    "ui_scale": 1.0,
}

_cache: dict | None = None


def _load_file() -> dict:
    """Read the JSON override file; return {} if missing/unreadable."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001  (malformed json etc. — fall back to defaults)
        return {}


def as_dict() -> dict:
    """Return the effective config (defaults merged with saved overrides)."""
    global _cache
    if _cache is None:
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in _load_file().items() if k in DEFAULTS})
        _cache = merged
    return dict(_cache)


def get(key: str):
    """Return one effective config value."""
    return as_dict().get(key, DEFAULTS.get(key))


def update(values: dict) -> None:
    """Merge `values` (known keys only) into the config and persist."""
    global _cache
    cfg = as_dict()
    for k, v in values.items():
        if k in DEFAULTS:
            cfg[k] = v
    _cache = cfg
    save()


def save() -> None:
    """Write the current config to disk (best effort).

    Only values that **differ from the computed defaults** are persisted.  The
    path defaults (``plans_dir``, ``launch_script``, ``session_dir`` …) are
    derived from the GUI's own location via :mod:`gui_qt.paths`, so on a clean
    install they equal their default and are *not* written — keeping the saved
    config free of absolute, machine-specific paths and portable across
    machines.  Genuine user overrides are still saved.
    """
    try:
        overrides = {k: v for k, v in as_dict().items() if v != DEFAULTS.get(k)}
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(overrides, fh, indent=2)
    except Exception:  # noqa: BLE001
        pass


def reset() -> dict:
    """Restore built-in defaults, persist, and return them."""
    global _cache
    _cache = dict(DEFAULTS)
    save()
    return dict(_cache)
