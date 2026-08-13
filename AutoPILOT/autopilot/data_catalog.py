"""Read-only lookups over a beamline's recorded runs, for AutoPILOT's data tools.

Reuses B-PILOT's Qt-free ``B_PILOT.databroker_access`` (the same databroker
access logic behind the "Open Bluesky Viewer" window) rather than
reimplementing catalog/Mongo access. Catalog access always goes through the
active profile's configured **catalog name** (``databroker_catalog``) --
never a raw Mongo URI, and this module never reads ``instrument/iconfig.yml``
directly (that file holds live plaintext MongoDB credentials; see
``tools.py``'s ``_resolve_plan_path`` for the same hard-scoping rationale
applied to plan files).

Every public function returns a plain JSON-safe dict and never raises --
mirrors ``B_PILOT.databroker_access.connect_catalog``'s tolerant-of-failure
convention, since these are called directly as LLM tool results.

Connections are cached at module level, keyed by (catalog, uri). This is
safe only because ``pipeline.converse()`` -- and therefore every function
here -- is always invoked from ``autopilot/gui/chat_panel.py``'s
``_ChatWorker``, a single persistent daemon thread started once per chat
dock and never re-created (some databroker/intake backends bind internal
state to whichever thread first touches them -- the same invariant
``B_PILOT/viewer.py``'s ``_CatalogWorker`` documents and relies on). If a
second concurrent caller of this module is ever introduced, this cache
needs a lock or a per-thread scope.
"""
from __future__ import annotations

import socket
from datetime import datetime

from . import settings
from ._bpilot_path import ensure_bpilot_on_path

ensure_bpilot_on_path()

from B_PILOT import config as bpilot_config  # noqa: E402
from B_PILOT import databroker_access as bpilot_data  # noqa: E402

# Fields present on real hexm_test start docs that are either large, noisy,
# or internal-only -- not useful to an LLM answering questions about a run
# and, in some cases (bcs_md/edf_md/hdf_md/tiff_md), large enough to blow up
# a tool result. Identified from the real sample during development.
_DENYLIST = {
    "bcs_md", "conda_prefix", "dm_exp", "edf_md", "hdf_md",
    "hints", "iconfig", "login_id", "pid", "tiff_md", "versions",
}

_MIN_LIMIT, _MAX_LIMIT, _DEFAULT_LIMIT = 1, 100, 20
_MAX_COLUMNS = 60

# Fetching per-run metadata is one Mongo round-trip per uid (see
# B_PILOT/databroker_access.py's page_from_uids docstring) -- fine for a
# ~100-run local test catalog, but scanning an entire real beamline catalog
# (which can hold tens of thousands of runs) on first use would block the
# single chat-worker thread for a long time with no progress indication.
# Bounded to the same page size the viewer fetches at once; search_runs
# reports when the catalog is bigger than what got scanned so the agent
# doesn't imply completeness it doesn't have (the "no silent caps" rule).
_MAX_RUNS_SCANNED = bpilot_data.PAGE_SIZE

# {(catalog_name, uri): (catalog_obj, [(uid, start, stop), ...], total_uids)}
_CACHE: dict[tuple[str, str], tuple] = {}

_TIMEOUT_APPLIED = False


def _ensure_socket_timeout() -> None:
    """Bound pymongo's read hangs, once, the first time a data tool actually connects.

    pymongo has no default *read* timeout, so a server that accepts a
    connection and then stops responding mid-query blocks forever with no
    exception raised (see ``B_PILOT/databroker_access.py``'s module
    docstring). ``B_PILOT/viewer.py`` sets this globally at import time
    because it always runs as its own process; AutoPILOT instead runs
    in-process inside the main B-PILOT GUI, so this is applied lazily --
    only if/when a data tool is actually used -- rather than unconditionally
    at import time, to minimize the chance of surprising an unrelated
    in-process network call (e.g. the Argo API client) with a timeout it
    didn't ask for. ``socket.setdefaulttimeout`` is process-wide and
    idempotent, so this only needs to run once per process.
    """
    global _TIMEOUT_APPLIED
    if not _TIMEOUT_APPLIED:
        socket.setdefaulttimeout(30)
        _TIMEOUT_APPLIED = True


