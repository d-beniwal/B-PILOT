"""Light theme + layout helpers for the Qt plan-runner GUI.

A clean light theme (soft-grey panels, dark text, orange accent, white input
fields) plus small layout helpers.  Self-contained: the checkmark/arrow SVG
helpers are copied in so this GUI does not depend on the midas package.
"""
# ruff: noqa: E501  (the QSS block below has long, readable one-line rules)
from __future__ import annotations

import atexit
import os
import tempfile

from PyQt5 import QtCore
from PyQt5 import QtWidgets

# ── Palette (light) ───────────────────────────────────────────────────────────
BG        = "#e9e9e9"   # window background
PANEL     = "#f6f6f6"   # raised card background
INPUT_BG  = "#ffffff"   # white input fields
INPUT_FG  = "#1a1a1a"
TEXT      = "#202020"   # primary (dark) text
MUTED     = "#6f6f6f"
BORDER    = "#bcbcbc"
HOVER     = "#8f8f8f"
ACCENT    = "#ff7800"   # orange accent (selected / checked / primary)
ACCENT_D  = "#c85e00"
ERROR     = "#c62828"   # invalid-field border

# Command-preview syntax colours.
CMD_IMPORT = "#1565c0"  # the "from ... import ..." line (blue)
CMD_RE     = "#2e7d32"  # the "RE(...)" line (green)

# Fixed-width font stack. Naming real per-platform families (rather than the
# generic "monospace") lets Qt resolve immediately.
MONO_FAMILIES = ["Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"]
MONO_CSS = ", ".join(f'"{f}"' if " " in f else f for f in MONO_FAMILIES)


# ── SVG glyphs for QSS sub-controls ────────────────────────────────────────────

def _make_checkmark_svg() -> str:
    """White tick SVG → temp file.  Returns forward-slash path for Qt QSS."""
    svg = (
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'>"
        b"<polyline points='2,7 5.5,11 12,3' stroke='white' stroke-width='2.2'"
        b" fill='none' stroke-linecap='round' stroke-linejoin='round'/>"
        b"</svg>"
    )
    f = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
    f.write(svg)
    f.close()
    atexit.register(os.unlink, f.name)
    return f.name.replace("\\", "/")


def _make_arrow_svg(direction: str = "down", color: str = "#444444") -> str:
    """Small filled triangle arrow → temp file, for spinbox/combo sub-controls."""
    pts = "2,7 8,7 5,2" if direction == "up" else "2,3 8,3 5,8"
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'>"
        f"<polygon points='{pts}' fill='{color}'/></svg>"
    ).encode()
    f = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
    f.write(svg)
    f.close()
    atexit.register(os.unlink, f.name)
    return f.name.replace("\\", "/")


