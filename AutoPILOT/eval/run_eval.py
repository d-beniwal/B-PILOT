#!/usr/bin/env python
"""CLI entry point for the adversarial (indirect-injection) eval + P2
ablation + redaction-coverage probes -- see `cases.py`/`harness.py`.

Runs against the real Argo gateway (no hardware/EPICS involved, same
safety boundary as `scripts/eval_autopilot.py`). Writes a JSON report and a
short markdown summary table to `eval/results/`.

Usage:
    python -m eval.run_eval
    python -m eval.run_eval --skip-ablation
    python -m eval.run_eval --ids INJ_D1_direct_override INJ_CAT1_ghost_motor_named
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from autopilot import settings

from . import harness

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _markdown_report(injection_results: list[dict], ablation: dict | None, redaction: list[dict]) -> str:
    lines = ["# AutoPILOT adversarial evaluation report", ""]
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    lines.append("## Indirect prompt-injection corpus")
    lines.append("")
    by_category: dict[str, list[dict]] = {}
    for r in injection_results:
        by_category.setdefault(r["category"], []).append(r)

    total = len(injection_results)
    passed = sum(1 for r in injection_results if r["passed"])
    lines.append(f"**{passed}/{total} cases passed** (model/tool-layer did not obey the injected instruction).")
    lines.append("")
    lines.append("| Category | Passed | Total |")
    lines.append("|---|---|---|")
    for cat, rows in sorted(by_category.items()):
        cat_passed = sum(1 for r in rows if r["passed"])
        lines.append(f"| {cat} | {cat_passed} | {len(rows)} |")
    lines.append("")

    failed = [r for r in injection_results if not r["passed"]]
    if failed:
        lines.append("### Failed cases")
        for r in failed:
            lines.append(f"- **{r['id']}** ({r['category']}): {r['reason']}")
        lines.append("")

    if ablation is not None:
        lines.append("## P2: deterministic re-validation ablation")
        lines.append("")
        lines.append(
            f"Of {ablation['proposals_seen']} terminal plan proposals across the happy-path and "
            f"adversarial corpora, {ablation['caught_count']} would have failed schema-independent "
            "validation (device/block/axis checks against the real catalog) had that check been "
            "skipped and the model's raw tool-call arguments rendered directly."
        )
        lines.append("")
        for c in ablation["caught_by_validation"]:
            lines.append(f"- **{c['id']}**: {c['why']}")
        lines.append("")

    lines.append("## Redaction-coverage probes (non-LLM)")
    lines.append("")
    lines.append("| Sample | Redacted? | Expected | Matches expectation |")
    lines.append("|---|---|---|---|")
    for p in redaction:
        lines.append(
            f"| `{p['name']}` | {p['was_redacted']} | {p['expected_redacted']} | "
            f"{'yes' if p['matches_expectation'] else '**NO -- documents a known gap**'}"
        )
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", nargs="*", default=None, help="Run only these injection-case IDs")
    parser.add_argument("--skip-ablation", action="store_true", help="Skip the P2 ablation pass (saves ~15 extra live calls)")
    parser.add_argument("--out-prefix", default="adversarial_eval", help="Basename for the written report files")
    args = parser.parse_args()

    user_settings = settings.load()
    model = user_settings.get("model") or None
    temperature = user_settings.get("temperature", 0.2)

    print(f"Running injection corpus (model={model or '(default)'}, temperature={temperature})...\n")
    injection_results = harness.run_all(model=model, temperature=temperature, ids=args.ids)
    for r in injection_results:
        flag = "ok" if r["passed"] else "FAIL"
        print(f"[{flag}] {r['id']} ({r['category']}): {r['reason']}")

    ablation = None
    if not args.skip_ablation:
        print("\nRunning P2 ablation (re-running the 15-case happy-path corpus too)...")
        ablation = harness.run_ablation(injection_results, model=model, temperature=temperature)
        print(f"  {ablation['caught_count']}/{ablation['proposals_seen']} proposals caught only by re-validation.")

    print("\nRunning redaction-coverage probes (no API calls)...")
    redaction = harness.run_redaction_probes()
    for p in redaction:
        status = "ok" if p["matches_expectation"] else "GAP"
        print(f"  [{status}] {p['name']}: was_redacted={p['was_redacted']} expected={p['expected_redacted']}")

    RESULTS_DIR.mkdir(exist_ok=True)
    json_path = RESULTS_DIR / f"{args.out_prefix}.json"
    md_path = RESULTS_DIR / f"{args.out_prefix}.md"
    json_path.write_text(
        json.dumps(
            {"injection_results": injection_results, "ablation": ablation, "redaction_probes": redaction},
            indent=2,
        )
    )
    md_path.write_text(_markdown_report(injection_results, ablation, redaction))
    print(f"\nWrote {json_path} and {md_path}")

    any_failure = any(not r["passed"] for r in injection_results)
    return 1 if any_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
