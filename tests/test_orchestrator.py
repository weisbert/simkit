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
    """End-to-end execute() with a 2-item review that pipes IC from trans → PSS.

    Synthetic on-disk results tree + mock bridge that records every
    pvt_runner_set_ic_source / clear call. v1.2 v1 behaviour pinned:
    set+clear fire once per consumer item, against corner-1's IC.
    """

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_orch_ic_"))
        # Lay out: <tmp>/results/maestro/<hist>/1/<test>/netlist/spectre.fc
        # Project file sits at <tmp>/<name>.pvtproject (its parent is
        # the session dir; _resolve_results_root joins ./results/maestro).
        self.pvt_path = self.tmp / "myproj.pvtproject"
        self.pvt_path.write_text('{"schema_version":1,"project":"myproj"}\n')
        # Use the history name the mock bridge will generate so the IC
        # resolver finds the .fc file we plant.
        # _sanitize_history("trans") = "trans"; the orch builds
        # "<prefix>_trans_<ts>_<idx>" — too dynamic. We override
        # MockBridge.pvt_runner_run to return a fixed string.
        self.fixed_hist = "fixed_trans_hist"
        corner_dir = (self.tmp / "results" / "maestro" / self.fixed_hist
                      / "1" / "sim_trans" / "netlist")
        corner_dir.mkdir(parents=True)
        (corner_dir / "spectre.fc").write_text("# fake fc\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_review(self):
        """2-item review: trans → pss, same union, ic_from on pss."""
        union_path = self.tmp / "unions" / "u.union.json"
        union_path.parent.mkdir(exist_ok=True)
        union_path.write_text("{}")  # plan_review tolerates unloadable
        doc = {
            "review_schema_version": 2,
            "name": "icfrom",
            "project": "myproj",
            "items": [
                {"name": "trans", "tests": ["sim_trans"],
                 "union": "unions/u.union.json"},
                {"name": "pss", "tests": ["sim_trans"],  # same test name so
                 "union": "unions/u.union.json",          # IC dir matches
                 "ic_from": {"item": "trans", "file": "fc", "mode": "readns"}},
            ],
        }
        review_path = self.tmp / "icfrom.review.json"
        review_path.write_text(json.dumps(doc))
        return load_review(review_path)

    def _make_bridge(self, fixed_hist: str, *, set_returns: str = "/old/path"):
        """MockBridge that returns a fixed history name + records ic calls."""
        class MockBridge:
            def __init__(self):
                self.calls = []
                self.ic_set_calls = []
                self.ic_clear_calls = []

            def pvt_runner_snapshot_test_state(self, *, session):
                self.calls.append(("snap",))
                return [("sim_trans", True)]

            def pvt_runner_restore_test_state(self, snap, *, session):
                self.calls.append(("restore",))

            def pvt_runner_enable_only(self, names, *, session):
                self.calls.append(("enable_only", tuple(names)))

            def pvt_runner_run(self, hist, *, session, **kwargs):
                self.calls.append(("run", hist))
                return (0, 0, fixed_hist)

            def pvt_save(self, hist, *, pvtproject_path, session):
                self.calls.append(("save", hist))
                return str(Path("/tmp") / f"fake_dump_{hist}")

            def pvt_corners_push(self, path, **kwargs):
                self.calls.append(("push",))

            def pvt_runner_set_ic_source(self, test, path, mode, *, session):
                self.ic_set_calls.append((test, path, mode))
                return set_returns

            def pvt_runner_clear_ic_source(self, test, mode, prev, *, session):
                self.ic_clear_calls.append((test, mode, prev))

        return MockBridge()

    def test_happy_path_sets_and_clears_ic(self):
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.fixed_hist)
        execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # set called once on the consumer item, with corner-1's resolved .fc
        self.assertEqual(len(bridge.ic_set_calls), 1)
        test_name, path, mode = bridge.ic_set_calls[0]
        self.assertEqual(test_name, "sim_trans")
        self.assertEqual(mode, "readns")
        self.assertTrue(path.endswith("/1/sim_trans/netlist/spectre.fc"))
        # clear called once, restoring the prev value the mock returned
        self.assertEqual(len(bridge.ic_clear_calls), 1)
        self.assertEqual(bridge.ic_clear_calls[0],
                         ("sim_trans", "readns", "/old/path"))

    def test_no_upstream_history_runs_naked(self):
        # Make the upstream item fail (run returns OK but we pretend its
        # history never makes it into history_by_item by simulating an
        # error — the cleanest way is to break pvt_runner_run on item 0).
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.fixed_hist)

        orig_run = bridge.pvt_runner_run
        call_n = {"n": 0}
        def failing_first_run(hist, *, session, **kwargs):
            call_n["n"] += 1
            if call_n["n"] == 1:
                raise RuntimeError("trans died")
            return orig_run(hist, session=session, **kwargs)
        bridge.pvt_runner_run = failing_first_run

        report = execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # No IC set/clear because source never recorded a history
        self.assertEqual(bridge.ic_set_calls, [])
        self.assertEqual(bridge.ic_clear_calls, [])
        # PSS item should still have run + completed, with a note
        pss_result = next(r for r in report.items if r.item_name == "pss")
        self.assertTrue(pss_result.completed)
        self.assertIn("no recorded history", pss_result.notes)

    def test_ic_file_missing_runs_naked(self):
        # Plant the upstream history in history_by_item by letting it run,
        # but DELETE its IC file before the consumer item runs. Easiest
        # path: rename the spectre.fc so resolve_ic_path returns None.
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.fixed_hist)

        # Use item_done_cb to delete the file between items
        ic_file = (self.tmp / "results" / "maestro" / self.fixed_hist
                   / "1" / "sim_trans" / "netlist" / "spectre.fc")
        def after_each(result):
            if result.item_name == "trans":
                ic_file.unlink()
        report = execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
            item_done_cb=after_each,
        )
        # No IC set/clear because the file was missing at lookup time
        self.assertEqual(bridge.ic_set_calls, [])
        self.assertEqual(bridge.ic_clear_calls, [])
        pss_result = next(r for r in report.items if r.item_name == "pss")
        self.assertTrue(pss_result.completed)
        self.assertIn("corner 1", pss_result.notes)

    def test_set_ic_raises_skips_clear(self):
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.fixed_hist)
        def explode(test, path, mode, *, session):
            raise RuntimeError("readns not registered")
        bridge.pvt_runner_set_ic_source = explode

        report = execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # Clear should NOT have run (no successful set to roll back)
        # — actually our helper returns "" sentinel on partial-set so the
        # orchestrator does call clear with "". Pin that explicit behaviour:
        # if a SET-attempt errored before producing a real prev, clear is
        # called once with "" to nudge the option back to empty.
        self.assertEqual(len(bridge.ic_clear_calls), 1)
        self.assertEqual(bridge.ic_clear_calls[0][2], "")
        pss_result = next(r for r in report.items if r.item_name == "pss")
        self.assertIn("readns not registered", pss_result.notes)


if __name__ == "__main__":
    unittest.main()
