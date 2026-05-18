"""Phase 3A v1.6 — strategy chain dispatch (DECISIONS #62).

Covers the new ``execute()`` integration:

* clean run (no FAIL) — no strategy invoked
* one FAIL corner — naive_retry recovers it
* one FAIL corner — naive_retry does not recover
* mixed: two corners FAIL, retry recovers one
* eval_err-only FAIL — not auto-retried, surfaced in final_failed
* no strategies declared — final_failed still populated
* unknown strategy name — skipped with note, chain continues
* max_attempts=2 — first attempt unchanged, second recovers
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.failures import FailedCorner  # noqa: E402
from simkit.orchestrator import (  # noqa: E402
    execute,
    plan_review,
)
from simkit.review import load_review  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers


def _write_review(tmp: Path, *, strategies=None, with_two_items=False):
    """Write a minimal review.json + union.json fixture; return its path."""
    union_doc = {
        "union_schema_version": 1,
        "name": "u", "project": "p", "testbench_id": "tb",
        "rows": [{"name": "TT", "enabled": True, "vars": {}, "models": []}],
    }
    union_path = tmp / "unions" / "u.union.json"
    union_path.parent.mkdir(exist_ok=True)
    union_path.write_text(json.dumps(union_doc))

    on_fail = {}
    if strategies is not None:
        on_fail = {"strategies": strategies}

    item = {
        "name": "it1", "tests": ["sim_test"],
        "union": "unions/u.union.json",
    }
    if on_fail:
        item["on_failure"] = on_fail
    items = [item]
    if with_two_items:
        items.append({"name": "it2", "tests": ["sim_test"],
                      "union": "unions/u.union.json"})

    doc = {
        "review_schema_version": 2,
        "name": "r", "project": "p",
        "items": items,
    }
    review_path = tmp / "r.review.json"
    review_path.write_text(json.dumps(doc))
    return review_path


def _make_run_dir(tmp: Path, run_id: str) -> Path:
    """Create a stub run dir with a minimal run.json so ``_load_run_id`` works.

    Mirrors the real PvtSave envelope shape — run_id lives at ``run.run_id``.
    """
    d = tmp / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "schema_version": 2,
        "run": {"run_id": run_id},
        "results": [],
        "artifacts": [],
    }))
    return d


class _MockBridge:
    """Records key calls; pvt_save returns a tmp run-dir per history."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.runs: list[str] = []         # history names passed to pvt_runner_run
        self.saves: list[str] = []        # history names passed to pvt_save
        self.corners_snap: list[tuple[str, bool]] = [
            ("TT", True), ("SS", True), ("FF", True),
        ]
        self.restore_log: list[list[tuple[str, bool]]] = []

    def pvt_runner_snapshot_test_state(self, *, session):
        return [("sim_test", True)]

    def pvt_runner_restore_test_state(self, snap, *, session):
        pass

    def pvt_runner_enable_only(self, names, *, session):
        pass

    def pvt_corners_push(self, path, **kwargs):
        pass

    def pvt_runner_run(self, hist, *, session, **kw):
        self.runs.append(hist)
        return (0, 0, hist)

    def pvt_save(self, hist, *, pvtproject_path, session):
        self.saves.append(hist)
        # Real bridge.pvt_save returns the run.json FILE path, not the
        # enclosing directory — mirror that here.
        return str(_make_run_dir(self.tmp, hist) / "run.json")

    def pvt_runner_snapshot_corners_enable(self, *, session):
        return list(self.corners_snap)

    def pvt_runner_restore_corners_enable(self, snap, *, session):
        self.restore_log.append(list(snap))


def _query_factory(fail_table: dict):
    """Build a query_failed_cb whose return is keyed by run_id.

    ``fail_table[run_id]`` is a tuple of (corner, reason_code) pairs;
    missing run_id ⇒ no FAIL.
    """
    def cb(pvtproject_path, run_id):
        out = []
        for corner, reason in fail_table.get(run_id, ()):
            out.append(FailedCorner(
                corner=corner,
                reasons=frozenset({reason}),
                sample_test="sim_test",
                sample_output="vout",
            ))
        return tuple(out)
    return cb


# ---------------------------------------------------------------------------
# Tests


