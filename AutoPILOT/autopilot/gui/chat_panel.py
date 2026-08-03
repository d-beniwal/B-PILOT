"""B-PILOT chat dock: a natural-language front end over `autopilot.pipeline`.

Embedded via a guarded import -- see `gui_qt/autopilot_bridge.py` in B-PILOT.
The LLM call runs on a persistent background thread, mirroring
`gui_qt/viewer.py`'s `_CatalogWorker` (the one background-worker pattern in
this codebase) -- it must never block the Qt event loop.

Transcript bubbles and the input composer (`_ComposerBox`) reuse B-PILOT's
own palette (`gui_qt/style.py`) via the same `ensure_bpilot_on_path()`
convention already used by `device_catalog.py`/`plan_context.py`, so the dock
looks like part of the same application rather than a bolted-on widget.
Font size, bubble/panel colors, model, temperature, and the debug raw-output
toggle are all user-configurable via the gear button -> `settings_dialog`,
persisted through `autopilot.settings`.
"""
from __future__ import annotations

import json
import queue
import threading
from html import escape

from PyQt5 import QtCore, QtGui, QtWidgets

from .. import pipeline
from .. import plan_context
from .. import settings
from .._bpilot_path import ensure_bpilot_on_path
from ..llm_client import ArgoClient
from . import themes
from .settings_dialog import AutoPilotSettingsDialog

ensure_bpilot_on_path()

from gui_qt import style as bpilot_style  # noqa: E402


class _ChatWorker(QtCore.QObject):
    """Single persistent background thread that runs the plan-builder pipeline."""

    result_ready = QtCore.pyqtSignal(object)  # pipeline.PlanResult

    def __init__(self, initial_settings: dict) -> None:
        super().__init__()
        self.client: ArgoClient
        self.temperature: float | None
        self._history: list[dict] = []
        self.reconfigure(
            model=initial_settings["model"],
            base_url=initial_settings["argo_base_url"],
            api_key=initial_settings["argo_api_key"],
            temperature=initial_settings["temperature"],
        )
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def reconfigure(self, *, model: str, base_url: str, api_key: str, temperature: float | None) -> None:
        """Rebuild the Argo client from fresh settings (blank -> env var / built-in default).

        Only ever called from the GUI thread; `_run` (background thread) just
        reads `self.client`/`self.temperature` per request, so a request that's
        already in flight when settings change simply finishes under the old
        configuration -- no lock needed for that.
        """
        self.client = ArgoClient(base_url=base_url or None, api_key=api_key or None, model=model or None)
        self.temperature = temperature

    def reset_conversation(self) -> None:
        """Drop the running conversation history -- same thread-safety
        reasoning as `reconfigure`: a simple reference reassignment, safe to
        call from the GUI thread even while `_run` is mid-request (that
        request just finishes under the old history)."""
        self._history = []

    def _run(self) -> None:
        while True:
            request = self._queue.get()
            try:
                result, self._history = pipeline.converse(
                    request, history=self._history, client=self.client, temperature=self.temperature
                )
            except Exception as exc:  # noqa: BLE001 -- never let the worker thread die silently
                result = pipeline.PlanResult(
                    ok=False, message=f"Unexpected error: {exc}", model=self.client.model
                )
            self.result_ready.emit(result)

    def submit(self, request: str) -> None:
        self._queue.put(request)


class _ComposerBox(QtWidgets.QTextEdit):
    """Multiline input that grows with its content, up to a capped height.

    Enter sends the message (common chat-UI convention); Shift+Enter inserts
    a newline instead. Built on `QTextEdit` rather than `QPlainTextEdit` --
    with `setAcceptRichText(False)` it behaves like a plain-text box, but its
    `QTextDocument` reports a reliable `document().size()` once bound to the
    viewport width via `setTextWidth()`, which `QPlainTextEdit`'s lazier
    block layout does not.
    """

    send_requested = QtCore.pyqtSignal()

    _MIN_LINES = 1
    _MAX_LINES = 6

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Describe a scan... (Enter to send, Shift+Enter for a new line)")
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self._resize_to_fit)
        self._resize_to_fit()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not (
            event.modifiers() & QtCore.Qt.ShiftModifier
        ):
            self.send_requested.emit()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._resize_to_fit()

    def set_font_size(self, px: int) -> None:
        font = self.font()
        font.setPixelSize(px)
        self.setFont(font)
        self._resize_to_fit()

    def _resize_to_fit(self) -> None:
        self.document().setTextWidth(self.viewport().width())
        line_height = self.fontMetrics().lineSpacing()
        padding = 2 * self.frameWidth() + self.contentsMargins().top() + self.contentsMargins().bottom()
        padding += bpilot_style.px(8)  # room for the stylesheet's own field padding
        min_height = line_height * self._MIN_LINES + padding
        max_height = line_height * self._MAX_LINES + padding
        content_height = int(self.document().size().height()) + padding
        new_height = max(min_height, min(content_height, max_height))
        self.setFixedHeight(new_height)
        at_max = content_height > max_height
        self.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded if at_max else QtCore.Qt.ScrollBarAlwaysOff
        )


