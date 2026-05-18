"""Unit tests for ``pvt list`` CLI (simkit.cli.list_runs)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.cli.__main__ import main as cli_main  # noqa: E402
from simkit.db import bootstrap, connect  # noqa: E402
from simkit.ingest import ingest_run_json  # noqa: E402


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_SYN_MIN = _FIXTURES / "synthetic_minimal" / "run.json"
_REAL = _FIXTURES / "bdc13f17-d39b-4a13-b58e-846435996a29" / "run.json"
_SYN_MIN_RUN_ID = "11111111-1111-4111-8111-111111111111"
_REAL_RUN_ID = "bdc13f17-d39b-4a13-b58e-846435996a29"


def _run(*args: str) -> tuple:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


class CliListTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_list_"))
        self.db = self.tmp / "simkit.duckdb"
        con = connect(self.db)
        try:
            bootstrap(con)
            ingest_run_json(con, _SYN_MIN)
            ingest_run_json(con, _REAL)
        finally:
            con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_table_default_lists_both(self):
        rc, out, err = _run("list", "--db", str(self.db))
        self.assertEqual(rc, 0, f"err={err}")
        # Header line present.
        self.assertIn("run_id", out)
        self.assertIn("timestamp", out)
        # Both runs shown (short form).
        self.assertIn(_SYN_MIN_RUN_ID[:8], out)
        self.assertIn(_REAL_RUN_ID[:8], out)

    def test_table_shows_history_column(self):
        # v1.8 — history_name surfaces as its own column so the user can
        # correlate a DB row to a Maestro history entry without dropping to
        # --json. Fixture history_names: 'syn_min' and 'simkit_verify'.
        rc, out, err = _run("list", "--db", str(self.db))
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("history", out.splitlines()[0])
        self.assertIn("syn_min", out)
        self.assertIn("simkit_verify", out)

    def test_table_truncates_long_history_with_ellipsis(self):
        # Real-orchestrator names like "v17gmin_v17_gmin_demo_1779086137_1"
        # exceed the 22-char column. The trunc helper appends "…" and never
        # blows the column width.
        long_hist = "v17gmin_v17_gmin_demo_1779086137_1_extra"
        self._ingest_v2_into(
            "cccccccc-3333-4333-8333-cccccccccccc",
            "< 1e-10", 5e-11, history_name=long_hist,
        )
        rc, out, _err = _run("list", "--db", str(self.db))
        self.assertEqual(rc, 0)
        # _trunc keeps width-1 chars then appends "…". width=22 → 21 chars.
        self.assertIn("v17gmin_v17_gmin_demo…", out)
        self.assertNotIn(long_hist, out)

    def test_json_emits_array(self):
        rc, out, err = _run(
            "list", "--db", str(self.db), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)
        ids = {row["run_id"] for row in data}
        self.assertEqual(ids, {_SYN_MIN_RUN_ID, _REAL_RUN_ID})

    def test_slice_only_filters_to_labeled(self):
        rc, out, err = _run(
            "list", "--db", str(self.db), "--slice-only",
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn(_REAL_RUN_ID[:8], out)
        self.assertNotIn(_SYN_MIN_RUN_ID[:8], out)

    def test_project_filter(self):
        rc, out, err = _run(
            "list", "--db", str(self.db),
            "--project", "synthetic",
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn(_SYN_MIN_RUN_ID[:8], out)
        self.assertNotIn(_REAL_RUN_ID[:8], out)

    def test_limit_one(self):
        rc, out, err = _run(
            "list", "--db", str(self.db),
            "--json", "--limit", "1",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)

    def test_empty_db_table_shows_no_runs(self):
        empty_db = self.tmp / "empty.duckdb"
        con = connect(empty_db)
        try:
            bootstrap(con)
        finally:
            con.close()
        rc, out, err = _run("list", "--db", str(empty_db))
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("(no runs)", out)

    def test_empty_db_json_emits_empty_array(self):
        empty_db = self.tmp / "empty.duckdb"
        con = connect(empty_db)
        try:
            bootstrap(con)
        finally:
            con.close()
        rc, out, err = _run("list", "--db", str(empty_db), "--json")
        self.assertEqual(rc, 0, f"err={err}")
        self.assertEqual(json.loads(out), [])

    def test_db_missing_exits_3(self):
        rc, _out, err = _run(
            "list", "--db", str(self.tmp / "no.duckdb"),
        )
        self.assertEqual(rc, 3, f"err={err}")
        self.assertIn("DB not found", err)

    # v1.4 — spec-aware list surface.
    def test_v1_only_data_does_not_show_specs_column(self):
        # The two fixtures are pre-v2 (no output_specs). The table should
        # NOT include the "specs" column header to keep the v1 view clean.
        rc, out, _err = _run("list", "--db", str(self.db))
        self.assertEqual(rc, 0)
        self.assertNotIn("specs", out)

    def _ingest_v2_into(
        self, run_uuid: str, spec: str, value: float,
        history_name: Optional[str] = None,
    ) -> None:
        from datetime import datetime, timezone
        dump = {
            "schema_version": 2,
            "run": {
                "run_id": run_uuid, "project_id": "v14",
                "testbench_id": "lib/cell/view", "testbench_alias": None,
                "timestamp": "2026-05-16T15:00:00+08:00",
                "author": "tester", "label": None, "note": None,
                "netlist_path": "input.scs",
                "history_name": history_name or f"v14_{run_uuid[:8]}",
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
        path = self.tmp / f"v14_{run_uuid[:8]}.json"
        path.write_text(json.dumps(dump))
        con = connect(self.db)
        try:
            ingest_run_json(con, path)
        finally:
            con.close()

    def test_specs_column_shown_when_v2_data_present(self):
        self._ingest_v2_into("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
                              "< 1e-10", 5e-11)
        rc, out, _err = _run("list", "--db", str(self.db))
        self.assertEqual(rc, 0)
        self.assertIn("specs", out)
        self.assertIn("1/1", out)
        self.assertNotIn("FAIL", out)  # passing run

    def test_specs_column_marks_failing_runs(self):
        self._ingest_v2_into("bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb",
                              "< 1e-10", 2e-10)
        rc, out, _err = _run("list", "--db", str(self.db))
        self.assertEqual(rc, 0)
        self.assertIn("0/1 FAIL", out)

    def test_failed_only_filters(self):
        self._ingest_v2_into("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
                              "< 1e-10", 5e-11)
        self._ingest_v2_into("bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb",
                              "< 1e-10", 2e-10)
        rc, out, _err = _run("list", "--db", str(self.db), "--failed-only")
        self.assertEqual(rc, 0)
        # Only the failing run id shows up.
        self.assertIn("bbbbbbbb", out)
        self.assertNotIn("aaaaaaaa", out)


if __name__ == "__main__":
    unittest.main()
