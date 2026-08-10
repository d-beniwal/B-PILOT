"""Client half of the B-PILOT -> MIDAS_GUI live-view bridge.

Whenever a Run/Queue dispatch involves an ``area_detector``-category device,
we resolve that device's live EPICS prefix (queried from the running kernel,
never hardcoded) and forward it to MIDAS_GUI over a local socket so its Data
Viewer can auto-start Live Data on the matching PVA channel -- zero clicks in
MIDAS_GUI. If MIDAS_GUI isn't running, or the bridge is disabled, this is a
silent no-op; a scan must never be blocked or slowed by MIDAS_GUI's presence
or absence. See MIDAS_GUI's ``midas_gui/bridge_server.py`` for the server
half of this protocol -- ``SERVER_NAME`` and the JSON message shape must
match on both sides.
"""
from __future__ import annotations

import ast
import json
import time

from PyQt5 import QtNetwork

from .plan_parser import ParamSpec  # noqa: F401  (type reference only)

SERVER_NAME = "midas_gui_live_bridge_v1"  # must match midas_gui/bridge_server.py


def area_detector_devices(params: list, values: dict) -> list:
    """Return the bare device name(s) bound to any ``area_detector``-category
    ``device``/``device_list`` param, using ``param_form.parse_values``'s
    generated (unquoted) ``RawCode`` values -- ``"pg6"`` for ``device``,
    ``"[pg6, pg7]"`` for ``device_list`` (split on comma, brackets stripped).
    Any other category/dtype, or a blank/omitted value, contributes nothing.
    """
    names: list = []
    for spec in params:
        if spec.category != "area_detector":
            continue
        val = values.get(spec.name)
        if not val:
            continue
        if spec.dtype == "device":
            names.append(str(val).strip())
        elif spec.dtype == "device_list":
            inner = str(val).strip()
            if inner.startswith("[") and inner.endswith("]"):
                inner = inner[1:-1]
            names.extend(n.strip() for n in inner.split(",") if n.strip())
    return names


def _send_live_pv(prefix: str) -> None:
    """Best-effort push of `prefix` to MIDAS_GUI; silent no-op if it's not
    listening (not running, or an older/incompatible version). Callers with
    no event loop running (e.g. queue_runner.py) need a live
    ``QCoreApplication`` instance for the blocking waits below to work."""
    sock = QtNetwork.QLocalSocket()
    try:
        sock.connectToServer(SERVER_NAME)
        if not sock.waitForConnected(150):
            return
        payload = json.dumps(
            {"type": "live_pv", "version": 1, "prefix": prefix}
        ).encode("utf-8") + b"\n"
        sock.write(payload)
        sock.waitForBytesWritten(150)
        sock.disconnectFromServer()
    except Exception:  # noqa: BLE001 — MIDAS_GUI absence must never surface here
        pass


def notify_interactive(console, det_names: list, enabled: bool) -> None:
    """GUI-process path: `console` is the live `ConsolePanel`. No-op if
    disabled or `det_names` is empty. Else asynchronously resolves each
    device's `.prefix` from the running kernel and forwards any that
    resolve; a device with no kernel object (kernel not running, name
    unbound) is silently skipped."""
    if not enabled or not det_names:
        return
    exprs = {f"prefix_{i}": f"{name}.prefix" for i, name in enumerate(det_names)}

    def _callback(result: dict) -> None:
        for key in exprs:
            prefix = result.get(key)
            if prefix:
                _send_live_pv(str(prefix))

    console.query_values(exprs, _callback)


def notify_queued_sync(kc, det_names: list, enabled: bool, timeout: float = 5.0) -> None:
    """Headless queue_runner.py path: `kc` is the runner's own
    `BlockingKernelClient` (shell channel only, no iopub). Mirrors
    `console_panel.ConsolePanel._dispatch_query_reply`'s decoding, but
    blocks on `kc.get_shell_msg` (same pattern as
    `queue_runner._wait_reply`) since this process has no Qt event loop
    driving callbacks. No-op if disabled, `det_names` is empty, the kernel
    is unreachable, or no reply arrives within `timeout` seconds."""
    if not enabled or not det_names:
        return
    exprs = {f"prefix_{i}": f"{name}.prefix" for i, name in enumerate(det_names)}
    try:
        msg_id = kc.execute("", silent=True, store_history=False, user_expressions=exprs)
    except Exception:  # noqa: BLE001
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = kc.get_shell_msg(timeout=1.0)
        except Exception:  # noqa: BLE001  (queue.Empty on timeout)
            continue
        if (
            msg.get("msg_type") != "execute_reply"
            or msg.get("parent_header", {}).get("msg_id") != msg_id
        ):
            continue
        user_exprs = msg.get("content", {}).get("user_expressions", {})
        for key in exprs:
            entry = user_exprs.get(key, {})
            if entry.get("status") != "ok":
                continue
            try:
                prefix = ast.literal_eval(entry["data"]["text/plain"])
            except Exception:  # noqa: BLE001
                continue
            if prefix:
                _send_live_pv(str(prefix))
        return
