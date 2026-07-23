"""Bluesky data viewer — a standalone window that browses runs in the catalog.

Launched as its OWN process (``python -m gui_qt.viewer``) so it stays open
independently of the plan-runner GUI.  Left panel = list of runs in the
catalog; right panel = the selected run's information in human-readable form
(summary, full metadata, data preview, files).

MPE stores runs in **databroker catalogs backed by MongoDB** (not Tiled).  The
catalog name is chosen per account in ``instrument/iconfig.yml``
(``DATABROKER_CATALOG``: ``hexm`` for ``s20iduser`` / 20-ID-E, ``ht_hedm`` for
``s20hedm``, ``1id_hexm`` for 1-ID); the connection URIs live in
``~/.local/share/intake/*.yml``.  The viewer defaults to the current account's
catalog and connects via :func:`databroker.catalog`.  An optional **Tiled URI**
field is kept for flexibility; when set it overrides the databroker catalog.  On
a machine without the catalog it falls back to an empty temporary catalog so the
UI still runs.

Read-only: this only *reads* stored run documents; it never touches hardware.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime

os.environ.setdefault("QT_API", "pyqt5")

# pymongo has no default *read* timeout — only a connect/server-selection
# timeout — so a server that accepts the connection but then stops
# responding mid-query (a flaky replica, a dropped connection, a very slow
# unindexed range) blocks the calling thread forever with no exception ever
# raised. That's indistinguishable from "the loading spinner never ends,"
# which is exactly what turning a page beyond the first one looked like on
# redwood. A blanket socket timeout turns that failure mode into a bounded,
# per-run exception (already caught by `except Exception: continue` in
# `_page_from_uids`) instead of an unrecoverable hang. Safe to set globally
# here because the viewer always runs as its own separate process (see
# module docstring) — this never touches the EPICS/hardware process.
socket.setdefaulttimeout(30)

from PyQt5 import QtCore  # noqa: E402
from PyQt5 import QtWidgets  # noqa: E402

if __package__:
    from . import config as _config
    from . import paths as _paths
    from . import style as S
else:  # allow `python gui_qt/viewer.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from gui_qt import config as _config
    from gui_qt import paths as _paths
    from gui_qt import style as S

# MPE instrument config lives at <project_root>/instrument/iconfig.yml.
_ICONFIG = _paths.ICONFIG
_PAGE_SIZE = 500  # runs fetched per page (catalogs can hold tens of thousands)

# Fallback catalog name if it can't be resolved from iconfig by account.
# 20-ID-E (s20iduser) uses 'hexm'; see instrument/iconfig.yml.
_DEFAULT_CATALOG = "hexm"


# ── Catalog access ──────────────────────────────────────────────────────────────

def _catalog_from_iconfig() -> str:
    """Catalog name for the current account from iconfig.yml (best effort).

    ``iconfig.yml`` maps each MPE account to a ``DATABROKER_CATALOG`` (e.g.
    ``s20iduser`` → ``hexm``).  Falls back to :data:`_DEFAULT_CATALOG` if the
    file, the account entry, or the key is missing.
    """
    import getpass

    try:
        import yaml

        with open(_ICONFIG, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        acct = cfg.get(getpass.getuser())
        if isinstance(acct, dict) and acct.get("DATABROKER_CATALOG"):
            return str(acct["DATABROKER_CATALOG"])
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_CATALOG


def load_defaults() -> dict:
    """Connection defaults: the active profile's settings, else iconfig.yml auto-detect.

    ``databroker_catalog`` (Configuration → Data Viewer) takes priority when
    set; an empty profile value preserves the original zero-config behavior
    of guessing the catalog from the logged-in account.
    """
    catalog = _config.get("databroker_catalog") or _catalog_from_iconfig()
    return {
        "catalog": catalog,
        "uri": _config.get("databroker_uri") or "",
        "nexus_dir": _config.get("databroker_nexus_dir") or "",
    }


def connect_catalog(catalog: str, uri: str = "") -> tuple:
    """Return (catalog_obj, status_message).  Falls back to a temp catalog.

    Priority: explicit **Tiled URI** (optional override) → named **databroker
    catalog** → empty temporary catalog.  A live connection is only opened when
    the user clicks *Connect* (the window never auto-connects).
    """
    if uri:
        try:
            from tiled.client import from_uri

            client = from_uri(uri)
            return client, f"Connected to Tiled URI {uri}"
        except Exception as exc:  # noqa: BLE001
            return _temp_catalog(f"Tiled URI {uri} failed ({_short(exc)})")
    if catalog:
        try:
            import databroker

            cat = databroker.catalog[catalog]
            return cat, f"Connected to databroker catalog '{catalog}'"
        except KeyError:
            return _temp_catalog(
                f"catalog '{catalog}' not found — check ~/.local/share/intake/*.yml"
            )
        except Exception as exc:  # noqa: BLE001
            return _temp_catalog(f"catalog '{catalog}' failed ({_short(exc)})")
    return _temp_catalog("no catalog/URI configured")


def _temp_catalog(reason: str) -> tuple:
    """Empty temporary databroker catalog (dev fallback)."""
    try:
        import databroker

        return databroker.temp().v2, f"⚠ {reason} — showing empty temp catalog"
    except Exception as exc:  # noqa: BLE001
        return None, f"✗ no catalog: {reason}; temp fallback failed ({_short(exc)})"


def _meta(run) -> tuple[dict, dict]:
    """Return (start_doc, stop_doc) for a run, tolerant of catalog flavour."""
    md = getattr(run, "metadata", None) or {}
    try:
        start = dict(md.get("start") or {})
    except Exception:  # noqa: BLE001
        start = {}
    try:
        stop = dict(md.get("stop") or {})
    except Exception:  # noqa: BLE001
        stop = {}
    return start, stop


def _all_uids(cat) -> list:
    """Every run uid in the catalog, in catalog-native order.

    This is the expensive part on a large remote MongoDB catalog — callers
    should fetch it once per connection and reuse it across page turns
    rather than re-listing the whole catalog on every page (which is what
    made every page navigation redo this from scratch).
    """
    try:
        return list(cat)
    except Exception:  # noqa: BLE001
        return []


def _page_from_uids(cat, uids: list, offset: int, limit: int = _PAGE_SIZE, progress_cb=None) -> list:
    """Return [(uid, start, stop), …] for one page, newest first.

    ``databroker`` catalogs iterate **newest-first** natively (the same
    convention behind ``catalog[-1]`` meaning "most recent run" — the
    underlying Mongo query sorts by ``time`` descending), so ``offset``
    counts *forward* from the head of ``uids``: 0 is the most recent page.
    A previous version of this counted backward from the tail assuming
    oldest-first order, which was backwards in practice — it showed the
    *oldest* runs on page 1 instead of the newest (see DECISIONS.md).
    ``progress_cb(done, total)`` — if given — is invoked periodically while
    fetching per-run metadata, since that's a per-uid catalog round-trip and
    can be slow on a remote MongoDB catalog.
    """
    window = uids[offset:offset + limit]
    out: list[tuple] = []
    for i, uid in enumerate(window):
        try:
            start, stop = _meta(cat[uid])
            out.append((uid, start, stop))
        except Exception:  # noqa: BLE001
            continue
        if progress_cb is not None and (i % 25 == 0 or i == len(window) - 1):
            progress_cb(i + 1, len(window))
    out.sort(key=lambda t: t[1].get("time", 0), reverse=True)
    return out


def list_runs(cat, offset: int = 0, limit: int = _PAGE_SIZE, progress_cb=None) -> tuple[list, int, list]:
    """Return ([(uid, start, stop), …], total, uids) for one page, newest first.

    Convenience wrapper used for the initial connect, where there is no uid
    list to reuse yet. ``uids`` is returned so the caller can cache it for
    subsequent page turns via :func:`_page_from_uids` instead of calling this
    (and re-listing the whole catalog) again.
    """
    uids = _all_uids(cat)
    total = len(uids)
    rows = _page_from_uids(cat, uids, offset, limit, progress_cb=progress_cb)
    return rows, total, uids


def _page_window(current: int, total: int, radius: int = 2) -> list:
    """Page numbers (1-indexed) to render as buttons, ``None`` marking a gap.

    Always includes the first and last page plus a ``radius``-wide window
    around ``current``, so long run lists (e.g. 100 pages) render as
    ``1 … 8 9 [10] 11 12 … 100`` instead of a hundred buttons.
    """
    if total <= 0:
        return []
    if total == 1:
        return [1]
    pages = {1, total, current}
    for d in range(1, radius + 1):
        pages.add(current - d)
        pages.add(current + d)
    ordered = sorted(p for p in pages if 1 <= p <= total)
    out: list = []
    prev = None
    for p in ordered:
        if prev is not None and p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out


def list_catalogs() -> tuple[list[str], str]:
    """Return (names, error) — every catalog registered for this account.

    Backed by ``databroker.catalog``, a dict-like registry populated from
    ``~/.local/share/intake/*.yml``. Best effort: on any failure, returns an
    empty list and a short error message instead of raising.
    """
    try:
        import databroker

        return sorted(str(name) for name in databroker.catalog), ""
    except Exception as exc:  # noqa: BLE001
        return [], _short(exc)


# ── Formatting helpers ──────────────────────────────────────────────────────────

def _fmt_time(epoch) -> str:
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return "—"


def _fmt_duration(start: dict, stop: dict) -> str:
    try:
        return f"{float(stop['time']) - float(start['time']):.1f} s"
    except Exception:  # noqa: BLE001
        return "—"


def _esc(v) -> str:
    import html

    return html.escape(str(v))


def _short(value, limit: int = 160) -> str:
    """Truncate a (possibly huge) message so it can't blow up the layout."""
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _payload_to_text(payload: dict) -> str:
    """Flat, human-readable rendering of an export payload (for a .txt export)."""
    lines: list[str] = []
    for section, content in payload.items():
        lines.append(f"=== {section} ===")
        if isinstance(content, dict):
            for k, v in content.items():
                lines.append(f"{k}: {v}")
        elif isinstance(content, list):
            for i, item in enumerate(content):
                lines.append(f"[{i}] {item}")
        else:
            lines.append(str(content))
        lines.append("")
    return "\n".join(lines)


# ── Background catalog worker ───────────────────────────────────────────────────

class _CatalogWorker(QtCore.QObject):
    """Single persistent background thread that owns ALL catalog access.

    Some databroker/intake catalog backends bind an internal async client
    (e.g. a Motor/asyncio event loop) to whichever thread first touches
    them. The previous design spun up a *fresh* ad-hoc ``threading.Thread``
    for every connect/discover/page-fetch — so the catalog object handed
    back from the connect thread could end up wired to that thread's now-
    dead event loop, and a later page fetch on a *different* new thread
    would then hang indefinitely with no exception ever raised. Routing
    every catalog-touching call through one long-lived thread keeps that
    internal state anchored to a single, always-running thread.

    Also caches the full uid listing (:func:`_all_uids`) from the connect
    call and reuses it for every page turn — re-listing the *entire*
    catalog (which can be tens of thousands of runs) on every page click
    was itself a likely source of the "page 2 hangs" symptom, since it's
    pure added latency on top of the per-run metadata fetch, and gave no
    feedback while it ran. ``progress`` reports per-run fetch progress
    within a page so a genuinely slow remote MongoDB catalog is visible as
    "crawling" rather than indistinguishable from a hang.
    """

    connected = QtCore.pyqtSignal(object, object, int, str)  # (catalog, rows, total, message)
    paged = QtCore.pyqtSignal(object, int, str)  # (rows, total, error)
    discovered = QtCore.pyqtSignal(list, str)  # (names, error)
    progress = QtCore.pyqtSignal(str)  # human-readable progress text

    def __init__(self) -> None:
        super().__init__()
        import queue
        import threading

        self._queue = queue.Queue()
        self._uids_cat = None  # catalog object the cache below belongs to
        self._uids_cache: list = []
        threading.Thread(target=self._run, daemon=True).start()

    def _uids_for(self, cat) -> list:
        """Full uid listing for ``cat``, from cache when it's still current."""
        if cat is self._uids_cat:
            return self._uids_cache
        uids = _all_uids(cat)
        self._uids_cat = cat
        self._uids_cache = uids
        return uids

    def _run(self) -> None:
        while True:
            kind, args = self._queue.get()
            if kind == "connect":
                catalog, uri = args
                try:
                    cat, msg = connect_catalog(catalog, uri)
                    # Force a fresh listing on every explicit connect/Refresh,
                    # even if the catalog client happens to hand back the same
                    # object as before (databroker.catalog can do this).
                    self._uids_cat = None
                    if cat is None:
                        rows, total = [], 0
                    else:
                        self.progress.emit("Listing runs…")
                        uids = self._uids_for(cat)
                        total = len(uids)
                        rows = _page_from_uids(
                            cat, uids, 0, _PAGE_SIZE, progress_cb=self._report_progress
                        )
                except Exception as exc:  # noqa: BLE001
                    cat, rows, total, msg = None, [], 0, f"✗ connection failed ({_short(exc)})"
                self.connected.emit(cat, rows, total, msg)
            elif kind == "page":
                cat, offset = args
                try:
                    uids = self._uids_for(cat)
                    total = len(uids)
                    rows = _page_from_uids(
                        cat, uids, offset, _PAGE_SIZE, progress_cb=self._report_progress
                    )
                    self.paged.emit(rows, total, "")
                except Exception as exc:  # noqa: BLE001
                    self.paged.emit([], 0, f"✗ failed to load page ({_short(exc)})")
            elif kind == "discover":
                names, err = list_catalogs()
                self.discovered.emit(names, err)

    def _report_progress(self, done: int, total: int) -> None:
        self.progress.emit(f"Fetching run metadata… {done}/{total}")

    def connect(self, catalog: str, uri: str) -> None:
        """Queue a connect + first-page listing."""
        self._queue.put(("connect", (catalog, uri)))

    def fetch_page(self, cat, offset: int) -> None:
        """Queue a page fetch from an already-connected catalog."""
        self._queue.put(("page", (cat, offset)))

    def discover(self) -> None:
        """Queue enumeration of every registered databroker catalog."""
        self._queue.put(("discover", None))


# ── Export settings dialog ───────────────────────────────────────────────────────

# key (config.DEFAULTS["viewer_export_fields"]) -> checkbox label
_EXPORT_FIELD_LABELS = {
    "summary": "Run summary (scan ID, plan, timing, exit status)",
    "start_metadata": "Full start-document metadata",
    "stop_metadata": "Full stop-document metadata",
    "notes": "Run notes",
    "file_references": "File/path references",
    "data_preview": "Data preview table (first 100 rows of the primary stream)",
}


class ExportConfigDialog(QtWidgets.QDialog):
    """Checkboxes controlling what "Export run…" writes out.

    Persisted per-profile as `viewer_export_fields` (see gui_qt/config.py) —
    editable here only; nothing is written until Save.
    """

    def __init__(self, current: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Data Viewer — Export settings")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(
            QtWidgets.QLabel("Choose what to include when exporting a run:")
        )
        self._checks: dict[str, QtWidgets.QCheckBox] = {}
        for key, label in _EXPORT_FIELD_LABELS.items():
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(bool(current.get(key, key != "data_preview")))
            self._checks[key] = cb
            lay.addWidget(cb)
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def values(self) -> dict:
        return {k: cb.isChecked() for k, cb in self._checks.items()}


# ── Main window ─────────────────────────────────────────────────────────────────

class ViewerWindow(QtWidgets.QMainWindow):
    """Browse catalog runs (left) and view one run's details (right)."""

    def __init__(self, defaults: dict) -> None:
        """Build the UI, then connect to the catalog and populate the run list."""
        super().__init__()
        self.setWindowTitle("Bluesky Data Viewer")
        self.resize(S.px(1300), S.px(820))
        self._cat = None
        self._current_run = None
        self._current_uid: str | None = None
        self._current_start: dict = {}
        self._current_stop: dict = {}
        self._connecting = False
        self._offset = 0
        self._total = 0
        self._shown_count = 0
        self._paging = False
        self._pending_offset = 0
        self._discovering = False
        self._worker = _CatalogWorker()
        self._worker.connected.connect(self._on_connected)
        self._worker.paged.connect(self._on_page_loaded)
        self._worker.discovered.connect(self._on_catalogs_found)
        self._worker.progress.connect(self._on_worker_progress)

        self._build_menu()

        central = QtWidgets.QWidget()
        clay = QtWidgets.QVBoxLayout(central)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(0)
        clay.addWidget(self._build_toolbar(defaults))

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.addWidget(self._build_run_list())
        split.addWidget(self._build_detail_panel())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([S.px(360), S.px(940)])
        clay.addWidget(split, 1)
        self.setCentralWidget(central)
        # Do NOT auto-connect: the window opens immediately; the user clicks
        # "Connect" to reach the (possibly unreachable) server on demand.
        self._conn_status.setText("Not connected — click Connect")
        self.statusBar().showMessage("Click “Connect” to load runs from the catalog.")

    # ── Menu (export) ────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        self._export_act = m.addAction("Export run…")
        self._export_act.setShortcut("Ctrl+E")
        self._export_act.setToolTip(
            "Save the selected run's metadata to a file (JSON or text) — "
            "choose what's included in File → Export settings…."
        )
        self._export_act.setEnabled(False)
        self._export_act.triggered.connect(self._export_run)
        m.addAction("Export settings…").triggered.connect(self._open_export_settings)

    def _open_export_settings(self) -> None:
        dlg = ExportConfigDialog(_config.get("viewer_export_fields") or {}, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            _config.update({"viewer_export_fields": dlg.values()})
            self.statusBar().showMessage("Export settings saved.")

    def _export_run(self) -> None:
        if self._current_uid is None:
            self.statusBar().showMessage("Select a run first.")
            return
        default_name = f"run_{self._current_start.get('scan_id', self._current_uid[:8])}.json"
        path, chosen_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export run", default_name, "JSON (*.json);;Text (*.txt)"
        )
        if not path:
            return
        as_text = path.lower().endswith(".txt") or "Text" in chosen_filter
        payload = self._build_export_payload()
        try:
            if as_text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(_payload_to_text(payload))
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2, default=str)
        except OSError as exc:
            self.statusBar().showMessage(f"Export failed: {exc}")
            return
        self.statusBar().showMessage(f"Exported to {path}")

    def _build_export_payload(self) -> dict:
        """Assemble the sections enabled in `viewer_export_fields` for the
        currently-selected run, reusing the same data the detail tabs show."""
        fields = _config.get("viewer_export_fields") or {}
        uid, start, stop = self._current_uid, self._current_start, self._current_stop
        payload: dict = {}
        if fields.get("summary", True):
            payload["summary"] = {
                "scan_id": start.get("scan_id", "—"),
                "plan_name": start.get("plan_name", "—"),
                "started": _fmt_time(start.get("time")),
                "duration": _fmt_duration(start, stop),
                "exit_status": stop.get("exit_status", "—"),
                "num_events": stop.get("num_events", "—"),
                "uid": uid,
            }
        if fields.get("start_metadata", True):
            payload["start_metadata"] = start
        if fields.get("stop_metadata", True):
            payload["stop_metadata"] = stop
        if fields.get("notes", True) and start.get("notes"):
            payload["notes"] = start.get("notes")
        if fields.get("file_references", True):
            payload["file_references"] = {
                k: v
                for k, v in start.items()
                if any(t in k.lower() for t in ("file", "path", "nexus", "dir"))
            }
        if fields.get("data_preview", False) and self._current_run is not None:
            df = self._data_preview_df()
            if df is not None:
                payload["data_preview"] = df.head(100).to_dict(orient="records")
        return payload

    def _data_preview_df(self):
        """Best-effort DataFrame for export — loads on demand even if the user
        never clicked "Load data preview" for this run."""
        run = self._current_run
        try:
            streams = list(run)
        except Exception:  # noqa: BLE001
            return None
        if not streams:
            return None
        stream = "primary" if "primary" in streams else streams[0]
        return self._read_stream_df(run, stream)

    # ── Toolbar (connection config) ─────────────────────────────────────────────

    def _build_toolbar(self, defaults: dict) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setObjectName("toolbar")
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)

        lay.addWidget(QtWidgets.QLabel("Catalog:"))
        self._catalog = S.NoScrollComboBox()
        self._catalog.setEditable(True)
        self._catalog.addItem(defaults["catalog"])
        self._catalog.setCurrentText(defaults["catalog"])
        self._catalog.setMaximumWidth(S.px(160))
        self._catalog.setToolTip(
            "databroker catalog name (defined in ~/.local/share/intake/*.yml) — "
            "click Discover to list what's registered for this account"
        )
        lay.addWidget(self._catalog)

        self._discover_btn = QtWidgets.QPushButton("Discover")
        self._discover_btn.setToolTip(
            "List all databroker catalogs registered for this account"
        )
        self._discover_btn.clicked.connect(self._discover_catalogs)
        lay.addWidget(self._discover_btn)

        lay.addWidget(QtWidgets.QLabel("Tiled URI (override):"))
        self._uri = QtWidgets.QLineEdit(defaults["uri"])
        self._uri.setPlaceholderText("http://host:8000  (optional, overrides catalog)")
        self._uri.setMinimumWidth(S.px(180))
        lay.addWidget(self._uri, 1)

        lay.addWidget(QtWidgets.QLabel("NeXus dir:"))
        self._nexus_dir = QtWidgets.QLineEdit(defaults["nexus_dir"])
        self._nexus_dir.setPlaceholderText("(optional) folder holding NeXus files")
        self._nexus_dir.setMinimumWidth(S.px(140))
        lay.addWidget(self._nexus_dir, 1)

        self._connect_btn = QtWidgets.QPushButton("Connect")
        self._connect_btn.clicked.connect(self._reconnect)
        lay.addWidget(self._connect_btn)

        self._conn_status = QtWidgets.QLabel("")
        self._conn_status.setStyleSheet(f"color: {S.MUTED};")
        # Cap the width so a long connection message can't force the window wider
        # than the screen (a non-wrapping QLabel's minimum size grows to its text).
        self._conn_status.setMaximumWidth(S.px(360))
        self._conn_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self._conn_status)
        return bar

    # ── Left: run list ───────────────────────────────────────────────────────────

    def _build_run_list(self) -> QtWidgets.QWidget:
        card = S.make_card("Runs")
        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText("filter (scan id / plan / uid)…")
        self._filter.textChanged.connect(self._apply_filter)
        card.body.addWidget(self._filter)

        self._runs = QtWidgets.QListWidget()
        self._runs.currentItemChanged.connect(self._on_run_selected)
        card.body.addWidget(self._runs, 1)

        self._page_label = QtWidgets.QLabel("Not connected.")
        self._page_label.setStyleSheet(f"color: {S.MUTED};")
        card.body.addWidget(self._page_label)

        btn_row = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self._reconnect)
        btn_row.addWidget(refresh)
        btn_row.addStretch(1)
        card.body.addLayout(btn_row)

        self._page_bar = QtWidgets.QHBoxLayout()
        self._page_bar.setSpacing(2)
        card.body.addLayout(self._page_bar)

        card.setMinimumWidth(S.px(300))
        return card

    # ── Right: detail tabs ───────────────────────────────────────────────────────

    def _build_detail_panel(self) -> QtWidgets.QWidget:
        self._tabs = QtWidgets.QTabWidget()

        self._summary = QtWidgets.QTextBrowser()
        self._tabs.addTab(self._summary, "Summary")

        self._meta_tree = QtWidgets.QTreeWidget()
        self._meta_tree.setColumnCount(2)
        self._meta_tree.setHeaderLabels(["Field", "Value"])
        self._meta_tree.setAlternatingRowColors(True)
        self._tabs.addTab(self._meta_tree, "Metadata")

        data_tab = QtWidgets.QWidget()
        dlay = QtWidgets.QVBoxLayout(data_tab)
        self._data_info = QtWidgets.QLabel("Select a run.")
        self._data_info.setWordWrap(True)
        dlay.addWidget(self._data_info)
        self._data_btn = QtWidgets.QPushButton("Load data preview")
        self._data_btn.clicked.connect(self._load_data_preview)
        self._data_btn.setEnabled(False)
        dlay.addWidget(self._data_btn)
        self._data_table = QtWidgets.QTableWidget()
        dlay.addWidget(self._data_table, 1)
        self._tabs.addTab(data_tab, "Data")

        self._files = QtWidgets.QTextBrowser()
        self._tabs.addTab(self._files, "Files")

        return self._tabs

    # ── Catalog discovery ────────────────────────────────────────────────────────

    def _discover_catalogs(self) -> None:
        """List every databroker catalog registered for this account."""
        if self._discovering:
            return
        self._discovering = True
        self._discover_btn.setEnabled(False)
        self.statusBar().showMessage("Discovering catalogs…")
        self._worker.discover()

    def _on_catalogs_found(self, names: list, err: str) -> None:
        self._discovering = False
        self._discover_btn.setEnabled(True)
        if err:
            self.statusBar().showMessage(f"✗ catalog discovery failed ({err})")
            return
        current = self._catalog.currentText().strip()
        self._catalog.clear()
        self._catalog.addItems(names)
        self._catalog.setCurrentText(current)
        if names:
            self.statusBar().showMessage(f"Found {len(names)} catalog(s): {', '.join(names)}")
        else:
            self.statusBar().showMessage("No catalogs found for this account.")

    # ── Connect / list ───────────────────────────────────────────────────────────

    def _reconnect(self) -> None:
        """Start connecting on a background thread (never blocks the GUI)."""
        if self._connecting:
            return
        self._connecting = True
        self._connect_btn.setEnabled(False)
        self._clear_page_bar()
        self._runs.clear()
        msg = "Connecting…"
        self._conn_status.setText(msg)
        self._conn_status.setToolTip(msg)
        self.statusBar().showMessage(msg)
        self._worker.connect(
            self._catalog.currentText().strip(),
            self._uri.text().strip(),
        )

    def _on_connected(self, cat, rows, total: int, msg: str) -> None:
        """Result from the background connector (delivered on the GUI thread)."""
        self._connecting = False
        self._connect_btn.setEnabled(True)
        self._cat = cat
        self._offset = 0
        self._total = total
        self._conn_status.setText(msg)
        self._conn_status.setToolTip(msg)   # full text on hover (label is width-capped)
        self.statusBar().showMessage(msg)
        self._populate_runs(rows)

    def _populate_runs(self, rows) -> None:
        self._runs.clear()
        for uid, start, stop in rows:
            sid = start.get("scan_id", "?")
            plan = start.get("plan_name", "?")
            when = _fmt_time(start.get("time")) if start.get("time") else "—"
            item = QtWidgets.QListWidgetItem(f"#{sid}   {plan}\n   {when}   {uid[:8]}")
            item.setData(QtCore.Qt.UserRole, (uid, start, stop))
            self._runs.addItem(item)
        self._shown_count = len(rows)
        self._update_page_label()
        self.statusBar().showMessage(f"{self._runs.count()} run(s) loaded.")

    def _current_page(self) -> int:
        """1-indexed page number for the currently-shown offset."""
        return self._offset // _PAGE_SIZE + 1

    def _total_pages(self) -> int:
        if self._total <= 0:
            return 0
        return -(-self._total // _PAGE_SIZE)  # ceil division

    def _update_page_label(self) -> None:
        if self._total == 0:
            self._page_label.setText("No runs found." if self._cat is not None else "Not connected.")
            self._clear_page_bar()
            return
        first = self._offset + 1
        last = self._offset + self._shown_count
        self._page_label.setText(
            f"Showing: {first:,}–{last:,}  |  Available: {self._total:,}  "
            f"|  Page {self._current_page()} of {self._total_pages()}"
        )
        self._rebuild_page_bar()

    def _clear_page_bar(self) -> None:
        while self._page_bar.count():
            item = self._page_bar.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild_page_bar(self) -> None:
        """Render Prev / page-number / Next buttons for the current page."""
        self._clear_page_bar()
        total_pages = self._total_pages()
        if total_pages <= 1:
            return
        current = self._current_page()

        prev_btn = QtWidgets.QPushButton("‹ Prev")
        prev_btn.setEnabled(not self._paging and current > 1)
        prev_btn.clicked.connect(lambda: self._go_to_page(current - 1))
        self._page_bar.addWidget(prev_btn)

        for entry in _page_window(current, total_pages):
            if entry is None:
                dots = QtWidgets.QLabel("…")
                dots.setStyleSheet(f"color: {S.MUTED};")
                self._page_bar.addWidget(dots)
                continue
            btn = QtWidgets.QPushButton(str(entry))
            btn.setCheckable(True)
            btn.setChecked(entry == current)
            btn.setEnabled(not self._paging and entry != current)
            btn.setFixedWidth(S.px(36))
            btn.clicked.connect(lambda _checked, p=entry: self._go_to_page(p))
            self._page_bar.addWidget(btn)

        next_btn = QtWidgets.QPushButton("Next ›")
        next_btn.setEnabled(not self._paging and current < total_pages)
        next_btn.clicked.connect(lambda: self._go_to_page(current + 1))
        self._page_bar.addWidget(next_btn)
        self._page_bar.addStretch(1)

    def _go_to_page(self, page: int) -> None:
        """Fetch the given 1-indexed page of runs."""
        if self._cat is None or self._paging:
            return
        total_pages = self._total_pages()
        page = max(1, min(page, total_pages))
        offset = (page - 1) * _PAGE_SIZE
        if offset == self._offset:
            return
        self._paging = True
        self._pending_offset = offset
        self._rebuild_page_bar()  # disables buttons while the page loads
        self.statusBar().showMessage(f"Loading page {page}…")
        self._worker.fetch_page(self._cat, offset)

    def _on_page_loaded(self, rows, total: int, err: str) -> None:
        self._paging = False
        if err:
            self.statusBar().showMessage(err)
            self._rebuild_page_bar()
            return
        self._offset = self._pending_offset
        self._total = total
        self._populate_runs(rows)

    def _on_worker_progress(self, msg: str) -> None:
        """Live progress from the catalog worker (listing / per-run fetch)."""
        self.statusBar().showMessage(msg)

    def _apply_filter(self, text: str) -> None:
        text = text.lower().strip()
        for i in range(self._runs.count()):
            item = self._runs.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    # ── Run selection → populate tabs ────────────────────────────────────────────

    def _on_run_selected(self, cur, _prev) -> None:
        if cur is None:
            return
        uid, start, stop = cur.data(QtCore.Qt.UserRole)
        self._current_uid, self._current_start, self._current_stop = uid, start, stop
        try:
            self._current_run = self._cat[uid]
        except Exception:  # noqa: BLE001
            self._current_run = None
        self._fill_summary(uid, start, stop)
        self._fill_metadata(start, stop)
        self._fill_files(start, stop)
        self._data_table.clear()
        self._data_table.setRowCount(0)
        self._data_table.setColumnCount(0)
        self._data_info.setText("Click “Load data preview” to read the primary stream.")
        self._data_btn.setEnabled(self._current_run is not None)
        self._export_act.setEnabled(True)

    def _fill_summary(self, uid: str, start: dict, stop: dict) -> None:
        rows = [
            ("Scan ID", start.get("scan_id", "—")),
            ("Plan", start.get("plan_name", "—")),
            ("Started", _fmt_time(start.get("time"))),
            ("Duration", _fmt_duration(start, stop)),
            ("Exit status", stop.get("exit_status", "—")),
            ("# events", stop.get("num_events", "—")),
            ("UID", uid),
        ]
        for key in ("beamline_id", "instrument_name", "proposal_id", "sample"):
            if key in start:
                rows.append((key.replace("_", " ").title(), start[key]))
        notes = start.get("notes")

        html = ["<style>td{padding:2px 12px 2px 0;vertical-align:top;}</style><table>"]
        for k, v in rows:
            html.append(
                f"<tr><td><b>{_esc(k)}</b></td><td>{_esc(v)}</td></tr>"
            )
        html.append("</table>")
        if notes:
            html.append(
                f"<h3 style='margin-top:14px;'>Run notes</h3>"
                f"<div style='white-space:pre-wrap;'>{_esc(notes)}</div>"
            )
        self._summary.setHtml("".join(html))

    def _fill_metadata(self, start: dict, stop: dict) -> None:
        self._meta_tree.clear()
        for title, doc in (("start", start), ("stop", stop)):
            top = QtWidgets.QTreeWidgetItem([title, ""])
            self._meta_tree.addTopLevelItem(top)
            self._add_tree(top, doc)
            top.setExpanded(True)
        self._meta_tree.resizeColumnToContents(0)

    def _add_tree(self, parent, obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                self._add_node(parent, str(k), v)
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                self._add_node(parent, f"[{i}]", v)

    def _add_node(self, parent, key, value) -> None:
        if isinstance(value, (dict, list, tuple)) and value:
            node = QtWidgets.QTreeWidgetItem([key, ""])
            parent.addChild(node)
            self._add_tree(node, value)
        else:
            parent.addChild(QtWidgets.QTreeWidgetItem([key, str(value)]))

    def _fill_files(self, start: dict, stop: dict) -> None:
        # File-ish metadata (paths injected into md, e.g. file_name/path/nexus).
        hits = []
        for k, v in start.items():
            kl = k.lower()
            if any(t in kl for t in ("file", "path", "nexus", "dir")):
                hits.append((k, v))
        html = ["<h3>File references (from run metadata)</h3>"]
        if hits:
            html.append("<table>")
            for k, v in hits:
                html.append(
                    f"<tr><td><b>{_esc(k)}</b></td>"
                    f"<td style='white-space:pre-wrap;'>{_esc(v)}</td></tr>"
                )
            html.append("</table>")
        else:
            html.append(
                "<p><i>No file paths found in this run's metadata.</i> "
                "Detector HDF frames are linked via the run's resource documents; "
                "inject NeXus/output paths into the plan's <code>md</code> to see "
                "them here.</p>"
            )
        nx = self._nexus_dir.text().strip()
        if nx:
            html.append(
                f"<h3>Configured NeXus dir</h3><div>{_esc(nx)}</div>"
            )
        self._files.setHtml("".join(html))

    def _load_data_preview(self) -> None:
        run = self._current_run
        if run is None:
            return
        try:
            streams = list(run)
        except Exception as exc:  # noqa: BLE001
            self._data_info.setText(f"Could not list streams: {exc}")
            return
        if not streams:
            self._data_info.setText("This run has no readable streams.")
            return
        stream = "primary" if "primary" in streams else streams[0]
        self._data_info.setText(f"Streams: {', '.join(streams)} — showing '{stream}'.")
        df = self._read_stream_df(run, stream)
        if df is None:
            self._data_info.setText(
                f"Streams: {', '.join(streams)} — preview of '{stream}' unavailable "
                "(non-scalar or unreadable data)."
            )
            return
        self._show_df(df.head(100))

    @staticmethod
    def _read_stream_df(run, stream):
        """Best-effort: return a pandas DataFrame of scalar columns, or None."""
        node = None
        try:
            node = run[stream]
        except Exception:  # noqa: BLE001
            return None
        ds = None
        for reader in (
            lambda: node.read(),
            lambda: node["data"].read(),
            lambda: node.to_dask(),
        ):
            try:
                ds = reader()
                break
            except Exception:  # noqa: BLE001
                continue
        if ds is None:
            return None
        try:
            df = ds.to_dataframe() if hasattr(ds, "to_dataframe") else ds
            # keep only scalar-per-event columns (drop image/array fields)
            keep = [
                c for c in df.columns
                if df[c].map(lambda x: getattr(x, "ndim", 0)).max() == 0
            ]
            return df[keep] if keep else df
        except Exception:  # noqa: BLE001
            return None

    def _show_df(self, df) -> None:
        self._data_table.clear()
        cols = list(df.columns)
        self._data_table.setColumnCount(len(cols))
        self._data_table.setRowCount(len(df))
        self._data_table.setHorizontalHeaderLabels([str(c) for c in cols])
        for r in range(len(df)):
            for c, col in enumerate(cols):
                self._data_table.setItem(
                    r, c, QtWidgets.QTableWidgetItem(str(df.iloc[r][col]))
                )
        self._data_table.resizeColumnsToContents()


def main() -> None:
    """Launch the standalone Bluesky data viewer."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Bluesky Data Viewer")
    S.set_scale(_config.get("ui_scale"))
    S.apply_theme(app)
    win = ViewerWindow(load_defaults())
    # Never open larger than the screen.
    screen = app.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        win.resize(min(S.px(1300), avail.width() - 80), min(S.px(820), avail.height() - 80))
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
