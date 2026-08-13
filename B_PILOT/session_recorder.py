"""Standalone IPython session recorder — appends the kernel's IOPub stream to a file.

Run as a **detached process** alongside a kernel so a full transcript (cell
inputs, stdout/stderr, results, errors) is captured continuously — independent
of any GUI.  Because it reads the kernel's IOPub broadcast (not a widget), it:

* keeps recording while the GUI is closed, so nothing is lost between sessions,
* captures output *live* even while the kernel is busy (the shell channel being
  blocked doesn't affect IOPub), so a reattached GUI can show what's happening
  without waiting for the running task to finish.

No Qt, no instrument imports — just ``jupyter_client``.  Exits when the kernel
dies.

Usage::

    python session_recorder.py <connection_file> <log_file>
"""
from __future__ import annotations

import re
import sys
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _clean(text: str) -> str:
    """Strip ANSI escape codes so the transcript is plain, readable text."""
    return _ANSI.sub("", text or "")


def _format(msg: dict) -> str:
    """Render one IOPub message as transcript text (empty string to skip)."""
    mtype = msg.get("msg_type")
    content = msg.get("content", {})
    if mtype == "execute_input":
        n = content.get("execution_count", "")
        return f"\nIn [{n}]: {content.get('code', '')}\n"
    if mtype == "stream":
        return _clean(content.get("text", ""))
    if mtype == "execute_result":
        data = content.get("data", {}).get("text/plain", "")
        return f"Out[{content.get('execution_count', '')}]: {_clean(data)}\n"
    if mtype == "display_data":
        data = content.get("data", {}).get("text/plain", "")
        return (_clean(data) + "\n") if data else ""
    if mtype == "error":
        return _clean("\n".join(content.get("traceback", []))) + "\n"
    return ""


def _alive(kc) -> bool:
    try:
        return bool(kc.is_alive())
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        sys.stderr.write("usage: session_recorder.py <connection_file> <log_file>\n")
        return 2
    connection_file, log_file = argv[1], argv[2]

    from jupyter_client import BlockingKernelClient

    kc = BlockingKernelClient()
    kc.load_connection_file(connection_file)
    kc.start_channels()

    misses = 0
    with open(log_file, "a", buffering=1, encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n===== recording started {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        while True:
            try:
                msg = kc.get_iopub_msg(timeout=1.0)
                misses = 0
            except Exception:  # noqa: BLE001  (queue.Empty on timeout, etc.)
                misses += 1
                # Every ~5 s of silence, confirm the kernel is still there.
                if misses % 5 == 0 and not _alive(kc):
                    fh.write(f"\n===== kernel exited {time.strftime('%H:%M:%S')} =====\n")
                    break
                continue
            try:
                out = _format(msg)
            except Exception:  # noqa: BLE001
                out = ""
            if out:
                fh.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
