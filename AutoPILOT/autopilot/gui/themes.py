"""Switchable color themes for the AutoPILOT chat dock.

Unlike the rest of `autopilot/gui/`, this module intentionally does NOT
reuse `gui_qt.style` colors for its non-`classic` presets -- the whole
point of a theme here is to look distinct from (and deliberately contrast
with) B-PILOT's own light theme, so the dock reads as an AI layer rather
than blending into the host window. `classic` is the one preset that
*does* fall back to B-PILOT's palette, for anyone who wants the old
blend-in look back.

`build_dock_stylesheet()` returns a stylesheet scoped to
`QDockWidget#AutoPILOTChatDock` (the objectName set in `chat_panel.py`) so
it overrides B-PILOT's app-wide stylesheet for exactly this dock and its
children, without touching `gui_qt/style.py` or any global Qt state.
"""
from __future__ import annotations

from dataclasses import dataclass

from .._bpilot_path import ensure_bpilot_on_path

ensure_bpilot_on_path()

from gui_qt import style as bpilot_style  # noqa: E402


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    bg: str  # dock/background
    panel: str  # transcript background
    input_bg: str  # composer background
    border: str
    accent: str  # primary accent (borders, header, focus glow)
    accent2: str  # secondary accent (e.g. the "You" bubble accent)
    text: str
    muted: str
    user_bubble_bg: str
    user_text: str
    assistant_bubble_bg: str
    assistant_text: str
    font_family: str  # CSS font-family list, or the sentinel "inherit"
    glow: bool  # whether to apply a QGraphicsDropShadowEffect glow on the composer


THEMES: dict[str, Theme] = {
    "cyberpunk_neon": Theme(
        key="cyberpunk_neon",
        label="Cyberpunk Neon",
        bg="#0a0e14",
        panel="#10151f",
        input_bg="#0d1420",
        border="#1c2b38",
        accent="#00e5ff",
        accent2="#ff2fd0",
        text="#d8f5ff",
        muted="#5c7a8a",
        user_bubble_bg="#1a1030",
        user_text="#ffd9fb",
        assistant_bubble_bg="#081820",
        assistant_text="#c8f8ff",
        font_family=bpilot_style.MONO_CSS,
        glow=True,
    ),
    "matrix_terminal": Theme(
        key="matrix_terminal",
        label="Matrix Terminal",
        bg="#000000",
        panel="#050805",
        input_bg="#020402",
        border="#123312",
        accent="#39ff14",
        accent2="#39ff14",
        text="#c8ffc8",
        muted="#2f6b2f",
        user_bubble_bg="#062006",
        user_text="#a6ffa6",
        assistant_bubble_bg="#020a02",
        assistant_text="#c8ffc8",
        font_family=bpilot_style.MONO_CSS,
        glow=True,
    ),
    "sleek_monochrome": Theme(
        key="sleek_monochrome",
        label="Sleek Monochrome HUD",
        bg="#12161c",
        panel="#1a1f28",
        input_bg="#161b22",
        border="#2a3140",
        accent="#3fa9ff",
        accent2="#3fa9ff",
        text="#e6ecf2",
        muted="#6b7684",
        user_bubble_bg="#1c2735",
        user_text="#eaf4ff",
        assistant_bubble_bg="#181d25",
        assistant_text="#e6ecf2",
        font_family=bpilot_style.MONO_CSS,
        glow=False,
    ),
    "classic": Theme(
        key="classic",
        label="Classic (matches B-PILOT)",
        bg=bpilot_style.BG,
        panel=bpilot_style.PANEL,
        input_bg=bpilot_style.INPUT_BG,
        border=bpilot_style.BORDER,
        accent=bpilot_style.ACCENT,
        accent2=bpilot_style.ACCENT,
        text=bpilot_style.TEXT,
        muted=bpilot_style.MUTED,
        user_bubble_bg="#fff1e0",
        user_text="#1a1a1a",
        assistant_bubble_bg="#f6f6f6",
        assistant_text="#202020",
        font_family="inherit",  # sentinel: keep the app's default (non-monospace) font
        glow=False,
    ),
}

THEME_CHOICES: list[tuple[str, str]] = [(t.key, t.label) for t in THEMES.values()]

DEFAULT_THEME_KEY = "cyberpunk_neon"


def resolve(theme_key: str | None) -> Theme:
    """Look up a theme by key, falling back to the default for an unknown/missing key."""
    return THEMES.get(theme_key or "", THEMES[DEFAULT_THEME_KEY])


def build_dock_stylesheet(theme: Theme, font_size: int) -> str:
    """Full QSS for the AutoPILOT dock, scoped so it overrides B-PILOT's
    app-wide stylesheet only for this dock and its children."""
    font_rule = "" if theme.font_family == "inherit" else f"font-family: {theme.font_family};"
    sel = "QDockWidget#AutoPILOTChatDock"
    return f"""
    {sel} {{
        background: {theme.bg};
        color: {theme.text};
        {font_rule}
        font-size: {font_size}px;
    }}
    {sel} QWidget#AutoPILOTTitleBar {{
        background: {theme.panel};
        border-bottom: 1px solid {theme.accent};
    }}
    {sel} QLabel#AutoPILOTTitleLabel {{
        color: {theme.accent};
        font-weight: bold;
        letter-spacing: 1px;
    }}
    {sel} QLabel#AutoPILOTTitleSep {{
        color: {theme.muted};
    }}
    {sel} QLabel#AutoPILOTTitleModelLabel {{
        color: {theme.accent2};
        font-weight: normal;
    }}
    {sel} QWidget {{
        background: {theme.bg};
        color: {theme.text};
        {font_rule}
    }}
    {sel} QLabel {{
        background: transparent;
        color: {theme.muted};
    }}
    {sel} QPushButton {{
        background: {theme.panel};
        color: {theme.accent};
        border: 1px solid {theme.accent};
        border-radius: 3px;
        padding: 4px 10px;
    }}
    {sel} QPushButton:hover {{
        background: {theme.accent};
        color: {theme.bg};
    }}
    {sel} QPushButton:pressed {{
        background: {theme.accent2};
        border-color: {theme.accent2};
    }}
    {sel} QPushButton#OpenInFormButton:disabled {{
        background: {theme.panel};
        color: {theme.muted};
        border: 1px solid {theme.border};
    }}
    {sel} QPushButton#OpenInFormButton:enabled {{
        background: {theme.accent};
        color: {theme.bg};
        border: 1px solid {theme.accent};
        font-weight: bold;
    }}
    {sel} QPushButton#OpenInFormButton:enabled:hover {{
        background: {theme.accent2};
        border-color: {theme.accent2};
    }}
    {sel} QTextEdit {{
        background: {theme.panel};
        color: {theme.text};
        border: 1px solid {theme.border};
        border-radius: 3px;
        selection-background-color: {theme.accent};
        selection-color: {theme.bg};
    }}
    {sel} QTextEdit:focus {{
        border: 1px solid {theme.accent};
    }}
    {sel} QScrollBar:vertical {{
        background: {theme.panel};
        width: 10px;
        margin: 0;
    }}
    {sel} QScrollBar::handle:vertical {{
        background: {theme.border};
        border-radius: 4px;
        min-height: 24px;
    }}
    {sel} QScrollBar::handle:vertical:hover {{
        background: {theme.accent};
    }}
    {sel} QScrollBar::add-line:vertical, {sel} QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    """
