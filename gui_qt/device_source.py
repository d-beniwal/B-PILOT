"""Source of available device names for the plan-runner's device-typed fields.

A plan parameter can be a **device object** (e.g. ``det``) or a **list of device
objects** (e.g. ``scalers``) rather than a scalar.  For those the GUI must
(a) offer the user a dropdown of valid device names and (b) emit them *unquoted*
in the generated command (``expose(det=pg6)``) — the plan needs the real object
from the session namespace, not a string.

This module supplies the *names* only; it **never imports ophyd or connects to
hardware**.  Names come from a static YAML manifest (``device_manifest.yml``),
grouped by **beamline** then **category**, so the mechanism replicates to any
plan / device / beamline with no code change: document the parameter as
``device{<category>}`` / ``device_list{<category>}`` and list that category's
devices under the beamline in the manifest.

The source is deliberately swappable behind :class:`DeviceCatalog`.  Today it is
a static manifest; later a live queueserver ``devices_allowed`` introspection or
an ``oregistry`` dump can build the same object without the GUI noticing — write
another ``get_catalog``-style factory that returns a ``DeviceCatalog``.
"""
from __future__ import annotations

from collections import OrderedDict

from . import paths as _paths

# Static manifest shipped alongside the GUI (see :mod:`gui_qt.paths`).
MANIFEST_PATH = _paths.DEVICE_MANIFEST

# Beamline used until the GUI exposes a picker.  Override with set_beamline().
DEFAULT_BEAMLINE = "20ide"


class DeviceCatalog:
    """Available device names for one beamline, grouped by category.

    Backend-agnostic: build it from a manifest, a queueserver, or a registry —
    the GUI only calls :meth:`names_for` / :meth:`has`.
    """

    def __init__(self, beamline: str, categories: dict[str, list[str]]) -> None:
        """Store `categories` ({category: [names]}) for `beamline`, deduped."""
        self.beamline = beamline
        self._by_cat: "OrderedDict[str, list[str]]" = OrderedDict()
        for cat, names in categories.items():
            deduped: list[str] = []
            for n in names or []:
                if n and n not in deduped:
                    deduped.append(n)
            self._by_cat[cat] = deduped

    def categories(self) -> list[str]:
        """Category names available for this beamline."""
        return list(self._by_cat)

    def names_for(self, category: str | None = None) -> list[str]:
        """Device names in `category`; with None/'' return every device (deduped).

        An unknown category yields an empty list (the field will show no options
        and, if required, fail validation — surfacing a manifest gap loudly).
        """
        if category:
            return list(self._by_cat.get(category, []))
        allnames: list[str] = []
        for names in self._by_cat.values():
            for n in names:
                if n not in allnames:
                    allnames.append(n)
        return allnames

    def has(self, name: str, category: str | None = None) -> bool:
        """True if `name` is a known device (optionally within `category`)."""
        return name in self.names_for(category)

    def is_empty(self) -> bool:
        """True if this beamline exposes no devices (e.g. manifest missing)."""
        return not any(self._by_cat.values())


# ── Manifest backend ────────────────────────────────────────────────────────────

def _load_manifest(path: str) -> dict:
    """Best-effort YAML load; returns {} if missing/unreadable/yaml-absent."""
    try:
        import yaml
    except Exception:  # noqa: BLE001  (PyYAML not installed)
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001  (malformed yaml, etc.)
        return {}


_beamline = DEFAULT_BEAMLINE
_cache: dict[str, DeviceCatalog] = {}


def set_beamline(beamline: str) -> None:
    """Set the default beamline used by :func:`get_catalog` (clears the cache)."""
    global _beamline
    _beamline = beamline
    _cache.clear()


def current_beamline() -> str:
    """Return the beamline :func:`get_catalog` uses by default."""
    return _beamline


def get_catalog(beamline: str | None = None, *, path: str | None = None) -> DeviceCatalog:
    """Return the :class:`DeviceCatalog` for `beamline` (default: module setting).

    Cached per (manifest path, beamline).  Falls back to an empty catalog when
    the manifest or beamline entry is missing.
    """
    bl = beamline or _beamline
    manifest = path or MANIFEST_PATH
    key = f"{manifest}::{bl}"
    if key in _cache:
        return _cache[key]

    data = _load_manifest(manifest)
    beamlines = (data.get("beamlines") or {}) if isinstance(data, dict) else {}
    raw = beamlines.get(bl) or {}
    clean = {
        cat: list(names)
        for cat, names in raw.items()
        if isinstance(names, (list, tuple))
    }
    catalog = DeviceCatalog(bl, clean)
    _cache[key] = catalog
    return catalog
