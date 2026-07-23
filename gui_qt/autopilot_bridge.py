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
if os.path.isdir(_AUTOPILOT_DIR) and _AUTOPILOT_DIR not in sys.path:
    sys.path.insert(0, _AUTOPILOT_DIR)

try:
    from autopilot.gui.chat_panel import ChatDockWidget  # noqa: F401
    AVAILABLE = True
except ImportError:
    ChatDockWidget = None  # type: ignore[assignment]
    AVAILABLE = False
