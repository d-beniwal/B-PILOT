"""PyQt5-dependent AutoPILOT code.

Kept isolated from the rest of `autopilot/` (which is Qt-free and runs from
AutoPILOT's own CLI-only venv) because this subpackage only ever runs
embedded inside B-PILOT's own process -- it relies on B-PILOT's own PyQt5
install, not a separate one.
"""
