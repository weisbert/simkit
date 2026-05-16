"""Unit tests for ``simkit.diff`` (resolve_slice, compute_*_diff, compute_diff).

Each test constructs the slices in-memory: build dump dicts, write
``run.json`` files into a temp ``<dbRoot>/runs/<run_id>/``, ingest, then
diff. Avoids fixture coupling so failure modes are explicit.
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
from simkit.diff import (  # noqa: E402
    compute_diff,
    compute_netlist_diff,
    compute_results_diff,
    resolve_slice,
)
from simkit.errors import (  # noqa: E402
    AmbiguousSliceError,
    SliceNotFoundError,
)
from simkit.ingest import ingest_run_json  # noqa: E402
from simkit.label import set_run_label  # noqa: E402


_RUN_A_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_RUN_B_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

_BASE_DUMP: Dict[str, Any] = {
    "schema_version": 1,
    "run": {
        "run_id": _RUN_A_ID,
        "project_id": "diff_test",
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
        "point": point,
        "corner": corner,
        "test": test,
        "output": output,
        "value": value,
        "status": status,
        "sweep": {},
        "corner_vars": {},
        "test_note": None,
    }


class DiffTestBase(unittest.TestCase):
    """Shared scaffolding: temp dbRoot, ingest helper, slice-builder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_diff_"))
        self.runs_root = self.tmp / "runs"
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ingest_dump(
        self,
        run_id: str,
        rows: List[Dict[str, Any]],
        *,
        netlist_text: Optional[str] = "* netlist v1\n",
        timestamp: str = "2026-05-10T12:00:00+08:00",
        label: Optional[str] = None,
        output_specs: Optional[Dict[str, Dict[str, str]]] = None,
    ):
        dump = copy.deepcopy(_BASE_DUMP)
        dump["run"]["run_id"] = run_id
        dump["run"]["timestamp"] = timestamp
        dump["results"] = rows
        if netlist_text is None:
            dump["run"]["netlist_path"] = None
        # v1.4 — if output_specs is provided, lift the dump to schema_version=2.
        if output_specs is not None:
            dump["schema_version"] = 2
            dump["output_specs"] = output_specs
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if netlist_text is not None:
            (run_dir / "input.scs").write_text(netlist_text, encoding="utf-8")
        run_json = run_dir / "run.json"
        run_json.write_text(json.dumps(dump), encoding="utf-8")
        ingest_run_json(self.con, run_json)
        if label is not None:
            set_run_label(self.con, run_id=run_id, label=label)


class ResolveSliceTests(DiffTestBase):

    def test_resolve_by_label(self):
        self._ingest_dump(_RUN_A_ID, [], label="golden")
        self.assertEqual(resolve_slice(self.con, "golden"), _RUN_A_ID)

    def test_resolve_by_run_id_prefix(self):
        self._ingest_dump(_RUN_A_ID, [])
        self.assertEqual(
            resolve_slice(self.con, _RUN_A_ID[:8]), _RUN_A_ID,
        )

    def test_resolve_by_full_run_id(self):
        self._ingest_dump(_RUN_A_ID, [])
        self.assertEqual(
            resolve_slice(self.con, _RUN_A_ID), _RUN_A_ID,
        )

    def test_label_takes_priority_over_prefix(self):
        # Two runs both ingested. The first is labeled 'cccccccc' which is
        # also the prefix of the second run's id — label match must win.
        self._ingest_dump(_RUN_A_ID, [], label="cccccccc")
        self._ingest_dump(
            "cccccccc-cccc-4ccc-8ccc-cccccccccccc", [],
        )
        self.assertEqual(resolve_slice(self.con, "cccccccc"), _RUN_A_ID)

    def test_ambiguous_prefix(self):
        self._ingest_dump("12345678-1111-4111-8111-111111111111", [])
        self._ingest_dump("12345679-2222-4222-8222-222222222222", [])
        with self.assertRaises(AmbiguousSliceError):
            resolve_slice(self.con, "1234567")

    def test_not_found(self):
        self._ingest_dump(_RUN_A_ID, [])
        with self.assertRaises(SliceNotFoundError):
            resolve_slice(self.con, "no-such-thing")

    def test_empty_identifier_rejected(self):
        with self.assertRaises(SliceNotFoundError):
            resolve_slice(self.con, "")


