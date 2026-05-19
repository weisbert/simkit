"""Phase 4 §9 — :class:`simkit.gui.controllers.run.RunController` tests.

Two layers:

* :class:`HandleLineTests` — pure JSONL parsing + signal emission via the
  ``_handle_line`` entry point. Uses :func:`_make_controller_bypass` to
  build a controller without going through the (C-extension) ``QObject``
  init, then replaces every signal with a ``MagicMock``. Avoids needing
  a ``QApplication``.

* :class:`SubprocessIntegrationTests` — spawns a tiny ``python -c '...'``
  subprocess via a real :class:`PyQt5.QtCore.QProcess`, drains JSONL,
  asserts the controller fires ``progress_event`` + ``run_finished``.
  Requires PyQt5 + an offscreen :class:`QApplication`; the existing
  ``test_results_model.py`` already follows this pattern.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


# Headless Qt — set BEFORE PyQt5 import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))


from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.gui.controllers.run import RunController  # noqa: E402


# Re-use existing QApplication if a sibling test already built one.
_QAPP = QApplication.instance() or QApplication(sys.argv)


def _make_controller_bypass(python_exe: str | None = None) -> RunController:
    """Build a RunController WITHOUT invoking ``QObject.__init__``.

    Mirrors the bypass-init pattern in ``test_bridge_worker.py``: replace
    each pyqtSignal attribute with a MagicMock so ``.emit(...)`` is
    recordable and side-effect free, then poke the fields ``__init__``
    would set. Lets us test the parser without a QApplication and
    without spawning real processes.
    """
    c = RunController.__new__(RunController)
    c._python_exe = python_exe or sys.executable
    c._proc = None
    c._buffer = bytearray()
    c._last_review_done = {}
    c._cancel_pending = False
    c.progress_event = mock.MagicMock(name="progress_event")
    c.run_finished = mock.MagicMock(name="run_finished")
    c.cancelled = mock.MagicMock(name="cancelled")
    c.error = mock.MagicMock(name="error")
    return c


def _spin_until(predicate, timeout_ms: int = 5000) -> bool:
    """Spin the Qt event loop until ``predicate()`` is True or timeout."""
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(20)
    elapsed = {"ms": 0}

    def _tick():
        elapsed["ms"] += timer.interval()
        if predicate() or elapsed["ms"] >= timeout_ms:
            timer.stop()
            loop.quit()

    timer.timeout.connect(_tick)
    timer.start()
    loop.exec_()
    return predicate()


class HandleLineTests(unittest.TestCase):
    """Pure-Python parsing tests; no Qt event loop, no subprocess."""

    def test_valid_jsonl_line_emits_progress_event(self):
        c = _make_controller_bypass()
        c._handle_line('{"event": "item_started", "item_index": 1}')
        c.progress_event.emit.assert_called_once()
        arg = c.progress_event.emit.call_args.args[0]
        self.assertEqual(arg["event"], "item_started")
        self.assertEqual(arg["item_index"], 1)

    def test_malformed_json_is_skipped(self):
        c = _make_controller_bypass()
        c._handle_line("not json at all")
        c.progress_event.emit.assert_not_called()

    def test_non_object_jsonl_is_skipped(self):
        c = _make_controller_bypass()
        c._handle_line("[1, 2, 3]")
        c.progress_event.emit.assert_not_called()

    def test_review_done_is_captured_for_finish_summary(self):
        c = _make_controller_bypass()
        c._handle_line('{"event": "review_done", "exit_code": 0, "summary": {"x": 1}}')
        self.assertEqual(c._last_review_done["event"], "review_done")
        self.assertEqual(c._last_review_done["exit_code"], 0)

    def test_buffer_splits_on_newline(self):
        """Drive _on_ready_read indirectly: append bytes + drain."""
        c = _make_controller_bypass()
        # Simulate readyReadStandardOutput receiving 1.5 lines, then the rest.
        c._buffer.extend(b'{"event":"a"}\n{"event":"b"')
        # Manually drain (replicate _on_ready_read's loop).
        while b"\n" in c._buffer:
            line_b, _, rest = c._buffer.partition(b"\n")
            c._buffer = bytearray(rest)
            c._handle_line(line_b.decode().strip())
        # First line dispatched, second still buffered.
        self.assertEqual(c.progress_event.emit.call_count, 1)
        c._buffer.extend(b'}\n')
        while b"\n" in c._buffer:
            line_b, _, rest = c._buffer.partition(b"\n")
            c._buffer = bytearray(rest)
            c._handle_line(line_b.decode().strip())
        self.assertEqual(c.progress_event.emit.call_count, 2)

    def test_blank_lines_skipped(self):
        c = _make_controller_bypass()
        c._handle_line("")
        c._handle_line("   ")
        c.progress_event.emit.assert_not_called()

    def test_unicode_payload_round_trips(self):
        c = _make_controller_bypass()
        c._handle_line('{"event":"item_started","item_name":"干扰仿真"}')
        arg = c.progress_event.emit.call_args.args[0]
        self.assertEqual(arg["item_name"], "干扰仿真")


class StartRunGuardTests(unittest.TestCase):
    """Verify start_run / is_running guard logic without real spawning."""

    def test_start_run_emits_error_when_already_running(self):
        c = _make_controller_bypass()
        # Fake an in-flight QProcess by stubbing is_running.
        fake_proc = mock.MagicMock()
        fake_proc.state.return_value = 999  # != NotRunning(0)
        c._proc = fake_proc
        ok = c.start_run("r.review.json", session="s")
        self.assertFalse(ok)
        c.error.emit.assert_called_once()
        msg = c.error.emit.call_args.args[0]
        self.assertIn("another run", msg)


class SubprocessIntegrationTests(unittest.TestCase):
    """Spawn a real ``python -c`` subprocess via QProcess + drain JSONL."""

    def setUp(self):
        # Build a real RunController (NOT bypass-init) so QProcess wiring
        # is exercised end-to-end.
        self.c = RunController(python_exe=sys.executable)
        self.events: list[dict] = []
        self.finished: list[tuple[int, dict]] = []
        self.errors: list[str] = []
        self.c.progress_event.connect(self.events.append)
        self.c.run_finished.connect(
            lambda code, summ: self.finished.append((code, summ))
        )
        self.c.error.connect(self.errors.append)

    def _launch(self, code: str, *, extra: list[str] | None = None) -> None:
        """Replace start_run's pvt-run argv with a tiny `python -c '...'`."""
        from PyQt5.QtCore import QProcess
        self.c._proc = QProcess(self.c)
        self.c._proc.setProcessChannelMode(QProcess.MergedChannels)
        self.c._proc.readyReadStandardOutput.connect(self.c._on_ready_read)
        self.c._proc.finished.connect(self.c._on_finished)
        self.c._proc.start(sys.executable, ["-c", code])
        self.assertTrue(self.c._proc.waitForStarted(3000))

    def test_real_subprocess_streams_jsonl(self):
        code = (
            "import json, sys\n"
            "for i in range(3):\n"
            "    print(json.dumps({'event':'item_started','item_index':i+1}))\n"
            "print(json.dumps({'event':'review_done','exit_code':0,'summary':{}}))\n"
            "sys.exit(0)\n"
        )
        self._launch(code)
        _spin_until(lambda: len(self.finished) >= 1, timeout_ms=10_000)
        self.assertEqual(len(self.finished), 1, msg=f"events={self.events}")
        kinds = [e["event"] for e in self.events]
        self.assertEqual(kinds.count("item_started"), 3)
        self.assertIn("review_done", kinds)
        exit_code, summary = self.finished[0]
        self.assertEqual(exit_code, 0)
        self.assertEqual(summary.get("event"), "review_done")

    def test_real_subprocess_skips_malformed_lines(self):
        code = (
            "import json, sys\n"
            "print(json.dumps({'event':'item_started','item_index':1}))\n"
            "print('not jsonl at all')\n"
            "print(json.dumps({'event':'review_done','exit_code':0,'summary':{}}))\n"
            "sys.exit(0)\n"
        )
        self._launch(code)
        _spin_until(lambda: len(self.finished) >= 1, timeout_ms=10_000)
        kinds = [e["event"] for e in self.events]
        # Both valid JSON lines parsed; malformed line skipped silently.
        self.assertEqual(kinds, ["item_started", "review_done"])

    def test_real_subprocess_propagates_exit_code(self):
        code = (
            "import json, sys\n"
            "print(json.dumps({'event':'error','code':'x','msg':'y'}))\n"
            "sys.exit(7)\n"
        )
        self._launch(code)
        _spin_until(lambda: len(self.finished) >= 1, timeout_ms=10_000)
        self.assertEqual(len(self.finished), 1)
        exit_code, summary = self.finished[0]
        self.assertEqual(exit_code, 7)
        # No review_done was emitted → summary is empty dict.
        self.assertEqual(summary, {})

    def test_trailing_partial_line_drained_on_finish(self):
        """Producer doesn't print a trailing newline; bytes still parsed."""
        code = (
            "import sys\n"
            'sys.stdout.write(\'{"event":"item_started","item_index":9}\')\n'
            "sys.stdout.flush()\n"
            "sys.exit(0)\n"
        )
        self._launch(code)
        _spin_until(lambda: len(self.finished) >= 1, timeout_ms=10_000)
        kinds = [e["event"] for e in self.events]
        self.assertIn("item_started", kinds)


