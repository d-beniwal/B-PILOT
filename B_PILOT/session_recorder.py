"""Standalone IPython session recorder — mirrors the kernel's IOPub stream into
its experiment's persistent history record.

Run as a **detached process** alongside a kernel so the full record (cell
inputs, stdout/stderr, results, errors) is captured continuously — independent
of any GUI. Because it reads the kernel's IOPub broadcast (not a widget), it:

* keeps recording while the GUI is closed, so nothing is lost between sessions,
* captures output *live* even while the kernel is busy (the shell channel being
  blocked doesn't affect IOPub), so a reattached GUI can show what's happening
  without waiting for the running task to finish,
* sees activity from ANY client of the shared kernel (this GUI's own console,
  another attached GUI, or the detached queue runner), since IOPub is a
  pub/sub broadcast, not a point-to-point channel.

Each message is appended as its own timestamped entry via
:mod:`experiment_history` — see that module for the storage format. No Qt, no
instrument imports — just ``jupyter_client`` plus the one Qt-free B_PILOT
module. Exits when the kernel dies.

Run as a module (needs its package for the relative import)::

    python -m B_PILOT.session_recorder <connection_file> <beamline> <experiment>
"""
from __future__ import annotations

import re
import sys

from . import experiment_history as eh

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _clean(text: str) -> str:
    """Strip ANSI escape codes so the recorded text is plain and readable."""
    return _ANSI.sub("", text or "")


def _classify(msg: dict) -> tuple[str, str] | None:
    """One IOPub message -> ``(kind, text)`` for :func:`experiment_history.append_entry`,
    or ``None`` to skip it (e.g. an empty stream/result with nothing to show)."""
    mtype = msg.get("msg_type")
    content = msg.get("content", {})
    if mtype == "execute_input":
        return "input", content.get("code", "")
    if mtype == "stream":
        text = _clean(content.get("text", ""))
        return ("stream", text) if text else None
    if mtype == "execute_result":
        data = content.get("data", {}).get("text/plain", "")
        return ("result", _clean(data)) if data else None
    if mtype == "display_data":
        data = content.get("data", {}).get("text/plain", "")
        return ("display", _clean(data)) if data else None
    if mtype == "error":
        return "error", _clean("\n".join(content.get("traceback", [])))
    return None


def _alive(kc) -> bool:
    try:
        return bool(kc.is_alive())
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        sys.stderr.write(
            "usage: python -m B_PILOT.session_recorder "
            "<connection_file> <beamline> <experiment>\n"
        )
        return 2
    connection_file, beamline, experiment = argv[1], argv[2], argv[3]

    from jupyter_client import BlockingKernelClient

    kc = BlockingKernelClient()
    kc.load_connection_file(connection_file)
    kc.start_channels()

    eh.append_entry(beamline, experiment, "marker", "Recording started")
    misses = 0
    while True:
        try:
            msg = kc.get_iopub_msg(timeout=1.0)
            misses = 0
        except Exception:  # noqa: BLE001  (queue.Empty on timeout, etc.)
            misses += 1
            # Every ~5 s of silence, confirm the kernel is still there.
            if misses % 5 == 0 and not _alive(kc):
                eh.append_entry(beamline, experiment, "marker", "Kernel exited")
                break
            continue
        try:
            classified = _classify(msg)
        except Exception:  # noqa: BLE001
            classified = None
        if classified:
            kind, text = classified
            eh.append_entry(beamline, experiment, kind, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
