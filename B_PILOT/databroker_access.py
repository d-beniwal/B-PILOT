"""Qt-free databroker/catalog access shared by :mod:`B_PILOT.viewer` (the
human-facing "Open Bluesky Viewer" window) and AutoPILOT's read-only data
tools (``AutoPILOT/autopilot/data_catalog.py``).

Deliberately has no PyQt import so AutoPILOT's otherwise Qt-free pipeline
(see ``AutoPILOT/autopilot/tools.py``'s docstring) can reuse real databroker
logic without pulling in a Qt dependency just to search/describe runs.

Callers are responsible for their own ``socket.setdefaulttimeout(...)``
guard against pymongo's lack of a default *read* timeout (see
``B_PILOT/viewer.py``'s module docstring for the original rationale) --
that is a process-wide side effect this module deliberately does not apply
on import, since it's used from two very different process contexts (the
viewer's own standalone process vs. AutoPILOT running in-process inside the
main GUI).

MPE stores runs in **databroker catalogs backed by MongoDB** (not Tiled).
The catalog name is chosen per account in ``instrument/iconfig.yml``
(``DATABROKER_CATALOG``: ``hexm`` for ``s20iduser`` / 20-ID-E, ``ht_hedm`` for
``s20hedm``, ``1id_hexm`` for 1-ID); the connection URIs live in
``~/.local/share/intake/*.yml``.

Read-only: this only *reads* stored run documents; it never touches hardware.
"""
from __future__ import annotations

from . import config as _config
from . import paths as _paths

# MPE instrument config lives at <bluesky_root>/instrument/iconfig.yml.
_ICONFIG = _paths.ICONFIG
PAGE_SIZE = 500  # runs fetched per page (catalogs can hold tens of thousands)

# Fallback catalog name if it can't be resolved from iconfig by account.
# 20-ID-E (s20iduser) uses 'hexm'; see instrument/iconfig.yml.
_DEFAULT_CATALOG = "hexm"


