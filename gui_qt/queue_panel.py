"""Plan queue — a table view/editor over the persistent per-beamline queue.

The queue itself lives in :mod:`queue_store` (a locked JSON file) and is driven
by the detached :mod:`queue_runner`, so this panel is a **view + editor**, not a
scheduler:

* It **polls** the queue file, so it restores after a GUI crash/close and shows
  status the runner set even while the GUI was detached (jobs finishing in a
  detached kernel).
* Columns: **#**, **Name** (double-click to edit), **Status** (coloured), and a
  truncated **Command** whose full text (and notes) shows on hover.
* Start/Pause just flip the queue ``state``; the runner does the dispatching.
"""
from __future__ import annotations

import json

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

from . import config
from . import queue_store as qs
from . import style as S

# Status → colour.  Per request: DONE red, RUNNING green, WAITING orange;
# ERROR gets a distinct purple so failures stand out from completed runs.
_STATUS_COLOR = {
    qs.WAITING: "#e69500",   # orange
    qs.RUNNING: "#2e7d32",   # green
    qs.DONE:    "#c62828",   # red
    qs.ERROR:   "#7b1fa2",   # purple (distinct from done)
}
_STATE_LABEL = {qs.IDLE: "Idle", qs.S_RUNNING: "Running", qs.PAUSED: "Paused"}


def _short(command: str, limit: int = 80) -> str:
    """One-line, truncated preview (the RE(...) line) of a command."""
    text = command.strip()
    line = text.splitlines()[-1] if text else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


