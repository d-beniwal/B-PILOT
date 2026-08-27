"""Plan queue — a table view/editor over one of two interchangeable
backends, selected by the ``queue_backend`` profile config key (see
:mod:`config`, the Configuration dialog's "Queue Backend" tab):

* ``"native"`` (the default): :class:`NativeQueuePanel`, a view over the
  persistent per-beamline queue in :mod:`queue_store`, driven by the
  detached :mod:`queue_runner`.
* ``"qs"``: :class:`QSQueuePanel`, a view over the Bluesky queueserver
  (QS)'s own queue, running against QS's own RE/device environment
  (typically redwood). See :mod:`qs_client`.

Both classes are a **view + editor**, not a scheduler — they poll their
respective backend so the table reflects reality even if this GUI wasn't the
one that queued/ran an item, and Start/Pause just flip the backend's own
running state rather than dispatching anything themselves. Only one is ever
constructed at a time, via :func:`create_queue_panel` — `main_window.py`
never imports either class directly, since their ``add()`` signatures
differ (native takes a plain command string; QS takes a structured item
dict, since a QS item's actual plan-argument values are needed, not just its
rendered source text).

Interactive Run/the embedded console kernel are untouched by either panel —
see :mod:`console_panel`/:mod:`main_window._on_run` for that separate path.
"""
from __future__ import annotations

import json

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

from . import command_builder
from . import config
from . import det_startup_state
from . import qs_client
from . import queue_sidecar
from . import queue_store
from . import style as S

# Per-row display status — shared by both backends (queue_store.py's own
# WAITING/RUNNING/DONE/ERROR constants carry the same string values; QS has
# no such module of its own, so this is these values' one home now).
WAITING, RUNNING, DONE, ERROR = "waiting", "running", "done", "error"


def _status_color() -> dict[str, str]:
    """Status → colour, read from the live theme (per request: DONE red,
    RUNNING green, WAITING orange; ERROR gets a distinct colour so failures
    stand out from completed runs)."""
    return {
        WAITING: S.STATUS_WAITING,
        RUNNING: S.STATUS_RUNNING,
        DONE:    S.STATUS_DONE,
        ERROR:   S.STATUS_ERROR,
    }


def _banner_qss(color: str) -> str:
    return f"font-weight:bold; font-size:{S.px(15)}px; color:{color};"


def _short(command: str, limit: int = 80) -> str:
    """One-line, truncated preview (the RE(...) line) of a command."""
    text = command.strip()
    line = text.splitlines()[-1] if text else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


class NativeQueuePanel(QtWidgets.QWidget):
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

        self._state_lbl = QtWidgets.QLabel("Idle")
        self._state_lbl.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self._state_lbl)

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
        self._toggle_btn = S.primary_btn("▶ Start")
        self._toggle_btn.setMinimumHeight(S.px(26))
        self._toggle_btn.clicked.connect(self._on_toggle)
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
        for w in (self._toggle_btn, up, down, delete, clear, self._copy_btn):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)

    # ── Public: add to queue ─────────────────────────────────────────────────────

    def add(self, command: str, notes: str = "", area_detectors: list | None = None) -> None:
        """Append a plan command to the persistent queue.

        `area_detectors` (device names bound to an area_detector-category
        param, if any) is stored so queue_runner.py can trigger the
        MIDAS_GUI live-view bridge when this item is dispatched.
        """
        queue_store.add(self._beamline(), command, notes, area_detectors=area_detectors or [])
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

    def _on_toggle(self) -> None:
        if queue_store.load(self._beamline()).get("state") == queue_store.S_RUNNING:
            self._pause()
        else:
            self._start()

    def _start(self) -> None:
        bl = self._beamline()
        data = queue_store.load(bl)
        if not any(it["status"] == WAITING for it in data["items"]):
            self._set_state_msg("Nothing queued", warn=True)
            return
        queue_store.set_state(bl, queue_store.S_RUNNING)
        if self._console is not None and not self._console.is_running():
            self._set_state_msg(
                "Armed — will run when a kernel is available", warn=True
            )
        self._refresh()

    def _pause(self) -> None:
        # Stops the runner dispatching the NEXT plan; the current one keeps going.
        queue_store.set_state(self._beamline(), queue_store.PAUSED)
        self._refresh()

    def _move(self, delta: int) -> None:
        item_id = self._selected_id()
        if item_id is not None:
            queue_store.move(self._beamline(), item_id, delta)
            self._refresh()

    def _delete(self) -> None:
        item_id = self._selected_id()
        if item_id is None:
            return
        queue_store.remove(self._beamline(), item_id)
        self._refresh()

    def _clear_finished(self) -> None:
        queue_store.clear_finished(self._beamline())
        self._refresh()

    def _copy_to_form(self) -> None:
        item_id = self._selected_id()
        if item_id is None:
            return
        data = queue_store.load(self._beamline())
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
            queue_store.rename(self._beamline(), item_id, item.text().strip())

    # ── Rendering (polled) ────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        # Don't fight the user mid-edit.
        if self._table.state() == QtWidgets.QAbstractItemView.EditingState:
            return
        data = queue_store.load(self._beamline())
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
                QtGui.QColor(_status_color().get(it["status"], S.TEXT))
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

        state = data.get("state")
        color, text = {
            queue_store.S_RUNNING: (S.STATUS_RUNNING, "● Running"),
            queue_store.PAUSED: (S.STATUS_WAITING, "❚❚ Paused"),
        }.get(state, (S.MUTED, "Idle"))
        self._state_lbl.setStyleSheet(_banner_qss(color))
        self._state_lbl.setText(text)
        self._toggle_btn.setText("⏸ Pause" if state == queue_store.S_RUNNING else "▶ Start")

    def _set_state_msg(self, text: str, *, warn: bool = False) -> None:
        self._state_lbl.setStyleSheet(_banner_qss(S.ERROR if warn else S.MUTED))
        self._state_lbl.setText(text)
        QtCore.QTimer.singleShot(
            3000, lambda: self._state_lbl.setStyleSheet(_banner_qss(S.MUTED))
        )


