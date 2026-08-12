"""Read-only view that loads and live-tails a session transcript file.

Paired with :mod:`session_recorder`: the recorder writes the kernel's full
IOPub transcript to a file, and this view shows it — so you can see *everything*
that happened in the kernel (across GUI restarts) and watch live output even
while the kernel is busy running a plan.
"""
from __future__ import annotations

import os

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

from . import style as S


class SessionLogView(QtWidgets.QWidget):
    """Tails a transcript file into a read-only console-style view."""

    def __init__(self, parent=None) -> None:
        """Build the view; call :meth:`load` with a transcript path to start."""
        super().__init__(parent)
        self._path: str | None = None
        self._pos = 0

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        self._follow = QtWidgets.QCheckBox("Follow")
        self._follow.setChecked(True)
        self._follow.setToolTip("Auto-scroll to the newest output.")
        row.addWidget(self._follow)
        reload_btn = QtWidgets.QPushButton("Reload")
        reload_btn.setToolTip("Re-read the whole transcript from disk.")
        reload_btn.clicked.connect(self._reload)
        row.addWidget(reload_btn)
        row.addStretch(1)
        self._path_lbl = QtWidgets.QLabel("(no session)")
        self._path_lbl.setStyleSheet(f"color: {S.MUTED};")
        self._path_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        row.addWidget(self._path_lbl)
        lay.addLayout(row)

        self._text = QtWidgets.QPlainTextEdit()
        self._text.setObjectName("mono")
        self._text.setReadOnly(True)
        self._text.setFont(QtGui.QFont(S.MONO_FAMILIES[0]))
        self._text.setMaximumBlockCount(50000)   # cap memory on very long runs
        self._text.setPlaceholderText(
            "The full kernel transcript appears here once a session is running "
            "(input, output, errors) — and keeps recording even while the GUI "
            "is closed or the kernel is busy."
        )
        lay.addWidget(self._text, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)   # poll the file for new bytes
        self._timer.timeout.connect(self._poll)

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, path: str | None) -> None:
        """Show `path` from the beginning and start live-tailing it."""
        self._timer.stop()
        self._path = path or None
        self._pos = 0
        self._text.clear()
        if self._path:
            self._path_lbl.setText(self._path)
            self._read_new()
            self._timer.start()
        else:
            self._path_lbl.setText("(no session)")

    def stop(self) -> None:
        """Stop tailing (e.g. when the kernel is shut down)."""
        self._timer.stop()

    # ── Internals ────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        if self._path:
            self._pos = 0
            self._text.clear()
            self._read_new()

    def _poll(self) -> None:
        if not self._path:
            return
        try:
            size = os.path.getsize(self._path)
        except OSError:
            return
        if size < self._pos:      # file was truncated/replaced — re-read
            self._pos = 0
            self._text.clear()
        self._read_new()

    def _read_new(self) -> None:
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8", errors="replace") as fh:
                fh.seek(self._pos)
                data = fh.read()
                self._pos = fh.tell()
        except OSError:
            return
        if data:
            self._append(data)

    def _append(self, data: str) -> None:
        sb = self._text.verticalScrollBar()
        cursor = self._text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self._text.setTextCursor(cursor)
        self._text.insertPlainText(data)
        if self._follow.isChecked():
            sb.setValue(sb.maximum())
