"""Single-instance, detachable Bluesky IPython kernel — hosted in a screen session.

Goal: at most **one** interactive kernel per beamline, that survives the GUI and
can be reattached (by the GUI, or by a human via ``screen``).

Design (mirrors how ``qserver.sh`` keeps the queueserver in a screen session):

* **Fixed per-beamline paths** — the connection file lives at a deterministic
  path (``<session_dir>/<beamline>/kernel.json``), so "is a session running?" and
  "how do I attach?" both reduce to that one file.  No random UUIDs to track.
* **Liveness = heartbeat, not PID** — :func:`is_alive` connects to the connection
  file and pings the kernel's heartbeat channel.  This is authoritative even when
  the kernel is busy running a plan, and immune to PID reuse / stale files.
* **Hosted in a named ``screen`` session** — ``bluesky-kernel-<beamline>``.  This
  supervises the process independently of the GUI and gives staff a terminal
  fallback (``screen -r bluesky-kernel-<beamline>`` shows/were the kernel runs).
* **Single-instance launch** — :func:`launch` refuses if a kernel already answers
  on the connection file (the GUI then offers to *attach* instead); otherwise it
  cleans any stale session/file and starts fresh.

No Qt here, so this doubles as a CLI:  ``python -m gui_qt.kernel_session
status|stop|launch [--beamline B]`` — a ``qserver.sh``-style handle for staff.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

from . import config
from . import paths as _paths


# ── Paths / names ────────────────────────────────────────────────────────────────

def session_name(beamline: str) -> str:
    """The screen session name hosting this beamline's kernel."""
    return f"bluesky-kernel-{beamline}"


def paths(beamline: str) -> dict:
    """Fixed per-beamline file paths (connection file, sidecar, transcript)."""
    base = os.path.join(os.path.expanduser(config.get("session_dir")), beamline)
    return {
        "dir": base,
        "connection_file": os.path.join(base, "kernel.json"),
        "sidecar": os.path.join(base, "session.json"),
        "log": os.path.join(base, "kernel.log"),
    }


def connection_file(beamline: str) -> str:
    """Deterministic connection-file path for `beamline` (the attach handle)."""
    return paths(beamline)["connection_file"]


# ── Liveness (heartbeat) ─────────────────────────────────────────────────────────

def is_alive(cf: str, timeout: float = 2.0) -> bool:
    """True if a kernel answers on connection file `cf` (heartbeat ping).

    Pings the kernel's heartbeat port directly with a single ZMQ REQ socket that
    is fully closed afterwards — cheap, leak-free (no accumulating fds), needs no
    session key, and works while the kernel is BUSY (the heartbeat is
    independent of the blocked shell channel).  Immune to PID reuse / stale files.
    """
    if not cf or not os.path.exists(cf):
        return False
    try:
        with open(cf, encoding="utf-8") as fh:
            info = json.load(fh)
    except Exception:  # noqa: BLE001
        return False
    hb = info.get("hb_port")
    if not hb:
        return False
    addr = f"{info.get('transport', 'tcp')}://{info.get('ip', '127.0.0.1')}:{hb}"
    try:
        import zmq
    except Exception:  # noqa: BLE001
        return False
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.LINGER, 0)
    try:
        sock.connect(addr)
        sock.send(b"ping")
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        if dict(poller.poll(int(timeout * 1000))).get(sock) == zmq.POLLIN:
            sock.recv()
            return True
        return False
    except Exception:  # noqa: BLE001
        return False
    finally:
        sock.close(0)
        ctx.term()


# ── Sidecar (metadata for messages / status) ─────────────────────────────────────

