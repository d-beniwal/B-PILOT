"""Per-beamline tracking of which detectors have had
``RE(det_startup(det=<name>))`` run, so B-PILOT can auto-inject that call
before a scan that needs it instead of requiring the user to remember it.

Client-side bookkeeping only (no live kernel/QS query) -- a JSON file per
beamline, next to the kernel's other session files, following the exact
locked read-modify-write + atomic-replace pattern as the retired
``queue_store`` module.

Two **independent** tracked sets live in the same file: ``started`` (the
interactive-console-kernel path, reset on every kernel (re)connection -- see
`clear`) and ``started_qs`` (the QS-queue path, reset on every QS
environment open -- see `clear_qs`). They must stay independent: the console
kernel and QS's own RE are genuinely separate device-instance processes on
redwood, so a detector started in one does NOT imply it's started in the
other. Conflating them into one shared set would risk the one outcome this
module exists to avoid -- wrongly *skipping* a real `det_startup` call. The
accepted worst case, in both paths, stays "one harmless redundant call"
(idempotent), never "wrongly skipped."

``det_startup`` itself (`instrument/plans/auxiliary_ad.py`) doesn't follow
B-PILOT's parseable-docstring grammar (no ``Parameters`` section), so its
call/item is hand-formatted here rather than routed through
`plan_parser.find_plan_specs`/`command_builder.make_re_line`.
"""
from __future__ import annotations

import json
import os

try:
    import fcntl
except ImportError:  # non-POSIX (not expected on beamline Linux/macOS)
    fcntl = None

from . import config
from . import kernel_session as ks
from .plan_parser import file_to_module

_DET_STARTUP_MODULE_FILE = "auxiliary_ad.py"


def _dir(beamline: str) -> str:
    return ks.paths(beamline)["dir"]


def _state_path(beamline: str) -> str:
    return os.path.join(_dir(beamline), "det_startup_state.json")


def _lock_path(beamline: str) -> str:
    return os.path.join(_dir(beamline), "det_startup_state.lock")


def _load_all(beamline: str) -> dict:
    try:
        with open(_state_path(beamline), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001
        pass
    return {}


def _load_key(beamline: str, key: str) -> set:
    names = _load_all(beamline).get(key)
    return set(names) if isinstance(names, list) else set()


def load(beamline: str) -> set:
    """Detector names already started this kernel connection (best effort)."""
    return _load_key(beamline, "started")


def load_qs(beamline: str) -> set:
    """Detector names already started in the current QS environment."""
    return _load_key(beamline, "started_qs")


def _write_key(beamline: str, key: str, values: set) -> None:
    os.makedirs(_dir(beamline), exist_ok=True)
    path = _state_path(beamline)
    tmp = path + ".tmp"
    data = _load_all(beamline)
    data[key] = sorted(values)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)  # atomic


def _mutate(beamline: str, key: str, fn) -> set:
    """Locked read-modify-write on one key's set: `fn(set)` returns the new set."""
    os.makedirs(_dir(beamline), exist_ok=True)
    lock = open(_lock_path(beamline), "w")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        values = fn(_load_key(beamline, key))
        _write_key(beamline, key, values)
        return values
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
        lock.close()


def clear(beamline: str) -> None:
    """Reset the console-kernel set to empty -- call whenever B-PILOT
    (re)connects to a kernel."""
    _mutate(beamline, "started", lambda _started: set())


def clear_qs(beamline: str) -> None:
    """Reset the QS set to empty -- call whenever B-PILOT opens a fresh QS
    environment (independent of the console-kernel set, see module docstring)."""
    _mutate(beamline, "started_qs", lambda _started: set())


def mark_started(beamline: str, names: list) -> None:
    if not names:
        return
    _mutate(beamline, "started", lambda started: started | set(names))


def mark_started_qs(beamline: str, names: list) -> None:
    if not names:
        return
    _mutate(beamline, "started_qs", lambda started: started | set(names))


def filter_unstarted(beamline: str, names: list) -> list:
    """`names` not yet marked started (console-kernel path), original order
    (deduped)."""
    return _filter_unstarted(load(beamline), names)


def filter_unstarted_qs(beamline: str, names: list) -> list:
    """`names` not yet marked started (QS path), original order (deduped)."""
    return _filter_unstarted(load_qs(beamline), names)


def _filter_unstarted(started: set, names: list) -> list:
    seen = set()
    result = []
    for name in names:
        if name in started or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _det_startup_module() -> str:
    plans_dir = config.get("plans_dir")
    abs_path = os.path.join(plans_dir, _DET_STARTUP_MODULE_FILE)
    return file_to_module(abs_path, config.get("import_root"))


def build_startup_commands(beamline: str, detector_names: list) -> str:
    """`from <module> import det_startup` + `RE(det_startup(det=<name>))`
    block for every not-yet-started name in `detector_names`, marking them
    started as a side effect. Returns "" if nothing needs starting.
    """
    unstarted = filter_unstarted(beamline, detector_names)
    if not unstarted:
        return ""
    module = _det_startup_module()
    lines = [f"from {module} import det_startup"]
    lines.extend(f"RE(det_startup(det={name}))" for name in unstarted)
    mark_started(beamline, unstarted)
    return "\n".join(lines)


def build_startup_items(beamline: str, detector_names: list) -> list[dict]:
    """QS item-shaped equivalent of `build_startup_commands`, for the queue
    path: one ``{"item_type": "plan", "name": "det_startup", "kwargs":
    {"det": name}}`` dict per not-yet-started (QS path) name, marking them
    started as a side effect. No import-line plumbing needed -- QS resolves
    `name` against its own already-loaded namespace, unlike the console
    path's `from <module> import det_startup` line. Returns `[]` if nothing
    needs starting.

    Called at **enqueue** time (see `main_window._on_queue`), not at actual
    dispatch time like `build_startup_commands` is for the interactive path
    -- QS dispatches items itself, so B-PILOT has no hook to inject right
    before execution. This is an accepted, documented behavior change: a
    queued item's detector-startup need is checked once, when it's added,
    not re-checked right before it runs.
    """
    unstarted = filter_unstarted_qs(beamline, detector_names)
    if not unstarted:
        return []
    mark_started_qs(beamline, unstarted)
    return [
        {"item_type": "plan", "name": "det_startup", "kwargs": {"det": name}}
        for name in unstarted
    ]
