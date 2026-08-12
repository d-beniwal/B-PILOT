"""Puts B-PILOT's ``gui_qt`` package on ``sys.path`` so AutoPILOT can reuse its
Qt-free backend modules (``config``, ``device_discovery``, ``device_source``,
``plan_parser``, ``databroker_access``) directly instead of re-implementing them.

This is the one allowed dependency direction (AutoPILOT -> B-PILOT). B-PILOT's
own code must never import anything from ``AutoPILOT/`` -- see
``.context/ARCHITECTURE.md``.
"""
from __future__ import annotations

import os
import sys

_AUTOPILOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BPILOT_DIR = os.path.dirname(_AUTOPILOT_DIR)


def ensure_bpilot_on_path() -> None:
    """Idempotently add B-PILOT's directory to ``sys.path`` (for ``import gui_qt...``)."""
    if BPILOT_DIR not in sys.path:
        sys.path.insert(0, BPILOT_DIR)