def read_info(beamline: str) -> dict | None:
    """Read the sidecar session metadata, or None."""
    try:
        with open(paths(beamline)["sidecar"], encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _write_info(beamline: str, info: dict) -> None:
    try:
        with open(paths(beamline)["sidecar"], "w", encoding="utf-8") as fh:
            json.dump(info, fh, indent=2)
    except Exception:  # noqa: BLE001
        pass


# ── screen helpers ───────────────────────────────────────────────────────────────

def screen_available() -> bool:
    """True if the ``screen`` binary is on PATH."""
    return shutil.which("screen") is not None


def _screen_running(name: str) -> bool:
    """True if a screen session named `name` exists (Attached or Detached)."""
    try:
        out = subprocess.run(
            ["screen", "-ls"], capture_output=True, text=True
        ).stdout
    except Exception:  # noqa: BLE001
        return False
    # lines look like:  \t12345.bluesky-kernel-20ide\t(Detached)
    return re.search(rf"\.{re.escape(name)}\s", out) is not None


def _screen_start(name: str, argv: list[str], cwd: str | None) -> None:
    """Start `argv` in a new detached screen session named `name`."""
    subprocess.Popen(
        ["screen", "-dmS", name, *argv],
        cwd=cwd or None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _screen_quit(name: str) -> None:
    """Terminate the screen session `name` (no-op if absent)."""
    try:
        subprocess.run(
            ["screen", "-S", name, "-X", "quit"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001
        pass


def _kernel_pid(cf: str) -> int | None:
    """PID of the ipykernel **python** process for connection file `cf`.

    Used both to record a PID at launch and to send interrupt signals.  In screen
    mode several processes share the connection file in their argv (SCREEN, the
    login shell, and python) — only the python one is the real kernel to signal,
    so we filter by process name and fall back to the first match otherwise.
    """
    try:
        pids = subprocess.run(
            ["pgrep", "-f", cf], capture_output=True, text=True
        ).stdout.split()
    except Exception:  # noqa: BLE001
        return None
    fallback = None
    for p in pids:
        try:
            comm = subprocess.run(
                ["ps", "-o", "comm=", "-p", p], capture_output=True, text=True
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            comm = ""
        if fallback is None:
            fallback = p
        if "python" in os.path.basename(comm).lower():
            try:
                return int(p)
            except ValueError:
                pass
    try:
        return int(fallback) if fallback else None
    except (ValueError, TypeError):
        return None


# ── Launch / stop / status ───────────────────────────────────────────────────────

def _wait_for_connection_file(cf: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(cf):
            try:
                with open(cf, encoding="utf-8") as fh:
                    if json.load(fh).get("shell_port"):
                        return True
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.1)
    return False


def launch(
    beamline: str,
    cwd: str | None = None,
    *,
    python: str | None = None,
    use_screen: bool | None = None,
) -> tuple[str, dict]:
    """Ensure exactly one kernel for `beamline`; start it if none is running.

    Returns ``(status, info)`` where status is:

    * ``"already_running"`` — a live kernel answered; `info` describes it (the
      caller should offer to *attach* instead of starting a second one).
    * ``"started"`` — a fresh kernel was launched; `info` has its paths.
    * ``"error"`` — could not start; `info["error"]` explains.
    """
    python = python or sys.executable
    # Default the kernel's working directory to the project root so that
    # ``from instrument.collection import *`` resolves no matter where the GUI
    # (or this CLI) was invoked from.
    cwd = cwd or _paths.KERNEL_CWD_DEFAULT
    if use_screen is None:
        use_screen = bool(config.get("use_screen"))
    p = paths(beamline)
    cf = p["connection_file"]
    name = session_name(beamline)

    # 1) Single-instance guard: a live kernel already owns this connection file.
    if is_alive(cf):
        info = read_info(beamline) or {}
        info.update({"beamline": beamline, "session_name": name,
                     "connection_file": cf, "log": p["log"]})
        return "already_running", info

    # 2) Not alive — clear any stale session/files so we start clean (and so the
    #    kernel picks fresh ZMQ ports rather than reusing a dead file's).
    if use_screen and screen_available():
        _screen_quit(name)
    for stale in (cf, p["sidecar"]):
        try:
            os.remove(stale)
        except OSError:
            pass
    os.makedirs(p["dir"], exist_ok=True)

    # 3) Start the kernel.  Preferred: a starter script that activates the env +
    #    records the experiment (like blueskyStarter.sh) and starts an ipykernel
    #    in a screen session at `cf` — so the embedded kernel does the full
    #    beamline activation.  Fallback: launch a bare ipykernel directly.
    starter = config.get("embedded_starter_script")
    try:
        if starter and os.path.exists(starter):
            hosted_in = "screen (starter)"
            subprocess.Popen(
                ["bash", starter,
                 config.get("dm_experiment") or "",
                 config.get("setup_file") or "exp_setup.yml",
                 cf, name],
                cwd=cwd or None, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif use_screen and screen_available():
            hosted_in = "screen"
            _screen_start(name, [python, "-m", "ipykernel_launcher", "-f", cf], cwd)
        else:
            hosted_in = "detached"
            subprocess.Popen(
                [python, "-m", "ipykernel_launcher", "-f", cf],
                cwd=cwd or None, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as exc:  # noqa: BLE001
        return "error", {"error": str(exc)}

    if not _wait_for_connection_file(cf):
        return "error", {"error": "kernel did not write its connection file"}

    info = {
        "beamline": beamline,
        "session_name": name,
        "connection_file": cf,
        "log": p["log"],
        "host": socket.gethostname(),
        "hosted_in": hosted_in,
        "cwd": cwd or os.getcwd(),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pid": _kernel_pid(cf),   # socket-free termination fallback
    }
    _write_info(beamline, info)
    return "started", info


def interrupt(beamline: str, hard: bool = False) -> bool:
    """Interrupt the running plan by sending SIGINT(s) to the kernel process.

    Mirrors pressing Ctrl+C in the console: **one** SIGINT triggers Bluesky's
    *deferred* pause (stop at the next checkpoint); a **second** SIGINT shortly
    after escalates to an *immediate* pause.  Works for a client-only connection
    because we send the signal to the kernel PID directly (from the sidecar, or
    discovered from the connection file).
    """
    info = read_info(beamline) or {}
    pid = info.get("pid") or _kernel_pid(connection_file(beamline))
    if not pid:
        return False
    try:
        os.kill(int(pid), signal.SIGINT)
        if hard:
            time.sleep(0.2)                       # both within the RE's Ctrl+C window
            os.kill(int(pid), signal.SIGINT)
        return True
    except (OSError, ValueError):
        return False


def shutdown_kernel(cf: str) -> bool:
    """Ask the kernel at connection file `cf` to shut down (client request).

    Starts ONLY the shell + control channels — **not** the heartbeat channel,
    whose background thread would otherwise raise (e.g. "Too many open files")
    on its own, escaping any try/except here.  Those two channels are blocking
    (no thread), so any error is caught synchronously.
    """
    if not cf or not os.path.exists(cf):
        return False
    try:
        from jupyter_client import BlockingKernelClient

        kc = BlockingKernelClient()
        kc.load_connection_file(cf)
        kc.start_channels(shell=True, iopub=False, stdin=False, hb=False,
                          control=True)
        try:
            kc.shutdown()
            return True
        finally:
            kc.stop_channels()
    except Exception:  # noqa: BLE001
        return False


def stop(beamline: str) -> bool:
    """Terminate the beamline's kernel: graceful request, quit screen, then kill.

    Ordered so it works regardless of file-descriptor state or hosting mode:
    a socket-light shutdown request (no heartbeat thread), then ``screen -X
    quit`` (screen mode), then a PID kill fallback (no-screen mode / GUI
    restart), then file cleanup.
    """
    p = paths(beamline)
    cf = p["connection_file"]
    ended = shutdown_kernel(cf)          # graceful; no heartbeat thread
    if screen_available():
        _screen_quit(session_name(beamline))
    # Fallback: if something is still answering, kill the recorded PID.
    info = read_info(beamline) or {}
    pid = info.get("pid")
    if pid and is_alive(cf):
        try:
            os.kill(int(pid), signal.SIGTERM)
            ended = True
        except OSError:
            pass
    for f in (cf, p["sidecar"]):
        try:
            os.remove(f)
        except OSError:
            pass
    return ended


def status(beamline: str) -> dict:
    """Return a status dict for the beamline's kernel."""
    p = paths(beamline)
    name = session_name(beamline)
    return {
        "beamline": beamline,
        "session_name": name,
        "connection_file": p["connection_file"],
        "log": p["log"],
        "alive": is_alive(p["connection_file"]),
        "screen_present": _screen_running(name) if screen_available() else False,
        "info": read_info(beamline),
    }


# ── CLI (a qserver.sh-style handle for staff) ─────────────────────────────────────

def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="bluesky-gui-kernel",
        description="Manage the single interactive Bluesky kernel for a beamline.",
    )
    ap.add_argument("command", choices=["status", "stop", "launch"])
    ap.add_argument("--beamline", default=config.get("beamline"))
    ap.add_argument("--cwd", default=None)
    args = ap.parse_args(argv[1:])

    if args.command == "status":
        s = status(args.beamline)
        print(json.dumps(s, indent=2, default=str))
        return 0 if s["alive"] else 1
    if args.command == "stop":
        print(f"Stopping kernel for beamline '{args.beamline}'…")
        stop(args.beamline)
        print("Done.")
        return 0
    if args.command == "launch":
        st, info = launch(args.beamline, args.cwd or None)   # None -> project root
        print(st)
        print(json.dumps(info, indent=2, default=str))
        return 0 if st in ("started", "already_running") else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
