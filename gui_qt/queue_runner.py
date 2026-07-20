"""Detached driver for the persistent plan queue (one per beamline).

Runs alongside the kernel (started like the session recorder) and is the ONLY
thing that dispatches queued plans — so the queue progresses and its per-item
status updates **independently of the GUI** (even while it is detached/closed).

* **Singleton** via an ``flock`` on ``queue_runner.lock``; extra copies self-exit,
  so it is safe to (re)launch on every kernel start/attach.
* While the queue ``state`` is ``running`` it dispatches the next ``waiting`` item
  to the kernel (as a normal, non-silent execution so it shows in the console /
  transcript), waits for the reply, and writes back ``done``/``error``.
* On error it pauses the queue (matches the interactive scheduler; a Ctrl-C /
  ``RunEngineInterrupted`` surfaces as an errored reply).
* Exits when the kernel dies.

No Qt.  Run: ``python -m gui_qt.queue_runner [<beamline>]``.
"""
from __future__ import annotations

import os
import sys
import time

try:
    import fcntl
except ImportError:
    fcntl = None

from . import config
from . import kernel_session as ks
from . import queue_store as qs


def _acquire_singleton(beamline: str):
    """Hold an exclusive lock so only one runner exists per beamline; else None."""
    path = os.path.join(ks.paths(beamline)["dir"], "queue_runner.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "w")
    if fcntl is None:
        return fh
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def _wait_reply(kc, msg_id: str, cf: str):
    """Block until the execute_reply for `msg_id`; return ok(bool) or None if kernel died."""
    while True:
        if not ks.is_alive(cf):
            return None
        try:
            msg = kc.get_shell_msg(timeout=1.0)
        except Exception:  # noqa: BLE001  (queue.Empty on timeout)
            continue
        if (
            msg.get("msg_type") == "execute_reply"
            and msg.get("parent_header", {}).get("msg_id") == msg_id
        ):
            return msg.get("content", {}).get("status") == "ok"


def main(argv: list[str]) -> int:
    beamline = argv[1] if len(argv) > 1 else config.get("beamline")
    lock = _acquire_singleton(beamline)
    if lock is None:
        return 0  # another runner is already active

    cf = ks.connection_file(beamline)
    try:
        from jupyter_client import BlockingKernelClient

        kc = BlockingKernelClient()
        kc.load_connection_file(cf)
        # shell+control only — no heartbeat thread (liveness uses ks.is_alive).
        kc.start_channels(shell=True, iopub=False, stdin=False, hb=False,
                          control=True)
    except Exception:  # noqa: BLE001
        lock.close()
        return 1

    # A leftover 'running' item means a previous runner died — flag it.
    qs.reconcile_stale_running(beamline)

    try:
        while True:
            if not ks.is_alive(cf):
                break
            data = qs.load(beamline)
            if data.get("state") == qs.S_RUNNING:
                nxt = next(
                    (it for it in data["items"] if it["status"] == qs.WAITING), None
                )
                if nxt is not None:
                    qs.set_item_status(beamline, nxt["id"], qs.RUNNING)
                    try:
                        msg_id = kc.execute(
                            nxt["command"], silent=False, store_history=True
                        )
                    except Exception:  # noqa: BLE001
                        qs.set_item_status(beamline, nxt["id"], qs.ERROR)
                        qs.set_state(beamline, qs.PAUSED)
                        continue
                    ok = _wait_reply(kc, msg_id, cf)
                    if ok is None:
                        break  # kernel died mid-plan; leave item as-is and exit
                    qs.set_item_status(
                        beamline, nxt["id"], qs.DONE if ok else qs.ERROR
                    )
                    if not ok:
                        qs.set_state(beamline, qs.PAUSED)  # stop on error
                    continue
            time.sleep(1.0)
    finally:
        try:
            kc.stop_channels()
        except Exception:  # noqa: BLE001
            pass
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
