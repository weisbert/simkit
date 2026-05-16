"""Unit tests for ``simkit.list_runs`` (query core)."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.ingest import ingest_run_json  # noqa: E402
from simkit.label import set_run_label  # noqa: E402
from simkit.list_runs import list_runs  # noqa: E402


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_SYN_MIN = _FIXTURES / "synthetic_minimal" / "run.json"
_SYN_ART = _FIXTURES / "synthetic_with_artifacts" / "run.json"
_SYN_MIN_RUN_ID = "11111111-1111-4111-8111-111111111111"
_SYN_ART_RUN_ID = "33333333-3333-4333-8333-333333333333"
_REAL = _FIXTURES / "bdc13f17-d39b-4a13-b58e-846435996a29" / "run.json"
_REAL_RUN_ID = "bdc13f17-d39b-4a13-b58e-846435996a29"


class ListRunsTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_list_"))
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)
        # Two 'synthetic' runs and one 'bridge_smoke' run.
        ingest_run_json(self.con, _SYN_MIN)
        ingest_run_json(self.con, _SYN_ART)
        ingest_run_json(self.con, _REAL)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_all_runs_by_default(self):
        rows = list_runs(self.con)
        ids = {r.run_id for r in rows}
        self.assertEqual(
            ids,
            {_SYN_MIN_RUN_ID, _SYN_ART_RUN_ID, _REAL_RUN_ID},
        )

    def test_ordered_by_timestamp_desc(self):
        rows = list_runs(self.con)
        # All non-empty timestamps; assert descending.
        tss = [r.timestamp for r in rows]
        self.assertEqual(tss, sorted(tss, reverse=True))

    def test_filter_by_project(self):
        rows = list_runs(self.con, project="synthetic")
        ids = {r.run_id for r in rows}
        self.assertEqual(ids, {_SYN_MIN_RUN_ID, _SYN_ART_RUN_ID})

    def test_filter_by_project_no_match(self):
        rows = list_runs(self.con, project="nonexistent")
        self.assertEqual(rows, [])

    def test_slice_only_shows_only_labeled(self):
        # bridge_smoke fixture comes with label='bridge-smoke'.
        rows = list_runs(self.con, slice_only=True)
        self.assertEqual([r.run_id for r in rows], [_REAL_RUN_ID])

    def test_slice_only_excludes_freshly_unlabeled(self):
        set_run_label(
            self.con, run_id=_SYN_MIN_RUN_ID, label="tt-v1",
        )
        rows = list_runs(self.con, slice_only=True)
        ids = {r.run_id for r in rows}
        self.assertEqual(ids, {_REAL_RUN_ID, _SYN_MIN_RUN_ID})

    def test_limit_caps_result(self):
        rows = list_runs(self.con, limit=1)
        self.assertEqual(len(rows), 1)

    def test_to_dict_round_trip(self):
        rows = list_runs(self.con, project="bridge_smoke")
        self.assertEqual(len(rows), 1)
        d = rows[0].to_dict()
        self.assertEqual(d["run_id"], _REAL_RUN_ID)
        self.assertEqual(d["project_id"], "bridge_smoke")
        self.assertEqual(d["label"], "bridge-smoke")
        # netlist_path was null in the fixture (collector soft-miss).
        self.assertIsNone(d["netlist_path"])

    def test_empty_db_returns_empty_list(self):
        empty_db = self.tmp / "empty.duckdb"
        con = connect(empty_db)
        try:
            bootstrap(con)
            rows = list_runs(con)
            self.assertEqual(rows, [])
        finally:
            con.close()


# ----------------------------------------------------------------------
# v1.4 — spec aggregate + --failed-only filter
# ----------------------------------------------------------------------

class SpecAggregateTests(unittest.TestCase):
    """v1.4: list_runs JOINs a spec verdict aggregate (n_pass / n_fail /
    n_has_spec) and accepts a ``failed_only=True`` filter."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_list_v14_"))
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)
        # Pre-v2 fixture; all rows ingest as spec_status='no_spec', so
        # n_has_spec == 0 across the run.
        ingest_run_json(self.con, _SYN_MIN)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ingest_v2(self, run_uuid: str, spec: str, value: float) -> None:
        """Hand-inject a v2 envelope into the DB to exercise the aggregate."""
        import json
        dump = {
            "schema_version": 2,
            "run": {
                "run_id": run_uuid,
                "project_id": "v14_spec",
                "testbench_id": "lib/cell/view",
                "testbench_alias": None,
                "timestamp": "2026-05-16T15:00:00+08:00",
                "author": "tester",
                "label": None, "note": None,
                "netlist_path": "input.scs",
                "history_name": f"v14_spec_{run_uuid[:8]}",
            },
            "results": [{
                "point": 1, "corner": "TT", "test": "Test",
                "output": "X", "value": value, "status": "ok",
                "sweep": {}, "corner_vars": {"temp": "27"},
                "test_note": None,
            }],
            "artifacts": [],
            "output_specs": {"Test": {"X": spec}},
        }
        path = self.tmp / f"run_{run_uuid[:8]}.json"
        path.write_text(json.dumps(dump))
        ingest_run_json(self.con, path)

    # Deterministic UUIDv4-shaped ids for the v1.4 tests.
    _UUID_PASS = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    _UUID_FAIL = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"

    def test_v1_run_has_zero_aggregates(self):
        rows = list_runs(self.con)
        row = next(r for r in rows if r.project_id == "synthetic")
        self.assertEqual(row.n_pass, 0)
        self.assertEqual(row.n_fail, 0)
        self.assertEqual(row.n_has_spec, 0)

    def test_v2_pass_run_aggregate(self):
        # 5e-11 < 1e-10 → pass
        self._ingest_v2(self._UUID_PASS, "< 1e-10", 5e-11)
        rows = list_runs(self.con, project="v14_spec")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].n_pass, 1)
        self.assertEqual(rows[0].n_fail, 0)
        self.assertEqual(rows[0].n_has_spec, 1)

    def test_v2_fail_run_aggregate(self):
        # 2e-10 > 1e-10 → fail
        self._ingest_v2(self._UUID_FAIL, "< 1e-10", 2e-10)
        rows = list_runs(self.con, project="v14_spec")
        self.assertEqual(rows[0].n_pass, 0)
        self.assertEqual(rows[0].n_fail, 1)
        self.assertEqual(rows[0].n_has_spec, 1)

    def test_failed_only_filters_to_runs_with_a_fail(self):
        # Setup: one passing v2 + one failing v2 + one pre-v2 (zero specs).
        self._ingest_v2(self._UUID_PASS, "< 1e-10", 5e-11)
        self._ingest_v2(self._UUID_FAIL, "< 1e-10", 2e-10)
        rows = list_runs(self.con, failed_only=True)
        # Only the failing run should remain.
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0].n_fail, 0)

    def test_failed_only_returns_empty_on_v1_only(self):
        # synthetic_minimal is v1 / no specs → no failures possible.
        rows = list_runs(self.con, failed_only=True)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
