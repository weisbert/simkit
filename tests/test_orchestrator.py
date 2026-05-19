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
    _pick_baseline_corner,
    dry_run,
    execute,
    plan_review,
    synthesize_adhoc_review,
)
from simkit.review import load_review  # noqa: E402
from simkit.union import ModelEntry, Union, UnionRow  # noqa: E402


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

    Phase 3A v1.3 (DECISIONS #57 stage-3): the consumer item runs as a
    SINGLE axlRunAllTests batch with per-corner IC delivered by a SKILL
    pre-run script attached to each test. These tests pin:
      * happy path: single run, pre-run script generated + installed,
        contains a corner→arg cons for every sub-corner with IC
      * upstream-failed → fallback to batch (no IC, no pre-run script)
      * one corner's IC missing → that corner's cons omitted from script
        (lookup misses → naked at runtime); other corners still mapped
      * cleanup: pre-run disabled + user's prior pre-run reattached
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
        """MockBridge with all the v1.3 pre-run surface methods."""
        class MockBridge:
            def __init__(self):
                self.run_histories = []
                self.run_n = 0
                self.installs = []        # list of (test, script_path)
                self.disables = []        # list of test
                self.get_prerun_calls = []
                self.clear_ic_calls = []  # cleanup at end

            def pvt_runner_snapshot_test_state(self, *, session):
                return [("sim_test", True)]

            def pvt_runner_restore_test_state(self, snap, *, session):
                pass

            def pvt_runner_enable_only(self, names, *, session):
                pass

            def pvt_runner_run(self, hist, *, session, **kwargs):
                self.run_n += 1
                self.run_histories.append(hist)
                if self.run_n == 1:
                    return (0, 0, trans_hist)
                return (0, 0, hist)

            def pvt_save(self, hist, *, pvtproject_path, session):
                return str(Path("/tmp") / f"fake_dump_{hist}")

            def pvt_corners_push(self, path, **kwargs):
                pass

            # v1.3 pre-run script surface
            def pvt_runner_get_pre_run_script(self, test, *, session):
                self.get_prerun_calls.append(test)
                return ""  # user had no prior pre-run

            def pvt_runner_install_pre_run_script(self, test, path, *, session):
                self.installs.append((test, path))
                return path

            def pvt_runner_disable_pre_run_script(self, test, *, session):
                self.disables.append(test)

            # Stage-1 leftovers used during cleanup
            def pvt_runner_clear_ic_source(self, test, mode, prev, *, session):
                self.clear_ic_calls.append((test, mode, prev))

        return MockBridge()

    def test_single_batch_with_pre_run_happy_path(self):
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
        # ONE consumer-item run (not N): trans = 1, pss = 1 → 2 total.
        self.assertEqual(len(bridge.run_histories), 2)

        # Pre-run script: get_pre_run_script + install (once per test),
        # disable (once per test on cleanup).
        self.assertEqual(bridge.get_prerun_calls, ["sim_test"])
        self.assertEqual(len(bridge.installs), 1)
        test_installed, script_path = bridge.installs[0]
        self.assertEqual(test_installed, "sim_test")
        self.assertTrue(script_path.endswith(".il"))
        self.assertTrue(Path(script_path).exists())
        self.assertEqual(bridge.disables, ["sim_test"])

        # Generated script must contain a (list ...) for every corner
        # (assoc-list shape; not (cons k v) which Cadence SKILL rejects
        # for non-list 2nd arg).
        src = Path(script_path).read_text()
        for cname in ("C1", "C2", "C3"):
            self.assertIn(f'(list "{cname}"', src)
        # Value uses netlist-syntax `readns="<path>"` (NOT spectre CLI
        # `+nodeset <path>`) — additionalArgs is appended into the
        # simulatorOptions block in the netlist; live-confirmed by
        # SFE-1994 warnings when we used CLI syntax. See DECISIONS #57.
        # The inner `"` is escaped by the SKILL string quoter, so we
        # match the rendered form `readns=\"...`.
        self.assertIn(r'readns=\"', src)
        self.assertIn("/1/sim_test/netlist/spectre.fc", src)
        self.assertIn("/2/sim_test/netlist/spectre.fc", src)
        self.assertIn("/3/sim_test/netlist/spectre.fc", src)

        # additionalArgs cleared at end (once)
        self.assertEqual(len(bridge.clear_ic_calls), 1)
        self.assertEqual(bridge.clear_ic_calls[0], ("sim_test", "readns", ""))

        pss = next(r for r in report.items if r.item_name == "pss")
        self.assertTrue(pss.completed)
        self.assertEqual(len(pss.run_dirs), 1)  # single batch = 1 run_dir

    def test_upstream_failed_falls_back_to_batch_no_prerun(self):
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.trans_hist)

        # Make the trans item's run raise
        def bomb_first(hist, *, session, **kwargs):
            if bridge.run_n == 0:
                bridge.run_n = 1
                raise RuntimeError("trans crashed")
            return (0, 0, hist)
        bridge.pvt_runner_run = bomb_first

        report = execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # No pre-run activity since upstream had no history
        self.assertEqual(bridge.installs, [])
        self.assertEqual(bridge.disables, [])
        pss = next(r for r in report.items if r.item_name == "pss")
        self.assertIn("no recorded history", pss.notes)
        self.assertTrue(pss.completed)  # ran naked via batch fallback

    def test_partial_ic_only_missing_corner_omitted_from_script(self):
        # Delete corner-2's spectre.fc; the generated script should
        # contain (cons "C1" ...) + (cons "C3" ...) but NOT C2 — at
        # runtime C2's assoc misses → additionalArgs stays untouched →
        # C2 runs naked, other corners still get IC.
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
        self.assertEqual(len(bridge.installs), 1)
        src = Path(bridge.installs[0][1]).read_text()
        self.assertIn('(list "C1"', src)
        self.assertNotIn('(list "C2"', src)
        self.assertIn('(list "C3"', src)
        pss = next(r for r in report.items if r.item_name == "pss")
        self.assertTrue(pss.completed)

    def test_cleanup_runs_even_when_batch_run_errors(self):
        # Pre-run install succeeds, batch run raises. Cleanup (disable
        # pre-run + clear additionalArgs) must still fire in finally.
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.trans_hist)

        def fail_pss(hist, *, session, **kwargs):
            bridge.run_n += 1
            bridge.run_histories.append(hist)
            if bridge.run_n == 1:
                return (0, 0, self.trans_hist)
            raise RuntimeError("pss batch died")
        bridge.pvt_runner_run = fail_pss

        execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        self.assertEqual(len(bridge.installs), 1)
        self.assertEqual(bridge.disables, ["sim_test"])
        self.assertEqual(len(bridge.clear_ic_calls), 1)

    def test_user_prior_pre_run_reattached_on_cleanup(self):
        # If user had their own pre-run script attached, capture +
        # reattach it after our run completes.
        review = self._make_review()
        plan = plan_review(review)
        bridge = self._make_bridge(self.trans_hist)
        bridge.pvt_runner_get_pre_run_script = (
            lambda test, *, session: "/tmp/user_owned.il"
        )

        execute(
            plan, bridge,
            session="fake_session",
            pvtproject_path=self.pvt_path,
            push_union=False,
            ingest_cb=lambda d: None,
        )
        install_paths = [c[1] for c in bridge.installs]
        self.assertEqual(len(install_paths), 2)
        self.assertIn(".simkit", install_paths[0])
        self.assertEqual(install_paths[1], "/tmp/user_owned.il")


