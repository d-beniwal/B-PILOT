#!/usr/bin/env python
"""A/B comparison of the draft-then-verify critic pass (`autopilot/critic.py`)
against the existing `prompts_cache/prompts.json` regression corpus.

Runs every case twice -- once with `use_critic=True` (the new default) and
once with `use_critic=False` (the old behavior) -- against the real Argo
gateway, and reports: how many terminal proposals occurred at all, how many
the critic flagged, and the added latency/token cost of the extra call.
This is a real live-Argo comparison, no hardware/EPICS involved, same
safety boundary as `scripts/eval_autopilot.py`.

Usage:
    python scripts/eval_critic.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import settings  # noqa: E402
from autopilot.llm_client import ArgoClient  # noqa: E402
from autopilot.pipeline import converse  # noqa: E402

PROMPTS_PATH = Path(__file__).resolve().parent.parent / "prompts_cache" / "prompts.json"


def run_case(case: dict, profile: str | None, model: str, temperature: float, use_critic: bool) -> dict:
    history = None
    result = None
    t0 = time.monotonic()
    for turn_text in case["turns"]:
        client = ArgoClient(model=model)
        result, history = converse(
            turn_text,
            history=history,
            profile=profile,
            client=client,
            temperature=temperature,
            record=False,
            use_critic=use_critic,
        )
    elapsed = time.monotonic() - t0
    return {
        "id": case["id"],
        "ok": result.ok,
        "tool_name": result.tool_name,
        "critic_flagged": result.critic_flagged,
        "critic_concerns": result.critic_concerns,
        "elapsed_s": round(elapsed, 2),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


def main() -> int:
    cases = json.loads(PROMPTS_PATH.read_text())
    user_settings = settings.load()
    model = user_settings.get("model") or None
    temperature = user_settings.get("temperature", 0.2)
    profile = "s20idd"

    print(f"Running {len(cases)} cases x2 (with/without critic), model={model or '(default)'}...\n")

    with_critic = [run_case(c, profile, model, temperature, use_critic=True) for c in cases]
    without_critic = [run_case(c, profile, model, temperature, use_critic=False) for c in cases]

    proposals_with = [r for r in with_critic if r["tool_name"] and r["tool_name"].startswith("propose_")]
    proposals_without = [r for r in without_critic if r["tool_name"] and r["tool_name"].startswith("propose_")]
    flagged = [r for r in proposals_with if r["critic_flagged"]]

    total_time_with = sum(r["elapsed_s"] for r in with_critic)
    total_time_without = sum(r["elapsed_s"] for r in without_critic)
    total_tokens_with = sum((r["input_tokens"] or 0) + (r["output_tokens"] or 0) for r in with_critic)
    total_tokens_without = sum((r["input_tokens"] or 0) + (r["output_tokens"] or 0) for r in without_critic)

    print(f"Terminal proposals: {len(proposals_with)}/{len(cases)} (with critic), {len(proposals_without)}/{len(cases)} (without)")
    print(f"Proposals flagged by the critic: {len(flagged)}/{len(proposals_with)}")
    for r in flagged:
        print(f"  {r['id']}: {r['critic_concerns']}")
    print(f"\nTotal wall time: {total_time_with:.1f}s (with critic) vs {total_time_without:.1f}s (without)")
    print(f"Total tokens: {total_tokens_with} (with critic) vs {total_tokens_without} (without)")

    out = {"with_critic": with_critic, "without_critic": without_critic}
    out_path = Path(__file__).resolve().parent.parent / "eval_critic_results.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