class ResultsDiffTests(DiffTestBase):

    def test_identical_slices_all_match(self):
        rows = [_row("T", "TT", 0, "v", 1.0)]
        self._ingest_dump(_RUN_A_ID, rows)
        self._ingest_dump(_RUN_B_ID, rows)
        drows = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(len(drows), 1)
        self.assertEqual(drows[0].kind, "match")
        self.assertEqual(drows[0].abs_delta, 0.0)
        self.assertEqual(drows[0].rel_delta, 0.0)

    def test_numeric_delta_positive(self):
        self._ingest_dump(_RUN_A_ID, [_row("T", "TT", 0, "v", 100.0)])
        self._ingest_dump(_RUN_B_ID, [_row("T", "TT", 0, "v", 110.0)])
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(d[0].abs_delta, 10.0)
        self.assertAlmostEqual(d[0].rel_delta, 0.10)
        self.assertEqual(d[0].kind, "match")

    def test_numeric_delta_negative(self):
        self._ingest_dump(_RUN_A_ID, [_row("T", "TT", 0, "v", 100.0)])
        self._ingest_dump(_RUN_B_ID, [_row("T", "TT", 0, "v", 90.0)])
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(d[0].abs_delta, -10.0)
        self.assertAlmostEqual(d[0].rel_delta, -0.10)

    def test_rel_delta_undefined_when_a_is_zero(self):
        self._ingest_dump(_RUN_A_ID, [_row("T", "TT", 0, "v", 0.0)])
        self._ingest_dump(_RUN_B_ID, [_row("T", "TT", 0, "v", 1.0)])
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(d[0].abs_delta, 1.0)
        self.assertIsNone(d[0].rel_delta)

    def test_only_a_row(self):
        self._ingest_dump(_RUN_A_ID, [_row("T", "TT", 0, "v", 1.0)])
        self._ingest_dump(_RUN_B_ID, [])
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0].kind, "only_a")
        self.assertEqual(d[0].value_a, 1.0)
        self.assertIsNone(d[0].value_b)
        self.assertIsNone(d[0].abs_delta)

    def test_only_b_row(self):
        self._ingest_dump(_RUN_A_ID, [])
        self._ingest_dump(_RUN_B_ID, [_row("T", "TT", 0, "v", 1.0)])
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(d[0].kind, "only_b")
        self.assertIsNone(d[0].value_a)
        self.assertEqual(d[0].value_b, 1.0)

    def test_status_mismatch_marked(self):
        # Slice A: ok measurement.
        # Slice B: same measurement now failed + sentinel (to satisfy I1).
        self._ingest_dump(
            _RUN_A_ID, [_row("T", "TT", 0, "v", 1.0, status="ok")],
        )
        self._ingest_dump(
            _RUN_B_ID, [
                _row("T", "TT", 0, "v", None, status="failed"),
                _row("T", "TT", 0, "__sim_status__", None, status="failed"),
            ],
        )
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        # 'v' is the row of interest.
        v_row = next(r for r in d if r.output == "v")
        self.assertEqual(v_row.kind, "status_mismatch")
        self.assertEqual(v_row.status_a, "ok")
        self.assertEqual(v_row.status_b, "failed")

    def test_sentinel_row_flag(self):
        self._ingest_dump(_RUN_A_ID, [
            _row("T", "TT", 0, "__sim_status__", None, status="failed"),
        ])
        self._ingest_dump(_RUN_B_ID, [
            _row("T", "TT", 0, "__sim_status__", None, status="failed"),
        ])
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(len(d), 1)
        self.assertTrue(d[0].is_sentinel)
        self.assertEqual(d[0].kind, "match")

    def test_rows_sorted_deterministically(self):
        # Insert rows in shuffled order; diff must return them sorted by
        # (test, corner, point, output).
        rows_a = [
            _row("Z", "FF", 0, "v", 1.0),
            _row("A", "TT", 1, "v", 1.0),
            _row("A", "TT", 0, "v", 1.0),
        ]
        self._ingest_dump(_RUN_A_ID, rows_a)
        self._ingest_dump(_RUN_B_ID, rows_a)
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        keys = [(r.test, r.corner, r.point, r.output) for r in d]
        self.assertEqual(keys, sorted(keys))

    # v1.4 — spec_status flip detection
    def test_spec_status_carried_per_slice(self):
        # Same numeric value; same spec; both slices pass → spec_changed False.
        self._ingest_dump(
            _RUN_A_ID, [_row("T", "TT", 0, "v", 50.0)],
            output_specs={"T": {"v": "< 100"}},
        )
        self._ingest_dump(
            _RUN_B_ID, [_row("T", "TT", 0, "v", 50.0)],
            output_specs={"T": {"v": "< 100"}},
        )
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(d[0].spec_status_a, "pass")
        self.assertEqual(d[0].spec_status_b, "pass")
        self.assertFalse(d[0].spec_changed)

    def test_spec_changed_true_when_verdict_flips(self):
        # Value drifts; same spec; pass→fail.
        self._ingest_dump(
            _RUN_A_ID, [_row("T", "TT", 0, "v", 50.0)],
            output_specs={"T": {"v": "< 100"}},
        )
        self._ingest_dump(
            _RUN_B_ID, [_row("T", "TT", 0, "v", 150.0)],
            output_specs={"T": {"v": "< 100"}},
        )
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertEqual(d[0].spec_status_a, "pass")
        self.assertEqual(d[0].spec_status_b, "fail")
        self.assertTrue(d[0].spec_changed)

    def test_spec_changed_false_when_one_side_missing(self):
        # v1 slice + v2 slice — verdict undefined on the v1 side.
        # spec_changed must NOT report a flip just because one side has
        # spec_status = None.
        self._ingest_dump(_RUN_A_ID, [_row("T", "TT", 0, "v", 50.0)])
        self._ingest_dump(
            _RUN_B_ID, [_row("T", "TT", 0, "v", 50.0)],
            output_specs={"T": {"v": "< 100"}},
        )
        d = compute_results_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
        )
        self.assertIsNone(d[0].spec_status_a)
        self.assertEqual(d[0].spec_status_b, "pass")
        self.assertFalse(d[0].spec_changed)


