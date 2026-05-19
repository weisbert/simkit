"""Long-lived ``BridgeWorker`` (spec §8, mandates A1 + A5).

The single ``QObject`` that owns every ``skillbridge`` call. Lives on a
dedicated ``QThread`` (NOT the UI thread); all SKILL ops are marshalled
via a ``queue.Queue`` so the single-stream socket never sees concurrent
calls (which corrupts response framing — same wedge as the manual
recovery dance).

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
  ``BridgeWorker`` on it, start. Returns ``(thread, worker)``.

PyQt5 is imported at module load (``QObject`` + signals are class-body
declarations). Mock the import at test time via plain
``unittest.mock.MagicMock``-style patches on the names this module
imports. The module's top-level import block is guarded so tests that
don't need Qt can still import ``simkit.gui`` siblings without dragging
PyQt5 in.
"""

from __future__ import annotations

import logging
import queue
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
        QObject,
        QThread,
        pyqtSignal,
        pyqtSlot,
        QTimer,
    )

    _QT_AVAILABLE = True
except ImportError:  # pragma: no cover — env-specific
    _QT_AVAILABLE = False

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

    # Qt signals (declared at class body — fixed signatures).
    op_complete = pyqtSignal(int, object)
    op_failed = pyqtSignal(int, object)
    busy_changed = pyqtSignal(bool)
    status_changed = pyqtSignal(object)  # BridgeStatus

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
        self._queue: "queue.Queue[Optional[BridgeOp]]" = queue.Queue()
        self._next_id = 1
        self._is_busy = False
        self._status = BridgeStatus.AMBER
        self._consec_fails = 0
        self._last_heartbeat_ok_at: Optional[float] = None
        self._stopped = False
        self._heartbeat_timer: Optional[QTimer] = None

    # --- public API (thread-safe) ----------------------------------------

    def queue_op(self, func_name: str, **kwargs: Any) -> int:
        """Enqueue an op. Thread-safe. Returns the request_id.

        The worker thread will pick this up via its blocking ``Queue.get``
        and dispatch it serially. Result delivered via ``op_complete`` /
        ``op_failed`` signal carrying the same request_id.
        """
        request_id = self._next_id
        self._next_id += 1
        op = BridgeOp(request_id=request_id, func_name=func_name, kwargs=kwargs)
        self._queue.put(op)
        return request_id

    def stop(self) -> None:
        """Signal the worker thread to exit cleanly.

        Posts a sentinel to the op queue; the run loop drains it and
        returns. Safe to call from any thread.
        """
        self._stopped = True
        self._queue.put(None)

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @property
    def status(self) -> BridgeStatus:
        return self._status

    # --- worker thread loop ----------------------------------------------

    @pyqtSlot()
    def run(self) -> None:
        """Main worker loop. Invoked once when the QThread starts.

        Pull ops one at a time, execute, emit result. Heartbeat is driven
        independently by ``_heartbeat_tick`` via a QTimer (set up by
        :meth:`start_heartbeat`); we do NOT inline the heartbeat into this
        loop because that would couple op-latency to heartbeat cadence.
        """
        while not self._stopped:
            op = self._queue.get()
            if op is None:
                break
            self._dispatch(op)

    def _dispatch(self, op: BridgeOp) -> None:
        """Execute one op + emit the result signal.

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

    # --- heartbeat (spec §8.2) -------------------------------------------

    def start_heartbeat(self) -> None:
        """Begin firing :func:`heartbeat_tick` every ``HEARTBEAT_INTERVAL_SEC``.

        Called once by the owning thread setup code (typically after
        ``moveToThread`` + ``QThread.started`` fires). Requires PyQt5 to
        actually run; tests drive the heartbeat manually via
        :meth:`heartbeat_tick`.
        """
        if not _QT_AVAILABLE:  # pragma: no cover — env-specific
            return
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(int(HEARTBEAT_INTERVAL_SEC * 1000))
        self._heartbeat_timer.timeout.connect(self.heartbeat_tick)
        self._heartbeat_timer.start()

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
    thread.started.connect(worker.run)
    thread.started.connect(worker.start_heartbeat)
    return thread, worker
