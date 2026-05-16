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


# ---------------------------------------------------------------------------
# Phase 3A v1.2: ic_from execute-path (DECISIONS #57)


class ExecuteIcFromTests(unittest.TestCase):
    """End-to-end execute() with a 2-item review piping IC from trans → PSS.

    Phase 3A v1.2 stage-2 (DECISIONS #57): the PSS item is run ONE CORNER
    AT A TIME, each corner with its own IC pointing at the upstream
    trans's per-corner spectre.fc. These tests pin:
      * happy path: N corners → N set / N clear / N run calls
      * upstream-failed → consumer falls back to batch (no IC)
      * one corner's IC missing → that corner runs naked, others succeed
      * corner-enable snapshot/restore wraps the loop
    """

    # 3-corner union shared by both items (same content; loader requires
    # source.union == consumer.union so we just emit it once).
    UNION_DOC = {
        "union_schema_version": 1,
        "name": "u",
        "project": "myproj",
        "testbench_id": "tb",
        "rows": [
            {"row_name": "C1", "vars": {"VDD": "1.0"}, "models": []},
            {"row_name": "C2", "vars": {"VDD": "1.1"}, "models": []},
            {"row_name": "C3", "vars": {"VDD": "1.2"}, "models": []},
        ],
    }
    N_CORNERS = 3

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_orch_ic_"))
        self.pvt_path = self.tmp / "myproj.pvtproject"
        self.pvt_path.write_text('{"schema_version":1,"project":"myproj"}\n')
        # The MockBridge returns a fixed trans-history name; orchestrator
        # uses it as history_by_item["trans"], and the IC resolver looks
        # for <results>/<hist>/<N>/<test>/netlist/spectre.fc on disk.
        self.trans_hist = "fixed_trans_hist"
        for corner_idx in range(1, self.N_CORNERS + 1):
            d = (self.tmp / "results" / "maestro" / self.trans_hist
                 / str(corner_idx) / "sim_test" / "netlist")
            d.mkdir(parents=True)
            (d / "spectre.fc").write_text(f"# fake fc corner {corner_idx}\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_review(self):
        """2-item review: trans (batch) → pss (per-corner ic_from)."""
        union_path = self.tmp / "unions" / "u.union.json"
        union_path.parent.mkdir(exist_ok=True)
        union_path.write_text(json.dumps(self.UNION_DOC))
        doc = {
            "review_schema_version": 2,
            "name": "icfrom",
            "project": "myproj",
            "items": [
                {"name": "trans", "tests": ["sim_test"],
                 "union": "unions/u.union.json"},
                {"name": "pss", "tests": ["sim_test"],
                 "union": "unions/u.union.json",
                 "ic_from": {"item": "trans", "file": "fc", "mode": "readns"}},
            ],
        }
        review_path = self.tmp / "icfrom.review.json"
        review_path.write_text(json.dumps(doc))
        return load_review(review_path)

    def _make_bridge(self, trans_hist: str):
        """MockBridge with all the v1.2 stage-2 surface methods."""
        class MockBridge:
            def __init__(self):
                self.ic_set_calls = []
                self.ic_clear_calls = []
                self.enable_corner_calls = []
                self.run_histories = []
                self.run_n = 0
                self.snap_called = 0
                self.restore_called = 0

            def pvt_runner_snapshot_test_state(self, *, session):
                return [("sim_test", True)]

            def pvt_runner_restore_test_state(self, snap, *, session):
                pass

            def pvt_runner_enable_only(self, names, *, session):
                pass

            def pvt_runner_run(self, hist, *, session, **kwargs):
                self.run_n += 1
                self.run_histories.append(hist)
                # First run is the trans batch item — return fixed name
                # so the IC resolver finds spectre.fc on disk. Subsequent
                # per-corner runs return their own caller-provided name.
                if self.run_n == 1:
                    return (0, 0, trans_hist)
                return (0, 0, hist)

            def pvt_save(self, hist, *, pvtproject_path, session):
                return str(Path("/tmp") / f"fake_dump_{hist}")

            def pvt_corners_push(self, path, **kwargs):
                pass

            def pvt_runner_set_ic_source(self, test, path, mode, *, session):
                self.ic_set_calls.append((test, path, mode))
                return "/orig/additionalArgs"

            def pvt_runner_clear_ic_source(self, test, mode, prev, *, session):
                self.ic_clear_calls.append((test, mode, prev))

            def pvt_runner_snapshot_corners_enable(self, *, session):
                self.snap_called += 1
                return [("C1", True), ("C2", False), ("C3", True)]

            def pvt_runner_enable_corner_by_index(self, idx, *, session):
                self.enable_corner_calls.append(idx)
                return f"C{idx}"

            def pvt_runner_restore_corners_enable(self, snap, *, session):
                self.restore_called += 1

        return MockBridge()

    def test_per_corner_happy_path(self):
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.trans_hist)
        report = execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # N corners → N set / N clear / N enable_corner / N+1 runs
        # (+1 for the upstream trans batch run).
        self.assertEqual(len(bridge.ic_set_calls), self.N_CORNERS)
        self.assertEqual(len(bridge.ic_clear_calls), self.N_CORNERS)
        self.assertEqual(bridge.enable_corner_calls, [1, 2, 3])
        self.assertEqual(len(bridge.run_histories), 1 + self.N_CORNERS)

        # IC set paths point at distinct per-corner .fc files
        paths = [c[1] for c in bridge.ic_set_calls]
        self.assertTrue(paths[0].endswith("/1/sim_test/netlist/spectre.fc"))
        self.assertTrue(paths[1].endswith("/2/sim_test/netlist/spectre.fc"))
        self.assertTrue(paths[2].endswith("/3/sim_test/netlist/spectre.fc"))

        # Clear always sees the prev value the set returned
        for c in bridge.ic_clear_calls:
            self.assertEqual(c, ("sim_test", "readns", "/orig/additionalArgs"))

        # Corner enable mask snapshot + restore both fired exactly once
        self.assertEqual(bridge.snap_called, 1)
        self.assertEqual(bridge.restore_called, 1)

        # PSS item completed with N run_dirs
        pss = next(r for r in report.items if r.item_name == "pss")
        self.assertTrue(pss.completed)
        self.assertEqual(len(pss.run_dirs), self.N_CORNERS)

    def test_upstream_failed_falls_back_to_batch(self):
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.trans_hist)

        # Make the trans item's run raise
        orig_run = bridge.pvt_runner_run
        def bomb_first(hist, *, session, **kwargs):
            if bridge.run_n == 0:
                bridge.run_n = 1
                raise RuntimeError("trans crashed")
            return orig_run(hist, session=session, **kwargs)
        bridge.pvt_runner_run = bomb_first

        report = execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # No per-corner activity since upstream had no history
        self.assertEqual(bridge.ic_set_calls, [])
        self.assertEqual(bridge.enable_corner_calls, [])
        self.assertEqual(bridge.snap_called, 0)
        pss = next(r for r in report.items if r.item_name == "pss")
        self.assertIn("no recorded history", pss.notes)
        self.assertTrue(pss.completed)  # ran naked via batch fallback

    def test_one_corner_missing_ic_runs_naked(self):
        # Delete corner-2's spectre.fc; corners 1 and 3 should still work.
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.trans_hist)
        ic2 = (self.tmp / "results" / "maestro" / self.trans_hist
               / "2" / "sim_test" / "netlist" / "spectre.fc")

        def after_each(result):
            if result.item_name == "trans":
                ic2.unlink()
        report = execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
            item_done_cb=after_each,
        )
        # Only corners 1 and 3 got set_ic; corner 2 ran naked
        self.assertEqual(len(bridge.ic_set_calls), 2)
        paths = [c[1] for c in bridge.ic_set_calls]
        self.assertTrue(paths[0].endswith("/1/sim_test/netlist/spectre.fc"))
        self.assertTrue(paths[1].endswith("/3/sim_test/netlist/spectre.fc"))
        # All 3 corners enabled (even the IC-less one)
        self.assertEqual(bridge.enable_corner_calls, [1, 2, 3])
        pss = next(r for r in report.items if r.item_name == "pss")
        self.assertIn("corner 2", pss.notes)

    def test_corner_enable_snapshot_restored_even_on_run_error(self):
        # Per-corner-run failures should NOT skip the restore in finally.
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.trans_hist)

        orig_run = bridge.pvt_runner_run
        def fail_corner_2(hist, *, session, **kwargs):
            bridge.run_n += 1
            bridge.run_histories.append(hist)
            if bridge.run_n == 3:  # 1=trans batch, 2=corner1, 3=corner2
                raise RuntimeError("corner 2 spectre died")
            if bridge.run_n == 1:
                return (0, 0, self.trans_hist)
            return (0, 0, hist)
        bridge.pvt_runner_run = fail_corner_2

        execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # Snapshot + restore both ran exactly once regardless of error
        self.assertEqual(bridge.snap_called, 1)
        self.assertEqual(bridge.restore_called, 1)
        # All 3 corners attempted enable (loop didn't break early)
        self.assertEqual(bridge.enable_corner_calls, [1, 2, 3])
        # IC cleared after every corner (even corner 2 which errored on run)
        self.assertEqual(len(bridge.ic_clear_calls), self.N_CORNERS)


if __name__ == "__main__":
    unittest.main()
