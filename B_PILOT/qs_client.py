"""Thin wrapper around the Bluesky queueserver's own ``REManagerAPI``
(``bluesky_queueserver_api``) — the connection used by :mod:`queue_panel`
and :mod:`run_controls` to drive the *queued*-plan backend. Interactive
Run/the embedded console kernel never go through this module (see their
own ``console_panel.py``/``kernel_session.py``).

**All network I/O runs on one dedicated background thread** (:class:`_QSWorker`,
mirroring :class:`B_PILOT.viewer._CatalogWorker`'s "route every blocking
call through one always-running thread" pattern). This is not optional:
``REManagerAPI``'s ``status``/``queue_get``/``history_get``/``item_add``/etc.
all block the *calling* thread until the queue server responds or the
request times out (confirmed against the installed
``bluesky_queueserver_api``'s ``api_threads.py`` — even its own internal
status-caching thread makes every caller ``Event.wait()`` for it). Calling
any of them directly from a Qt ``QTimer`` callback on the GUI thread —
which an earlier version of this module did — freezes the entire GUI for
up to the request timeout on *every poll tick* whenever the queue server is
unreachable, which is indistinguishable from a hang (spinning cursor, no
input processed). See ``.context/DECISIONS.md`` for the incident this fixed.

Read functions (:func:`status`, :func:`queue_get`, :func:`history_get`)
return the worker's last-fetched snapshot instantly, from an in-memory
cache — safe to call every GUI poll tick. Action functions (:func:`item_add`,
:func:`queue_start`, etc.) hand the call to the worker thread and return
immediately (fire-and-forget); their effect shows up in the next cache
snapshot once the worker thread completes it, same eventual-consistency
model the queue panel's own 500ms poll already assumes for everything else.
"Fire-and-forget" only means the *caller* doesn't block — it does not mean
failures vanish: every action's outcome (including a server-side rejection,
e.g. a malformed item or an unreachable server) is recorded and available
via :func:`last_action_error`, which a poller should check every tick
alongside :func:`connected` (this fixes an earlier version that discarded
every action exception with a bare ``except Exception: pass``, so a
rejected `item_add` looked identical to a queued one — nothing to add, no
error, no clue).

Connection settings (``qs_zmq_control_addr``, ``qs_zmq_info_addr``,
``qs_user``, ``qs_user_group``) are profile keys (see :mod:`config`),
beamline facts like ``databroker_catalog`` — not workstation-specific paths.
Call :func:`reset` after a Configuration change so the worker rebuilds its
client against the (possibly new) settings.

**Console output** (:func:`console_text`): every ``REManagerAPI`` this module
builds already carries a live ``console_monitor`` (a
``ConsoleMonitor_ZMQ_Threads`` subscribing over ``qs_zmq_info_addr`` to RE
Manager's ``--zmq-publish-console`` socket) — this used to be built and left
disabled, so RunEngine console output for QS-dispatched plans (print
statements, scan progress, exceptions) never reached B-PILOT at all, even
though the embedded-kernel path's Console/Session-log tabs made it look like
queued plans should show up somewhere. ``_build_client`` now calls
``rm.console_monitor.enable()`` and ``_poll_once`` refreshes a cached copy of
its rolling text buffer (``rm.console_monitor.text()``/``text_uid``) every
tick, the same cache-read pattern as :func:`status`/:func:`queue_get`.
"""
from __future__ import annotations

import getpass
import queue as _queue
import threading

from PyQt5 import QtCore

from . import config

_POLL_INTERVAL_S = 1.0  # how often the worker re-fetches when idle


