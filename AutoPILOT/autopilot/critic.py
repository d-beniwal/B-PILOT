"""A second, independent LLM turn that reviews a validated plan proposal
before it is presented to the user -- a draft-then-verify pattern layered
on top of `pipeline.converse()`'s existing single-agent ReAct loop.

Nothing here can change what gets built or run: `plan_spec.validate()`
already decided the proposal is schema-valid before this module is ever
called, and this module's only effect is to add a disclosed caveat to the
reply AutoPILOT is about to send -- exactly like the existing
transparency-heuristic and sanity-check notes it sits alongside in
`pipeline.py`. It never re-runs, blocks, or silently edits the proposal.
This mirrors the fact that "schema-valid" and "matches what the user
actually meant" are different claims (the same reasoning behind
`plan_spec.validate()`'s own re-validation of the model's first tool call);
here a second, independently-prompted model instance checks the second
claim, since the first instance already committed to an answer and cannot
usefully grade its own work.

Failure-open by design: if the critic call itself fails (network, a
malformed tool response), `review_proposal` returns `flagged=False` with
`error` set, rather than blocking or degrading the (already-validated)
proposal on a second system's availability. Cost/latency note: this is one
additional Argo call per proposal, only for terminal proposals that were
already going to succeed -- lookups, declines, and clarifying questions
never reach this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from .llm_client import ArgoClient

REVIEW_TOOL_NAME = "submit_review"


def _build_review_tool_schema() -> dict:
    return {
        "name": REVIEW_TOOL_NAME,
        "description": "Submit your review verdict for the drafted plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "matches_intent": {
                    "type": "boolean",
                    "description": "True if the drafted command plausibly matches what the user actually asked for.",
                },
                "concerns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Short, specific concerns, if any: an unrequested device "
                        "substitution beyond what was already disclosed, a "
                        "magnitude mismatch (e.g. '5 steps' asked, 500 drafted), "
                        "a missing part of a multi-part request, or anything "
                        "else that looks like a misread. Empty list if none."
                    ),
                },
            },
            "required": ["matches_intent", "concerns"],
        },
    }


_SYSTEM_PROMPT = (
    "You are reviewing a plan another instance of yourself just drafted for a "
    "Bluesky beamline scan, before it is shown to the user. You did not write "
    "this draft and cannot edit or execute it -- your only job is to check it "
    "against the user's original request. Call submit_review exactly once. "
    "Flag a concern only for a real, specific mismatch between the request "
    "and the drafted command -- do not flag stylistic preferences, and do not "
    "invent doubts about a request that is genuinely unambiguous. The command "
    "has already passed schema/device validation; you are checking intent, "
    "not syntax."
)


@dataclass
class CriticVerdict:
    flagged: bool
    concerns: list[str]
    error: str | None = None


def review_proposal(
    request: str,
    rendered_command: str,
    disclosed_notes: list[str] | None = None,
    client: ArgoClient | None = None,
    temperature: float | None = None,
) -> CriticVerdict:
    """One bounded, independent LLM turn checking `rendered_command` against
    `request`. See module docstring for the failure-open contract."""
    client = client or ArgoClient()
    already_disclosed = "\n".join(disclosed_notes or []) or "(none)"
    user_msg = (
        f"Original request:\n{request}\n\n"
        f"Drafted command:\n{rendered_command}\n\n"
        f"Notes already disclosed to the user by the drafting turn:\n{already_disclosed}\n\n"
        "Does the drafted command actually match the request?"
    )
    try:
        resp = client.call(
            _SYSTEM_PROMPT,
            [{"role": "user", "content": user_msg}],
            [_build_review_tool_schema()],
            temperature=temperature,
            tool_choice={"type": "tool", "name": REVIEW_TOOL_NAME},
        )
    except Exception as exc:  # noqa: BLE001 -- a critic failure must never block a proposal
        return CriticVerdict(flagged=False, concerns=[], error=str(exc))

    tool_use = next((block for block in resp.content if block.type == "tool_use"), None)
    if tool_use is None:
        return CriticVerdict(flagged=False, concerns=[], error="critic returned no tool call")

    concerns = [c for c in (tool_use.input.get("concerns") or []) if c]
    matches = bool(tool_use.input.get("matches_intent", True))
    return CriticVerdict(flagged=(not matches) or bool(concerns), concerns=concerns)
