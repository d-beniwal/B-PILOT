"""Embedded live IPython console (out-of-process kernel) as a Qt panel.

Wraps qtconsole's :class:`RichJupyterWidget` on a kernel started in a *separate*
process (via :class:`QtKernelManager`), so:

* the GUI survives a plan crash / kernel death,
* the RunEngine (when the user loads it) lives in its own process,
* the kernel uses the SAME interpreter as the GUI, so ``import instrument``
  resolves.

**Persistence & reattach.** The kernel is a real separate process, so it can
outlive the GUI.  :meth:`start` records the kernel's *connection file* (and
saves it to config); on GUI close the session is **detached, not killed** (see
:meth:`close_session`, gated by the ``keep_kernel_on_exit`` config).  A later
GUI instance calls :meth:`attach` with that connection file to reconnect to the
same running kernel — including one with a plan still running in it.

The kernel is NOT started until :meth:`start` (or :meth:`attach`) is called.  A
fresh kernel starts PLAIN — nothing beamline runs automatically, so no
hardware/EPICS is touched.  Loading the Bluesky startup is an explicit user
action (:meth:`load_bluesky`).
"""
from __future__ import annotations

import json
import os
import sys

# qtconsole imports Qt through qtpy; pin the binding to PyQt5 before that happens.
os.environ.setdefault("QT_API", "pyqt5")

from PyQt5 import QtCore  # noqa: E402
from PyQt5 import QtWidgets  # noqa: E402
from qtconsole.manager import QtKernelManager  # noqa: E402
from qtconsole.rich_jupyter_widget import RichJupyterWidget  # noqa: E402

from . import config  # noqa: E402
from . import kernel_session as ks  # noqa: E402
from . import paths  # noqa: E402
from . import style as S  # noqa: E402

# Fallback startup import if config is unavailable.  The real command is
# user-configurable (Python → Configuration) — for MPE it defaults to
# 'from instrument.collection import *'.  Running it CONNECTS TO HARDWARE /
# EPICS, so it is only ever triggered by an explicit user action.
BLUESKY_STARTUP = "from instrument.collection import *"

_PLACEHOLDER = (
    "IPython session not started.\n\n"
    "Set a working directory above and click  ▶ Launch IPython."
)