class StrategyChainIntegrationTests(unittest.TestCase):

    def setUp(self):
        self._tmp_holder = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp_holder.name)

    def tearDown(self):
        self._tmp_holder.cleanup()

    def _run(self, *, strategies, fail_table):
        review_path = _write_review(self.tmp, strategies=strategies)
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)
        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=_query_factory(fail_table),
        )
        return report, bridge

    def test_clean_run_no_strategy_invoked(self):
        report, bridge = self._run(
            strategies=[{"name": "naive_retry"}],
            fail_table={},  # primary run has zero FAIL
        )
        item = report.items[0]
        self.assertEqual(item.final_failed_corners, ())
        self.assertEqual(item.strategy_attempts, ())
        # naive_retry never invoked → only the primary run, no retry hist
        self.assertEqual(len(bridge.runs), 1)
        self.assertEqual(len(item.history_names), 1)

    def test_one_fail_recovers_via_history_match(self):
        """TT fails on primary; after one retry it's clean."""
        review_path = _write_review(
            self.tmp, strategies=[{"name": "naive_retry"}],
        )
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)

        def cb(pvtproject_path, run_id):
            # Primary history = the first one save'd; retry has "__retry"
            if "__retry" in run_id:
                return ()  # clean
            return (FailedCorner("TT", frozenset({"spec_fail"}),
                                 "sim_test", "vout"),)

        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
        )
        item = report.items[0]
        self.assertEqual(item.final_failed_corners, ())
        self.assertEqual(len(item.strategy_attempts), 1)
        att = item.strategy_attempts[0]
        self.assertEqual(att.strategy_name, "naive_retry")
        self.assertEqual(att.outcome, "recovered")
        self.assertEqual(att.corners_targeted, ("TT",))
        self.assertEqual(att.corners_remaining, ())
        # 1 primary + 1 retry
        self.assertEqual(len(bridge.runs), 2)
        self.assertEqual(len(item.history_names), 2)
        self.assertIn("__retry1", item.history_names[1])

    def test_one_fail_does_not_recover(self):
        review_path = _write_review(
            self.tmp, strategies=[{"name": "naive_retry"}],
        )
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)

        # TT keeps failing.
        def cb(pvtproject_path, run_id):
            return (FailedCorner("TT", frozenset({"spec_fail"}),
                                 "sim_test", "vout"),)

        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
        )
        item = report.items[0]
        self.assertEqual(item.final_failed_corners, ("TT",))
        self.assertEqual(len(item.strategy_attempts), 1)
        self.assertEqual(item.strategy_attempts[0].outcome, "unchanged")

    def test_two_fails_one_recovers(self):
        review_path = _write_review(
            self.tmp, strategies=[{"name": "naive_retry"}],
        )
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)

        def cb(pvtproject_path, run_id):
            if "__retry" in run_id:
                # Only SS still fails after retry
                return (FailedCorner("SS", frozenset({"spec_fail"}),
                                     "sim_test", "vout"),)
            return (
                FailedCorner("TT", frozenset({"spec_fail"}),
                             "sim_test", "vout"),
                FailedCorner("SS", frozenset({"spec_fail"}),
                             "sim_test", "vout"),
            )

        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
        )
        item = report.items[0]
        self.assertEqual(item.final_failed_corners, ("SS",))
        att = item.strategy_attempts[0]
        self.assertEqual(att.outcome, "recovered")
        self.assertEqual(set(att.corners_targeted), {"TT", "SS"})
        self.assertEqual(att.corners_remaining, ("SS",))

    def test_eval_err_only_not_auto_retried(self):
        review_path = _write_review(
            self.tmp, strategies=[{"name": "naive_retry"}],
        )
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)

        def cb(pvtproject_path, run_id):
            return (FailedCorner("TT", frozenset({"eval_err"}),
                                 "sim_test", "vout"),)

        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
        )
        item = report.items[0]
        # Surfaced in final_failed, but no retry attempted.
        self.assertEqual(item.final_failed_corners, ("TT",))
        self.assertEqual(item.strategy_attempts, ())
        self.assertEqual(len(bridge.runs), 1)  # primary only

    def test_no_strategies_declared_still_reports_fails(self):
        review_path = _write_review(self.tmp, strategies=None)
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)

        def cb(pvtproject_path, run_id):
            return (FailedCorner("TT", frozenset({"spec_fail"}),
                                 "sim_test", "vout"),)

        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
        )
        item = report.items[0]
        self.assertEqual(item.final_failed_corners, ("TT",))
        self.assertEqual(item.strategy_attempts, ())

    def test_unknown_strategy_skipped_chain_continues(self):
        # Chain: bogus → naive_retry. Bogus skipped; naive_retry recovers.
        review_path = _write_review(
            self.tmp,
            strategies=[{"name": "bogus"}, {"name": "naive_retry"}],
        )
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)

        def cb(pvtproject_path, run_id):
            if "__retry" in run_id:
                return ()
            return (FailedCorner("TT", frozenset({"spec_fail"}),
                                 "sim_test", "vout"),)

        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
        )
        item = report.items[0]
        # Only naive_retry recorded an attempt (bogus never instantiated).
        self.assertEqual(len(item.strategy_attempts), 1)
        self.assertEqual(item.strategy_attempts[0].strategy_name, "naive_retry")
        self.assertIn("unknown strategy", item.notes)
        self.assertEqual(item.final_failed_corners, ())

    def test_max_attempts_two_recovers_on_second(self):
        review_path = _write_review(
            self.tmp,
            strategies=[{"name": "naive_retry", "max_attempts": 2}],
        )
        review = load_review(review_path)
        plan = plan_review(review)
        bridge = _MockBridge(self.tmp)

        # Track: 1st retry still FAIL, 2nd retry clean.
        retry_count = {"n": 0}

        def cb(pvtproject_path, run_id):
            if "__retry" in run_id:
                retry_count["n"] += 1
                if retry_count["n"] == 1:
                    return (FailedCorner("TT", frozenset({"spec_fail"}),
                                         "sim_test", "vout"),)
                return ()  # clean on 2nd retry
            return (FailedCorner("TT", frozenset({"spec_fail"}),
                                 "sim_test", "vout"),)

        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "fake.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
            query_failed_cb=cb,
        )
        item = report.items[0]
        self.assertEqual(item.final_failed_corners, ())
        self.assertEqual(len(item.strategy_attempts), 2)
        self.assertEqual(item.strategy_attempts[0].outcome, "unchanged")
        self.assertEqual(item.strategy_attempts[1].outcome, "recovered")
        self.assertEqual(len(bridge.runs), 3)  # primary + 2 retries


if __name__ == "__main__":
    unittest.main()
