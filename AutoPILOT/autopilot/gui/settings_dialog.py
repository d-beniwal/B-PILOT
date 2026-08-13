"""AutoPILOT settings dialog: model/behavior, appearance, and advanced Argo overrides.

Mirrors B-PILOT's own `B_PILOT/config_dialog.py` house style (card sections,
Restore Defaults/Cancel/Save, values previewed in the form and only written
on Save) but as AutoPILOT's own small dialog -- deliberately *not* hooked
into B-PILOT's `config.py` profile system, which is per-beamline instrument
config and has nothing to do with one user's chat-widget preferences.
"""
from __future__ import annotations

from PyQt5 import QtGui, QtWidgets

from .. import settings
from .._bpilot_path import ensure_bpilot_on_path
from . import themes

ensure_bpilot_on_path()

from B_PILOT import style as bpilot_style  # noqa: E402

# A short, curated starting list -- the combo is editable so any
# Argo-supported model id can be typed directly.
_MODEL_CHOICES = [
    "",  # environment default (ARGO_MODEL / llm_client.DEFAULT_MODEL)
    "claude-haiku-4-5-20251001",
    "claude-sonnet-5",
    "claude-opus-4-8",
]


def _readable_fg(hex_color: str) -> str:
    """Black or white text, whichever reads better against `hex_color`."""
    color = QtGui.QColor(hex_color)
    luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
    return "#000000" if luminance > 140 else "#ffffff"


class _ColorButton(QtWidgets.QPushButton):
    """A small button showing its current color as a swatch; click opens a color picker."""

    def __init__(self, color: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(bpilot_style.px(70))
        self.color = color
        self.clicked.connect(self._pick)
        self.refresh()

    def refresh(self) -> None:
        self.setText(self.color)
        self.setStyleSheet(
            f"background-color: {self.color}; color: {_readable_fg(self.color)}; "
            f"border: 1px solid {bpilot_style.BORDER};"
        )

    def _pick(self) -> None:
        chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color), self, "Choose color")
        if chosen.isValid():
            self.color = chosen.name()
            self.refresh()


