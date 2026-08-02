"""Persistent user preferences for the AutoPILOT chat dock.

Qt-free (like `llm_client.py`) even though only `gui/chat_panel.py` and
`gui/settings_dialog.py` use it today -- no reason to couple simple JSON I/O
to PyQt. Stored at the top of the AutoPILOT tree, gitignored (see
`.gitignore`), same "per-installation runtime state, not source" spirit as
B-PILOT's own `gui_config.json`.
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "autopilot_settings.json"

DEFAULTS: dict = {
    # Model & behavior
    "model": "",  # blank -> llm_client resolves ARGO_MODEL / DEFAULT_MODEL
    "temperature": 0.2,
    "show_raw_output": False,
    # Appearance -- colors default to the "Cyberpunk Neon" theme (see
    # `gui/themes.py`) so a fresh install already looks like a distinct AI
    # layer rather than blending into B-PILOT's own light theme. Switching
    # themes in Settings overwrites these fields with the chosen preset's
    # colors; `theme` records which preset is active for chat_panel.py's
    # dock-wide stylesheet/font/glow (not derivable from the colors alone).
    "theme": "cyberpunk_neon",
    "font_size": 12,
    "user_bubble_bg": "#1a1030",
    "user_text_color": "#ffd9fb",
    "assistant_bubble_bg": "#081820",
    "assistant_text_color": "#c8f8ff",
    "panel_background": "#10151f",
    # Advanced: Argo connection overrides (blank -> env vars / defaults)
    "argo_base_url": "",
    "argo_api_key": "",
    # Testing (local only): overrides the active profile's databroker_catalog
    # for AutoPILOT's data-lookup tools (search_runs/describe_run/
    # read_run_data) only -- never the profile config itself. Blank on the
    # real beamline; see data_catalog.py's _catalog_key().
    "databroker_catalog_override": "",
}


# The 5 fields that must stay coherent with each other for a theme to look
# intentional rather than mismatched (dark chrome from a new default theme
# next to leftover light bubbles from a pre-theme settings file, say).
_APPEARANCE_COLOR_KEYS = (
    "user_bubble_bg",
    "user_text_color",
    "assistant_bubble_bg",
    "assistant_text_color",
    "panel_background",
)


def load() -> dict:
    """`DEFAULTS` merged with whatever's on disk; tolerant of a missing or corrupt file.

    A file saved before the `theme` key existed carries colors picked for the
    old, single fixed (light) appearance -- keeping them would pair the new
    default dark theme's chrome with stale light bubbles. When `theme` is
    absent, the on-disk appearance colors are dropped so `DEFAULTS`' matching
    set applies instead; everything else on disk (model, temperature, Argo
    overrides, etc.) still carries forward unchanged.
    """
    values = dict(DEFAULTS)
    try:
        on_disk = json.loads(SETTINGS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return values
    if "theme" not in on_disk:
        on_disk = {k: v for k, v in on_disk.items() if k not in _APPEARANCE_COLOR_KEYS}
    values.update({k: v for k, v in on_disk.items() if k in DEFAULTS})
    return values


def save(values: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(values, indent=2, sort_keys=True))
