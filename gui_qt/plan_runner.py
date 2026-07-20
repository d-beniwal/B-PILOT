"""Plan-runner panel: file browser + plan dropdown + parameter form + command.

The parameter form is built directly from each plan's docstring + signature
(via :mod:`plan_parser`, AST-only — nothing is imported), so any new plan is
picked up automatically.

Beyond the tk GUI it adds **live datatype validation** — numeric fields reject
non-numeric input and every field is checked on the fly (required / format),
with invalid fields flagged in red and the *Run* button gated on a valid form —
and **rich hover tooltips** describing each parameter.

Emits :pyattr:`runRequested` (a two-line ``from ... import ...`` +
``RE(plan(...))`` string) when the user clicks **Run**; the main window feeds
that into the embedded console.
"""
from __future__ import annotations

import html
import os

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

from . import config
from . import device_source
from . import plan_parser as P
from . import style as S
from .plan_parser import _NODEFAULT
from .plan_parser import ParamSpec
from .plan_parser import RawCode


class PlanRunnerPanel(QtWidgets.QWidget):
    """File browser, plan selector, parameter form, and command builder."""

    # emitted with (command_text, run_notes) when the user clicks Run
    runRequested = QtCore.pyqtSignal(str, str)
    # emitted with (command_text, run_notes) when the user clicks Add to Queue
    queueRequested = QtCore.pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        """Build the panel and populate the file browser + plan dropdown."""
        super().__init__(parent)
        self._file_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._plan_origins: dict[str, str] = {}
        self._plan_specs: dict[str, dict] = {}
        self._plan_list: list[str] = []
        self._param_widgets: dict[str, tuple] = {}
        self._current_params: list[ParamSpec] = []
        self._console_ready = False

        self._build_ui()
        self._populate_file_browser()
        self._refresh_plan_dropdown(preserve_selection=False)

    # ── UI construction ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        outer.addWidget(split, 1)

        # ── Left: file browser card ─────────────────────────────────────────
        fb_card = S.make_card("User files")
        self._fb_container = QtWidgets.QWidget()
        self._fb_layout = QtWidgets.QVBoxLayout(self._fb_container)
        self._fb_layout.setContentsMargins(2, 2, 2, 2)
        self._fb_layout.setSpacing(2)
        self._fb_layout.addStretch(1)
        fb_scroll = QtWidgets.QScrollArea()
        fb_scroll.setWidgetResizable(True)
        fb_scroll.setWidget(self._fb_container)
        fb_card.body.addWidget(fb_scroll, 1)
        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_files)
        fb_card.body.addWidget(refresh_btn)
        fb_card.setMinimumWidth(170)
        split.addWidget(fb_card)

        # ── Right: plan selector + params + command ─────────────────────────
        right = QtWidgets.QWidget()
        rlay = QtWidgets.QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)

        sel_row = QtWidgets.QHBoxLayout()
        sel_row.addWidget(S.LabelRight("Plan:"))
        self._plan_cb = S.NoScrollComboBox()
        self._plan_cb.setMinimumWidth(220)
        self._plan_cb.currentIndexChanged.connect(self._on_plan_change)
        sel_row.addWidget(self._plan_cb)
        self._doc_lbl = QtWidgets.QLabel("")
        self._doc_lbl.setWordWrap(True)
        self._doc_lbl.setStyleSheet(f"color: {S.MUTED};")
        sel_row.addWidget(self._doc_lbl, 1)
        rlay.addLayout(sel_row)

        # Resizable stack: Parameters / Command / Run notes.  A vertical splitter
        # gives each panel a draggable divider so heights can be adjusted.
        vsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        vsplit.setChildrenCollapsible(False)

        # ── Parameters card (scrollable grid) ──
        param_card = S.make_card("Parameters   (hover a name · ★ = required)")
        self._param_host = QtWidgets.QWidget()
        self._param_grid = QtWidgets.QGridLayout(self._param_host)
        self._param_grid.setContentsMargins(4, 4, 4, 4)
        self._param_grid.setHorizontalSpacing(8)
        self._param_grid.setVerticalSpacing(6)
        self._param_grid.setColumnStretch(1, 1)
        param_scroll = QtWidgets.QScrollArea()
        param_scroll.setWidgetResizable(True)
        param_scroll.setWidget(self._param_host)
        param_card.body.addWidget(param_scroll)
        vsplit.addWidget(param_card)

        # ── Command card (live, coloured) ──
        cmd_card = S.make_card("Command  (updates live · Run in console →, or Copy)")
        self._cmd_display = QtWidgets.QTextEdit()
        self._cmd_display.setObjectName("mono")
        self._cmd_display.setReadOnly(True)
        self._cmd_display.setMinimumHeight(44)
        self._cmd_display.setFont(QtGui.QFont(S.MONO_FAMILIES[0]))
        cmd_card.body.addWidget(self._cmd_display)
        vsplit.addWidget(cmd_card)

        # ── Run notes card ── attached to the run on Run, then cleared.
        notes_card = S.make_card("Run notes   (attached to this run, then cleared)")
        self._notes = QtWidgets.QPlainTextEdit()
        self._notes.setPlaceholderText(
            "Notes about this run… attached to the Bluesky run on Run, then cleared."
        )
        self._notes.setMinimumHeight(40)
        notes_card.body.addWidget(self._notes)
        vsplit.addWidget(notes_card)

        vsplit.setStretchFactor(0, 1)   # Parameters takes the slack by default
        vsplit.setStretchFactor(1, 0)
        vsplit.setStretchFactor(2, 0)
        vsplit.setSizes([420, 130, 110])
        rlay.addWidget(vsplit, 1)

        # ── Fixed action row (always visible below the resizable panels) ──
        btn_row = QtWidgets.QHBoxLayout()
        self._build_btn = QtWidgets.QPushButton("Build / Update")
        self._build_btn.clicked.connect(self._update_command)
        self._copy_btn = QtWidgets.QPushButton("Copy")
        self._copy_btn.clicked.connect(self._copy_command)
        self._add_btn = QtWidgets.QPushButton("Add to Queue")
        self._add_btn.setToolTip("Append this plan to the queue (bottom-right).")
        self._add_btn.clicked.connect(self._queue_command)
        self._add_btn.setEnabled(False)
        self._run_btn = S.primary_btn("▶  Run in console")
        self._run_btn.clicked.connect(self._run_command)
        self._run_btn.setEnabled(False)
        self._run_btn.setToolTip("Launch the IPython session first (top toolbar).")
        btn_row.addWidget(self._build_btn)
        btn_row.addWidget(self._copy_btn)
        btn_row.addStretch(1)
        self._status_lbl = QtWidgets.QLabel("")
        self._status_lbl.setStyleSheet(f"color: {S.MUTED};")
        btn_row.addWidget(self._status_lbl)
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._run_btn)
        rlay.addLayout(btn_row)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([190, 560])

    # ── Console-readiness (set by the main window) ──────────────────────────────

    def set_console_ready(self, ready: bool) -> None:
        """Enable/disable *Run* depending on whether the console is live."""
        self._console_ready = ready
        self._run_btn.setToolTip(
            "" if ready else "Launch the IPython session first (top toolbar)."
        )
        self._live_validate()

    # ── File browser ────────────────────────────────────────────────────────────

    def _populate_file_browser(self) -> None:
        # Clear existing widgets (keep the trailing stretch).
        while self._fb_layout.count() > 1:
            item = self._fb_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        old = {p: cb.isChecked() for p, cb in self._file_checks.items()}
        self._file_checks.clear()

        plans_dir = config.get("plans_dir")
        default_file = config.get("default_plan_file")
        visible = set(config.get("visible_plan_files") or [])
        insert_at = 0
        pending_dir = None
        for display_name, kind, abs_path, depth in P.scan_user_dir(plans_dir):
            if kind == "dir":
                pending_dir = (display_name, kind, abs_path, depth)
                continue
            if depth == 0:
                pending_dir = None  # a top-level file ends any pending dir group
            rel = os.path.relpath(abs_path, plans_dir).replace(os.sep, "/")
            if rel not in visible:
                continue
            if pending_dir is not None:
                lbl = QtWidgets.QLabel(f"📁 {pending_dir[0]}")
                lbl.setStyleSheet(f"color: {S.MUTED};")
                self._fb_layout.insertWidget(insert_at, lbl)
                insert_at += 1
                pending_dir = None
            cb = QtWidgets.QCheckBox(display_name)
            if depth:
                cb.setStyleSheet(f"margin-left: {16 * depth}px;")
            checked = old.get(abs_path, display_name == default_file)
            cb.setChecked(checked)
            cb.toggled.connect(self._on_file_toggle)
            self._file_checks[abs_path] = cb
            self._fb_layout.insertWidget(insert_at, cb)
            insert_at += 1

    def _refresh_files(self) -> None:
        self._populate_file_browser()
        self._refresh_plan_dropdown(preserve_selection=True)

    def apply_config(self) -> None:
        """Re-scan the file browser / plan dropdown after a config change."""
        self._refresh_files()

    def _on_file_toggle(self, _checked: bool) -> None:
        self._refresh_plan_dropdown(preserve_selection=True)

    # ── Plan dropdown ─────────────────────────────────────────────────────────────

    def _refresh_plan_dropdown(self, preserve_selection: bool = True) -> None:
        old = self._plan_cb.currentText() if preserve_selection else ""
        self._plan_origins.clear()
        self._plan_specs.clear()
        self._plan_list.clear()

        import_root = config.get("import_root")
        for abs_path, cb in self._file_checks.items():
            if not cb.isChecked():
                continue
            module = P.file_to_module(abs_path, import_root)
            for name, spec in P.find_plan_specs(abs_path).items():
                if name not in self._plan_specs:
                    self._plan_specs[name] = spec
                    self._plan_origins[name] = module
                    self._plan_list.append(name)

        self._plan_cb.blockSignals(True)
        self._plan_cb.clear()
        self._plan_cb.addItems(self._plan_list)
        self._plan_cb.blockSignals(False)

        if self._plan_list:
            keep = preserve_selection and old in self._plan_list
            idx = self._plan_list.index(old) if keep else 0
            self._plan_cb.setCurrentIndex(idx)
            self._on_plan_change()
        else:
            self._doc_lbl.setText("(no plans — check a .py file on the left)")
            self._rebuild_param_form([])
            self._set_cmd_text("(no plan selected)")

    # ── Parameter form ────────────────────────────────────────────────────────────

    def _on_plan_change(self, *_) -> None:
        plan_name = self._plan_cb.currentText()
        if not plan_name:
            return
        spec = self._plan_specs.get(plan_name)
        module = self._plan_origins.get(plan_name, "")
        fallback = f"from {module}" if module else ""
        if spec and spec["documented"]:
            self._doc_lbl.setText(spec["summary"] or fallback)
            self._current_params = spec["params"]
            self._rebuild_param_form(spec["params"])
        else:
            summary = spec["summary"] if spec else ""
            self._doc_lbl.setText(summary or fallback)
            self._current_params = []
            self._rebuild_generic_form()
        self._live_validate()   # marks fields, gates Run, and renders the command

    def _clear_param_grid(self) -> None:
        while self._param_grid.count():
            item = self._param_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._param_widgets.clear()

    @staticmethod
    def _label_text(spec: ParamSpec) -> str:
        label = spec.short or spec.name
        if spec.units:
            label += f"  ({spec.units})"
        if spec.required:
            label += "  ★"
        return label

    @staticmethod
    def _tooltip(spec: ParamSpec) -> str:
        """Rich hover hint: name, dtype/units, required-ness, description."""
        head = f"{spec.name} : {spec.dtype}"
        if spec.units:
            head += f" [{spec.units}]"
        lines = [head, "required" if spec.required else "optional"]
        detail = spec.long or (spec.short if spec.short != spec.name else "")
        if detail:
            lines += ["", detail]
        return "\n".join(lines)

    def _rebuild_param_form(self, params: list[ParamSpec]) -> None:
        self._clear_param_grid()
        for row, spec in enumerate(params):
            tip = self._tooltip(spec)
            lbl = S.LabelRight(self._label_text(spec))
            lbl.setWordWrap(True)
            S.HoverTip(lbl, tip)
            self._param_grid.addWidget(lbl, row, 0)

            if spec.dtype == "positions":
                widget = QtWidgets.QPlainTextEdit()
                widget.setObjectName("mono")
                widget.setFixedHeight(90)
                widget.setPlaceholderText("100, 0, 50\n150, 0, 50")
                widget.textChanged.connect(self._live_validate)
            elif spec.dtype == "bool":
                widget = QtWidgets.QCheckBox()
                widget.setChecked(bool(spec.default))
                widget.toggled.connect(self._live_validate)
            elif spec.dtype == "choice":
                widget = S.NoScrollComboBox()
                opts = spec.choices or (
                    [str(spec.default)] if spec.default is not None else []
                )
                widget.addItems(opts)
                if spec.default is not None and str(spec.default) in opts:
                    widget.setCurrentText(str(spec.default))
                widget.currentTextChanged.connect(self._live_validate)
            elif spec.dtype == "device":
                # One device object -> dropdown of names for this category.
                widget = S.NoScrollComboBox()
                names = device_source.get_catalog().names_for(spec.category)
                # An optional device (None default / not required) gets a blank
                # entry meaning "omit the arg -> plan uses its default device".
                if spec.blank_omits or not spec.required:
                    widget.addItem("")
                widget.addItems(names)
                if spec.default not in (None, _NODEFAULT) and str(spec.default) in names:
                    widget.setCurrentText(str(spec.default))
                widget.currentTextChanged.connect(self._live_validate)
            elif spec.dtype == "device_list":
                # List of device objects -> multi-select of names for the category.
                widget = QtWidgets.QListWidget()
                widget.setSelectionMode(
                    QtWidgets.QAbstractItemView.ExtendedSelection
                )
                widget.addItems(device_source.get_catalog().names_for(spec.category))
                widget.setFixedHeight(90)
                widget.itemSelectionChanged.connect(self._live_validate)
            else:  # str / int / float / unknown -> line edit
                widget = QtWidgets.QLineEdit()
                # Datatype enforcement: numeric fields reject non-numeric input.
                if spec.dtype == "int":
                    widget.setValidator(QtGui.QIntValidator())
                elif spec.dtype == "float":
                    v = QtGui.QDoubleValidator()
                    v.setNotation(QtGui.QDoubleValidator.StandardNotation)
                    v.setLocale(QtCore.QLocale.c())
                    widget.setValidator(v)
                if spec.default not in (None, _NODEFAULT):
                    widget.setText(str(spec.default))
                widget.textChanged.connect(self._live_validate)

            S.HoverTip(widget, tip)
            self._param_grid.addWidget(widget, row, 1)
            self._param_widgets[spec.name] = (spec, widget)

        self._param_grid.setRowStretch(len(params), 1)

    def _rebuild_generic_form(self) -> None:
        self._clear_param_grid()
        lbl = QtWidgets.QLabel("Arguments  (Python syntax, comma-separated):")
        self._param_grid.addWidget(lbl, 0, 0, 1, 2)
        txt = QtWidgets.QPlainTextEdit()
        txt.setObjectName("mono")
        txt.setFixedHeight(90)
        txt.setPlaceholderText("file_name='test', p_start=-5, p_end=5")
        txt.textChanged.connect(self._live_validate)
        self._param_grid.addWidget(txt, 1, 0, 1, 2)
        self._param_widgets["__args__"] = ("generic", txt)
        self._param_grid.setRowStretch(2, 1)

    # ── Validation ────────────────────────────────────────────────────────────────

    def _field_error(self, spec: ParamSpec, widget) -> str | None:
        """Return an error string for `widget`, or None when it is acceptable."""
        short = spec.short or spec.name
        if spec.dtype == "positions":
            raw = widget.toPlainText().strip()
            if not raw:
                return f"{short}: required" if spec.required else None
            for i, line in enumerate(raw.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    parts = [float(x.strip()) for x in line.split(",")]
                except ValueError:
                    return f"{short} line {i}: non-numeric value"
                if len(parts) != 3:
                    return f"{short} line {i}: expected 3 values, got {len(parts)}"
            return None
        if spec.dtype == "bool":
            return None
        if spec.dtype == "choice":
            if not widget.currentText().strip() and spec.required:
                return f"{short}: required"
            return None
        if spec.dtype == "device":
            if not widget.currentText().strip() and spec.required:
                return f"{short}: required"
            return None
        if spec.dtype == "device_list":
            if not widget.selectedItems() and spec.required:
                return f"{short}: required"
            return None

        # str / int / float / unknown -> line edit
        raw = widget.text().strip()
        if not raw:
            if spec.required and not spec.blank_omits:
                return f"{short}: required"
            return None
        if spec.dtype == "float":
            try:
                float(raw)
            except ValueError:
                return f"{short}: not a valid number"
        elif spec.dtype == "int":
            try:
                int(raw)
            except ValueError:
                return f"{short}: not a valid integer"
        return None

    def _live_validate(self) -> None:
        """Re-check every field, flag invalid ones, and gate the Run button."""
        errors: list[str] = []
        if "__args__" not in self._param_widgets:
            for spec in self._current_params:
                widget = self._param_widgets[spec.name][1]
                err = self._field_error(spec, widget)
                # bool / device_list have no single-border widget to flag
                if spec.dtype not in ("bool", "device_list"):
                    S.mark_invalid(widget, err is not None)
                if err:
                    errors.append(err)

        self._run_btn.setEnabled(self._console_ready and not errors)
        # Add-to-queue needs only a valid form (you can build the queue before
        # launching IPython; the scheduler dispatches once the console is up).
        self._add_btn.setEnabled(not errors)
        if errors:
            n = len(errors)
            self._status_lbl.setText(f"⚠ {n} field{'s' if n > 1 else ''} to fix")
            self._status_lbl.setToolTip("\n".join(errors))
        else:
            self._status_lbl.setText("")
            self._status_lbl.setToolTip("")

        self._refresh_command(has_errors=bool(errors))

    # ── Parameter parsing ─────────────────────────────────────────────────────────

    def _parse_params(self) -> tuple[dict | None, list[str]]:
        plan_name = self._plan_cb.currentText()
        if not plan_name:
            return None, ["No plan selected."]
        if "__args__" in self._param_widgets:
            _, txt = self._param_widgets["__args__"]
            return {"__args__": txt.toPlainText().strip()}, []

        values: dict = {}
        errors: list[str] = []

        for spec in self._current_params:
            widget = self._param_widgets[spec.name][1]
            short = spec.short or spec.name

            if spec.dtype == "positions":
                raw = widget.toPlainText().strip()
                if not raw:
                    if spec.required:
                        errors.append(f"{short}: required")
                    continue
                triples = []
                for i, line in enumerate(raw.splitlines(), 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parts = [float(x.strip()) for x in line.split(",")]
                        if len(parts) != 3:
                            raise ValueError(f"expected 3 values, got {len(parts)}")
                        triples.append(tuple(parts))
                    except ValueError as exc:
                        errors.append(f"{short} line {i}: {exc}")
                if triples:
                    values[spec.name] = triples
            elif spec.dtype == "bool":
                values[spec.name] = widget.isChecked()
            elif spec.dtype == "choice":
                val = widget.currentText().strip()
                if val:
                    values[spec.name] = val
                elif spec.default not in (None, _NODEFAULT):
                    values[spec.name] = spec.default
            elif spec.dtype == "device":
                # RawCode -> emitted unquoted (a real object, not a string).
                val = widget.currentText().strip()
                if val:
                    values[spec.name] = RawCode(val)
                elif spec.required:
                    errors.append(f"{short}: required")
                # else: blank -> omit the arg (plan uses its default device)
            elif spec.dtype == "device_list":
                names = [it.text() for it in widget.selectedItems()]
                if names:
                    values[spec.name] = RawCode("[" + ", ".join(names) + "]")
                elif spec.required:
                    errors.append(f"{short}: required")
                # else: empty -> omit the arg (plan uses its default, e.g. [])
            elif spec.dtype == "float":
                self._read_number(spec, widget, values, errors, short, float, "number")
            elif spec.dtype == "int":
                self._read_number(spec, widget, values, errors, short, int, "integer")
            else:  # str / unknown -> text
                raw = widget.text().strip()
                if raw:
                    values[spec.name] = raw
                elif spec.blank_omits:
                    pass
                elif spec.required:
                    errors.append(f"{short}: required")
                elif spec.default not in (None, _NODEFAULT):
                    values[spec.name] = spec.default

        return values, errors

    @staticmethod
    def _read_number(spec, widget, values, errors, short, caster, kind) -> None:
        raw = widget.text().strip()
        if raw:
            try:
                values[spec.name] = caster(raw)
            except ValueError:
                errors.append(f"{short}: not a valid {kind}")
        elif spec.blank_omits:
            pass
        elif spec.required:
            errors.append(f"{short}: required")
        elif spec.default not in (None, _NODEFAULT):
            values[spec.name] = spec.default

    # ── Command generation ───────────────────────────────────────────────────────

    def _make_import_line(self, plan_name: str) -> str:
        # Fallback to instrument.collection when the plan's source module is
        # unknown: the MPE session loads `from instrument.collection import *`,
        # which re-exports every plan, so this import resolves for any real plan.
        module = self._plan_origins.get(plan_name, "instrument.collection")
        return f"from {module} import {plan_name}"

    def _make_re_line(self, plan_name: str, values: dict) -> str:
        if "__args__" in values:
            return f"RE({plan_name}({values['__args__']}))"
        args = []
        for spec in self._current_params:
            if spec.name not in values:
                continue
            val = values[spec.name]
            # RawCode (device refs) emit verbatim; everything else via repr().
            rendered = str(val) if isinstance(val, RawCode) else repr(val)
            args.append(f"{spec.name}={rendered}")
        return f"RE({plan_name}({', '.join(args)}))"

    def _compose_lines(self) -> tuple[str, str] | tuple[None, None]:
        """Return (import_line, re_line) if the form is valid, else (None, None)."""
        plan_name = self._plan_cb.currentText()
        if not plan_name:
            return None, None
        values, errors = self._parse_params()
        if errors:
            return None, None
        return (
            self._make_import_line(plan_name),
            self._make_re_line(plan_name, values),
        )

    def _refresh_command(self, has_errors: bool) -> None:
        """Re-render the command preview.  Called live on every change."""
        if not self._plan_cb.currentText():
            self._set_cmd_text("(no plan selected)")
            return
        if has_errors:
            self._set_cmd_text("(fix the highlighted fields to build the command)")
            return
        import_line, re_line = self._compose_lines()
        if not re_line:
            self._set_cmd_text("(fill in the parameters above)")
            return
        self._set_cmd_colored(import_line, re_line)

    def _update_command(self) -> None:
        # The preview updates live; this button is just a manual refresh.
        self._live_validate()

    def _set_cmd_text(self, text: str) -> None:
        """Show a plain (muted) message in the command box."""
        self._cmd_display.setPlainText(text)

    def _set_cmd_colored(self, import_line: str, re_line: str) -> None:
        """Show the two-line command with the import and RE lines coloured."""
        doc = (
            f'<div style="color:{S.CMD_IMPORT}; white-space:pre-wrap;">'
            f"{html.escape(import_line)}</div>"
            f'<div style="color:{S.CMD_RE}; white-space:pre-wrap;">'
            f"{html.escape(re_line)}</div>"
        )
        self._cmd_display.setHtml(doc)

    def _copy_command(self) -> None:
        import_line, re_line = self._compose_lines()
        if not re_line:
            self._flash_status("Nothing to copy — fix fields first.")
            return
        QtWidgets.QApplication.clipboard().setText(f"{import_line}\n{re_line}")
        self._flash_status("Copied.")

    def _run_command(self) -> None:
        import_line, re_line = self._compose_lines()
        if not re_line:
            return
        notes = self._notes.toPlainText().strip()
        self.runRequested.emit(f"{import_line}\n{re_line}", notes)
        # The notes' job is done once the run is launched — clear them.
        self._notes.clear()
        self._flash_status(
            "Sent to console." + (" Notes attached & cleared." if notes else "")
        )

    def _queue_command(self) -> None:
        import_line, re_line = self._compose_lines()
        if not re_line:
            return
        notes = self._notes.toPlainText().strip()
        self.queueRequested.emit(f"{import_line}\n{re_line}", notes)
        self._notes.clear()
        self._flash_status("Added to queue." + (" Notes attached." if notes else ""))

    def _flash_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)
        QtCore.QTimer.singleShot(3000, self._live_validate)
