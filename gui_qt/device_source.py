"""Source of available device names for the plan-runner's device-typed fields.

A plan parameter can be a **device object** (e.g. ``det``) or a **list of device
objects** (e.g. ``scalers``) rather than a scalar.  For those the GUI must
(a) offer the user a dropdown of valid device names and (b) emit them *unquoted*
in the generated command (``expose(det=pg6)``) — the plan needs the real object
from the session namespace, not a string.

This module supplies the *names* only; it **never imports ophyd or connects to
hardware**. Names come from :mod:`device_discovery`, which statically scans the
active profile's ``device_search_paths`` for ``__all__``-exported names and
infers a category per name. The active profile's ``device_selection``
(``{category: {name: shown_bool}}``) then filters which discovered names are
actually shown (Configuration dialog's Devices tab), so the mechanism
replicates to any plan / device / beamline with no code change: point a
profile's search paths at that beamline's device modules and Discover.
"""
from __future__ import annotations

import os

from . import config as _config
from . import device_discovery as _discovery
from . import paths as _paths


class DeviceCatalog:
    """Available device names for one beamline, grouped by category.

    Backend-agnostic: build it from discovery, a queueserver, or a registry —
    the GUI only calls :meth:`names_for` / :meth:`has`.
    """

    def __init__(self, beamline: str, categories: dict[str, list[str]]) -> None:
        """Store `categories` ({category: [names]}) for `beamline`, deduped."""
        self.beamline = beamline
        self._by_cat: dict[str, list[str]] = {}
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
        and, if required, fail validation — surfacing a discovery gap loudly).
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
        """True if this beamline exposes no devices (e.g. no search paths set)."""
        return not any(self._by_cat.values())


# ── Beamline selection ──────────────────────────────────────────────────────

# Explicit override (used by CLI-style tools); None means "read config.get('beamline')".
_beamline_override: str | None = None
_cache: dict[str, DeviceCatalog] = {}


def set_beamline(beamline: str | None) -> None:
    """Override the beamline used by :func:`get_catalog` (None reverts to config)."""
    global _beamline_override
    _beamline_override = beamline
    _cache.clear()


def current_beamline() -> str:
    """Return the beamline :func:`get_catalog` uses by default."""
    return _beamline_override or _config.get("beamline")


def refresh() -> None:
    """Clear the cached catalog so the next :func:`get_catalog` re-scans."""
    _cache.clear()


def resolve_path(path: str) -> str:
    """Resolve a (possibly project-relative) device search path to an absolute one."""
    return path if os.path.isabs(path) else os.path.join(_paths.PROJECT_ROOT, path)


def get_catalog(beamline: str | None = None, *, search_paths: list[str] | None = None) -> DeviceCatalog:
    """Return the :class:`DeviceCatalog` for `beamline` (default: active profile).

    Cached per (beamline, search paths). Falls back to an empty catalog when
    no search paths are configured (e.g. a beamline profile not yet set up).
    """
    bl = beamline or current_beamline()
    raw_paths = search_paths if search_paths is not None else (_config.get("device_search_paths") or [])
    key = f"{bl}::{'|'.join(raw_paths)}"
    if key in _cache:
        return _cache[key]

    selection = _config.get("device_selection") or {}
    resolved_paths = [resolve_path(p) for p in raw_paths]
    by_cat: dict[str, list[str]] = {}
    for device in _discovery.scan(resolved_paths):
        cat_selection = selection.get(device.category, {})
        if not cat_selection.get(device.name, True):  # unseen names default shown
            continue
        by_cat.setdefault(device.category, []).append(device.name)

    catalog = DeviceCatalog(bl, by_cat)
    _cache[key] = catalog
    return catalog
