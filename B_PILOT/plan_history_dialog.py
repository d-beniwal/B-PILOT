"""Modal dialog: browse this beamline's persistent experiment history and
import a previously-run plan straight back into the plan-form panel.

Read-only over :mod:`experiment_history` — never mutates anything. The actual
import (turning a saved command string back into form fields) is handled by
:meth:`plan_runner.PlanRunnerPanel.load_from_command`, already exercised by
the queue panel's "Copy to form" action — this dialog only has to find and
preview the command text.
"""
from __future__ import annotations

import time

from PyQt5 import QtCore
from PyQt5 import QtWidgets

from . import config
from . import experiment_history as eh
from . import style as S


def _fmt_ts(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "?"


class PlanHistoryDialog(QtWidgets.QDialog):
    """Pick an experiment, then a previously-run plan, preview it, and import."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import plan from experiment history")
        self.resize(S.px(860), S.px(560))
        self._all_runs: list[dict] = []   # every run for the selected experiment
        self._runs: list[dict] = []       # `_all_runs` narrowed by the filter box

        outer = QtWidgets.QVBoxLayout(self)

        split = S.Splitter(QtCore.Qt.Horizontal)
        S.configure_splitter(split)
        outer.addWidget(split, 1)

        # ── Left: experiments ──
        exp_card = S.make_card("Experiments (most recent activity first)")
        self._exp_list = QtWidgets.QListWidget()
        self._exp_list.currentItemChanged.connect(self._on_experiment_selected)
        exp_card.body.addWidget(self._exp_list, 1)
        split.addWidget(exp_card)

        # ── Right: filter + plan runs + preview ──
        right = QtWidgets.QWidget()
        rlay = QtWidgets.QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)

        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText("Filter by plan name…")
        self._filter.textChanged.connect(self._apply_filter)
        rlay.addWidget(self._filter)

        vsplit = S.Splitter(QtCore.Qt.Vertical)
        S.configure_splitter(vsplit)

        runs_card = S.make_card("Plans run (most recent first)")
        self._runs_list = QtWidgets.QListWidget()
        self._runs_list.currentRowChanged.connect(self._on_run_selected)
        runs_card.body.addWidget(self._runs_list, 1)
        vsplit.addWidget(runs_card)

        preview_card = S.make_card("Preview")
        self._preview = QtWidgets.QPlainTextEdit()
        self._preview.setObjectName("mono")
        self._preview.setReadOnly(True)
        preview_card.body.addWidget(self._preview, 1)
        vsplit.addWidget(preview_card)
        vsplit.setStretchFactor(0, 1)
        vsplit.setStretchFactor(1, 1)
        rlay.addWidget(vsplit, 1)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([S.px(220), S.px(600)])

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self._import_btn = S.primary_btn("Import into form")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._import_btn)
        outer.addLayout(btn_row)

        self._populate_experiments()

    # ── Population ────────────────────────────────────────────────────────────

    def _populate_experiments(self) -> None:
        beamline = config.get("beamline")
        for exp in eh.list_experiments(beamline):
            item = QtWidgets.QListWidgetItem(exp["name"])
            item.setData(QtCore.Qt.UserRole, exp["name"])
            if exp.get("last_activity"):
                item.setToolTip(f"Last activity: {_fmt_ts(exp['last_activity'])}")
            self._exp_list.addItem(item)
        if self._exp_list.count():
            self._exp_list.setCurrentRow(0)

    def _on_experiment_selected(self, current, _previous) -> None:
        if current is None:
            self._all_runs = []
        else:
            beamline = config.get("beamline")
            experiment = current.data(QtCore.Qt.UserRole)
            entries = eh.read_entries(beamline, experiment)
            self._all_runs = eh.extract_plan_runs(entries)
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._filter.text().strip().lower()
        self._runs = (
            [r for r in self._all_runs if query in r["plan_name"].lower()]
            if query
            else list(self._all_runs)
        )
        self._runs_list.clear()
        for run in self._runs:
            self._runs_list.addItem(f"{_fmt_ts(run['ts'])}   {run['plan_name']}")
        if self._runs_list.count():
            self._runs_list.setCurrentRow(0)
        else:
            self._on_run_selected(-1)

    def _on_run_selected(self, row: int) -> None:
        if 0 <= row < len(self._runs):
            self._preview.setPlainText(self._runs[row]["command"])
            self._import_btn.setEnabled(True)
        else:
            self._preview.setPlainText("")
            self._import_btn.setEnabled(False)

    # ── Public result ────────────────────────────────────────────────────────

    def selected_command(self) -> str | None:
        """The chosen run's raw command text, or ``None`` if nothing is selected."""
        row = self._runs_list.currentRow()
        if 0 <= row < len(self._runs):
            return self._runs[row]["command"]
        return None
