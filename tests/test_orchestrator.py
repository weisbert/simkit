"""Unit tests for simkit.orchestrator (Phase 3A §5 skeleton).

Run with stdlib unittest or pytest:

    PYTHONPATH=python python3.11 -m unittest tests.test_orchestrator -v
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.orchestrator import (  # noqa: E402
    NotImplementedYetError,
    OrchestratorError,
    PlannedItem,
    RunPlan,
    dry_run,
    execute,
    plan_review,
    synthesize_adhoc_review,
)
from simkit.review import load_review  # noqa: E402


_EXAMPLE_REVIEW = _REPO_ROOT / "config" / "review_example.review.json"
_EXAMPLE_UNION = _REPO_ROOT / "config" / "pvt_union_example.union.json"


class PlanReviewTests(unittest.TestCase):
    """plan_review() — degraded path: example file's union paths don't exist."""

    def test_example_plans_all_items_with_path_issues(self):
        review = load_review(_EXAMPLE_REVIEW)
        plan = plan_review(review)
        self.assertEqual(len(plan.planned), 5)
        # No corner counts since union files don't exist
        for p in plan.planned:
            self.assertIsNone(p.corner_count)
            self.assertGreaterEqual(len(p.path_issues), 1)
        self.assertTrue(plan.has_blocking_issues)
        self.assertEqual(plan.total_corners, 0)

    def test_items_filter_subset(self):
        review = load_review(_EXAMPLE_REVIEW)
        plan = plan_review(review, items_filter=["BT2GRX trans PVT", "干扰仿真"])
        self.assertEqual(len(plan.planned), 2)
        names = [p.item.name for p in plan.planned]
        self.assertEqual(names, ["BT2GRX trans PVT", "干扰仿真"])

    def test_items_filter_unknown_name_errors(self):
        review = load_review(_EXAMPLE_REVIEW)
        with self.assertRaises(OrchestratorError) as cm:
            plan_review(review, items_filter=["does_not_exist"])
        self.assertIn("--items", str(cm.exception))

    def test_disabled_items_skipped(self):
        """A disabled item is dropped from planned[]."""
        review = load_review(_EXAMPLE_REVIEW)
        # Mutate the loaded shape isn't safe (frozen dataclasses); rebuild
        # via JSON edit.
        with _EXAMPLE_REVIEW.open() as f:
            doc = json.load(f)
        doc["items"][0]["enabled"] = False
        tmp = Path(tempfile.mkdtemp(prefix="simkit_orch_"))
        try:
            p = tmp / "review_example.review.json"
            p.write_text(json.dumps(doc))
            new_review = load_review(p)
            plan = plan_review(new_review)
            self.assertEqual(len(plan.planned), 4)  # was 5, one disabled
            self.assertNotIn(
                "BT2GRX trans PVT",
                [pl.item.name for pl in plan.planned],
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PlanReviewWithRealUnionTests(unittest.TestCase):
    """plan_review() — happy path: synthesise a review pointing at the real
    Phase 2 example union, verify corner_count is non-None."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_orch_real_"))
        # Copy the real union over preserving its basename (load_union
        # requires basename to match the internal "name" field).
        dest = self.tmp / _EXAMPLE_UNION.name
        shutil.copy(_EXAMPLE_UNION, dest)
        # Build a review pointing at it
        doc = {
            "review_schema_version": 1,
            "name": "myreview",
            "project": "my_ldo",
            "items": [
                {
                    "name": "item one",
                    "tests": ["sim_a"],
                    "union": _EXAMPLE_UNION.name,
                }
            ],
        }
        self.path = self.tmp / "myreview.review.json"
        self.path.write_text(json.dumps(doc))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_corner_count_populated(self):
        review = load_review(self.path)
        plan = plan_review(review)
        self.assertEqual(len(plan.planned), 1)
        # The Phase 2 example union explodes to 7 sub-corners.
        self.assertEqual(plan.planned[0].corner_count, 7)
        self.assertFalse(plan.has_blocking_issues)
        self.assertEqual(plan.total_corners, 7)


