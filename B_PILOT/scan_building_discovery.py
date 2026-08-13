"""Static-analysis discovery of scan_skeletons.py building blocks.

``instrument/plans/scan_skeletons.py`` plans (``mpe_list_scan``,
``mpe_step_scan``, ...) take ``plan_opener``, ``per_step``, ``plan_closer``,
``suspenders``, and ``pseudo_suspenders`` as keyword arguments — function/
object references, not primitives. This module catalogs the valid names for
each, the same way :mod:`device_discovery` catalogs devices: by reading each
source file's ``__all__`` list with :mod:`ast`, never importing it (no
ophyd, no EPICS, no hardware).

Unlike devices, categorizing these names needs more than the exported-name
list: ``plan_opener``/``per_step``/``plan_closer`` (from
``scan_hw_triggering.py`` / ``scan_sw_triggering.py``) and true-vs-pseudo
suspenders (from ``suspenders.py`` / ``suspenders_pseudo.py``) are only
distinguished by the ``# section comment`` immediately preceding a run of
names inside ``__all__`` — e.g.::

    __all__ = [
        # scan openers ------------------
        "hardware_opener",

        # per-step plan stubs -----------
        "hardware_sweep_",
    ]

``ast`` discards comments, so :func:`_entries_with_headers` uses
:mod:`tokenize` instead to walk the token stream inside the ``__all__``
assignment and pair each string literal with the nearest preceding comment.
Beamline-specific ``<bl>_suspenders.py`` files have no section comments at
all — everything in them is a true suspender by convention (see
``mpe_bluesky/.context/DOMAIN.md``, "scan_skeletons.py building blocks").
"""
from __future__ import annotations

import ast
import io
import os
import tokenize
from typing import NamedTuple

CATEGORIES = ("plan_opener", "per_step", "plan_closer", "suspender", "pseudo_suspender")


class DiscoveredBlock(NamedTuple):
    """One `__all__`-exported name found by a `scan_*` function, categorized
    by its section comment (or, for beamline-specific suspender files with no
    section comments, by filename)."""

    name: str
    category: str
    source_file: str


def _entries_with_headers(path: str) -> list[tuple[str, str | None]]:
    """Every `__all__` string in `path`, paired with the lowercased text of
    the nearest preceding `#` comment inside the list (None if none seen
    yet). Returns `[]` if the file can't be parsed/read/tokenized."""
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    all_range: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Name)]
        if len(targets) == 1 and targets[0].id == "__all__":
            all_range = (node.lineno, node.end_lineno or node.lineno)
            break
    if all_range is None:
        return []
    start, end = all_range

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        return []

    entries: list[tuple[str, str | None]] = []
    current_header: str | None = None
    for tok in tokens:
        line = tok.start[0]
        if line < start:
            continue
        if line > end:
            break
        if tok.type == tokenize.COMMENT:
            current_header = tok.string.lstrip("#").strip().lower()
        elif tok.type == tokenize.STRING:
            try:
                value = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, str):
                entries.append((value, current_header))
    return entries


def _category_from_header(header: str | None) -> str | None:
    """Map a `__all__` section comment to one of :data:`CATEGORIES`, or None
    to exclude the entries under it (signals, bookkeeping lists of
    per-steps, and un-headered helper functions like `retrieve_det`)."""
    if not header:
        return None
    if "pseudo" in header:
        return "pseudo_suspender"
    if "signal" in header:
        return None
    if "suspender" in header:
        return "suspender"
    if "opener" in header:
        return "plan_opener"
    if "per-step" in header or "per step" in header:
        return "per_step"
    if "closer" in header:
        return "plan_closer"
    return None


def scan_plan_stub_files(paths: list[str]) -> list[DiscoveredBlock]:
    """Discover plan_opener/per_step/plan_closer names from
    scan_hw_triggering.py / scan_sw_triggering.py -style files."""
    blocks: list[DiscoveredBlock] = []
    for path in paths:
        for name, header in _entries_with_headers(path):
            category = _category_from_header(header)
            if category in ("plan_opener", "per_step", "plan_closer"):
                blocks.append(DiscoveredBlock(name, category, path))
    return blocks


def scan_suspender_files(paths: list[str]) -> list[DiscoveredBlock]:
    """Discover suspender/pseudo_suspender names from suspenders.py /
    suspenders_pseudo.py -style files (section-commented) and
    beamline-specific <bl>_suspenders.py files (no section comments — every
    entry is a true suspender, or a pseudo one if "pseudo" is in the
    filename, matching the one file in this codebase that would ever need
    it)."""
    blocks: list[DiscoveredBlock] = []
    for path in paths:
        entries = _entries_with_headers(path)
        if entries and all(header is None for _, header in entries):
            fallback = "pseudo_suspender" if "pseudo" in os.path.basename(path).lower() else "suspender"
            blocks.extend(DiscoveredBlock(name, fallback, path) for name, _ in entries)
            continue
        for name, header in entries:
            category = _category_from_header(header)
            if category in ("suspender", "pseudo_suspender"):
                blocks.append(DiscoveredBlock(name, category, path))
    return blocks


def scan(plan_stub_paths: list[str], suspender_paths: list[str]) -> dict[str, list[str]]:
    """Discover every building-block name from both sets of paths (already
    resolved to absolute/usable paths by the caller), grouped by category
    and deduped (first occurrence wins) within each category."""
    result: dict[str, list[str]] = {cat: [] for cat in CATEGORIES}
    seen: dict[str, set[str]] = {cat: set() for cat in CATEGORIES}
    for block in scan_plan_stub_files(plan_stub_paths) + scan_suspender_files(suspender_paths):
        if block.name in seen[block.category]:
            continue
        seen[block.category].add(block.name)
        result[block.category].append(block.name)
    return result
