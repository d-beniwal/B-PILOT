"""Mode-switch status button + floating shortcut-picker popup.

Replaces the old inline-expanding ``SwitchToPanel`` card. :class:`SwitchToButton`
is a persistent 2-line status button ("Mode:\\n<short name>") living in the
status bar above the console; its label only updates once a ``switch_to_*``
shortcut actually **runs and succeeds** in the kernel (via
``ConsolePanel.code_executed``, which only fires on a clean, non-error
completion), never merely when it is queued and never when the command
raises. Clicking it opens :class:`SwitchToPopup`, a
``Qt.Popup`` single-row picker — shortcut dropdown, per-shortcut param fields
(:func:`gui_qt.param_form.build_row`), Notes, and Add-to-Queue/Run-in-console
buttons, any of which closes the popup. Discovery (``switch_to_*`` plans found
via ``config.get("switch_to_search_paths")``) and command generation are
carried over unchanged from the old panel.
"""
from __future__ import annotations

import os
import re

from PyQt5 import QtCore
from PyQt5 import QtWidgets

from . import command_builder
from . import config
from . import midas_bridge
from . import param_form
from . import paths as _paths
from . import plan_parser as P
from . import style as S
from .skeleton_widgets import MotorAxisPicker

_MODE_RE = re.compile(r"\bswitch_to_(\w+)\s*\(")


def _resolve_path(path: str) -> str:
    """Resolve a (possibly project-relative) search path to an absolute one."""
    return path if os.path.isabs(path) else os.path.join(_paths.PROJECT_ROOT, path)


def discover_shortcuts() -> dict[str, tuple[dict, str]]:
    """Scan the active profile's `switch_to_search_paths` for `switch_to_*`
    plans. Returns ``{name: (spec_dict, module)}``. Cheap (a handful of small
    files) — safe to call every time the popup opens so a profile switch is
    picked up live."""
    shortcuts: dict[str, tuple[dict, str]] = {}
    import_root = config.get("import_root")
    for rel_path in config.get("switch_to_search_paths") or []:
        abs_path = _resolve_path(rel_path)
        module = P.file_to_module(abs_path, import_root)
        for name, spec in P.find_plan_specs(abs_path).items():
            if not name.startswith("switch_to_"):
                continue
            if name not in shortcuts:
                shortcuts[name] = (spec, module)
    return shortcuts


def _button_qss(bg: str) -> str:
    border = S.darken(bg, 130)
    hover = S.lighten(bg, 112)
    pressed = S.darken(bg, 112)
    return (
        f"QPushButton{{background:{bg};color:white;font-weight:bold;"
        f"border:{S.px(1)}px solid {border};border-radius:{S.px(6)}px;"
        f"padding:{S.px(5)}px {S.px(12)}px;}}"
        f"QPushButton:hover{{background:{hover};border-color:{border};}}"
        f"QPushButton:pressed{{background:{pressed};}}"
        f"QPushButton:disabled{{background:{S.BUTTON_DISABLED_BG};"
        f"color:{S.DISABLED_TEXT};border-color:{S.BUTTON_DISABLED_BORDER};}}"
    )