class AutoPilotSettingsDialog(QtWidgets.QDialog):
    """Modal settings form. Nothing is persisted until the caller saves `values()`."""

    _COLOR_FIELDS = [
        ("user_bubble_bg", "Your message background:"),
        ("user_text_color", "Your message text:"),
        ("assistant_bubble_bg", "AutoPILOT message background:"),
        ("assistant_text_color", "AutoPILOT message text:"),
        ("panel_background", "Transcript background:"),
    ]

    def __init__(self, current: dict, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AutoPILOT Settings")
        self.setMinimumWidth(bpilot_style.px(420))

        self._color_buttons: dict[str, _ColorButton] = {}

        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(8)
        outer.addWidget(self._build_model_card())
        outer.addWidget(self._build_appearance_card())
        outer.addWidget(self._build_advanced_card())
        outer.addWidget(self._build_testing_card())
        outer.addStretch(1)
        outer.addLayout(self._build_buttons())

        self._load_from(current)

    # ── Cards ────────────────────────────────────────────────────────────

    def _build_model_card(self) -> QtWidgets.QWidget:
        card = bpilot_style.make_card("Model && behavior")

        row = QtWidgets.QHBoxLayout()
        row.addWidget(bpilot_style.LabelRight("Model:"))
        self._model = QtWidgets.QComboBox()
        self._model.setEditable(True)
        self._model.addItems(_MODEL_CHOICES)
        self._model.setToolTip(
            "Argo-backed Claude model id. Blank uses ARGO_MODEL, or the built-in default."
        )
        row.addWidget(self._model, 1)
        card.body.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(bpilot_style.LabelRight("Temperature:"))
        self._temperature = QtWidgets.QDoubleSpinBox()
        self._temperature.setRange(0.0, 1.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(2)
        self._temperature.setToolTip(
            "Lower values give more consistent/reproducible scan-parameter choices "
            "for the forced tool call; higher values vary more."
        )
        row.addWidget(self._temperature)
        row.addStretch(1)
        card.body.addLayout(row)

        self._show_raw = QtWidgets.QCheckBox("Show raw model output (debug)")
        self._show_raw.setToolTip(
            "Include the raw and validated tool-call JSON in each AutoPILOT reply."
        )
        card.body.addWidget(self._show_raw)
        return card

    def _build_appearance_card(self) -> QtWidgets.QWidget:
        card = bpilot_style.make_card("Appearance")

        row = QtWidgets.QHBoxLayout()
        row.addWidget(bpilot_style.LabelRight("Theme:"))
        self._theme = QtWidgets.QComboBox()
        for key, label in themes.THEME_CHOICES:
            self._theme.addItem(label, key)
        self._theme.setToolTip(
            "Picking a theme previews its colors into the swatches below -- "
            "tweak any swatch afterward for a custom mix."
        )
        self._theme.currentIndexChanged.connect(self._preview_theme)
        row.addWidget(self._theme, 1)
        card.body.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(bpilot_style.LabelRight("Font size:"))
        self._font_size = QtWidgets.QSpinBox()
        self._font_size.setRange(8, 24)
        self._font_size.setSuffix(" px")
        row.addWidget(self._font_size)
        row.addStretch(1)
        card.body.addLayout(row)

        for key, label in self._COLOR_FIELDS:
            row = QtWidgets.QHBoxLayout()
            row.addWidget(bpilot_style.LabelRight(label))
            btn = _ColorButton(settings.DEFAULTS[key])
            self._color_buttons[key] = btn
            row.addWidget(btn)
            row.addStretch(1)
            card.body.addLayout(row)
        return card

    def _preview_theme(self) -> None:
        """Selecting a theme previews its preset colors into the existing
        swatches -- it does not lock them; a user can still hand-tweak a
        swatch afterward for a custom mix (`values()` always reads back
        whatever the swatches currently show)."""
        theme = themes.THEMES[self._theme.currentData()]
        field_to_attr = {
            "user_bubble_bg": theme.user_bubble_bg,
            "user_text_color": theme.user_text,
            "assistant_bubble_bg": theme.assistant_bubble_bg,
            "assistant_text_color": theme.assistant_text,
            "panel_background": theme.panel,
        }
        for key, color in field_to_attr.items():
            btn = self._color_buttons[key]
            btn.color = color
            btn.refresh()

    def _build_advanced_card(self) -> QtWidgets.QWidget:
        card = bpilot_style.make_card("Advanced: Argo connection")

        row = QtWidgets.QHBoxLayout()
        row.addWidget(bpilot_style.LabelRight("Base URL:"))
        self._base_url = QtWidgets.QLineEdit()
        row.addWidget(self._base_url, 1)
        card.body.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(bpilot_style.LabelRight("API key:"))
        self._api_key = QtWidgets.QLineEdit()
        self._api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        row.addWidget(self._api_key, 1)
        card.body.addLayout(row)

        note = QtWidgets.QLabel(
            "Leave blank to use the ARGO_BASE_URL / ARGO_API_KEY environment variables."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {bpilot_style.MUTED}; font-size: {bpilot_style.px(10)}px;")
        card.body.addWidget(note)
        return card

    def _build_testing_card(self) -> QtWidgets.QWidget:
        card = bpilot_style.make_card("Testing (local only)")

        row = QtWidgets.QHBoxLayout()
        row.addWidget(bpilot_style.LabelRight("Catalog override:"))
        self._catalog_override = QtWidgets.QLineEdit()
        self._catalog_override.setPlaceholderText("e.g. hexm_test")
        self._catalog_override.setToolTip(
            "Overrides the active profile's configured databroker catalog for "
            "AutoPILOT's data-lookup tools only (search_runs / describe_run / "
            "read_run_data) -- does not affect scan drafting, and does not "
            "change the profile's own configuration. Also ignores the "
            "profile's databroker_uri. Leave blank on the real beamline."
        )
        row.addWidget(self._catalog_override, 1)
        card.body.addLayout(row)

        note = QtWidgets.QLabel(
            "Leave blank to use the active profile's configured catalog (the normal, "
            "correct setting on the real beamline)."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {bpilot_style.MUTED}; font-size: {bpilot_style.px(10)}px;")
        card.body.addWidget(note)
        return card

    def _build_buttons(self) -> QtWidgets.QLayout:
        row = QtWidgets.QHBoxLayout()
        restore_btn = QtWidgets.QPushButton("Restore Defaults")
        restore_btn.clicked.connect(lambda: self._load_from(settings.DEFAULTS))
        row.addWidget(restore_btn)
        row.addStretch(1)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)
        save_btn = bpilot_style.primary_btn("Save")
        save_btn.clicked.connect(self.accept)
        row.addWidget(save_btn)
        return row

    # ── Load / read back ────────────────────────────────────────────────

    def _load_from(self, values: dict) -> None:
        self._model.setCurrentText(values.get("model", ""))
        self._temperature.setValue(float(values.get("temperature", settings.DEFAULTS["temperature"])))
        self._show_raw.setChecked(bool(values.get("show_raw_output", False)))
        self._font_size.setValue(int(values.get("font_size", settings.DEFAULTS["font_size"])))
        # Block signals so setting the combo doesn't fire `_preview_theme` and
        # clobber the swatch colors the loop below is about to load explicitly.
        self._theme.blockSignals(True)
        idx = self._theme.findData(values.get("theme", settings.DEFAULTS["theme"]))
        self._theme.setCurrentIndex(idx if idx >= 0 else 0)
        self._theme.blockSignals(False)
        for key, btn in self._color_buttons.items():
            btn.color = values.get(key, settings.DEFAULTS[key])
            btn.refresh()
        self._base_url.setText(values.get("argo_base_url", ""))
        self._api_key.setText(values.get("argo_api_key", ""))
        self._catalog_override.setText(values.get("databroker_catalog_override", ""))

    def values(self) -> dict:
        result = {
            "model": self._model.currentText().strip(),
            "temperature": round(self._temperature.value(), 2),
            "show_raw_output": self._show_raw.isChecked(),
            "theme": self._theme.currentData(),
            "font_size": self._font_size.value(),
            "argo_base_url": self._base_url.text().strip(),
            "argo_api_key": self._api_key.text().strip(),
            "databroker_catalog_override": self._catalog_override.text().strip(),
        }
        for key, btn in self._color_buttons.items():
            result[key] = btn.color
        return result