class ConsolePanel(QtWidgets.QWidget):
    """A live IPython console backed by an out-of-process ipykernel."""

    started = QtCore.pyqtSignal()        # emitted once the kernel + widget are up
    ready = QtCore.pyqtSignal()          # kernel handshake done (safe to execute)
    executing = QtCore.pyqtSignal(object)  # a cell started (source)
    executed = QtCore.pyqtSignal(object)   # a cell finished (execute_reply msg)
    attach_failed = QtCore.pyqtSignal(str)  # attach() could not connect (reason)
    launch_blocked = QtCore.pyqtSignal(object)  # start() refused: kernel already running

    def __init__(self, parent=None, font_size: int = 11) -> None:
        """Build the placeholder view; the kernel starts later via :meth:`start`."""
        super().__init__(parent)
        self.kernel_manager: QtKernelManager | None = None
        self.kernel_client = None
        self.jupyter_widget: RichJupyterWidget | None = None
        self._font_size = font_size
        self._down = False
        self._busy = False
        self._ready = False
        self._attached = False               # True if reconnected to an existing kernel
        self._connection_file: str | None = None
        self._proc = None                    # Popen of a kernel we started (else None)
        self._log_file: str | None = None    # transcript file the recorder appends to

        self._stack = QtWidgets.QStackedWidget()
        self._placeholder = QtWidgets.QLabel(_PLACEHOLDER)
        self._placeholder.setAlignment(QtCore.Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet(f"color: {S.MUTED}; padding: 24px;")
        self._stack.addWidget(self._placeholder)   # index 0

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._stack)

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """True once a kernel (started OR attached) is connected and not torn down.

        Does not depend on ``kernel_manager.has_kernel`` — for an *attached*
        kernel this manager did not spawn the process, so ``has_kernel`` is False
        even though the session is live.
        """
        return (
            self.jupyter_widget is not None
            and not self._down
            and self.kernel_client is not None
        )

    def is_attached(self) -> bool:
        """True if this panel is reconnected to a pre-existing kernel."""
        return self._attached

    @property
    def connection_file(self) -> str | None:
        """Path to the current kernel's connection file (use it to reattach)."""
        return self._connection_file

    def start(self, cwd: str | None = None) -> None:
        """Ensure the beamline's ONE kernel is running, then connect to it.

        Process lifecycle + single-instance are delegated to
        :mod:`kernel_session` (which hosts the kernel in a named ``screen``
        session at a fixed per-beamline connection file, so it survives the GUI
        and is reattachable).  If a kernel is already running this emits
        :attr:`launch_blocked` (with its details) instead of starting a second
        one — the caller should offer to *attach*.
        """
        if self.jupyter_widget is not None:
            return  # this GUI already has a connection

        beamline = config.get("beamline")
        status, info = ks.launch(beamline, cwd or None)
        if status == "already_running":
            self.launch_blocked.emit(info or {})
            return
        if status != "started":
            self.attach_failed.emit(
                "Could not start kernel: " + str((info or {}).get("error", "unknown"))
            )
            return

        cf = info["connection_file"]
        self._attached = False
        # Start the detached transcript recorder so the full session is captured
        # from the first line (survives GUI restarts; readable while busy).
        self._log_file = info.get("log") or self._log_path_for(cf)
        self._start_recorder(cf, self._log_file)
        self._start_queue_runner()
        self._connect(cf)

    def default_connection_file(self) -> str:
        """The fixed per-beamline connection file (the default attach target)."""
        return ks.connection_file(config.get("beamline"))

    def attach(self, connection_file: str | None = None) -> bool:
        """Reconnect to the ALREADY-RUNNING kernel (default: this beamline's).

        The kernel keeps all of its state — including a plan still running in it;
        if it is mid-plan the readiness handshake completes once that plan
        finishes.  Returns True if the connection was set up, else emits
        :attr:`attach_failed` and returns False.
        """
        if self.jupyter_widget is not None:
            return False  # already connected

        cf = connection_file or self.default_connection_file()
        if not cf or not os.path.exists(cf):
            self.attach_failed.emit(
                f"No running kernel found — connection file missing:\n"
                f"{cf or '(none)'}"
            )
            return False
        try:  # a valid connection file is JSON with ports + key
            with open(cf, encoding="utf-8") as fh:
                json.load(fh)
        except Exception as exc:  # noqa: BLE001
            self.attach_failed.emit(f"Invalid connection file:\n{cf}\n\n{exc}")
            return False

        self._proc = None       # we did not spawn this one
        self._attached = True
        # Reuse the transcript for this kernel; if there is none yet (e.g. a
        # kernel not started by our GUI), begin recording from now on.
        self._log_file = self._log_path_for(cf)
        if not os.path.exists(self._log_file):
            self._start_recorder(cf, self._log_file)
        self._start_queue_runner()
        return self._connect(cf)

    # ── shared setup (start + attach) ────────────────────────────────────────────

    def _connect(self, cf: str) -> bool:
        """Wire a QtKernelClient to the connection file `cf` and show the widget."""
        km = QtKernelManager(connection_file=cf)
        try:
            km.load_connection_file()
            kc = km.client()
        except Exception as exc:  # noqa: BLE001
            self.attach_failed.emit(f"Could not connect to kernel:\n{exc}")
            return False

        # QtKernelClient channels need their ioloop, which start_channels() sets
        # up, before the widget can bind to them — so start channels first.
        try:
            kc.start_channels()
        except Exception as exc:  # noqa: BLE001
            self.attach_failed.emit(f"Could not connect to kernel:\n{exc}")
            return False

        self.kernel_manager = km
        self.kernel_client = kc
        self._connection_file = cf
        self._remember_connection_file(cf)

        self._begin_handshake()
        self._wire_widget(km, kc)
        self._down = False
        if self._attached:
            # qtconsole paints its banner/prompt only after a shell round-trip
            # (kernel_info + a silent execute for the prompt number).  A reattached
            # kernel that is BUSY (mid-plan) has a blocked shell channel, so it
            # would show a BLANK panel until it goes idle.  Write an explanatory
            # notice now; the real banner/prompt replaces it once the kernel frees.
            self._show_attach_notice()
        self.started.emit()
        return True

    def _show_attach_notice(self) -> None:
        """Write a 'reattached' notice into the widget so it is never blank."""
        jw = self.jupyter_widget
        if jw is None:
            return
        try:
            jw._append_plain_text(
                "[Reattached to a running kernel.\n"
                " If this panel looks idle, the kernel is BUSY running something;\n"
                " its output and the prompt will appear once it is free.]\n\n"
            )
        except Exception:  # noqa: BLE001
            pass

    def is_alive(self) -> bool:
        """Best-effort kernel liveness via the heartbeat (True even when BUSY).

        Lets callers tell an alive-but-busy reattached kernel (heartbeat beats
        while a plan runs) from one that has actually shut down.
        """
        kc = self.kernel_client
        if kc is None:
            return False
        try:
            return bool(kc.is_alive())
        except Exception:  # noqa: BLE001
            return False

    # ── Transcript recorder ──────────────────────────────────────────────────────

    @property
    def log_file(self) -> str | None:
        """Path to this session's transcript file (see :mod:`session_recorder`)."""
        return self._log_file

    @staticmethod
    def _log_path_for(cf: str) -> str:
        """Transcript path for a connection file (kernel-*.json -> kernel-*.log)."""
        return os.path.splitext(cf)[0] + ".log"

    @staticmethod
    def _start_recorder(cf: str, log_file: str) -> None:
        """Spawn the detached IOPub->file recorder for this kernel (best effort)."""
        import subprocess

        script = paths.SESSION_RECORDER
        try:
            subprocess.Popen(
                [sys.executable, script, cf, log_file],
                start_new_session=True,   # independent of the GUI, like the kernel
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _start_queue_runner() -> None:
        """Spawn the detached plan-queue runner for this beamline (best effort).

        The runner is a singleton (flock) — extra launches self-exit — so it is
        safe to call on every start/attach.  Run as a module so its relative
        imports resolve (cwd = the package parent).
        """
        import subprocess

        pkg_parent = paths.PKG_PARENT
        try:
            subprocess.Popen(
                [sys.executable, "-m", "gui_qt.queue_runner", config.get("beamline")],
                cwd=pkg_parent,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001
            pass

    def _begin_handshake(self) -> None:
        """Send kernel_info; mark ready when its reply arrives (dispatch gate).

        On a reattached *busy* kernel the reply only returns once the running
        cell finishes — which is precisely when dispatching is safe again — so
        the absence of a reply is never treated as a failure.
        """
        self._ready = False
        self.kernel_client.shell_channel.message_received.connect(self._on_shell_msg)
        self.kernel_client.kernel_info()

    def _wire_widget(self, km, kc) -> None:
        """Build the RichJupyterWidget on (km, kc) and show it."""
        jw = RichJupyterWidget()
        jw.kernel_manager = km
        jw.kernel_client = kc
        jw.confirm_restart = False

        # Show input/output from OTHER clients too (the detached queue runner,
        # or another attached GUI) so queued plans appear with their In [N]:
        # prompt + echoed command, exactly like manually-typed cells.
        jw.include_other_output = True
        try:
            jw.other_output_prefix = ""   # no "[remote] " prefix -> looks native
        except Exception:  # noqa: BLE001  (older qtconsole without the trait)
            pass

        # Track execution lifecycle so the scheduler can chain queued plans and
        # so we know when the kernel is busy.
        jw.executing.connect(self._on_jw_executing)
        jw.executed.connect(self._on_jw_executed)

        # Light styling to match the light Qt theme.
        jw.gui_completion = "droplist"
        jw.set_default_style("lightbg")
        jw.syntax_style = "default"
        jw.font_family = S.MONO_FAMILIES[0]
        jw.font_size = self._font_size
        jw.reset_font()

        self.jupyter_widget = jw
        self._stack.addWidget(jw)
        self._stack.setCurrentWidget(jw)

    @staticmethod
    def _remember_connection_file(cf: str | None) -> None:
        """Persist the current connection file so a later GUI can reattach."""
        try:
            config.update({"last_kernel_connection_file": cf or ""})
        except Exception:  # noqa: BLE001
            pass

    # ── Public API ────────────────────────────────────────────────────────────

    def run_code(self, src: str) -> None:
        """Run `src` in the console as if typed: echoes the input, shows output."""
        if not src or not src.strip() or self.jupyter_widget is None:
            return
        # RichJupyterWidget.execute() echoes the source AND sends it to the
        # kernel; kernel_client.execute() would not echo.
        self.jupyter_widget.execute(source=src, hidden=False)

    def is_busy(self) -> bool:
        """True while the kernel is executing a cell."""
        return self._busy

    def is_ready(self) -> bool:
        """True once the kernel handshake completed (safe to dispatch)."""
        return self._ready

    def _on_shell_msg(self, msg) -> None:
        if self._ready:
            return
        mtype = msg.get("msg_type") or msg.get("header", {}).get("msg_type")
        if mtype == "kernel_info_reply":
            self._ready = True
            self.ready.emit()

    def _on_jw_executing(self, source) -> None:
        self._busy = True
        self.executing.emit(source)

    def _on_jw_executed(self, msg) -> None:
        self._busy = False
        self.executed.emit(msg)

    def load_bluesky(self, command: str | None = None) -> None:
        """Run the configured Bluesky startup command(s) (CONNECTS TO HARDWARE).

        Uses `command` when given, else the ``bluesky_startup`` config value
        (Python → Configuration), falling back to :data:`BLUESKY_STARTUP`.
        """
        cmd = command if command is not None else config.get("bluesky_startup")
        if not cmd or not cmd.strip():
            cmd = BLUESKY_STARTUP
        self.run_code(cmd)

    def detach(self) -> None:
        """Disconnect the GUI but LEAVE the kernel running (so it can be reattached).

        Stops the client channels only; does not shut the kernel down and does
        not remove the connection file.  Idempotent.
        """
        if self._down:
            return
        self._down = True
        if self.kernel_client is not None:
            try:
                self.kernel_client.stop_channels()
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        """Stop channels AND terminate the kernel (ends the session).  Idempotent.

        Detaches our client, then ends the kernel process.  If we're connected to
        this beamline's managed session, :func:`kernel_session.stop` also quits
        the hosting ``screen`` session and clears the fixed connection file;
        otherwise we just request the specific kernel to exit.
        """
        cf = self._connection_file
        beamline = config.get("beamline")
        if self.kernel_client is not None:
            try:
                self.kernel_client.stop_channels()
            except Exception:  # noqa: BLE001
                pass
        try:
            if cf and cf == ks.connection_file(beamline):
                ks.stop(beamline)           # shutdown + quit screen + clean files
            elif cf:
                ks.shutdown_kernel(cf)      # arbitrary kernel: just request exit
        except Exception:  # noqa: BLE001
            pass
        if self._proc is not None:          # fallback path (screen unavailable)
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        self._down = True
        self._remember_connection_file("")   # session over — nothing to reattach

    def close_session(self) -> None:
        """On GUI close: keep the kernel alive (detach) or kill it, per config.

        Default (``keep_kernel_on_exit`` True) detaches, so the session survives
        and can be reattached next launch.
        """
        if config.get("keep_kernel_on_exit"):
            self.detach()
        else:
            self.shutdown()

    def reset_view(self) -> None:
        """Return to the placeholder so a new kernel can be started/attached.

        Use after :meth:`shutdown` when the GUI stays open (e.g. the user chose
        *Shutdown kernel* and wants to launch a fresh one without restarting).
        """
        jw = self.jupyter_widget
        if jw is not None:
            self._stack.removeWidget(jw)
            jw.deleteLater()
        self.jupyter_widget = None
        self.kernel_client = None
        self.kernel_manager = None
        self._proc = None
        self._attached = False
        self._ready = False
        self._busy = False
        self._down = False
        self._connection_file = None
        self._log_file = None
        self._stack.setCurrentWidget(self._placeholder)

    # ── Qt ────────────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        """On panel close, keep or kill the kernel per config (see close_session)."""
        self.close_session()
        super().closeEvent(event)