class DryRunTests(unittest.TestCase):
    def test_dry_run_writes_to_stream(self):
        review = load_review(_EXAMPLE_REVIEW)
        plan = plan_review(review)
        buf = io.StringIO()
        dry_run(plan, stream=buf)
        out = buf.getvalue()
        self.assertIn("REVIEW review_example", out)
        self.assertIn("BT2GRX trans PVT", out)
        self.assertIn("干扰仿真", out)
        self.assertIn("SUMMARY", out)
        # ISSUE lines for the missing files
        self.assertIn("ISSUE:", out)
        # No exception raised


class SynthesizeAdHocTests(unittest.TestCase):
    def test_one_item_review_built(self):
        review = synthesize_adhoc_review(
            project="testproj",
            tests=["sim_a", "sim_b"],
            union=Path("/tmp/whatever.union.json"),
            bundle=None,
        )
        self.assertEqual(len(review.items), 1)
        item = review.items[0]
        self.assertEqual(item.name, "ad-hoc")
        self.assertEqual(list(item.tests), ["sim_a", "sim_b"])
        self.assertIsNone(item.bundle)
        self.assertEqual(item.on_failure.default, "skip")

    def test_adhoc_inherits_suite_on_failure(self):
        review = synthesize_adhoc_review(
            project="p",
            tests=["t"],
            union=Path("/tmp/x.union.json"),
            on_failure_dict={
                "default": "halt",
                "strategies": [{"name": "naive_retry", "max_attempts": 3}],
            },
        )
        pol = review.items[0].on_failure
        self.assertEqual(pol.default, "halt")
        self.assertEqual(pol.item_policy, "halt")
        self.assertEqual(pol.corner_policy, "halt")
        self.assertEqual(len(pol.strategies), 1)
        self.assertEqual(pol.strategies[0].max_attempts, 3)


class ExecuteSkeletonTests(unittest.TestCase):
    """execute() is wired against a bridge interface; we test the happy
    path with a mock bridge — no real Maestro touched."""

    def test_execute_with_mock_bridge_runs_each_item(self):
        review = load_review(_EXAMPLE_REVIEW)
        plan = plan_review(review)

        class MockBridge:
            def __init__(self):
                self.calls = []

            def pvt_runner_snapshot_test_state(self, *, session):
                self.calls.append(("snap", session))
                return [("Test", True)]

            def pvt_runner_restore_test_state(self, snap, *, session):
                self.calls.append(("restore", session))

            def pvt_runner_enable_only(self, names, *, session):
                self.calls.append(("enable_only", tuple(names)))
                return [(n, True, True) for n in names]

            def pvt_runner_run(self, hist, *, session):
                self.calls.append(("run", hist))
                return (0, 0, hist)

            def pvt_save(self, hist, *, pvtproject_path, session):
                self.calls.append(("save", hist))
                return f"/tmp/fake_run_{hist}"

            def pvt_corners_push(self, path, **kwargs):
                self.calls.append(("push", path))

        bridge = MockBridge()
        # ingest_cb=lambda noop bypasses _default_ingest (which would try to
        # open the fake .pvtproject and emit a noisy warning into test output).
        report = execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=Path("/tmp/fake.pvtproject"),
            push_union=False,
            ingest_cb=lambda run_dir: None,
        )
        self.assertTrue(report.snapshot_restored)
        # 5 items × (enable_only, run, save) + snap + restore
        kinds = [c[0] for c in bridge.calls]
        self.assertEqual(kinds[0], "snap")
        self.assertEqual(kinds[-1], "restore")
        self.assertEqual(kinds.count("enable_only"), 5)
        self.assertEqual(kinds.count("run"), 5)
        self.assertEqual(kinds.count("save"), 5)
        # All items completed (no exceptions)
        self.assertEqual(len(report.items), 5)
        for it in report.items:
            self.assertTrue(it.completed)

    def test_execute_requires_pvtproject(self):
        review = load_review(_EXAMPLE_REVIEW)
        plan = plan_review(review)
        with self.assertRaises(OrchestratorError):
            execute(plan, bridge=None, session="x")


if __name__ == "__main__":
    unittest.main()
