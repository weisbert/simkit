"""Unit tests for ``simkit.trend`` (resolve_trend_column, compute_trend).

Mirrors ``test_diff.py``'s in-memory scaffolding: build dump dicts,
write ``run.json`` into a temp ``<dbRoot>/runs/<run_id>/``, ingest,
then trend.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.errors import SliceNotFoundError  # noqa: E402
from simkit.ingest import ingest_run_json  # noqa: E402
from simkit.label import set_run_label  # noqa: E402
from simkit.milestone import set_run_milestone  # noqa: E402
from simkit.trend import (  # noqa: E402
    TrendColumn,
    TrendRow,
    compute_trend,
    provenance_consistency,
    resolve_trend_column,
)


_RUN_1 = "11111111-1111-4111-8111-111111111111"
_RUN_2 = "22222222-2222-4222-8222-222222222222"
_RUN_3 = "33333333-3333-4333-8333-333333333333"


_BASE: Dict[str, Any] = {
    "schema_version": 1,
    "run": {
        "run_id": _RUN_1,
        "project_id": "trend_test",
        "testbench_id": "lib/cell/view",
        "testbench_alias": None,
        "timestamp": "2026-05-10T12:00:00+08:00",
        "author": "tester",
        "label": None,
        "note": None,
        "netlist_path": "input.scs",
        "history_name": "h",
    },
    "results": [],
    "artifacts": [],
}


def _row(test, corner, point, output, value, status="ok"):
    return {
        "point": point, "corner": corner, "test": test, "output": output,
        "value": value, "status": status,
        "sweep": {}, "corner_vars": {}, "test_note": None,
    }


class TrendTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_trend_"))
        self.runs_root = self.tmp / "runs"
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ingest(
        self,
        run_id: str,
        rows: List[Dict[str, Any]],
        *,
        timestamp: str = "2026-05-10T12:00:00+08:00",
        label: Optional[str] = None,
        milestone: Optional[str] = None,
        output_specs: Optional[Dict[str, Dict[str, str]]] = None,
    ):
        dump = copy.deepcopy(_BASE)
        dump["run"]["run_id"] = run_id
        dump["run"]["timestamp"] = timestamp
        dump["results"] = rows
        if output_specs is not None:
            dump["schema_version"] = 2
            dump["output_specs"] = output_specs
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "input.scs").write_text("* net\n", encoding="utf-8")
        run_json = run_dir / "run.json"
        run_json.write_text(json.dumps(dump), encoding="utf-8")
        ingest_run_json(self.con, run_json)
        if label is not None:
            set_run_label(self.con, run_id=run_id, label=label)
        if milestone is not None:
            set_run_milestone(self.con, run_id=run_id, milestone=milestone)


class ResolveTrendColumnTests(TrendTestBase):

    def test_resolve_by_run_id_prefix(self):
        self._ingest(_RUN_1, [_row("T", "TT", 1, "gain", 10.0)])
        col = resolve_trend_column(self.con, _RUN_1[:8])
        self.assertEqual(col.run_id, _RUN_1)

    def test_resolve_by_label(self):
        self._ingest(_RUN_1, [_row("T", "TT", 1, "gain", 10.0)], label="goldA")
        col = resolve_trend_column(self.con, "goldA")
        self.assertEqual(col.run_id, _RUN_1)
        self.assertEqual(col.label, "goldA")

    def test_resolve_by_milestone(self):
        self._ingest(_RUN_1, [_row("T", "TT", 1, "gain", 10.0)], milestone="PDR")
        col = resolve_trend_column(self.con, "PDR")
        self.assertEqual(col.run_id, _RUN_1)
        self.assertEqual(col.milestone, "PDR")

    def test_milestone_resolves_to_latest_run_not_ambiguous(self):
        # Two runs share a milestone — newest wins instead of raising.
        self._ingest(
            _RUN_1, [_row("T", "TT", 1, "gain", 10.0)],
            timestamp="2026-05-01T00:00:00+08:00", milestone="CDR",
        )
        self._ingest(
            _RUN_2, [_row("T", "TT", 1, "gain", 12.0)],
            timestamp="2026-05-09T00:00:00+08:00", milestone="CDR",
        )
        col = resolve_trend_column(self.con, "CDR")
        self.assertEqual(col.run_id, _RUN_2)

    def test_unknown_identifier_raises(self):
        self._ingest(_RUN_1, [_row("T", "TT", 1, "gain", 10.0)])
        with self.assertRaises(SliceNotFoundError):
            resolve_trend_column(self.con, "no-such-thing")

    def test_empty_identifier_raises(self):
        with self.assertRaises(SliceNotFoundError):
            resolve_trend_column(self.con, "")


class ComputeTrendTests(TrendTestBase):

    def test_three_way_alignment_column_order_preserved(self):
        self._ingest(
            _RUN_1, [_row("T", "TT", 1, "gain", 10.0)],
            timestamp="2026-05-01T00:00:00+08:00", milestone="PDR",
        )
        self._ingest(
            _RUN_2, [_row("T", "TT", 1, "gain", 11.0)],
            timestamp="2026-05-05T00:00:00+08:00", milestone="CDR",
        )
        self._ingest(
            _RUN_3, [_row("T", "TT", 1, "gain", 12.0)],
            timestamp="2026-05-09T00:00:00+08:00", milestone="FDR",
        )
        result = compute_trend(self.con, slices=["PDR", "CDR", "FDR"])
        self.assertEqual(
            [c.milestone for c in result.columns], ["PDR", "CDR", "FDR"],
        )
        self.assertEqual(len(result.rows), 1)
        row = result.rows[0]
        self.assertEqual([c.value for c in row.cells], [10.0, 11.0, 12.0])
        self.assertEqual(row.direction, "up")
        self.assertTrue(row.varies)

    def test_missing_key_in_one_slice_marked_absent(self):
        self._ingest(
            _RUN_1, [_row("T", "TT", 1, "gain", 10.0)],
            timestamp="2026-05-01T00:00:00+08:00",
        )
        self._ingest(
            _RUN_2,
            [_row("T", "TT", 1, "gain", 11.0), _row("T", "TT", 1, "nf", 3.0)],
            timestamp="2026-05-05T00:00:00+08:00",
        )
        result = compute_trend(self.con, slices=[_RUN_1[:8], _RUN_2[:8]])
        by_output = {r.output: r for r in result.rows}
        self.assertFalse(by_output["nf"].cells[0].present)
        self.assertTrue(by_output["nf"].cells[1].present)

    def test_direction_down_and_mixed(self):
        self._ingest(
            _RUN_1,
            [_row("T", "TT", 1, "a", 10.0), _row("T", "TT", 1, "b", 5.0)],
            timestamp="2026-05-01T00:00:00+08:00",
        )
        self._ingest(
            _RUN_2,
            [_row("T", "TT", 1, "a", 8.0), _row("T", "TT", 1, "b", 9.0)],
            timestamp="2026-05-05T00:00:00+08:00",
        )
        self._ingest(
            _RUN_3,
            [_row("T", "TT", 1, "a", 6.0), _row("T", "TT", 1, "b", 4.0)],
            timestamp="2026-05-09T00:00:00+08:00",
        )
        result = compute_trend(
            self.con, slices=[_RUN_1[:8], _RUN_2[:8], _RUN_3[:8]],
        )
        by_output = {r.output: r for r in result.rows}
        self.assertEqual(by_output["a"].direction, "down")
        self.assertEqual(by_output["b"].direction, "mixed")

    def test_varies_false_when_all_equal(self):
        self._ingest(
            _RUN_1, [_row("T", "TT", 1, "gain", 10.0)],
            timestamp="2026-05-01T00:00:00+08:00",
        )
        self._ingest(
            _RUN_2, [_row("T", "TT", 1, "gain", 10.0)],
            timestamp="2026-05-05T00:00:00+08:00",
        )
        result = compute_trend(self.con, slices=[_RUN_1[:8], _RUN_2[:8]])
        self.assertFalse(result.rows[0].varies)
        self.assertEqual(result.rows[0].direction, "flat")

    def test_fewer_than_two_slices_raises(self):
        self._ingest(_RUN_1, [_row("T", "TT", 1, "gain", 10.0)])
        with self.assertRaises(ValueError):
            compute_trend(self.con, slices=[_RUN_1[:8]])

    def test_to_dict_round_trips(self):
        self._ingest(
            _RUN_1, [_row("T", "TT", 1, "gain", 10.0)],
            timestamp="2026-05-01T00:00:00+08:00",
        )
        self._ingest(
            _RUN_2, [_row("T", "TT", 1, "gain", 12.0)],
            timestamp="2026-05-05T00:00:00+08:00",
        )
        result = compute_trend(self.con, slices=[_RUN_1[:8], _RUN_2[:8]])
        d = result.to_dict()
        self.assertEqual(len(d["columns"]), 2)
        self.assertEqual(d["rows"][0]["direction"], "up")
        # JSON-serialisable end to end.
        json.dumps(d)

    def test_sentinel_rows_flagged(self):
        self._ingest(
            _RUN_1,
            [_row("T", "SS", 1, "__sim_status__", None, status="failed"),
             _row("T", "TT", 1, "gain", 10.0)],
            timestamp="2026-05-01T00:00:00+08:00",
        )
        self._ingest(
            _RUN_2,
            [_row("T", "SS", 1, "__sim_status__", None, status="failed"),
             _row("T", "TT", 1, "gain", 11.0)],
            timestamp="2026-05-05T00:00:00+08:00",
        )
        result = compute_trend(self.con, slices=[_RUN_1[:8], _RUN_2[:8]])
        sentinels = [r for r in result.rows if r.is_sentinel]
        self.assertEqual(len(sentinels), 1)
        self.assertEqual(sentinels[0].output, "__sim_status__")


class ComputeTrendProvenanceTests(TrendTestBase):
    """G-5 — compute_trend reads runs.provenance into each TrendColumn."""

    def test_column_carries_provenance(self):
        self._ingest(
            _RUN_1, [_row("T", "TT", 1, "g", 10.0)],
            timestamp="2026-05-01T00:00:00+08:00",
        )
        self._ingest(
            _RUN_2, [_row("T", "TT", 1, "g", 11.0)],
            timestamp="2026-05-05T00:00:00+08:00",
        )
        self.con.execute(
            "UPDATE runs SET provenance = ? WHERE run_id = ?",
            ['{"host": "farm-1"}', _RUN_1],
        )
        result = compute_trend(self.con, slices=[_RUN_1[:8], _RUN_2[:8]])
        self.assertEqual(result.columns[0].provenance, {"host": "farm-1"})
        self.assertIsNone(result.columns[1].provenance)


def _tc(display, provenance=None):
    return TrendColumn(
        identifier=display, run_id=display + "-id", label=display,
        milestone=None, timestamp="2026-05-01 00:00:00+08",
        provenance=provenance,
    )


class ProvenanceConsistencyTests(unittest.TestCase):
    """G-5 — provenance_consistency flags cross-run condition mismatch."""

    def test_all_consistent_returns_empty(self):
        prov = {"host": "h", "pdk_version": "v1", "model_files": []}
        cols = (_tc("PDR", prov), _tc("CDR", dict(prov)))
        self.assertEqual(provenance_consistency(cols), [])

    def test_host_mismatch_flagged(self):
        cols = (
            _tc("PDR", {"host": "h1", "model_files": []}),
            _tc("CDR", {"host": "h2", "model_files": []}),
        )
        diffs = provenance_consistency(cols)
        self.assertEqual(len(diffs), 1)
        self.assertIn("PDR", diffs[0])
        self.assertIn("CDR", diffs[0])

    def test_missing_provenance_flagged_as_unprovable(self):
        cols = (_tc("PDR", {"host": "h", "model_files": []}), _tc("CDR", None))
        diffs = provenance_consistency(cols)
        self.assertTrue(any("没有 provenance" in d for d in diffs))

    def test_single_column_returns_empty(self):
        self.assertEqual(
            provenance_consistency((_tc("PDR", {"host": "h"}),)), [],
        )


_ABSENT = object()


class TrendRowDirectionUnitTests(unittest.TestCase):
    """Direction / varies logic without a DB."""

    def _row_with(self, values):
        from simkit.trend import TrendCell
        cells = tuple(
            TrendCell(present=(v is not _ABSENT), value=(None if v is _ABSENT else v),
                      status=None, spec_status=None)
            for v in values
        )
        return TrendRow(test="t", corner="c", point=1, output="o",
                        cells=cells, is_sentinel=False)

    def test_single_numeric_has_no_direction(self):
        self.assertIsNone(self._row_with([5.0, _ABSENT]).direction)

    def test_strings_have_no_direction_but_can_vary(self):
        row = self._row_with(["a", "b"])
        self.assertIsNone(row.direction)
        self.assertTrue(row.varies)

    def test_booleans_excluded_from_direction(self):
        # bools must not be treated as 0/1 numeric trend.
        self.assertIsNone(self._row_with([True, False]).direction)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
