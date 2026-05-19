"""Long-lived ``BridgeWorker`` (spec §8, mandates A1 + A5).

The single ``QObject`` that owns every ``skillbridge`` call. Lives on a
dedicated ``QThread`` (NOT the UI thread); ops are marshalled via a
Qt-internal ``pyqtSignal`` with ``Qt.QueuedConnection`` so the single-
stream socket never sees concurrent calls (which corrupts response
framing — same wedge as the manual recovery dance).

Architecture (post-2026-05-19 refactor — see DECISIONS #76):

  Main thread                 Worker thread (QThread.exec_ event loop)
  -----------                 ----------------------------------------
  queue_op(...)
      ↓ emit _op_queued        receive (queued)
                                   ↓ _dispatch(op)
                                   ↓ bridge.<func>(**kwargs)
  receive (queued)            emit op_complete / op_failed
       ↑

  stop()                       heartbeat QTimer (started in initialize)
      ↓ emit _stop_requested        ↓ every 10s
                                receive: _cleanup            heartbeat_tick
                                   ↓ timer.stop()                ↓
                                                              evalstring("t")
                                                              status_changed
                                                                    ↑
  set_bridge_status            receive (queued)

The two-signal pattern (``_op_queued`` / ``_stop_requested``) replaces
the previous blocking ``queue.Queue`` + ``thread.started.connect(run)``
pattern. The old design was broken twice: (a) ``run()`` blocked the
worker's event loop forever, so the heartbeat timer never actually fired;
(b) on shutdown the timer was destroyed on the main thread (Python GC)
while its affinity was the worker thread → ``QObject::killTimer: Timers
cannot be stopped from another thread`` warning. Both bugs share the
same root cause and are fixed by letting ``QThread.exec_()`` run the
event loop normally.

Public surface (the only one the rest of the GUI should touch):

* :class:`BridgeOp` — a single request enqueued by callers; carries the
  callable name + kwargs.
* :class:`BridgeStatus` — heartbeat colour enum: ``GREEN`` / ``AMBER`` /
  ``RED``.
* :class:`BridgeWorker` — the worker QObject. Signals:

  - ``op_complete(int, object)`` — request_id, result
  - ``op_failed(int, object)``   — request_id, BridgeError
  - ``busy_changed(bool)``       — toggles UI-button enabled state
  - ``status_changed(BridgeStatus)`` — heartbeat dot colour

* :func:`build_bridge` — convenience: spawn a ``QThread``, instantiate a
  ``BridgeWorker`` on it, wire ``started → initialize``. Returns
  ``(thread, worker)``.

PyQt5 is imported at module load (``QObject`` + signals are class-body
declarations). Mock the import at test time via plain
``unittest.mock.MagicMock``-style patches on the names this module
imports. The module's top-level import block is guarded so tests that
don't need Qt can still import ``simkit.gui`` siblings without dragging
PyQt5 in.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

# Heartbeat cadence + thresholds from spec §8.2.
HEARTBEAT_INTERVAL_SEC = 10.0
"""Seconds between idle-heartbeat probes."""

AMBER_AFTER_SEC = 15.0
"""If the last heartbeat is older than this, status drops to AMBER."""

RED_AFTER_FAILS = 3
"""Consecutive heartbeat failures before status drops to RED."""


log = logging.getLogger(__name__)


# --- Qt import (lazy) ----------------------------------------------------
# The QObject base class is needed at class-definition time, so we import
# it here. If PyQt5 is missing we fall back to a stub so the module can
# still be imported by other simkit code that doesn't actually instantiate
# the worker — tests prefer this over a hard ImportError at module load.

try:
    from PyQt5.QtCore import (  # type: ignore[import-not-found]
        Qt,
        QObject,
        QThread,
        pyqtSignal,
        pyqtSlot,
        QTimer,
    )

    _QT_AVAILABLE = True
except ImportError:  # pragma: no cover — env-specific
    _QT_AVAILABLE = False

    class Qt:  # type: ignore[no-redef]
        """Minimal Qt namespace stub — only the connection-type constants
        we reference at module load (referenced in slot decorators / connect
        calls). Real values come from PyQt5 when present."""

        QueuedConnection = 2  # matches Qt::QueuedConnection enum value
        AutoConnection = 0

    class _StubSignal:  # pylint: disable=too-few-public-methods
        """Fallback so class-body declarations don't blow up without PyQt5."""

        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            raise RuntimeError(
                "PyQt5 not installed; BridgeWorker is not runnable. "
                "Install via `pip install PyQt5==5.15.9` "
                "or the 'gui' extras."
            )

        def connect(self, *args, **kwargs):
            pass

    class QObject:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "PyQt5 not installed; BridgeWorker cannot be instantiated. "
                "Install via `pip install PyQt5==5.15.9` "
                "or the 'gui' extras."
            )

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("PyQt5 not installed; QThread unavailable.")

    class QTimer:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("PyQt5 not installed; QTimer unavailable.")

    def pyqtSignal(*args, **kwargs):  # type: ignore[no-redef]
        return _StubSignal(*args, **kwargs)

    def pyqtSlot(*args, **kwargs):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco


