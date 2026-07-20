"""Persistent, single-per-beamline plan queue shared by the GUI and the runner.

The queue lives in one JSON file per beamline, next to the kernel's session
files: ``<session_dir>/<beamline>/queue.json``.  Because a detached
:mod:`queue_runner` (which drives the kernel) and the GUI both read/modify it,
every write is a **locked read-modify-write** (``fcntl.flock``) so they never
race, and writes are atomic (``os.replace``) so readers never see a half file.
The GUI displays by polling the file, so status the runner sets shows up even
after the GUI was closed and reopened — and there is exactly one queue per
beamline session.

Schema::

    {"state": "idle|running|paused", "seq": <int>, "items": [
        {"id": str, "name": str, "command": str, "notes": str,
         "status": "waiting|running|done|error"}, ...]}
"""
from __future__ import annotations

import json
import os
import re

try:
    import fcntl
except ImportError:  # non-POSIX (not expected on beamline Linux/macOS)
    fcntl = None

from . import kernel_session as ks

# item statuses
WAITING, RUNNING, DONE, ERROR = "waiting", "running", "done", "error"
# queue states
IDLE, S_RUNNING, PAUSED = "idle", "running", "paused"


def _dir(beamline: str) -> str:
    return ks.paths(beamline)["dir"]


def queue_path(beamline: str) -> str:
    """Path to this beamline's queue file."""
    return os.path.join(_dir(beamline), "queue.json")


def _lock_path(beamline: str) -> str:
    return os.path.join(_dir(beamline), "queue.lock")


def _default() -> dict:
    return {"state": IDLE, "seq": 0, "items": []}


def load(beamline: str) -> dict:
    """Read the queue (best effort); returns an empty queue if absent/bad."""
    try:
        with open(queue_path(beamline), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data.setdefault("state", IDLE)
            data.setdefault("seq", 0)
            return data
    except Exception:  # noqa: BLE001
        pass
    return _default()


def _write(beamline: str, data: dict) -> None:
    os.makedirs(_dir(beamline), exist_ok=True)
    tmp = queue_path(beamline) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, queue_path(beamline))   # atomic


def mutate(beamline: str, fn) -> dict:
    """Locked read-modify-write: ``fn(data)`` mutates in place. Returns new data."""
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


# ── Operations (all locked via mutate) ───────────────────────────────────────────

# Plan name inside an ``RE(<plan>(...))`` command, e.g. RE(expose(...)) -> expose.
_RE_PLAN = re.compile(r"\bRE\(\s*([A-Za-z_]\w*)\s*\(")


def _default_name(command: str) -> str:
    """Default queue name = the plan name (not the whole RE command).

    Extracts the function inside ``RE(<plan>(...))``; falls back to the last
    non-empty line (truncated) if the command isn't a recognisable RE call.
    The user can still rename it in the table afterwards.
    """
    match = _RE_PLAN.search(command or "")
    if match:
        return match.group(1)
    text = (command or "").strip()
    line = text.splitlines()[-1] if text else "plan"
    return line if len(line) <= 60 else line[:59] + "…"


def add(beamline: str, command: str, notes: str = "", name: str = "") -> dict:
    """Append a waiting item; returns the new queue."""
    def _fn(d: dict) -> None:
        d["seq"] = int(d.get("seq", 0)) + 1
        d["items"].append({
            "id": str(d["seq"]),
            "name": name or _default_name(command),
            "command": command,
            "notes": notes,
            "status": WAITING,
        })
    return mutate(beamline, _fn)


def rename(beamline: str, item_id: str, name: str) -> dict:
    def _fn(d: dict) -> None:
        for it in d["items"]:
            if it["id"] == item_id:
                it["name"] = name
    return mutate(beamline, _fn)


def set_item_status(beamline: str, item_id: str, status: str) -> dict:
    def _fn(d: dict) -> None:
        for it in d["items"]:
            if it["id"] == item_id:
                it["status"] = status
    return mutate(beamline, _fn)


def remove(beamline: str, item_id: str) -> dict:
    """Remove an item unless it is currently running."""
    def _fn(d: dict) -> None:
        d["items"] = [
            it for it in d["items"]
            if not (it["id"] == item_id and it["status"] != RUNNING)
        ]
    return mutate(beamline, _fn)


def move(beamline: str, item_id: str, delta: int) -> dict:
    """Reorder an item by `delta` (won't move across a running item)."""
    def _fn(d: dict) -> None:
        items = d["items"]
        idx = next((i for i, it in enumerate(items) if it["id"] == item_id), None)
        if idx is None:
            return
        j = idx + delta
        if j < 0 or j >= len(items):
            return
        if RUNNING in (items[idx]["status"], items[j]["status"]):
            return
        items[idx], items[j] = items[j], items[idx]
    return mutate(beamline, _fn)


def set_state(beamline: str, state: str) -> dict:
    def _fn(d: dict) -> None:
        d["state"] = state
    return mutate(beamline, _fn)


def clear_finished(beamline: str) -> dict:
    def _fn(d: dict) -> None:
        d["items"] = [it for it in d["items"] if it["status"] not in (DONE, ERROR)]
    return mutate(beamline, _fn)


def reconcile_stale_running(beamline: str) -> dict:
    """Mark any 'running' item as 'error' (used by a fresh runner at startup).

    A leftover 'running' item means a previous runner died mid-plan; nothing is
    tracking it now, so flag it rather than leave it stuck green.
    """
    def _fn(d: dict) -> None:
        for it in d["items"]:
            if it["status"] == RUNNING:
                it["status"] = ERROR
    return mutate(beamline, _fn)
