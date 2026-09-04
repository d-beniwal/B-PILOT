"""Composite motor-rows widget for ``scan_skeletons.py``'s six ``*args`` plans.

``instrument/plans/scan_skeletons.py`` defines six generic scan plans
(``mpe_list_scan``, ``mpe_list_grid_scan``, ``mpe_step_scan``,
``mpe_step_grid_scan``, ``mpe_rel_scan``, ``mpe_rel_grid_scan``) that all take
their motor(s)/position(s) through a bare ``*args`` tuple -- something
:mod:`plan_parser`'s AST walker can never turn into an ordinary ``ParamSpec``
field, no matter what the docstring says (see ``plan_parser.SKELETON_SHAPES``
for the shape table and the research behind it).

:class:`MotorRowsWidget` renders a repeatable per-motor row (a motor picker +
shape-dependent numeric fields) and exposes the result via :meth:`tokens`, a
flat list of ALREADY-RENDERED source-code fragments -- bare motor names,
numeric literals, ``"[10, 20, 30]"`` list literals -- meant to be spliced
VERBATIM as leading positional arguments ahead of the ordinary kwargs (see
``PlanRunnerPanel._make_re_line``). No ``repr()``, no ``RawCode`` wrapping
needed at the splice site: these tokens are already valid Python source.
"""
from __future__ import annotations

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

from . import device_source
from . import style as S

# Shapes matching plan_parser.SKELETON_SHAPES's `shape` values.
_LIST_SHAPES = {"list", "list_grid"}
_STEP_SHAPES = {"step", "step_grid"}


def _float_field(placeholder: str, on_change) -> QtWidgets.QLineEdit:
    field = QtWidgets.QLineEdit()
    validator = QtGui.QDoubleValidator()
    validator.setNotation(QtGui.QDoubleValidator.StandardNotation)
    validator.setLocale(QtCore.QLocale.c())
    field.setValidator(validator)
    field.setPlaceholderText(placeholder)
    field.textChanged.connect(on_change)
    return field


