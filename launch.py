"""Launch the B-PILOT plan-runner GUI: ``python launch.py`` from this directory.

Equivalent to ``python -m B_PILOT``; provided as a plain, discoverable entry
point for a repo root. Puts this directory on ``sys.path`` so ``B_PILOT`` is
importable regardless of how the interpreter was invoked, then calls the same
``B_PILOT.app.main()`` used by the module form.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from B_PILOT.app import main

if __name__ == "__main__":
    main()
