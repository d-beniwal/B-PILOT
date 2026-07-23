"""Main window: toolbar + plan-runner (left) + console / notes (right)."""
from __future__ import annotations

import os
import subprocess
import sys

from PyQt5 import QtCore
from PyQt5 import QtWidgets

from . import autopilot_bridge
from . import config
from . import device_source
from . import paths
from . import style as S
from .console_panel import ConsolePanel
from .plan_runner import PlanRunnerPanel
from .queue_panel import QueuePanel
from .run_controls import RunControlBar
from .session_log import SessionLogView

# Default work dir (kernel cwd): the project root, so a launched kernel's
# ``from instrument.collection import *`` resolves regardless of where the GUI
# was started from.  Editable in the toolbar.
_DEFAULT_LAUNCH_DIR = paths.PROJECT_ROOT


class MainWindow(QtWidgets.QMainWindow):
    """QMainWindow hosting the plan runner, the embedded console, and run notes."""

    def __init__(self) -> None:
        """Build the toolbar + split layout and wire the console lifecycle."""
        super().__init__()
        self.setWindowTitle("MPE Bluesky Plan Runner (Qt)")
        self.resize(S.px(1500), S.px(900))
        self.setMinimumSize(S.px(980), S.px(600))

        self.runner = PlanRunnerPanel()
        self.console = ConsolePanel()
        self.session_log = SessionLogView()
        self.run_controls = RunControlBar(self.console, self._shutdown_kernel)
        self.queue = QueuePanel(self.console)
        self.runner.runRequested.connect(self._on_run)
        self.runner.queueRequested.connect(self._on_queue)
        self.console.started.connect(self._on_console_started)
        self.console.ready.connect(self._on_console_ready)
        self.console.attach_failed.connect(self._on_attach_failed)
        self.console.launch_blocked.connect(self._on_launch_blocked)

        central = QtWidgets.QWidget()
        clay = QtWidgets.QVBoxLayout(central)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(0)
        clay.addWidget(self._build_toolbar())
        clay.addWidget(self._build_script_params_row())

        main_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_split.addWidget(self.runner)
        main_split.addWidget(self._build_right_panel())
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([840, 620])
        clay.addWidget(main_split, 1)

        self.setCentralWidget(central)

        # Optional AutoPILOT chat dock -- absent entirely if AutoPILOT/ isn't
        # there or its deps aren't installed (see gui_qt/autopilot_bridge.py).
        if autopilot_bridge.AVAILABLE:
            self.autopilot_chat = autopilot_bridge.ChatDockWidget(self)
            self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.autopilot_chat)

        self._build_menu()
        self._apply_launch_mode()   # show/hide script params + set control states
        self.statusBar().showMessage(
            "Pick a Launch mode → Launch IPython → Load Bluesky, then Run plans."
        )

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setObjectName("toolbar")
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)

        lay.addWidget(QtWidgets.QLabel("Bluesky dir:"))
        self._workdir = QtWidgets.QLineEdit(_DEFAULT_LAUNCH_DIR)
        self._workdir.setMinimumWidth(S.px(160))
        self._workdir.setMaximumWidth(S.px(230))
        self._workdir.setToolTip("Directory the Bluesky session runs in.")
        lay.addWidget(self._workdir)

        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_dir)
        lay.addWidget(browse_btn)

        lay.addWidget(QtWidgets.QLabel("  Launch:"))
        self._mode_combo = S.NoScrollComboBox()
        self._mode_combo.addItem("Embedded kernel", "embedded")
        self._mode_combo.addItem("Launch script", "script")
        self._mode_combo.setCurrentIndex(
            max(0, self._mode_combo.findData(config.get("launch_mode")))
        )
        self._mode_combo.setToolTip(
            "Embedded kernel: GUI-managed ipykernel (Attach, transcript, queue).\n"
            "Launch script: run the external launcher (blueskyStarter.sh) in a "
            "screen session — interact via a terminal."
        )
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        lay.addWidget(self._mode_combo)

        self._launch_btn = S.primary_btn("▶  Launch IPython")
        self._launch_btn.clicked.connect(self._launch_console)
        lay.addWidget(self._launch_btn)

        self._attach_btn = QtWidgets.QPushButton("Attach")
        self._attach_btn.setToolTip(
            "Reconnect to a kernel left running by a previous GUI session "
            "(Console → Attach to running kernel…)."
        )
        self._attach_btn.clicked.connect(self._attach_console)
        lay.addWidget(self._attach_btn)

        self._load_btn = QtWidgets.QPushButton("Load Bluesky")
        self._load_btn.setToolTip(
            "Run the configured startup command in the console (connects to "
            "hardware). Set it in Python → Configuration."
        )
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._load_bluesky)
        lay.addWidget(self._load_btn)

        viewer_btn = QtWidgets.QPushButton("Open Bluesky Viewer")
        viewer_btn.setToolTip(
            "Open the data viewer in a separate, independent window/process."
        )
        viewer_btn.clicked.connect(self._open_viewer)
        lay.addWidget(viewer_btn)

        lay.addStretch(1)
        self._toolbar_status = QtWidgets.QLabel("")
        self._toolbar_status.setStyleSheet(f"color: {S.MUTED};")
        lay.addWidget(self._toolbar_status)
        return bar

    def _build_script_params_row(self) -> QtWidgets.QWidget:
        """Second toolbar row: args passed to the launch script (script mode only)."""
        bar = QtWidgets.QFrame()
        bar.setObjectName("toolbar")
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(6)

        lay.addWidget(QtWidgets.QLabel("Experiment:"))
        self._dm_exp = QtWidgets.QLineEdit(config.get("dm_experiment"))
        self._dm_exp.setMinimumWidth(S.px(150))
        self._dm_exp.setToolTip(
            "DM experiment name. Recorded to user_defaults/dm_experiment.txt by "
            "the launcher (used by both launch modes)."
        )
        lay.addWidget(self._dm_exp)

        lay.addWidget(QtWidgets.QLabel("Setup file:"))
        self._setup_file = QtWidgets.QLineEdit(config.get("setup_file"))
        self._setup_file.setMaximumWidth(S.px(160))
        self._setup_file.setToolTip("Setup YAML (default exp_setup.yml).")
        lay.addWidget(self._setup_file)

        self._run_mode_label = QtWidgets.QLabel("Run as:")
        lay.addWidget(self._run_mode_label)
        self._run_mode = S.NoScrollComboBox()
        for m in ("screen", "console", "lab"):
            self._run_mode.addItem(m, m)
        self._run_mode.setCurrentIndex(
            max(0, self._run_mode.findData(config.get("script_run_mode")))
        )
        self._run_mode.setToolTip(
            "Launch-script arg 3: screen (recommended from the GUI), console, or "
            "lab. (Only used by the 'Launch script' mode.)"
        )
        lay.addWidget(self._run_mode)

        self._script_hint = QtWidgets.QLabel("")
        self._script_hint.setStyleSheet(f"color: {S.MUTED};")
        lay.addWidget(self._script_hint)
        lay.addStretch(1)

        self._script_params_bar = bar
        return bar

    def _browse_dir(self) -> None:
        start = self._workdir.text().strip() or os.path.expanduser("~")
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Bluesky directory", start
        )
        if chosen:
            self._workdir.setText(chosen)

    # ── Launch mode (embedded kernel vs external launch script) ─────────────────

    def _is_script_mode(self) -> bool:
        return self._mode_combo.currentData() == "script"

    def _on_mode_changed(self) -> None:
        config.update({"launch_mode": self._mode_combo.currentData()})
        self._apply_launch_mode()

    def _apply_launch_mode(self) -> None:
        """Set control states for the mode; both modes take Experiment/Setup args."""
        script = self._is_script_mode()
        running = self.console.is_running()
        # Experiment/Setup apply to both modes (both launchers record them);
        # "Run as" is only used by the external-script mode.
        self._run_mode_label.setVisible(script)
        self._run_mode.setVisible(script)
        self._script_hint.setText(
            "→ external launcher (interact via a terminal)" if script
            else "→ embedded kernel starter (activates env + collection)"
        )
        # Attach / Load Bluesky / run-controls are embedded-only.
        self._attach_btn.setEnabled((not script) and (not running))
        self._load_btn.setEnabled((not script) and running)
        self._launch_btn.setEnabled(not running)
        self._launch_btn.setToolTip(
            "Run the external launch script in a screen session." if script
            else "Start the embedded kernel (via the embedded starter script)."
        )

    # ── Right panel: console + notes ────────────────────────────────────────────

    def _build_right_panel(self) -> QtWidgets.QWidget:
        vsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        vsplit.setChildrenCollapsible(False)

        console_card = S.make_card("IPython console")
        self._console_tabs = QtWidgets.QTabWidget()
        self._console_tabs.addTab(self.console, "Console")
        self._console_tabs.addTab(self.session_log, "Session log")
        self._console_tabs.setTabToolTip(
            1,
            "Full kernel transcript (input + output). Keeps recording even while "
            "the GUI is closed or the kernel is busy — so nothing is lost and you "
            "can watch a running plan live without waiting for the prompt.",
        )
        console_card.body.addWidget(self._console_tabs)
        console_card.body.addWidget(self.run_controls)   # Stop run / recovery / shutdown
        vsplit.addWidget(console_card)

        queue_card = S.make_card("Plan queue (scheduler)")
        queue_card.body.addWidget(self.queue)
        vsplit.addWidget(queue_card)

        vsplit.setStretchFactor(0, 1)
        vsplit.setStretchFactor(1, 0)
        vsplit.setSizes([560, 260])
        return vsplit

    def _on_run(self, command: str, notes: str) -> None:
        """Run the command in the console.

        `notes` is already baked into `command` by `plan_runner` as
        ``md={'notes': ...}`` on the generated ``RE(plan(...))`` call, so it
        lands in the run's start document (``cat[uid].metadata["start"]``).
        It is still passed through here for status-line/logging purposes.
        """
        self.console.run_code(command)

    def _on_queue(self, command: str, notes: str) -> None:
        """Append a plan to the queue (the scheduler dispatches it in turn).

        `notes` is stored separately by `queue_store` for the queue panel's
        tooltip display only — the actual attachment to the run's start
        document happens via the ``md={'notes': ...}`` already embedded in
        `command` (see `plan_runner._make_re_line`).
        """
        self.queue.add(command, notes)

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        pym = self.menuBar().addMenu("&Python")
        act_config = pym.addAction("Configuration…")
        act_config.setShortcut("Ctrl+,")
        act_config.setToolTip("Configure the visible plan files and launch command.")
        act_config.triggered.connect(self._open_config)

        m = self.menuBar().addMenu("&Console")
        self._act_attach = m.addAction("Attach to running kernel…")
        self._act_attach.triggered.connect(self._attach_console)
        self._act_restart = m.addAction("Restart kernel")
        self._act_restart.setEnabled(False)
        self._act_restart.triggered.connect(self._restart_kernel)
        m.addSeparator()
        self._act_shutdown = m.addAction("Shut down kernel")
        self._act_shutdown.setEnabled(False)
        self._act_shutdown.triggered.connect(self._shutdown_kernel)

    def _open_config(self) -> None:
        """Open the Configuration dialog; apply changes on Save."""
        from .config_dialog import ConfigDialog

        old_scale = config.get("ui_scale")
        dlg = ConfigDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            # Re-scan the plan browser with the new files scope, and refresh
            # the device catalog for the (possibly new) active profile's
            # beamline — the console reads the launch command live, so
            # nothing else to push.
            device_source.set_beamline(config.get("beamline"))
            self.runner.apply_config()
            self._set_toolbar_status("Configuration saved.")
            if config.get("ui_scale") != old_scale:
                QtWidgets.QMessageBox.information(
                    self,
                    "Restart required",
                    "Restart B-PILOT for the new UI scale to take effect.",
                )

    def _restart_kernel(self) -> None:
        # The kernel runs detached (client-only connection), so "restart" is a
        # shutdown + fresh launch in the same work dir. Only for kernels we
        # started (an attached kernel is someone else's to manage).
        if not self.console.is_running() or self.console.is_attached():
            return
        ok = QtWidgets.QMessageBox.question(
            self,
            "Restart kernel",
            "Restart the IPython kernel? All in-console state is lost.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ok != QtWidgets.QMessageBox.Yes:
            return
        self.console.shutdown()
        self.console.reset_view()
        self._reset_console_ui()
        self._launch_console()

    # ── Console lifecycle ───────────────────────────────────────────────────────

    def _launch_console(self) -> None:
        """Launch per the selected mode: embedded kernel, or external script."""
        if self._is_script_mode():
            self._launch_via_script()
        else:
            self._launch_embedded()

    def _launch_embedded(self) -> None:
        work_dir = self._workdir.text().strip() or _DEFAULT_LAUNCH_DIR
        try:
            os.makedirs(work_dir, exist_ok=True)
        except OSError as exc:
            self._set_toolbar_status(f"Cannot create {work_dir}: {exc}", error=True)
            return
        # Persist the experiment args so the embedded starter script picks them up.
        config.update({
            "dm_experiment": self._dm_exp.text().strip(),
            "setup_file": self._setup_file.text().strip() or "exp_setup.yml",
        })
        self._launch_btn.setEnabled(False)
        self._attach_btn.setEnabled(False)
        self._set_toolbar_status("Starting IPython…", error=False)
        # Let the label paint before the (brief) kernel spin-up blocks.
        QtCore.QTimer.singleShot(0, lambda: self.console.start(cwd=work_dir))

    def _launch_via_script(self) -> None:
        """Run the configured launcher (blueskyStarter.sh) with the GUI args."""
        script = config.get("launch_script")
        if not script or not os.path.exists(script):
            self._set_toolbar_status(
                f"Launch script not found: {script or '(unset)'} "
                "— set it in Python → Configuration.", error=True)
            return
        dm = self._dm_exp.text().strip()
        if not dm:
            self._set_toolbar_status(
                "Enter an Experiment name (arg 1 to the launch script).", error=True)
            return
        setup = self._setup_file.text().strip() or "exp_setup.yml"
        run_mode = self._run_mode.currentData() or "screen"
        config.update({"dm_experiment": dm, "setup_file": setup,
                       "script_run_mode": run_mode})   # persist the args

        work_dir = self._workdir.text().strip() or _DEFAULT_LAUNCH_DIR
        cwd = work_dir if os.path.isdir(work_dir) else None
        try:
            subprocess.Popen(
                ["bash", script, dm, setup, run_mode],
                cwd=cwd, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_toolbar_status(f"Could not run launch script: {exc}", error=True)
            return
        msg = f"Ran {os.path.basename(script)} {dm} {setup} {run_mode}."
        if run_mode == "screen":
            msg += f"  Attach in a terminal:  screen -r bluesky_{dm}"
        self._set_toolbar_status(msg)

    def _attach_console(self) -> None:
        """Reconnect to this beamline's running kernel (or a picked connection file)."""
        if self.console.is_running():
            self._set_toolbar_status("A session is already connected.", error=True)
            return
        cf = self.console.default_connection_file()
        if not cf or not os.path.exists(cf):
            # No running kernel for this beamline — let the user pick a file.
            start_dir = os.path.dirname(cf) if cf else os.path.expanduser("~")
            if not os.path.isdir(start_dir):
                start_dir = os.path.expanduser("~")
            cf, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Select kernel connection file",
                start_dir,
                "Kernel connection (*.json)",
            )
            if not cf:
                return
        self._launch_btn.setEnabled(False)
        self._attach_btn.setEnabled(False)
        self._set_toolbar_status("Attaching to running kernel…")
        QtCore.QTimer.singleShot(0, lambda: self.console.attach(cf))
        # A connected-but-silent kernel is either busy (fine) or dead — check
        # once the connection has had time to settle.
        QtCore.QTimer.singleShot(4500, self._verify_attach)

    def _on_launch_blocked(self, info) -> None:
        """Launch refused because a kernel is already running — offer to attach."""
        self._reset_console_ui()
        info = info or {}
        detail = (
            f"session: {info.get('session_name', '?')}\n"
            f"host: {info.get('host', '?')}\n"
            f"started: {info.get('started', '?')}\n"
            f"hosted in: {info.get('hosted_in', '?')}"
        )
        ans = QtWidgets.QMessageBox.question(
            self,
            "Kernel already running",
            "A Bluesky kernel is already running for this beamline — only one is "
            f"allowed at a time.\n\n{detail}\n\nAttach to it instead?\n\n"
            "(To stop it: Console → Shut down kernel, or run\n"
            f"  python -m gui_qt.kernel_session stop --beamline "
            f"{info.get('beamline', '')})",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if ans == QtWidgets.QMessageBox.Yes:
            self._attach_console()
        else:
            self._set_toolbar_status("Launch cancelled — a kernel is already running.")

    def _on_console_started(self) -> None:
        attached = self.console.is_attached()
        # Show the full transcript (from disk) and tail it live — this is where
        # you see everything, including history from before this GUI and live
        # output while the kernel is busy.
        self.session_log.load(self.console.log_file)
        if attached:
            # Jump to the transcript so a reattached (possibly busy) kernel shows
            # activity immediately, instead of the blank interactive prompt.
            self._console_tabs.setCurrentWidget(self.session_log)
        self._workdir.setEnabled(False)
        self._launch_btn.setEnabled(False)
        self._attach_btn.setEnabled(False)
        self._load_btn.setEnabled(True)
        # Restarting only works for a kernel we started (not an attached one).
        self._act_restart.setEnabled(not attached)
        self._act_shutdown.setEnabled(True)
        self.runner.set_console_ready(True)
        self.run_controls.set_console_ready(True)
        where = self._workdir.text().strip()
        if attached:
            self._set_toolbar_status(
                "Reattached — if the panel is blank the kernel is busy; "
                "it will respond when the running task finishes."
            )
        else:
            self._set_toolbar_status(f"IPython running in {where}")

    def _on_console_ready(self) -> None:
        """Kernel finished its handshake (idle) — safe to run and prompt visible."""
        if self.console.is_attached():
            self._set_toolbar_status("Reattached and ready.")

    def _verify_attach(self) -> None:
        """After attach settles: distinguish a busy kernel from a dead one."""
        if not self.console.is_running() or self.console.is_ready():
            return  # not attached, or already responded — nothing to warn about
        if self.console.is_alive():
            self._set_toolbar_status(
                "Reattached — kernel is busy (a task is running); it will "
                "respond when done."
            )
        else:
            self._set_toolbar_status(
                "Attached, but the kernel is not responding — it may have shut "
                "down. Use Console → Shut down, then Launch.",
                error=True,
            )

    def _on_attach_failed(self, reason: str) -> None:
        """Attach could not connect — restore the idle toolbar state."""
        self._reset_console_ui()
        QtWidgets.QMessageBox.warning(self, "Attach failed", reason)
        self._set_toolbar_status("Attach failed.", error=True)

    def _shutdown_kernel(self) -> None:
        """Explicitly terminate the kernel and return to the idle state."""
        ok = QtWidgets.QMessageBox.question(
            self,
            "Shut down kernel",
            "Terminate the IPython kernel?\n\n"
            "A killed kernel CANNOT be resumed later — all session state and any "
            "running plan will be lost (unlike closing the GUI, which leaves the "
            "kernel running to reattach).",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ok != QtWidgets.QMessageBox.Yes:
            return
        self.console.shutdown()
        self.console.reset_view()
        self._reset_console_ui()
        self._set_toolbar_status("Kernel shut down.")

    def _reset_console_ui(self) -> None:
        """Return the toolbar/menu to the pre-launch state."""
        self._workdir.setEnabled(True)
        self._launch_btn.setEnabled(True)
        self._attach_btn.setEnabled(True)
        self._load_btn.setEnabled(False)
        self._act_restart.setEnabled(False)
        self._act_shutdown.setEnabled(False)
        self.runner.set_console_ready(False)
        self.run_controls.set_console_ready(False)
        self.session_log.stop()   # kernel gone — stop polling (keep text visible)
        self._apply_launch_mode()  # re-apply mode-specific control states

    def _load_bluesky(self) -> None:
        from . import config

        cmd = config.get("bluesky_startup")
        ok = QtWidgets.QMessageBox.warning(
            self,
            "Load Bluesky",
            f"This runs the following in the console, which CONNECTS TO EPICS / "
            f"hardware:\n\n    {cmd}\n\nOnly do this on a real beamline "
            f"workstation.\n(Change the command in Python → Configuration.)\n\n"
            f"Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ok == QtWidgets.QMessageBox.Yes:
            self.console.load_bluesky()
            self._set_toolbar_status("Loaded Bluesky startup.")

    def _open_viewer(self) -> None:
        """Launch the Bluesky data viewer as a detached, independent process."""
        ok, _pid = QtCore.QProcess.startDetached(
            sys.executable, ["-m", "gui_qt.viewer"], paths.PKG_PARENT
        )
        if ok:
            self._set_toolbar_status("Opened Bluesky Viewer (separate window).")
        else:
            self._set_toolbar_status("Could not launch the viewer.", error=True)

    def _set_toolbar_status(self, msg: str, *, error: bool = False) -> None:
        self._toolbar_status.setStyleSheet(f"color: {S.ERROR if error else S.MUTED};")
        self._toolbar_status.setText(msg)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        """On close, keep or kill the kernel per config (see close_session)."""
        try:
            self.console.close_session()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
