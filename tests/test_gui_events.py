"""Unit tests for :mod:`simkit.gui_events` — the JSONL emitter producer."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.gui_events import GuiEventEmitter  # noqa: E402


def _lines(stream: io.StringIO) -> list[dict]:
    out = []
    for line in stream.getvalue().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


class DisabledTests(unittest.TestCase):
    def test_disabled_no_writes(self):
        buf = io.StringIO()
        em = GuiEventEmitter(enabled=False, stream=buf)
        em.emit({"event": "ignored"})
        em.item_started(item_index=1, item_name="x", total_items=1)
        em.error(code="x", msg="x")
        em.review_done(exit_code=0, summary={})
        self.assertEqual(buf.getvalue(), "")


class EmitShapeTests(unittest.TestCase):
    def setUp(self):
        self.buf = io.StringIO()
        self.em = GuiEventEmitter(enabled=True, stream=self.buf)

    def test_emit_stamps_ts_when_missing(self):
        self.em.emit({"event": "foo"})
        events = _lines(self.buf)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["event"], "foo")
        self.assertIn("ts", ev)
        self.assertTrue(ev["ts"].endswith("Z"))

    def test_emit_preserves_caller_ts(self):
        self.em.emit({"ts": "fixed", "event": "foo"})
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["ts"], "fixed")

    def test_one_line_per_event(self):
        self.em.emit({"event": "a"})
        self.em.emit({"event": "b"})
        self.em.emit({"event": "c"})
        raw = self.buf.getvalue()
        self.assertEqual(raw.count("\n"), 3)
        events = _lines(self.buf)
        self.assertEqual([e["event"] for e in events], ["a", "b", "c"])

    def test_unicode_passes_through(self):
        self.em.item_started(item_index=1, item_name="干扰仿真", total_items=2)
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["item_name"], "干扰仿真")

    def test_item_started(self):
        self.em.item_started(item_index=2, item_name="BT2GRX trans", total_items=5)
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["event"], "item_started")
        self.assertEqual(ev["item_index"], 2)
        self.assertEqual(ev["item_name"], "BT2GRX trans")
        self.assertEqual(ev["total_items"], 5)

    def test_item_progress(self):
        self.em.item_progress(
            item_index=1, running=2, completed=3, failed=1, total_corners=6,
        )
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["event"], "item_progress")
        self.assertEqual(ev["running"], 2)
        self.assertEqual(ev["completed"], 3)
        self.assertEqual(ev["failed"], 1)
        self.assertEqual(ev["total_corners"], 6)

    def test_item_completed_with_history(self):
        self.em.item_completed(
            item_index=1, run_id="abc123", completed=5, failed=1,
            history_name="orch_x_123_1",
        )
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["event"], "item_completed")
        self.assertEqual(ev["run_id"], "abc123")
        self.assertEqual(ev["history_name"], "orch_x_123_1")

    def test_item_completed_no_history(self):
        self.em.item_completed(
            item_index=1, run_id="", completed=0, failed=0, history_name=None,
        )
        ev = _lines(self.buf)[0]
        self.assertIsNone(ev["history_name"])

    def test_log(self):
        self.em.log("warn", "couldn't push union")
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["event"], "log")
        self.assertEqual(ev["level"], "warn")
        self.assertEqual(ev["msg"], "couldn't push union")

    def test_review_done(self):
        self.em.review_done(exit_code=0, summary={"items": [{"name": "x"}]})
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["event"], "review_done")
        self.assertEqual(ev["exit_code"], 0)
        self.assertEqual(ev["summary"], {"items": [{"name": "x"}]})

    def test_error(self):
        self.em.error(code="bridge_import", msg="nope")
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["event"], "error")
        self.assertEqual(ev["code"], "bridge_import")
        self.assertEqual(ev["msg"], "nope")

    def test_strategy_attempt(self):
        self.em.strategy_attempt(
            item_index=1, strategy_name="naive_retry", attempt_number=2,
            outcome="recovered", targeted=["TT", "SS"], remaining=[],
        )
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["event"], "strategy_attempt")
        self.assertEqual(ev["strategy_name"], "naive_retry")
        self.assertEqual(ev["attempt_number"], 2)
        self.assertEqual(ev["outcome"], "recovered")
        self.assertEqual(ev["targeted"], ["TT", "SS"])
        self.assertEqual(ev["remaining"], [])

    def test_emit_dict_event_callback_alias(self):
        self.em.emit_dict_event_callback({"event": "x"})
        ev = _lines(self.buf)[0]
        self.assertEqual(ev["event"], "x")


class DefaultStreamTests(unittest.TestCase):
    def test_default_stream_is_sys_stdout_at_emit_time(self):
        em = GuiEventEmitter(enabled=True)
        captured = io.StringIO()
        old = sys.stdout
        sys.stdout = captured
        try:
            em.emit({"event": "x"})
        finally:
            sys.stdout = old
        self.assertIn('"event": "x"', captured.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
