"""Read-only view of the queue server's own RunEngine console output.

Companion to :mod:`session_log` (which tails the *embedded-kernel* session's
transcript file): this view instead polls :func:`B_PILOT.qs_client.console_text`,
which mirrors ``REManagerAPI.console_monitor``'s live buffer over QS's
``--zmq-publish-console`` socket. These two views are deliberately kept
separate rather than merged into one -- they show output from two genuinely
different RunEngine processes (the embedded kernel vs. QS's own RE Worker on
redwood), and conflating them would misattribute output to the wrong one.
See :mod:`qs_client`'s module docstring for why this bridge didn't exist
before.
"""
from __future__ import annotations

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

from . import qs_client
from . import style as S


class QSConsolePanel(QtWidgets.QWidget):
    """Polls :func:`qs_client.console_text` into a read-only console-style view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._last_uid: str | None = None

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        self._follow = QtWidgets.QCheckBox("Follow")
        self._follow.setChecked(True)
        self._follow.setToolTip("Auto-scroll to the newest output.")
        row.addWidget(self._follow)
        row.addStretch(1)
        self._status_lbl = QtWidgets.QLabel("")
        self._status_lbl.setStyleSheet(f"color: {S.MUTED};")
        row.addWidget(self._status_lbl)
        lay.addLayout(row)

        self._text = QtWidgets.QPlainTextEdit()
        self._text.setObjectName("mono")
        self._text.setReadOnly(True)
        self._text.setFont(QtGui.QFont(S.MONO_FAMILIES[0]))
        self._text.setMaximumBlockCount(50000)
        self._text.setPlaceholderText(
            "RunEngine console output for plans dispatched through the queue "
            "server appears here (print statements, scan progress, errors) — "
            "separate from the embedded-kernel Console/Session log tabs."
        )
        lay.addWidget(self._text, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _poll(self) -> None:
        text, uid = qs_client.console_text()
        if uid == self._last_uid:
            return
        self._last_uid = uid
        sb = self._text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self._text.setPlainText(text)
        if self._follow.isChecked() or at_bottom:
            sb.setValue(sb.maximum())
        self._status_lbl.setText(
            "connected" if qs_client.connected() else "⚠ not connected to queue server"
        )
