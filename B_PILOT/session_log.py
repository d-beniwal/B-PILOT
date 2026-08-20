"""Read-only view of one experiment's persistent, newest-first history.

Paired with :mod:`experiment_history` (the storage) and :mod:`session_recorder`
(the detached process that keeps appending to it): this widget renders that
record — everything that has reached the kernel across every launch/attach
under this experiment name, not just the current session — and live-tails new
entries as they're appended, even while the kernel is busy or the GUI was
just reattached.

Ordering is newest-first (like a Mongo/tiled catalog query sorted `-1`): the
most recent entry is always at the top, so re-opening this tab (or attaching
under an experiment that already has history) shows what just happened
without scrolling.
"""
from __future__ import annotations

import html
import json
import os
import time

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

from . import experiment_history
from . import style as S

# Cap on rendered entries (successor to the old QPlainTextEdit
# `setMaximumBlockCount` cap -- a QTextEdit has no such built-in limit, and
# each rendered entry here is a heavier HTML block than a raw text line, so
# the cap is smaller). Re-checked in _prepend with a margin so a full
# _reload() (which re-caps from disk) only runs occasionally, not on every
# poll tick.
_MAX_RENDERED_ENTRIES = 2000
_TRIM_MARGIN = 200

# Kinds rendered as a shaded "output box" -- everything the kernel produced
# as a *result* of running a command, as opposed to the command itself
# ("input") or a structural bookkeeping line ("marker").
_OUTPUT_KINDS = {"stream", "result", "display", "error"}

# A dashed rule marking the boundary between one command's input+output block
# and the next. Entries render newest-first, and an "input" entry always has
# the smallest timestamp within its own block (its output(s) come after it in
# time, so they sort above it) -- so appending this right after an "input"
# entry's HTML correctly closes out that block, whether the whole history is
# rendered at once (_render_entries) or a batch is prepended live (_prepend):
# new entries always land above whatever was already there, so a
# once-emitted separator is never split away from the input it followed.
_BLOCK_SEP = f'<hr style="border:none; border-top:1px dashed {S.BORDER}; margin:8px 0;">'


