"""Launch the B-PILOT plan-runner GUI: ``python launch.py`` from this directory.

Equivalent to ``python -m gui_qt``; provided as a plain, discoverable entry
point for a repo root. Puts this directory on ``sys.path`` so ``gui_qt`` is
importable regardless of how the interpreter was invoked, then calls the same
``gui_qt.app.main()`` used by the module form.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui_qt.app import main

if __name__ == "__main__":
    main()