# Qt's rich-text engine has no flexbox and ignores CSS `max-width` on
# <table>/<div> (verified empirically), so a percentage-only bubble width
# grows with the dock and stops wrapping once the dock is wide enough to fit
# a whole message on one line. Capping at a literal pixel width keeps bubbles
# at a comfortable reading width regardless of dock/window size.
_BUBBLE_MAX_WIDTH_PX = 640


def _html_style_font_family(font_family: str) -> str:
    """`bpilot_style.MONO_CSS`/theme font stacks quote multi-word font names
    with `"` (correct CSS, and fine when used in a QSS stylesheet), but here
    they get spliced into an HTML `style="..."` attribute -- the embedded `"`
    closes that attribute early and corrupts the rest of the tag. Verified
    this silently drops `white-space:pre-wrap` on the debug `<pre>` block,
    so it falls back to `<pre>`'s true default (no wrap at all) -- the actual
    cause of "raw: {...}" rendering as one unbroken line. Single quotes are
    equally valid CSS for a quoted font name and don't collide with the
    surrounding double-quoted HTML attribute.
    """
    return font_family.replace('"', "'")


def _bubble_html(
    header: str,
    body_html: str,
    *,
    align: str,
    bg: str,
    border: str,
    fg: str,
    muted: str,
    font_px: int,
    container_width: int,
    font_family: str = "inherit",
) -> str:
    """One chat bubble as a fixed-width table (Qt rich text has no
    flexbox/border-radius, so alignment + background are faked this way).

    `border`/`muted` come from the active theme (not always B-PILOT's own
    palette) so a bubble's frame and caption stay legible against whichever
    theme's background/text colors are in use. `container_width` is the
    transcript viewport's current width in pixels, used to size the bubble to
    78% of it, capped at `_BUBBLE_MAX_WIDTH_PX` so wide/floating docks don't
    turn messages into one long unwrapped line.
    """
    header_px = max(8, font_px - 2)
    font_rule = "" if font_family == "inherit" else f"font-family:{_html_style_font_family(font_family)};"
    bubble_px = max(200, min(int(container_width * 0.78), _BUBBLE_MAX_WIDTH_PX))
    return (
        f'<table align="{align}" width="{bubble_px}" cellpadding="8" cellspacing="0" style="margin:4px 0;">'
        f"<tr><td style=\"background-color:{bg}; border:1px solid {border};\">"
        f'<div style="color:{muted}; font-size:{header_px}px; font-weight:bold; {font_rule}">{header}</div>'
        f'<div style="color:{fg}; font-size:{font_px}px; {font_rule}">{body_html}</div>'
        f"</td></tr></table>"
    )


