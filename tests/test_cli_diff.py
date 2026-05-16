"""Unit tests for ``pvt diff`` CLI (simkit.cli.diff)."""

from __future__ import annotations

import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.cli.__main__ import main as cli_main  # noqa: E402
from simkit.db import bootstrap, connect  # noqa: E402
from simkit.ingest import ingest_run_json  # noqa: E402
from simkit.label import set_run_label  # noqa: E402


_RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_RUN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


_BASE: Dict[str, Any] = {
    "schema_version": 1,
    "run": {
        "run_id": _RUN_A,
        "project_id": "diff_cli",
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


def _run(*args: str) -> tuple:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


class CliDiffBase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_diff_"))
        self.runs_root = self.tmp / "runs"
        self.db = self.tmp / "simkit.duckdb"
        # Bootstrap via a transient connection so nothing holds the file
        # lock when the CLI opens it next.
        con = connect(self.db)
        try:
            bootstrap(con)
        finally:
            con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ingest(
        self,
        run_id: str,
        rows: List[Dict[str, Any]],
        *,
        netlist_text: Optional[str] = "net v1\n",
        label: Optional[str] = None,
        output_specs: Optional[Dict[str, Dict[str, str]]] = None,
    ):
        dump = copy.deepcopy(_BASE)
        dump["run"]["run_id"] = run_id
        dump["results"] = rows
        if netlist_text is None:
            dump["run"]["netlist_path"] = None
        if output_specs is not None:
            dump["schema_version"] = 2
            dump["output_specs"] = output_specs
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if netlist_text is not None:
            (run_dir / "input.scs").write_text(netlist_text)
        run_json = run_dir / "run.json"
        run_json.write_text(json.dumps(dump))
        con = connect(self.db)
        try:
            ingest_run_json(con, run_json)
            if label is not None:
                set_run_label(con, run_id=run_id, label=label)
        finally:
            con.close()


class CliDiffTableTests(CliDiffBase):

    def test_default_table_shows_changed_row(self):
        self._ingest(_RUN_A, [_row("T", "TT", 0, "v", 1.0)], label="a")
        self._ingest(_RUN_B, [_row("T", "TT", 0, "v", 1.1)], label="b")
        rc, out, err = _run(
            "diff", "a", "b", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        # Header present.
        self.assertIn("test", out)
        self.assertIn("dAbs", out)
        # Row data present.
        self.assertIn("TT", out)
        # Both run_ids in the header comment.
        self.assertIn(_RUN_A, out)
        self.assertIn(_RUN_B, out)

    def test_threshold_hides_small_match(self):
        # 1% delta, threshold 5% → row hidden.
        self._ingest(_RUN_A, [_row("T", "TT", 0, "v", 100.0)], label="a")
        self._ingest(_RUN_B, [_row("T", "TT", 0, "v", 101.0)], label="b")
        rc, out, _err = _run(
            "diff", "a", "b", "--threshold", "0.05",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0)
        self.assertIn("(no rows)", out)
        self.assertIn("hidden by --threshold", out)

    def test_threshold_does_not_hide_only_a(self):
        self._ingest(_RUN_A, [_row("T", "TT", 0, "v", 1.0)], label="a")
        self._ingest(_RUN_B, [], label="b")
        rc, out, _err = _run(
            "diff", "a", "b", "--threshold", "0.5",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0)
        self.assertIn("only in slice_a", out)

    def test_sentinel_hidden_by_default(self):
        self._ingest(_RUN_A, [
            _row("T", "TT", 0, "__sim_status__", None, status="failed"),
        ], label="a")
        self._ingest(_RUN_B, [
            _row("T", "TT", 0, "__sim_status__", None, status="failed"),
        ], label="b")
        rc, out, _err = _run(
            "diff", "a", "b", "--db", str(self.db),
        )
        self.assertEqual(rc, 0)
        # Data area should be empty (only the meta note may mention the
        # sentinel name).
        self.assertIn("(no rows)", out)
        self.assertIn("__sim_status__ rows hidden", out)

    def test_include_status_shows_sentinel(self):
        self._ingest(_RUN_A, [
            _row("T", "TT", 0, "__sim_status__", None, status="failed"),
        ], label="a")
        self._ingest(_RUN_B, [
            _row("T", "TT", 0, "__sim_status__", None, status="failed"),
        ], label="b")
        rc, out, _err = _run(
            "diff", "a", "b", "--include-status",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0)
        self.assertIn("__sim_status__", out)

    def test_netlist_section_renders_diff(self):
        self._ingest(_RUN_A, [], label="a", netlist_text="line1\n")
        self._ingest(_RUN_B, [], label="b", netlist_text="line2\n")
        rc, out, _err = _run(
            "diff", "a", "b", "--db", str(self.db),
        )
        self.assertEqual(rc, 0)
        self.assertIn("-line1", out)
        self.assertIn("+line2", out)

    def test_netlist_identical_note(self):
        self._ingest(_RUN_A, [], label="a", netlist_text="same\n")
        self._ingest(_RUN_B, [], label="b", netlist_text="same\n")
        rc, out, _err = _run(
            "diff", "a", "b", "--db", str(self.db),
        )
        self.assertEqual(rc, 0)
        self.assertIn("files identical", out)

    # v1.4 — --spec-changes filter
    def test_spec_changes_shows_only_flipped_rows(self):
        # Two rows: one whose verdict flips (pass→fail), one whose verdict
        # stays pass. --spec-changes should hide the unchanged one and
        # surface only the flipped row.
        self._ingest(
            _RUN_A, [
                _row("T", "TT", 0, "v_flip", 50.0),
                _row("T", "TT", 0, "v_stay", 50.0),
            ],
            label="a",
            output_specs={"T": {"v_flip": "< 100", "v_stay": "< 100"}},
        )
        self._ingest(
            _RUN_B, [
                _row("T", "TT", 0, "v_flip", 150.0),  # now fails
                _row("T", "TT", 0, "v_stay", 50.0),
            ],
            label="b",
            output_specs={"T": {"v_flip": "< 100", "v_stay": "< 100"}},
        )
        rc, out, err = _run(
            "diff", "a", "b", "--spec-changes", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("v_flip", out)
        self.assertNotIn("v_stay", out)
        self.assertIn("--spec-changes", out)
        # spec column header is included
        self.assertIn("spec_a", out)
        self.assertIn("spec_b", out)

    def test_spec_changes_empty_when_no_flips(self):
        self._ingest(
            _RUN_A, [_row("T", "TT", 0, "v", 50.0)], label="a",
            output_specs={"T": {"v": "< 100"}},
        )
        self._ingest(
            _RUN_B, [_row("T", "TT", 0, "v", 60.0)], label="b",
            output_specs={"T": {"v": "< 100"}},
        )
        rc, out, _err = _run(
            "diff", "a", "b", "--spec-changes", "--db", str(self.db),
        )
        self.assertEqual(rc, 0)
        self.assertIn("(no rows)", out)


class CliDiffJsonTests(CliDiffBase):

    def test_json_carries_full_structure(self):
        self._ingest(_RUN_A, [_row("T", "TT", 0, "v", 1.0)], label="a")
        self._ingest(_RUN_B, [_row("T", "TT", 0, "v", 1.1)], label="b")
        rc, out, err = _run(
            "diff", "a", "b", "--json", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(data["slice_a"]["run_id"], _RUN_A)
        self.assertEqual(data["slice_b"]["run_id"], _RUN_B)
        self.assertEqual(len(data["results"]), 1)
        row = data["results"][0]
        self.assertEqual(row["kind"], "match")
        self.assertAlmostEqual(row["rel_delta"], 0.10)
        self.assertIn("diff_text", data["netlist"])

    def test_json_carries_sentinel_rows(self):
        # JSON output ignores --include-status; everything is emitted.
        self._ingest(_RUN_A, [
            _row("T", "TT", 0, "__sim_status__", None, status="failed"),
        ], label="a")
        self._ingest(_RUN_B, [
            _row("T", "TT", 0, "__sim_status__", None, status="failed"),
        ], label="b")
        rc, out, _err = _run(
            "diff", "a", "b", "--json", "--db", str(self.db),
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(len(data["results"]), 1)
        self.assertTrue(data["results"][0]["is_sentinel"])


class CliDiffErrorTests(CliDiffBase):

    def test_slice_not_found_exits_1(self):
        self._ingest(_RUN_A, [], label="a")
        rc, _out, err = _run(
            "diff", "nonexistent", "a", "--db", str(self.db),
        )
        self.assertEqual(rc, 1)
        self.assertIn("no run matches", err)

    def test_ambiguous_prefix_exits_1(self):
        self._ingest(
            "12345678-1111-4111-8111-111111111111", [], label=None,
        )
        self._ingest(
            "12345679-2222-4222-8222-222222222222", [], label="b",
        )
        rc, _out, err = _run(
            "diff", "1234567", "b", "--db", str(self.db),
        )
        self.assertEqual(rc, 1)
        self.assertIn("matches 2 runs", err)

    def test_db_missing_exits_3(self):
        rc, _out, err = _run(
            "diff", "a", "b",
            "--db", str(self.tmp / "no.duckdb"),
        )
        self.assertEqual(rc, 3, f"err={err}")
        self.assertIn("DB not found", err)


if __name__ == "__main__":
    unittest.main()