class _QSWorker(QtCore.QObject):
    """Single persistent background thread owning all queueserver network
    I/O (see module docstring for why this must never run on the GUI
    thread). ``updated`` is available for a future push-based UI, but
    :func:`status`/:func:`queue_get`/:func:`history_get` read the cache
    directly today — polling stays the simplest match for
    :class:`B_PILOT.queue_panel.QueuePanel`'s existing 500ms-QTimer design.
    """

    updated = QtCore.pyqtSignal(dict, dict, dict)  # (status, queue_data, history_data)

    def __init__(self) -> None:
        super().__init__()
        self._queue: _queue.Queue = _queue.Queue()
        self._client = None
        self._lock = threading.Lock()
        self._status: dict = {}
        self._queue_data: dict = {}
        self._history_data: dict = {}
        # Reachability + error state (see is_connected()/last_error_info()
        # below) -- all guarded by self._lock since they're written on this
        # worker thread and read from the GUI thread.
        self._connected: bool = False
        self._connect_error: str | None = None
        self._error_seq: int = 0
        self._last_error: tuple[int, str, str] | None = None
        # RunEngine console output for QS-dispatched plans (see module
        # docstring) -- cached the same way as status/queue_data/history_data.
        self._console_text: str = ""
        self._console_text_uid: str | None = None
        threading.Thread(
            target=self._run, name="qs_client worker", daemon=True
        ).start()

    # ── client lifecycle (background thread only) ────────────────────────────

    def _build_client(self):
        try:
            from bluesky_queueserver_api.zmq import REManagerAPI
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._connect_error = f"bluesky_queueserver_api is not importable: {exc}"
            return None
        try:
            rm = REManagerAPI(
                zmq_control_addr=config.get("qs_zmq_control_addr") or None,
                zmq_info_addr=config.get("qs_zmq_info_addr") or None,
            )
            rm.user = config.get("qs_user") or getpass.getuser()
            rm.user_group = config.get("qs_user_group") or "primary"
            try:
                rm.console_monitor.enable()
            except Exception:  # noqa: BLE001
                # Not fatal -- the connection itself still works, we just
                # won't get console text until a later poll retries this
                # (a fresh client is built next time _client is None, e.g.
                # after reset()).
                pass
            return rm
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._connect_error = f"could not create queue server client: {exc}"
            return None

    def _client_or_build(self):
        if self._client is None:
            self._client = self._build_client()
        return self._client

    # ── worker loop ───────────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            try:
                kind, args = self._queue.get(timeout=_POLL_INTERVAL_S)
            except _queue.Empty:
                kind, args = "poll", None
            if kind == "reset":
                rm, self._client = self._client, None
                if rm is not None:
                    try:
                        rm.close()
                    except Exception:  # noqa: BLE001
                        pass
                continue
            if kind == "console_clear":
                rm = self._client_or_build()
                if rm is not None:
                    try:
                        rm.console_monitor.clear()
                    except Exception:  # noqa: BLE001
                        pass
                with self._lock:
                    self._console_text = ""
                    self._console_text_uid = None
                continue
            if kind == "call":
                name, call_args, call_kwargs = args
                rm = self._client_or_build()
                if rm is None:
                    self._record_error(name, self._connect_error or "not connected to queue server")
                else:
                    try:
                        getattr(rm, name)(*call_args, **call_kwargs)
                    except Exception as exc:  # noqa: BLE001
                        self._record_error(name, str(exc))
                    else:
                        self._clear_error()
            # Refresh the cached snapshot after any action, or on the idle
            # poll tick -- always on THIS thread, never the caller's.
            self._poll_once()

    def _record_error(self, name: str, message: str) -> None:
        """Remember the most recent failed action call (`item_add`,
        `queue_start`, etc.) so a caller can surface it -- this is the fix
        for the bug where every action's exception used to be silently
        discarded (`except Exception: pass`), making a rejected/undeliverable
        item look identical to a successful one."""
        with self._lock:
            self._error_seq += 1
            self._last_error = (self._error_seq, name, message)

    def _clear_error(self) -> None:
        with self._lock:
            self._last_error = None

    def _poll_once(self) -> None:
        rm = self._client_or_build()
        if rm is None:
            self._set_cache({}, {}, {})
            with self._lock:
                self._connected = False
            return
        try:
            status = rm.status() or {}
            reachable = True
        except Exception as exc:  # noqa: BLE001
            status = {}
            reachable = False
            with self._lock:
                self._connect_error = str(exc)
        try:
            qdata = rm.queue_get() or {}
        except Exception:  # noqa: BLE001
            qdata = {}
        try:
            hdata = rm.history_get() or {}
        except Exception:  # noqa: BLE001
            hdata = {}
        try:
            new_uid = rm.console_monitor.text_uid
            if new_uid != self._console_text_uid:
                text = rm.console_monitor.text()
                with self._lock:
                    self._console_text = text
                    self._console_text_uid = new_uid
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._connected = reachable
            if reachable:
                self._connect_error = None
        self._set_cache(status, qdata, hdata)

    def _set_cache(self, status: dict, qdata: dict, hdata: dict) -> None:
        with self._lock:
            self._status, self._queue_data, self._history_data = status, qdata, hdata
        self.updated.emit(status, qdata, hdata)

    # ── thread-safe accessors (any thread) ───────────────────────────────────

    def snapshot(self) -> tuple[dict, dict, dict]:
        with self._lock:
            return dict(self._status), dict(self._queue_data), dict(self._history_data)

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def last_connect_error(self) -> str | None:
        with self._lock:
            return self._connect_error

    def last_error_info(self) -> tuple[int, str, str] | None:
        with self._lock:
            return self._last_error

    def console_text(self) -> tuple[str, str | None]:
        with self._lock:
            return self._console_text, self._console_text_uid

    def submit(self, name: str, *args, **kwargs) -> None:
        self._queue.put(("call", (name, args, kwargs)))

    def reset(self) -> None:
        self._queue.put(("reset", None))

    def clear_console(self) -> None:
        self._queue.put(("console_clear", None))