def stylesheet(checkmark_svg: str, up_arrow_svg: str = "", down_arrow_svg: str = "") -> str:
    """Return the full application QSS (light theme)."""
    return f"""
    QWidget {{ color: {TEXT}; font-size: 12px; }}
    QMainWindow, QScrollArea, QSplitter {{ background: {BG}; }}
    QScrollArea {{ border: none; }}
    QToolTip {{ background: #fffbe6; color: {TEXT}; border: 1px solid {BORDER}; padding: 3px; }}

    /* ── Context menus ─────────────────────────────────────────── */
    QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
    QMenu::item {{ padding: 4px 22px; background: transparent; }}
    QMenu::item:selected {{ background: {ACCENT}; color: white; }}
    QMenu::item:disabled {{ color: {MUTED}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 6px; }}
    QMenuBar {{ background: {BG}; color: {TEXT}; }}
    QMenuBar::item:selected {{ background: {ACCENT}; color: white; }}

    /* ── Top toolbar ───────────────────────────────────────────── */
    QFrame#toolbar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}

    /* ── Section cards ─────────────────────────────────────────── */
    QGroupBox {{
        border: 1px solid {BORDER};
        border-radius: 5px;
        margin-top: 9px;
        padding: 6px 4px 4px 4px;
        background: {PANEL};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 4px;
        color: #3a3a3a;
        font-weight: bold;
    }}
    QGroupBox::indicator {{ width: 14px; height: 14px; }}

    /* ── Buttons ──────────────────────────────────────────────── */
    QPushButton {{
        color: {TEXT};
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fdfdfd, stop:1 #e6e6e6);
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 4px 10px;
        min-height: 18px;
    }}
    QPushButton:hover {{ border: 1px solid {HOVER}; }}
    QPushButton:pressed {{ background: #dcdcdc; }}
    QPushButton:disabled {{ color: {MUTED}; background: #ededed; border-color: #d0d0d0; }}
    QPushButton#primary {{
        color: white; font-weight: bold;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT_D});
        border: 1px solid {ACCENT_D};
    }}
    QPushButton#primary:hover {{ border: 1px solid #7a3a00; }}
    QPushButton#primary:disabled {{ background: #d9d9d9; color: {MUTED}; border-color: #cccccc; }}

    /* ── Inputs (white fields) ────────────────────────────────── */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {INPUT_BG}; color: {INPUT_FG};
        border: 1px solid {BORDER}; border-radius: 3px;
        selection-background-color: {ACCENT}; selection-color: white;
        min-height: 18px; padding: 1px 3px;
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        background: #e6e6e6; color: #9a9a9a;
    }}
    /* Invalid fields (datatype / required check) get a red border. */
    QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"], QComboBox[invalid="true"] {{
        border: 2px solid {ERROR};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding; subcontrol-position: center right;
        width: 18px; border-left: 1px solid {BORDER};
    }}
    QComboBox::down-arrow {{ image: url({down_arrow_svg}); width: 9px; height: 9px; }}
    QComboBox QAbstractItemView {{
        background: {INPUT_BG}; color: {INPUT_FG};
        selection-background-color: {ACCENT}; selection-color: white;
        border: 1px solid {BORDER}; outline: 0;
    }}
    QComboBox:disabled {{ background: #e6e6e6; }}

    /* Monospace boxes (command display, positions, notes). */
    QPlainTextEdit#mono, QTextEdit#mono {{
        background: #fbfbfb; color: {INPUT_FG}; font-family: {MONO_CSS};
    }}

    /* ── Checkboxes / radios (orange when on) ─────────────────── */
    QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 6px; background: transparent; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px; height: 14px;
        border: 1px solid #9a9a9a; background: #ffffff;
    }}
    QCheckBox::indicator {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 7px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
    QCheckBox::indicator:checked {{
        background: {ACCENT}; border-color: {ACCENT}; image: url({checkmark_svg});
    }}
    QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

    /* ── Item views (lists, trees, file dialogs) ──────────────── */
    QTreeView, QListView, QListWidget, QColumnView, QTableView {{
        background: {INPUT_BG}; color: {INPUT_FG};
        alternate-background-color: #f2f2f2;
        selection-background-color: {ACCENT}; selection-color: white;
        border: 1px solid {BORDER}; outline: 0;
    }}
    QListWidget::item:hover, QTreeView::item:hover {{ background: #f0e0d0; }}
    QListWidget::item:selected, QTreeView::item:selected {{
        background: {ACCENT}; color: white;
    }}

    /* ── Scrollbars / splitter / status bar ───────────────────── */
    QScrollBar:vertical {{ background: #e2e2e2; width: 11px; margin: 0; border: none; }}
    QScrollBar::handle:vertical {{ background: #bcbcbc; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: #a2a2a2; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QScrollBar:horizontal {{ background: #e2e2e2; height: 11px; margin: 0; border: none; }}
    QScrollBar::handle:horizontal {{ background: #bcbcbc; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QSplitter::handle {{ background: #d3d3d3; }}
    QSplitter::handle:hover {{ background: {BORDER}; }}
    QStatusBar {{ color: {MUTED}; }}
    """


# ── Layout helpers ──────────────────────────────────────────────────────────────

class LabelRight(QtWidgets.QLabel):
    """Right-aligned, vertically-centred label (Dioptas form style)."""

    def __init__(self, text="", parent=None):
        """Create the label and set right / vertical-centre alignment."""
        super().__init__(text, parent)
        self.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)


def hline() -> QtWidgets.QFrame:
    """A thin horizontal separator line in the border colour."""
    f = QtWidgets.QFrame()
    f.setFrameShape(QtWidgets.QFrame.HLine)
    f.setFrameShadow(QtWidgets.QFrame.Plain)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{BORDER}; border:none;")
    return f


