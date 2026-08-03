"""Main window: toolbar + plan-runner (left) + console / notes (right)."""
from __future__ import annotations

import os
import sys

from PyQt5 import QtCore
from PyQt5 import QtWidgets

from . import autopilot_bridge
from . import config
from . import device_source
from . import paths
from . import style as S
from .console_panel import ConsolePanel
from .contacq_popup import ContAcqButton
from .mode_buttons import ModeButtonBar
from .panel_ribbon import CollapsibleDockPanel
from .panel_ribbon import PanelRibbon
from .plan_runner import PlanRunnerPanel
from .queue_panel import QueuePanel
from .run_controls import RunControlBar
from .session_log import SessionLogView
from .switchto_popup import SwitchToButton

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
        # AutoPILOT is the only dock widget, but nesting still widens Qt's own
        # redock hit-testing along an edge (see _sync_autopilot_dock).
        self.setDockNestingEnabled(True)

        self._last_launch_experiment: str | None = None
        self._exp_probe_inflight = False

        self.ribbon = PanelRibbon()
        self.runner = PlanRunnerPanel(ribbon=self.ribbon)
        self.console = ConsolePanel()
        self.session_log = SessionLogView()
        self.run_controls = RunControlBar(self.console, self._shutdown_kernel)
        self.mode_buttons = ModeButtonBar(self.console)
        self.queue = QueuePanel(self.console)
        self._mode_btn = SwitchToButton(self.console)
        self._contacq_btn = ContAcqButton(self.console)
        self.runner.runRequested.connect(self._on_run)
        self.runner.queueRequested.connect(self._on_queue)
        self._mode_btn.runRequested.connect(self._on_run)
        self._mode_btn.queueRequested.connect(self._on_queue)
        self.queue.copyToFormRequested.connect(self.runner.load_from_command)
        self.console.started.connect(self._on_console_started)
        self.console.ready.connect(self._on_console_ready)
        self.console.attach_failed.connect(self._on_attach_failed)
        self.console.launch_blocked.connect(self._on_launch_blocked)
        self.console.code_executed.connect(self._mode_btn.note_code_ran)

        central = QtWidgets.QWidget()
        clay = QtWidgets.QVBoxLayout(central)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(0)
        clay.addWidget(self._build_toolbar())

        main_split = S.Splitter(QtCore.Qt.Horizontal)
        S.configure_splitter(main_split)
        main_split.addWidget(self.runner)
        main_split.addWidget(self._build_right_panel())
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([840, 620])

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.ribbon)
        row.addWidget(main_split, 1)
        clay.addLayout(row, 1)

        self.setCentralWidget(central)

        # Optional AutoPILOT chat dock -- off by default, absent entirely if
        # AutoPILOT/ isn't there or its deps aren't installed (see
        # gui_qt/autopilot_bridge.py); enabled via Configuration -> Appearance.
        self.autopilot_chat = None
        self._autopilot_collapsible = None
        self._sync_autopilot_dock()

        self._build_menu()
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

        browse_btn = QtWidgets.QToolButton()
        browse_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DirOpenIcon))
        browse_btn.setToolTip("Select the Bluesky directory…")
        browse_btn.clicked.connect(self._browse_dir)
        lay.addWidget(browse_btn)

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

        lay.addStretch(1)
        self._toolbar_status = QtWidgets.QLabel("")
        self._toolbar_status.setStyleSheet(f"color: {S.MUTED};")
        lay.addWidget(self._toolbar_status)
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

    # ── Right panel: console + notes ────────────────────────────────────────────

    def _build_right_panel(self) -> QtWidgets.QWidget:
        vsplit = S.Splitter(QtCore.Qt.Vertical)
        S.configure_splitter(vsplit)

        console_card = S.make_card("IPython console")
        console_card.body.addWidget(self._build_status_bar())
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
        # BEAMMODE/TESTMODE toggles share run_controls' top row with Stop run
        # / Shut down kernel, rather than sitting in a row of their own.
        self.run_controls.add_trailing_widget(self.mode_buttons)
        console_card.body.addWidget(self.run_controls)
        vsplit.addWidget(console_card)

        queue_card = S.make_card("Plan queue (scheduler)")
        queue_card.body.addWidget(self.queue)
        vsplit.addWidget(queue_card)

        vsplit.setStretchFactor(0, 1)
        vsplit.setStretchFactor(1, 0)
        vsplit.setSizes([570, 260])
        return vsplit

    def _build_status_bar(self) -> QtWidgets.QWidget:
        """3-part strip above the console: Experiment / Mode / Cont. Aq.

        The experiment banner gets half the strip's width; the two action
        buttons split the other half between them (they use an Expanding
        size policy — see SwitchToButton/ContAcqButton — so the stretch
        factors below actually grow them instead of leaving dead space).
        """
        bar = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(6)

        self._exp_label = QtWidgets.QLabel("")
        self._exp_label.setAlignment(QtCore.Qt.AlignCenter)
        self._exp_label.setVisible(False)
        lay.addWidget(self._exp_label, 2)
        lay.addWidget(self._mode_btn, 1)
        lay.addWidget(self._contacq_btn, 1)
        return bar

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

        pym.addSeparator()
        act_viewer = pym.addAction("Open Bluesky Viewer")
        act_viewer.setToolTip(
            "Open the data viewer in a separate, independent window/process."
        )
        act_viewer.triggered.connect(self._open_viewer)

        self._act_autopilot = pym.addAction("AutoPILOT")
        self._act_autopilot.setCheckable(True)
        self._act_autopilot.setChecked(
            autopilot_bridge.AVAILABLE and bool(config.get("autopilot_enabled"))
        )
        if autopilot_bridge.AVAILABLE:
            self._act_autopilot.setToolTip("Show/hide the AutoPILOT chat panel.")
        else:
            self._act_autopilot.setEnabled(False)
            self._act_autopilot.setToolTip(
                "AutoPILOT was not found (or its dependencies aren't "
                "installed) next to this B-PILOT checkout."
            )
        self._act_autopilot.toggled.connect(self._on_autopilot_toggled)

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
        old_theme = config.get("theme")
        old_font = config.get("font_family")
        dlg = ConfigDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            # Re-scan the plan browser with the new files scope, and refresh
            # the device catalog for the (possibly new) active profile's
            # beamline — the console reads the launch command live, so
            # nothing else to push.
            device_source.set_beamline(config.get("beamline"))
            self.runner.apply_config()
            self._sync_autopilot_dock()
            self._act_autopilot.blockSignals(True)
            self._act_autopilot.setChecked(bool(config.get("autopilot_enabled")))
            self._act_autopilot.blockSignals(False)
            self._set_toolbar_status("Configuration saved.")
            if (
                config.get("ui_scale") != old_scale
                or config.get("theme") != old_theme
                or config.get("font_family") != old_font
            ):
                QtWidgets.QMessageBox.information(
                    self,
                    "Restart required",
                    "Restart B-PILOT for the new appearance settings to take effect.",
                )

    def _on_autopilot_toggled(self, checked: bool) -> None:
        config.update({"autopilot_enabled": checked})
        self._sync_autopilot_dock()

    def _sync_autopilot_dock(self) -> None:
        """Create or tear down the AutoPILOT chat dock to match the current
        "Enable the AutoPILOT chat panel" setting (Configuration -> Appearance).

        Called once at startup and again after every Configuration save, so
        toggling the checkbox takes effect immediately — no restart needed.
        """
        want = autopilot_bridge.AVAILABLE and bool(config.get("autopilot_enabled"))
        have = self.autopilot_chat is not None
        if want and not have:
            self.autopilot_chat = autopilot_bridge.ChatDockWidget(self.runner, self)
            # Left/right only (never top/bottom) -- a chat panel is unusable as
            # a thin horizontal strip, and narrowing the allowed areas also
            # stops the top/bottom corners from competing with the right edge
            # for Qt's redock-target hit-testing.
            self.autopilot_chat.setAllowedAreas(
                QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea
            )
            self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.autopilot_chat)
            self._autopilot_collapsible = CollapsibleDockPanel(
                self.autopilot_chat, self.ribbon, "autopilot", "AutoPILOT"
            )
        elif have and not want:
            self._autopilot_collapsible.detach()
            self._autopilot_collapsible = None
            self.removeDockWidget(self.autopilot_chat)
            self.autopilot_chat.deleteLater()
            self.autopilot_chat = None

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
        self.session_log.load(None)   # own kernel gone — drop the old transcript
        self._launch_console()

    # ── Console lifecycle ───────────────────────────────────────────────────────

    def _launch_console(self) -> None:
        """Confirm Experiment/Setup file, then launch (cancel aborts entirely)."""
        from .launch_dialog import LaunchDialog

        dlg = LaunchDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        # Persist the experiment args so the embedded starter script picks them up.
        config.update({
            "dm_experiment": dlg.experiment(),
            "setup_file": dlg.setup_file(),
        })
        self._last_launch_experiment = dlg.experiment()
        self._launch_embedded()

    def _launch_embedded(self) -> None:
        work_dir = self._workdir.text().strip() or _DEFAULT_LAUNCH_DIR
        try:
            os.makedirs(work_dir, exist_ok=True)
        except OSError as exc:
            self._set_toolbar_status(f"Cannot create {work_dir}: {exc}", error=True)
            return
        self._launch_btn.setEnabled(False)
        self._attach_btn.setEnabled(False)
        self._set_toolbar_status("Starting IPython…", error=False)
        # Let the label paint before the (brief) kernel spin-up blocks.
        QtCore.QTimer.singleShot(0, lambda: self.console.start(cwd=work_dir))

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
        self._mode_btn.set_console_ready(True)
        self._contacq_btn.set_console_ready(True)
        self.run_controls.set_console_ready(True)
        self.mode_buttons.set_console_ready(True)
        where = self._workdir.text().strip()
        if attached:
            self._set_toolbar_status(
                "Reattached — if the panel is blank the kernel is busy; "
                "it will respond when the running task finishes."
            )
            # The GUI never prompted for an experiment this session — read the
            # truth from the kernel itself rather than trust stale GUI config.
            self._set_experiment_banner("checking…", confirmed=False)
            self._start_experiment_probe()
        else:
            self._set_toolbar_status(f"IPython running in {where}")
            # Trusted immediately: this is exactly what LaunchDialog just wrote
            # to dm_experiment.txt before this kernel started.
            self._set_experiment_banner(self._last_launch_experiment, confirmed=True)

    def _start_experiment_probe(self) -> None:
        """Poll the attached kernel for DM_EXP until it resolves, then stop.

        `instrument.devices.global_variables` (which defines ``DM_EXP``) is
        only importable once Bluesky is loaded in that kernel — which may not
        have happened yet at attach time — so this retries rather than giving
        up after one failed query.
        """
        if getattr(self, "_exp_probe_timer", None) is None:
            self._exp_probe_timer = QtCore.QTimer(self)
            self._exp_probe_timer.setInterval(3000)
            self._exp_probe_timer.timeout.connect(self._probe_experiment)
        self._exp_probe_inflight = False
        self._exp_probe_timer.start()
        self._probe_experiment()

    def _probe_experiment(self) -> None:
        if self._exp_probe_inflight or not self.console.is_running():
            return
        self._exp_probe_inflight = True
        self.console.query_values(
            {"__dm_exp__": "instrument.devices.global_variables.DM_EXP"},
            self._on_experiment_probed,
        )

    def _on_experiment_probed(self, result: dict) -> None:
        self._exp_probe_inflight = False
        value = result.get("__dm_exp__")
        if value:
            self._set_experiment_banner(value, confirmed=True)
            self._exp_probe_timer.stop()
        else:
            self._set_experiment_banner(
                "unknown — Bluesky not loaded yet", confirmed=False
            )

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
        self.session_log.load(None)   # own kernel gone — drop the old transcript
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
        self._mode_btn.set_console_ready(False)
        self._contacq_btn.set_console_ready(False)
        self.run_controls.set_console_ready(False)
        self.mode_buttons.set_console_ready(False)
        self.session_log.stop()   # kernel gone — stop polling (keep text visible)
        self._clear_experiment_banner()

    def _set_experiment_banner(self, value: str, *, confirmed: bool) -> None:
        """Show "Experiment:\\n<value>" in a bold banner above the console tabs.

        `confirmed` picks accent (trusted value) vs warning (not yet known)
        styling — see :meth:`_on_console_started` / :meth:`_on_experiment_probed`.
        """
        color = S.ACCENT if confirmed else S.WARNING
        self._exp_label.setStyleSheet(
            f"font-weight: 700; font-size: {S.px(13)}px; padding: 4px; "
            f"border-radius: 3px; background: {color}; color: white;"
        )
        self._exp_label.setText(f"Experiment:\n{value}")
        self._exp_label.setVisible(True)

    def _clear_experiment_banner(self) -> None:
        self._exp_label.setVisible(False)
        self._exp_label.setText("")
        if getattr(self, "_exp_probe_timer", None) is not None:
            self._exp_probe_timer.stop()

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