class MotorAxisPicker(QtWidgets.QWidget):
    """Motor combo + dependent axis combo -> a ``motor.axis`` source token.

    A "motor" is a multi-axis device whose scannable objects are sub-components
    (``lens1E.x``, ``lens1E.y``, ...); see :mod:`axis_discovery`.  Selecting a
    motor repopulates the axis combo from
    ``device_source.get_catalog().axes_for(motor)``:

    * **0 axes** -> axis combo hidden; :meth:`token` returns the bare motor name
      (a directly-settable device such as a plain ``EpicsMotor``).
    * **1 axis**  -> auto-selected, combo shown so ``nfE.x`` is visible.
    * **N axes**  -> a leading blank forces a conscious choice; :meth:`error`
      flags the field until the user picks one (a silent default axis would
      quietly scan the wrong stage).

    Reused by :class:`_MotorRow` (scan_skeletons rows), the plan-runner's
    ``device{motor}`` field, and (via :class:`MotorAxisListWidget`) its
    ``device_list{motor}`` field, so every place a motor is chosen behaves the
    same way.
    """

    changed = QtCore.pyqtSignal()

    def __init__(self, allow_blank: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._has_axes = False  # tracked explicitly (widget.isVisible() is
        # False until the picker is actually shown, which breaks off-screen use)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(S.px(4))

        self.motor_cb = S.NoScrollComboBox()
        if allow_blank:
            self.motor_cb.addItem("")  # blank = not yet chosen
        self.motor_cb.addItems(device_source.get_catalog().names_for("motor"))
        self.motor_cb.currentTextChanged.connect(self._on_motor_changed)
        layout.addWidget(self.motor_cb, 2)

        self.axis_cb = S.NoScrollComboBox()
        self.axis_cb.setToolTip("Axis of the selected motor to scan.")
        self.axis_cb.currentTextChanged.connect(self.changed)
        layout.addWidget(self.axis_cb, 1)

        self._reload_axes()

    def _on_motor_changed(self, *_) -> None:
        self._reload_axes()
        self.changed.emit()

    def _reload_axes(self) -> None:
        motor = self.motor_cb.currentText().strip()
        axes = device_source.get_catalog().axes_for(motor) if motor else []
        self.axis_cb.blockSignals(True)
        self.axis_cb.clear()
        if len(axes) > 1:
            self.axis_cb.addItem("")  # force a conscious pick when ambiguous
        self.axis_cb.addItems(axes)
        self.axis_cb.blockSignals(False)
        self._has_axes = bool(axes)
        self.axis_cb.setVisible(self._has_axes)  # hidden when device has no axes

    # -- public API --
    def motor(self) -> str:
        return self.motor_cb.currentText().strip()

    def axis(self) -> str:
        return self.axis_cb.currentText().strip() if self.has_axes() else ""

    def has_axes(self) -> bool:
        return self._has_axes

    def token(self) -> str:
        """``motor.axis`` (or bare ``motor`` when it has no axes); '' if unset."""
        motor = self.motor()
        if not motor:
            return ""
        axis = self.axis()
        return f"{motor}.{axis}" if axis else motor

    def error(self) -> str | None:
        """Human-readable error, or None when a usable token can be produced."""
        motor = self.motor()
        if not motor:
            return "select a motor"
        if self.has_axes() and not self.axis():
            return f"{motor}: select an axis"
        return None

    def set_from(self, motor: str, axis: str | None) -> None:
        """Best-effort inverse of :meth:`token` -- select `motor` then `axis`.

        Unknown motor/axis names (e.g. from a command built under a different
        device selection, or hand-edited) are added so the restored value stays
        visible and selected rather than silently dropped.
        """
        motor = (motor or "").strip()
        idx = self.motor_cb.findText(motor)
        if idx < 0 and motor:
            self.motor_cb.addItem(motor)
            idx = self.motor_cb.count() - 1
        if idx >= 0:
            self.motor_cb.setCurrentIndex(idx)
        # Reload unconditionally: setCurrentIndex to the *same* motor fires no
        # signal, which would otherwise leave a stale axis from a prior value
        # (a bare motor loaded over an existing `motor.axis` selection). A fresh
        # populate leaves the axis blank (multi-axis) or auto-selected (single).
        self._reload_axes()
        axis = (axis or "").strip()
        if axis:
            aidx = self.axis_cb.findText(axis)
            if aidx < 0:
                self.axis_cb.addItem(axis)
                self._has_axes = True
                self.axis_cb.setVisible(True)
                aidx = self.axis_cb.count() - 1
            self.axis_cb.setCurrentIndex(aidx)


class MotorAxisListWidget(QtWidgets.QWidget):
    """Repeatable list of :class:`MotorAxisPicker` rows for ``device_list{motor}``.

    A plain multi-select list can't attach a per-item axis choice, so each
    selected motor gets its own row (motor + axis + Remove), mirroring
    :class:`MotorRowsWidget`.  :meth:`tokens` yields one ``motor.axis`` fragment
    per filled row.
    """

    changed = QtCore.pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[MotorAxisPicker, QtWidgets.QWidget]] = []

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(S.px(4))
        self._rows_layout = QtWidgets.QVBoxLayout()
        self._rows_layout.setSpacing(S.px(4))
        outer.addLayout(self._rows_layout)

        add_btn = QtWidgets.QPushButton("+ Add motor")
        add_btn.clicked.connect(lambda: self.add_row())
        wrap = QtWidgets.QHBoxLayout()
        wrap.addWidget(add_btn)
        wrap.addStretch(1)
        outer.addLayout(wrap)

        self.add_row()

    def add_row(self, motor: str = "", axis: str | None = None) -> None:
        row = QtWidgets.QWidget()
        rl = QtWidgets.QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(S.px(6))
        picker = MotorAxisPicker(allow_blank=True)
        picker.changed.connect(self.changed)
        rl.addWidget(picker, 1)
        remove_btn = QtWidgets.QToolButton()
        remove_btn.setText("✕")
        remove_btn.setToolTip("Remove this motor")
        remove_btn.clicked.connect(lambda: self._remove_row(row))
        rl.addWidget(remove_btn)
        if motor:
            picker.set_from(motor, axis)
        self._rows.append((picker, row))
        self._rows_layout.addWidget(row)
        self.changed.emit()

    def _remove_row(self, row: QtWidgets.QWidget) -> None:
        for i, (_picker, widget) in enumerate(self._rows):
            if widget is row:
                self._rows.pop(i)
                self._rows_layout.removeWidget(widget)
                widget.deleteLater()
                self.changed.emit()
                return

    def tokens(self) -> list[str]:
        """One ``motor.axis`` fragment per filled row (blank rows omitted)."""
        return [p.token() for p, _ in self._rows if p.token()]

    def errors(self) -> list[str]:
        """Errors for rows the user started filling (a wholly blank row is fine)."""
        errs: list[str] = []
        for picker, _ in self._rows:
            if picker.motor():
                err = picker.error()
                if err:
                    errs.append(err)
        return errs

    def set_from_pairs(self, pairs: list[tuple[str, str | None]]) -> None:
        """Replace all rows with `pairs` ((motor, axis) each); one blank if empty."""
        while self._rows:
            _picker, widget = self._rows.pop()
            self._rows_layout.removeWidget(widget)
            widget.deleteLater()
        if not pairs:
            self.add_row()
            return
        for motor, axis in pairs:
            self.add_row(motor, axis)