class QueuePanel(QtWidgets.QWidget):
    """Table view of the persistent plan queue (one per beamline session)."""

    # emitted with the selected item's command text when "Copy to form" is clicked
    copyToFormRequested = QtCore.pyqtSignal(str)

    def __init__(self, console=None, parent=None) -> None:
        """`console` is optional, used only to advise when no kernel is running."""
        super().__init__(parent)
        self._console = console
        self._loading = False        # guard so programmatic edits don't re-trigger
        self._last_sig: str | None = None
        self._build_ui()

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._table = QtWidgets.QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["#", "Name", "Status", "Command"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
        )
        self._table.setWordWrap(False)
        self._table.setToolTip(
            "Double-click a Name to rename. Hover a row for the full command."
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self._table.setColumnWidth(1, 160)
        self._table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self._table, 1)

        row = QtWidgets.QHBoxLayout()
        self._start_btn = S.primary_btn("▶ Start")
        self._start_btn.setMinimumHeight(S.px(26))
        self._start_btn.clicked.connect(self._start)
        self._pause_btn = QtWidgets.QPushButton("Pause")
        self._pause_btn.clicked.connect(self._pause)
        up = QtWidgets.QPushButton("▲")
        up.setToolTip("Move selected plan up")
        up.clicked.connect(lambda: self._move(-1))
        down = QtWidgets.QPushButton("▼")
        down.setToolTip("Move selected plan down")
        down.clicked.connect(lambda: self._move(1))
        delete = QtWidgets.QPushButton("Delete")
        delete.clicked.connect(self._delete)
        clear = QtWidgets.QPushButton("Clear finished")
        clear.clicked.connect(self._clear_finished)
        self._copy_btn = QtWidgets.QPushButton("Copy to form")
        self._copy_btn.setToolTip(
            "Load the selected plan's command back into the main panel's form "
            "so you can tweak and resubmit it."
        )
        self._copy_btn.clicked.connect(self._copy_to_form)
        for w in (self._start_btn, self._pause_btn, up, down, delete, clear, self._copy_btn):
            row.addWidget(w)
        row.addStretch(1)
        self._state_lbl = QtWidgets.QLabel("Idle")
        self._state_lbl.setStyleSheet(f"color: {S.MUTED};")
        row.addWidget(self._state_lbl)
        lay.addLayout(row)

    # ── Public: add to queue ─────────────────────────────────────────────────────

    def add(self, command: str, notes: str = "") -> None:
        """Append a plan command to the persistent queue."""
        qs.add(self._beamline(), command, notes)
        self._refresh()

    # ── Helpers ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _beamline() -> str:
        return config.get("beamline")

    def _selected_id(self) -> str | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        cell = self._table.item(row, 0)
        return cell.data(QtCore.Qt.UserRole) if cell is not None else None

    # ── Controls ──────────────────────────────────────────────────────────────────

    def _start(self) -> None:
        bl = self._beamline()
        data = qs.load(bl)
        if not any(it["status"] == qs.WAITING for it in data["items"]):
            self._set_state_msg("Nothing queued", warn=True)
            return
        qs.set_state(bl, qs.S_RUNNING)
        if self._console is not None and not self._console.is_running():
            self._set_state_msg(
                "Armed — will run when a kernel is available", warn=True
            )
        self._refresh()

    def _pause(self) -> None:
        # Stops the runner dispatching the NEXT plan; the current one keeps going.
        qs.set_state(self._beamline(), qs.PAUSED)
        self._refresh()

    def _move(self, delta: int) -> None:
        item_id = self._selected_id()
        if item_id is not None:
            qs.move(self._beamline(), item_id, delta)
            self._refresh()

    def _delete(self) -> None:
        item_id = self._selected_id()
        if item_id is None:
            return
        qs.remove(self._beamline(), item_id)
        self._refresh()

    def _clear_finished(self) -> None:
        qs.clear_finished(self._beamline())
        self._refresh()

    def _copy_to_form(self) -> None:
        item_id = self._selected_id()
        if item_id is None:
            return
        data = qs.load(self._beamline())
        item = next((it for it in data["items"] if it["id"] == item_id), None)
        if item is not None:
            self.copyToFormRequested.emit(item["command"])

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._loading or item.column() != 1:
            return
        id_cell = self._table.item(item.row(), 0)
        if id_cell is None:
            return
        item_id = id_cell.data(QtCore.Qt.UserRole)
        if item_id:
            qs.rename(self._beamline(), item_id, item.text().strip())

    # ── Rendering (polled) ────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        # Don't fight the user mid-edit.
        if self._table.state() == QtWidgets.QAbstractItemView.EditingState:
            return
        data = qs.load(self._beamline())
        items = data["items"]
        sig = json.dumps(
            [data.get("state"), [(it["id"], it["name"], it["status"],
                                  it["command"]) for it in items]]
        )
        if sig == self._last_sig:
            return
        self._last_sig = sig

        keep_id = self._selected_id()
        self._loading = True
        self._table.setRowCount(len(items))
        for r, it in enumerate(items):
            tip = it["command"] + (
                f"\n\nnotes: {it['notes']}" if it.get("notes") else ""
            )

            num = QtWidgets.QTableWidgetItem(str(r + 1))
            num.setData(QtCore.Qt.UserRole, it["id"])
            num.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            name = QtWidgets.QTableWidgetItem(it["name"])
            name.setFlags(
                QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                | QtCore.Qt.ItemIsEditable
            )

            status = QtWidgets.QTableWidgetItem(it["status"].upper())
            status.setForeground(
                QtGui.QColor(_STATUS_COLOR.get(it["status"], S.TEXT))
            )
            status.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            cmd = QtWidgets.QTableWidgetItem(_short(it["command"]))
            cmd.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            for cell in (num, name, status, cmd):
                cell.setToolTip(tip)
            self._table.setItem(r, 0, num)
            self._table.setItem(r, 1, name)
            self._table.setItem(r, 2, status)
            self._table.setItem(r, 3, cmd)
        self._loading = False

        if keep_id is not None:
            for r in range(self._table.rowCount()):
                cell = self._table.item(r, 0)
                if cell is not None and cell.data(QtCore.Qt.UserRole) == keep_id:
                    self._table.setCurrentCell(r, 0)
                    break

        self._state_lbl.setStyleSheet(f"color: {S.MUTED};")
        self._state_lbl.setText(_STATE_LABEL.get(data.get("state"), "Idle"))

    def _set_state_msg(self, text: str, *, warn: bool = False) -> None:
        self._state_lbl.setStyleSheet(f"color: {S.ERROR if warn else S.MUTED};")
        self._state_lbl.setText(text)
        QtCore.QTimer.singleShot(
            3000, lambda: self._state_lbl.setStyleSheet(f"color: {S.MUTED};")
        )