class NetlistDiffTests(DiffTestBase):

    def test_identical_netlists(self):
        self._ingest_dump(_RUN_A_ID, [], netlist_text="line1\nline2\n")
        self._ingest_dump(_RUN_B_ID, [], netlist_text="line1\nline2\n")
        nd = compute_netlist_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
            runs_root=self.runs_root,
        )
        self.assertEqual(nd.diff_text, "")  # empty = identical
        self.assertIsNone(nd.note)

    def test_different_netlists_unified_diff(self):
        self._ingest_dump(_RUN_A_ID, [], netlist_text="line1\nline2\n")
        self._ingest_dump(_RUN_B_ID, [], netlist_text="line1\nline2-edited\n")
        nd = compute_netlist_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
            runs_root=self.runs_root,
        )
        self.assertIsNotNone(nd.diff_text)
        self.assertIn("-line2", nd.diff_text)
        self.assertIn("+line2-edited", nd.diff_text)

    def test_both_null_netlist(self):
        self._ingest_dump(_RUN_A_ID, [], netlist_text=None)
        self._ingest_dump(_RUN_B_ID, [], netlist_text=None)
        nd = compute_netlist_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
            runs_root=self.runs_root,
        )
        self.assertIsNone(nd.diff_text)
        self.assertIn("both", nd.note)

    def test_one_null_netlist(self):
        self._ingest_dump(_RUN_A_ID, [], netlist_text=None)
        self._ingest_dump(_RUN_B_ID, [], netlist_text="line\n")
        nd = compute_netlist_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
            runs_root=self.runs_root,
        )
        self.assertIsNone(nd.diff_text)
        self.assertIn("slice_a has null", nd.note)

    def test_missing_file_on_disk(self):
        self._ingest_dump(_RUN_A_ID, [], netlist_text="line\n")
        self._ingest_dump(_RUN_B_ID, [], netlist_text="line\n")
        # Remove slice_b's netlist from disk after ingest.
        (self.runs_root / _RUN_B_ID / "input.scs").unlink()
        nd = compute_netlist_diff(
            self.con,
            slice_a_run_id=_RUN_A_ID, slice_b_run_id=_RUN_B_ID,
            runs_root=self.runs_root,
        )
        self.assertIsNone(nd.diff_text)
        self.assertIn("missing on disk", nd.note)


class ComputeDiffEndToEndTests(DiffTestBase):

    def test_e2e_with_label_resolution(self):
        rows_a = [_row("T", "TT", 0, "v", 1.0)]
        rows_b = [_row("T", "TT", 0, "v", 1.1)]
        self._ingest_dump(
            _RUN_A_ID, rows_a, label="golden",
            netlist_text="line\n",
        )
        self._ingest_dump(
            _RUN_B_ID, rows_b, label="candidate",
            netlist_text="line2\n",
        )
        result = compute_diff(
            self.con,
            slice_a="golden", slice_b="candidate",
            runs_root=self.runs_root,
        )
        self.assertEqual(result.slice_a_run_id, _RUN_A_ID)
        self.assertEqual(result.slice_b_run_id, _RUN_B_ID)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].kind, "match")
        self.assertAlmostEqual(result.rows[0].rel_delta, 0.10)
        self.assertIsNotNone(result.netlist.diff_text)


if __name__ == "__main__":
    unittest.main()
