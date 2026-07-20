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

import os
import sys
from datetime import datetime

os.environ.setdefault("QT_API", "pyqt5")

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
_MAX_RUNS = 500  # cap how many runs we enumerate for responsiveness

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
    """Connection defaults: the account's databroker catalog + empty overrides."""
    return {"catalog": _catalog_from_iconfig(), "uri": "", "nexus_dir": ""}


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


def list_runs(cat, limit: int = _MAX_RUNS) -> list[tuple]:
    """Return [(uid, start, stop), …] newest first (best effort)."""
    try:
        uids = list(cat)
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple] = []
    for uid in uids[-limit:]:
        try:
            start, stop = _meta(cat[uid])
            out.append((uid, start, stop))
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda t: t[1].get("time", 0), reverse=True)
    return out


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


# ── Background connector ────────────────────────────────────────────────────────

class _Connector(QtCore.QObject):
    """Run the (blocking) catalog connect + run-listing off the GUI thread.

    ``connect_catalog`` / ``list_runs`` can block for a long time when the
    MongoDB catalog (or a Tiled server) is unreachable.  Doing it on a daemon
    thread keeps the window responsive and lets the process exit cleanly even
    mid-attempt; the result is delivered back to the GUI thread via ``done``.
    """

    done = QtCore.pyqtSignal(object, object, str)  # (catalog, rows, message)

    def start(self, catalog: str, uri: str) -> None:
        """Kick off the connection attempt on a daemon thread."""
        import threading

        threading.Thread(
            target=self._work, args=(catalog, uri), daemon=True
        ).start()

    def _work(self, catalog: str, uri: str) -> None:
        try:
            cat, msg = connect_catalog(catalog, uri)
            rows = list_runs(cat) if cat is not None else []
        except Exception as exc:  # noqa: BLE001
            cat, rows, msg = None, [], f"✗ connection failed ({_short(exc)})"
        self.done.emit(cat, rows, msg)


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
        self._connecting = False
        self._connector = _Connector()
        self._connector.done.connect(self._on_connected)

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

    # ── Toolbar (connection config) ─────────────────────────────────────────────

    def _build_toolbar(self, defaults: dict) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setObjectName("toolbar")
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)

        lay.addWidget(QtWidgets.QLabel("Catalog:"))
        self._catalog = QtWidgets.QLineEdit(defaults["catalog"])
        self._catalog.setMaximumWidth(S.px(140))
        self._catalog.setToolTip(
            "databroker catalog name (defined in ~/.local/share/intake/*.yml)"
        )
        lay.addWidget(self._catalog)

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

        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self._reconnect)
        card.body.addWidget(refresh)
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

    # ── Connect / list ───────────────────────────────────────────────────────────

    def _reconnect(self) -> None:
        """Start connecting on a background thread (never blocks the GUI)."""
        if self._connecting:
            return
        self._connecting = True
        self._connect_btn.setEnabled(False)
        self._runs.clear()
        msg = "Connecting…"
        self._conn_status.setText(msg)
        self._conn_status.setToolTip(msg)
        self.statusBar().showMessage(msg)
        self._connector.start(
            self._catalog.text().strip(),
            self._uri.text().strip(),
        )

    def _on_connected(self, cat, rows, msg: str) -> None:
        """Result from the background connector (delivered on the GUI thread)."""
        self._connecting = False
        self._connect_btn.setEnabled(True)
        self._cat = cat
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
        self.statusBar().showMessage(f"{self._runs.count()} run(s).")

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