class BridgeStatus(str, Enum):
    """Heartbeat status rendered as a dot in the top bar."""

    GREEN = "green"   # last heartbeat OK within AMBER_AFTER_SEC
    AMBER = "amber"   # heartbeat aged / retrying
    RED = "red"       # >= RED_AFTER_FAILS consecutive failures


@dataclass
class BridgeError:
    """Failure payload for ``op_failed`` signal."""

    category: str
    message: str
    source: Optional[str] = None

    @classmethod
    def from_exception(cls, exc: BaseException) -> "BridgeError":
        category = getattr(exc, "category", None) or exc.__class__.__name__
        message = getattr(exc, "message", None) or str(exc)
        source = getattr(exc, "source", None)
        return cls(category=str(category), message=str(message), source=source)


@dataclass
class BridgeOp:
    """A single enqueued operation.

    ``func`` is a callable resolved against ``simkit.skill_bridge`` (or
    any namespace the caller provides via the worker constructor). At
    construction time we don't import skill_bridge — that's a worker-side
    concern, so tests can mock it.

    ``kwargs`` are passed through verbatim. Positional args are not
    supported by design — every existing ``skill_bridge`` entry point uses
    keyword-only args.
    """

    request_id: int
    func_name: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class BridgeWorker(QObject):
    """Owns every skillbridge call. One per app.

    Spec §8.1 contract:
      * Lives on a dedicated ``QThread``; never on the UI thread.
      * ``queue_op`` is callable from any thread; returns a request_id.
      * Ops execute strictly in FIFO order.
      * ``is_busy`` tracks "an op is currently mid-call"; ``busy_changed``
        toggles for the UI to disable buttons globally.

    Spec §8.2 heartbeat:
      * When idle, fires :func:`heartbeat_probe` every
        ``HEARTBEAT_INTERVAL_SEC`` seconds.
      * Status transitions:
        - last success within ``AMBER_AFTER_SEC``: GREEN
        - older / retrying:                       AMBER
        - ``RED_AFTER_FAILS`` consecutive fails:  RED

    Tests inject a fake ``bridge_module`` (any namespace with the
    ``pvt_*`` callables + an ``evalstring`` for the heartbeat) to avoid
    touching skillbridge.
    """

    # --- Qt signals (declared at class body — fixed signatures) ---------
    #
    # Public signals (consumed by the rest of the GUI):
    op_complete = pyqtSignal(int, object)
    op_failed = pyqtSignal(int, object)
    busy_changed = pyqtSignal(bool)
    status_changed = pyqtSignal(object)  # BridgeStatus

    # Internal signals (cross-thread queuing — both connected to slots on
    # this same QObject in __init__; after ``moveToThread`` is called,
    # AutoConnection resolves to QueuedConnection at emit time whenever
    # the emit thread differs from the worker thread).
    _op_queued = pyqtSignal(object)  # BridgeOp -> _dispatch
    _stop_requested = pyqtSignal()   # () -> _cleanup
    _restart_requested = pyqtSignal()  # () -> _restart_local (spec A5)

    def __init__(
        self,
        bridge_module: Any = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._bridge_module = bridge_module  # may be None until started
        self._clock = clock
        self._next_id = 1
        self._is_busy = False
        self._status = BridgeStatus.AMBER
        self._consec_fails = 0
        self._last_heartbeat_ok_at: Optional[float] = None
        self._heartbeat_timer: Optional[QTimer] = None
        # Wire internal cross-thread dispatch signals. Connections are
        # made in __init__ (before any moveToThread call) but Qt re-
        # resolves the connection type at each emit based on current
        # thread affinity, so this stays correct after we're moved onto
        # a worker thread.
        self._op_queued.connect(self._dispatch)
        self._stop_requested.connect(self._cleanup)
        self._restart_requested.connect(self._restart_local)

    # --- public API (thread-safe) ----------------------------------------

    def queue_op(self, func_name: str, **kwargs: Any) -> int:
        """Enqueue an op. Thread-safe. Returns the request_id.

        Emits ``_op_queued`` which is connected to ``_dispatch`` —
        AutoConnection across threads resolves to QueuedConnection,
        posting the BridgeOp to the worker thread's event loop. Ops
        dispatch strictly in FIFO order (single-slot connection;
        events serialize through the event queue).
        """
        request_id = self._next_id
        self._next_id += 1
        op = BridgeOp(request_id=request_id, func_name=func_name, kwargs=kwargs)
        self._op_queued.emit(op)
        return request_id

    def stop(self) -> None:
        """Request worker-thread-local cleanup. Thread-safe.

        Emits ``_stop_requested`` which is connected to ``_cleanup``
        (queued onto the worker thread). The caller should then call
        ``thread.quit()`` + ``thread.wait()`` to actually end the worker
        thread — both events queue on the same event loop in FIFO order
        so cleanup runs before quit.
        """
        self._stop_requested.emit()

    def restart(self) -> None:
        """Request an immediate bridge re-probe (spec A5). Thread-safe.

        Clears the failure counter and fires a heartbeat NOW instead of
        waiting up to ``HEARTBEAT_INTERVAL_SEC`` for the next tick. Used
        by the MainWindow "Restart bridge" button when the user has
        re-launched the Cadence pyServer (e.g. ``(pyKillServer)
        (pyStartServer ?python "/usr/bin/python3")`` in CIW).

        Does NOT re-import :mod:`simkit.skill_bridge` — each bridge call
        opens its own :class:`skillbridge.Workspace`, so there is no
        cached socket to invalidate; only the worker's status state
        needs to be primed.
        """
        self._restart_requested.emit()

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @property
    def status(self) -> BridgeStatus:
        return self._status

    # --- worker-thread slots ---------------------------------------------

    @pyqtSlot(object)
    def _dispatch(self, op: BridgeOp) -> None:
        """Execute one op + emit the result signal.

        Runs on the worker thread (queued from main via ``_op_queued``).
        Wrapped in try/except so any exception (skillbridge transport
        errors, SkillBridgeError, plain RuntimeError) becomes an
        ``op_failed`` signal rather than crashing the thread.
        """
        self._set_busy(True)
        try:
            fn = self._resolve(op.func_name)
            result = fn(**op.kwargs)
        except BaseException as exc:  # noqa: BLE001 - intentional broad catch
            log.warning("BridgeOp failed: %s(%r): %s", op.func_name, op.kwargs, exc)
            self.op_failed.emit(op.request_id, BridgeError.from_exception(exc))
        else:
            self.op_complete.emit(op.request_id, result)
        finally:
            self._set_busy(False)

    def _resolve(self, func_name: str) -> Callable[..., Any]:
        """Look up ``func_name`` on the bridge module.

        Late-binds so the module reference can be swapped at test time.
        If no bridge module is set, imports ``simkit.skill_bridge`` lazily
        (so unit tests that mock the worker never trigger the real import).
        """
        mod = self._bridge_module
        if mod is None:
            from simkit import skill_bridge as mod  # noqa: WPS433
            self._bridge_module = mod
        try:
            return getattr(mod, func_name)
        except AttributeError as exc:
            raise RuntimeError(
                f"BridgeWorker: bridge module has no attribute {func_name!r}"
            ) from exc

    def _set_busy(self, busy: bool) -> None:
        if busy == self._is_busy:
            return
        self._is_busy = busy
        self.busy_changed.emit(busy)

    # --- worker-thread lifecycle (spec §8.1 + §8.2) ----------------------

    @pyqtSlot()
    def initialize(self) -> None:
        """Worker-thread-local setup. Connect to ``QThread.started``.

        Runs on the worker thread (because ``started`` is emitted from
        within the worker thread, and AutoConnection on a same-thread
        receiver becomes a direct call). Creates the heartbeat ``QTimer``
        as a child of ``self`` so its thread affinity matches the worker
        thread — required so that ``timer.stop()`` later runs on the
        correct thread without raising the cross-thread ``killTimer``
        warning.

        Tests drive ``heartbeat_tick`` directly and don't go through this
        method; PyQt5 is required to actually run it.
        """
        if not _QT_AVAILABLE:  # pragma: no cover — env-specific
            return
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(int(HEARTBEAT_INTERVAL_SEC * 1000))
        self._heartbeat_timer.timeout.connect(self.heartbeat_tick)
        self._heartbeat_timer.start()

    @pyqtSlot()
    def _cleanup(self) -> None:
        """Worker-thread-local teardown. Connected to ``_stop_requested``.

        Runs on the worker thread; stops the heartbeat ``QTimer`` while
        we still own its dispatcher, so a later destruction on the main
        thread (Python GC of the worker after the QThread has joined)
        no longer trips ``QObject::killTimer: Timers cannot be stopped
        from another thread``. The QTimer object itself is still
        destroyed later — but with no live timer ID registered, that
        destruction is harmless from any thread.
        """
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.stop()
            self._heartbeat_timer = None

    @pyqtSlot()
    def _restart_local(self) -> None:
        """Worker-thread-local restart: reset state + immediate probe.

        Connected to ``_restart_requested``. Mirrors the AMBER pre-probe
        state and then runs one heartbeat synchronously so the dot flips
        as soon as the bridge is healthy again.
        """
        self._consec_fails = 0
        self._last_heartbeat_ok_at = None
        if self._status != BridgeStatus.AMBER:
            self._status = BridgeStatus.AMBER
            self.status_changed.emit(BridgeStatus.AMBER)
        self.heartbeat_tick()

    @pyqtSlot()
    def heartbeat_tick(self) -> None:
        """One heartbeat probe. Skips if mid-op (don't tangle with the queue)."""
        if self._is_busy:
            # While an op is in flight the bridge socket is being used;
            # avoid concurrent calls (spec §8.1 A1). Just refresh status
            # based on time-since-last-success.
            self._recompute_status()
            return
        try:
            fn = self._resolve("evalstring")
            fn("t")
        except BaseException as exc:  # noqa: BLE001
            self._consec_fails += 1
            log.debug("heartbeat fail %d: %s", self._consec_fails, exc)
        else:
            self._consec_fails = 0
            self._last_heartbeat_ok_at = self._clock()
        self._recompute_status()

    def _recompute_status(self) -> None:
        """Promote / demote ``self._status`` per spec §8.2."""
        new_status: BridgeStatus
        if self._consec_fails >= RED_AFTER_FAILS:
            new_status = BridgeStatus.RED
        elif self._last_heartbeat_ok_at is None:
            # Before the first successful probe.
            new_status = BridgeStatus.AMBER
        else:
            age = self._clock() - self._last_heartbeat_ok_at
            if age <= AMBER_AFTER_SEC:
                new_status = BridgeStatus.GREEN
            else:
                new_status = BridgeStatus.AMBER

        if new_status != self._status:
            self._status = new_status
            self.status_changed.emit(new_status)


def build_bridge(
    bridge_module: Any = None,
) -> tuple[QThread, BridgeWorker]:
    """Spawn a QThread, build a :class:`BridgeWorker`, move it onto the thread.

    Caller is responsible for keeping the returned references alive
    (Qt will GC the thread otherwise) and for calling
    ``worker.stop()`` + ``thread.quit()`` + ``thread.wait()`` on shutdown.

    Requires PyQt5 — raises ``RuntimeError`` otherwise.
    """
    if not _QT_AVAILABLE:
        raise RuntimeError(
            "PyQt5 not installed; cannot build the bridge worker. "
            "Install via `pip install PyQt5==5.15.9` "
            "or the 'gui' extras."
        )
    thread = QThread()
    worker = BridgeWorker(bridge_module=bridge_module)
    worker.moveToThread(thread)
    # No ``worker.run`` connection — QThread's default ``run()`` calls
    # ``exec_()`` which spins the event loop on the worker thread, and
    # that's exactly what we need (heartbeat timer fires, queued ops
    # dispatch, queued stop_requested cleanup all happen on the right
    # thread). Just wire ``initialize`` so the heartbeat QTimer is
    # constructed inside the worker thread.
    thread.started.connect(worker.initialize)
    return thread, worker
