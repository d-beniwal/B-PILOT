"""Configuration dialog: edit the visible plan files and the launch commands.

Reachable from the main window's **Python → Configuration…** menu.  Edits are
written through :mod:`config` on *Save*; the caller then refreshes the panels so
changes take effect immediately (no restart).
"""
from __future__ import annotations

import os

from PyQt5 import QtWidgets

from . import config
from . import plan_parser as P
from . import style as S


class ConfigDialog(QtWidgets.QDialog):
    """Modal dialog for the Files/visibility (search scope) + Launch (startup) settings."""

    def __init__(self, parent=None) -> None:
        """Build the form pre-filled from the current :mod:`config` values."""
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self.setMinimumWidth(600)

        cfg = config.as_dict()

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        outer.addWidget(self._build_files_card(cfg))
        outer.addWidget(self._build_visibility_card(cfg))
        outer.addWidget(self._build_launch_card(cfg))
        outer.addWidget(self._build_session_card(cfg))
        outer.addStretch(1)
        outer.addWidget(self._build_buttons())

    # ── Files (plan search scope) ────────────────────────────────────────────────

    def _build_files_card(self, cfg: dict) -> QtWidgets.QWidget:
        card = S.make_card("Files  (which plans the runner shows)")
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        # Plans directory (scanned for .py files)
        self._plans_dir = QtWidgets.QLineEdit(cfg["plans_dir"])
        self._plans_dir.setToolTip(
            "Folder scanned for plan .py files (top level + one subfolder deep)."
        )
        grid.addWidget(S.LabelRight("Plans directory:"), 0, 0)
        grid.addWidget(self._plans_dir, 0, 1)
        grid.addWidget(self._browse_button(self._plans_dir), 0, 2)

        # Import root (module resolution for the generated import line)
        self._import_root = QtWidgets.QLineEdit(cfg["import_root"])
        self._import_root.setToolTip(
            "Root the 'from <module> import <plan>' line is resolved against.\n"
            "The module = plan file path relative to this root.\n"
            "e.g. root=mpe_bluesky/ -> instrument/plans/foo.py -> "
            "instrument.plans.foo"
        )
        grid.addWidget(S.LabelRight("Import root:"), 1, 0)
        grid.addWidget(self._import_root, 1, 1)
        grid.addWidget(self._browse_button(self._import_root), 1, 2)

        # Default plan file (checked on startup)
        self._default_file = QtWidgets.QLineEdit(cfg["default_plan_file"])
        self._default_file.setToolTip(
            "File in the plans directory checked (shown) by default on startup."
        )
        grid.addWidget(S.LabelRight("Default plan file:"), 2, 0)
        grid.addWidget(self._default_file, 2, 1)

        card.body.addLayout(grid)
        return card

    # ── Plan visibility (which files show as rows in the file browser) ──────────

    def _build_visibility_card(self, cfg: dict) -> QtWidgets.QWidget:
        card = S.make_card("Plan visibility  (which files appear in the User files panel)")

        self._visibility_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._visible_files_initial = set(cfg.get("visible_plan_files") or [])

        btn_row = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("Select all")
        select_all_btn.clicked.connect(lambda: self._set_all_visibility(True))
        deselect_all_btn = QtWidgets.QPushButton("Deselect all")
        deselect_all_btn.clicked.connect(lambda: self._set_all_visibility(False))
        refresh_btn = QtWidgets.QPushButton("Refresh list")
        refresh_btn.setToolTip(
            "Re-scan the Plans directory above (picks up files added/removed "
            "on disk, or an edited Plans directory field)."
        )
        refresh_btn.clicked.connect(self._rebuild_visibility_list)
        btn_row.addWidget(select_all_btn)
        btn_row.addWidget(deselect_all_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(refresh_btn)
        card.body.addLayout(btn_row)

        self._visibility_container = QtWidgets.QWidget()
        self._visibility_layout = QtWidgets.QVBoxLayout(self._visibility_container)
        self._visibility_layout.setContentsMargins(2, 2, 2, 2)
        self._visibility_layout.setSpacing(2)
        vis_scroll = QtWidgets.QScrollArea()
        vis_scroll.setWidgetResizable(True)
        vis_scroll.setWidget(self._visibility_container)
        vis_scroll.setMinimumHeight(160)
        card.body.addWidget(vis_scroll)

        self._rebuild_visibility_list()
        # Re-scan automatically when the Plans directory field is edited.
        self._plans_dir.editingFinished.connect(self._rebuild_visibility_list)

        return card

    def _rebuild_visibility_list(self) -> None:
        """Re-scan the (possibly just-edited) Plans directory and rebuild the list.

        Preserves already-toggled checkbox states across a rescan, same pattern
        as :meth:`PlanRunnerPanel._populate_file_browser`.
        """
        plans_dir = self._plans_dir.text().strip()
        old = {rel: cb.isChecked() for rel, cb in self._visibility_checks.items()}

        while self._visibility_layout.count():
            item = self._visibility_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._visibility_checks.clear()

        for display_name, kind, abs_path, depth in P.scan_user_dir(plans_dir):
            if kind == "dir":
                lbl = QtWidgets.QLabel(f"📁 {display_name}")
                lbl.setStyleSheet(f"color: {S.MUTED};")
                self._visibility_layout.addWidget(lbl)
                continue
            rel = os.path.relpath(abs_path, plans_dir).replace(os.sep, "/")
            cb = QtWidgets.QCheckBox(display_name)
            if depth:
                cb.setStyleSheet(f"margin-left: {16 * depth}px;")
            cb.setChecked(old.get(rel, rel in self._visible_files_initial))
            self._visibility_checks[rel] = cb
            self._visibility_layout.addWidget(cb)
        self._visibility_layout.addStretch(1)

    def _set_all_visibility(self, checked: bool) -> None:
        for cb in self._visibility_checks.values():
            cb.setChecked(checked)

    # ── Launch Bluesky command(s) ────────────────────────────────────────────────

    def _build_launch_card(self, cfg: dict) -> QtWidgets.QWidget:
        card = S.make_card("Launch  (Load Bluesky command)")
        card.body.addWidget(
            QtWidgets.QLabel(
                "Run in the console when you click “Load Bluesky” "
                "(one command per line — CONNECTS TO HARDWARE on a beamline):"
            )
        )
        self._startup = QtWidgets.QPlainTextEdit(cfg["bluesky_startup"])
        self._startup.setObjectName("mono")
        self._startup.setFixedHeight(90)
        self._startup.setToolTip(
            "MPE console startup is 'from instrument.collection import *' "
            "(account-gated).  Queueserver uses 'from instrument.queueserver "
            "import *'."
        )
        card.body.addWidget(self._startup)

        self._keep_kernel = QtWidgets.QCheckBox(
            "Keep the IPython kernel running when the GUI closes "
            "(so it can be reattached)"
        )
        self._keep_kernel.setChecked(bool(cfg["keep_kernel_on_exit"]))
        self._keep_kernel.setToolTip(
            "On: closing the GUI leaves the kernel (and any running plan) alive; "
            "relaunch and use Console → Attach to reconnect.\n"
            "Off: the kernel is shut down when the GUI closes."
        )
        card.body.addWidget(self._keep_kernel)
        return card

    # ── Session (single kernel per beamline) ─────────────────────────────────────

    def _build_session_card(self, cfg: dict) -> QtWidgets.QWidget:
        card = S.make_card("Session  (one kernel per beamline)")
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)

        self._beamline = QtWidgets.QLineEdit(cfg["beamline"])
        self._beamline.setToolTip(
            "Identifies the single interactive kernel for this beamline "
            "(screen session name + fixed connection-file path)."
        )
        grid.addWidget(S.LabelRight("Beamline id:"), 0, 0)
        grid.addWidget(self._beamline, 0, 1)

        card.body.addLayout(grid)

        self._use_screen = QtWidgets.QCheckBox(
            "Host the kernel in a named 'screen' session (recommended)"
        )
        self._use_screen.setChecked(bool(cfg["use_screen"]))
        self._use_screen.setToolTip(
            "On: the kernel runs inside 'screen bluesky-kernel-<beamline>' so it "
            "survives the GUI and staff can attach a terminal with\n"
            "  screen -r bluesky-kernel-<beamline>\n"
            "Off: the kernel is launched as a plain detached process."
        )
        card.body.addWidget(self._use_screen)

        # External launcher (used when Launch mode = "Launch script").
        srow = QtWidgets.QGridLayout()
        srow.setColumnStretch(1, 1)
        self._launch_script = QtWidgets.QLineEdit(cfg["launch_script"])
        self._launch_script.setToolTip(
            "Shell script run by Launch when the toolbar Launch mode is set to "
            "'Launch script' (e.g. blueskyStarter.sh). Called as:\n"
            "  <script> <dm_experiment> <setup_file> <mode>"
        )
        srow.addWidget(S.LabelRight("Launch script:"), 0, 0)
        srow.addWidget(self._launch_script, 0, 1)
        srow.addWidget(self._browse_button(self._launch_script, kind="file"), 0, 2)

        self._embedded_starter = QtWidgets.QLineEdit(cfg["embedded_starter_script"])
        self._embedded_starter.setToolTip(
            "Script run for the EMBEDDED kernel launch — activates the env + "
            "records the experiment (like blueskyStarter.sh) but starts a "
            "connectable ipykernel. Called as:\n"
            "  <script> <dm_experiment> <setup_file> <connection_file> <screen>\n"
            "Leave blank to launch a bare kernel with no env activation."
        )
        srow.addWidget(S.LabelRight("Embedded starter:"), 1, 0)
        srow.addWidget(self._embedded_starter, 1, 1)
        srow.addWidget(self._browse_button(self._embedded_starter, kind="file"), 1, 2)

        card.body.addLayout(srow)
        return card

    # ── Buttons ──────────────────────────────────────────────────────────────────

    def _build_buttons(self) -> QtWidgets.QWidget:
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.RestoreDefaults
        )
        box.button(QtWidgets.QDialogButtonBox.Save).setObjectName("primary")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        box.button(QtWidgets.QDialogButtonBox.RestoreDefaults).clicked.connect(
            self._restore_defaults
        )
        return box

    # ── Actions ──────────────────────────────────────────────────────────────────

    def _browse_button(self, target: QtWidgets.QLineEdit, kind: str = "dir"):
        btn = QtWidgets.QPushButton("Browse…")
        btn.clicked.connect(lambda: self._browse_into(target, kind))
        return btn

    def _browse_into(self, target: QtWidgets.QLineEdit, kind: str = "dir") -> None:
        start = target.text().strip() or os.path.expanduser("~")
        base = start if os.path.isdir(start) else os.path.dirname(start)
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        if kind == "file":
            chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Select launch script", base
            )
        else:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select directory", base
            )
        if chosen:
            target.setText(chosen)

    def _restore_defaults(self) -> None:
        d = config.DEFAULTS
        self._plans_dir.setText(d["plans_dir"])
        self._import_root.setText(d["import_root"])
        self._default_file.setText(d["default_plan_file"])
        self._visible_files_initial = set(d["visible_plan_files"])
        self._visibility_checks.clear()
        self._rebuild_visibility_list()
        self._startup.setPlainText(d["bluesky_startup"])
        self._keep_kernel.setChecked(bool(d["keep_kernel_on_exit"]))
        self._beamline.setText(d["beamline"])
        self._use_screen.setChecked(bool(d["use_screen"]))
        self._launch_script.setText(d["launch_script"])
        self._embedded_starter.setText(d["embedded_starter_script"])

    def values(self) -> dict:
        """Return the edited settings as a config dict."""
        return {
            "plans_dir": self._plans_dir.text().strip(),
            "import_root": self._import_root.text().strip(),
            "default_plan_file": self._default_file.text().strip(),
            "visible_plan_files": sorted(
                rel for rel, cb in self._visibility_checks.items() if cb.isChecked()
            ),
            "bluesky_startup": self._startup.toPlainText().strip(),
            "keep_kernel_on_exit": self._keep_kernel.isChecked(),
            "beamline": self._beamline.text().strip(),
            "use_screen": self._use_screen.isChecked(),
            "launch_script": self._launch_script.text().strip(),
            "embedded_starter_script": self._embedded_starter.text().strip(),
        }

    def accept(self) -> None:  # noqa: D102
        config.update(self.values())
        super().accept()
