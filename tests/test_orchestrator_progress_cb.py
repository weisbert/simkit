"""Phase 4 §9 — `execute(progress_cb=...)` JSONL-shape events + `cancel_check`."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.failures import FailedCorner  # noqa: E402
from simkit.orchestrator import execute, plan_review  # noqa: E402
from simkit.review import load_review  # noqa: E402

# Re-use the existing mock-bridge + fixtures from the strategy-chain test.
from tests.test_orchestrator_strategy_chain import (  # noqa: E402
    _MockBridge,
    _query_factory,
    _write_review,
)


class ProgressCbTests(unittest.TestCase):

    def setUp(self):
        self._tmp_holder = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp_holder.name)

    def tearDown(self):
        self._tmp_holder.cleanup()

    def _run(self, *, strategies=None, fail_table=None, cancel_check=None,
             with_two_items=False):
        review_path = _write_review(
            self.tmp, strategies=strategies, with_two_items=with_two_items,
        )
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)
        events: list[dict] = []
        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=_query_factory(fail_table or {}),
            progress_cb=events.append,
            cancel_check=cancel_check,
        )
        return events, report, bridge

    def test_item_started_and_completed_clean(self):
        events, report, _bridge = self._run(
            strategies=[{"name": "naive_retry"}],
            fail_table={},
        )
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds, ["item_started", "item_completed"])
        started, completed = events
        self.assertEqual(started["item_index"], 1)
        self.assertEqual(started["item_name"], "it1")
        self.assertEqual(started["total_items"], 1)
        self.assertEqual(completed["item_index"], 1)
        self.assertEqual(completed["completed"], 1)
        self.assertEqual(completed["failed"], 0)
        # history_name is the first/only history.
        self.assertEqual(
            completed["history_name"], report.items[0].history_names[0],
        )
        # run_id non-empty (run.json was created).
        self.assertTrue(completed["run_id"])

    def test_strategy_attempt_recovered_event(self):
        # TT fails on primary, recovers on retry.
        def cb(_pvtproject_path, run_id):
            if "__retry" in run_id:
                return ()
            return (FailedCorner("TT", frozenset({"spec_fail"}),
                                 "sim_test", "vout"),)

        review_path = _write_review(
            self.tmp, strategies=[{"name": "naive_retry"}],
        )
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)
        events: list[dict] = []
        execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
            progress_cb=events.append,
        )
        kinds = [e["event"] for e in events]
        self.assertEqual(
            kinds,
            ["item_started", "strategy_attempt", "item_completed"],
        )
        att = events[1]
        self.assertEqual(att["item_index"], 1)
        self.assertEqual(att["strategy_name"], "naive_retry")
        self.assertEqual(att["attempt_number"], 1)
        self.assertEqual(att["outcome"], "recovered")
        self.assertEqual(att["targeted"], ["TT"])
        self.assertEqual(att["remaining"], [])

    def test_completed_failed_count_reflects_final_failed(self):
        def cb(_p, _rid):
            return (FailedCorner("TT", frozenset({"spec_fail"}),
                                 "sim_test", "vout"),)
        events, report, _bridge = self._run(
            strategies=None,  # no strategies → just report FAIL
            fail_table={},  # unused; cb is overridden below — use explicit
        )
        # Re-run with a real failing cb (helper doesn't support per-test).
        events.clear()
        bridge = _MockBridge(self.tmp)
        review_path = _write_review(self.tmp)
        review = load_review(review_path)
        plan = plan_review(review)
        execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
            progress_cb=events.append,
        )
        completed_evs = [e for e in events if e["event"] == "item_completed"]
        self.assertEqual(len(completed_evs), 1)
        self.assertEqual(completed_evs[0]["failed"], 1)

    def test_two_items_total_items_is_two(self):
        events, _report, _bridge = self._run(with_two_items=True)
        starts = [e for e in events if e["event"] == "item_started"]
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0]["item_index"], 1)
        self.assertEqual(starts[1]["item_index"], 2)
        self.assertEqual(starts[0]["total_items"], 2)
        self.assertEqual(starts[1]["total_items"], 2)

    def test_progress_cb_none_does_not_emit(self):
        review_path = _write_review(self.tmp)
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)
        # No progress_cb passed at all — must not raise.
        execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=_query_factory({}),
        )

    def test_progress_cb_exception_does_not_crash_run(self):
        def boom(_event):
            raise RuntimeError("explode")
        review_path = _write_review(self.tmp)
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)
        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=_query_factory({}),
            progress_cb=boom,
        )
        # Run still completed despite callback raising.
        self.assertEqual(len(report.items), 1)
        self.assertTrue(report.snapshot_restored)


class CancelCheckTests(unittest.TestCase):

    def setUp(self):
        self._tmp_holder = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp_holder.name)

    def tearDown(self):
        self._tmp_holder.cleanup()

    def test_cancel_check_true_skips_remaining_items(self):
        review_path = _write_review(self.tmp, with_two_items=True)
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)
        seen = {"calls": 0}

        def cancel():
            # Trigger cancellation BEFORE the 2nd item.
            seen["calls"] += 1
            return seen["calls"] > 1

        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=_query_factory({}),
            cancel_check=cancel,
        )
        # Only the first item ran.
        self.assertEqual(len(report.items), 1)
        self.assertEqual(report.items[0].item_name, "it1")
        # Snapshot restore still ran in the finally block.
        self.assertTrue(report.snapshot_restored)

    def test_cancel_check_false_runs_all(self):
        review_path = _write_review(self.tmp, with_two_items=True)
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)
        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=_query_factory({}),
            cancel_check=lambda: False,
        )
        self.assertEqual(len(report.items), 2)

    def test_cancel_check_raises_logged_not_propagated(self):
        review_path = _write_review(self.tmp)
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)

        def boom():
            raise RuntimeError("cancel-check-explode")
        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=_query_factory({}),
            cancel_check=boom,
        )
        # Run still progressed despite cancel_check raising.
        self.assertEqual(len(report.items), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