def short(value, limit: int = 160) -> str:
    """Truncate a (possibly huge) message so it can't blow up a display or a tool result."""
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _catalog_from_iconfig() -> str:
    """Catalog name for the current account from iconfig.yml (best effort).

    ``iconfig.yml`` maps each MPE account to a ``DATABROKER_CATALOG`` (e.g.
    ``s20iduser`` → ``hexm``).  Falls back to :data:`_DEFAULT_CATALOG` if the
    file, the account entry, or the key is missing.
    """
    import getpass

    try:
        import yaml

        with open(_ICONFIG, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        acct = cfg.get(getpass.getuser())
        if isinstance(acct, dict) and acct.get("DATABROKER_CATALOG"):
            return str(acct["DATABROKER_CATALOG"])
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_CATALOG


def load_defaults() -> dict:
    """Connection defaults: the active profile's settings, else iconfig.yml auto-detect.

    ``databroker_catalog`` (Configuration → Data Viewer) takes priority when
    set; an empty profile value preserves the original zero-config behavior
    of guessing the catalog from the logged-in account.
    """
    catalog = _config.get("databroker_catalog") or _catalog_from_iconfig()
    return {
        "catalog": catalog,
        "uri": _config.get("databroker_uri") or "",
        "nexus_dir": _config.get("databroker_nexus_dir") or "",
    }


def connect_catalog(catalog: str, uri: str = "") -> tuple:
    """Return (catalog_obj, status_message).  Falls back to a temp catalog.

    Priority: explicit **Tiled URI** (optional override) → named **databroker
    catalog** → empty temporary catalog.
    """
    if uri:
        try:
            from tiled.client import from_uri

            client = from_uri(uri)
            return client, f"Connected to Tiled URI {uri}"
        except Exception as exc:  # noqa: BLE001
            return _temp_catalog(f"Tiled URI {uri} failed ({short(exc)})")
    if catalog:
        try:
            import databroker

            cat = databroker.catalog[catalog]
            return cat, f"Connected to databroker catalog '{catalog}'"
        except KeyError:
            return _temp_catalog(
                f"catalog '{catalog}' not found — check ~/.local/share/intake/*.yml"
            )
        except Exception as exc:  # noqa: BLE001
            return _temp_catalog(f"catalog '{catalog}' failed ({short(exc)})")
    return _temp_catalog("no catalog/URI configured")


def _temp_catalog(reason: str) -> tuple:
    """Empty temporary databroker catalog (dev fallback)."""
    try:
        import databroker

        return databroker.temp().v2, f"⚠ {reason} — showing empty temp catalog"
    except Exception as exc:  # noqa: BLE001
        return None, f"✗ no catalog: {reason}; temp fallback failed ({short(exc)})"


def meta(run) -> tuple[dict, dict]:
    """Return (start_doc, stop_doc) for a run, tolerant of catalog flavour."""
    md = getattr(run, "metadata", None) or {}
    try:
        start = dict(md.get("start") or {})
    except Exception:  # noqa: BLE001
        start = {}
    try:
        stop = dict(md.get("stop") or {})
    except Exception:  # noqa: BLE001
        stop = {}
    return start, stop


def read_stream_df(run, stream):
    """Best-effort: return a pandas DataFrame of scalar columns, or None."""
    node = None
    try:
        node = run[stream]
    except Exception:  # noqa: BLE001
        return None
    ds = None
    for reader in (
        lambda: node.read(),
        lambda: node["data"].read(),
        lambda: node.to_dask(),
    ):
        try:
            ds = reader()
            break
        except Exception:  # noqa: BLE001
            continue
    if ds is None:
        return None
    try:
        df = ds.to_dataframe() if hasattr(ds, "to_dataframe") else ds
        # keep only scalar-per-event columns (drop image/array fields)
        keep = [
            c for c in df.columns
            if df[c].map(lambda x: getattr(x, "ndim", 0)).max() == 0
        ]
        return df[keep] if keep else df
    except Exception:  # noqa: BLE001
        return None


def all_uids(cat) -> list:
    """Every run uid in the catalog, in catalog-native order.

    This is the expensive part on a large remote MongoDB catalog — callers
    should fetch it once per connection and reuse it across page turns
    rather than re-listing the whole catalog on every page (which is what
    made every page navigation redo this from scratch).
    """
    try:
        return list(cat)
    except Exception:  # noqa: BLE001
        return []


def page_from_uids(cat, uids: list, offset: int, limit: int = PAGE_SIZE, progress_cb=None) -> list:
    """Return [(uid, start, stop), …] for one page, newest first.

    ``databroker`` catalogs iterate **newest-first** natively (the same
    convention behind ``catalog[-1]`` meaning "most recent run" — the
    underlying Mongo query sorts by ``time`` descending), so ``offset``
    counts *forward* from the head of ``uids``: 0 is the most recent page.
    ``progress_cb(done, total)`` — if given — is invoked periodically while
    fetching per-run metadata, since that's a per-uid catalog round-trip and
    can be slow on a remote MongoDB catalog.
    """
    window = uids[offset:offset + limit]
    out: list[tuple] = []
    for i, uid in enumerate(window):
        try:
            start, stop = meta(cat[uid])
            out.append((uid, start, stop))
        except Exception:  # noqa: BLE001
            continue
        if progress_cb is not None and (i % 25 == 0 or i == len(window) - 1):
            progress_cb(i + 1, len(window))
    out.sort(key=lambda t: t[1].get("time", 0), reverse=True)
    return out


def list_runs(cat, offset: int = 0, limit: int = PAGE_SIZE, progress_cb=None) -> tuple[list, int, list]:
    """Return ([(uid, start, stop), …], total, uids) for one page, newest first.

    Convenience wrapper used for the initial connect, where there is no uid
    list to reuse yet. ``uids`` is returned so the caller can cache it for
    subsequent page turns via :func:`page_from_uids` instead of calling this
    (and re-listing the whole catalog) again.
    """
    uids = all_uids(cat)
    total = len(uids)
    rows = page_from_uids(cat, uids, offset, limit, progress_cb=progress_cb)
    return rows, total, uids


def list_catalogs() -> tuple[list[str], str]:
    """Return (names, error) — every catalog registered for this account.

    Backed by ``databroker.catalog``, a dict-like registry populated from
    ``~/.local/share/intake/*.yml``. Best effort: on any failure, returns an
    empty list and a short error message instead of raising.
    """
    try:
        import databroker

        return sorted(str(name) for name in databroker.catalog), ""
    except Exception as exc:  # noqa: BLE001
        return [], short(exc)
