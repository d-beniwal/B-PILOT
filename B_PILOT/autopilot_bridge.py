"""Optional bridge to AutoPILOT (``../AutoPILOT``), B-PILOT's agentic AI layer.

Guarded import -- mirrors the ``fcntl`` idiom in ``queue_store.py`` /
``queue_runner.py``, the only existing precedent in this codebase for a
dependency that degrades gracefully rather than being required. B-PILOT must
keep working with ``AutoPILOT/`` absent or deleted; nothing outside this
module should assume AutoPILOT is present.
"""
from __future__ import annotations

import os
import sys

from . import paths

_AUTOPILOT_DIR = os.path.join(paths.BUNDLE_DIR, "AutoPILOT")
_DIR_EXISTS = os.path.isdir(_AUTOPILOT_DIR)
if _DIR_EXISTS and _AUTOPILOT_DIR not in sys.path:
    sys.path.insert(0, _AUTOPILOT_DIR)

IMPORT_ERROR: str | None = None
try:
    from autopilot.gui.chat_panel import ChatDockWidget  # noqa: F401
    AVAILABLE = True
except ImportError as exc:
    ChatDockWidget = None  # type: ignore[assignment]
    AVAILABLE = False
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def diagnose() -> list[str]:
    """Explain why AutoPILOT isn't available, for a click-triggered popup
    instead of the ribbon tab just doing nothing. Empty once AVAILABLE.

    Checks the two failure modes ``.context/DEPLOY.md`` calls out as easy to
    miss on a fresh beamline checkout -- the ``AutoPILOT/`` folder not being
    copied in, and the ``anthropic`` package not being installed in the GUI's
    env -- then falls back to the raw import error for anything else.
    """
    if AVAILABLE:
        return []
    if not _DIR_EXISTS:
        return [
            f"The AutoPILOT/ folder isn't present at:\n{_AUTOPILOT_DIR}\n"
            "It needs to be copied/cloned in next to this B-PILOT checkout."
        ]
    issues: list[str] = []
    try:
        import anthropic  # noqa: F401
    except ImportError:
        issues.append(
            "The 'anthropic' Python package isn't installed in this GUI's "
            "environment. Install it with:\n"
            "    pip install anthropic\n"
            "(or: pip install -r AutoPILOT/requirements.txt)"
        )
    if IMPORT_ERROR:
        issues.append(f"Import error: {IMPORT_ERROR}")
    return issues
