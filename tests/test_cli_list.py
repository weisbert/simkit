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


if __name__ == "__main__":
    unittest.main()