class _MotorRow(QtWidgets.QWidget):
    """One motor's fields: a picker + shape-dependent numeric fields, plus a
    Remove button.

    Unlike a typical composite widget, this lays out none of its children --
    it never appears on screen itself. :class:`MotorRowsWidget` places its
    widgets (see :meth:`field_widgets`) directly into a shared grid, one grid
    row per motor, so every row's fields line up under a single column-header
    row instead of each row repeating its own inline labels.
    """

    changed = QtCore.pyqtSignal()
    remove_requested = QtCore.pyqtSignal(object)  # emits self

    def __init__(self, shape: str, relative: bool, parent=None) -> None:
        super().__init__(parent)
        self.shape = shape
        self.nsteps: QtWidgets.QLineEdit | None = None
        self.setVisible(False)  # never shown itself -- pure signal/widget holder

        self.picker = MotorAxisPicker(allow_blank=True)
        self.picker.changed.connect(self.changed)

        if shape in _LIST_SHAPES:
            self.positions = QtWidgets.QLineEdit()
            self.positions.setPlaceholderText("10  (or 10, 20, 30)")
            self.positions.textChanged.connect(self.changed)
        else:
            self.start = _float_field("0", self.changed)
            self.stop = _float_field("10", self.changed)
            if shape == "step_grid":
                self.nsteps = QtWidgets.QLineEdit()
                self.nsteps.setValidator(QtGui.QIntValidator(1, 1_000_000))
                self.nsteps.setPlaceholderText("11")
                self.nsteps.textChanged.connect(self.changed)

        self.remove_btn = QtWidgets.QToolButton()
        self.remove_btn.setText("✕")
        self.remove_btn.setToolTip("Remove this motor")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))

    def field_widgets(self) -> list[QtWidgets.QWidget]:
        """This row's data-column widgets, in header-column order (no Remove button)."""
        if self.shape in _LIST_SHAPES:
            return [self.picker, self.positions]
        out = [self.picker, self.start, self.stop]
        if self.nsteps is not None:
            out.append(self.nsteps)
        return out

    def error(self) -> str | None:
        """Human-readable error, or None if this row is valid."""
        picker_err = self.picker.error()
        if picker_err is not None:
            return picker_err
        motor = self.picker.motor()
        if self.shape in _LIST_SHAPES:
            raw = self.positions.text().strip()
            if not raw:
                return f"{motor}: enter at least one position"
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    float(part)
                except ValueError:
                    return f"{motor}: '{part}' is not a number"
            return None
        start = self.start.text().strip()
        stop = self.stop.text().strip()
        if not start or not stop:
            return f"{motor}: start/stop required"
        try:
            float(start)
            float(stop)
        except ValueError:
            return f"{motor}: start/stop must be numbers"
        if self.nsteps is not None:
            nsteps = self.nsteps.text().strip()
            if not nsteps:
                return f"{motor}: nsteps required"
            try:
                n = int(nsteps)
            except ValueError:
                return f"{motor}: nsteps must be a whole number"
            if n <= 0:
                return f"{motor}: nsteps must be > 0"
        return None

    def tokens(self) -> list[str]:
        """This row's contribution to the flat *args token list (call only when valid)."""
        out = [self.picker.token()]  # "motor.axis" (or bare motor if no axes)
        if self.shape in _LIST_SHAPES:
            raw = self.positions.text().strip()
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            out.append("[" + ", ".join(parts) + "]")
        else:
            out.append(self.start.text().strip())
            out.append(self.stop.text().strip())
            if self.nsteps is not None:
                out.append(self.nsteps.text().strip())
        return out

    def set_tokens(self, tokens: list[str]) -> None:
        """Best-effort inverse of :meth:`tokens` — populate this row from source tokens."""
        if not tokens:
            return
        head = tokens[0].strip()
        if head:
            # token[0] is "motor.axis" (or a bare "motor" for an axis-less
            # device) — split on the LAST dot so multi-dot attribute paths still
            # keep the trailing axis. MotorAxisPicker.set_from re-adds any
            # motor/axis not in the current discovery (see its docstring).
            motor_part, dot, axis_part = head.rpartition(".")
            if dot:
                self.picker.set_from(motor_part, axis_part)
            else:
                self.picker.set_from(axis_part, None)
        if self.shape in _LIST_SHAPES:
            if len(tokens) > 1:
                raw = tokens[1].strip()
                if raw.startswith("[") and raw.endswith("]"):
                    raw = raw[1:-1]
                self.positions.setText(raw)
        else:
            if len(tokens) > 1:
                self.start.setText(tokens[1].strip())
            if len(tokens) > 2:
                self.stop.setText(tokens[2].strip())
            if self.nsteps is not None and len(tokens) > 3:
                self.nsteps.setText(tokens[3].strip())


