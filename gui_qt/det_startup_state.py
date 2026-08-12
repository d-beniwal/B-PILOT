"""Per-beamline, per-kernel-connection tracking of which detectors have had
``RE(det_startup(det=<name>))`` run, so B-PILOT can auto-inject that call
before a scan that needs it instead of requiring the user to remember it.

Client-side bookkeeping only (no live kernel query) -- a JSON file per
beamline, next to the kernel's other session files, following the exact
locked read-modify-write + atomic-replace pattern as :mod:`queue_store` so
it's safe to share between the GUI process and the detached
:mod:`queue_runner` process. The tracked set is intentionally reset on every
kernel (re)connection -- whether a fresh kernel or reattaching to one already
running -- so it never wrongly skips a needed call; the worst case is one
harmless redundant `det_startup` (idempotent) after a GUI restart.

``det_startup`` itself (`instrument/plans/auxiliary_ad.py`) doesn't follow
B-PILOT's parseable-docstring grammar (no ``Parameters`` section), so its
call is hand-formatted here rather than routed through
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


def load(beamline: str) -> set:
    """Detector names already started this kernel connection (best effort)."""
    try:
        with open(_state_path(beamline), encoding="utf-8") as fh:
            data = json.load(fh)
        started = data.get("started")
        if isinstance(started, list):
            return set(started)
    except Exception:  # noqa: BLE001
        pass
    return set()


def _write(beamline: str, started: set) -> None:
    os.makedirs(_dir(beamline), exist_ok=True)
    path = _state_path(beamline)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"started": sorted(started)}, fh, indent=2)
    os.replace(tmp, path)  # atomic


def _mutate(beamline: str, fn) -> set:
    """Locked read-modify-write: `fn(started_set)` returns the new set."""
    os.makedirs(_dir(beamline), exist_ok=True)
    lock = open(_lock_path(beamline), "w")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        started = fn(load(beamline))
        _write(beamline, started)
        return started
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
        lock.close()


def clear(beamline: str) -> None:
    """Reset to empty -- call whenever B-PILOT (re)connects to a kernel."""
    _mutate(beamline, lambda _started: set())


def mark_started(beamline: str, names: list) -> None:
    if not names:
        return
    _mutate(beamline, lambda started: started | set(names))


def filter_unstarted(beamline: str, names: list) -> list:
    """`names` not yet marked started, in their original order (deduped)."""
    started = load(beamline)
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
