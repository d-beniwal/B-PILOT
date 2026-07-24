"""BEAMMODE / TESTMODE toggle buttons, shown in the run-controls button row.

``BEAMMODE`` and ``TESTMODE`` are in-memory ophyd ``Signal``s (see
``instrument/devices/global_variables.py``) that many plans branch on —
e.g. whether to monitor for beam, or which suspenders to arm. Both buttons:

* poll ``NAME.get()`` **silently** (no console echo, no history entry) via
  :meth:`ConsolePanel.query_values`, colouring green/red/gray for
  True/False/unknown (unknown = not yet imported into the kernel, or the
  console isn't running),
* toggle the value by running ``NAME.put(not current)`` **visibly** in the
  console on click, per spec — the toggle itself is a real, echoed command,
  only the background status poll is silent.
"""
from __future__ import annotations

from PyQt5 import QtCore
from PyQt5 import QtWidgets

from . import style as S

_POLL_MS = 2800

_ON_QSS = (
    f"QPushButton{{background:{S.CMD_RE};color:white;font-weight:bold;"
    "border-radius:4px;padding:5px 12px;}"
    "QPushButton:disabled{background:#e0e0e0;color:#9a9a9a;}"
)
_OFF_QSS = (
    f"QPushButton{{background:{S.ERROR};color:white;font-weight:bold;"
    "border-radius:4px;padding:5px 12px;}"
    "QPushButton:disabled{background:#e0e0e0;color:#9a9a9a;}"
)
_UNKNOWN_QSS = (
    f"QPushButton{{background:{S.MUTED};color:white;font-weight:bold;"
    "border-radius:4px;padding:5px 12px;}"
    "QPushButton:disabled{background:#e0e0e0;color:#9a9a9a;}"
)

_NAMES = ("BEAMMODE", "TESTMODE")


class ModeButtonBar(QtWidgets.QWidget):
    """Two colour-coded toggle buttons for BEAMMODE / TESTMODE."""

    def __init__(self, console, parent=None) -> None:
        """`console` is the ConsolePanel used for both the silent poll and the
        visible put() toggle."""
        super().__init__(parent)
        self._console = console
        self._values: dict[str, bool | None] = {n: None for n in _NAMES}
        self._poll_inflight = False

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._buttons: dict[str, QtWidgets.QPushButton] = {}
        for name in _NAMES:
            btn = QtWidgets.QPushButton(name)
            btn.setToolTip(
                f"Click to toggle {name} (runs {name}.put(...) in the console).\n"
                f"Green = True, red = False, gray = unknown (not yet loaded, or "
                f"the console isn't running)."
            )
            btn.clicked.connect(lambda _checked, n=name: self._on_clicked(n))
            lay.addWidget(btn)
            self._buttons[name] = btn

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)

        self.set_console_ready(False)

    # ── Console-readiness (set by the main window) ──────────────────────────────

    def set_console_ready(self, ready: bool) -> None:
        """Enable polling/clicking only while a kernel is connected."""
        for btn in self._buttons.values():
            btn.setEnabled(ready)
        if ready:
            self._timer.start()
            self._poll()
        else:
            self._timer.stop()
            self._poll_inflight = False
            for name in _NAMES:
                self._apply_state(name, None)

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if self._poll_inflight or not self._console.is_running():
            return
        self._poll_inflight = True
        self._console.query_values(
            {
                "beammode": "bool(BEAMMODE.get())",
                "testmode": "bool(TESTMODE.get())",
            },
            self._on_status,
        )

    def _on_status(self, result: dict) -> None:
        self._poll_inflight = False
        self._apply_state("BEAMMODE", result.get("beammode"))
        self._apply_state("TESTMODE", result.get("testmode"))

    def _apply_state(self, name: str, value) -> None:
        self._values[name] = value
        btn = self._buttons[name]
        btn.setStyleSheet(_ON_QSS if value else (_OFF_QSS if value is False else _UNKNOWN_QSS))

    # ── Toggling ──────────────────────────────────────────────────────────────

    def _on_clicked(self, name: str) -> None:
        current = self._values.get(name)
        new_val = not current if current is not None else True
        self._console.run_code(f"{name}.put({new_val})")
        QtCore.QTimer.singleShot(500, self._poll)  # fast follow-up refresh