def make_card(title: str) -> QtWidgets.QGroupBox:
    """Return a styled section card (QGroupBox) with a tight QVBoxLayout body.

    Use ``card.body`` to add widgets/layouts.
    """
    gb = QtWidgets.QGroupBox(title)
    body = QtWidgets.QVBoxLayout(gb)
    body.setContentsMargins(8, 6, 8, 6)
    body.setSpacing(5)
    gb.body = body          # type: ignore[attr-defined]
    return gb


def primary_btn(text: str) -> QtWidgets.QPushButton:
    """A prominent accent (orange) action button."""
    b = QtWidgets.QPushButton(text)
    b.setObjectName("primary")
    b.setMinimumHeight(32)
    return b


class NoScrollComboBox(QtWidgets.QComboBox):
    """QComboBox that ignores mouse-wheel scrolls so the selection never changes
    by accident.

    The ignored event propagates to the parent (the scroll panel scrolls
    instead), and only clicks / keyboard change the value.  The drop-down popup
    still scrolls normally while it is open.
    """

    def wheelEvent(self, e) -> None:  # noqa: N802
        """Ignore the wheel so it scrolls the panel, not the selection."""
        e.ignore()


class HoverTip(QtCore.QObject):
    """Custom hover tooltip (like the tk GUI's ``_Tooltip``).

    Shows a small frameless popup immediately when the mouse enters `widget`,
    so it does not depend on the native ``QToolTip`` mechanism (which can be
    flaky under an application stylesheet).  Parented to `widget`, so it lives
    exactly as long as the widget.
    """

    def __init__(self, widget: QtWidgets.QWidget, text: str) -> None:
        """Install an event filter on `widget` to show `text` on hover."""
        super().__init__(widget)
        self._w = widget
        self._text = text
        self._tip: QtWidgets.QLabel | None = None
        widget.setMouseTracking(True)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """Show the popup on Enter; hide it on Leave / hide / focus-out."""
        et = event.type()
        if et == QtCore.QEvent.Enter:
            self._show()
        elif et in (
            QtCore.QEvent.Leave,
            QtCore.QEvent.Hide,
            QtCore.QEvent.FocusOut,
            QtCore.QEvent.Wheel,
        ):
            self._hide()
        return False

    def _show(self) -> None:
        if self._tip is not None or not self._text:
            return
        pos = self._w.mapToGlobal(QtCore.QPoint(0, self._w.height() + 4))
        tip = QtWidgets.QLabel(self._text, None, QtCore.Qt.ToolTip)
        tip.setWordWrap(True)
        tip.setMaximumWidth(440)
        tip.setStyleSheet(
            f"background: #fffbe6; color: {TEXT}; "
            f"border: 1px solid {BORDER}; padding: 5px;"
        )
        tip.move(pos)
        tip.show()
        self._tip = tip

    def _hide(self) -> None:
        if self._tip is not None:
            self._tip.hide()
            self._tip.deleteLater()
            self._tip = None


def mark_invalid(widget: QtWidgets.QWidget, invalid: bool) -> None:
    """Toggle the red ``invalid`` border on a field and repolish it."""
    if widget.property("invalid") == invalid:
        return
    widget.setProperty("invalid", invalid)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def apply_theme(app: QtWidgets.QApplication) -> None:
    """Set Fusion + the light palette + the application stylesheet on `app`."""
    from PyQt5 import QtGui

    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    for role, col in [
        (QtGui.QPalette.Window,          BG),
        (QtGui.QPalette.WindowText,      TEXT),
        (QtGui.QPalette.Base,            INPUT_BG),
        (QtGui.QPalette.AlternateBase,   "#f0f0f0"),
        (QtGui.QPalette.Text,            INPUT_FG),
        (QtGui.QPalette.Button,          "#e6e6e6"),
        (QtGui.QPalette.ButtonText,      TEXT),
        (QtGui.QPalette.Highlight,       ACCENT),
        (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ToolTipBase,     "#fffbe6"),
        (QtGui.QPalette.ToolTipText,     TEXT),
    ]:
        pal.setColor(role, QtGui.QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(
        stylesheet(_make_checkmark_svg(), _make_arrow_svg("up"), _make_arrow_svg("down"))
    )