def _catalog_key(profile: str | None) -> tuple[str, str]:
    # Local-testing escape hatch: overrides the profile's catalog (and drops
    # its databroker_uri too, since connect_catalog() prioritizes a Tiled URI
    # over a named catalog and a local test catalog is always named, never a
    # Tiled URI) -- see settings.py's databroker_catalog_override docstring.
    override = settings.load().get("databroker_catalog_override")
    if override:
        return str(override), ""
    values = bpilot_config.profile_values(profile) if profile else bpilot_config.as_dict()
    return str(values.get("databroker_catalog") or ""), str(values.get("databroker_uri") or "")


def _connect(profile: str | None) -> tuple:
    """Return (catalog_obj | None, uid_rows, error, total_uids). Cached per (catalog, uri)."""
    key = _catalog_key(profile)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached[0], cached[1], "", cached[2]

    _ensure_socket_timeout()
    catalog_name, uri = key
    cat, status = bpilot_data.connect_catalog(catalog_name, uri)
    if cat is None:
        return None, [], status, 0

    uids = bpilot_data.all_uids(cat)
    rows = bpilot_data.page_from_uids(cat, uids, 0, limit=min(len(uids), _MAX_RUNS_SCANNED) or 1)
    _CACHE[key] = (cat, rows, len(uids))
    return cat, rows, "" if "✗" not in status else status, len(uids)


def _row_summary(uid: str, start: dict, stop: dict) -> dict:
    duration = None
    try:
        duration = round(float(stop["time"]) - float(start["time"]), 1)
    except Exception:  # noqa: BLE001
        pass
    return {
        "uid": uid,
        "scan_id": start.get("scan_id"),
        "plan_name": start.get("plan_name"),
        "time": _iso(start.get("time")),
        "duration_s": duration,
        "exit_status": stop.get("exit_status"),
        "motors": start.get("motors") or [],
        "detectors": start.get("detectors") or [],
    }


def _iso(epoch) -> str | None:
    try:
        return datetime.fromtimestamp(float(epoch)).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return None