def _fmt_ts(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "?"


def _entry_html(entry: dict) -> str:
    """One entry -> an HTML block: plain for a command, boxed for its output.

    Commands ("input") render bare, like a prompt -- a bold accent label plus
    the command text. Everything the kernel produced in response (stream,
    result, display, error) renders inside a shaded, bordered box so it reads
    as visually distinct output. Markers (kernel launched/attached/shut down)
    render as a small centered divider. All entry text is HTML-escaped since
    it can be arbitrary kernel stdout/tracebacks, never markup.
    """
    kind = entry.get("kind", "")
    stamp = _fmt_ts(entry.get("ts"))
    text = html.escape((entry.get("text") or "").rstrip("\n"))

    if kind == "marker":
        return (
            f'<p align="center" style="color:{S.MUTED}; font-style:italic; '
            f'margin:6px 0;">&mdash; {text} &middot; {stamp} &mdash;</p>'
        )

    if kind in _OUTPUT_KINDS:
        label_color = S.ERROR if kind == "error" else S.MUTED
        return (
            f'<table width="100%" cellspacing="0" cellpadding="6" '
            f'style="background-color:{S.ALT_ROW_BG}; border:1px solid {S.BORDER}; '
            f'margin:4px 0;"><tr><td>'
            f'<span style="color:{label_color}; font-weight:600;">[{kind}]</span> '
            f'<span style="color:{S.MUTED};">{stamp}</span>'
            f'<pre style="margin:4px 0 0 0; font-family:{S.MONO_CSS}; '
            f'color:{S.TEXT};">{text}</pre>'
            f"</td></tr></table>"
        )

    # kind == "input" (or anything unrecognized -- render like a command
    # rather than silently dropping it).
    return (
        f'<p style="margin:8px 0 4px 0;">'
        f'<b style="color:{S.ACCENT};">&#9654; input</b> '
        f'<span style="color:{S.MUTED};">{stamp}</span>'
        f'<pre style="margin:2px 0 0 0; font-family:{S.MONO_CSS}; '
        f'color:{S.TEXT};">{text}</pre>'
        f"</p>"
    )


def _closes_block(kind: str) -> bool:
    """True for the entry kind that ends an input+output block -- "input"
    itself, or anything unrecognized (rendered the same way as "input" -- see
    _entry_html's fallback branch)."""
    return kind not in _OUTPUT_KINDS and kind != "marker"


def _render_entries(entries: list[dict]) -> str:
    """`entries` (any order) -> HTML, newest block first, capped in length."""
    ordered = sorted(entries, key=lambda e: e.get("ts") or 0, reverse=True)
    ordered = ordered[:_MAX_RENDERED_ENTRIES]
    parts = []
    for e in ordered:
        parts.append(_entry_html(e))
        if _closes_block(e.get("kind", "")):
            parts.append(_BLOCK_SEP)
    return "".join(parts)


class SessionLogView(QtWidgets.QWidget):
    """Tails an experiment's history record into a read-only, newest-first view."""

    def __init__(self, parent=None) -> None:
        """Build the view; call :meth:`load` with (beamline, experiment) to start."""
        super().__init__(parent)
        self._beamline: str | None = None
        self._experiment: str | None = None
        self._pos = 0   # byte offset into the history file already rendered
        self._rendered_count = 0   # entries currently rendered (see _MAX_RENDERED_ENTRIES)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        self._follow = QtWidgets.QCheckBox("Follow")
        self._follow.setChecked(True)
        self._follow.setToolTip("Keep scrolled to the newest entry (shown at the top).")
        row.addWidget(self._follow)
        reload_btn = QtWidgets.QPushButton("Reload")
        reload_btn.setToolTip("Re-read the whole history record from disk.")
        reload_btn.clicked.connect(self._reload)
        row.addWidget(reload_btn)
        row.addStretch(1)
        self._path_lbl = QtWidgets.QLabel("(no session)")
        self._path_lbl.setStyleSheet(f"color: {S.MUTED};")
        self._path_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        row.addWidget(self._path_lbl)
        lay.addLayout(row)

        self._text = QtWidgets.QTextEdit()
        self._text.setObjectName("mono")
        self._text.setReadOnly(True)
        self._text.setFont(QtGui.QFont(S.MONO_FAMILIES[0]))
        self._text.setPlaceholderText(
            "This experiment's full interactive history appears here once a "
            "session is running (input, output, errors), newest entry on top — "
            "and keeps recording even while the GUI is closed or the kernel is "
            "busy, across any number of launches/attaches under this experiment."
        )
        lay.addWidget(self._text, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)   # poll the file for new lines
        self._timer.timeout.connect(self._poll)

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, beamline: str | None, experiment: str | None) -> None:
        """Show `experiment`'s full history (from disk) and start live-tailing it."""
        self._timer.stop()
        self._beamline = beamline or None
        self._experiment = experiment if beamline else None
        self._pos = 0
        self._text.clear()
        if self._beamline is not None and self._experiment is not None:
            self._path_lbl.setText(f"{self._beamline} / {self._experiment}")
            self._reload()
            self._timer.start()
        else:
            self._path_lbl.setText("(no session)")

    def stop(self) -> None:
        """Stop tailing (e.g. when the kernel is shut down)."""
        self._timer.stop()

    # ── Internals ────────────────────────────────────────────────────────────

    def _history_path(self) -> str | None:
        if self._beamline is None or self._experiment is None:
            return None
        return experiment_history.history_path(self._beamline, self._experiment)

    def _reload(self) -> None:
        path = self._history_path()
        if path is None:
            return
        entries = experiment_history.read_entries(self._beamline, self._experiment)
        self._text.setHtml(_render_entries(entries))
        self._rendered_count = min(len(entries), _MAX_RENDERED_ENTRIES)
        try:
            self._pos = os.path.getsize(path)
        except OSError:
            self._pos = 0
        if self._follow.isChecked():
            self._text.verticalScrollBar().setValue(0)

    def _poll(self) -> None:
        path = self._history_path()
        if path is None:
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size < self._pos:      # file was truncated/replaced — re-read
            self._reload()
            return
        if size == self._pos:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                fh.seek(self._pos)
                data = fh.read()
        except OSError:
            return
        # Only consume complete lines -- a line still mid-write is left for
        # the next poll rather than parsed as a truncated (invalid) entry.
        cut = data.rfind("\n")
        if cut < 0:
            return
        new_lines = data[: cut + 1]
        self._pos += len(new_lines.encode("utf-8", errors="replace"))

        entries = []
        for line in new_lines.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        if entries:
            self._prepend(entries)

    def _prepend(self, entries: list[dict]) -> None:
        """Insert newly-arrived `entries` (file order) at the very top, newest first."""
        self._rendered_count += len(entries)
        if self._rendered_count > _MAX_RENDERED_ENTRIES + _TRIM_MARGIN:
            # Simpler and more robust than surgically trimming the tail of a
            # rich QTextEdit -- a full reload re-caps from disk, and this only
            # happens once every _TRIM_MARGIN new entries, not every poll.
            self._reload()
            return
        ordered = sorted(entries, key=lambda e: e.get("ts") or 0, reverse=True)
        parts = []
        for e in ordered:
            parts.append(_entry_html(e))
            if _closes_block(e.get("kind", "")):
                parts.append(_BLOCK_SEP)
        block = "".join(parts)
        cursor = QtGui.QTextCursor(self._text.document())
        cursor.movePosition(QtGui.QTextCursor.Start)
        cursor.insertHtml(block)
        if self._follow.isChecked():
            self._text.verticalScrollBar().setValue(0)
