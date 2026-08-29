"""Backend-agnostic staging store for AutoPILOT-proposed queue items,
pending human approval.

Entirely separate from both the native queue file (`queue_store.py`) and
the queueserver's own server-side queue state (`qs_client.py`) -- a
proposal written here never becomes a real queue item on its own. Only
`queue_panel.py`'s "Pending AI proposals" section can promote one, and it
does so by calling the exact same `NativeQueuePanel.add()` /
`QSQueuePanel.add()` method a human's own "Add to Queue" click already
uses (see those methods' call sites in `queue_panel.py`). No new
hardware-dispatch call site is introduced anywhere in this module or its
callers -- the codebase-wide audit this project relies on
(`.context/ARCHITECTURE.md`) still finds exactly the same call sites as
before this file existed.

Same locked-JSON-file-per-beamline pattern as `queue_store.py` (one file
next to it, `<session_dir>/<beamline>/agent_proposals.json`), duplicated
rather than shared -- this codebase's established convention for small,
independent on-disk stores (`queue_store.py`, `det_startup_state.py`,
`experiment_history.py` each keep their own copy of the same ~15-line
locked read-modify-write, not a shared utility module).

Schema::

    {"seq": <int>, "proposals": [
        {"id": str, "status": "pending"|"approved"|"rejected",
         "created_at": <float>, "backend": "native"|"qs",
         "template_key": str, "request_summary": str,
         "command": str, "notes": str, "area_detectors": [str, ...],
         "qs_item": dict|None},
        ...]}

`command`/`notes`/`area_detectors` are always populated (used for the
native backend, and for display regardless of backend); `qs_item` is the
structured dict `queue_proposal.build_qs_item()` built, populated only
when `backend == "qs"` and a structured item could be built at all (`None`
otherwise, the same "hand-edited text" fallback case
`command_builder.make_queue_item` already has for the human-driven path).
"""
from __future__ import annotations

import json
import os
import time
import uuid

try:
    import fcntl
except ImportError:  # non-POSIX (not expected on beamline Linux/macOS)
    fcntl = None

from . import kernel_session as ks

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"


def _dir(beamline: str) -> str:
    return ks.paths(beamline)["dir"]


def _path(beamline: str) -> str:
    return os.path.join(_dir(beamline), "agent_proposals.json")


def _lock_path(beamline: str) -> str:
    return os.path.join(_dir(beamline), "agent_proposals.lock")


def _default() -> dict:
    return {"seq": 0, "proposals": []}


def load(beamline: str) -> dict:
    """Read the store (best effort); returns an empty store if absent/bad."""
    try:
        with open(_path(beamline), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("proposals"), list):
            data.setdefault("seq", 0)
            return data
    except Exception:  # noqa: BLE001
        pass
    return _default()


def _write(beamline: str, data: dict) -> None:
    os.makedirs(_dir(beamline), exist_ok=True)
    tmp = _path(beamline) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, _path(beamline))  # atomic


def _mutate(beamline: str, fn) -> dict:
    os.makedirs(_dir(beamline), exist_ok=True)
    lock = open(_lock_path(beamline), "w")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(beamline)
        fn(data)
        _write(beamline, data)
        return data
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
        lock.close()


def add_pending(
    beamline: str,
    *,
    backend: str,
    template_key: str,
    request_summary: str,
    command: str,
    notes: str = "",
    area_detectors: list | None = None,
    qs_item: dict | None = None,
) -> dict:
    """Stage a new `pending` proposal; returns it (not the whole store)."""
    proposal = {
        "id": uuid.uuid4().hex[:12],
        "status": PENDING,
        "created_at": time.time(),
        "backend": backend,
        "template_key": template_key,
        "request_summary": request_summary,
        "command": command,
        "notes": notes,
        "area_detectors": area_detectors or [],
        "qs_item": qs_item,
    }

    def _fn(d: dict) -> None:
        d["proposals"].append(proposal)

    _mutate(beamline, _fn)
    return proposal


def list_pending(beamline: str) -> list[dict]:
    return [p for p in load(beamline).get("proposals", []) if p.get("status") == PENDING]


def set_status(beamline: str, proposal_id: str, status: str) -> dict | None:
    """Set `proposal_id`'s status; returns the updated proposal, or `None`
    if no proposal with that id exists (already removed/never existed)."""
    found: dict | None = None

    def _fn(d: dict) -> None:
        nonlocal found
        for p in d["proposals"]:
            if p["id"] == proposal_id:
                p["status"] = status
                found = p
                break

    _mutate(beamline, _fn)
    return found


def remove(beamline: str, proposal_id: str) -> None:
    def _fn(d: dict) -> None:
        d["proposals"] = [p for p in d["proposals"] if p["id"] != proposal_id]

    _mutate(beamline, _fn)


def clear_resolved(beamline: str) -> None:
    """Drop every `approved`/`rejected` proposal, keeping only `pending`
    ones -- mirrors `queue_store.clear_finished`'s tidy-up role."""

    def _fn(d: dict) -> None:
        d["proposals"] = [p for p in d["proposals"] if p.get("status") == PENDING]

    _mutate(beamline, _fn)