class CancelTests(unittest.TestCase):
    """Cancel cascade: SIGTERM → 5s grace → SIGKILL + ``cancelled`` signal."""

    def test_cancel_noop_when_not_running(self):
        c = _make_controller_bypass()
        c.cancel()  # No proc — must not raise.
        c.cancelled.emit.assert_not_called()

    def test_cancel_calls_terminate_then_schedules_sigkill(self):
        c = _make_controller_bypass()
        fake_proc = mock.MagicMock()
        fake_proc.state.return_value = 999  # running
        c._proc = fake_proc
        with mock.patch(
            "simkit.gui.controllers.run.QTimer.singleShot"
        ) as msingle:
            c.cancel()
        fake_proc.terminate.assert_called_once()
        self.assertTrue(c._cancel_pending)
        # singleShot called with the 5_000 ms grace window.
        msingle.assert_called_once()
        ms = msingle.call_args.args[0]
        self.assertEqual(ms, 5000)

    def test_sigkill_only_fires_if_still_alive(self):
        c = _make_controller_bypass()
        fake_proc = mock.MagicMock()
        from PyQt5.QtCore import QProcess
        # State() returns NotRunning → no kill needed.
        fake_proc.state.return_value = QProcess.NotRunning
        c._proc = fake_proc
        c._sigkill_if_alive()
        fake_proc.kill.assert_not_called()

    def test_sigkill_fires_when_process_still_alive(self):
        c = _make_controller_bypass()
        fake_proc = mock.MagicMock()
        fake_proc.state.return_value = 999  # not NotRunning
        c._proc = fake_proc
        c._sigkill_if_alive()
        fake_proc.kill.assert_called_once()

    def test_on_finished_emits_cancelled_when_pending(self):
        c = _make_controller_bypass()
        c._proc = mock.MagicMock()
        c._cancel_pending = True
        c._on_finished(143, mock.Mock())  # 143 = 128 + SIGTERM
        c.cancelled.emit.assert_called_once()
        c.run_finished.emit.assert_called_once()
        code = c.run_finished.emit.call_args.args[0]
        self.assertEqual(code, 143)

    def test_on_finished_does_not_emit_cancelled_on_normal_exit(self):
        c = _make_controller_bypass()
        c._proc = mock.MagicMock()
        c._cancel_pending = False
        c._on_finished(0, mock.Mock())
        c.cancelled.emit.assert_not_called()
        c.run_finished.emit.assert_called_once()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