class ChatDockWidget(QtWidgets.QDockWidget):
    """A minimal chat window: type a scan request, get a draft plan file (or,
    for a GUI-drivable template, fill B-PILOT's own plan-runner form)."""

    def __init__(self, plan_runner, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("AutoPILOT", parent)
        self.setObjectName("AutoPILOTChatDock")

        self._plan_runner = plan_runner
        # The latest `ok` result that can be pushed into the form -- cleared
        # on a new send/New Chat so "Open in form" can never act on a stale
        # proposal from an earlier turn.
        self._pending: pipeline.PlanResult | None = None
        # "Thinking" placeholder state -- see _start_thinking()/_stop_thinking().
        self._thinking_timer: QtCore.QTimer | None = None
        self._thinking_pos: int | None = None
        self._thinking_dots = 0

        self._settings = settings.load()
        self._worker = _ChatWorker(self._settings)
        self._worker.result_ready.connect(self._on_result)

        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)

        # Two rows so the header doesn't force the dock wider than the
        # transcript/composer need: prefixes + the buttons on row 0, the
        # actual model/catalog names (the variable-width part) on row 1,
        # each name under its own prefix.
        header_grid = QtWidgets.QGridLayout()
        header_grid.setContentsMargins(0, 0, 0, 0)
        header_grid.setHorizontalSpacing(8)
        header_grid.setVerticalSpacing(0)

        self._model_prefix = QtWidgets.QLabel("Model:")
        header_grid.addWidget(self._model_prefix, 0, 0)
        # Hidden unless a local testing catalog override is set (see
        # settings_dialog.py's "Testing (local only)" card) -- must stay
        # visible whenever active so a tester can't forget it's on.
        self._catalog_prefix = QtWidgets.QLabel("Catalog override:")
        self._catalog_prefix.setVisible(False)
        header_grid.addWidget(self._catalog_prefix, 0, 1)
        header_grid.setColumnStretch(2, 1)
        self._open_form_btn = QtWidgets.QPushButton("Open in form")
        self._open_form_btn.setToolTip(
            "Load the most recent proposed plan into B-PILOT's plan-runner form for review."
        )
        self._open_form_btn.setEnabled(False)
        self._open_form_btn.clicked.connect(self._on_open_in_form)
        header_grid.addWidget(self._open_form_btn, 0, 3)
        new_chat_btn = QtWidgets.QPushButton("New Chat")
        new_chat_btn.setToolTip("Clear the transcript and start a fresh conversation (no memory of prior turns).")
        new_chat_btn.clicked.connect(self._new_chat)
        header_grid.addWidget(new_chat_btn, 0, 4)
        settings_btn = QtWidgets.QPushButton("⚙")
        settings_btn.setFixedWidth(bpilot_style.px(28))
        settings_btn.setToolTip("AutoPILOT settings")
        settings_btn.clicked.connect(self._open_settings)
        header_grid.addWidget(settings_btn, 0, 5)

        # Color is set from the active theme in `_apply_settings` -- a
        # widget's own stylesheet otherwise wins over the dock-wide QSS's
        # `QLabel` rule, which would leave this one label stuck on
        # B-PILOT's muted grey regardless of theme.
        self._model_caption = QtWidgets.QLabel(self._worker.client.model)
        header_grid.addWidget(self._model_caption, 1, 0)
        self._catalog_override_caption = QtWidgets.QLabel()
        self._catalog_override_caption.setVisible(False)
        header_grid.addWidget(self._catalog_override_caption, 1, 1)
        layout.addLayout(header_grid)

        self._transcript = QtWidgets.QTextEdit()
        self._transcript.setReadOnly(True)
        self._transcript.setPlaceholderText(
            "Describe a scan in plain English, e.g. "
            "\"step scan samE from 0 to 10 mm in 21 steps, 1s exposure on pimega\"."
        )
        layout.addWidget(self._transcript, 1)

        row = QtWidgets.QHBoxLayout()
        self._input = _ComposerBox()
        self._input.send_requested.connect(self._on_send)
        row.addWidget(self._input, 1)
        self._send_btn = QtWidgets.QPushButton("Send")
        self._send_btn.clicked.connect(self._on_send)
        row.addWidget(self._send_btn, 0, QtCore.Qt.AlignBottom)
        layout.addLayout(row)

        self.setWidget(body)
        self._apply_settings()

    def _new_chat(self) -> None:
        # Stop (don't "remove-then-clear") -- _transcript.clear() below already
        # wipes the placeholder; a stale self._thinking_pos would otherwise
        # point past the now-empty document on the next tick.
        if self._thinking_timer is not None:
            self._thinking_timer.stop()
            self._thinking_timer = None
        self._thinking_pos = None
        self._worker.reset_conversation()
        self._transcript.clear()
        self._pending = None
        self._open_form_btn.setEnabled(False)

    def _open_settings(self) -> None:
        dlg = AutoPilotSettingsDialog(self._settings, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._settings = dlg.values()
            settings.save(self._settings)
            self._apply_settings()

    def _apply_settings(self) -> None:
        s = self._settings
        self._theme = themes.resolve(s.get("theme"))
        self._worker.reconfigure(
            model=s["model"], base_url=s["argo_base_url"], api_key=s["argo_api_key"], temperature=s["temperature"]
        )
        self._model_caption.setText(self._worker.client.model)
        self._model_prefix.setStyleSheet(f"color: {self._theme.muted}; font-size: 10px;")
        self._model_caption.setStyleSheet(f"color: {self._theme.muted}; font-size: 10px;")
        override = s.get("databroker_catalog_override", "")
        self._catalog_override_caption.setText(override)
        self._catalog_prefix.setStyleSheet(
            f"color: {self._theme.accent}; font-size: 10px; font-weight: bold;"
        )
        self._catalog_override_caption.setStyleSheet(
            f"color: {self._theme.accent}; font-size: 10px; font-weight: bold;"
        )
        self._catalog_prefix.setVisible(bool(override))
        self._catalog_override_caption.setVisible(bool(override))
        # Dock-scoped stylesheet: overrides B-PILOT's app-wide (light) QSS for
        # just this dock and its children -- see `themes.build_dock_stylesheet`.
        self.setStyleSheet(themes.build_dock_stylesheet(self._theme, s["font_size"]))
        self._input.set_font_size(s["font_size"])
        self._apply_theme_fonts()
        self._apply_theme_glow()

    def _apply_theme_fonts(self) -> None:
        """QSS `font-family` doesn't reliably repaint an already-constructed
        QTextDocument, so set the family directly on both text widgets too."""
        theme = self._theme
        if theme.font_family == "inherit":
            return
        first_family = theme.font_family.split(",")[0].strip().strip('"')
        for widget in (self._input, self._transcript):
            font = widget.font()
            font.setFamily(first_family)
            widget.setFont(font)

    def _apply_theme_glow(self) -> None:
        """Approximate a HUD "glowing focus" look for themes that call for
        it -- plain QSS has no box-shadow/glow, so this uses a real Qt
        graphics effect on the composer box instead."""
        if self._theme.glow:
            glow = QtWidgets.QGraphicsDropShadowEffect(self._input)
            glow.setColor(QtGui.QColor(self._theme.accent))
            glow.setBlurRadius(bpilot_style.px(18))
            glow.setOffset(0, 0)
            self._input.setGraphicsEffect(glow)
        else:
            self._input.setGraphicsEffect(None)

    def _append_user(self, text: str) -> None:
        s = self._settings
        body_html = escape(text).replace("\n", "<br>")
        html = _bubble_html(
            "You", body_html, align="right", bg=s["user_bubble_bg"], border=self._theme.border,
            fg=s["user_text_color"], muted=self._theme.muted, font_px=s["font_size"],
            container_width=self._transcript.viewport().width(), font_family=self._theme.font_family,
        )
        self._transcript.append(html)
        self._scroll_to_bottom()

    def _append_response(self, result: "pipeline.PlanResult") -> None:
        s = self._settings
        meta_bits = ["AutoPILOT"]
        if result.model:
            meta_bits.append(f"model: {escape(result.model)}")
        if result.tool_calls:
            trail = " -&gt; ".join(escape(name) for name in result.tool_calls)
            meta_bits.append(f"tools: {trail}")
        elif result.tool_name:
            meta_bits.append(f"tool: {escape(result.tool_name)}")
        if result.input_tokens is not None:
            tokens_bit = f"tokens: {result.input_tokens} in / {result.output_tokens} out"
            if result.cache_read_input_tokens:
                tokens_bit += f" (cached: {result.cache_read_input_tokens})"
            meta_bits.append(tokens_bit)
        header = " &middot; ".join(meta_bits)
        body_html = escape(result.message).replace("\n", "<br>")
        if s["show_raw_output"]:
            body_html += self._debug_block(result)
        html = _bubble_html(
            header, body_html, align="left", bg=s["assistant_bubble_bg"], border=self._theme.border,
            fg=s["assistant_text_color"], muted=self._theme.muted, font_px=s["font_size"],
            container_width=self._transcript.viewport().width(), font_family=self._theme.font_family,
        )
        self._transcript.append(html)
        self._scroll_to_bottom()

    def _debug_block(self, result: "pipeline.PlanResult") -> str:
        lines = []
        if result.raw_spec is not None:
            lines.append("raw: " + json.dumps(result.raw_spec))
        if result.clean_spec is not None and result.clean_spec != result.raw_spec:
            lines.append("validated: " + json.dumps(result.clean_spec))
        if not lines:
            return ""
        text = escape("\n".join(lines))
        debug_px = max(8, self._settings["font_size"] - 2)
        theme = self._theme
        font_family = bpilot_style.MONO_CSS if theme.font_family == "inherit" else theme.font_family
        font_family = _html_style_font_family(font_family)
        return (
            f'<pre style="background-color:{theme.panel}; border:1px solid {theme.border}; '
            f'font-family:{font_family}; font-size:{debug_px}px; padding:4px; '
            f'margin-top:4px; white-space:pre-wrap;">{text}</pre>'
        )

    def _append_note(self, text: str) -> None:
        """A short muted status line -- for 'Open in form' outcomes, not a
        full chat bubble (there's no model turn attached to these)."""
        html = f'<div style="color:{self._theme.muted}; font-size:{self._settings["font_size"] - 1}px;">{escape(text)}</div>'
        self._transcript.append(html)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        scrollbar = self._transcript.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _start_thinking(self) -> None:
        """Append an animated placeholder bubble while a request is in flight.

        `self._thinking_pos` marks the document offset right before the
        placeholder is appended -- `QTextEdit.append()` always appends at the
        true document end regardless of `textCursor()`, so re-rendering (or
        finally removing) the placeholder is just "delete from that saved
        offset to the current end, then append again."
        """
        self._thinking_dots = 0
        cursor = self._transcript.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self._thinking_pos = cursor.position()
        self._render_thinking()
        self._thinking_timer = QtCore.QTimer(self)
        self._thinking_timer.timeout.connect(self._tick_thinking)
        self._thinking_timer.start(450)

    def _tick_thinking(self) -> None:
        self._thinking_dots = (self._thinking_dots + 1) % 3
        self._render_thinking()

    def _render_thinking(self) -> None:
        self._clear_thinking_text()
        dots = "." * (1 + self._thinking_dots)
        html = (
            f'<div style="color:{self._theme.muted}; '
            f'font-size:{self._settings["font_size"] - 1}px;">AutoPILOT is thinking{dots}</div>'
        )
        self._transcript.append(html)
        self._scroll_to_bottom()

    def _clear_thinking_text(self) -> None:
        if self._thinking_pos is None:
            return
        cursor = self._transcript.textCursor()
        cursor.setPosition(self._thinking_pos)
        cursor.movePosition(QtGui.QTextCursor.End, QtGui.QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

    def _stop_thinking(self) -> None:
        if self._thinking_timer is not None:
            self._thinking_timer.stop()
            self._thinking_timer = None
        self._clear_thinking_text()
        self._thinking_pos = None

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._append_user(text)
        self._input.clear()
        self._input.setEnabled(False)
        self._send_btn.setEnabled(False)
        # A new turn in flight supersedes whatever the last turn proposed.
        self._pending = None
        self._open_form_btn.setEnabled(False)
        self._start_thinking()
        self._worker.submit(text)

    def _on_result(self, result: "pipeline.PlanResult") -> None:
        self._stop_thinking()
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._append_response(result)
        self._input.setFocus()
        self._pending = result if (result.ok and result.gui_command) else None
        self._open_form_btn.setEnabled(self._pending is not None)

    def _on_open_in_form(self) -> None:
        if self._pending is None or not self._pending.template_key:
            return
        template = plan_context.TEMPLATES.get(self._pending.template_key)
        if template is None or not template.gui_plan_name:
            return
        if not self._plan_runner.has_plan(template.gui_plan_name):
            self._append_note(
                f"'{template.gui_plan_name}' isn't in a currently-visible file -- "
                f"check '{template.gui_plan_file}' in the file browser (left panel), "
                "then click Open in form again."
            )
            return
        self._plan_runner.load_from_command(self._pending.gui_command)
        self._append_note(
            f"Opened {template.gui_plan_name} in the form -- review the fields, "
            "then Run or Add to Queue."
        )
