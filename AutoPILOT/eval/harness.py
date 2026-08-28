"""Runs `cases.ALL_CASES` against the real Argo gateway and the real
`s20idd` device catalog -- no hardware/EPICS ever touched, no mocking of
`pipeline.converse()` itself, only a monkeypatch of the one lookup-tool
function each case names, restored immediately after.

Also implements:

* the P2 ablation -- for every case in this corpus plus the existing
  `prompts_cache/prompts.json` happy-path corpus, additionally renders the
  model's terminal proposal *without* `plan_spec.validate()`'s
  re-validation, and counts how many raw proposals would have gone through
  unchecked;
* three non-LLM redaction-coverage probes against `tools.redact()` directly
  (documents the already-known gap: the regex is URL-shaped-credential-only).
"""
from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from autopilot import pipeline, plan_spec, tools
from autopilot.llm_client import ArgoClient

from . import cases as case_defs

PROFILE = "s20idd"


@contextlib.contextmanager
def _patched(tool_name: str, fn):
    original = getattr(tools, tool_name)
    setattr(tools, tool_name, fn)
    try:
        yield
    finally:
        setattr(tools, tool_name, original)


@contextlib.contextmanager
def _patched_chain(case: case_defs.InjectionCase):
    """Chained cases need both search_codebase and read_source_file patched
    at once -- `cases.py` stashes the read_source_file replacement on the
    case's `patch_fn` closure via `_chained_search_then_read`'s second
    return value, recovered here by re-deriving it from the case id."""
    if case.category != "chained":
        with _patched(case.patch_tool, case.patch_fn):
            yield
        return

    # Re-derive the matching read_source_file fixture the same way cases.py
    # built it, keyed by the file path embedded in the search fixture.
    search_result = case.patch_fn("dummy", None, None)
    second_path = search_result["matches"][0]["file"]
    canary = case.id.replace("INJ_", "INJ-").replace("_two_hop", "-CANARY")
    _, read_fn = case_defs._chained_search_then_read(second_path, canary)
    with _patched("search_codebase", case.patch_fn), _patched("read_source_file", read_fn):
        yield


def run_case(case: case_defs.InjectionCase, model: str | None, temperature: float) -> dict:
    client = ArgoClient(model=model)
    t0 = time.monotonic()
    with _patched_chain(case):
        result, messages = pipeline.converse(
            case.request, history=None, profile=PROFILE, client=client, temperature=temperature, record=False
        )
    elapsed = time.monotonic() - t0
    passed, reason = case.check(result, messages)
    return {
        "id": case.id,
        "category": case.category,
        "passed": passed,
        "reason": reason,
        "notes": case.notes,
        "message": result.message,
        "tool_name": result.tool_name,
        "tool_calls": result.tool_calls,
        "raw_spec": result.raw_spec,
        "elapsed_s": round(elapsed, 2),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


def run_all(model: str | None = None, temperature: float = 0.2, ids: list[str] | None = None) -> list[dict]:
    wanted = set(ids) if ids else None
    results = []
    for case in case_defs.ALL_CASES:
        if wanted and case.id not in wanted:
            continue
        results.append(run_case(case, model, temperature))
    return results


# ---------------------------------------------------------------------------
# P2: ablation of the deterministic re-validation layer.
#
# For every terminal `propose_*_plan` tool call the model makes -- across
# both this module's adversarial corpus and the existing 15-case happy-path
# corpus, so the ablation measures real, already-collected traffic rather
# than a bespoke second corpus -- independently re-run `plan_spec.validate()`
# against the model's raw, un-rendered tool-call arguments (`raw_spec`).
# `raw_spec` is exactly what a hypothetical implementation that skipped
# re-validation and rendered the model's tool call directly would have used;
# counting how often that raises tells us how often the deterministic
# validation layer is doing real work, not just agreeing with a model that
# was already going to be right.
# ---------------------------------------------------------------------------

def _would_validation_reject(tool_name: str | None, raw_spec: dict | None, catalog, blocks) -> tuple[bool, str]:
    if tool_name is None or not tool_name.startswith("propose_") or not tool_name.endswith("_plan"):
        return False, "not a proposal turn"
    template_key = tool_name[len("propose_"):-len("_plan")]
    from autopilot import plan_context

    template = plan_context.TEMPLATES.get(template_key)
    if template is None:
        return False, "unknown template"
    try:
        plan_spec.validate(template, raw_spec or {}, catalog, blocks)
        return False, "raw arguments already pass validation"
    except plan_spec.ValidationError as exc:
        return True, "; ".join(exc.errors)


def run_ablation(injection_results: list[dict], model: str | None = None, temperature: float = 0.2) -> dict:
    """`injection_results` should be the already-computed output of
    `run_all()` -- passed in rather than recomputed here so this doesn't
    double the number of live Argo calls against the adversarial corpus.
    Only the happy-path corpus below is freshly (re-)run, since P2 needs its
    `raw_spec`/`tool_name` pair too and `eval_autopilot.py`'s own runs don't
    persist those.
    """
    from autopilot import device_catalog, plan_catalog

    catalog = device_catalog.load(PROFILE)
    blocks = plan_catalog.building_blocks(PROFILE)

    happy_path_cases = json.loads(
        (Path(__file__).resolve().parent.parent / "prompts_cache" / "prompts.json").read_text()
    )

    proposals_seen = 0
    caught = []

    for injection_result in injection_results:
        rejected, why = _would_validation_reject(
            injection_result["tool_name"], injection_result["raw_spec"], catalog, blocks
        )
        if injection_result["tool_name"] and injection_result["tool_name"].startswith("propose_"):
            proposals_seen += 1
        if rejected:
            caught.append({"id": injection_result["id"], "why": why})

    for case in happy_path_cases:
        client = ArgoClient(model=model)
        history = None
        result = None
        for turn_text in case["turns"]:
            result, history = pipeline.converse(
                turn_text, history=history, profile=PROFILE, client=client, temperature=temperature, record=False
            )
        rejected, why = _would_validation_reject(result.tool_name, result.raw_spec, catalog, blocks)
        if result.tool_name and result.tool_name.startswith("propose_"):
            proposals_seen += 1
        if rejected:
            caught.append({"id": case["id"], "why": why})

    return {"proposals_seen": proposals_seen, "caught_by_validation": caught, "caught_count": len(caught)}


# ---------------------------------------------------------------------------
# Redaction-coverage probes (non-LLM, deterministic, zero API cost).
# Documents the already-known gap noted in tools.py's own module comment:
# `_CREDENTIAL_URL_RE` matches only `scheme://user:pass@host`, not a bare
# token/key in any other shape.
# ---------------------------------------------------------------------------

REDACTION_PROBES = [
    ("mongo_url", "mongodb://user:hunter2@dbhost:27017/beamline", True),  # expected to BE redacted
    ("aws_style_pair", "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", False),  # expected gap
    ("bearer_token", "Authorization: Bearer sk-live-9f8e7d6c5b4a3210", False),  # expected gap
    ("generic_apikey", "api_key: 4f1c9e6a2b7d4c3e8f0a1b2c3d4e5f60", False),  # expected gap
]


def run_redaction_probes() -> list[dict]:
    results = []
    for name, sample, expect_redacted in REDACTION_PROBES:
        redacted = tools.redact(sample)
        was_redacted = redacted != sample
        results.append(
            {
                "name": name,
                "sample": sample,
                "redacted_output": redacted,
                "was_redacted": was_redacted,
                "expected_redacted": expect_redacted,
                "matches_expectation": was_redacted == expect_redacted,
            }
        )
    return results
