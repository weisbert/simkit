"""Unit tests for ``pvt trend`` CLI (simkit.cli.trend)."""

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
from simkit.milestone import set_run_milestone  # noqa: E402


_RUN_1 = "11111111-1111-4111-8111-111111111111"
_RUN_2 = "22222222-2222-4222-8222-222222222222"
_RUN_3 = "33333333-3333-4333-8333-333333333333"


_BASE: Dict[str, Any] = {
    "schema_version": 1,
    "run": {
        "run_id": _RUN_1,
        "project_id": "trend_cli",
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


class CliTrendBase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_trend_"))
        self.runs_root = self.tmp / "runs"
        self.db = self.tmp / "simkit.duckdb"
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
        timestamp: str = "2026-05-10T12:00:00+08:00",
        milestone: Optional[str] = None,
    ):
        dump = copy.deepcopy(_BASE)
        dump["run"]["run_id"] = run_id
        dump["run"]["timestamp"] = timestamp
        dump["results"] = rows
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "input.scs").write_text("* net\n")
        run_json = run_dir / "run.json"
        run_json.write_text(json.dumps(dump))
        con = connect(self.db)
        try:
            ingest_run_json(con, run_json)
            if milestone is not None:
                set_run_milestone(con, run_id=run_id, milestone=milestone)
        finally:
            con.close()


class CliTrendTests(CliTrendBase):

    def test_three_way_table(self):
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
        rc, out, err = _run("trend", "PDR", "CDR", "FDR", "--db", str(self.db))
        self.assertEqual(rc, 0, err)
        self.assertIn("PDR", out)
        self.assertIn("CDR", out)
        self.assertIn("FDR", out)
        self.assertIn("gain", out)
        # monotonic-up glyph present.
        self.assertIn("▲", out)

    def test_json_output(self):
        self._ingest(
            _RUN_1, [_row("T", "TT", 1, "gain", 10.0)],
            timestamp="2026-05-01T00:00:00+08:00",
        )
        self._ingest(
            _RUN_2, [_row("T", "TT", 1, "gain", 12.0)],
            timestamp="2026-05-05T00:00:00+08:00",
        )
        rc, out, err = _run(
            "trend", _RUN_1[:8], _RUN_2[:8], "--json", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(len(payload["columns"]), 2)
        self.assertEqual(payload["rows"][0]["direction"], "up")

    def test_changed_only_hides_flat_rows(self):
        self._ingest(
            _RUN_1,
            [_row("T", "TT", 1, "gain", 10.0), _row("T", "TT", 1, "nf", 3.0)],
            timestamp="2026-05-01T00:00:00+08:00",
        )
        self._ingest(
            _RUN_2,
            [_row("T", "TT", 1, "gain", 12.0), _row("T", "TT", 1, "nf", 3.0)],
            timestamp="2026-05-05T00:00:00+08:00",
        )
        rc, out, err = _run(
            "trend", _RUN_1[:8], _RUN_2[:8], "--changed-only", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, err)
        self.assertIn("gain", out)
        self.assertNotIn(" nf ", out)
        self.assertIn("unchanged rows hidden", out)

    def test_unknown_slice_exit_1(self):
        self._ingest(_RUN_1, [_row("T", "TT", 1, "gain", 10.0)])
        rc, out, err = _run(
            "trend", _RUN_1[:8], "no-such-slice", "--db", str(self.db),
        )
        self.assertEqual(rc, 1)
        self.assertIn("no run matches", err)

    def test_single_slice_rejected(self):
        self._ingest(_RUN_1, [_row("T", "TT", 1, "gain", 10.0)])
        rc, out, err = _run("trend", _RUN_1[:8], "--db", str(self.db))
        self.assertEqual(rc, 1)
        self.assertIn("at least two slices", err)

    def test_missing_db_exit_3(self):
        rc, out, err = _run(
            "trend", "PDR", "CDR", "--db", str(self.tmp / "nope.duckdb"),
        )
        self.assertEqual(rc, 3)
        self.assertIn("DB not found", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