class MotorRowsWidget(QtWidgets.QWidget):
    """Repeatable per-motor rows for one of ``scan_skeletons.py``'s six shapes.

    Starts with 2 rows (the dominant real-world shape across all three
    beamlines is a 2-motor outer/inner grid); floor of 1 enforced by
    :meth:`remove_row`; no hard ceiling (20-ID-E occasionally uses 3).
    """

    changed = QtCore.pyqtSignal()

    def __init__(self, shape: str, relative: bool = False, parent=None) -> None:
        super().__init__(parent)
        if shape not in _LIST_SHAPES | _STEP_SHAPES:
            raise ValueError(f"Unknown skeleton shape: {shape!r}")
        self.shape = shape
        self.relative = relative
        self._rows: list[_MotorRow] = []

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(S.px(4))

        self._grid = QtWidgets.QGridLayout()
        self._grid.setSpacing(S.px(6))
        outer.addLayout(self._grid)
        self._ncols = self._add_header()

        add_btn = QtWidgets.QPushButton("+ Add motor")
        add_btn.clicked.connect(self.add_row)
        add_row_wrap = QtWidgets.QHBoxLayout()
        add_row_wrap.addWidget(add_btn)
        add_row_wrap.addStretch(1)
        outer.addLayout(add_row_wrap)

        self.add_row()
        self.add_row()

    def _add_header(self) -> int:
        """Add the single column-header row above the motor rows.

        Replaces the old per-row inline labels ("positions:", "start:", ...)
        with one set of column headers, freeing the fields themselves to be
        wider and easier to read. Returns the data-column count (the Remove
        button lives one column past it).
        """
        if self.shape in _LIST_SHAPES:
            headers = ["Motor", "Positions"]
        else:
            start_lbl = "Start (Δ)" if self.relative else "Start"
            stop_lbl = "Stop (Δ)" if self.relative else "Stop"
            headers = ["Motor", start_lbl, stop_lbl]
            if self.shape == "step_grid":
                headers.append("Steps")
        for col, text in enumerate(headers):
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet(f"color: {S.MUTED}; font-weight: bold;")
            self._grid.addWidget(lbl, 0, col)
        self._grid.setColumnStretch(0, 2)
        for col in range(1, len(headers)):
            self._grid.setColumnStretch(col, 3)
        self._grid.setColumnStretch(len(headers), 0)  # Remove-button column
        return len(headers)

    def add_row(self) -> None:
        row = _MotorRow(self.shape, self.relative)
        row.changed.connect(self.changed)
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._place_row(row)
        self.changed.emit()

    def _place_row(self, row: _MotorRow) -> None:
        """(Re)place `row`'s widgets at its current index in the shared grid."""
        grid_row = self._rows.index(row) + 1  # +1: header occupies row 0
        for col, widget in enumerate(row.field_widgets()):
            self._grid.addWidget(widget, grid_row, col)
        self._grid.addWidget(row.remove_btn, grid_row, self._ncols)

    def _remove_row(self, row: _MotorRow) -> None:
        if len(self._rows) <= 1:
            return  # floor: always at least one motor
        self._rows.remove(row)
        for widget in row.field_widgets() + [row.remove_btn]:
            self._grid.removeWidget(widget)
            widget.deleteLater()  # these are unparented from `row` -- see class docstring
        row.deleteLater()
        for remaining in self._rows:  # re-place so grid rows stay contiguous
            self._place_row(remaining)
        self.changed.emit()

    def errors(self) -> list[str]:
        errs = []
        for row in self._rows:
            err = row.error()
            if err is not None:
                errs.append(err)
        return errs

    def tokens(self) -> list[str]:
        """Flat *args token list across all rows -- call only once `errors()` is empty."""
        out: list[str] = []
        for row in self._rows:
            out.extend(row.tokens())
        return out

    def load_rows(self, rows: list[list[str]]) -> None:
        """Replace all rows with `rows` (each a per-motor token list, see :meth:`tokens`).

        Best-effort inverse of the flat `tokens()` list, chunked back into rows
        by the caller (see `plan_runner.load_from_command`). Grows/shrinks the
        row count to match; a no-op when `rows` is empty (keeps the current
        rows rather than dropping to zero, since the floor is always 1).
        """
        if not rows:
            return
        while len(self._rows) < len(rows):
            self.add_row()
        while len(self._rows) > len(rows):
            self._remove_row(self._rows[-1])
        for row, tokens in zip(self._rows, rows):
            row.set_tokens(tokens)