# ---------------------------------------------------------------------------
# Phase 3A v1.4: baseline-corner preservation (DECISIONS #59)


def _mk_union(rows: list[UnionRow]) -> Union:
    return Union(
        union_schema_version=1, name="u", project="p", testbench_id="tb",
        rows=tuple(rows),
    )


def _scalar(name: str, vdd: str = "1.0") -> UnionRow:
    return UnionRow(
        row_name=name, vars={"VDD": (vdd,)}, models=(), enabled=True,
    )


def _sweep_vars(name: str) -> UnionRow:
    return UnionRow(
        row_name=name, vars={"VDD": ("1.0", "1.1")}, models=(),
        sweep_var_keys=frozenset({"VDD"}), enabled=True,
    )


def _sweep_models(name: str) -> UnionRow:
    return UnionRow(
        row_name=name,
        vars={"VDD": ("1.0",)},
        models=(ModelEntry(
            file="rf018.scs", block="Global", test="All",
            section=("tt", "ss"),
        ),),
        sweep_model_indices=frozenset({0}),
        enabled=True,
    )


class PickBaselineCornerTests(unittest.TestCase):
    """Pure-function picker (DECISIONS #59 A4 policy)."""

    def test_auto_picks_first_scalar_in_declared_order(self):
        u = _mk_union([_scalar("TT"), _scalar("FF"), _sweep_vars("TT_pvt")])
        self.assertEqual(_pick_baseline_corner(u, None), "TT")

    def test_auto_skips_sweep_rows(self):
        u = _mk_union([_sweep_vars("TT_pvt"), _scalar("TT"), _scalar("FF")])
        # First scalar is TT (the second row); first row is sweep so skipped.
        self.assertEqual(_pick_baseline_corner(u, None), "TT")

    def test_auto_skips_model_sweep_rows(self):
        u = _mk_union([_sweep_models("TT_models"), _scalar("TT_2p5G")])
        self.assertEqual(_pick_baseline_corner(u, None), "TT_2p5G")

    def test_auto_raises_when_table_has_no_scalar(self):
        u = _mk_union([_sweep_vars("TT_pvt"), _sweep_models("TT_models")])
        with self.assertRaises(OrchestratorError) as cm:
            _pick_baseline_corner(u, None)
        self.assertIn("no scalar", str(cm.exception))

    def test_override_returns_named_corner_when_it_exists(self):
        u = _mk_union([_scalar("TT"), _scalar("SS")])
        self.assertEqual(_pick_baseline_corner(u, "SS"), "SS")

    def test_override_raises_when_name_unknown(self):
        u = _mk_union([_scalar("TT")])
        with self.assertRaises(OrchestratorError) as cm:
            _pick_baseline_corner(u, "FF")
        self.assertIn("baseline_corner='FF'", str(cm.exception))
        self.assertIn("TT", str(cm.exception))

    def test_override_permits_sweep_row_when_user_explicit(self):
        # Power-user case: user knows they want the sweep row as baseline
        # for some reason. We honor it (the picker is permissive on
        # explicit override; the auto path is the conservative one).
        u = _mk_union([_scalar("TT"), _sweep_vars("TT_pvt")])
        self.assertEqual(_pick_baseline_corner(u, "TT_pvt"), "TT_pvt")


