"""Modal dialog gating "Launch IPython": confirm the DM experiment and setup
file before a kernel starts, so the experiment banner shown once it's running
(see :mod:`main_window`) is never a guess.
"""
from __future__ import annotations

import os

from PyQt5 import QtWidgets

from . import config

_DEFAULT_SETUP_FILE = "exp_setup.yml"


def _experiment_dir(experiment: str) -> str:
    """Path where ``instrument/session_logs.py`` expects this experiment's
    data/log folder — created by the beamline's data-management setup, never
    by B-PILOT."""
    return os.path.join(os.path.expanduser("~"), "new_data", experiment)


class LaunchDialog(QtWidgets.QDialog):
    """Ask for the DM experiment name + setup file before starting a kernel.

    Pre-fills from the last-used values in config. Experiment cannot be left
    blank — it drives the beamline account's data/log paths (see
    ``instrument/devices/global_variables.py``) and is what the console
    banner displays once the kernel is up, so a silent stale value is a real
    risk on the beamline.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Launch IPython")
        self.setModal(True)

        lay = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            "Confirm the experiment for this session. This name is recorded "
            "to dm_experiment.txt and determines where Bluesky reads/writes "
            "data and session logs."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        form = QtWidgets.QFormLayout()
        self._experiment = QtWidgets.QLineEdit(config.get("dm_experiment") or "")
        self._experiment.setToolTip(
            "DM experiment name. Recorded to user_defaults/dm_experiment.txt "
            "by the embedded starter script."
        )
        form.addRow("Experiment:", self._experiment)

        self._setup_file = QtWidgets.QLineEdit(
            config.get("setup_file") or _DEFAULT_SETUP_FILE
        )
        self._setup_file.setToolTip(f"Setup YAML (default {_DEFAULT_SETUP_FILE}).")
        form.addRow("Setup file:", self._setup_file)
        lay.addLayout(form)

        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)

        self._experiment.textChanged.connect(self._update_ok_enabled)
        self._update_ok_enabled()
        self._experiment.setFocus()
        self._experiment.selectAll()

    def _update_ok_enabled(self) -> None:
        ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        ok_btn.setEnabled(bool(self._experiment.text().strip()))

    def accept(self) -> None:
        experiment = self.experiment()
        exp_dir = _experiment_dir(experiment)
        if experiment and not os.path.isdir(exp_dir):
            ans = QtWidgets.QMessageBox.warning(
                self,
                "Experiment directory not found",
                f"No directory found for experiment {experiment!r}:\n\n{exp_dir}\n\n"
                "This folder is created by the beamline's data-management setup, "
                "not by B-PILOT — double-check the experiment name.\n\n"
                "Launch anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if ans != QtWidgets.QMessageBox.Yes:
                return
        super().accept()

    def experiment(self) -> str:
        return self._experiment.text().strip()

    def setup_file(self) -> str:
        return self._setup_file.text().strip() or _DEFAULT_SETUP_FILE
