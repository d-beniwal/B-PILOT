"""Configuration dialog: a profile bar on top, tabbed pages below.

Tabs: Paths, Plans, Launch Session, Devices, Appearance — one page each,
selected via a left-hand list (`QListWidget` + `QStackedWidget`). A profile
bar above the tabs lets you switch which on-disk profile
(`B-PILOT/profiles/<name>/{default_config.json,active_config.json}`) you're
editing; see :mod:`config` for the profile lifecycle — `default_config.json`
is the shared, git-committed baseline for that beamline, `active_config.json`
is the live, per-workstation settings actually used day to day. Selecting a
different profile loads its *active* values into the form for
editing/preview only — nothing is written to disk or made active until
*Save*, same as every other field here. *Restore Defaults* previews the
profile's `default_config.json` instead; *Save as Default* is the only
action that writes back to it.

Reachable from the main window's **Python → Configuration…** menu. Edits are
written through :mod:`config` on *Save*; the caller then refreshes the panels
so changes take effect immediately (no restart) — except **UI scale**, which
is read once at startup (see :mod:`app`) and needs a relaunch to apply.
"""
from __future__ import annotations

import os

from PyQt5 import QtCore, QtWidgets

from . import config
from . import device_discovery as ddisc
from . import device_source
from . import paths as _paths
from . import plan_parser as P
from . import style as S


