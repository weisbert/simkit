"""Unit tests for :mod:`simkit.gui.bridge_worker` with a MOCKED bridge.

The skillbridge module is never imported here — every test uses a
``MagicMock`` stand-in passed as ``bridge_module``.

Two layers of testing:
  * Pure data classes (``BridgeError``, ``BridgeOp``, ``BridgeStatus``) —
    PyQt5-independent, always run.
  * Worker behaviour (queue dispatch, busy toggling, heartbeat state
    machine) — these touch a ``BridgeWorker`` instance. The class itself
    has a stub ``QObject`` when PyQt5 is missing, so we bypass the
    constructor via ``object.__new__`` + manual field init, then replace
    each signal attribute with a ``MagicMock`` so ``.emit()`` is
    side-effect-free.

This avoids the ``sys.modules[...] = None`` pattern flagged in user
memory (which leaks real SKILL calls into tests) by mocking at the
``bridge_module`` parameter boundary.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.gui.bridge_worker import (  # noqa: E402
    AMBER_AFTER_SEC,
    BridgeError,
    BridgeOp,
    BridgeStatus,
    BridgeWorker,
    RED_AFTER_FAILS,
)


def _make_worker(bridge_module: Any, *, clock=None) -> BridgeWorker:
    """Build a ``BridgeWorker`` without invoking the (stub) QObject init.

    Bypasses ``__init__`` so the test runs whether or not PyQt5 is
    installed. Replaces signal attributes (public + the internal
    ``_op_queued`` / ``_stop_requested``) with ``MagicMock`` so any
    ``.emit(...)`` call inside the worker's methods is recordable and
    side-effect-free.

    Post-2026-05-19 refactor: there's no ``_queue`` / ``_stopped`` field
    anymore — ops travel via the ``_op_queued`` signal (queued
    connection to ``_dispatch`` on the worker thread), and shutdown
    travels via the ``_stop_requested`` signal (queued connection to
    ``_cleanup``).
    """
    # ``object.__new__(BridgeWorker)`` fails when PyQt5 is present because
    # ``QObject`` is a C-extension class with a non-trivial ``__new__``.
    # ``BridgeWorker.__new__(BridgeWorker)`` defers to PyQt5's metaclass
    # which handles the C-side allocation properly. Same call works under
    # the PyQt5-missing stub (plain Python class), so this is the
    # portable form.
    worker = BridgeWorker.__new__(BridgeWorker)
    worker._bridge_module = bridge_module
    worker._clock = clock if clock is not None else (lambda: 0.0)
    worker._next_id = 1
    worker._is_busy = False
    worker._status = BridgeStatus.AMBER
    worker._consec_fails = 0
    worker._last_heartbeat_ok_at = None
    worker._heartbeat_timer = None
    worker.op_complete = mock.MagicMock(name="op_complete")
    worker.op_failed = mock.MagicMock(name="op_failed")
    worker.busy_changed = mock.MagicMock(name="busy_changed")
    worker.status_changed = mock.MagicMock(name="status_changed")
    worker._op_queued = mock.MagicMock(name="_op_queued")
    worker._stop_requested = mock.MagicMock(name="_stop_requested")
    return worker


class BridgeErrorTests(unittest.TestCase):

    def test_from_exception_plain(self):
        err = BridgeError.from_exception(RuntimeError("boom"))
        self.assertEqual(err.category, "RuntimeError")
        self.assertEqual(err.message, "boom")
        self.assertIsNone(err.source)

    def test_from_exception_skill_bridge_error_shape(self):
        # SkillBridgeError-like object: category + message + source attrs.
        class FakeSBError(RuntimeError):
            def __init__(self):
                super().__init__("pvt_validation: bad arg (in fnxSession0)")
                self.category = "pvt_validation"
                self.message = "bad arg"
                self.source = "fnxSession0"

        err = BridgeError.from_exception(FakeSBError())
        self.assertEqual(err.category, "pvt_validation")
        self.assertEqual(err.message, "bad arg")
        self.assertEqual(err.source, "fnxSession0")


class BridgeOpTests(unittest.TestCase):

    def test_construction_defaults(self):
        op = BridgeOp(request_id=42, func_name="evalstring")
        self.assertEqual(op.request_id, 42)
        self.assertEqual(op.func_name, "evalstring")
        self.assertEqual(op.kwargs, {})


class QueueOpTests(unittest.TestCase):
    """``queue_op`` is thread-safe + returns monotonic request IDs.

    Post-refactor: ops are delivered via the ``_op_queued`` signal
    (queued connection to ``_dispatch``), not a ``queue.Queue``. The
    test verifies the signal was emitted with a ``BridgeOp`` carrying
    the expected request_id + func_name + kwargs in submission order.
    """

    def test_queue_op_assigns_monotonic_ids_and_emits_in_order(self):
        worker = _make_worker(bridge_module=mock.MagicMock())
        a = worker.queue_op("evalstring", expr="t")
        b = worker.queue_op("pvt_corners_pull", out_path="/x")
        c = worker.queue_op("evalstring", expr="t")
        self.assertEqual([a, b, c], [1, 2, 3])
        # Three emits on _op_queued, in submission order.
        self.assertEqual(worker._op_queued.emit.call_count, 3)
        emitted_ops = [call.args[0] for call in worker._op_queued.emit.call_args_list]
        self.assertEqual(
            [(op.request_id, op.func_name) for op in emitted_ops],
            [(1, "evalstring"), (2, "pvt_corners_pull"), (3, "evalstring")],
        )
        # And kwargs are carried verbatim.
        self.assertEqual(emitted_ops[0].kwargs, {"expr": "t"})
        self.assertEqual(emitted_ops[1].kwargs, {"out_path": "/x"})

    def test_stop_emits_stop_requested(self):
        """``stop()`` is just a signal emit — the actual cleanup happens
        on the worker thread when ``_cleanup`` runs as the queued slot."""
        worker = _make_worker(bridge_module=mock.MagicMock())
        worker.stop()
        worker._stop_requested.emit.assert_called_once_with()


class DispatchTests(unittest.TestCase):
    """One-shot ``_dispatch`` exercises busy toggling + result signalling."""

    def test_successful_op_emits_op_complete(self):
        # MOCKED bridge with the production-shape ``pvt_corners_pull``
        # signature. Lesson from v1.6 false-green incident: mock the
        # *same* call signature production uses, returning a string-shaped
        # result (skill_bridge.py pvt_corners_pull returns the echoed path).
        bridge = mock.MagicMock()
        bridge.pvt_corners_pull.return_value = "/abs/path/to/some.union.json"

        worker = _make_worker(bridge_module=bridge)
        op = BridgeOp(
            request_id=7,
            func_name="pvt_corners_pull",
            kwargs={
                "out_path": "/tmp/x.union.json",
                "pvtproject_path": Path("/tmp/proj.pvtproject"),
                "session": "fnxSession0",
            },
        )

        worker._dispatch(op)

        bridge.pvt_corners_pull.assert_called_once_with(
            out_path="/tmp/x.union.json",
            pvtproject_path=Path("/tmp/proj.pvtproject"),
            session="fnxSession0",
        )
        worker.op_complete.emit.assert_called_once_with(
            7, "/abs/path/to/some.union.json",
        )
        worker.op_failed.emit.assert_not_called()
        # busy toggled True then False.
        busy_calls = [c.args[0] for c in worker.busy_changed.emit.call_args_list]
        self.assertEqual(busy_calls, [True, False])
        self.assertFalse(worker.is_busy)

    def test_failed_op_emits_op_failed_with_bridge_error(self):
        bridge = mock.MagicMock()
        bridge.pvt_corners_pull.side_effect = RuntimeError("simulated SKILL error")

        worker = _make_worker(bridge_module=bridge)
        op = BridgeOp(request_id=9, func_name="pvt_corners_pull")
        worker._dispatch(op)

        worker.op_complete.emit.assert_not_called()
        worker.op_failed.emit.assert_called_once()
        emitted_id, emitted_err = worker.op_failed.emit.call_args.args
        self.assertEqual(emitted_id, 9)
        self.assertIsInstance(emitted_err, BridgeError)
        self.assertEqual(emitted_err.message, "simulated SKILL error")

    def test_unknown_func_name_becomes_op_failed(self):
        bridge = mock.MagicMock(spec=[])  # nothing exposed
        # spec=[] means getattr raises AttributeError for any name.
        worker = _make_worker(bridge_module=bridge)
        op = BridgeOp(request_id=11, func_name="does_not_exist")
        worker._dispatch(op)
        worker.op_complete.emit.assert_not_called()
        worker.op_failed.emit.assert_called_once()
        _, err = worker.op_failed.emit.call_args.args
        self.assertIsInstance(err, BridgeError)


class HeartbeatTests(unittest.TestCase):
    """Spec §8.2: GREEN ≤15s old; AMBER older/retrying; RED 3 consec fails."""

    def test_first_successful_heartbeat_goes_green(self):
        bridge = mock.MagicMock()
        bridge.evalstring.return_value = "t"
        clock = [100.0]
        worker = _make_worker(bridge_module=bridge, clock=lambda: clock[0])

        worker.heartbeat_tick()

        bridge.evalstring.assert_called_once_with("t")
        self.assertEqual(worker.status, BridgeStatus.GREEN)
        worker.status_changed.emit.assert_called_once_with(BridgeStatus.GREEN)

    def test_three_consec_fails_drops_to_red(self):
        bridge = mock.MagicMock()
        bridge.evalstring.side_effect = RuntimeError("socket gone")
        clock = [100.0]
        worker = _make_worker(bridge_module=bridge, clock=lambda: clock[0])

        # First fail: AMBER (never had a success).
        worker.heartbeat_tick()
        self.assertEqual(worker.status, BridgeStatus.AMBER)
        # Second fail: still AMBER.
        worker.heartbeat_tick()
        self.assertEqual(worker.status, BridgeStatus.AMBER)
        # Third fail: RED.
        worker.heartbeat_tick()
        self.assertEqual(worker.status, BridgeStatus.RED)
        self.assertEqual(worker._consec_fails, RED_AFTER_FAILS)

    def test_red_recovers_to_green_on_next_success(self):
        bridge = mock.MagicMock()
        clock = [100.0]
        worker = _make_worker(bridge_module=bridge, clock=lambda: clock[0])

        # Drive into RED.
        bridge.evalstring.side_effect = RuntimeError("nope")
        for _ in range(RED_AFTER_FAILS):
            worker.heartbeat_tick()
        self.assertEqual(worker.status, BridgeStatus.RED)

        # Now recover.
        bridge.evalstring.side_effect = None
        bridge.evalstring.return_value = "t"
        worker.heartbeat_tick()
        self.assertEqual(worker.status, BridgeStatus.GREEN)
        self.assertEqual(worker._consec_fails, 0)

    def test_aged_heartbeat_becomes_amber(self):
        bridge = mock.MagicMock()
        bridge.evalstring.return_value = "t"
        clock = [100.0]
        worker = _make_worker(bridge_module=bridge, clock=lambda: clock[0])

        worker.heartbeat_tick()
        self.assertEqual(worker.status, BridgeStatus.GREEN)

        # Now simulate stalling: future ticks fire but the response never
        # arrives because evalstring blocks. Easier model: advance clock
        # past AMBER_AFTER_SEC with no new successes and call
        # _recompute_status directly (the tick would normally either
        # succeed -> reset to now, or fail -> bump fail counter).
        clock[0] = 100.0 + AMBER_AFTER_SEC + 1.0
        worker._recompute_status()
        self.assertEqual(worker.status, BridgeStatus.AMBER)

    def test_heartbeat_skips_probe_while_busy(self):
        bridge = mock.MagicMock()
        bridge.evalstring.return_value = "t"
        worker = _make_worker(bridge_module=bridge)
        worker._is_busy = True
        worker.heartbeat_tick()
        # The probe must NOT have fired (single-stream socket rule).
        bridge.evalstring.assert_not_called()


class CleanupTests(unittest.TestCase):
    """``_cleanup`` runs on the worker thread (queued from main via
    ``_stop_requested``) and must stop the heartbeat ``QTimer`` while we
    still own its dispatcher — otherwise the later main-thread Python GC
    of the worker trips ``QObject::killTimer: Timers cannot be stopped
    from another thread``.
    """

    def test_cleanup_stops_and_clears_heartbeat_timer(self):
        worker = _make_worker(bridge_module=mock.MagicMock())
        # Pretend ``initialize`` had run earlier and installed a timer.
        fake_timer = mock.MagicMock(name="heartbeat_timer")
        worker._heartbeat_timer = fake_timer

        worker._cleanup()

        fake_timer.stop.assert_called_once_with()
        # Reference dropped so later Python GC of the worker doesn't
        # find an active timer to kill on the wrong thread.
        self.assertIsNone(worker._heartbeat_timer)

    def test_cleanup_is_idempotent_when_timer_never_initialized(self):
        """Safe to call ``_cleanup`` even if PyQt5 was missing at
        ``initialize`` time (or if the worker thread never started)."""
        worker = _make_worker(bridge_module=mock.MagicMock())
        worker._heartbeat_timer = None
        # Should not raise.
        worker._cleanup()
        self.assertIsNone(worker._heartbeat_timer)

    def test_cleanup_safe_to_call_twice(self):
        """Defensive: queued events can in principle deliver twice on
        weird shutdown sequences. Second call must be a no-op."""
        worker = _make_worker(bridge_module=mock.MagicMock())
        fake_timer = mock.MagicMock(name="heartbeat_timer")
        worker._heartbeat_timer = fake_timer
        worker._cleanup()
        worker._cleanup()
        # The timer was stopped exactly once (the second call short-
        # circuited on the ``is None`` guard).
        fake_timer.stop.assert_called_once_with()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