# QS manager_state values (bluesky_queueserver.manager.manager.MState) that
# mean "actively executing something right now." QSQueuePanel-only.
_QS_ACTIVE_STATES = {"executing_queue", "executing_task", "starting_queue"}


class QSQueuePanel(QtWidgets.QWidget):
    """Table view of the QS-backed plan queue."""

    # emitted with the selected item's command text when "Copy to form" is clicked
    copyToFormRequested = QtCore.pyqtSignal(str)

    def __init__(self, console=None, parent=None) -> None:
        """`console` is accepted for call-site compatibility with
        :class:`NativeQueuePanel` but is not used here — the QS-backed queue
        dispatches through the queue server, independent of the console
        kernel."""
        super().__init__(parent)
        self._console = console
        self._loading = False        # guard so programmatic edits don't re-trigger
        self._last_sig: str | None = None
        self._rows_cache: list[tuple[dict, str]] = []
        self._waiting_uids: list[str] = []
        self._running_uid: str | None = None
        self._seen_error_seq: int = 0
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

        self._state_lbl = QtWidgets.QLabel("Idle")
        self._state_lbl.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self._state_lbl)

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
            "Double-click a Name to rename (cosmetic only). Hover a row for "
            "the full command."
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
        self._toggle_btn = S.primary_btn("▶ Start")
        self._toggle_btn.setMinimumHeight(S.px(26))
        self._toggle_btn.clicked.connect(self._on_toggle)
        up = QtWidgets.QPushButton("▲")
        up.setToolTip("Move selected plan up")
        up.clicked.connect(lambda: self._move(-1))
        down = QtWidgets.QPushButton("▼")
        down.setToolTip("Move selected plan down")
        down.clicked.connect(lambda: self._move(1))
        delete = QtWidgets.QPushButton("Delete")
        delete.clicked.connect(self._delete)
        clear = QtWidgets.QPushButton("Clear finished")
        clear.setToolTip("Clear the queue server's history log of finished plans.")
        clear.clicked.connect(self._clear_finished)
        self._copy_btn = QtWidgets.QPushButton("Copy to form")
        self._copy_btn.setToolTip(
            "Load the selected plan's command back into the main panel's form "
            "so you can tweak and resubmit it."
        )
        self._copy_btn.clicked.connect(self._copy_to_form)
        for w in (self._toggle_btn, up, down, delete, clear, self._copy_btn):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)

    # ── Public: add to queue ─────────────────────────────────────────────────────

    def add(self, item: dict) -> None:
        """Append a fully-formed QS item dict (see
        :func:`B_PILOT.command_builder.make_queue_item`, and
        ``main_window._on_queue`` for ``meta``/``det_startup``-item assembly)
        to the queue server's queue."""
        qs_client.item_add(item)
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

    def _item_by_uid(self, uid: str) -> dict | None:
        for it, _st in self._rows_cache:
            if it.get("item_uid") == uid:
                return it
        return None

    # ── Controls ──────────────────────────────────────────────────────────────────

    def _is_active(self, status: dict) -> bool:
        return status.get("manager_state") in _QS_ACTIVE_STATES

    def _on_toggle(self) -> None:
        status = qs_client.status() or {}
        if self._is_active(status) and not status.get("queue_stop_pending"):
            self._pause()
        else:
            self._start()

    def _start(self) -> None:
        # qs_client's action calls are fire-and-forget (see its module
        # docstring) -- their effect shows up in the next poll tick, same
        # eventual-consistency model this panel already uses everywhere
        # else, so there's no synchronous success/failure to report here.
        status = qs_client.status() or {}
        if status.get("items_in_queue", 0) == 0 and not status.get("running_item_uid"):
            self._set_state_msg("Nothing queued", warn=True)
            return
        if not status.get("worker_environment_exists"):
            qs_client.environment_open()
            # A fresh QS environment means a fresh RE-Worker process: old
            # det_startup bookkeeping and console text no longer apply.
            det_startup_state.clear_qs(self._beamline())
            qs_client.console_clear()
            self._set_state_msg("Armed — opening a QS environment…", warn=True)
        qs_client.queue_start()
        self._refresh()

    def _pause(self) -> None:
        # Stops QS dispatching the NEXT plan; the current one keeps going.
        qs_client.queue_stop()
        self._refresh()

    def _move(self, delta: int) -> None:
        uid = self._selected_id()
        if uid is None or uid not in self._waiting_uids:
            return
        idx = self._waiting_uids.index(uid)
        j = idx + delta
        if j < 0 or j >= len(self._waiting_uids):
            return
        target = self._waiting_uids[j]
        if delta < 0:
            qs_client.item_move(uid, before_uid=target)
        else:
            qs_client.item_move(uid, after_uid=target)
        self._refresh()

    def _delete(self) -> None:
        uid = self._selected_id()
        if uid is None or uid == self._running_uid:
            return
        qs_client.item_remove(uid)
        self._refresh()

    def _clear_finished(self) -> None:
        qs_client.history_clear()
        self._refresh()

    def _copy_to_form(self) -> None:
        uid = self._selected_id()
        if uid is None:
            return
        item = self._item_by_uid(uid)
        if item is not None:
            notes = (item.get("meta") or {}).get("notes", "")
            text = command_builder.display_command_from_item(item, notes=notes)
            self.copyToFormRequested.emit(text)

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._loading or item.column() != 1:
            return
        id_cell = self._table.item(item.row(), 0)
        if id_cell is None:
            return
        uid = id_cell.data(QtCore.Qt.UserRole)
        if uid:
            queue_sidecar.set_display_name(self._beamline(), uid, item.text().strip())

    # ── Rendering (polled) ────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        # Don't fight the user mid-edit.
        if self._table.state() == QtWidgets.QAbstractItemView.EditingState:
            return

        # Surface a new action failure (a rejected/undeliverable item_add,
        # queue_start, etc.) every tick, independent of the row-signature
        # dedup below -- a rejected add against an unchanged (still empty)
        # queue would otherwise never re-trigger that block, and the
        # failure would never be shown at all.
        err = qs_client.last_action_error()
        if err is not None and err[0] != self._seen_error_seq:
            self._seen_error_seq = err[0]
            self._set_state_msg(f"Queue server error: {err[2]}", warn=True)

        # Likewise: a lost/never-established connection to QS must be shown
        # even though status/queue/history all stay at the same empty `{}`
        # forever, which would otherwise dedup away after the first tick and
        # leave a stale "Idle" label -- indistinguishable from a real,
        # connected, empty queue (the exact confusion that made "added to
        # queue, but nothing shows up" a mystery instead of an obvious
        # connection problem).
        if not qs_client.connected():
            if self._table.rowCount():
                self._table.setRowCount(0)
            self._rows_cache = []
            self._waiting_uids = []
            self._running_uid = None
            self._last_sig = None  # force a real rebuild once reconnected
            self._state_lbl.setStyleSheet(_banner_qss(S.ERROR))
            reason = qs_client.last_connect_error()
            text = "⚠ Not connected to queue server"
            if reason:
                text += f": {reason}"
            self._state_lbl.setText(text)
            self._toggle_btn.setText("▶ Start")
            return

        bl = self._beamline()
        status = qs_client.status() or {}
        qdata = qs_client.queue_get() or {}
        hdata = qs_client.history_get() or {}

        running_item = qdata.get("running_item") or {}
        waiting_items = qdata.get("items") or []
        history_items = hdata.get("items") or []

        rows: list[tuple[dict, str]] = []
        if running_item.get("item_uid"):
            rows.append((running_item, RUNNING))
        rows.extend((it, WAITING) for it in waiting_items)
        for it in history_items:
            exit_status = (it.get("result") or {}).get("exit_status")
            rows.append((it, DONE if exit_status == "completed" else ERROR))

        self._rows_cache = rows
        self._waiting_uids = [it.get("item_uid") for it in waiting_items]
        self._running_uid = running_item.get("item_uid")

        live_uids = {it.get("item_uid") for it, _st in rows if it.get("item_uid")}
        queue_sidecar.prune(bl, live_uids)

        sig = json.dumps(
            [
                status.get("manager_state"),
                status.get("queue_stop_pending"),
                [
                    (it.get("item_uid"), st, it.get("name"), it.get("args"), it.get("kwargs"))
                    for it, st in rows
                ],
            ],
            default=str,
        )
        if sig == self._last_sig:
            return
        self._last_sig = sig

        keep_id = self._selected_id()
        self._loading = True
        self._table.setRowCount(len(rows))
        for r, (it, st) in enumerate(rows):
            uid = it.get("item_uid", "") or ""
            notes = (it.get("meta") or {}).get("notes", "")
            command = command_builder.display_command_from_item(it, notes=notes)
            name = queue_sidecar.get_display_name(bl, uid, it.get("name", ""))
            tip = command + (f"\n\nnotes: {notes}" if notes else "")

            num = QtWidgets.QTableWidgetItem(str(r + 1))
            num.setData(QtCore.Qt.UserRole, uid)
            num.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setFlags(
                QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                | QtCore.Qt.ItemIsEditable
            )

            status_item = QtWidgets.QTableWidgetItem(st.upper())
            status_item.setForeground(
                QtGui.QColor(_status_color().get(st, S.TEXT))
            )
            status_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            cmd = QtWidgets.QTableWidgetItem(_short(command))
            cmd.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            for cell in (num, name_item, status_item, cmd):
                cell.setToolTip(tip)
            self._table.setItem(r, 0, num)
            self._table.setItem(r, 1, name_item)
            self._table.setItem(r, 2, status_item)
            self._table.setItem(r, 3, cmd)
        self._loading = False

        if keep_id is not None:
            for r in range(self._table.rowCount()):
                cell = self._table.item(r, 0)
                if cell is not None and cell.data(QtCore.Qt.UserRole) == keep_id:
                    self._table.setCurrentCell(r, 0)
                    break

        manager_state = status.get("manager_state")
        if self._is_active(status):
            color, text = S.STATUS_RUNNING, "● Running"
        elif manager_state == "paused" or status.get("queue_stop_pending"):
            color, text = S.STATUS_WAITING, "❚❚ Paused"
        else:
            color, text = S.MUTED, "Idle"
        self._state_lbl.setStyleSheet(_banner_qss(color))
        self._state_lbl.setText(text)
        self._toggle_btn.setText("⏸ Pause" if self._is_active(status) else "▶ Start")

    def _set_state_msg(self, text: str, *, warn: bool = False) -> None:
        self._state_lbl.setStyleSheet(_banner_qss(S.ERROR if warn else S.MUTED))
        self._state_lbl.setText(text)
        QtCore.QTimer.singleShot(
            3000, lambda: self._state_lbl.setStyleSheet(_banner_qss(S.MUTED))
        )


def create_queue_panel(console=None, parent=None) -> QtWidgets.QWidget:
    """Construct whichever queue panel matches the active ``queue_backend``
    profile setting ("native", the default, or "qs") — the one place that
    decides which backend is live, so nothing else has to branch on it to
    build the widget. Selecting "qs" is what causes any QS connection to be
    attempted at all; "native" never touches :mod:`qs_client`."""
    if config.get("queue_backend") == "qs":
        return QSQueuePanel(console, parent)
    return NativeQueuePanel(console, parent)
