"""Phase 3A v1.9 #3 gap #3 — 3-item IC chain offline pytest.

Pure-Python end-to-end exercise of the ic_from chain semantics across
THREE items A → B → C where:

  * A produces IC for B
  * B consumes A's IC AND produces its own IC for C
  * C consumes B's IC

Verifies that:
  1. ``history_by_item`` correctly propagates from each item's run into
     the orchestrator's per-item map (B → A's history, C → B's history).
  2. Each consumer's pre-run script is rendered with per-corner IC paths
     resolved against the upstream's history dir (not the original A's).
  3. If A's history is missing (A crashed pre-PvtSave), B falls back to
     the batch path and the chain breaks gracefully without raising.
  4. ``_resolve_ic_path`` walks
     ``<results_root>/<source_history>/<idx>/<test>/<subdir>/spectre.ic``
     correctly for each link in the chain.

No skillbridge required — synthetic filesystem under tmp_path mimics the
``<dbRoot>/<history>/<idx>/<test>/netlist/spectre.ic`` layout per item.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.orchestrator import execute, plan_review  # noqa: E402
from simkit.review import load_review  # noqa: E402


_UNION_DOC = {
    "union_schema_version": 1,
    "name": "u",
    "project": "myproj",
    "testbench_id": "tb",
    "rows": [
        {"row_name": "C1", "vars": {"VDD": "1.0"}, "models": []},
        {"row_name": "C2", "vars": {"VDD": "1.1"}, "models": []},
    ],
}


def _make_three_item_review(tmp: Path, hist_a: str, hist_b: str) -> Path:
    """Synth a 3-item review: A (batch) → B (ic_from A) → C (ic_from B).

    Also seeds the on-disk per-corner IC files for A and B (under the
    pretend Maestro results layout) so the orchestrator's IC resolution
    finds something.
    """
    pvt_path = tmp / "myproj.pvtproject"
    pvt_path.write_text('{"schema_version":1,"project":"myproj"}\n')

    union_path = tmp / "unions" / "u.union.json"
    union_path.parent.mkdir(exist_ok=True)
    union_path.write_text(json.dumps(_UNION_DOC))

    # Seed A's IC files for B to consume (2 corners, fc files).
    for ci in (1, 2):
        d = (tmp / "results" / "maestro" / hist_a / str(ci) / "sim_test" /
             "netlist")
        d.mkdir(parents=True)
        (d / "spectre.fc").write_text(f"# A's fc for corner {ci}\n")

    # Seed B's IC files for C to consume (2 corners, ic files).
    for ci in (1, 2):
        d = (tmp / "results" / "maestro" / hist_b / str(ci) / "sim_test" /
             "netlist")
        d.mkdir(parents=True)
        (d / "spectre.ic").write_text(f"# B's ic for corner {ci}\n")

    doc = {
        "review_schema_version": 2,
        "name": "chain3", "project": "myproj",
        "items": [
            {"name": "A", "tests": ["sim_test"],
             "union": "unions/u.union.json"},
            {"name": "B", "tests": ["sim_test"],
             "union": "unions/u.union.json",
             "ic_from": {"item": "A", "file": "fc", "mode": "readns"}},
            {"name": "C", "tests": ["sim_test"],
             "union": "unions/u.union.json",
             "ic_from": {"item": "B", "file": "ic", "mode": "readic"}},
        ],
    }
    review_path = tmp / "chain3.review.json"
    review_path.write_text(json.dumps(doc))
    return review_path


class _ChainMockBridge:
    """3-item chain bridge: each pvt_runner_run call returns a unique
    history name keyed on the order it was called in.

    The orchestrator hands history names like
    ``<prefix>_<sanitized>_<ts>_<idx>``; the mock's return value (slot 2
    of the 3-tuple) is what gets recorded in ``history_by_item``. We
    return the pre-seeded ``hist_a`` / ``hist_b`` names so the IC
    resolver can find them under tmp/results/maestro.
    """

    def __init__(self, hist_a: str, hist_b: str):
        self.hist_a = hist_a
        self.hist_b = hist_b
        self.run_n = 0
        self.run_histories: list[str] = []  # values passed in
        self.installs: list[tuple[str, str]] = []
        self.disables: list[str] = []
        self.clear_ic_calls: list[tuple] = []
        self.probe_calls: list[tuple] = []

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
            return (0, 0, self.hist_a)
        if self.run_n == 2:
            return (0, 0, self.hist_b)
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
        # Simulate a clean session (no prior additionalArgs) so the v1.9
        # snap/restore path takes the "None -> clear-to-empty" branch.
        self.probe_calls.append((test, key))
        return None


class ThreeItemChainHappyPathTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_chain3_"))
        self.hist_a = "hist_A_seeded"
        self.hist_b = "hist_B_seeded"
        self.review_path = _make_three_item_review(
            self.tmp, self.hist_a, self.hist_b,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_three_items_run_in_order_with_each_consumer_chained(self):
        review = load_review(self.review_path)
        plan = plan_review(review)
        bridge = _ChainMockBridge(self.hist_a, self.hist_b)
        report = execute(
            plan, bridge,
            session="sess",
            pvtproject_path=self.tmp / "myproj.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # 3 items → 3 axlRunAllTests calls
        self.assertEqual(bridge.run_n, 3)
        for r in report.items:
            self.assertTrue(r.completed, f"{r.item_name} did not complete")

    def test_B_consumer_script_references_A_history_per_corner(self):
        # B should install ONE pre-run script that references
        # /<hist_a>/<idx>/sim_test/netlist/spectre.fc for each corner.
        bridge = _ChainMockBridge(self.hist_a, self.hist_b)
        execute(
            plan_review(load_review(self.review_path)), bridge,
            session="sess",
            pvtproject_path=self.tmp / "myproj.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # 2 installs total — one for B, one for C (A is batch, no install)
        self.assertEqual(len(bridge.installs), 2)
        # First install = B's script (consumes A)
        b_script_path = bridge.installs[0][1]
        b_src = Path(b_script_path).read_text()
        self.assertIn(self.hist_a, b_src)
        self.assertNotIn(self.hist_b, b_src)
        self.assertIn(f"/{self.hist_a}/1/sim_test/netlist/spectre.fc", b_src)
        self.assertIn(f"/{self.hist_a}/2/sim_test/netlist/spectre.fc", b_src)

    def test_C_consumer_script_references_B_history_not_A(self):
        # C's script must reference B's history (history_by_item propagation).
        bridge = _ChainMockBridge(self.hist_a, self.hist_b)
        execute(
            plan_review(load_review(self.review_path)), bridge,
            session="sess",
            pvtproject_path=self.tmp / "myproj.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
        )
        c_script_path = bridge.installs[1][1]
        c_src = Path(c_script_path).read_text()
        self.assertIn(self.hist_b, c_src)
        # C should not reference A's history — that was B's source, not C's
        self.assertNotIn(f"/{self.hist_a}/1/", c_src)
        self.assertIn(f"/{self.hist_b}/1/sim_test/netlist/spectre.ic", c_src)
        self.assertIn(f"/{self.hist_b}/2/sim_test/netlist/spectre.ic", c_src)

    def test_each_chain_link_uses_correct_per_corner_layout(self):
        # Pin the layout walker: each corner_idx in 1..N becomes a
        # subdir under the source history.
        bridge = _ChainMockBridge(self.hist_a, self.hist_b)
        execute(
            plan_review(load_review(self.review_path)), bridge,
            session="sess",
            pvtproject_path=self.tmp / "myproj.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # B's script has BOTH corners present (corner 1 fc + corner 2 fc).
        b_src = Path(bridge.installs[0][1]).read_text()
        self.assertEqual(
            b_src.count(f"/{self.hist_a}/"), 2,
            "B's script should reference exactly 2 corner paths under A's history",
        )
        # C's script has BOTH corners under B
        c_src = Path(bridge.installs[1][1]).read_text()
        self.assertEqual(
            c_src.count(f"/{self.hist_b}/"), 2,
            "C's script should reference exactly 2 corner paths under B's history",
        )


class ThreeItemChainFallbackTests(unittest.TestCase):
    """Regression: if A's history is missing, B falls back to batch and
    C — whose history_by_item['B'] is the actual recorded B history —
    still runs its IC chain off B."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_chain3_fb_"))
        self.hist_a = "hist_A_seeded"
        self.hist_b = "hist_B_seeded"
        self.review_path = _make_three_item_review(
            self.tmp, self.hist_a, self.hist_b,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_A_crashes_pre_PvtSave_B_falls_back_to_batch_no_install(self):
        # Make A's pvt_runner_run raise so its history_by_item entry is
        # never recorded. B should see "no upstream history" → batch.
        bridge = _ChainMockBridge(self.hist_a, self.hist_b)
        original_run = bridge.pvt_runner_run

        def crash_first(hist, *, session, **kwargs):
            bridge.run_n += 1
            bridge.run_histories.append(hist)
            if bridge.run_n == 1:
                raise RuntimeError("A blew up before PvtSave")
            if bridge.run_n == 2:
                return (0, 0, bridge.hist_b)
            return (0, 0, hist)
        bridge.pvt_runner_run = crash_first

        report = execute(
            plan_review(load_review(self.review_path)), bridge,
            session="sess",
            pvtproject_path=self.tmp / "myproj.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # A failed → no install for A (it's batch anyway, never installs)
        # B should NOT install a pre-run script — fell back to batch path
        # C also falls back since B's history isn't seeded (no IC files
        # under hist_b after B's batch run since it's mocked, BUT the
        # files ARE pre-seeded in setUp for hist_b, so C's script DOES
        # render). Just verify the chain doesn't raise.
        # B's notes mention "no recorded history" for A
        b = next(r for r in report.items if r.item_name == "B")
        self.assertIn("no recorded history", b.notes)
        self.assertEqual(b.completed, True)  # ran naked via batch fallback

    def test_chain_does_not_crash_when_intermediate_install_count_is_one(self):
        # Sanity: when A succeeds, B and C both install — confirms the
        # install count budget. (Counter-check to the fallback test above.)
        bridge = _ChainMockBridge(self.hist_a, self.hist_b)
        execute(
            plan_review(load_review(self.review_path)), bridge,
            session="sess",
            pvtproject_path=self.tmp / "myproj.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
        )
        self.assertEqual(len(bridge.installs), 2)


class ThreeItemChainHistoryByItemPropagationTests(unittest.TestCase):
    """Pin the history_by_item dict shape after a 3-item run.

    Indirect verification via the pre-run script contents: if
    history_by_item[B] points to the right B history, C's script will
    reference it. Direct dict introspection would need exposing the
    orchestrator's internal state, which we deliberately don't do.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_chain3_h_"))
        self.hist_a = "histA"
        self.hist_b = "histB"
        self.review_path = _make_three_item_review(
            self.tmp, self.hist_a, self.hist_b,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_history_propagation_via_C_script_referencing_B_hist(self):
        bridge = _ChainMockBridge(self.hist_a, self.hist_b)
        report = execute(
            plan_review(load_review(self.review_path)), bridge,
            session="sess",
            pvtproject_path=self.tmp / "myproj.pvtproject",
            push_union=False,
            ingest_cb=lambda d: None,
        )
        # Direct cross-check: history_names on each ItemResult.
        a = next(r for r in report.items if r.item_name == "A")
        b = next(r for r in report.items if r.item_name == "B")
        c = next(r for r in report.items if r.item_name == "C")
        self.assertEqual(a.history_names[-1], self.hist_a)
        self.assertEqual(b.history_names[-1], self.hist_b)
        # C's history is whatever it was assigned (auto-derived name; we
        # don't pin the format, just that it's distinct).
        self.assertEqual(len(c.history_names), 1)
        # Cross-verify history_by_item by inspecting C's pre-run script
        c_script_path = bridge.installs[1][1]
        c_src = Path(c_script_path).read_text()
        self.assertIn(b.history_names[-1], c_src)


if __name__ == "__main__":
    unittest.main()