class ExecuteBaselineCornerTests(unittest.TestCase):
    """Orchestrator wiring: snapshot/pick/restore around the v1.3 chain.

    Pins the v1.4 fix from the user-observed cosmetic bug: when
    `_execute_ic_chained_item` runs with all scalar corners disabled,
    Maestro inserts a `nom` subdir with all-section model includes.
    Fix: snapshot enable state → flip one scalar to enabled → run →
    restore on the way out (DECISIONS #59).
    """

    UNION_DOC = {
        "union_schema_version": 1,
        "name": "u", "project": "myproj", "testbench_id": "tb",
        "rows": [
            {"row_name": "C1", "vars": {"VDD": "1.0"}, "models": []},
        ],
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_v14_"))
        self.pvt_path = self.tmp / "myproj.pvtproject"
        self.pvt_path.write_text('{"schema_version":1,"project":"myproj"}\n')
        self.trans_hist = "fixed_trans_hist"
        d = (self.tmp / "results" / "maestro" / self.trans_hist
             / "1" / "sim_test" / "netlist")
        d.mkdir(parents=True)
        (d / "spectre.fc").write_text("# fake fc\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_review(self, baseline_corner: str | None = None):
        """Two-item review: trans (batch) → pss (ic_from)."""
        union_path = self.tmp / "unions" / "u.union.json"
        union_path.parent.mkdir(exist_ok=True)
        union_path.write_text(json.dumps(self.UNION_DOC))
        pss_item = {
            "name": "pss", "tests": ["sim_test"],
            "union": "unions/u.union.json",
            "ic_from": {"item": "trans", "file": "fc", "mode": "readns"},
        }
        if baseline_corner is not None:
            pss_item["baseline_corner"] = baseline_corner
        doc = {
            "review_schema_version": 2,
            "name": "icfrom", "project": "myproj",
            "items": [
                {"name": "trans", "tests": ["sim_test"],
                 "union": "unions/u.union.json"},
                pss_item,
            ],
        }
        review_path = self.tmp / "icfrom.review.json"
        review_path.write_text(json.dumps(doc))
        return load_review(review_path)

    def _make_bridge(self, table_rows: list[dict], snap_state: list[tuple[str, bool]]):
        """MockBridge with v1.3 + v1.4 surface (corner snapshot/restore + pull).

        ``table_rows`` is what pvt_corners_pull returns as the live union;
        ``snap_state`` is what pvt_runner_snapshot_corners_enable returns.
        """
        trans_hist = self.trans_hist
        # Real SKILL pvtCornersPull writes name = file basename; mirror
        # that here so load_union's basename==name check passes.
        rows_for_pull = table_rows

        class MockBridge:
            def __init__(self):
                self.run_n = 0
                self.run_histories = []
                self.installs = []
                self.disables = []
                self.get_prerun_calls = []
                self.clear_ic_calls = []
                # v1.4 surface
                self.snapshot_calls = 0
                self.restore_calls = []  # list of snaps passed in
                self.pulls = []

            def pvt_runner_snapshot_test_state(self, *, session):
                return [("sim_test", True)]

            def pvt_runner_restore_test_state(self, snap, *, session):
                pass

            def pvt_runner_enable_only(self, names, *, session):
                pass

            def pvt_runner_run(self, hist, *, session, **kwargs):
                self.run_n += 1
                self.run_histories.append(hist)
                if self.run_n == 1:
                    return (0, 0, trans_hist)
                return (0, 0, hist)

            def pvt_save(self, hist, *, pvtproject_path, session):
                return str(Path("/tmp") / f"fake_dump_{hist}")

            def pvt_corners_push(self, path, **kwargs):
                pass

            def pvt_runner_get_pre_run_script(self, test, *, session):
                self.get_prerun_calls.append(test)
                return ""

            def pvt_runner_install_pre_run_script(self, test, path, *, session):
                self.installs.append((test, path))
                return path

            def pvt_runner_disable_pre_run_script(self, test, *, session):
                self.disables.append(test)

            def pvt_runner_clear_ic_source(self, test, mode, prev, *, session):
                self.clear_ic_calls.append((test, mode, prev))

            # v1.4 baseline-corner surface
            def pvt_runner_snapshot_corners_enable(self, *, session):
                self.snapshot_calls += 1
                return list(snap_state)

            def pvt_runner_restore_corners_enable(self, snap, *, session):
                self.restore_calls.append(list(snap))

            def pvt_corners_pull(self, out_path, *, pvtproject_path, session):
                self.pulls.append(str(out_path))
                basename = Path(out_path).name
                # strip ".union.json" suffix to match load_union's expectation
                stem = basename[:-len(".union.json")] if basename.endswith(".union.json") else basename
                doc = {
                    "union_schema_version": 1,
                    "name": stem, "project": "p", "testbench_id": "tb",
                    "rows": rows_for_pull,
                }
                Path(out_path).write_text(json.dumps(doc))
                return str(out_path)

        return MockBridge()

    def test_auto_picks_scalar_and_restores_snapshot(self):
        # Live table: 1 disabled scalar (TT) + 1 enabled sweep (TT_pvt).
        # Auto-picker should flip TT to enabled; restore in finally.
        review = self._make_review()
        plan = plan_review(review)
        snap = [("TT", False), ("TT_pvt", True)]
        rows = [
            {"row_name": "TT", "vars": {"VDD": "1.0"}, "models": []},
            {"row_name": "TT_pvt",
             "vars": {"VDD": ["1.0", "1.1"]}, "models": []},
        ]
        bridge = self._make_bridge(rows, snap)
        execute(
            plan, bridge,
            session="fake_session", pvtproject_path=self.pvt_path,
            push_union=False, ingest_cb=lambda d: None,
        )
        # First restore: target snap with TT flipped to enabled.
        # Second restore: original snap (TT back to disabled).
        self.assertEqual(bridge.snapshot_calls, 1)
        self.assertEqual(len(bridge.restore_calls), 2)
        self.assertEqual(
            sorted(bridge.restore_calls[0]),
            sorted([("TT", True), ("TT_pvt", True)]),
            "first restore enables TT alongside the sweep row",
        )
        self.assertEqual(
            sorted(bridge.restore_calls[1]),
            sorted(snap),
            "finally restore returns to original snapshot",
        )

    def test_explicit_override_beats_auto(self):
        # Two scalars: TT (first) + SS. Auto would pick TT; override
        # says SS, so picker must pick SS.
        review = self._make_review(baseline_corner="SS")
        plan = plan_review(review)
        snap = [("TT", False), ("SS", False), ("TT_pvt", True)]
        rows = [
            {"row_name": "TT", "vars": {"VDD": "1.0"}, "models": []},
            {"row_name": "SS", "vars": {"VDD": "0.9"}, "models": []},
            {"row_name": "TT_pvt",
             "vars": {"VDD": ["1.0", "1.1"]}, "models": []},
        ]
        bridge = self._make_bridge(rows, snap)
        execute(
            plan, bridge,
            session="fake_session", pvtproject_path=self.pvt_path,
            push_union=False, ingest_cb=lambda d: None,
        )
        target = bridge.restore_calls[0]
        self.assertEqual(
            sorted(target),
            sorted([("TT", False), ("SS", True), ("TT_pvt", True)]),
            "override flips SS, not TT",
        )

    def test_picker_failure_propagates_as_orchestrator_error(self):
        # Live table has only sweep rows + no override → hard fail.
        # Existing v1.3 cleanup still runs (try/finally), so pre-run
        # disables fire even though the run was never submitted.
        review = self._make_review()
        plan = plan_review(review)
        snap = [("TT_pvt", True), ("TT_models", True)]
        rows = [
            {"row_name": "TT_pvt",
             "vars": {"VDD": ["1.0", "1.1"]}, "models": []},
            {"row_name": "TT_models",
             "vars": {"VDD": "1.0"}, "models": [
                 {"file": "rf018.scs", "block": "Global", "test": "All",
                  "section": ["tt", "ss"]},
             ]},
        ]
        bridge = self._make_bridge(rows, snap)
        with self.assertRaises(OrchestratorError) as cm:
            execute(
                plan, bridge,
                session="fake_session", pvtproject_path=self.pvt_path,
                push_union=False, ingest_cb=lambda d: None,
            )
        self.assertIn("no scalar", str(cm.exception))
        # Run was NOT submitted for the pss item (only trans's 1 run).
        self.assertEqual(len(bridge.run_histories), 1)
        # Pre-run cleanup still fired (finally block ran).
        self.assertEqual(bridge.disables, ["sim_test"])

    def test_v13_compat_when_bridge_lacks_v14_methods(self):
        # Old MockBridge w/o snapshot/restore/pull methods: v1.4 code
        # catches AttributeError, logs a note, and v1.3 proceeds.
        # This is the safety net so partial bridge upgrades don't
        # break existing pipelines.
        review = self._make_review()
        plan = plan_review(review)
        # Reuse the original ExecuteIcFromTests's bridge (no v1.4 methods).
        helper = ExecuteIcFromTests()
        helper.tmp = self.tmp
        helper.trans_hist = self.trans_hist
        bridge = helper._make_bridge(self.trans_hist)
        report = execute(
            plan, bridge,
            session="fake_session", pvtproject_path=self.pvt_path,
            push_union=False, ingest_cb=lambda d: None,
        )
        pss = next(r for r in report.items if r.item_name == "pss")
        self.assertTrue(pss.completed)
        self.assertIn("baseline-corner setup failed", pss.notes)


# ---------------------------------------------------------------------------
# Phase 3A v1.9 #3 — additionalArgs snap/restore (gap #1 closeout from DECISIONS #68)


class ExecuteIcFromAdditionalArgsSnapRestoreTests(unittest.TestCase):
    """Pin the v1.9 #3 fix: orchestrator captures each test's prior
    ``additionalArgs`` via ``pvt_runner_get_sim_option_val`` before installing
    the per-corner pre-run hook, and restores per-test in the finally
    block instead of unconditionally clearing to "".

    Edge cases:
      * snapshot returns non-empty string → restore loop writes it back
      * snapshot returns None (option unset) → restore loop clears to ""
      * snapshot raises per-test → notes appended, run proceeds, restore = ""
      * bridge lacks ``pvt_runner_get_sim_option_val`` attr → fallback
        path is exactly v1.3 clear-to-"" (back-compat)
      * clear_ic_source is called with the captured value (NOT hardcoded "")
    """

    UNION_DOC = {
        "union_schema_version": 1,
        "name": "u", "project": "myproj", "testbench_id": "tb",
        "rows": [
            {"row_name": "C1", "vars": {"VDD": "1.0"}, "models": []},
        ],
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_v19gap1_"))
        self.pvt_path = self.tmp / "myproj.pvtproject"
        self.pvt_path.write_text('{"schema_version":1,"project":"myproj"}\n')
        self.trans_hist = "fixed_trans_hist"
        d = (self.tmp / "results" / "maestro" / self.trans_hist
             / "1" / "sim_test" / "netlist")
        d.mkdir(parents=True)
        (d / "spectre.fc").write_text("# fake fc\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_review(self):
        union_path = self.tmp / "unions" / "u.union.json"
        union_path.parent.mkdir(exist_ok=True)
        union_path.write_text(json.dumps(self.UNION_DOC))
        doc = {
            "review_schema_version": 2,
            "name": "icfrom", "project": "myproj",
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

    def _make_bridge(self, *, probe_returns=None, probe_raises=False,
                     include_probe=True):
        """Bridge with v1.9 #2 probe surface. probe_returns: dict[test]->val
        or single value (broadcast). probe_raises: True => probe raises."""
        trans_hist = self.trans_hist

        class MockBridge:
            def __init__(self):
                self.run_n = 0
                self.run_histories = []
                self.installs = []
                self.disables = []
                self.clear_ic_calls = []
                self.probe_calls = []

            def pvt_runner_snapshot_test_state(self, *, session):
                return [("sim_test", True)]

            def pvt_runner_restore_test_state(self, snap, *, session):
                pass

            def pvt_runner_enable_only(self, names, *, session):
                pass

            def pvt_runner_run(self, hist, *, session, **kwargs):
                self.run_n += 1
                self.run_histories.append(hist)
                if self.run_n == 1:
                    return (0, 0, trans_hist)
                return (0, 0, hist)

            def pvt_save(self, hist, *, pvtproject_path, session):
                return str(Path("/tmp") / f"fake_dump_{hist}")

            def pvt_corners_push(self, path, **kwargs):
                pass

            def pvt_runner_get_pre_run_script(self, test, *, session):
                return ""

            def pvt_runner_install_pre_run_script(self, test, path, *, session):
                self.installs.append((test, path))
                return path

            def pvt_runner_disable_pre_run_script(self, test, *, session):
                self.disables.append(test)

            def pvt_runner_clear_ic_source(self, test, mode, prev, *, session):
                self.clear_ic_calls.append((test, mode, prev))

        b = MockBridge()
        if include_probe:
            def probe(test, key, *, session):
                b.probe_calls.append((test, key))
                if probe_raises:
                    raise RuntimeError("probe wedge")
                if isinstance(probe_returns, dict):
                    return probe_returns.get(test)
                return probe_returns
            b.pvt_runner_get_sim_option_val = probe
        return b

    def _run(self, bridge):
        review = self._make_review()
        plan = plan_review(review)
        return execute(
            plan, bridge,
            session="fake_session", pvtproject_path=self.pvt_path,
            push_union=False, ingest_cb=lambda d: None,
        )

    def test_snapshot_captures_nonempty_prior_value_and_restores_it(self):
        bridge = self._make_bridge(probe_returns="+ic /tmp/preexisting.ic")
        self._run(bridge)
        # probe called once per test in item.tests = ["sim_test"]
        self.assertIn(("sim_test", "additionalArgs"), bridge.probe_calls)
        # restore writes the captured value back, NOT ""
        self.assertEqual(len(bridge.clear_ic_calls), 1)
        self.assertEqual(
            bridge.clear_ic_calls[0],
            ("sim_test", "readns", "+ic /tmp/preexisting.ic"),
        )

    def test_snapshot_captures_none_translates_to_empty_clear(self):
        # Option was unset (probe returns None). Restore writes "" so the
        # SKILL helper normalises back to "unset" terminal state.
        bridge = self._make_bridge(probe_returns=None)
        self._run(bridge)
        self.assertEqual(bridge.clear_ic_calls[0], ("sim_test", "readns", ""))

    def test_snapshot_raises_records_note_and_falls_back_to_empty(self):
        bridge = self._make_bridge(probe_raises=True)
        report = self._run(bridge)
        pss = next(r for r in report.items if r.item_name == "pss")
        # Per-test note recorded; run not aborted
        self.assertTrue(any("additionalArgs snapshot" in n
                            for n in pss.notes.split("\n")) or
                        "additionalArgs snapshot" in pss.notes)
        self.assertTrue(pss.completed)
        # Restore falls back to ""
        self.assertEqual(bridge.clear_ic_calls[0], ("sim_test", "readns", ""))

    def test_bridge_without_probe_method_uses_v13_fallback(self):
        # Mock bridge without pvt_runner_get_sim_option_val attr — must
        # restore to "" without crashing, and append a fallback note.
        bridge = self._make_bridge(include_probe=False)
        report = self._run(bridge)
        pss = next(r for r in report.items if r.item_name == "pss")
        self.assertEqual(bridge.clear_ic_calls[0], ("sim_test", "readns", ""))
        self.assertIn("lacks pvt_runner_get_sim_option_val", pss.notes)

    def test_clear_ic_source_called_per_test_with_captured_value(self):
        # Pin contract: per-test loop, not hardcoded ""
        bridge = self._make_bridge(probe_returns="readic=\"/old.ic\"")
        self._run(bridge)
        # Exactly one call (single-test item), value matches probe
        self.assertEqual(len(bridge.clear_ic_calls), 1)
        self.assertEqual(bridge.clear_ic_calls[0][2], 'readic="/old.ic"')


class ExecuteIcFromMultiTestSnapRestoreTests(unittest.TestCase):
    """Multi-test item: each test gets its own per-test snapshot + restore."""

    UNION_DOC = {
        "union_schema_version": 1,
        "name": "u", "project": "myproj", "testbench_id": "tb",
        "rows": [
            {"row_name": "C1", "vars": {"VDD": "1.0"}, "models": []},
        ],
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_v19gap1m_"))
        self.pvt_path = self.tmp / "myproj.pvtproject"
        self.pvt_path.write_text('{"schema_version":1,"project":"myproj"}\n')
        self.trans_hist = "fixed_trans_hist"
        # IC resolution uses item.tests[0] = "Test" → /1/Test/netlist/spectre.fc
        d = (self.tmp / "results" / "maestro" / self.trans_hist
             / "1" / "Test" / "netlist")
        d.mkdir(parents=True)
        (d / "spectre.fc").write_text("# fake\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_per_test_restore_with_divergent_prior_values(self):
        union_path = self.tmp / "unions" / "u.union.json"
        union_path.parent.mkdir(exist_ok=True)
        union_path.write_text(json.dumps(self.UNION_DOC))
        doc = {
            "review_schema_version": 2,
            "name": "icfrom", "project": "myproj",
            "items": [
                {"name": "trans", "tests": ["Test"],
                 "union": "unions/u.union.json"},
                {"name": "pss", "tests": ["Test", "Test_trans"],
                 "union": "unions/u.union.json",
                 "ic_from": {"item": "trans", "file": "fc", "mode": "readns"}},
            ],
        }
        review_path = self.tmp / "icfrom.review.json"
        review_path.write_text(json.dumps(doc))
        review = load_review(review_path)
        plan = plan_review(review)
        trans_hist = self.trans_hist
        prior_map = {
            "Test": "+nodeset /tmp/fc",
            "Test_trans": "+ic /tmp/ic",
        }

        class MockBridge:
            def __init__(self):
                self.run_n = 0
                self.installs = []
                self.disables = []
                self.clear_ic_calls = []
                self.probe_calls = []

            def pvt_runner_snapshot_test_state(self, *, session):
                return [("Test", True), ("Test_trans", True)]

            def pvt_runner_restore_test_state(self, snap, *, session):
                pass

            def pvt_runner_enable_only(self, names, *, session):
                pass

            def pvt_runner_run(self, hist, *, session, **kwargs):
                self.run_n += 1
                if self.run_n == 1:
                    return (0, 0, trans_hist)
                return (0, 0, hist)

            def pvt_save(self, hist, *, pvtproject_path, session):
                return str(Path("/tmp") / f"fake_dump_{hist}")

            def pvt_corners_push(self, path, **kwargs):
                pass

            def pvt_runner_get_pre_run_script(self, test, *, session):
                return ""

            def pvt_runner_install_pre_run_script(self, test, path, *, session):
                self.installs.append((test, path))
                return path

            def pvt_runner_disable_pre_run_script(self, test, *, session):
                self.disables.append(test)

            def pvt_runner_clear_ic_source(self, test, mode, prev, *, session):
                self.clear_ic_calls.append((test, mode, prev))

            def pvt_runner_get_sim_option_val(self, test, key, *, session):
                self.probe_calls.append((test, key))
                return prior_map[test]

        bridge = MockBridge()
        execute(
            plan, bridge,
            session="fake_session", pvtproject_path=self.pvt_path,
            push_union=False, ingest_cb=lambda d: None,
        )
        # Each test's prior value got captured and written back
        self.assertEqual(len(bridge.probe_calls), 2)
        self.assertEqual(len(bridge.clear_ic_calls), 2)
        by_test = {c[0]: c[2] for c in bridge.clear_ic_calls}
        self.assertEqual(by_test["Test"], "+nodeset /tmp/fc")
        self.assertEqual(by_test["Test_trans"], "+ic /tmp/ic")


if __name__ == "__main__":
    unittest.main()
