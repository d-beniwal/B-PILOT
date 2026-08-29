"""Persistent, per-beamline record of every AutoPILOT chat interaction.

Mirrors ``B_PILOT/experiment_history.py``'s storage pattern (same
``session_dir``/beamline nesting, append-only JSON-Lines, single ``write()``
call per line, ``try/except OSError: pass`` on every write) but keyed by
**beamline + calendar day** rather than by experiment -- a chat conversation
isn't necessarily tied to a running kernel/experiment, and a per-day file
keeps individual files bounded in size while still segregating cleanly by
beamline at the directory level, since B-PILOT runs standalone on separate
machines at separate beamlines and this data is never synced between them
(that's a deliberately separate, later piece of work).

Storage::

    <session_dir>/<beamline>/autopilot/interactions/<YYYY-MM-DD>.jsonl

Two entry kinds, one JSON object per line:

- ``"turn"`` -- one full :func:`autopilot.pipeline.converse` call: the
  request, the final reply, which tool(s) fired, the drafted plan spec (raw
  and validated), and token usage. Written by :func:`record_turn`.
- ``"outcome"`` -- a later human action tied back to a turn's ``turn_id``
  (e.g. ``action="opened_in_form"``) -- the clearest available implicit
  signal for whether a proposed plan was actually good. Written by
  :func:`record_outcome`.

``conversation_id`` groups every turn (and outcome) of one chat-dock session
together; ``turn_id`` identifies one turn precisely so an outcome can be
correlated back to the turn that produced it.

Every string value is passed through :func:`autopilot.tools.redact` (the
same credential-URL redaction already applied to ``search_codebase``/
``read_source_file`` results) before it reaches disk -- defense-in-depth in
case a user pastes something sensitive into chat.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time
import uuid

from . import tools
from ._bpilot_path import ensure_bpilot_on_path

ensure_bpilot_on_path()

from B_PILOT import config as bpilot_config  # noqa: E402


def _safe_name(beamline: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (beamline or "").strip()).strip("_")
    return safe or "unknown"


def _beamline_dir(beamline: str) -> str:
    return os.path.join(
        os.path.expanduser(bpilot_config.get("session_dir")), _safe_name(beamline), "autopilot", "interactions"
    )


def _day_path(beamline: str, ts: float) -> str:
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    return os.path.join(_beamline_dir(beamline), f"{day}.jsonl")


def new_conversation_id() -> str:
    """A short random id grouping every turn/outcome of one chat-dock session."""
    return uuid.uuid4().hex[:12]


def _redact_value(value):
    """Recursively apply `tools.redact` to every string in `value` (a
    dict/list/str/anything), leaving non-string leaves (numbers, bools,
    None) untouched."""
    if isinstance(value, str):
        return tools.redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def _append(beamline: str, entry: dict) -> None:
    """Append one JSON line. Never raises -- a logging failure must never
    break a real chat turn (same convention as
    `experiment_history.append_entry`)."""
    path = _day_path(beamline, entry["ts"])
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def record_turn(
    beamline: str,
    *,
    conversation_id: str,
    turn_id: str,
    profile: str | None,
    experiment: str | None,
    request: str,
    result,  # pipeline.PlanResult -- typed loosely to avoid a pipeline<->interaction_history import cycle
) -> None:
    """Append one `"turn"` entry for a completed `pipeline.converse()` call."""
    entry = {
        "ts": time.time(),
        "kind": "turn",
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "beamline": beamline,
        "profile": profile,
        "experiment": experiment or None,
        "request": tools.redact(request or ""),
        "ok": result.ok,
        "message": tools.redact(result.message or ""),
        "template_key": result.template_key,
        "tool_name": result.tool_name,
        "tool_calls": result.tool_calls,
        "raw_spec": _redact_value(result.raw_spec),
        "clean_spec": _redact_value(result.clean_spec),
        "errors": result.errors,
        "filepath": result.filepath,
        "gui_command": tools.redact(result.gui_command) if result.gui_command else None,
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cache_read_input_tokens": result.cache_read_input_tokens,
    }
    _append(beamline, entry)


def record_outcome(
    beamline: str, *, conversation_id: str, turn_id: str | None, action: str, detail: str | None = None
) -> None:
    """Append one `"outcome"` entry -- a human action taken on a prior turn."""
    entry = {
        "ts": time.time(),
        "kind": "outcome",
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "beamline": beamline,
        "action": action,
        "detail": detail,
    }
    _append(beamline, entry)


def list_days(beamline: str) -> list[str]:
    """Available `YYYY-MM-DD` days with recorded interactions for `beamline`,
    most recent first."""
    days = [
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(_beamline_dir(beamline), "*.jsonl"))
    ]
    return sorted(days, reverse=True)


def read_day(beamline: str, day: str) -> list[dict]:
    """All entries recorded for `beamline` on `day` (`YYYY-MM-DD`), in file
    (oldest-first) order. Malformed lines are skipped rather than aborting
    the whole read."""
    entries: list[dict] = []
    path = os.path.join(_beamline_dir(beamline), f"{day}.jsonl")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return entries


# ---------------------------------------------------------------------------
# Grounded retrieval over this log -- turns the write-only history above
# into something a live conversation can actually draw on. Deliberately
# lexical (token-set overlap), not embedding/vector-based: no new
# dependency, no index to keep in sync, and every candidate this returns is
# already redacted (it was redacted before being written, above). See
# `tools.recall_similar_requests` for the read-only lookup tool that
# exposes this to the model -- like every other lookup tool, the model
# decides whether to call it; nothing here is auto-injected into every turn.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def find_similar(beamline: str, request: str, limit: int = 2, max_days: int = 30) -> list[dict]:
    """Past recorded `"turn"` entries for `beamline`, scored by lexical
    token-overlap (Jaccard) against `request`, restricted to turns that
    both succeeded (`ok`) and were later opened in the form
    (`action="opened_in_form"`) -- the same positive signal this log's own
    design already treats as the clearest available evidence a draft was
    actually good, not just schema-valid. Returns up to `limit` entries,
    highest-overlap first (ties broken by recency); `max_days` bounds how
    far back to scan so a long-lived log doesn't make every call slower
    over time.
    """
    query_tokens = _tokenize(request)
    if not query_tokens:
        return []

    scored: list[tuple[float, float, dict]] = []
    for day in list_days(beamline)[:max_days]:
        entries = read_day(beamline, day)
        opened_turn_ids = {
            e.get("turn_id")
            for e in entries
            if e.get("kind") == "outcome" and e.get("action") == "opened_in_form"
        }
        for e in entries:
            if e.get("kind") != "turn" or not e.get("ok") or e.get("turn_id") not in opened_turn_ids:
                continue
            candidate_tokens = _tokenize(e.get("request", ""))
            if not candidate_tokens:
                continue
            overlap = len(query_tokens & candidate_tokens) / len(query_tokens | candidate_tokens)
            if overlap > 0:
                scored.append((overlap, e.get("ts", 0.0), e))

    scored.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return [e for _score, _ts, e in scored[:limit]]


def summarize_candidate(entry: dict) -> dict:
    """The subset of a `find_similar` match worth showing the model --
    never the full raw entry (which also carries token-usage/tool-trace
    bookkeeping irrelevant to grounding a new answer)."""
    return {
        "request": entry.get("request"),
        "template_key": entry.get("template_key"),
        "clean_spec": entry.get("clean_spec"),
        "gui_command": entry.get("gui_command"),
        "filepath": entry.get("filepath"),
        "when": time.strftime("%Y-%m-%d", time.localtime(entry.get("ts", 0))) if entry.get("ts") else None,
    }
