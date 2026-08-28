"""Adversarial (indirect prompt-injection) evaluation of AutoPILOT, separate
from the mechanical happy-path regression suite in `scripts/eval_autopilot.py`
and its `prompts_cache/prompts.json`.

See `cases.py` for the corpus, `harness.py` for how a case is actually run
(against the real Argo gateway, with one lookup tool's *return value*
monkeypatched per case -- never a real file, never real hardware), and
`run_eval.py` for the CLI entry point.
"""