class ConfigDialog(QtWidgets.QDialog):
    """Modal dialog: profile bar + tabbed Paths/Plans/Launch Session/Devices/Appearance."""

    def __init__(self, parent=None) -> None:
        """Build every tab's widgets once, then populate them from the active profile."""
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self.setMinimumWidth(S.px(760))
        self.setMinimumHeight(S.px(520))

        self._current_profile = config.active_profile()

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        outer.addLayout(self._build_profile_bar())

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(8)

        self._tab_list = QtWidgets.QListWidget()
        self._tab_list.setFixedWidth(S.px(140))
        self._stack = QtWidgets.QStackedWidget()

        # Build order matters (Plans depends on Paths' plans_dir field); it
        # happens to match the desired tab display order too.
        pages = [
            ("Paths", self._page(self._build_files_card())),
            ("Plans", self._page(self._build_visibility_card())),
            ("Launch Session", self._page(self._build_launch_card(), self._build_session_card())),
            ("Devices", self._page(self._build_devices_card())),
            ("Data Viewer", self._page(self._build_data_viewer_card())),
            ("Appearance", self._page(self._build_appearance_card())),
        ]
        for title, page in pages:
            self._tab_list.addItem(title)
            self._stack.addWidget(page)
        self._tab_list.currentRowChanged.connect(self._stack.setCurrentIndex)

        body.addWidget(self._tab_list)
        body.addWidget(self._stack, 1)
        outer.addLayout(body, 1)

        outer.addWidget(self._build_buttons())

        self._load_from(config.as_dict())
        self._tab_list.setCurrentRow(0)

    @staticmethod
    def _page(*widgets: QtWidgets.QWidget) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        for w in widgets:
            layout.addWidget(w)
        layout.addStretch(1)
        return page

    # ── Profile bar ──────────────────────────────────────────────────────────────

    def _build_profile_bar(self) -> QtWidgets.QLayout:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(S.LabelRight("Profile:"))
        self._profile_combo = S.NoScrollComboBox()
        self._profile_combo.setMinimumWidth(S.px(160))
        self._profile_combo.setToolTip(
            "Beamline configuration profile. Each profile is a folder "
            "(profiles/<name>/) holding a shared default_config.json and a "
            "live active_config.json — paths, plans, launch/session commands, "
            "devices, and appearance all travel together."
        )
        self._refresh_profile_combo()
        self._profile_combo.currentTextChanged.connect(self._on_profile_selected)
        row.addWidget(self._profile_combo)

        new_btn = QtWidgets.QPushButton("New…")
        new_btn.setToolTip("Create a new profile, cloned from the one currently shown.")
        new_btn.clicked.connect(self._new_profile)
        save_as_btn = QtWidgets.QPushButton("Save As…")
        save_as_btn.setToolTip("Save the current form values as a new profile.")
        save_as_btn.clicked.connect(self._save_profile_as)
        save_default_btn = QtWidgets.QPushButton("Save as Default")
        save_default_btn.setToolTip(
            "Overwrite this profile's shared default_config.json with the "
            "current form values. This is the git-committed baseline other "
            "workstations reset to — use deliberately."
        )
        save_default_btn.clicked.connect(self._save_as_default)
        delete_btn = QtWidgets.QPushButton("Delete")
        delete_btn.setToolTip("Delete the selected profile (at least one must remain).")
        delete_btn.clicked.connect(self._delete_profile)

        row.addWidget(new_btn)
        row.addWidget(save_as_btn)
        row.addWidget(save_default_btn)
        row.addWidget(delete_btn)
        row.addStretch(1)
        return row

    def _refresh_profile_combo(self) -> None:
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        self._profile_combo.addItems(config.list_profiles())
        idx = self._profile_combo.findText(self._current_profile)
        self._profile_combo.setCurrentIndex(max(0, idx))
        self._profile_combo.blockSignals(False)

    def _on_profile_selected(self, name: str) -> None:
        if not name or name == self._current_profile:
            return
        self._current_profile = name
        self._load_from(config.profile_values(name))

    def _new_profile(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New profile", "Profile name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            config.new_profile(name, clone_from=self._current_profile)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "New profile", str(exc))
            return
        self._current_profile = name
        self._refresh_profile_combo()
        self._load_from(config.profile_values(name))

    def _save_profile_as(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Save profile as", "Profile name:")
        name = name.strip()
        if not ok or not name:
            return
        config.save_profile_as(name, self.values())
        self._current_profile = name
        self._refresh_profile_combo()

    def _save_as_default(self) -> None:
        name = self._current_profile
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Save as Default",
            f"Overwrite the shared default_config.json for '{name}' with the "
            "current form values? This is the git-committed baseline other "
            "workstations reset to.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        config.save_as_default(name, self.values())

    def _delete_profile(self) -> None:
        name = self._profile_combo.currentText()
        if not name:
            return
        if len(config.list_profiles()) <= 1:
            QtWidgets.QMessageBox.warning(
                self, "Delete profile", "Cannot delete the last remaining profile."
            )
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Delete profile",
            f"Delete profile '{name}'? This cannot be undone.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        config.delete_profile(name)
        self._current_profile = config.active_profile()
        self._refresh_profile_combo()
        self._load_from(config.profile_values(self._current_profile))

    # ── Paths ────────────────────────────────────────────────────────────────────

    def _build_files_card(self) -> QtWidgets.QWidget:
        card = S.make_card("Paths  (where the runner looks for plans)")
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._plans_dir = QtWidgets.QLineEdit()
        self._plans_dir.setToolTip(
            "Folder scanned for plan .py files (top level + one subfolder deep)."
        )
        grid.addWidget(S.LabelRight("Plans directory:"), 0, 0)
        grid.addWidget(self._plans_dir, 0, 1)
        grid.addWidget(self._browse_button(self._plans_dir), 0, 2)

        self._import_root = QtWidgets.QLineEdit()
        self._import_root.setToolTip(
            "Root the 'from <module> import <plan>' line is resolved against.\n"
            "The module = plan file path relative to this root.\n"
            "e.g. root=mpe_bluesky/ -> instrument/plans/foo.py -> "
            "instrument.plans.foo"
        )
        grid.addWidget(S.LabelRight("Import root:"), 1, 0)
        grid.addWidget(self._import_root, 1, 1)
        grid.addWidget(self._browse_button(self._import_root), 1, 2)

        self._default_file = QtWidgets.QLineEdit()
        self._default_file.setToolTip(
            "File in the plans directory checked (shown) by default on startup."
        )
        grid.addWidget(S.LabelRight("Default plan file:"), 2, 0)
        grid.addWidget(self._default_file, 2, 1)

        card.body.addLayout(grid)
        return card

    # ── Plans (plan visibility) ──────────────────────────────────────────────────

    def _build_visibility_card(self) -> QtWidgets.QWidget:
        card = S.make_card("Plan visibility  (which files appear in the User files panel)")

        # Leaf (file) tree items, keyed by plans_dir-relative path.
        self._visibility_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._visible_files_initial: set[str] = set()

        btn_row = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("Select all")
        select_all_btn.clicked.connect(lambda: self._set_all_visibility_checked(True))
        deselect_all_btn = QtWidgets.QPushButton("Deselect all")
        deselect_all_btn.clicked.connect(lambda: self._set_all_visibility_checked(False))
        expand_all_btn = QtWidgets.QPushButton("Expand all")
        expand_all_btn.clicked.connect(lambda: self._visibility_tree.expandAll())
        collapse_all_btn = QtWidgets.QPushButton("Collapse all")
        collapse_all_btn.clicked.connect(lambda: self._visibility_tree.collapseAll())
        refresh_btn = QtWidgets.QPushButton("Refresh list")
        refresh_btn.setToolTip(
            "Re-scan the Plans directory (Paths tab) — picks up files added/"
            "removed on disk, or an edited Plans directory field."
        )
        refresh_btn.clicked.connect(self._rebuild_visibility_list)
        btn_row.addWidget(select_all_btn)
        btn_row.addWidget(deselect_all_btn)
        btn_row.addWidget(expand_all_btn)
        btn_row.addWidget(collapse_all_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(refresh_btn)
        card.body.addLayout(btn_row)

        self._visibility_tree = QtWidgets.QTreeWidget()
        self._visibility_tree.setHeaderHidden(True)
        self._visibility_tree.setMinimumHeight(S.px(160))
        card.body.addWidget(self._visibility_tree)

        # Re-scan automatically when the Plans directory field is edited.
        self._plans_dir.editingFinished.connect(self._rebuild_visibility_list)

        return card

    def _rebuild_visibility_list(self) -> None:
        """Re-scan the (possibly just-edited) Plans directory and rebuild the tree.

        Preserves already-toggled checkbox states and expand/collapse state
        (by folder path) across a rescan.
        """
        plans_dir = self._plans_dir.text().strip()
        old_checked = {
            rel: item.checkState(0) == QtCore.Qt.Checked
            for rel, item in self._visibility_items.items()
        }
        old_expanded = {
            item.data(0, QtCore.Qt.UserRole): item.isExpanded()
            for item in self._all_visibility_dir_items()
        }

        self._visibility_tree.clear()
        self._visibility_items.clear()

        root = self._visibility_tree.invisibleRootItem()
        parent_by_depth: dict[int, QtWidgets.QTreeWidgetItem] = {-1: root}
        for display_name, kind, abs_path, depth in P.scan_user_dir(plans_dir):
            parent = parent_by_depth[depth - 1]
            if kind == "dir":
                item = QtWidgets.QTreeWidgetItem(parent, [f"📁 {display_name}"])
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsUserCheckable)
                item.setData(0, QtCore.Qt.UserRole, abs_path)
                item.setExpanded(old_expanded.get(abs_path, True))
                parent_by_depth[depth] = item
                continue
            rel = os.path.relpath(abs_path, plans_dir).replace(os.sep, "/")
            item = QtWidgets.QTreeWidgetItem(parent, [display_name])
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(
                0,
                QtCore.Qt.Checked
                if old_checked.get(rel, rel in self._visible_files_initial)
                else QtCore.Qt.Unchecked,
            )
            self._visibility_items[rel] = item

    def _all_visibility_dir_items(self):
        """Yield every folder QTreeWidgetItem currently in the visibility tree."""
        stack = [self._visibility_tree.invisibleRootItem()]
        while stack:
            node = stack.pop()
            for i in range(node.childCount()):
                child = node.child(i)
                if not (child.flags() & QtCore.Qt.ItemIsUserCheckable):
                    yield child  # dirs are the only non-checkable items
                stack.append(child)

    def _set_all_visibility_checked(self, checked: bool) -> None:
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        for item in self._visibility_items.values():
            item.setCheckState(0, state)

    @staticmethod
    def _set_all_checked(checks: dict[str, QtWidgets.QCheckBox], checked: bool) -> None:
        for cb in checks.values():
            cb.setChecked(checked)

    # ── Launch Session: Load Bluesky command(s) ──────────────────────────────────

    def _build_launch_card(self) -> QtWidgets.QWidget:
        card = S.make_card("Launch  (Load Bluesky command)")
        card.body.addWidget(
            QtWidgets.QLabel(
                "Run in the console when you click “Load Bluesky” "
                "(one command per line — CONNECTS TO HARDWARE on a beamline):"
            )
        )
        self._startup = QtWidgets.QPlainTextEdit()
        self._startup.setObjectName("mono")
        self._startup.setFixedHeight(S.px(90))
        self._startup.setToolTip(
            "MPE console startup is 'from instrument.collection import *' "
            "(account-gated).  Queueserver uses 'from instrument.queueserver "
            "import *'."
        )
        card.body.addWidget(self._startup)

        self._keep_kernel = QtWidgets.QCheckBox(
            "Keep the IPython kernel running when the GUI closes "
            "(so it can be reattached)"
        )
        self._keep_kernel.setToolTip(
            "On: closing the GUI leaves the kernel (and any running plan) alive; "
            "relaunch and use Console → Attach to reconnect.\n"
            "Off: the kernel is shut down when the GUI closes."
        )
        card.body.addWidget(self._keep_kernel)
        return card

    # ── Launch Session: one kernel per beamline ──────────────────────────────────

    def _build_session_card(self) -> QtWidgets.QWidget:
        card = S.make_card("Session  (one kernel per beamline)")
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)

        self._beamline = QtWidgets.QLineEdit()
        self._beamline.setToolTip(
            "Identifies the single interactive kernel for this beamline "
            "(screen session name + fixed connection-file path) and the "
            "device catalog used by this profile."
        )
        grid.addWidget(S.LabelRight("Beamline id:"), 0, 0)
        grid.addWidget(self._beamline, 0, 1)

        card.body.addLayout(grid)

        self._use_screen = QtWidgets.QCheckBox(
            "Host the kernel in a named 'screen' session (recommended)"
        )
        self._use_screen.setToolTip(
            "On: the kernel runs inside 'screen bluesky-kernel-<beamline>' so it "
            "survives the GUI and staff can attach a terminal with\n"
            "  screen -r bluesky-kernel-<beamline>\n"
            "Off: the kernel is launched as a plain detached process."
        )
        card.body.addWidget(self._use_screen)

        srow = QtWidgets.QGridLayout()
        srow.setColumnStretch(1, 1)
        self._launch_script = QtWidgets.QLineEdit()
        self._launch_script.setToolTip(
            "Shell script run by Launch when the toolbar Launch mode is set to "
            "'Launch script' (e.g. blueskyStarter.sh). Called as:\n"
            "  <script> <dm_experiment> <setup_file> <mode>"
        )
        srow.addWidget(S.LabelRight("Launch script:"), 0, 0)
        srow.addWidget(self._launch_script, 0, 1)
        srow.addWidget(self._browse_button(self._launch_script, kind="file"), 0, 2)

        self._embedded_starter = QtWidgets.QLineEdit()
        self._embedded_starter.setToolTip(
            "Script run for the EMBEDDED kernel launch — activates the env + "
            "records the experiment (like blueskyStarter.sh) but starts a "
            "connectable ipykernel. Called as:\n"
            "  <script> <dm_experiment> <setup_file> <connection_file> <screen>\n"
            "Leave blank to launch a bare kernel with no env activation."
        )
        srow.addWidget(S.LabelRight("Embedded starter:"), 1, 0)
        srow.addWidget(self._embedded_starter, 1, 1)
        srow.addWidget(self._browse_button(self._embedded_starter, kind="file"), 1, 2)

        card.body.addLayout(srow)
        return card

    # ── Devices (search paths + discover) ────────────────────────────────────────

    def _build_devices_card(self) -> QtWidgets.QWidget:
        card = S.make_card("Devices  (search paths + discovered device names)")

        card.body.addWidget(
            QtWidgets.QLabel(
                "Directories scanned for device-defining .py files (never imported "
                "— only their __all__ list is read):"
            )
        )
        paths_row = QtWidgets.QHBoxLayout()
        self._device_paths_widget = QtWidgets.QListWidget()
        self._device_paths_widget.setFixedHeight(S.px(70))
        paths_row.addWidget(self._device_paths_widget, 1)
        paths_btns = QtWidgets.QVBoxLayout()
        add_path_btn = QtWidgets.QPushButton("Add…")
        add_path_btn.clicked.connect(self._add_device_path)
        remove_path_btn = QtWidgets.QPushButton("Remove")
        remove_path_btn.clicked.connect(self._remove_device_path)
        paths_btns.addWidget(add_path_btn)
        paths_btns.addWidget(remove_path_btn)
        paths_btns.addStretch(1)
        paths_row.addLayout(paths_btns)
        card.body.addLayout(paths_row)

        btn_row = QtWidgets.QHBoxLayout()
        discover_btn = QtWidgets.QPushButton("Discover")
        discover_btn.setToolTip(
            "Re-scan the search paths above for __all__-exported device names. "
            "Newly found devices are shown by default; existing checkbox states "
            "are preserved."
        )
        discover_btn.clicked.connect(self._rebuild_device_list)
        select_all_btn = QtWidgets.QPushButton("Select all")
        select_all_btn.clicked.connect(lambda: self._set_all_device_checked(True))
        deselect_all_btn = QtWidgets.QPushButton("Deselect all")
        deselect_all_btn.clicked.connect(lambda: self._set_all_device_checked(False))
        btn_row.addWidget(discover_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(select_all_btn)
        btn_row.addWidget(deselect_all_btn)
        card.body.addLayout(btn_row)

        # category -> name -> checkbox; mirrors the on-disk device_selection shape.
        self._device_checks: dict[str, dict[str, QtWidgets.QCheckBox]] = {}
        self._device_selection_initial: dict[str, dict[str, bool]] = {}
        # {device_name: category} — manual per-profile override of the category
        # device_discovery infers; see the Devices card's per-device combo below.
        self._device_category_overrides: dict[str, str] = {}
        # {device_name: discovered_category} — recomputed on every rescan, used
        # to know what "(auto)" resolves to and when an override is redundant.
        self._device_raw_category: dict[str, str] = {}
        self._device_container = QtWidgets.QWidget()
        self._device_layout = QtWidgets.QVBoxLayout(self._device_container)
        self._device_layout.setContentsMargins(2, 2, 2, 2)
        self._device_layout.setSpacing(2)
        dev_scroll = QtWidgets.QScrollArea()
        dev_scroll.setWidgetResizable(True)
        dev_scroll.setWidget(self._device_container)
        dev_scroll.setMinimumHeight(S.px(200))
        card.body.addWidget(dev_scroll)

        return card

    def _add_device_path(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select device search directory", _paths.PROJECT_ROOT
        )
        if not chosen:
            return
        rel = os.path.relpath(chosen, _paths.PROJECT_ROOT)
        value = rel if not rel.startswith("..") else chosen
        self._device_paths_widget.addItem(value)
        self._rebuild_device_list()

    def _remove_device_path(self) -> None:
        for item in self._device_paths_widget.selectedItems():
            self._device_paths_widget.takeItem(self._device_paths_widget.row(item))
        self._rebuild_device_list()

    def _rebuild_device_list(self) -> None:
        """Re-scan the search paths and rebuild the checkbox list, grouped by category.

        Discovery's inferred category is never changed by this — a per-device
        combo lets the user move a device to a different category for THIS
        profile only (`self._device_category_overrides`), applied on top of
        the discovered category before grouping.

        Preserves already-toggled checkbox states across a rescan (keyed by
        device name only, so moving a device to a new category doesn't lose
        its state); a name never seen before defaults to checked (shown) —
        "everything found is shown by default."
        """
        raw_paths = [
            self._device_paths_widget.item(i).text() for i in range(self._device_paths_widget.count())
        ]
        # Flat by name (not by category) — a device recategorized during this
        # session must keep its checked state when it lands in a new group.
        old_checked: dict[str, bool] = {
            name: cb.isChecked()
            for names in self._device_checks.values()
            for name, cb in names.items()
        }
        resolved = [device_source.resolve_path(p) for p in raw_paths]
        discovered = ddisc.scan(resolved)

        while self._device_layout.count():
            item = self._device_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._device_checks.clear()
        self._device_raw_category.clear()

        by_category: dict[str, list] = {}
        for device in discovered:
            self._device_raw_category[device.name] = device.category
            category = self._device_category_overrides.get(device.name, device.category)
            by_category.setdefault(category, []).append(device)
        all_categories = sorted(
            set(by_category) | set(self._device_category_overrides.values()) | {"other"}
        )

        for category in sorted(by_category):
            hdr = QtWidgets.QLabel(category)
            hdr.setStyleSheet(f"color: {S.MUTED}; font-weight: bold;")
            self._device_layout.addWidget(hdr)
            self._device_checks[category] = {}
            for device in sorted(by_category[category], key=lambda d: d.name.lower()):
                cb = QtWidgets.QCheckBox(device.name)
                cb.setToolTip(device.source_file)
                checked = old_checked.get(device.name)
                if checked is None:
                    checked = self._initial_device_checked(category, device.name)
                cb.setChecked(checked)
                self._device_checks[category][device.name] = cb

                combo = self._make_category_combo(device.name, device.category, all_categories)

                row = QtWidgets.QWidget()
                row_lay = QtWidgets.QHBoxLayout(row)
                row_lay.setContentsMargins(0, 0, 0, 0)
                row_lay.setSpacing(6)
                row_lay.addWidget(cb)
                row_lay.addWidget(combo)
                row_lay.addStretch(1)
                self._device_layout.addWidget(row)
        self._device_layout.addStretch(1)

    def _initial_device_checked(self, category: str, name: str) -> bool:
        """Fall back to the on-disk `device_selection` for a device with no
        session-local checked state yet — first under its current (possibly
        overridden) category, then any category (covers a device recategorized
        after `device_selection` was last saved under its old category)."""
        sel = self._device_selection_initial
        if name in sel.get(category, {}):
            return sel[category][name]
        for cat_sel in sel.values():
            if name in cat_sel:
                return cat_sel[name]
        return True

    def _make_category_combo(
        self, name: str, raw_category: str, all_categories: list[str]
    ) -> QtWidgets.QComboBox:
        """Editable dropdown to move `name` into a different category (this
        profile only) — discovery's own inference is never changed."""
        combo = S.NoScrollComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(S.px(130))
        combo.setToolTip(
            "Move this device to a different category (saved with this profile "
            "only). Discovery's own filename/class-based inference is unaffected "
            "— pick '(auto: ...)' to clear the override."
        )
        combo.addItem(f"(auto: {raw_category})", None)
        for cat in all_categories:
            if cat != raw_category:
                combo.addItem(cat, cat)

        override = self._device_category_overrides.get(name)
        if override and override != raw_category:
            idx = combo.findData(override)
            if idx < 0:
                combo.addItem(override, override)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentIndex(0)

        # `activated` fires when an item is picked from the popup (by click or
        # keyboard+Enter). For typed-then-Enter text, use the line edit's
        # `returnPressed` — NOT `editingFinished`: on an editable combo,
        # opening the popup itself shifts focus off the internal line edit,
        # which fires `editingFinished` immediately: the handler's
        # `_rebuild_device_list()` then tears down (and rebuilds) this very
        # combo mid-popup, snapping it shut before a selection can be made.
        combo.activated.connect(lambda _i, n=name, c=combo: self._apply_device_category(n, c))
        combo.lineEdit().returnPressed.connect(
            lambda n=name, c=combo: self._apply_device_category(n, c)
        )
        return combo

    def _apply_device_category(self, name: str, combo: QtWidgets.QComboBox) -> None:
        text = combo.currentText().strip()
        raw = self._device_raw_category.get(name)
        if not text or text.startswith("(auto") or text == raw:
            self._device_category_overrides.pop(name, None)
        else:
            self._device_category_overrides[name] = text
        self._rebuild_device_list()

    def _set_all_device_checked(self, checked: bool) -> None:
        for names in self._device_checks.values():
            for cb in names.values():
                cb.setChecked(checked)

    # ── Data Viewer (databroker connection) ──────────────────────────────────────

    def _build_data_viewer_card(self) -> QtWidgets.QWidget:
        card = S.make_card("Data Viewer  (databroker connection)")
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._databroker_catalog = QtWidgets.QLineEdit()
        self._databroker_catalog.setToolTip(
            "Name of a databroker catalog registered in ~/.local/share/intake/*.yml "
            "(e.g. 'hexm', 'ht_hedm', '1id_hexm') — see instrument/iconfig.yml's "
            "DATABROKER_CATALOG per account. This is a NAME, not a connection "
            "string: the actual MongoDB URI (with credentials) is resolved locally "
            "from the pre-registered intake file, never stored here.\n"
            "Leave blank to auto-detect from iconfig.yml by the logged-in account."
        )
        grid.addWidget(S.LabelRight("Databroker catalog:"), 0, 0)
        grid.addWidget(self._databroker_catalog, 0, 1)

        self._databroker_uri = QtWidgets.QLineEdit()
        self._databroker_uri.setToolTip(
            "Optional Tiled (or other) URI override — when set, this replaces the "
            "named catalog above. Do NOT put a credentialed mongodb://user:pass@ "
            "URI here: profiles are meant to be committed to git and shared "
            "between beamline staff, so secrets don't belong in this field."
        )
        grid.addWidget(S.LabelRight("Alternate URI:"), 1, 0)
        grid.addWidget(self._databroker_uri, 1, 1)

        self._databroker_nexus_dir = QtWidgets.QLineEdit()
        self._databroker_nexus_dir.setToolTip(
            "Optional folder holding raw NeXus files alongside catalog records."
        )
        grid.addWidget(S.LabelRight("NeXus files dir:"), 2, 0)
        grid.addWidget(self._databroker_nexus_dir, 2, 1)
        grid.addWidget(self._browse_button(self._databroker_nexus_dir), 2, 2)

        card.body.addLayout(grid)
        note = QtWidgets.QLabel(
            "These are starting defaults for the standalone Data Viewer window "
            "(python -m gui_qt.viewer) — still editable there per-session."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {S.MUTED};")
        card.body.addWidget(note)
        return card

    # ── Appearance (display scale) ───────────────────────────────────────────────

    def _build_appearance_card(self) -> QtWidgets.QWidget:
        card = S.make_card("Appearance")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(S.LabelRight("UI scale:"))
        self._ui_scale = QtWidgets.QDoubleSpinBox()
        self._ui_scale.setRange(0.5, 3.0)
        self._ui_scale.setSingleStep(0.1)
        self._ui_scale.setDecimals(2)
        self._ui_scale.setSuffix("×")
        self._ui_scale.setToolTip(
            "Multiplier applied to every font, widget, and window size — for "
            "high-DPI screens (e.g. 4K). Takes effect on the next launch."
        )
        row.addWidget(self._ui_scale)
        row.addStretch(1)
        card.body.addLayout(row)
        note = QtWidgets.QLabel("Restart B-PILOT for a scale change to take effect.")
        note.setStyleSheet(f"color: {S.MUTED};")
        card.body.addWidget(note)
        return card

    # ── Buttons ──────────────────────────────────────────────────────────────────

    def _build_buttons(self) -> QtWidgets.QWidget:
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.RestoreDefaults
        )
        box.button(QtWidgets.QDialogButtonBox.Save).setObjectName("primary")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        box.button(QtWidgets.QDialogButtonBox.RestoreDefaults).setToolTip(
            "Preview this profile's saved default_config.json. Nothing is "
            "written until Save."
        )
        box.button(QtWidgets.QDialogButtonBox.RestoreDefaults).clicked.connect(
            lambda: self._load_from(config.default_profile_values(self._current_profile))
        )
        return box

    # ── Load / collect values ────────────────────────────────────────────────────

    def _browse_button(self, target: QtWidgets.QLineEdit, kind: str = "dir"):
        btn = QtWidgets.QPushButton("Browse…")
        btn.clicked.connect(lambda: self._browse_into(target, kind))
        return btn

    def _browse_into(self, target: QtWidgets.QLineEdit, kind: str = "dir") -> None:
        start = target.text().strip() or os.path.expanduser("~")
        base = start if os.path.isdir(start) else os.path.dirname(start)
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        if kind == "file":
            chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Select launch script", base
            )
        else:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select directory", base
            )
        if chosen:
            target.setText(chosen)

    def _load_from(self, cfg: dict) -> None:
        """Populate every tab's widgets from `cfg` (a full effective-config dict)."""
        self._plans_dir.setText(cfg["plans_dir"])
        self._import_root.setText(cfg["import_root"])
        self._default_file.setText(cfg["default_plan_file"])
        self._visible_files_initial = set(cfg.get("visible_plan_files") or [])
        self._visibility_items.clear()
        self._rebuild_visibility_list()

        self._startup.setPlainText(cfg["bluesky_startup"])
        self._keep_kernel.setChecked(bool(cfg["keep_kernel_on_exit"]))
        self._beamline.setText(cfg["beamline"])
        self._use_screen.setChecked(bool(cfg["use_screen"]))
        self._launch_script.setText(cfg["launch_script"])
        self._embedded_starter.setText(cfg["embedded_starter_script"])

        self._ui_scale.setValue(float(cfg["ui_scale"]))

        self._device_paths_widget.clear()
        self._device_paths_widget.addItems(cfg.get("device_search_paths") or [])
        self._device_selection_initial = dict(cfg.get("device_selection") or {})
        self._device_category_overrides = dict(cfg.get("device_category_overrides") or {})
        self._device_checks.clear()
        self._rebuild_device_list()

        self._databroker_catalog.setText(cfg.get("databroker_catalog") or "")
        self._databroker_uri.setText(cfg.get("databroker_uri") or "")
        self._databroker_nexus_dir.setText(cfg.get("databroker_nexus_dir") or "")

    def values(self) -> dict:
        """Return the edited settings (all tabs) as a config dict."""
        return {
            "plans_dir": self._plans_dir.text().strip(),
            "import_root": self._import_root.text().strip(),
            "default_plan_file": self._default_file.text().strip(),
            "visible_plan_files": sorted(
                rel
                for rel, item in self._visibility_items.items()
                if item.checkState(0) == QtCore.Qt.Checked
            ),
            "bluesky_startup": self._startup.toPlainText().strip(),
            "keep_kernel_on_exit": self._keep_kernel.isChecked(),
            "beamline": self._beamline.text().strip(),
            "use_screen": self._use_screen.isChecked(),
            "launch_script": self._launch_script.text().strip(),
            "embedded_starter_script": self._embedded_starter.text().strip(),
            "ui_scale": self._ui_scale.value(),
            "device_search_paths": [
                self._device_paths_widget.item(i).text()
                for i in range(self._device_paths_widget.count())
            ],
            "device_selection": {
                cat: {name: cb.isChecked() for name, cb in names.items()}
                for cat, names in self._device_checks.items()
            },
            "device_category_overrides": dict(self._device_category_overrides),
            "databroker_catalog": self._databroker_catalog.text().strip(),
            "databroker_uri": self._databroker_uri.text().strip(),
            "databroker_nexus_dir": self._databroker_nexus_dir.text().strip(),
        }

    def accept(self) -> None:  # noqa: D102
        config.set_active_profile(self._current_profile)
        config.update(self.values())
        super().accept()
