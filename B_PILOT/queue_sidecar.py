"""GUI-only metadata for QS queue items that has no safe home on the item
itself, keyed by the QS item's ``item_uid``.

QS's server strips any item key outside ``item_uid, item_type, name, args,
kwargs, meta, user, user_group, properties`` (confirmed against the
installed ``bluesky_queueserver`` --
``plan_queue_ops.py::filter_item_parameters``), so an arbitrary top-level
key like a GUI display name would not round-trip. ``meta`` *does* round-trip
reliably, but it is spread as the run's own ``RE(plan(...), **meta)``
metadata kwargs (``worker.py``) -- exactly where the run's ``notes`` belong
(see ``command_builder``/``main_window._on_queue``), not a place to stash
GUI bookkeeping that would otherwise leak into every run's start-document
metadata.

The one truly QS-opaque, purely-cosmetic piece of state left over is a
user's double-click rename of a queue row's Name column (``queue_panel.py``)
-- QS's own ``name`` field is the real plan-function name and can't be
repurposed as a free-text label without breaking dispatch. This sidecar
carries only that.

Same locked-JSON pattern as :mod:`det_startup_state`/the retired
``queue_store`` -- one file per beamline, `fcntl`-guarded read-modify-write,
atomic replace.
"""
from __future__ import annotations

import json
import os

try:
    import fcntl
except ImportError:  # non-POSIX (not expected on beamline Linux/macOS)
    fcntl = None

from . import kernel_session as ks


def _dir(beamline: str) -> str:
    return ks.paths(beamline)["dir"]


def _path(beamline: str) -> str:
    return os.path.join(_dir(beamline), "queue_sidecar.json")


def _lock_path(beamline: str) -> str:
    return os.path.join(_dir(beamline), "queue_sidecar.lock")


def load(beamline: str) -> dict[str, str]:
    """``{item_uid: display_name}`` (best effort; `{}` if absent/bad)."""
    try:
        with open(_path(beamline), encoding="utf-8") as fh:
            data = json.load(fh)
        names = data.get("display_names")
        if isinstance(names, dict):
            return {str(k): str(v) for k, v in names.items()}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _write(beamline: str, names: dict[str, str]) -> None:
    os.makedirs(_dir(beamline), exist_ok=True)
    path = _path(beamline)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"display_names": names}, fh, indent=2)
    os.replace(tmp, path)  # atomic


def _mutate(beamline: str, fn) -> dict[str, str]:
    os.makedirs(_dir(beamline), exist_ok=True)
    lock = open(_lock_path(beamline), "w")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        names = load(beamline)
        fn(names)
        _write(beamline, names)
        return names
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
        lock.close()


def set_display_name(beamline: str, item_uid: str, name: str) -> None:
    _mutate(beamline, lambda names: names.__setitem__(item_uid, name))


def get_display_name(beamline: str, item_uid: str, fallback: str) -> str:
    return load(beamline).get(item_uid, fallback)


def prune(beamline: str, live_uids: set) -> None:
    """Drop entries for uids no longer in QS's queue+history, so this file
    doesn't grow unboundedly across many completed/cleared items."""
    def _fn(names: dict) -> None:
        for uid in list(names):
            if uid not in live_uids:
                del names[uid]
    _mutate(beamline, _fn)
