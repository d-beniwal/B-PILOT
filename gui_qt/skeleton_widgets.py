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


class _MotorRow(QtWidgets.QWidget):
    """One motor + its position/step fields, plus a Remove button."""

    changed = QtCore.pyqtSignal()
    remove_requested = QtCore.pyqtSignal(object)  # emits self

    def __init__(self, shape: str, relative: bool, parent=None) -> None:
        super().__init__(parent)
        self.shape = shape
        self.nsteps: QtWidgets.QLineEdit | None = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(S.px(6))

        self.motor_cb = S.NoScrollComboBox()
        self.motor_cb.addItem("")  # blank = not yet chosen
        self.motor_cb.addItems(device_source.get_catalog().names_for("motor"))
        self.motor_cb.currentTextChanged.connect(self.changed)
        layout.addWidget(self.motor_cb, 1)

        if shape in _LIST_SHAPES:
            self.positions = QtWidgets.QLineEdit()
            self.positions.setPlaceholderText("10  (or 10, 20, 30)")
            self.positions.textChanged.connect(self.changed)
            layout.addWidget(QtWidgets.QLabel("positions:"))
            layout.addWidget(self.positions, 2)
        else:
            start_lbl = "start (Δ):" if relative else "start:"
            stop_lbl = "stop (Δ):" if relative else "stop:"
            self.start = _float_field("0", self.changed)
            self.stop = _float_field("10", self.changed)
            layout.addWidget(QtWidgets.QLabel(start_lbl))
            layout.addWidget(self.start)
            layout.addWidget(QtWidgets.QLabel(stop_lbl))
            layout.addWidget(self.stop)
            if shape == "step_grid":
                self.nsteps = QtWidgets.QLineEdit()
                self.nsteps.setValidator(QtGui.QIntValidator(1, 1_000_000))
                self.nsteps.setPlaceholderText("11")
                self.nsteps.textChanged.connect(self.changed)
                layout.addWidget(QtWidgets.QLabel("nsteps:"))
                layout.addWidget(self.nsteps)

        remove_btn = QtWidgets.QToolButton()
        remove_btn.setText("✕")
        remove_btn.setToolTip("Remove this motor")
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(remove_btn)

    def error(self) -> str | None:
        """Human-readable error, or None if this row is valid."""
        motor = self.motor_cb.currentText().strip()
        if not motor:
            return "select a motor"
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
        motor = self.motor_cb.currentText().strip()
        out = [motor]
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

        self._rows_layout = QtWidgets.QVBoxLayout()
        self._rows_layout.setSpacing(S.px(4))
        outer.addLayout(self._rows_layout)

        add_btn = QtWidgets.QPushButton("+ Add motor")
        add_btn.clicked.connect(self.add_row)
        add_row_wrap = QtWidgets.QHBoxLayout()
        add_row_wrap.addWidget(add_btn)
        add_row_wrap.addStretch(1)
        outer.addLayout(add_row_wrap)

        self.add_row()
        self.add_row()

    def add_row(self) -> None:
        row = _MotorRow(self.shape, self.relative)
        row.changed.connect(self.changed)
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        self.changed.emit()

    def _remove_row(self, row: _MotorRow) -> None:
        if len(self._rows) <= 1:
            return  # floor: always at least one motor
        self._rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.deleteLater()
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