_worker: _QSWorker | None = None


def _get_worker() -> _QSWorker:
    global _worker
    if _worker is None:
        _worker = _QSWorker()
    return _worker


def reset() -> None:
    """Drop the worker's cached client so it rebuilds against (possibly
    new) connection settings on its next call -- e.g. after a Configuration
    change. Non-blocking (just queues the request onto the worker thread)."""
    _get_worker().reset()


def status() -> dict:
    """Last-fetched RE Manager status, or ``{}`` before the first fetch
    completes or if the server is unreachable. Instant -- reads an
    in-memory cache, never touches the network on the calling thread."""
    return _get_worker().snapshot()[0]


def queue_get() -> dict:
    """Last-fetched ``{"items": [...], "running_item": {...}}``, or ``{}``."""
    return _get_worker().snapshot()[1]


def history_get() -> dict:
    """Last-fetched ``{"items": [...]}`` history, or ``{}``."""
    return _get_worker().snapshot()[2]


def connected() -> bool:
    """Was the worker's most recent poll able to reach the queue server?
    Instant -- reads the worker's cached reachability flag, never touches
    the network on the calling thread. False both before the first poll
    completes and whenever the server is unreachable -- callers that need
    to tell those apart don't currently need to (see queue_panel.py)."""
    return _get_worker().is_connected()


def last_connect_error() -> str | None:
    """Human-readable reason the most recent connection attempt failed --
    an import error (``bluesky_queueserver_api`` missing from the env), a
    client-construction error (malformed ``qs_zmq_*`` address), or the
    exception ``rm.status()`` raised (e.g. a timeout because the queue
    server isn't running, or a DNS/network failure reaching its host).
    ``None`` once/while connected. This is the connection-level counterpart
    to :func:`last_action_error` -- without it, ``connected() == False`` was
    surfaced as a bare "not connected" with no way to tell those causes
    apart, the same silent-failure problem :func:`last_action_error` fixed
    for action calls."""
    return _get_worker().last_connect_error()


def last_action_error() -> tuple[int, str, str] | None:
    """``(seq, call_name, message)`` of the most recent failed action call
    (`item_add`, `queue_start`, `re_pause`, etc.), or ``None`` if the most
    recent action (if any) succeeded. `seq` increments on every new
    failure -- a caller should remember the last `seq` it displayed and
    only react when this returns a different one, rather than re-showing
    the same failure on every poll tick."""
    return _get_worker().last_error_info()


def history_clear() -> None:
    _get_worker().submit("history_clear")


def console_text() -> tuple[str, str | None]:
    """``(text, text_uid)`` snapshot of RE Manager's RunEngine console
    output (print statements, scan progress, exceptions) for QS-dispatched
    plans -- instant, reads an in-memory cache refreshed every poll tick,
    same as :func:`status`. ``text_uid`` changes whenever new output has
    arrived; a poller can skip re-rendering when it hasn't. ``("", None)``
    before the first poll completes or if the server/console monitor is
    unreachable."""
    return _get_worker().console_text()


def console_clear() -> None:
    """Clear the cached console text (and the underlying console monitor's
    own buffer) -- call when B-PILOT opens a fresh QS environment, so old
    output from a previous environment doesn't linger."""
    _get_worker().clear_console()


def item_add(
    item: dict,
    *,
    pos=None,
    before_uid: str | None = None,
    after_uid: str | None = None,
) -> None:
    kwargs = {}
    if before_uid is not None:
        kwargs["before_uid"] = before_uid
    elif after_uid is not None:
        kwargs["after_uid"] = after_uid
    elif pos is not None:
        kwargs["pos"] = pos
    _get_worker().submit("item_add", item, **kwargs)


def item_remove(uid: str) -> None:
    _get_worker().submit("item_remove", uid=uid)


def item_move(uid: str, *, before_uid: str | None = None, after_uid: str | None = None) -> None:
    kwargs = {"uid": uid}
    if before_uid is not None:
        kwargs["before_uid"] = before_uid
    if after_uid is not None:
        kwargs["after_uid"] = after_uid
    _get_worker().submit("item_move", **kwargs)


def queue_start() -> None:
    _get_worker().submit("queue_start")


def queue_stop() -> None:
    _get_worker().submit("queue_stop")


def environment_open() -> None:
    _get_worker().submit("environment_open")


def environment_close() -> None:
    _get_worker().submit("environment_close")


def re_pause(option: str | None = None) -> None:
    kwargs = {"option": option} if option else {}
    _get_worker().submit("re_pause", **kwargs)


def re_resume() -> None:
    _get_worker().submit("re_resume")


def re_stop() -> None:
    _get_worker().submit("re_stop")


def re_abort() -> None:
    _get_worker().submit("re_abort")


def re_halt() -> None:
    _get_worker().submit("re_halt")
