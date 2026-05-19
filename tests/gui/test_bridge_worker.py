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

import queue
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
    installed. Replaces signal attributes with ``MagicMock`` so any
    ``.emit(...)`` call inside the worker's methods is recordable and
    side-effect-free.
    """
    worker = object.__new__(BridgeWorker)
    worker._bridge_module = bridge_module
    worker._clock = clock if clock is not None else (lambda: 0.0)
    worker._queue = queue.Queue()
    worker._next_id = 1
    worker._is_busy = False
    worker._status = BridgeStatus.AMBER
    worker._consec_fails = 0
    worker._last_heartbeat_ok_at = None
    worker._stopped = False
    worker._heartbeat_timer = None
    worker.op_complete = mock.MagicMock(name="op_complete")
    worker.op_failed = mock.MagicMock(name="op_failed")
    worker.busy_changed = mock.MagicMock(name="busy_changed")
    worker.status_changed = mock.MagicMock(name="status_changed")
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
    """``queue_op`` is thread-safe + returns monotonic request IDs."""

    def test_queue_op_assigns_monotonic_ids(self):
        worker = _make_worker(bridge_module=mock.MagicMock())
        a = worker.queue_op("evalstring", expr="t")
        b = worker.queue_op("pvt_corners_pull", out_path="/x")
        c = worker.queue_op("evalstring", expr="t")
        self.assertEqual([a, b, c], [1, 2, 3])
        # Three ops sitting in the queue, in FIFO order.
        self.assertEqual(worker._queue.qsize(), 3)
        op1 = worker._queue.get_nowait()
        op2 = worker._queue.get_nowait()
        op3 = worker._queue.get_nowait()
        self.assertEqual(
            (op1.request_id, op1.func_name), (1, "evalstring"),
        )
        self.assertEqual(
            (op2.request_id, op2.func_name), (2, "pvt_corners_pull"),
        )
        self.assertEqual(op3.request_id, 3)

    def test_stop_posts_sentinel_to_queue(self):
        worker = _make_worker(bridge_module=mock.MagicMock())
        worker.stop()
        self.assertTrue(worker._stopped)
        self.assertEqual(worker._queue.qsize(), 1)
        self.assertIsNone(worker._queue.get_nowait())


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
