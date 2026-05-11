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


if __name__ == "__main__":
    unittest.main()