class SwitchToButton(QtWidgets.QPushButton):
    """Persistent 2-line "Mode:\\n<name>" status button; opens the shortcut popup.

    The label reflects the last `switch_to_*` shortcut that actually
    completed *successfully* in the console — see :meth:`note_code_ran`,
    wired by `MainWindow` to :attr:`ConsolePanel.code_executed`, which only
    fires once a cell finishes without raising. Never updates for a shortcut
    merely added to the queue, nor one that ran and errored.
    """

    # bubbled up from the popup, same signature MainWindow already wires
    # PlanRunnerPanel/the old SwitchToPanel to.
    runRequested = QtCore.pyqtSignal(str, str)
    queueRequested = QtCore.pyqtSignal(str, str)

    def __init__(self, console, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._console = console
        self.setText("Mode:\n—")
        self.setStyleSheet(_button_qss(S.MUTED))
        self.setToolTip("Click to switch beamline mode (switch_to_* shortcuts).")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.clicked.connect(self._open_popup)
        self.setEnabled(False)
        self._last_area_detectors: list = []

    def set_console_ready(self, ready: bool) -> None:
        self.setEnabled(ready)
        if not ready:
            self.setText("Mode:\n—")

    def note_code_ran(self, source: str) -> None:
        """Update the label once a `switch_to_*(...)` call actually ran."""
        m = _MODE_RE.search(source)
        if m:
            self.setText(f"Mode:\n{m.group(1)}")

    def last_dispatch_area_detector_devices(self) -> list:
        """area_detector device name(s) bound in the command last produced by
        the popup -- mirrors PlanRunnerPanel's own accessor of the same name,
        so `MainWindow` can treat both senders uniformly."""
        return self._last_area_detectors

    def _open_popup(self) -> None:
        popup = SwitchToPopup(self._console, parent=self)
        popup.runRequested.connect(lambda cmd, notes, p=popup: self._forward(p, self.runRequested, cmd, notes))
        popup.queueRequested.connect(lambda cmd, notes, p=popup: self._forward(p, self.queueRequested, cmd, notes))
        popup.move(S.clamp_popup_to_window(self, popup))
        popup.show()

    def _forward(self, popup: "SwitchToPopup", signal, command: str, notes: str) -> None:
        # Stash the popup's detector list before it closes (and is destroyed)
        # so MainWindow can read it off `self` once this signal reaches it.
        self._last_area_detectors = popup.last_dispatch_area_detector_devices()
        signal.emit(command, notes)


class SwitchToPopup(QtWidgets.QFrame):
    """Floating single-row shortcut picker.

    ``Qt.Popup`` auto-closes on an outside click or focus loss; Run/Queue
    also close it explicitly, satisfying "any action closes the menu".
    """

    runRequested = QtCore.pyqtSignal(str, str)
    queueRequested = QtCore.pyqtSignal(str, str)

    def __init__(self, console, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent, QtCore.Qt.Popup)
        self._console = console
        self._shortcuts = discover_shortcuts()
        self._current_params: list = []
        self._param_widgets: dict = {}
        self._last_area_detectors: list = []
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setStyleSheet(
            f"QFrame{{background:{S.PANEL};border:1px solid {S.BORDER};"
            f"border-radius:4px;}}"
        )
        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self._shortcut_cb = S.NoScrollComboBox()
        names = sorted(self._shortcuts)
        self._shortcut_cb.addItems(names)
        self._shortcut_cb.currentTextChanged.connect(self._on_shortcut_change)
        row.addWidget(self._shortcut_cb)

        self._param_row = QtWidgets.QHBoxLayout()
        self._param_row.setSpacing(8)
        row.addLayout(self._param_row)
        row.addStretch(1)
        outer.addLayout(row)

        self._notes = QtWidgets.QLineEdit()
        self._notes.setPlaceholderText("Notes (attached to this run, then cleared)…")
        outer.addWidget(self._notes)

        btn_row = QtWidgets.QHBoxLayout()
        self._status_lbl = QtWidgets.QLabel("")
        self._status_lbl.setStyleSheet(f"color: {S.MUTED};")
        btn_row.addWidget(self._status_lbl)
        btn_row.addStretch(1)
        self._add_btn = QtWidgets.QPushButton("Add to Queue")
        self._add_btn.clicked.connect(self._queue_command)
        self._run_btn = S.primary_btn("▶  Run in console")
        self._run_btn.clicked.connect(self._run_command)
        self._run_btn.setToolTip("Launch the IPython session first (top toolbar).")
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._run_btn)
        outer.addLayout(btn_row)

        if not names:
            self._status_lbl.setText("No switch_to_* shortcuts found for this beamline.")
            self._run_btn.setEnabled(False)
            self._add_btn.setEnabled(False)
        else:
            self._on_shortcut_change(names[0])

    # ── Parameter row ─────────────────────────────────────────────────────────

    def _on_shortcut_change(self, name: str) -> None:
        param_form.clear_layout(self._param_row)
        entry = self._shortcuts.get(name)
        self._current_params = entry[0]["params"] if entry else []
        self._param_widgets = param_form.build_row(
            self._param_row, self._current_params, self._live_validate
        )
        self._live_validate()

    # ── Validation ────────────────────────────────────────────────────────────

    def _field_errors(self) -> list[str]:
        errors: list[str] = []
        for spec in self._current_params:
            widget = self._param_widgets[spec.name][1]
            err = param_form.field_error(spec, widget)
            if isinstance(widget, MotorAxisPicker):
                bad = err is not None
                S.mark_invalid(widget.motor_cb, bad and not widget.motor())
                S.mark_invalid(widget.axis_cb, bad and bool(widget.motor()))
            elif spec.dtype not in ("bool", "device_list"):
                S.mark_invalid(widget, err is not None)
            if err:
                errors.append(err)
        return errors

    def _live_validate(self) -> None:
        if not self._shortcut_cb.currentText():
            self._run_btn.setEnabled(False)
            self._add_btn.setEnabled(False)
            return
        errors = self._field_errors()
        self._run_btn.setEnabled(self._console.is_running() and not errors)
        self._add_btn.setEnabled(not errors)
        if errors:
            n = len(errors)
            self._status_lbl.setText(f"⚠ {n} field{'s' if n > 1 else ''} to fix")
            self._status_lbl.setToolTip("\n".join(errors))
        else:
            self._status_lbl.setText("")
            self._status_lbl.setToolTip("")

    # ── Command generation ────────────────────────────────────────────────────

    def _command_text(self) -> str | None:
        name = self._shortcut_cb.currentText()
        entry = self._shortcuts.get(name)
        if not entry:
            return None
        values, errors = param_form.parse_values(self._current_params, self._param_widgets)
        if errors:
            return None
        self._last_area_detectors = midas_bridge.area_detector_devices(
            self._current_params, values
        )
        spec, module = entry
        notes = self._notes.text().strip()
        import_line = command_builder.make_import_line(name, module)
        re_line = command_builder.make_re_line(name, self._current_params, values, notes)
        return f"{import_line}\n{re_line}"

    def last_dispatch_area_detector_devices(self) -> list:
        """area_detector device name(s) bound in the command last produced by
        `_command_text()` -- mirrors PlanRunnerPanel's own accessor of the
        same name."""
        return self._last_area_detectors

    def _run_command(self) -> None:
        text = self._command_text()
        if not text:
            return
        notes = self._notes.text().strip()
        self.runRequested.emit(text, notes)
        self.close()

    def _queue_command(self) -> None:
        text = self._command_text()
        if not text:
            return
        notes = self._notes.text().strip()
        self.queueRequested.emit(text, notes)
        self.close()
