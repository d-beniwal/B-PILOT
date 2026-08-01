#!/usr/bin/env python
"""Regression runner over `prompts_cache/prompts.json` (no Qt/GUI).

Runs each case's turns through `pipeline.converse()` against the real Argo
gateway and the active (or given) beamline profile's real device catalog --
no hardware/EPICS involved. Uses whatever model/temperature the chat dock
itself would use (`autopilot.settings.load()`), so results reflect the
actually-deployed configuration.

This only flags mechanical failures (an exception, an empty/placeholder
reply, an unresolved tool call after max_turns) -- it does not grade
semantic correctness. Read the written transcript for that judgment call.

Usage:
    python scripts/eval_autopilot.py
    python scripts/eval_autopilot.py --profile s20idd
    python scripts/eval_autopilot.py --ids P10_docstring_assist_real_file P12_physically_unreasonable_params
    python scripts/eval_autopilot.py --out /tmp/run1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import settings  # noqa: E402
from autopilot.llm_client import ArgoClient  # noqa: E402
from autopilot.pipeline import converse  # noqa: E402

PROMPTS_PATH = Path(__file__).resolve().parent.parent / "prompts_cache" / "prompts.json"

_MECHANICAL_FAILURE_MARKERS = (
    "(no response)",
    "Argo call failed:",
    "I wasn't able to reach a decision after several lookups",
    "The model didn't return a usable reply",
)


def load_cases(ids: list[str] | None) -> list[dict]:
    cases = json.loads(PROMPTS_PATH.read_text())
    if ids:
        wanted = set(ids)
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def run_case(case: dict, profile: str | None, model: str, temperature: float) -> dict:
    history = None
    turn_results = []
    mechanical_failure = False

    for turn_text in case["turns"]:
        client = ArgoClient(model=model)
        result, history = converse(turn_text, history=history, profile=profile, client=client, temperature=temperature)
        flagged = result.message is not None and any(marker in result.message for marker in _MECHANICAL_FAILURE_MARKERS)
        mechanical_failure = mechanical_failure or flagged
        turn_results.append(
            {
                "request": turn_text,
                "ok": result.ok,
                "message": result.message,
                "template_key": result.template_key,
                "tool_name": result.tool_name,
                "tool_calls": result.tool_calls,
                "clean_spec": result.clean_spec,
                "errors": result.errors,
                "filepath": result.filepath,
                "gui_command": result.gui_command,
                "mechanical_failure": flagged,
            }
        )

    return {
        "id": case["id"],
        "category": case.get("category"),
        "notes": case.get("notes"),
        "mechanical_failure": mechanical_failure,
        "turns": turn_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default=None, help="Beamline profile name (default: active profile)")
    parser.add_argument("--ids", nargs="*", default=None, help="Run only these case IDs (default: all)")
    parser.add_argument("--out", default=None, help="Write full transcripts to this JSON path")
    args = parser.parse_args()

    cases = load_cases(args.ids)
    if not cases:
        print("No matching cases in prompts_cache/prompts.json.")
        return 1

    user_settings = settings.load()
    model = user_settings.get("model") or None
    temperature = user_settings.get("temperature", 0.2)
    print(f"Running {len(cases)} case(s) (model={model or '(default)'}, temperature={temperature})...\n")

    results = []
    any_failure = False
    for case in cases:
        result = run_case(case, args.profile, model, temperature)
        results.append(result)
        any_failure = any_failure or result["mechanical_failure"]
        flag = "FAIL" if result["mechanical_failure"] else "ok"
        last_turn = result["turns"][-1]
        summary = last_turn["tool_name"] or " -> ".join(last_turn["tool_calls"] or []) or "(no tool call)"
        print(f"[{flag}] {result['id']} ({result['category']}): {summary}")

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"\nFull transcripts written to {out_path}")

    print(f"\n{sum(1 for r in results if not r['mechanical_failure'])}/{len(results)} cases had no mechanical failure.")
    return 1 if any_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
