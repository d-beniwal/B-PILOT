#!/usr/bin/env python
"""CLI test harness for the AutoPILOT plan-builder pipeline (no Qt/GUI).

Usage:
    python scripts/try_plan_builder.py --smoke-test
    python scripts/try_plan_builder.py "step scan samE from 0 to 10 mm in 21 steps, 1s exposure on pimega"
    python scripts/try_plan_builder.py --profile s20ide "count tc32E for 1s, 5 readings"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot.llm_client import ArgoClient  # noqa: E402
from autopilot.pipeline import generate_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("request", nargs="?", help="Natural-language scan description")
    parser.add_argument("--profile", default=None, help="Beamline profile name (default: active profile)")
    parser.add_argument("--smoke-test", action="store_true", help="Just check Argo connectivity and exit")
    args = parser.parse_args()

    if args.smoke_test:
        client = ArgoClient()
        print(f"Calling Argo ({client.base_url}, model={client.model})...")
        print("Response:", client.smoke_test())
        return 0

    if not args.request:
        parser.error("a natural-language request is required unless --smoke-test is given")

    result = generate_plan(args.request, profile=args.profile)

    if result.template_key:
        print(f"Template: {result.template_key}")
    if result.model:
        print(f"Model: {result.model}")
    if result.tool_calls:
        print("Tools used:", " -> ".join(result.tool_calls))
    elif result.tool_name:
        print(f"Tool called: {result.tool_name}")
    if result.raw_spec is not None:
        print("Raw spec from model:", result.raw_spec)

    if not result.ok:
        print(result.message)
        for err in result.errors or []:
            print(" -", err)
        return 1

    print("Validated spec:", result.clean_spec)
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
