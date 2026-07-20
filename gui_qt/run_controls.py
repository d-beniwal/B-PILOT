"""Run controls shown below the console: Stop run, recovery actions, Shutdown.

Maps the Bluesky RunEngine interrupt/recovery model onto buttons:

* **Stop run** — *click* = deferred pause (one Ctrl+C, stops at the next
  checkpoint); *press-and-hold >1 s* = immediate pause (double Ctrl+C).  Both are
  delivered as SIGINT(s) to the kernel via :func:`kernel_session.interrupt`.
* After a pause, the RunEngine's **four** recovery options appear as temporary
  buttons — ``RE.resume()`` / ``RE.stop()`` / ``RE.abort()`` / ``RE.halt()`` —
  sent to the console.  They hide again once one is chosen.
* **Shut down kernel** — delegates to the caller's handler (which confirms and
  warns that a killed kernel cannot be resumed).
"""
from __future__ import annotations

from PyQt5 import QtCore
from PyQt5 import QtWidgets

from . import config
from . import kernel_session as ks
from . import style as S

_HOLD_MS = 1000  # press-and-hold threshold for a hard (immediate) halt

_STOP_QSS = (
    "QPushButton{background:#c62828;color:white;font-weight:bold;"
    "border:1px solid #8e1c1c;border-radius:4px;padding:5px 12px;}"
    "QPushButton:hover{background:#b71c1c;}"
    "QPushButton:pressed{background:#8e1c1c;}"
    "QPushButton:disabled{background:#e0e0e0;color:#9a9a9a;border-color:#cccccc;}"
)


class RunControlBar(QtWidgets.QWidget):
    """Stop-run (soft/hard), RunEngine recovery actions, and Shutdown kernel."""

    def __init__(self, console, on_shutdown, parent=None) -> None:
        """`console` drives RE commands; `on_shutdown` is called for Shutdown."""
        super().__init__(parent)
        self._console = console
        self._on_shutdown = on_shutdown
        self._held = False

        self._hold_timer = QtCore.QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(_HOLD_MS)
        self._hold_timer.timeout.connect(self._on_hold_elapsed)

        self._build_ui()
        self.set_console_ready(False)

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        self._stop_btn = QtWidgets.QPushButton("■  Stop run")
        self._stop_btn.setStyleSheet(_STOP_QSS)
        self._stop_btn.setMinimumHeight(30)
        self._stop_btn.setToolTip(
            "Click = pause at the next checkpoint (Ctrl+C once).\n"
            "Press and hold >1 s = pause immediately (Ctrl+C twice)."
        )
        self._stop_btn.pressed.connect(self._on_pressed)
        self._stop_btn.released.connect(self._on_released)
        row.addWidget(self._stop_btn)

        self._hint = QtWidgets.QLabel("")
        self._hint.setStyleSheet(f"color: {S.MUTED};")
        row.addWidget(self._hint, 1)

        self._shutdown_btn = QtWidgets.QPushButton("Shut down kernel")
        self._shutdown_btn.setToolTip(
            "Terminate the kernel. A killed kernel cannot be resumed later."
        )
        self._shutdown_btn.clicked.connect(self._shutdown)
        row.addWidget(self._shutdown_btn)
        outer.addLayout(row)

        # Temporary recovery actions (hidden until a pause is requested).
        self._recovery = QtWidgets.QFrame()
        self._recovery.setObjectName("toolbar")
        rlay = QtWidgets.QHBoxLayout(self._recovery)
        rlay.setContentsMargins(8, 4, 8, 4)
        rlay.setSpacing(6)
        rlay.addWidget(QtWidgets.QLabel("Run paused →"))
        self._resume_btn = self._recovery_btn(
            "Resume", "RE.resume()", "Continue the plan from where it paused."
        )
        self._stop_re_btn = self._recovery_btn(
            "Stop", "RE.stop()",
            "End now, run cleanup, mark the run SUCCESSFUL."
        )
        self._abort_btn = self._recovery_btn(
            "Abort", "RE.abort()",
            "End now, run cleanup, mark the run ABORTED."
        )
        self._halt_btn = self._recovery_btn(
            "Halt", "RE.halt()",
            "End now WITHOUT running cleanup handlers."
        )
        for b in (self._resume_btn, self._stop_re_btn, self._abort_btn, self._halt_btn):
            rlay.addWidget(b)
        rlay.addStretch(1)
        self._recovery.setVisible(False)
        outer.addWidget(self._recovery)

    def _recovery_btn(self, label: str, command: str, tip: str) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(label)
        btn.setToolTip(f"{tip}\n\nRuns: {command}")
        btn.clicked.connect(lambda: self._recover(command))
        return btn

    # ── Enable/disable with the console ─────────────────────────────────────────

    def set_console_ready(self, ready: bool) -> None:
        """Enable the controls only when a kernel is connected."""
        self._stop_btn.setEnabled(ready)
        self._shutdown_btn.setEnabled(ready)
        if not ready:
            self._hide_recovery()

    # ── Stop run: click = soft, hold = hard ─────────────────────────────────────

    def _on_pressed(self) -> None:
        if not self._console.is_running():
            return
        self._held = False
        self._hold_timer.start()

    def _on_hold_elapsed(self) -> None:
        # Held long enough → immediate (hard) pause.
        self._held = True
        self._interrupt(hard=True)

    def _on_released(self) -> None:
        self._hold_timer.stop()
        if self._held:
            return  # hard halt already fired on hold
        if self._console.is_running():
            self._interrupt(hard=False)

    def _interrupt(self, hard: bool) -> None:
        ok = ks.interrupt(config.get("beamline"), hard=hard)
        if not ok:
            self._hint.setText("Could not signal the kernel.")
            return
        self._show_recovery(immediate=hard)

    # ── Recovery ────────────────────────────────────────────────────────────────

    def _show_recovery(self, immediate: bool) -> None:
        self._hint.setText(
            "Pausing immediately… choose an action once paused."
            if immediate else
            "Pausing at next checkpoint… choose an action once paused."
        )
        self._recovery.setVisible(True)

    def _hide_recovery(self) -> None:
        self._recovery.setVisible(False)
        self._hint.setText("")

    def _recover(self, command: str) -> None:
        # Send the RE.* command to the console (echoes + runs), then hide the bar.
        if self._console.is_running():
            self._console.run_code(command)
        self._hide_recovery()

    # ── Shutdown ────────────────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        self._hide_recovery()
        if callable(self._on_shutdown):
            self._on_shutdown()