def _parse_date_bound(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:  # noqa: BLE001
        return None


def search_runs(
    profile: str | None = None,
    *,
    plan_name: str | None = None,
    exit_status: str | None = None,
    scan_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    """Structured filter-dict search over this beamline's recorded runs, newest first."""
    cat, rows, error, total_uids = _connect(profile)
    if cat is None:
        return {"found": False, "error": error or "no catalog configured for this profile"}

    since_ts = _parse_date_bound(since)
    until_ts = _parse_date_bound(until)

    def keep(start: dict, stop: dict) -> bool:
        if plan_name is not None and start.get("plan_name") != plan_name:
            return False
        if exit_status is not None and stop.get("exit_status") != exit_status:
            return False
        if scan_id is not None and start.get("scan_id") != scan_id:
            return False
        t = start.get("time")
        if since_ts is not None and (t is None or t < since_ts):
            return False
        if until_ts is not None and (t is None or t > until_ts):
            return False
        return True

    matched = [(uid, start, stop) for uid, start, stop in rows if keep(start, stop)]
    limit = max(_MIN_LIMIT, min(_MAX_LIMIT, limit))
    offset = max(0, offset)
    page = matched[offset:offset + limit]
    result = {
        "found": True,
        "total_matches": len(matched),
        "returned": len(page),
        "offset": offset,
        "runs": [_row_summary(uid, start, stop) for uid, start, stop in page],
    }
    if len(rows) < total_uids:
        result["catalog_note"] = (
            f"searched only the {len(rows)} most recent of {total_uids} total runs "
            "in this catalog"
        )
    return result


def _find_run(rows: list, run_id: str):
    """Resolve `run_id` (full/partial uid or scan_id) against cached rows.

    Returns (uid, start, stop, None) on a unique match, or
    (None, None, None, ambiguous_uid_list) otherwise.
    """
    run_id = str(run_id)
    exact = [(uid, start, stop) for uid, start, stop in rows if uid == run_id]
    if len(exact) == 1:
        return (*exact[0], None)

    try:
        as_int = int(run_id)
    except ValueError:
        as_int = None
    if as_int is not None:
        by_scan = [(uid, start, stop) for uid, start, stop in rows if start.get("scan_id") == as_int]
        if len(by_scan) == 1:
            return (*by_scan[0], None)
        if len(by_scan) > 1:
            return None, None, None, [uid for uid, _, _ in by_scan]

    prefix = [(uid, start, stop) for uid, start, stop in rows if uid.startswith(run_id)]
    if len(prefix) == 1:
        return (*prefix[0], None)
    if len(prefix) > 1:
        return None, None, None, [uid for uid, _, _ in prefix]
    return None, None, None, []


def describe_run(profile: str | None, run_id: str) -> dict:
    """Full metadata for one run (full/partial uid or numeric scan_id), noisy fields stripped."""
    cat, rows, error, _total_uids = _connect(profile)
    if cat is None:
        return {"found": False, "error": error or "no catalog configured for this profile"}

    uid, start, stop, ambiguous = _find_run(rows, run_id)
    if uid is None:
        if ambiguous:
            return {"found": False, "ambiguous": ambiguous}
        return {"found": False, "error": f"no run matching {run_id!r}"}

    clean_start = {k: v for k, v in start.items() if k not in _DENYLIST}
    clean_stop = {k: v for k, v in stop.items() if k not in _DENYLIST}

    streams: list[str] = []
    try:
        streams = list(cat[uid])
    except Exception:  # noqa: BLE001
        pass

    return {
        "found": True,
        "uid": uid,
        "start": clean_start,
        "stop": clean_stop,
        "streams": streams,
        "num_events": (stop.get("num_events") or {}),
    }


def read_run_data(
    profile: str | None,
    run_id: str,
    stream: str = "primary",
    columns: list | None = None,
) -> dict:
    """Per-column summary statistics + a short head/tail preview for one run's stream."""
    cat, rows, error, _total_uids = _connect(profile)
    if cat is None:
        return {"found": False, "error": error or "no catalog configured for this profile"}

    uid, start, stop, ambiguous = _find_run(rows, run_id)
    if uid is None:
        if ambiguous:
            return {"found": False, "ambiguous": ambiguous}
        return {"found": False, "error": f"no run matching {run_id!r}"}

    try:
        run = cat[uid]
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "error": f"could not open run {uid}: {exc}"}

    df = bpilot_data.read_stream_df(run, stream)
    if df is None or df.empty:
        return {
            "found": False,
            "reason": f"no readable scalar data in stream {stream!r} for run {uid}",
        }

    all_columns = list(df.columns)
    wanted = [c for c in columns if c in all_columns] if columns else all_columns
    truncated = 0
    if len(wanted) > _MAX_COLUMNS:
        truncated = len(wanted) - _MAX_COLUMNS
        wanted = wanted[:_MAX_COLUMNS]

    stats: dict[str, dict] = {}
    for col in wanted:
        series = df[col]
        entry = {"n": int(series.count())}
        try:
            entry["min"] = float(series.min())
            entry["max"] = float(series.max())
            entry["mean"] = round(float(series.mean()), 6)
        except (TypeError, ValueError):
            pass
        if len(series):
            entry["first"] = _jsonable(series.iloc[0])
            entry["last"] = _jsonable(series.iloc[-1])
        stats[col] = entry

    preview_cols = wanted
    preview = {
        "head": df[preview_cols].head(3).to_dict(orient="records"),
        "tail": df[preview_cols].tail(3).to_dict(orient="records"),
    }
    result = {
        "found": True,
        "uid": uid,
        "stream": stream,
        "num_rows": int(len(df)),
        "columns": stats,
        "preview": preview,
    }
    if truncated:
        result["note"] = f"{truncated} additional column(s) omitted (cap: {_MAX_COLUMNS})"
    return result


def _jsonable(value):
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
    except Exception:  # noqa: BLE001
        pass
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)
